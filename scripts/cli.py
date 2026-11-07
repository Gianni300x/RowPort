import argparse
import csv
import sys
from pathlib import Path

import psycopg
from psycopg import sql


class InputError(Exception):
    """The CSV or target table cannot be used for an import."""


def read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as source:
        reader = csv.reader(source, strict=True)
        headers = next(reader, None)
        if not headers or any(not name.strip() for name in headers):
            raise InputError("CSV headers must not be empty.")
        if len(set(headers)) != len(headers):
            raise InputError("The CSV contains duplicate columns.")
        rows = list(reader)
    return headers, rows


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
    if unknown:
        raise InputError("Unknown columns: " + ", ".join(sorted(unknown)))
    if missing:
        raise InputError("Missing required columns: " + ", ".join(sorted(missing)))


def import_rows(conn, schema, table, headers, rows):
    statement = sql.SQL("INSERT INTO {}.{} ({}) VALUES ({})").format(
        sql.Identifier(schema), sql.Identifier(table),
        sql.SQL(", ").join(map(sql.Identifier, headers)),
        sql.SQL(", ").join(sql.Placeholder() for _ in headers),
    )
    rejected = []
    saved = 0
    for number, row in enumerate(rows, start=2):
        if len(row) != len(headers):
            rejected.append((number, row, f"Expected {len(headers)} values; got {len(row)}."))
            continue
        try:
            # A savepoint isolates invalid rows; the outer transaction remains atomic.
            with conn.transaction():
                conn.execute(statement, row)
        except (psycopg.DataError, psycopg.IntegrityError) as exc:
            rejected.append((number, row, exc.diag.message_primary or str(exc)))
        else:
            saved += 1
    return saved, rejected


def write_report(path, headers, rejected):
    with path.open("w", encoding="utf-8", newline="") as target:
        writer = csv.writer(target)
        # Preserve irregular rows without silently dropping extra fields.
        writer.writerow(["row_number", "csv_row", "reason"])
        for number, row, reason in rejected:
            import io
            buffer = io.StringIO(newline="")
            csv.writer(buffer, lineterminator="").writerow(row)
            writer.writerow([number, buffer.getvalue(), reason])


def main(argv=None):
    parser = argparse.ArgumentParser(description="Import a CSV into an existing PostgreSQL table.")
    parser.add_argument("csv", type=Path, help="Input CSV file")
    parser.add_argument("--dsn", required=True, help="PostgreSQL connection string")
    parser.add_argument("--table", required=True, help="Table name")
    parser.add_argument("--schema", default="public", help="Schema (default: public)")
    parser.add_argument("--rejects", type=Path, required=True, help="Rejected rows CSV file")
    args = parser.parse_args(argv)
    if args.csv.resolve() == args.rejects.resolve():
        parser.error("The rejection report must differ from the input CSV.")
    try:
        headers, rows = read_csv(args.csv)
        with psycopg.connect(args.dsn) as conn:
            with conn.transaction():
                validate_headers(headers, table_columns(conn, args.schema, args.table))
                saved, rejected = import_rows(conn, args.schema, args.table, headers, rows)
        try:
            write_report(args.rejects, headers, rejected)
        except OSError as exc:
            print(f"Saved {saved} rows, but could not write the report: {exc}", file=sys.stderr)
            return 1
    except (OSError, UnicodeError, csv.Error, InputError, psycopg.Error) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(f"Rows saved: {saved}. Rows rejected: {len(rejected)}. Report: {args.rejects}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
