import csv
import io
import tempfile
import unittest
from contextlib import contextmanager, redirect_stdout, redirect_stderr
from pathlib import Path
from unittest.mock import patch

import psycopg

from scripts.cli import InputError, database_url, import_rows, main, map_headers, read_csv, show_progress, validate_headers, write_report


class Connection:
    def __init__(self, failure=None):
        self.failure = failure
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    @contextmanager
    def transaction(self):
        yield

    def execute(self, statement, row):
        if row[0] == "invalid":
            raise psycopg.DataError("invalid type")
        if self.failure:
            raise self.failure
        self.executed.append(row)


class RowPortTests(unittest.TestCase):
    def test_column_mappings_and_collisions(self):
        self.assertEqual(map_headers(["street", "price", "bedrooms"],
                                     ["street=address", "bedrooms=rooms"]),
                         ["address", "price", "rooms"])
        for mappings, message in [(["street"], "form"), (["street="], "form"),
                                  (["missing=address"], "not found"),
                                  (["street=address", "street=rooms"], "Duplicate mapping"),
                                  (["street=price"], "same table column")]:
            with self.subTest(mappings=mappings), self.assertRaisesRegex(InputError, message):
                map_headers(["street", "price", "bedrooms"], mappings)

    def test_database_url_from_file_and_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text('# Configuration\nDATABASE_URL="postgresql://file/db"\n', encoding="utf-8")
            with patch.dict("os.environ", {}, clear=True):
                self.assertEqual(database_url(path), "postgresql://file/db")
            with patch.dict("os.environ", {"DATABASE_URL": "postgresql://env/db"}):
                self.assertEqual(database_url(path), "postgresql://env/db")

    def test_explicit_dsn_takes_precedence(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.csv"
            source.write_text("a\nok\n", encoding="utf-8")
            with patch("scripts.cli.psycopg.connect", return_value=Connection()) as connect, \
                 patch("scripts.cli.table_columns", return_value=[("a", True)]), \
                 patch.dict("os.environ", {"DATABASE_URL": "postgresql://env/db"}):
                self.assertEqual(main([str(source), "--dsn", "explicit", "--table", "t",
                                       "--rejects", str(Path(directory) / "out.csv")]), 0)
                connect.assert_called_once_with("explicit")

    def test_header_validation(self):
        columns = [("id", False), ("address", True)]
        validate_headers(["address"], columns)
        with self.assertRaisesRegex(InputError, "Missing"):
            validate_headers(["id"], columns)
        with self.assertRaisesRegex(InputError, "Unknown"):
            validate_headers(["address", "otra"], columns)

    def test_read_rejects_duplicate_headers(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.csv"
            path.write_text("a,a\n1,2\n", encoding="utf-8")
            with self.assertRaisesRegex(InputError, "duplicate"):
                with read_csv(path):
                    pass

    def test_csv_is_read_lazily(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.csv"
            path.write_text('a\nok\n"unterminated\n', encoding="utf-8")
            with read_csv(path) as (headers, rows):
                self.assertEqual(headers, ["a"])
                self.assertEqual(next(rows), ["ok"])
                with self.assertRaises(csv.Error):
                    next(rows)

    def test_rows_and_report_keep_invalid_and_irregular_values(self):
        conn = Connection()
        report = io.StringIO(newline="")
        saved, rejected = import_rows(conn, "public", "properties", ["a", "b"],
                                      iter([["ok", "1"], ["invalid", "2"], ["extra", "3", "4"]]), report)
        self.assertEqual(saved, 1)
        self.assertEqual(rejected, 2)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rejected.csv"
            write_report(path, report)
            with path.open(encoding="utf-8", newline="") as report:
                records = list(csv.reader(report))
            self.assertEqual([int(row[0]) for row in records[1:]], [3, 4])
            self.assertEqual(list(csv.reader([records[2][1]]))[0], ["extra", "3", "4"])

    def test_database_failure_is_not_treated_as_row_rejection(self):
        conn = Connection(psycopg.OperationalError("connection lost"))
        with self.assertRaises(psycopg.OperationalError):
            import_rows(conn, "public", "t", ["a"], [["ok"]], io.StringIO(newline=""))

    def test_progress_counts_all_completed_rows_at_interval(self):
        events = []
        saved, rejected = import_rows(Connection(), "public", "t", ["a"],
                                      iter([["ok"], ["invalid"], ["extra", "field"], ["ok"]]),
                                      io.StringIO(newline=""),
                                      progress=lambda *counts: events.append(counts), progress_interval=2)
        self.assertEqual(events, [(2, 1, 1), (4, 2, 2)])
        self.assertEqual((saved, rejected), (2, 2))

    def test_failure_after_progress_does_not_count_failed_row(self):
        class FailingConnection(Connection):
            def execute(self, statement, row):
                if row == ["fail"]:
                    raise psycopg.OperationalError("connection lost")
                super().execute(statement, row)

        events = []
        with self.assertRaises(psycopg.OperationalError):
            import_rows(FailingConnection(), "public", "t", ["a"],
                        iter([["ok"], ["invalid"], ["fail"]]), io.StringIO(newline=""),
                        progress=lambda *counts: events.append(counts), progress_interval=2)
        self.assertEqual(events, [(2, 1, 1)])

    def test_progress_is_flushed_as_independent_lines(self):
        class Output(io.StringIO):
            def __init__(self):
                super().__init__()
                self.flushes = 0

            def flush(self):
                self.flushes += 1

        output = Output()
        with redirect_stdout(output):
            show_progress(10000, 9999, 1)
        self.assertEqual(output.getvalue(),
                         "Progress: 10000 processed; 9999 saved pending commit; 1 rejected.\n")
        self.assertEqual(output.flushes, 1)

    def test_failed_import_has_no_success_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.csv"
            source.write_text("a\nok\n", encoding="utf-8")
            stdout, stderr = io.StringIO(), io.StringIO()
            with patch("scripts.cli.psycopg.connect", return_value=Connection(psycopg.OperationalError("lost"))), \
                 patch("scripts.cli.table_columns", return_value=[("a", True)]), \
                 redirect_stdout(stdout), redirect_stderr(stderr):
                result = main([str(source), "--dsn", "test", "--table", "t",
                               "--rejects", str(Path(directory) / "out.csv")])
            self.assertEqual(result, 1)
            self.assertNotIn("Rows saved:", stdout.getvalue())
            self.assertIn("lost", stderr.getvalue())

    def test_report_failure_is_reported_after_commit(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.csv"
            source.write_text("a\nok\n", encoding="utf-8")
            conn = Connection()
            stderr = io.StringIO()
            with patch("scripts.cli.psycopg.connect", return_value=conn), \
                 patch("scripts.cli.table_columns", return_value=[("a", True)]), \
                 patch("scripts.cli.write_report", side_effect=OSError("no space")), \
                 redirect_stderr(stderr):
                self.assertEqual(main([str(source), "--dsn", "test", "--table", "t",
                                       "--rejects", str(Path(directory) / "out.csv")]), 1)
            self.assertIn("Saved 1 rows, but could not write the report", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
