import argparse
import csv
import io
import itertools
import json
import os
import shutil
import sys
import tempfile
from collections import Counter
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


def csv_delimiter(value):
    if len(value) != 1 or value in "\r\n\0\"":
        raise argparse.ArgumentTypeError("Delimiter must be one character other than a quote, newline, or NUL.")
    return value


def csv_encoding(value):
    try:
        with io.TextIOWrapper(io.BytesIO(), encoding=value):
            pass
    except (LookupError, ValueError):
        raise argparse.ArgumentTypeError(f"Unknown or unsupported text encoding: {value}") from None
    return value


@contextmanager
def read_csv(path, delimiter=",", encoding="utf-8-sig"):
    with path.open(encoding=encoding, newline="") as source:
        reader = csv.reader(source, delimiter=delimiter, strict=True)
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


def map_headers(headers, mappings):
    mapping = {}
    for item in mappings:
        source, sep, target = item.partition("=")
        if not sep or not source.strip() or not target.strip():
            raise InputError("Each --map must have the form CSV_COLUMN=TABLE_COLUMN.")
        if source in mapping:
            raise InputError(f"Duplicate mapping for CSV column: {source}")
        if source not in headers:
            raise InputError(f"Mapped CSV column not found: {source}")
        mapping[source] = target
    result = [mapping.get(header, header) for header in headers]
    duplicates = sorted(name for name, count in Counter(result).items() if count > 1)
    if duplicates:
        raise InputError("Multiple CSV columns map to the same table column: " + ", ".join(duplicates))
    return result


CONFIG_FIELDS = {
    "table": str, "schema": str, "delimiter": str, "encoding": str,
    "map": list, "rejects": str, "skip_duplicates": bool, "empty_as_null": bool,
}


def apply_config(parser, argv, fields):
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--config", type=Path)
    known, _ = pre.parse_known_args(argv)
    if not known.config:
        return
    try:
        config = json.loads(known.config.read_text(encoding="utf-8"))
        if not isinstance(config, dict):
            raise ValueError("configuration must be a JSON object")
        unknown = set(config) - set(CONFIG_FIELDS)
        if unknown:
            raise ValueError("unknown configuration keys: " + ", ".join(sorted(unknown)))
        for key, value in config.items():
            if type(value) is not CONFIG_FIELDS[key]:
                raise ValueError(f"{key} must be {CONFIG_FIELDS[key].__name__}")
            if key == "map" and not all(isinstance(item, str) for item in value):
                raise ValueError("map must contain strings")
            if key in ("table", "schema", "rejects") and not value.strip():
                raise ValueError(f"{key} cannot be empty")
        if "delimiter" in config:
            csv_delimiter(config["delimiter"])
        if "encoding" in config:
            csv_encoding(config["encoding"])
        defaults = {key: value for key, value in config.items() if key in fields}
        if "rejects" in defaults:
            defaults["rejects"] = Path(defaults["rejects"])
        if any(arg == "--map" or arg.startswith("--map=") for arg in argv):
            defaults.pop("map", None)
        parser.set_defaults(**defaults)
    except (OSError, UnicodeError, ValueError, argparse.ArgumentTypeError) as exc:
        parser.error(f"Invalid configuration: {exc}")


def positive_int(value):
    try:
        result = int(value)
        if result <= 0:
            raise ValueError
        return result
    except ValueError:
        raise argparse.ArgumentTypeError("Must be a positive integer.") from None


def preview_main(argv):
    parser = argparse.ArgumentParser(prog="rowport preview", description="Preview CSV rows without connecting to PostgreSQL.")
    parser.add_argument("csv", type=Path, help="Input CSV file")
    parser.add_argument("--config", type=Path, help="JSON configuration file")
    parser.add_argument("--delimiter", type=csv_delimiter, default=",")
    parser.add_argument("--encoding", type=csv_encoding, default="utf-8-sig")
    parser.add_argument("--map", action="append", default=[], metavar="CSV_COLUMN=TABLE_COLUMN")
    parser.add_argument("--limit", type=positive_int, default=5, help="Maximum rows to display (default: 5)")
    apply_config(parser, argv, {"delimiter", "encoding", "map"})
    args = parser.parse_args(argv)
    try:
        with read_csv(args.csv, delimiter=args.delimiter, encoding=args.encoding) as (headers, rows):
            mapped = map_headers(headers, args.map)
            print("CSV columns: " + ", ".join(headers))
            if args.map:
                print("Mapped columns: " + ", ".join(mapped))
            print("Preview (JSON arrays, original values):")
            for number, row in enumerate(itertools.islice(rows, args.limit), start=2):
                print(f"{number}: {json.dumps(row, ensure_ascii=False)}")
    except (OSError, UnicodeError, csv.Error, InputError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print("Preview only: no database connection or inserts.")
    return 0


def error_category(exc):
    if isinstance(exc, psycopg.errors.UniqueViolation):
        return "Unique constraint"
    if isinstance(exc, psycopg.errors.NotNullViolation):
        return "Required value"
    if isinstance(exc, psycopg.errors.CheckViolation):
        return "Check constraint"
    if isinstance(exc, psycopg.errors.ForeignKeyViolation):
        return "Foreign key constraint"
    if isinstance(exc, psycopg.DataError):
        return "Invalid data"
    return "Other integrity constraint"


def import_rows(conn, schema, table, headers, rows, report, progress=None, progress_interval=10_000, skip_duplicates=False, empty_as_null=False, error_counts=None):
    statement = sql.SQL("INSERT INTO {}.{} ({}) VALUES ({})").format(
        sql.Identifier(schema), sql.Identifier(table),
        sql.SQL(", ").join(map(sql.Identifier, headers)),
        sql.SQL(", ").join(sql.Placeholder() for _ in headers),
    )
    rejected = 0
    saved = 0
    skipped = 0
    writer = csv.writer(report)
    writer.writerow(["row_number", "csv_row", "reason"])
    for number, row in enumerate(rows, start=2):
        if len(row) != len(headers):
            reason = f"Expected {len(headers)} values; got {len(row)}."
            write_rejection(writer, number, row, reason)
            rejected += 1
            if error_counts is not None:
                error_counts["Incorrect field count"] += 1
        else:
            try:
                # A savepoint isolates invalid rows; the outer transaction remains atomic.
                with conn.transaction():
                    values = [None if value == "" else value for value in row] if empty_as_null else row
                    conn.execute(statement, values)
            except psycopg.errors.UniqueViolation as exc:
                if skip_duplicates and exc.diag.schema_name == schema and exc.diag.table_name == table:
                    skipped += 1
                else:
                    write_rejection(writer, number, row, exc.diag.message_primary or str(exc))
                    rejected += 1
                    if error_counts is not None:
                        error_counts[error_category(exc)] += 1
            except (psycopg.DataError, psycopg.IntegrityError) as exc:
                write_rejection(writer, number, row, exc.diag.message_primary or str(exc))
                rejected += 1
                if error_counts is not None:
                    error_counts[error_category(exc)] += 1
            else:
                saved += 1
        processed = number - 1
        if progress and processed % progress_interval == 0:
            progress(processed, saved, rejected, skipped)
    return saved, rejected, skipped


def show_progress(processed, saved, rejected, skipped=0):
    message = (f"Progress: {processed} processed; {saved} saved pending commit; "
               f"{rejected} rejected.")
    if skipped:
        message += f" {skipped} duplicates skipped."
    print(message, flush=True)


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
    parser.add_argument("--table", help="Table name")
    parser.add_argument("--config", type=Path, help="JSON configuration file")
    parser.add_argument("--schema", default="public", help="Schema (default: public)")
    parser.add_argument("--delimiter", type=csv_delimiter, default=",", help="CSV delimiter (default: comma)")
    parser.add_argument("--encoding", type=csv_encoding, default="utf-8-sig", help="Input CSV encoding (default: UTF-8, with optional BOM)")
    parser.add_argument("--map", action="append", default=[], metavar="CSV_COLUMN=TABLE_COLUMN", help="Map a CSV column to a table column; repeat as needed")
    apply_config(parser, argv, {"table", "schema", "delimiter", "encoding", "map"})
    args = parser.parse_args(argv)
    if not args.table:
        parser.error("--table is required (or set table in --config).")
    try:
        dsn = args.dsn or database_url()
        if not dsn:
            parser.error("Provide --dsn or set DATABASE_URL in the environment or .env file.")
        with read_csv(args.csv, delimiter=args.delimiter, encoding=args.encoding) as (headers, _):
            mapped = map_headers(headers, args.map)
            with psycopg.connect(dsn) as conn:
                conn.read_only = True
                columns = table_columns(conn, args.schema, args.table)
            print("CSV columns: " + ", ".join(headers))
            if args.map:
                print("Mapped columns: " + ", ".join(mapped))
            if columns:
                print("Table insertable columns: " + ", ".join(name for name, _ in columns))
                print("Required columns: " + (", ".join(name for name, required in columns if required) or "(none)"))
            validate_headers(mapped, columns)
    except (OSError, UnicodeError, csv.Error, InputError, psycopg.Error) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print("Headers are compatible. No rows were checked or imported.")
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "validate" and (len(argv) == 1 or argv[1] == "--help" or not argv[1].startswith("-")):
        return validation_main(argv[1:])
    if argv and argv[0] == "preview" and (len(argv) == 1 or argv[1] == "--help" or not argv[1].startswith("-")):
        return preview_main(argv[1:])
    parser = argparse.ArgumentParser(description="Import a CSV into an existing PostgreSQL table.")
    parser.add_argument("csv", type=Path, help="Input CSV file")
    parser.add_argument("--dsn", help="PostgreSQL connection string (overrides DATABASE_URL)")
    parser.add_argument("--table", help="Table name")
    parser.add_argument("--config", type=Path, help="JSON configuration file")
    parser.add_argument("--schema", default="public", help="Schema (default: public)")
    parser.add_argument("--delimiter", type=csv_delimiter, default=",", help="CSV delimiter (default: comma)")
    parser.add_argument("--encoding", type=csv_encoding, default="utf-8-sig", help="Input CSV encoding (default: UTF-8, with optional BOM)")
    parser.add_argument("--map", action="append", default=[], metavar="CSV_COLUMN=TABLE_COLUMN", help="Map a CSV column to a table column; repeat as needed")
    parser.add_argument("--rejects", type=Path, help="Rejected rows CSV file")
    parser.add_argument("--skip-duplicates", action=argparse.BooleanOptionalAction, default=False, help="Skip unique constraint violations on the target table")
    parser.add_argument("--empty-as-null", action=argparse.BooleanOptionalAction, default=False, help="Insert empty CSV fields as SQL NULL")
    parser.add_argument("--dry-run", action="store_true", help="Test import and roll back inserted rows")
    parser.epilog = "Check headers: rowport validate CSV --table TABLE. Preview rows: rowport preview CSV."
    apply_config(parser, argv, set(CONFIG_FIELDS))
    args = parser.parse_args(argv)
    if not args.table or not args.rejects:
        parser.error("--table and --rejects are required (or set them in --config).")
    try:
        dsn = args.dsn or database_url()
    except (OSError, UnicodeError) as exc:
        print(f"Error reading .env: {exc}", file=sys.stderr)
        return 1
    if not dsn:
        parser.error("Provide --dsn or set DATABASE_URL in the environment or .env file.")
    if args.csv.resolve() == args.rejects.resolve():
        parser.error("The rejection report must differ from the input CSV.")
    if args.config and args.config.resolve() == args.rejects.resolve():
        parser.error("The rejection report must differ from the configuration file.")
    try:
        error_counts = Counter()
        with read_csv(args.csv, delimiter=args.delimiter, encoding=args.encoding) as (headers, rows), tempfile.TemporaryFile(mode="w+", encoding="utf-8", newline="") as report:
            headers = map_headers(headers, args.map)
            with psycopg.connect(dsn) as conn:
                with conn.transaction():
                    validate_headers(headers, table_columns(conn, args.schema, args.table))
                    saved, rejected, skipped = import_rows(conn, args.schema, args.table, headers, rows, report,
                                                           progress=show_progress, skip_duplicates=args.skip_duplicates,
                                                           empty_as_null=args.empty_as_null, error_counts=error_counts)
                    if args.dry_run:
                        conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
                        raise psycopg.Rollback()
            try:
                write_report(args.rejects, report)
            except OSError as exc:
                message = "Dry run rolled back" if args.dry_run else f"Saved {saved} rows"
                print(f"{message}, but could not write the report: {exc}", file=sys.stderr)
                return 1
    except (OSError, UnicodeError, csv.Error, InputError, psycopg.Error) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    summary = (f"Dry run: rows that would be saved: {saved}. Rows rejected: {rejected}."
               if args.dry_run else f"Rows saved: {saved}. Rows rejected: {rejected}.")
    if args.skip_duplicates:
        summary += f" Duplicates skipped: {skipped}."
    print(f"{summary} Report: {args.rejects}")
    if error_counts:
        print("Rejection summary:")
        for category, count in sorted(error_counts.items()):
            print(f"  {category}: {count}")
    if args.dry_run:
        print("Dry run rolled back the transaction; no rows were saved.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
