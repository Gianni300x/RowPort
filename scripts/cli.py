import argparse
import csv
import io
import os
import shutil
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

import psycopg
from psycopg import sql


class InputError(Exception):
    """The CSV or target table cannot be used for an import."""


def database_url(path=Path(".env")):
    """Read the connection from the environment or the current directory's .env."""
    if os.environ.get("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    if not path.exists():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        if separator and key.strip() == "DATABASE_URL":
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            return value or None
    return None


@contextmanager
def read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as source:
        reader = csv.reader(source, strict=True)
        headers = next(reader, None)
        if not headers or any(not name.strip() for name in headers):
            raise InputError("CSV headers must not be empty.")
        if len(set(headers)) != len(headers):
            raise InputError("The CSV contains duplicate columns.")
        yield headers, reader


def table_columns(conn, schema, table):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT a.attname,
                   a.attnotnull AND NOT a.atthasdef AND a.attidentity = ''
            FROM pg_catalog.pg_attribute a
            JOIN pg_catalog.pg_class c ON c.oid = a.attrelid
            JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = %s AND c.relname = %s
              AND c.relkind IN ('r', 'p') AND a.attnum > 0
              AND NOT a.attisdropped AND a.attgenerated = ''
            ORDER BY a.attnum
        """, (schema, table))
        return cur.fetchall()


def validate_headers(headers, columns):
    if not columns:
        raise InputError("The table does not exist, is unsupported, or has no insertable columns.")
    known = {name for name, _ in columns}
    unknown = set(headers) - known
    missing = {name for name, required in columns if required} - set(headers)
    errors = []
    if unknown:
        errors.append("Unknown columns: " + ", ".join(sorted(unknown)))
    if missing:
        errors.append("Missing required columns: " + ", ".join(sorted(missing)))
    if errors:
        raise InputError("; ".join(errors))


def import_rows(conn, schema, table, headers, rows, report, progress=None, progress_interval=10_000):
    statement = sql.SQL("INSERT INTO {}.{} ({}) VALUES ({})").format(
        sql.Identifier(schema), sql.Identifier(table),
        sql.SQL(", ").join(map(sql.Identifier, headers)),
        sql.SQL(", ").join(sql.Placeholder() for _ in headers),
    )
    rejected = 0
    saved = 0
    writer = csv.writer(report)
    writer.writerow(["row_number", "csv_row", "reason"])
    for number, row in enumerate(rows, start=2):
        if len(row) != len(headers):
            reason = f"Expected {len(headers)} values; got {len(row)}."
            write_rejection(writer, number, row, reason)
            rejected += 1
        else:
            try:
                # A savepoint isolates invalid rows; the outer transaction remains atomic.
                with conn.transaction():
                    conn.execute(statement, row)
            except (psycopg.DataError, psycopg.IntegrityError) as exc:
                write_rejection(writer, number, row, exc.diag.message_primary or str(exc))
                rejected += 1
            else:
                saved += 1
        processed = number - 1
        if progress and processed % progress_interval == 0:
            progress(processed, saved, rejected)
    return saved, rejected


def show_progress(processed, saved, rejected):
    print(f"Progress: {processed} processed; {saved} saved pending commit; "
          f"{rejected} rejected.", flush=True)


def write_rejection(writer, number, row, reason):
    buffer = io.StringIO(newline="")
    csv.writer(buffer, lineterminator="").writerow(row)
    writer.writerow([number, buffer.getvalue(), reason])


def write_report(path, report):
    report.seek(0)
    with path.open("w", encoding="utf-8", newline="") as target:
        shutil.copyfileobj(report, target)


def validation_main(argv):
    parser = argparse.ArgumentParser(prog="rowport validate", description="Check CSV headers against an existing PostgreSQL table without importing rows.")
    parser.add_argument("csv", type=Path, help="Input CSV file")
    parser.add_argument("--dsn", help="PostgreSQL connection string (overrides DATABASE_URL)")
    parser.add_argument("--table", required=True, help="Table name")
    parser.add_argument("--schema", default="public", help="Schema (default: public)")
    args = parser.parse_args(argv)
    try:
        dsn = args.dsn or database_url()
        if not dsn:
            parser.error("Provide --dsn or set DATABASE_URL in the environment or .env file.")
        with read_csv(args.csv) as (headers, _):
            with psycopg.connect(dsn) as conn:
                conn.read_only = True
                columns = table_columns(conn, args.schema, args.table)
            print("CSV columns: " + ", ".join(headers))
            if columns:
                print("Table insertable columns: " + ", ".join(name for name, _ in columns))
                print("Required columns: " + (", ".join(name for name, required in columns if required) or "(none)"))
            validate_headers(headers, columns)
    except (OSError, UnicodeError, csv.Error, InputError, psycopg.Error) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print("Headers are compatible. No rows were checked or imported.")
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "validate" and (len(argv) == 1 or argv[1] == "--help" or not argv[1].startswith("-")):
        return validation_main(argv[1:])
    parser = argparse.ArgumentParser(description="Import a CSV into an existing PostgreSQL table.")
    parser.add_argument("csv", type=Path, help="Input CSV file")
    parser.add_argument("--dsn", help="PostgreSQL connection string (overrides DATABASE_URL)")
    parser.add_argument("--table", required=True, help="Table name")
    parser.add_argument("--schema", default="public", help="Schema (default: public)")
    parser.add_argument("--rejects", type=Path, required=True, help="Rejected rows CSV file")
    parser.epilog = "To check headers without importing: rowport validate CSV --table TABLE"
    args = parser.parse_args(argv)
    try:
        dsn = args.dsn or database_url()
    except (OSError, UnicodeError) as exc:
        print(f"Error reading .env: {exc}", file=sys.stderr)
        return 1
    if not dsn:
        parser.error("Provide --dsn or set DATABASE_URL in the environment or .env file.")
    if args.csv.resolve() == args.rejects.resolve():
        parser.error("The rejection report must differ from the input CSV.")
    try:
        with read_csv(args.csv) as (headers, rows), tempfile.TemporaryFile(mode="w+", encoding="utf-8", newline="") as report:
            with psycopg.connect(dsn) as conn:
                with conn.transaction():
                    validate_headers(headers, table_columns(conn, args.schema, args.table))
                    saved, rejected = import_rows(conn, args.schema, args.table, headers, rows, report,
                                                  progress=show_progress)
            try:
                write_report(args.rejects, report)
            except OSError as exc:
                print(f"Saved {saved} rows, but could not write the report: {exc}", file=sys.stderr)
                return 1
    except (OSError, UnicodeError, csv.Error, InputError, psycopg.Error) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(f"Rows saved: {saved}. Rows rejected: {rejected}. Report: {args.rejects}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
