# RowPort

RowPort is a Python CLI that imports CSV files into an existing PostgreSQL table. It saves valid rows and writes rejected rows with their reasons to a CSV report.

## Requirements and installation

Python 3.10 or later and an accessible PostgreSQL database are required.

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install .
```

## Usage

Create the example table in your database using `examples/properties.sql`, then run:

```sh
rowport examples/properties.csv --dsn "$DATABASE_URL" --table properties --rejects rejects.csv
```

`DATABASE_URL` is the PostgreSQL connection string. The table must already exist. The default schema is `public`; use `--schema` to select another. See `rowport --help` for all options.

The input must be UTF-8 CSV with unique headers matching insertable table columns. Columns marked `NOT NULL` without a default or identity are required; unknown columns prevent the import. Empty values are sent as empty strings, not `NULL`.

PostgreSQL validates types and constraints. Valid rows are inserted in one transaction; invalid rows are captured in the report. A database failure interrupts and rolls back the import. The report contains the CSV record number (header = 1), the original row encoded as CSV, and the rejection reason. It is written even when there are no rejected rows and overwrites the selected output file. If writing the report fails after commit, the CLI reports that rows were already saved.

## Tests

```sh
python -m unittest discover -s tests
```

The unit tests use simulated database responses. Integration tests can be run against a **disposable** PostgreSQL database by setting `ROWPORT_TEST_DSN`; they create and drop a dedicated schema in that database:

```sh
ROWPORT_TEST_DSN="$DATABASE_URL" python -m unittest discover -s tests -v
```
