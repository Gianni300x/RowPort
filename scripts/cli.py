import argparse
import csv
import itertools
import json
import os
import sys
from collections import Counter
from pathlib import Path

import psycopg

from .config import CONFIG_FIELDS, apply_config, database_url
from .csv_io import CSV_FIELD_LIMIT, csv_delimiter, csv_encoding, map_headers, read_csv
from .errors import InputError
from .importer import (import_rows, same_file, show_progress, staged_report,
                       table_columns, validate_headers, write_report)


def configure_windows_output(platform=None, streams=None):
    """Use UTF-8 for redirected Windows output unless explicitly configured."""
    if (sys.platform if platform is None else platform) != "win32" or os.environ.get("PYTHONIOENCODING"):
        return
    for stream in (sys.stdout, sys.stderr) if streams is None else streams:
        if not stream.isatty() and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")


def display_error(exc):
    if (isinstance(exc, psycopg.OperationalError)
            and (not exc.sqlstate or exc.sqlstate.startswith(("08", "28", "57P")))):
        message = ("Could not connect to PostgreSQL or the connection was lost. "
                   "Check that PostgreSQL is running and verify the host, port, database and credentials.")
    elif isinstance(exc, psycopg.Error) and not exc.sqlstate:
        message = "Database operation failed. Check the connection settings and PostgreSQL availability."
    elif isinstance(exc, FileNotFoundError):
        message = f"File not found: {exc.filename}. Check the path and current directory."
    elif isinstance(exc, PermissionError):
        message = f"Permission denied: {exc.filename}. Check access to the file or directory."
    elif isinstance(exc, UnicodeDecodeError):
        message = (f"Could not decode the CSV using {exc.encoding}. "
                   "Check the file encoding or specify it with --encoding, for example --encoding latin-1.")
    elif isinstance(exc, csv.Error):
        if "field larger than field limit" in str(exc):
            message = f"Invalid CSV: a field exceeds the {CSV_FIELD_LIMIT:,}-character limit."
        else:
            message = f"Invalid CSV: {exc}. Check the delimiter and quoted fields."
    else:
        message = str(exc)
    print(f"Error: {message}", file=sys.stderr)


def positive_int(value):
    try:
        result = int(value)
        if result <= 0:
            raise ValueError
        return result
    except ValueError:
        raise argparse.ArgumentTypeError("Must be a positive integer.") from None


def preview_main(argv):
    parser = argparse.ArgumentParser(prog="rowport preview", usage="%(prog)s CSV [options]", description="Preview CSV rows without connecting to PostgreSQL.")
    parser.add_argument("csv", type=Path, help="Input CSV file")
    settings = parser.add_argument_group("Configuration")
    settings.add_argument("--config", type=Path, help="JSON configuration file")
    csv_options = parser.add_argument_group("CSV format and columns")
    csv_options.add_argument("--delimiter", type=csv_delimiter, default=",", help="CSV delimiter (default: comma)")
    csv_options.add_argument("--encoding", type=csv_encoding, default="utf-8-sig", help="CSV encoding (default: UTF-8)")
    csv_options.add_argument("--map", action="append", default=[], metavar="CSV_COLUMN=TABLE_COLUMN", help="Map CSV columns to table columns")
    csv_options.add_argument("--postgres-csv", action=argparse.BooleanOptionalAction, default=False, help="Interpret unquoted empty fields as NULL")
    parser.add_argument_group("Preview").add_argument("--limit", type=positive_int, default=5, help="Maximum rows to display (default: 5)")
    apply_config(parser, argv, {"delimiter", "encoding", "map", "postgres_csv"})
    args = parser.parse_args(argv)
    try:
        with read_csv(args.csv, delimiter=args.delimiter, encoding=args.encoding, postgres_csv=args.postgres_csv) as (headers, rows):
            mapped = map_headers(headers, args.map)
            print("CSV columns: " + ", ".join(headers))
            if args.map:
                print("Mapped columns: " + ", ".join(mapped))
            print("Preview (JSON arrays, interpreted values):" if args.postgres_csv else "Preview (JSON arrays, original values):")
            for number, row in enumerate(itertools.islice(rows, args.limit), start=2):
                print(f"{number}: {json.dumps(row, ensure_ascii=False)}")
    except (OSError, UnicodeError, csv.Error, InputError) as exc:
        display_error(exc)
        return 1
    print("Preview only: no database connection or inserts.")
    return 0


def validation_main(argv):
    parser = argparse.ArgumentParser(prog="rowport validate", usage="%(prog)s CSV [options]", description="Check CSV headers against an existing PostgreSQL table without importing rows.")
    parser.add_argument("csv", type=Path, help="Input CSV file")
    target = parser.add_argument_group("Database and table")
    target.add_argument("--table", help="Table name (required unless set in --config)")
    target.add_argument("--dsn", help="PostgreSQL connection string (overrides DATABASE_URL)")
    target.add_argument("--schema", default="public", help="Schema (default: public)")
    settings = parser.add_argument_group("Configuration")
    settings.add_argument("--config", type=Path, help="JSON configuration file")
    csv_options = parser.add_argument_group("CSV format and columns")
    csv_options.add_argument("--delimiter", type=csv_delimiter, default=",", help="CSV delimiter (default: comma)")
    csv_options.add_argument("--encoding", type=csv_encoding, default="utf-8-sig", help="Input CSV encoding (default: UTF-8, with optional BOM)")
    csv_options.add_argument("--map", action="append", default=[], metavar="CSV_COLUMN=TABLE_COLUMN", help="Map a CSV column to a table column; repeat as needed")
    csv_options.add_argument("--postgres-csv", action=argparse.BooleanOptionalAction, default=False, help="Use PostgreSQL CSV empty-field conventions")
    apply_config(parser, argv, {"table", "schema", "delimiter", "encoding", "map", "postgres_csv"})
    args = parser.parse_args(argv)
    if not args.table:
        parser.error("--table is required (or set table in --config).")
    try:
        dsn = args.dsn or database_url()
        if not dsn:
            parser.error("Provide --dsn or set DATABASE_URL in the environment or .env file.")
        with read_csv(args.csv, delimiter=args.delimiter, encoding=args.encoding, postgres_csv=args.postgres_csv) as (headers, _):
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
        display_error(exc)
        return 1
    print("Headers are compatible. No rows were checked or imported.")
    return 0


def main(argv=None):
    configure_windows_output()
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "validate":
        return validation_main(argv[1:])
    if argv and argv[0] == "preview":
        return preview_main(argv[1:])
    parser = argparse.ArgumentParser(usage="%(prog)s CSV [options]", description="Import a CSV into an existing PostgreSQL table.")
    parser.add_argument("csv", type=Path, help="Input CSV file")
    essential = parser.add_argument_group("Required for import", "These can also be supplied by --config.")
    essential.add_argument("--table", help="Existing PostgreSQL table")
    essential.add_argument("--rejects", type=Path, help="Rejected rows CSV file")
    target = parser.add_argument_group("Database connection")
    target.add_argument("--dsn", help="PostgreSQL connection string (overrides DATABASE_URL)")
    target.add_argument("--schema", default="public", help="Schema (default: public)")
    settings = parser.add_argument_group("Configuration")
    settings.add_argument("--config", type=Path, help="JSON configuration file")
    csv_options = parser.add_argument_group("CSV format and columns")
    csv_options.add_argument("--delimiter", type=csv_delimiter, default=",", help="CSV delimiter (default: comma)")
    csv_options.add_argument("--encoding", type=csv_encoding, default="utf-8-sig", help="Input CSV encoding (default: UTF-8, with optional BOM)")
    csv_options.add_argument("--map", action="append", default=[], metavar="CSV_COLUMN=TABLE_COLUMN", help="Map a CSV column to a table column; repeat as needed")
    csv_options.add_argument("--postgres-csv", action=argparse.BooleanOptionalAction, default=False, help="Use PostgreSQL CSV empty-field conventions")
    behavior = parser.add_argument_group("Import behavior")
    behavior.add_argument("--skip-duplicates", action=argparse.BooleanOptionalAction, default=False, help="Skip unique constraint violations on the target table")
    behavior.add_argument("--empty-as-null", action=argparse.BooleanOptionalAction, default=False, help="Insert empty CSV fields as SQL NULL")
    behavior.add_argument("--dry-run", action="store_true", help="Test import and roll back inserted rows")
    parser.epilog = "Check headers: rowport validate CSV --table TABLE. Preview rows: rowport preview CSV."
    apply_config(parser, argv, set(CONFIG_FIELDS))
    args = parser.parse_args(argv)
    if not args.table or not args.rejects:
        parser.error("--table and --rejects are required (or set them in --config).")
    if args.postgres_csv and args.empty_as_null:
        parser.error("--postgres-csv cannot be combined with --empty-as-null.")
    try:
        dsn = args.dsn or database_url()
    except (OSError, UnicodeError) as exc:
        display_error(exc)
        return 1
    if not dsn:
        parser.error("Provide --dsn or set DATABASE_URL in the environment or .env file.")
    try:
        if same_file(args.csv, args.rejects):
            raise InputError("The rejection report must differ from the input CSV (including hard links).")
        if args.config and same_file(args.config, args.rejects):
            raise InputError("The rejection report must differ from the configuration file (including hard links).")
        error_counts = Counter()
        with read_csv(args.csv, delimiter=args.delimiter, encoding=args.encoding, postgres_csv=args.postgres_csv) as (headers, rows), staged_report(args.rejects) as (target, report):
            headers = map_headers(headers, args.map)
            with psycopg.connect(dsn) as conn:
                with conn.transaction():
                    validate_headers(headers, table_columns(conn, args.schema, args.table))
                    saved, rejected, skipped = import_rows(conn, args.schema, args.table, headers, rows, report,
                                                           progress=show_progress, skip_duplicates=args.skip_duplicates,
                                                           empty_as_null=args.empty_as_null, error_counts=error_counts,
                                                           postgres_csv=args.postgres_csv)
                    report.flush()
                    if args.dry_run:
                        conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
                        raise psycopg.Rollback()
            try:
                write_report(target, report)
            except OSError as exc:
                message = "Dry run rolled back" if args.dry_run else f"Saved {saved} rows"
                print(f"{message}, but could not write the report: {exc}", file=sys.stderr)
                return 1
    except (OSError, UnicodeError, csv.Error, InputError, psycopg.Error) as exc:
        display_error(exc)
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
