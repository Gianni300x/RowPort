import csv
import io
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
        raise InputError("; ".join(errors) + ". Check the CSV headers and table columns, or use --map CSV_COLUMN=TABLE_COLUMN.")


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


def import_rows(conn, schema, table, headers, rows, report, progress=None, progress_interval=10_000, skip_duplicates=False, empty_as_null=False, error_counts=None, postgres_csv=False):
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
            write_rejection(writer, number, row, reason, postgres_csv=postgres_csv)
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
                    write_rejection(writer, number, row, exc.diag.message_primary or str(exc), postgres_csv=postgres_csv)
                    rejected += 1
                    if error_counts is not None:
                        error_counts[error_category(exc)] += 1
            except (psycopg.DataError, psycopg.IntegrityError) as exc:
                write_rejection(writer, number, row, exc.diag.message_primary or str(exc), postgres_csv=postgres_csv)
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


def write_rejection(writer, number, row, reason, postgres_csv=False):
    if postgres_csv:
        fields = []
        for value in row:
            if value is None:
                fields.append("")
            else:
                buffer = io.StringIO(newline="")
                csv.writer(buffer, quoting=csv.QUOTE_ALL, lineterminator="").writerow([value])
                fields.append(buffer.getvalue())
        encoded = ",".join(fields)
    else:
        buffer = io.StringIO(newline="")
        csv.writer(buffer, lineterminator="").writerow(row)
        encoded = buffer.getvalue()
    writer.writerow([number, encoded, reason])


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
