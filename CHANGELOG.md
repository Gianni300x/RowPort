# Changelog

All notable changes to RowPort are documented in this file.

## Unreleased

### Added

- Retry corrected rejection reports with `rowport retry`.

- Display an updating percentage progress bar for interactive imports.

- Add `--postgres-csv` to preserve NULL and empty text in PostgreSQL CSV exports.
- Preview parsed CSV rows without connecting to PostgreSQL.
- Group rejected rows by error category in the console summary.
- Reuse import, validation and preview options from a JSON configuration file.
- Test an import with `--dry-run`, rolling back the transaction after validation.
- Support selecting the input CSV encoding for validation and import.
- Add a README reference table for command options and defaults.
- Optionally insert empty CSV fields as SQL `NULL` with `--empty-as-null`.
- Support a custom single-character CSV delimiter for validation and import.
- Add a demonstration of the `--skip-duplicates` option to the README.
- Optionally skip target-table unique constraint violations and count them separately.
- Map CSV headers to different PostgreSQL column names with repeatable `--map` options.
- Validate CSV headers against a PostgreSQL table without importing rows.
- Show import progress every 10,000 rows, including pending and rejected counts.
- Run a 200,000-row demo with Docker Compose and save a rejection report.
- Configure the database connection through `DATABASE_URL` in a local `.env` file.
- Test unit behavior and PostgreSQL integration, including transaction rollback.
- Import CSV rows into an existing PostgreSQL table and report rejected rows.

### Changed

- Give actionable guidance for common database, disk, CSV and interruption errors.

- Rejection reports expose original CSV columns and diagnostic columns; this replaces the previous encoded-row format.

- Simplify the README's getting-started path and group CLI help by purpose.
- Explain common file, connection, CSV and column errors with actionable hints.
- License RowPort under the MIT License instead of GPL-3.0-or-later.
- Read CSV rows incrementally and stage rejections on disk to limit memory use.
- Split CSV parsing, configuration, database import and shared errors into separate modules.

### Fixed

- Read UTF-8 `.env` files with or without a BOM.
- Use UTF-8 for redirected Windows output unless explicitly configured otherwise.
- Document installation and PostgreSQL command-line setup for Windows.
- Protect the input and configuration from report paths pointing to the same file, including hard links.
- Check the report destination before importing and replace reports atomically after success.
- Exclude `GENERATED ALWAYS` identity columns from insertable-column validation.
- Support CSV fields up to 16,777,216 characters and report the limit accurately.
- Allow `preview` and `validate` options before the CSV path.
