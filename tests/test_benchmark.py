import argparse
import csv
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock, patch

import psycopg

from scripts.benchmark import generate_csv, is_rejected, main, percentage, verify


class BenchmarkTests(unittest.TestCase):
    def test_deterministic_input_contains_exact_rejections_and_complex_text(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.csv"
            for rows, rejects in [(1, 0), (1, 1), (31, 3), (31, 31)]:
                with self.subTest(rows=rows, rejects=rejects):
                    generate_csv(source, rows, rejects)
                    original = source.read_bytes()
                    generate_csv(source, rows, rejects)
                    self.assertEqual(source.read_bytes(), original)
                    with source.open(encoding="utf-8", newline="") as stream:
                        records = list(csv.DictReader(stream))
                    self.assertEqual(len(records), rows)
                    self.assertEqual(sum(row["price"] == "invalid" for row in records), rejects)
                    self.assertIn('Córdoba\nSynthetic "quoted", description', records[0]["name"])
                    self.assertEqual([int(row["id"]) for row in records], list(range(1, rows + 1)))
                    self.assertEqual(sum(is_rejected(i, rows, rejects) for i in range(rows)), rejects)

    def test_percentages_and_explicit_disposable_connection(self):
        for value in ("NaN", "Infinity", "-1", "100.01", "invalid"):
            with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError):
                percentage(value)
        self.assertEqual(percentage("12.5"), Decimal("12.5"))
        with patch.dict(os.environ, {"DATABASE_URL": "must-not-use"}, clear=True), \
             patch("scripts.benchmark.psycopg.connect") as connect, redirect_stderr(io.StringIO()), \
             self.assertRaises(SystemExit) as result:
            main([])
        self.assertEqual(result.exception.code, 2)
        connect.assert_not_called()

    def test_verification_rejects_wrong_counts_and_duplicate_report_records(self):
        conn = Mock()
        conn.execute.return_value.fetchone.return_value = [8]
        summary = {"rows_processed": 10, "rows_saved": 8, "rows_rejected": 2, "duplicates_skipped": 0,
                   "exit_code": 3, "dry_run": False, "rejection_categories": {"Invalid data": 2}}
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "rejects.csv"
            def write(ids):
                with report.open("w", encoding="utf-8", newline="") as stream:
                    writer = csv.writer(stream)
                    writer.writerow(["id", "price", "rowport_record", "rowport_error"])
                    writer.writerows([i, "invalid", i + 1, "Invalid price"] for i in ids)
            write([5, 10])
            self.assertEqual(verify(conn, "test", 10, 2, report, summary, 3), (8, 2))
            for ids in ([5, 5], [5], [4, 10]):
                write(ids)
                with self.subTest(ids=ids), self.assertRaises(ValueError):
                    verify(conn, "test", 10, 2, report, summary, 3)
            write([5, 10])
            conn.execute.return_value.fetchone.return_value = [9]
            with self.assertRaises(ValueError):
                verify(conn, "test", 10, 2, report, summary, 3)


@unittest.skipUnless(os.environ.get("ROWPORT_TEST_DSN") and sys.platform in ("linux", "darwin"),
                     "Requires disposable PostgreSQL and Linux/macOS RSS measurement")
class BenchmarkIntegrationTests(unittest.TestCase):
    def schemas(self):
        with psycopg.connect(os.environ["ROWPORT_TEST_DSN"]) as conn:
            return conn.execute("SELECT nspname FROM pg_namespace WHERE starts_with(nspname, 'rowport_bench_') ORDER BY nspname").fetchall()

    def test_real_worker_repeats_verify_counts_and_cleanup(self):
        before = self.schemas()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "benchmark.json"
            result = subprocess.run([sys.executable, "-m", "scripts.benchmark", "--rows", "30", "--reject-percent", "0", "10", "100",
                                     "--repeat", "2", "--output", str(output)],
                                    env={**os.environ, "ROWPORT_BENCHMARK_DSN": os.environ["ROWPORT_TEST_DSN"]},
                                    capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(len(payload["results"]), 6)
            for result in payload["results"]:
                self.assertTrue(result["verified"])
                self.assertEqual(result["database_rows"] + result["report_rows"], 30)
                self.assertEqual(result["report_rows"], result["expected_rejections"])
                self.assertGreater(result["peak_rss_bytes"], 0)
                self.assertGreater(result["duration_seconds"], 0)
                self.assertGreater(result["rows_per_second"], 0)
            self.assertNotIn(os.environ["ROWPORT_TEST_DSN"], output.read_text())
        self.assertEqual(self.schemas(), before)

    def test_timeout_terminates_worker_cleans_schema_and_preserves_previous_results(self):
        before = self.schemas()
        process = Mock()
        process.wait.side_effect = [subprocess.TimeoutExpired("worker", 1), 0]
        process.poll.return_value = None
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "benchmark.json"
            output.write_text("previous results", encoding="utf-8")
            with patch.dict(os.environ, {"ROWPORT_BENCHMARK_DSN": os.environ["ROWPORT_TEST_DSN"]}), \
                 patch("scripts.benchmark.subprocess.Popen", return_value=process), \
                 redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(main(["--rows", "10", "--output", str(output)]), 1)
            process.terminate.assert_called_once()
            self.assertEqual(output.read_text(), "previous results")
            self.assertEqual(list(Path(directory).iterdir()), [output])
        self.assertEqual(self.schemas(), before)


if __name__ == "__main__":
    unittest.main()
