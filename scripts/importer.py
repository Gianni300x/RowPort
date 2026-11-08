import csv
import io
import json
import os
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path

import psycopg
from psycopg import sql

from .errors import InputError


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
              AND NOT a.attisdropped AND a.attgenerated = '' AND a.attidentity <> 'a'
            ORDER BY a.attnum
        """, (schema, table))
        return cur.fetchall()


def validate_headers(headers, columns):
    if not columns:
        raise InputError("The table does not exist, is unsupported, or has no insertable columns. Check --table and --schema, and that the table has insertable columns.")
    known = {name for name, _ in columns}
    unknown = set(headers) - known
    missing = {name for name, required in columns if required} - set(headers)
    errors = []
    if unknown:
        errors.append("Unknown columns: " + ", ".join(sorted(unknown)))
    if missing:
        errors.append("Missing required columns: " + ", ".join(sorted(missing)))
    if errors:
        raise InputError("; ".join(errors) + ". Check the CSV delimiter and table columns with rowport validate, or use --map CSV_COLUMN=TABLE_COLUMN.")


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


def rejection_message(exc):
    """Explain common database errors while preserving PostgreSQL's detail."""
    if isinstance(exc, psycopg.errors.InvalidTextRepresentation):
        explanation = "A value does not match the expected data type."
    elif isinstance(exc, psycopg.errors.NumericValueOutOfRange):
        explanation = "A number is outside the allowed range."
    elif isinstance(exc, psycopg.errors.NotNullViolation):
        explanation = "A required value is missing."
    elif isinstance(exc, psycopg.errors.UniqueViolation):
        explanation = "A value conflicts with an existing unique value."
    elif isinstance(exc, psycopg.errors.ForeignKeyViolation):
        explanation = "A referenced value does not meet a foreign key constraint."
    elif isinstance(exc, psycopg.errors.CheckViolation):
        explanation = "A value does not meet a table constraint."
    elif isinstance(exc, psycopg.DataError):
        explanation = "A value could not be accepted by PostgreSQL."
    else:
        explanation = "A database constraint rejected this row."
    detail = exc.diag.message_primary or str(exc)
    return f"{explanation} PostgreSQL: {detail}" if detail else explanation


def import_rows(conn, schema, table, headers, rows, report, progress=None, progress_interval=10_000, skip_duplicates=False, empty_as_null=False, error_counts=None, postgres_csv=False, report_headers=None):
    report_headers = headers if report_headers is None else report_headers
    statement = sql.SQL("INSERT INTO {}.{} ({}) VALUES ({})").format(
        sql.Identifier(schema), sql.Identifier(table),
        sql.SQL(", ").join(map(sql.Identifier, headers)),
        sql.SQL(", ").join(sql.Placeholder() for _ in headers),
    )
    rejected = 0
    saved = 0
    skipped = 0
    writer = csv.writer(report)
    writer.writerow([*report_headers, *report_metadata_headers(report_headers)])
    for number, row in enumerate(rows, start=2):
        if len(row) != len(headers):
            reason = f"Expected {len(headers)} values; got {len(row)}."
            write_rejection(report, number, row, reason, len(report_headers), postgres_csv=postgres_csv)
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
                    write_rejection(report, number, row, rejection_message(exc), len(report_headers), postgres_csv=postgres_csv)
                    rejected += 1
                    if error_counts is not None:
                        error_counts[error_category(exc)] += 1
            except (psycopg.DataError, psycopg.IntegrityError) as exc:
                write_rejection(report, number, row, rejection_message(exc), len(report_headers), postgres_csv=postgres_csv)
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


def report_metadata_headers(headers):
    used = set(headers)
    result = []
    for base in ("rowport_record", "rowport_error", "rowport_extra"):
        name = base
        while name in used:
            name += "_"
        used.add(name)
        result.append(name)
    return result


def retry_headers(headers):
    original = headers[:-3]
    if len(headers) < 4 or headers[-3:] != report_metadata_headers(original):
        raise InputError("Not a RowPort rejection report: expected its three diagnostic columns at the end.")
    return original


def retry_rows(rows, column_count):
    for row in rows:
        if len(row) != column_count + 3:
            raise InputError("Invalid rejection report row. Keep all original and diagnostic columns when retrying.")
        if row[-1]:
            raise InputError("A rejected row has extra fields. Move their values into the original columns and clear the rowport_extra column before retrying.")
        yield row[:-3]


def write_rejection(report, number, row, reason, column_count, postgres_csv=False):
    extra = json.dumps(row[column_count:], ensure_ascii=False) if len(row) > column_count else ""
    fields = [*row[:column_count], *([None if postgres_csv else ""] * max(0, column_count - len(row))), number, reason, extra]
    if not postgres_csv:
        csv.writer(report).writerow(fields)
        return
    # QUOTE_ALL preserves empty text; SQL NULL is the only unquoted empty field.
    encoded = []
    for value in fields:
        if value is None:
            encoded.append("")
        else:
            buffer = io.StringIO(newline="")
            csv.writer(buffer, quoting=csv.QUOTE_ALL).writerow([value])
            encoded.append(buffer.getvalue()[:-2])
    report.write(",".join(encoded) + "\r\n")


def write_report(path, report):
    report.flush()
    report.close()
    os.replace(report.name, path)


def same_file(first, second):
    if first.resolve() == second.resolve():
        return True
    try:
        return os.path.samefile(first, second)
    except FileNotFoundError:
        return False


@contextmanager
def staged_report(destination):
    target = destination.resolve()
    try:
        mode = target.stat().st_mode
    except FileNotFoundError:
        pass
    else:
        if not stat.S_ISREG(mode):
            raise InputError(f"Rejection report destination is not a regular file: {destination}")
    temporary = tempfile.NamedTemporaryFile(mode="w+", encoding="utf-8", newline="",
                                            dir=target.parent, prefix=".rowport-", delete=False)
    temporary_path = Path(temporary.name)
    try:
        with temporary:
            yield target, temporary
    finally:
        temporary_path.unlink(missing_ok=True)
