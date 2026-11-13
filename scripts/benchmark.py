"""Reproducible import benchmark using synthetic data in a disposable schema."""

import argparse
import csv
import hashlib
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
import uuid
from decimal import Decimal, InvalidOperation
from pathlib import Path

import psycopg
from psycopg import sql

from .cli import main as import_main, positive_int
from .errors import InputError
from .importer import staged_report, write_report


def percentage(value):
    try:
        result = Decimal(value)
        if not result.is_finite() or not 0 <= result <= 100:
            raise ValueError
        return result
    except (InvalidOperation, ValueError):
        raise argparse.ArgumentTypeError("Rejection percentage must be between 0 and 100.") from None


def is_rejected(index, rows, rejects):
    """Distribute an exact number of invalid prices across the file (zero-based)."""
    return (index + 1) * rejects // rows != index * rejects // rows


def generate_csv(path, rows, rejects):
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["id", "name", "price", "quantity"])
        for index in range(rows):
            name = f"Product {index + 1} – Córdoba"
            if index % 1000 == 0:
                name += '\nSynthetic "quoted", description'
            writer.writerow([index + 1, name, "invalid" if is_rejected(index, rows, rejects) else "125.50", index % 10])


def worker(argv):
    # Fresh process per measurement: ru_maxrss cannot be reset between imports.
    import resource

    source, schema, report, summary, metrics = argv
    started = time.perf_counter()
    code = import_main([source, "--schema", schema, "--table", "items", "--rejects", report,
                        "--summary-json", summary, "--fail-on-rejects"])
    duration = time.perf_counter() - started
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    Path(metrics).write_text(json.dumps({"exit_code": code, "duration_seconds": duration,
                                       "peak_rss_bytes": int(rss if sys.platform == "darwin" else rss * 1024)}), encoding="utf-8")
    return code


def verify(conn, schema, rows, rejects, report, summary, exit_code):
    stored = conn.execute(sql.SQL("SELECT count(*) FROM {}.items").format(sql.Identifier(schema))).fetchone()[0]
    expected_code = 3 if rejects else 0
    expected = {"rows_processed": rows, "rows_saved": rows - rejects, "rows_rejected": rejects,
                "duplicates_skipped": 0, "exit_code": expected_code, "dry_run": False,
                "rejection_categories": {"Invalid data": rejects} if rejects else {}}
    if exit_code != expected_code or stored != rows - rejects or any(summary.get(key) != value for key, value in expected.items()):
        raise ValueError("Import exit code, summary or PostgreSQL count does not match the generated input.")
    count = 0
    previous = 0
    with report.open(encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream):
            record_id = int(row["id"])
            if not (previous < record_id <= rows and is_rejected(record_id - 1, rows, rejects)
                    and int(row["rowport_record"]) == record_id + 1 and row["rowport_error"]
                    and row["price"] == "invalid"):
                raise ValueError("Rejection report does not match the generated invalid rows.")
            previous = record_id
            count += 1
    if count != rejects:
        raise ValueError("Rejection report count does not match the generated input.")
    return stored, count


def run(args, dsn):
    schema = "rowport_bench_" + uuid.uuid4().hex
    results = []
    # Stage the output before DB work; preserve any previous results on failure.
    with staged_report(args.output) as (target, output), tempfile.TemporaryDirectory(prefix="rowport-bench-") as directory:
        folder = Path(directory)
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
            try:
                conn.execute(sql.SQL("CREATE TABLE {}.items (id bigint PRIMARY KEY, name text NOT NULL, "
                                     "price numeric(12,2) NOT NULL CHECK (price > 0), "
                                     "quantity integer NOT NULL CHECK (quantity >= 0))").format(sql.Identifier(schema)))
                server_version = conn.info.server_version
                for rows in args.rows:
                    for percent in args.reject_percent:
                        rejects = int(Decimal(rows) * percent / 100)
                        source = folder / "input.csv"
                        generate_csv(source, rows, rejects)
                        for repetition in range(1, args.repeat + 1):
                            conn.execute(sql.SQL("TRUNCATE {}.items").format(sql.Identifier(schema)))
                            print(f"Running {rows:,} rows, {percent}% rejects, repetition {repetition}/{args.repeat}…", flush=True)
                            report, summary_path, metrics_path = [folder / name for name in ("rejects.csv", "summary.json", "metrics.json")]
                            for old in (report, summary_path, metrics_path):
                                old.unlink(missing_ok=True)
                            env = {**os.environ, "DATABASE_URL": dsn, "PYTHONIOENCODING": "utf-8"}
                            # Keep logs on disk: capturing progress in RAM would skew the harness.
                            with (folder / "worker.log").open("w", encoding="utf-8") as log:
                                process = subprocess.Popen([sys.executable, "-m", "scripts.benchmark", "--worker",
                                                            str(source), schema, str(report), str(summary_path), str(metrics_path)],
                                                           env=env, stdout=log, stderr=log)
                                try:
                                    code = process.wait(timeout=args.timeout)
                                finally:
                                    if process.poll() is None:
                                        process.terminate()
                                        try:
                                            process.wait(timeout=10)
                                        except subprocess.TimeoutExpired:
                                            process.kill()
                                            process.wait()
                            if code not in (0, 3):
                                raise ValueError("Import worker failed. Check PostgreSQL availability and available disk space.")
                            summary = json.loads(summary_path.read_text(encoding="utf-8"))
                            metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
                            stored, report_count = verify(conn, schema, rows, rejects, report, summary, code)
                            duration = metrics["duration_seconds"]
                            result = {"rows": rows, "reject_percent": str(percent), "expected_rejections": rejects,
                                      "repetition": repetition, "duration_seconds": round(duration, 6),
                                      "rows_per_second": round(rows / duration, 2), "peak_rss_bytes": metrics["peak_rss_bytes"],
                                      "input_bytes": source.stat().st_size, "database_rows": stored,
                                      "report_rows": report_count, "verified": True}
                            results.append(result)
                            print(f"  Verified: {stored:,} saved, {report_count:,} rejected; {duration:.2f}s; "
                                  f"{result['rows_per_second']:,.0f} rows/s; peak RSS {metrics['peak_rss_bytes'] / 1048576:.2f} MiB", flush=True)
            finally:
                conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
        payload = {"format_version": 1, "python_version": platform.python_version(), "platform": platform.platform(),
                   "postgresql_version_num": server_version,
                   "source_sha256": {name: hashlib.sha256(Path(__file__).with_name(name + ".py").read_bytes()).hexdigest()
                                     for name in ("benchmark", "cli", "config", "csv_io", "errors", "importer", "progress")},
                   "measurement": {"duration": "CLI execution through report and summary publication; excludes process startup, generation and verification",
                                   "memory": "Fresh worker process peak RSS, including Python runtime; excludes PostgreSQL and harness",
                                   "input": "Deterministic synthetic products; evenly distributed invalid numeric prices; Unicode, quoting and multiline fields",
                                   "execution": "Sequential runs on an empty table with a primary key and check constraints; non-interactive output; no cache reset"},
                   "results": results}
        json.dump(payload, output, indent=2)
        output.write("\n")
        write_report(target, output)
    print(f"Verified {len(results)} benchmark runs. Results: {args.output}")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "--worker":
        return worker(argv[1:])
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=positive_int, nargs="+", default=[100_000, 1_000_000])
    parser.add_argument("--reject-percent", type=percentage, nargs="+", default=[Decimal(0), Decimal(1), Decimal(10)])
    parser.add_argument("--repeat", type=positive_int, default=1)
    parser.add_argument("--timeout", type=positive_int, default=1800, help="Timeout per import in seconds (default: 1800)")
    parser.add_argument("--output", type=Path, default=Path("reports/benchmark.json"))
    args = parser.parse_args(argv)
    if sys.platform not in ("darwin", "linux"):
        parser.error("Peak RSS measurement requires Linux or macOS. Use the Docker benchmark on Windows.")
    # Deliberately do not fall back to .env or DATABASE_URL from another project.
    dsn = os.environ.get("ROWPORT_BENCHMARK_DSN")
    if not dsn:
        parser.error("Set ROWPORT_BENCHMARK_DSN to a disposable PostgreSQL database, or use compose.benchmark.yaml.")
    try:
        run(args, dsn)
    except KeyboardInterrupt:
        print("Benchmark interrupted; no new result file published.", file=sys.stderr)
        return 130
    except (OSError, ValueError, KeyError, InputError, csv.Error, psycopg.Error, subprocess.TimeoutExpired):
        print("Benchmark failed; no new result file published. Check the disposable database, timeout and output directory.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
