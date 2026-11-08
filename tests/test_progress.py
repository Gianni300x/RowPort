import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from scripts.cli import main
from scripts.progress import ProgressBar
from test_cli import Connection


class Terminal(io.StringIO):
    def isatty(self):
        return True


class ProgressTests(unittest.TestCase):
    def test_bar_updates_one_line_and_finishes(self):
        output = io.StringIO()
        bar = ProgressBar(output)
        bar.counting()
        bar.start(2)
        bar.update(1, 1, 0, 0)
        bar.update(2, 2, 0, 0)
        self.assertNotIn("\n", output.getvalue())
        bar.finish()
        bar.finish()
        self.assertIn("  0%  0/2 processed", output.getvalue())
        self.assertIn(" 50%  1/2 processed", output.getvalue())
        self.assertIn("100%  2/2 processed", output.getvalue())
        self.assertEqual(output.getvalue().count("\n"), 1)

    def run_import(self, contents, mutate=False):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.csv"
            report = Path(directory) / "rejects.csv"
            source.write_text(contents, encoding="utf-8")
            conn = Connection()
            if mutate:
                execute = conn.execute
                def change_source(statement, row):
                    execute(statement, row)
                    source.write_text(contents + "changed\n", encoding="utf-8")
                conn.execute = change_source
            output, errors = Terminal(), io.StringIO()
            with patch("scripts.cli.psycopg.connect", return_value=conn), \
                 patch("scripts.cli.table_columns", return_value=[("value", False)]), \
                 redirect_stdout(output), redirect_stderr(errors):
                result = main([str(source), "--table", "example", "--rejects", str(report), "--dsn", "test"])
            return result, output.getvalue(), errors.getvalue(), report.exists()

    def test_counts_csv_records_including_multiline_values(self):
        result, output, errors, report = self.run_import('value\n"two\nlines"\nlast\n')
        self.assertEqual(result, 0, errors)
        self.assertIn("100%  2/2 processed", output)
        self.assertIn("Rows saved: 2.", output)
        self.assertTrue(report)

    def test_empty_csv_reaches_completion(self):
        result, output, errors, _ = self.run_import("value\n")
        self.assertEqual(result, 0, errors)
        self.assertIn("100%  0/0 processed", output)

    def test_changed_source_fails_before_report_is_published(self):
        result, output, errors, report = self.run_import("value\nfirst\n", mutate=True)
        self.assertEqual(result, 1)
        self.assertIn("Input CSV changed", errors)
        self.assertTrue(output.endswith("\n"))
        self.assertFalse(report)


if __name__ == "__main__":
    unittest.main()
