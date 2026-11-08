import argparse
import csv
import errno
import io
import random
import tempfile
import unittest
from contextlib import contextmanager, redirect_stdout, redirect_stderr
from pathlib import Path
from unittest.mock import patch

import psycopg

from scripts.cli import InputError, configure_windows_output, csv_delimiter, csv_encoding, database_url, import_rows, main, map_headers, read_csv, show_progress, staged_report, validate_headers, write_report


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
    def test_help_groups_options_by_purpose(self):
        for argv, expected in [(["--help"], ["Required for import", "Database connection", "CSV format and columns", "Import behavior"]),
                               (["validate", "--help"], ["Database and table", "CSV format and columns"]),
                               (["preview", "--help"], ["CSV format and columns", "Preview"] )]:
            output = io.StringIO()
            with self.subTest(argv=argv), redirect_stdout(output), self.assertRaises(SystemExit) as result:
                main(argv)
            self.assertEqual(result.exception.code, 0)
            for title in expected:
                self.assertIn(title, output.getvalue())

    def test_encoding_must_be_supported_for_text(self):
        self.assertEqual(csv_encoding("latin-1"), "latin-1")
        for value in ("not-a-real-encoding", "base64_codec", ""):
            with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError):
                csv_encoding(value)

    def test_delimiter_requires_one_safe_character(self):
        self.assertEqual(csv_delimiter(";"), ";")
        for value in ("", "::", "\n", "\r", "\0", '"'):
            with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError):
                csv_delimiter(value)

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
            path.write_text('DATABASE_URL="postgresql://bom/db"\n', encoding="utf-8-sig")
            with patch.dict("os.environ", {}, clear=True):
                self.assertEqual(database_url(path), "postgresql://bom/db")

    def test_windows_redirected_output_uses_utf8_and_respects_explicit_encoding(self):
        with patch.dict("os.environ", {"PYTHONIOENCODING": ""}):
            stream = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
            configure_windows_output(platform="win32", streams=(stream,))
            stream.write("España 🧉")
            stream.flush()
            self.assertEqual(stream.buffer.getvalue().decode("utf-8"), "España 🧉")
        with patch.dict("os.environ", {"PYTHONIOENCODING": "cp1252"}):
            stream = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
            configure_windows_output(platform="win32", streams=(stream,))
            self.assertEqual(stream.encoding, "cp1252")

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

    def test_postgres_csv_distinguishes_null_and_quoted_empty(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "postgres.csv"
            source.write_text('a;b;c;d;e\r\n;"";" ";"line 1\nline ""2""";end\r\n"";;x;;\r\n', encoding="utf-8", newline="")
            with read_csv(source, delimiter=";", postgres_csv=True) as (headers, rows):
                self.assertEqual(headers, ["a", "b", "c", "d", "e"])
                self.assertEqual(list(rows), [[None, "", " ", 'line 1\nline "2"', "end"],
                                             ["", None, "x", None, None]])

    def test_postgres_csv_single_column_empty_line_is_null(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "single.csv"
            source.write_text('value\n\n""\n', encoding="utf-8")
            with read_csv(source, postgres_csv=True) as (_, rows):
                self.assertEqual(list(rows), [[None], [""]])

    def test_postgres_csv_many_mixed_quoting_patterns(self):
        rng = random.Random(42)
        values = [None, "", "normal", " ", "a,b", 'a"b', "line\nnext", "carriage\rreturn", "Ñandú 🧉"]
        expected = [[rng.choice(values) for _ in range(4)] for _ in range(1000)]
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "mixed.csv"
            with source.open("w", encoding="utf-8", newline="") as target:
                target.write("a,b,c,d\r\n")
                for row in expected:
                    fields = []
                    for value in row:
                        if value is None:
                            fields.append("")
                        else:
                            buffer = io.StringIO(newline="")
                            quoting = csv.QUOTE_ALL if rng.choice((True, False)) else csv.QUOTE_MINIMAL
                            csv.writer(buffer, quoting=quoting, lineterminator="\r\n").writerow([value])
                            fields.append(buffer.getvalue()[:-2])
                    target.write(",".join(fields) + "\r\n")
            with read_csv(source, postgres_csv=True) as (_, rows):
                self.assertEqual(list(rows), expected)

    def test_postgres_csv_and_empty_as_null_are_incompatible(self):
        stderr = io.StringIO()
        with redirect_stderr(stderr), self.assertRaises(SystemExit) as result:
            main(["file.csv", "--table", "t", "--rejects", "out.csv", "--postgres-csv", "--empty-as-null"])
        self.assertEqual(result.exception.code, 2)
        self.assertIn("cannot be combined", stderr.getvalue())

    def test_postgres_csv_config_can_be_overridden_for_preview(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.csv"
            config = Path(directory) / "config.json"
            source.write_text('value\n\n""\n', encoding="utf-8")
            config.write_text('{"postgres_csv": true}', encoding="utf-8")
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(["preview", str(source), "--config", str(config)]), 0)
            self.assertIn('2: [null]', output.getvalue())
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(main(["preview", str(source), "--config", str(config), "--no-postgres-csv"]), 0)
            self.assertIn('2: []', output.getvalue())

    def test_rows_and_report_keep_invalid_and_irregular_values(self):
        conn = Connection()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rejected.csv"
            with staged_report(path) as (target, report):
                saved, rejected, skipped = import_rows(conn, "public", "properties", ["a", "b"],
                                              iter([["ok", "1"], ["invalid", "2"], ["extra", "3", "4"]]), report)
                self.assertEqual((saved, rejected, skipped), (1, 2, 0))
                write_report(target, report)
            with path.open(encoding="utf-8", newline="") as report:
                records = list(csv.reader(report))
            self.assertEqual([int(row[2]) for row in records[1:]], [3, 4])
            self.assertEqual(records[2][:2], ["extra", "3"])
            self.assertEqual(records[2][-1], '["4"]')

    def test_report_diagnostic_headers_avoid_original_column_collisions(self):
        from scripts.importer import report_metadata_headers, retry_headers
        original = ["rowport_record", "rowport_error", "rowport_extra"]
        diagnostic = report_metadata_headers(original)
        self.assertEqual(diagnostic, ["rowport_record_", "rowport_error_", "rowport_extra_"])
        self.assertEqual(retry_headers(original + diagnostic), original)

    def test_retry_refuses_unresolved_extra_fields(self):
        from scripts.importer import retry_rows
        with self.assertRaisesRegex(InputError, "extra fields"):
            list(retry_rows([["ok", "2", "bad", '["extra"]']], 1))

    def test_subcommands_accept_options_before_csv(self):
        source = Path(__file__).resolve().parent.parent / "examples" / "properties.csv"
        output = io.StringIO()
        with redirect_stdout(output):
            result = main(["preview", "--limit", "1", str(source)])
        self.assertEqual(result, 0)
        self.assertIn('2: ["123 Oak Avenue", "85000", "3"]', output.getvalue())

    def test_database_failure_is_not_treated_as_row_rejection(self):
        conn = Connection(psycopg.OperationalError("connection lost"))
        with self.assertRaises(psycopg.OperationalError):
            import_rows(conn, "public", "t", ["a"], [["ok"]], io.StringIO(newline=""))

    def test_progress_counts_all_completed_rows_at_interval(self):
        events = []
        saved, rejected, skipped = import_rows(Connection(), "public", "t", ["a"],
                                      iter([["ok"], ["invalid"], ["extra", "field"], ["ok"]]),
                                      io.StringIO(newline=""),
                                      progress=lambda *counts: events.append(counts), progress_interval=2)
        self.assertEqual(events, [(2, 1, 1, 0), (4, 2, 2, 0)])
        self.assertEqual((saved, rejected, skipped), (2, 2, 0))

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
        self.assertEqual(events, [(2, 1, 1, 0)])

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
            self.assertIn("connection was lost", stderr.getvalue())

    def test_connection_error_does_not_expose_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.csv"
            source.write_text("a\nok\n", encoding="utf-8")
            stderr = io.StringIO()
            with patch("scripts.cli.psycopg.connect", side_effect=psycopg.OperationalError("password=secret")), \
                 redirect_stderr(stderr):
                result = main([str(source), "--dsn", "postgresql://user:secret@localhost/db", "--table", "t",
                               "--rejects", str(Path(directory) / "out.csv")])
            self.assertEqual(result, 1)
            self.assertNotIn("secret", stderr.getvalue())
            self.assertIn("Check that PostgreSQL is running", stderr.getvalue())

    def test_database_error_guidance_does_not_expose_details(self):
        from scripts.cli import display_error
        cases = [
            (psycopg.errors.InvalidPassword("password=secret"), "authentication failed"),
            (psycopg.errors.InvalidCatalogName("password=secret"), "database was not found"),
            (psycopg.errors.InsufficientPrivilege("password=secret"), "denied access"),
            (psycopg.errors.QueryCanceled("password=secret"), "canceled the operation"),
            (OSError(errno.ENOSPC, "no space"), "No disk space"),
        ]
        for error, expected in cases:
            output = io.StringIO()
            with self.subTest(error=error), redirect_stderr(output):
                display_error(error)
                self.assertIn(expected, output.getvalue())
                self.assertNotIn("secret", output.getvalue())

    def test_interruption_exits_without_claiming_rollback(self):
        stderr = io.StringIO()
        with patch("scripts.cli._main", side_effect=KeyboardInterrupt), redirect_stderr(stderr):
            self.assertEqual(main([]), 130)
        self.assertIn("check the database and report", stderr.getvalue())
        self.assertIn("may already have committed", stderr.getvalue())

    def test_preview_reports_missing_file_with_suggestion(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "missing.csv"
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                result = main(["preview", str(source)])
            self.assertEqual(result, 1)
            self.assertIn(str(source), stderr.getvalue())
            self.assertIn("Check the path", stderr.getvalue())

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

    def test_failed_report_replacement_preserves_existing_report(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.csv"
            destination = Path(directory) / "out.csv"
            source.write_text("a\nok\n", encoding="utf-8")
            destination.write_text("previous report", encoding="utf-8")
            conn = Connection()
            stderr = io.StringIO()
            with patch("scripts.cli.psycopg.connect", return_value=conn), \
                 patch("scripts.cli.table_columns", return_value=[("a", True)]), \
                 patch("scripts.cli.os.replace", side_effect=OSError("rename failed")), \
                 redirect_stderr(stderr):
                result = main([str(source), "--dsn", "test", "--table", "t", "--rejects", str(destination)])
            self.assertEqual(result, 1)
            self.assertIn("Saved 1 rows, but could not write the report", stderr.getvalue())
            self.assertEqual(destination.read_text(encoding="utf-8"), "previous report")
            self.assertEqual(conn.executed, [["ok"]])
            self.assertFalse(list(Path(directory).glob(".rowport-*")))


if __name__ == "__main__":
    unittest.main()
