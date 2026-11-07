import csv
import json
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

    def test_mapping_validates_and_imports_without_losing_rejected_values(self):
        source = Path(self.directory.name) / "mapped.csv"
        source.write_text('street,price,bedrooms\n"First, Avenue",100,2\nBad,invalid,3\n', encoding="utf-8")
        report = Path(self.directory.name) / "mapped-rejects.csv"
        common = [str(source), "--dsn", DSN, "--schema", self.schema, "--table", "properties",
                  "--map", "street=address", "--map", "bedrooms=rooms"]
        validation = subprocess.run([sys.executable, "-m", "scripts.cli", "validate", *common],
                                    cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(validation.returncode, 0, validation.stderr)
        self.assertIn("Mapped columns: address, price, rooms", validation.stdout)
        self.assertEqual(self.count(), 0)
        result = subprocess.run([sys.executable, "-m", "scripts.cli", *common, "--rejects", str(report)],
                                cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        with psycopg.connect(DSN) as conn:
            row = conn.execute(sql.SQL("SELECT address, price, rooms FROM {}.properties").format(sql.Identifier(self.schema))).fetchone()
        self.assertEqual((row[0], int(row[1]), row[2]), ("First, Avenue", 100, 2))
        with report.open(encoding="utf-8", newline="") as f:
            rejected = list(csv.DictReader(f))
        self.assertEqual(len(rejected), 1)
        self.assertEqual(next(csv.reader([rejected[0]["csv_row"]])), ["Bad", "invalid", "3"])

        for mapping, message in [("street=missing", "Unknown columns"),
                                 ("street=price", "same table column")]:
            bad = subprocess.run([sys.executable, "-m", "scripts.cli", str(source), "--dsn", DSN,
                                  "--schema", self.schema, "--table", "properties", "--map", mapping,
                                  "--rejects", str(report)], cwd=ROOT, capture_output=True, text=True)
            self.assertNotEqual(bad.returncode, 0)
            self.assertIn(message, bad.stderr)
            self.assertEqual(self.count(), 1)

    def test_duplicate_policy_and_other_errors(self):
        with psycopg.connect(DSN) as conn:
            conn.execute(sql.SQL("ALTER TABLE {}.properties ADD UNIQUE (address)").format(sql.Identifier(self.schema)))
            conn.execute(sql.SQL("INSERT INTO {}.properties (address, price, rooms) VALUES ('Existing', 100, 1)").format(sql.Identifier(self.schema)))
        source = Path(self.directory.name) / "duplicates.csv"
        source.write_text("address,price,rooms\nExisting,200,2\nNew,300,3\nNew,400,4\nInvalid,bad,1\n", encoding="utf-8")
        report = Path(self.directory.name) / "duplicates-report.csv"
        common = [sys.executable, "-m", "scripts.cli", str(source), "--dsn", DSN,
                  "--schema", self.schema, "--table", "properties", "--rejects", str(report)]
        result = subprocess.run([*common, "--skip-duplicates"], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Rows saved: 1. Rows rejected: 1. Duplicates skipped: 2.", result.stdout)
        self.assertEqual(self.count(), 2)
        with report.open(encoding="utf-8", newline="") as f:
            rejected = list(csv.DictReader(f))
        self.assertEqual(len(rejected), 1)
        self.assertIn("Invalid,bad,1", rejected[0]["csv_row"])

        result = subprocess.run(common, cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Rows saved: 0. Rows rejected: 4.", result.stdout)
        self.assertEqual(self.count(), 2)
        with report.open(encoding="utf-8", newline="") as f:
            self.assertEqual(len(list(csv.DictReader(f))), 4)

    def test_semicolon_delimiter_with_quoted_values_and_mapping(self):
        source = Path(self.directory.name) / "semicolon.csv"
        source.write_text('street;price;rooms\n"Main; Street";100;2\n"Bad; Street";invalid;3\n', encoding="utf-8")
        report = Path(self.directory.name) / "semicolon-rejects.csv"
        common = [str(source), "--dsn", DSN, "--schema", self.schema, "--table", "properties",
                  "--delimiter", ";", "--map", "street=address"]
        validation = subprocess.run([sys.executable, "-m", "scripts.cli", "validate", *common],
                                    cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(validation.returncode, 0, validation.stderr)
        self.assertEqual(self.count(), 0)
        result = subprocess.run([sys.executable, "-m", "scripts.cli", *common, "--rejects", str(report)],
                                cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        with psycopg.connect(DSN) as conn:
            address = conn.execute(sql.SQL("SELECT address FROM {}.properties").format(sql.Identifier(self.schema))).fetchone()[0]
        self.assertEqual(address, "Main; Street")
        with report.open(encoding="utf-8", newline="") as f:
            rejected = list(csv.DictReader(f))
        self.assertEqual(len(rejected), 1)
        self.assertEqual(next(csv.reader([rejected[0]["csv_row"]])), ["Bad; Street", "invalid", "3"])

    def test_empty_as_null_is_opt_in_and_preserves_rejections(self):
        with psycopg.connect(DSN) as conn:
            for table in ("default_values", "null_values"):
                conn.execute(sql.SQL("CREATE TABLE {}.{} (name text NOT NULL, note text, amount integer)")
                             .format(sql.Identifier(self.schema), sql.Identifier(table)))
        source = Path(self.directory.name) / "empty.csv"
        source.write_text('name,note,amount\nFirst,,\nSecond,"",5\nThird, ,7\n,comment,9\n', encoding="utf-8")

        def run(table, flag):
            report = Path(self.directory.name) / f"{table}.csv"
            result = subprocess.run([sys.executable, "-m", "scripts.cli", str(source), "--dsn", DSN,
                                     "--schema", self.schema, "--table", table, "--rejects", str(report), *flag],
                                    cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            with report.open(encoding="utf-8", newline="") as f:
                return result.stdout, list(csv.DictReader(f))

        output, rejected = run("default_values", [])
        self.assertIn("Rows saved: 3. Rows rejected: 1.", output)
        self.assertEqual(next(csv.reader([rejected[0]["csv_row"]])), ["First", "", ""])
        output, rejected = run("null_values", ["--empty-as-null"])
        self.assertIn("Rows saved: 3. Rows rejected: 1.", output)
        self.assertEqual(next(csv.reader([rejected[0]["csv_row"]])), ["", "comment", "9"])
        with psycopg.connect(DSN) as conn:
            rows = conn.execute(sql.SQL("SELECT name, note, amount FROM {}.null_values ORDER BY name")
                                .format(sql.Identifier(self.schema))).fetchall()
        self.assertEqual(rows, [("First", None, None), ("Second", None, 5), ("Third", " ", 7)])

    def test_latin1_encoding_and_decoding_error(self):
        source = Path(self.directory.name) / "latin1.csv"
        source.write_bytes('address;price;rooms\nAvenida España;100;2\nNiño;invalid;3\n'.encode("latin-1"))
        report = Path(self.directory.name) / "latin1-rejected.csv"
        common = [str(source), "--dsn", DSN, "--schema", self.schema, "--table", "properties",
                  "--delimiter", ";"]
        bad = subprocess.run([sys.executable, "-m", "scripts.cli", *common, "--rejects", str(report)],
                             cwd=ROOT, capture_output=True, text=True)
        self.assertNotEqual(bad.returncode, 0)
        self.assertEqual(self.count(), 0)
        self.assertFalse(report.exists())
        validation = subprocess.run([sys.executable, "-m", "scripts.cli", "validate", *common,
                                     "--encoding", "latin-1"], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(validation.returncode, 0, validation.stderr)
        result = subprocess.run([sys.executable, "-m", "scripts.cli", *common, "--encoding", "latin-1",
                                 "--rejects", str(report)], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        with psycopg.connect(DSN) as conn:
            address = conn.execute(sql.SQL("SELECT address FROM {}.properties").format(sql.Identifier(self.schema))).fetchone()[0]
        self.assertEqual(address, "Avenida España")
        with report.open(encoding="utf-8", newline="") as f:
            rejected = list(csv.DictReader(f))
        self.assertEqual(len(rejected), 1)
        self.assertIn("Niño", rejected[0]["csv_row"])

    def test_config_preview_and_dry_run(self):
        source = Path(self.directory.name) / "custom.csv"
        source.write_text('street;price;rooms\n"First; Street";100;2\nBad;invalid;3\n', encoding="utf-8")
        report = Path(self.directory.name) / "dry-report.csv"
        config = Path(self.directory.name) / "import.json"
        config.write_text(json.dumps({"schema": self.schema, "table": "wrong_table", "delimiter": ";",
                                      "map": ["street=address"], "rejects": str(report),
                                      "empty_as_null": True}), encoding="utf-8")
        base = [sys.executable, "-m", "scripts.cli"]
        preview = subprocess.run([*base, "preview", str(source), "--config", str(config), "--limit", "1"],
                                 cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(preview.returncode, 0, preview.stderr)
        self.assertIn('2: ["First; Street", "100", "2"]', preview.stdout)
        self.assertNotIn('3: [', preview.stdout)
        self.assertIn("Mapped columns: address, price, rooms", preview.stdout)
        self.assertEqual(self.count(), 0)
        result = subprocess.run([*base, str(source), "--config", str(config), "--table", "properties",
                                 "--dsn", DSN, "--dry-run"], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("rows that would be saved: 1. Rows rejected: 1.", result.stdout)
        self.assertIn("Invalid data: 1", result.stdout)
        self.assertIn("no rows were saved", result.stdout)
        self.assertEqual(self.count(), 0)
        with report.open(encoding="utf-8", newline="") as f:
            self.assertEqual(len(list(csv.DictReader(f))), 1)

    def test_invalid_config_does_not_modify_database(self):
        config = Path(self.directory.name) / "invalid.json"
        config.write_text('{"table": "properties", "skip_duplicates": "false"}', encoding="utf-8")
        result = subprocess.run([sys.executable, "-m", "scripts.cli", str(ROOT / "examples/properties.csv"),
                                 "--config", str(config), "--dsn", DSN, "--rejects", str(Path(self.directory.name) / "out.csv")],
                                cwd=ROOT, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("skip_duplicates must be bool", result.stderr)
        self.assertEqual(self.count(), 0)

    def test_dry_run_checks_deferred_constraint_and_rolls_back(self):
        with psycopg.connect(DSN) as conn:
            conn.execute(sql.SQL("ALTER TABLE {}.properties ADD CONSTRAINT unique_address UNIQUE (address) DEFERRABLE INITIALLY DEFERRED")
                         .format(sql.Identifier(self.schema)))
        source = Path(self.directory.name) / "deferred.csv"
        source.write_text("address,price,rooms\nSame,100,1\nSame,200,2\n", encoding="utf-8")
        report = Path(self.directory.name) / "deferred-rejected.csv"
        result = subprocess.run([sys.executable, "-m", "scripts.cli", str(source), "--dsn", DSN,
                                 "--schema", self.schema, "--table", "properties", "--rejects", str(report),
                                 "--dry-run"], cwd=ROOT, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unique_address", result.stderr)
        self.assertEqual(self.count(), 0)
        self.assertFalse(report.exists())

    def test_summary_groups_different_rejections(self):
        with psycopg.connect(DSN) as conn:
            conn.execute(sql.SQL("ALTER TABLE {}.properties ADD UNIQUE (address)").format(sql.Identifier(self.schema)))
            conn.execute(sql.SQL("INSERT INTO {}.properties (address, price, rooms) VALUES ('Same', 100, 1)")
                         .format(sql.Identifier(self.schema)))
        source = Path(self.directory.name) / "categories.csv"
        source.write_text("address,price,rooms\nSame,100,1\nBad,not-a-number,1\nShort,100\n", encoding="utf-8")
        result, report = self.run_cli(source)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Incorrect field count: 1", result.stdout)
        self.assertIn("Invalid data: 1", result.stdout)
        self.assertIn("Unique constraint: 1", result.stdout)
        with report.open(encoding="utf-8", newline="") as f:
            self.assertEqual(len(list(csv.DictReader(f))), 3)

    def test_config_cli_overrides_boolean_and_mappings(self):
        source = Path(self.directory.name) / "override.csv"
        source.write_text("street,price,rooms\nFirst,100,2\n", encoding="utf-8")
        config = Path(self.directory.name) / "override.json"
        report = Path(self.directory.name) / "override-rejected.csv"
        config.write_text(json.dumps({"schema": self.schema, "table": "properties", "rejects": str(report),
                                      "map": ["street=wrong"], "skip_duplicates": True}), encoding="utf-8")
        result = subprocess.run([sys.executable, "-m", "scripts.cli", str(source), "--dsn", DSN,
                                 "--config", str(config), "--map", "street=address", "--no-skip-duplicates"],
                                cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Rows saved: 1.", result.stdout)
        self.assertNotIn("Duplicates skipped:", result.stdout)
        self.assertEqual(self.count(), 1)

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
