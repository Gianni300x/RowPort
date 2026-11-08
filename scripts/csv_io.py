import argparse
import csv
import io
import itertools
from collections import Counter
from contextlib import contextmanager

from .errors import InputError


CSV_FIELD_LIMIT = 16 * 1024 * 1024  # Characters per field.


def csv_delimiter(value):
    if len(value) != 1 or value in "\r\n\0\"":
        raise argparse.ArgumentTypeError("Delimiter must be one character other than a quote, newline, or NUL; for example, --delimiter ';'.")
    return value


def csv_encoding(value):
    try:
        with io.TextIOWrapper(io.BytesIO(), encoding=value):
            pass
    except (LookupError, ValueError):
        raise argparse.ArgumentTypeError(f"Unknown or unsupported text encoding: {value}. Try --encoding utf-8-sig or --encoding latin-1.") from None
    return value


class RecordingLines:
    """Capture the physical lines consumed by csv.reader for one record."""

    def __init__(self, source):
        self.source = iter(source)
        self.lines = []

    def __iter__(self):
        return self

    def __next__(self):
        line = next(self.source)
        self.lines.append(line)
        return line

    def take(self):
        lines, self.lines = self.lines, []
        return lines


def quoted_fields(lines, delimiter):
    """Identify fields whose first character is a quote, including multiline CSV."""
    flags = []
    state = "start"
    was_quoted = False
    for char in itertools.chain.from_iterable(lines):
        if state == "quoted":
            if char == '"':
                state = "after_quote"
        elif state == "after_quote" and char == '"':
            state = "quoted"
        elif char == delimiter:
            flags.append(was_quoted)
            state, was_quoted = "start", False
        elif char in "\r\n":
            break
        elif state == "start" and char == '"':
            state, was_quoted = "quoted", True
        else:
            state = "unquoted"
    flags.append(was_quoted)
    return flags


def postgres_rows(reader, lines, delimiter, column_count):
    for row in reader:
        captured = lines.take()
        if not row:
            # PostgreSQL COPY emits an empty line for a single NULL column.
            yield [None] if column_count == 1 else row
            continue
        flags = quoted_fields(captured, delimiter)
        if len(flags) != len(row):
            raise InputError("Could not safely interpret PostgreSQL CSV fields; import was stopped.")
        yield [None if value == "" and not quoted else value for value, quoted in zip(row, flags)]


@contextmanager
def read_csv(path, delimiter=",", encoding="utf-8-sig", postgres_csv=False):
    previous_limit = csv.field_size_limit(CSV_FIELD_LIMIT)
    try:
        with path.open(encoding=encoding, newline="") as source:
            lines = RecordingLines(source) if postgres_csv else source
            reader = csv.reader(lines, delimiter=delimiter, strict=True)
            headers = next(reader, None)
            if postgres_csv:
                lines.take()
            if not headers or any(not name.strip() for name in headers):
                raise InputError("CSV headers must not be empty.")
            if len(set(headers)) != len(headers):
                raise InputError("The CSV contains duplicate columns.")
            yield headers, postgres_rows(reader, lines, delimiter, len(headers)) if postgres_csv else reader
    finally:
        csv.field_size_limit(previous_limit)


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


