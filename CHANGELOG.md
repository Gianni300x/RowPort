# Changelog

All notable changes to RowPort are documented in this file.

## Unreleased

### Added

- License RowPort under the GNU General Public License, version 3 or later.
- Validate CSV headers against a PostgreSQL table without importing rows.
- Show import progress every 10,000 rows, including pending and rejected counts.
- Run a 200,000-row demo with Docker Compose and save a rejection report.
- Configure the database connection through `DATABASE_URL` in a local `.env` file.
- Test unit behavior and PostgreSQL integration, including transaction rollback.
- Import CSV rows into an existing PostgreSQL table and report rejected rows.

### Changed

- Read CSV rows incrementally and stage rejections on disk to limit memory use.
