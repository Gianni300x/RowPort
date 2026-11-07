import csv
import os
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parent.parent
DSN = os.environ.get("ROWPORT_TEST_DSN")


@unittest.skipUnless(DSN, "Set ROWPORT_TEST_DSN to run PostgreSQL integration tests")
class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.schema = "rowport_test_" + uuid.uuid4().hex
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        with psycopg.connect(DSN, autocommit=True) as conn:
            conn.execute(sql.SQL("CREATE SCHEMA {} ").format(sql.Identifier(self.schema)))
            self.addCleanup(self.drop_schema)
            conn.execute(sql.SQL("CREATE TABLE {}.properties (id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, address text NOT NULL, price numeric(12,2) NOT NULL CHECK (price > 0), rooms integer NOT NULL)").format(sql.Identifier(self.schema)))

    def drop_schema(self):
        with psycopg.connect(DSN, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)))

    def run_cli(self, source):
        report = Path(self.directory.name) / "rejects.csv"
        result = subprocess.run([sys.executable, "-m", "scripts.cli", str(source), "--dsn", DSN,
                                 "--schema", self.schema, "--table", "properties", "--rejects", str(report)],
                                cwd=ROOT, capture_output=True, text=True)
        return result, report

    def count(self):
        with psycopg.connect(DSN) as conn:
            return conn.execute(sql.SQL("SELECT count(*) FROM {}.properties").format(sql.Identifier(self.schema))).fetchone()[0]

    def test_validation_checks_headers_without_importing_rows(self):
        def validate(source):
            return subprocess.run([sys.executable, "-m", "scripts.cli", "validate", str(source),
                                   "--dsn", DSN, "--schema", self.schema, "--table", "properties"],
                                  cwd=ROOT, capture_output=True, text=True)

        result = validate(ROOT / "examples/properties.csv")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("CSV columns: address, price, rooms", result.stdout)
        self.assertIn("Required columns: address, price, rooms", result.stdout)
        self.assertIn("No rows were checked or imported", result.stdout)
        self.assertEqual(self.count(), 0)
        self.assertFalse((Path(self.directory.name) / "rejects.csv").exists())

        source = Path(self.directory.name) / "bad_headers.csv"
        source.write_text("address,price,extra\nExample,100,x\n", encoding="utf-8")
        result = validate(source)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unknown columns: extra", result.stderr)
        self.assertIn("Missing required columns: rooms", result.stderr)
        self.assertEqual(self.count(), 0)

        source.write_text("address,address\nExample,Example\n", encoding="utf-8")
        result = validate(source)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("duplicate", result.stderr)
        self.assertEqual(self.count(), 0)

    def test_example_and_missing_column(self):
        result, report = self.run_cli(ROOT / "examples/properties.csv")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.count(), 2)
        with report.open(encoding="utf-8", newline="") as source:
            rejected = list(csv.DictReader(source))
        self.assertEqual(len(rejected), 1)
        self.assertIn("not a price", rejected[0]["csv_row"])
        self.assertTrue(rejected[0]["reason"])
        missing = Path(self.directory.name) / "missing.csv"
        missing.write_text("address,price\nExample,100\n", encoding="utf-8")
        result, _ = self.run_cli(missing)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("rooms", result.stderr)
        self.assertEqual(self.count(), 2)

    def test_database_failure_rolls_back_prior_insert(self):
        with psycopg.connect(DSN) as conn:
            conn.execute(sql.SQL("CREATE FUNCTION {}.fail_import() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.address = 'FAIL' THEN RAISE EXCEPTION 'simulated failure' USING ERRCODE = '58000'; END IF; RETURN NEW; END $$").format(sql.Identifier(self.schema)))
            conn.execute(sql.SQL("CREATE TRIGGER fail_import BEFORE INSERT ON {}.properties FOR EACH ROW EXECUTE FUNCTION {}.fail_import()").format(sql.Identifier(self.schema), sql.Identifier(self.schema)))
        source = Path(self.directory.name) / "failure.csv"
        source.write_text("address,price,rooms\nBefore failure,100,1\nFAIL,200,2\n", encoding="utf-8")
        result, _ = self.run_cli(source)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("simulated failure", result.stderr)
        self.assertEqual(self.count(), 0)

    def test_malformed_csv_after_valid_row_rolls_back(self):
        source = Path(self.directory.name) / "malformed.csv"
        source.write_text('address,price,rooms\nValid,100,1\n"unfinished\n', encoding="utf-8")
        report = Path(self.directory.name) / "rejects.csv"
        report.write_text("existing report", encoding="utf-8")
        result, _ = self.run_cli(source)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.count(), 0)
        self.assertEqual(report.read_text(encoding="utf-8"), "existing report")
