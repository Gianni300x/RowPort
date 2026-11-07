import csv
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import psycopg

from scripts.cli import InputError, import_rows, main, read_csv, validate_headers, write_report


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
                read_csv(path)

    def test_rows_and_report_keep_invalid_and_irregular_values(self):
        conn = Connection()
        saved, rejected = import_rows(conn, "public", "properties", ["a", "b"],
                                      [["ok", "1"], ["invalid", "2"], ["extra", "3", "4"]])
        self.assertEqual(saved, 1)
        self.assertEqual([item[0] for item in rejected], [3, 4])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rejected.csv"
            write_report(path, ["a", "b"], rejected)
            with path.open(encoding="utf-8", newline="") as report:
                records = list(csv.reader(report))
            self.assertEqual(list(csv.reader([records[2][1]]))[0], ["extra", "3", "4"])

    def test_database_failure_is_not_treated_as_row_rejection(self):
        conn = Connection(psycopg.OperationalError("connection lost"))
        with self.assertRaises(psycopg.OperationalError):
            import_rows(conn, "public", "t", ["a"], [["ok"]])

    def test_report_failure_is_reported_after_commit(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.csv"
            source.write_text("a\nok\n", encoding="utf-8")
            conn = Connection()
            with patch("scripts.cli.psycopg.connect", return_value=conn), \
                 patch("scripts.cli.table_columns", return_value=[("a", True)]), \
                 patch("scripts.cli.write_report", side_effect=OSError("no space")):
                self.assertEqual(main([str(source), "--dsn", "test", "--table", "t",
                                       "--rejects", str(Path(directory) / "out.csv")]), 1)


if __name__ == "__main__":
    unittest.main()
