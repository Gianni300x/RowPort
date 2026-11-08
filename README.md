# RowPort

RowPort is a Python CLI that imports CSV files into an existing PostgreSQL table. It saves valid rows and writes rejected rows with their reasons to a CSV report.

## Start here

Once RowPort is installed and connected to PostgreSQL, a basic import needs only
your CSV, an existing table, and a path for rejected rows:

```sh
rowport data.csv --table products --rejects rejects.csv
```

Valid rows are inserted into `products`; invalid rows and their reasons are
written to `rejects.csv`. RowPort prints progress and a final summary. By
default, the CSV uses UTF-8 and comma-separated column names that match the
table. [Set up your database and run an example](#usage), or try the
[Docker demo](#quick-demo-with-docker) first.

Need to change how a file is read, preview it, or test without committing rows?
See [more ways to use RowPort](#more-ways-to-use-rowport). You do not need to
configure those options for a basic import.

## Quick demo with Docker

![Accelerated recording of a RowPort import](docs/rowport-demo.gif)

The demo above shows real output from a Docker run, replayed at an accelerated pace.

Install and start Docker with the Compose plugin, then clone this repository and run:

```sh
git clone https://github.com/Gianni300x/RowPort.git
cd RowPort
docker compose up --build --exit-code-from demo --attach demo
```

Compose starts a temporary PostgreSQL database, creates the example table and
imports 200,000 rows. Four rows have invalid prices, so a successful run reports
199,996 saved and 4 rejected. The rejection report is available at
`reports/rejects.csv` on your computer. The command stops the containers when the
import finishes; the demo database is temporary and its contents are discarded.
Each run starts with an empty database and overwrites the report. To remove the
stopped demo containers and network, run `docker compose down`.

To use your own database or CSV, follow the manual installation below.

## Requirements and installation

Python 3.10 or later and an accessible PostgreSQL database are required.

On macOS or Linux:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install .
```

On Windows, using PowerShell:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install .
```

If PowerShell's execution policy prevents activation, you can install and run
without changing the policy by using `.\.venv\Scripts\python.exe -m pip install .`
and replacing `rowport` in the examples with `.\.venv\Scripts\rowport.exe`.

## Usage

Copy the configuration template to `.env` in the directory where you run RowPort,
then edit it with your PostgreSQL connection string:

```sh
cp .env.example .env
```

In Windows PowerShell, you can use `Copy-Item .env.example .env` instead.

The `.env` file is ignored by Git because it can contain credentials. Use a single
line `DATABASE_URL=...` (optionally surrounded by single or double quotes). A
UTF-8 BOM is accepted. If a
username or password contains special URL characters, percent-encode them.

Create the example table in your database using `examples/properties.sql`.
For example, with `psql`, replace the user and database names below with yours
(add `-p PORT` if PostgreSQL uses a non-default port):

```sh
psql -h localhost -U your_user -d your_database -f examples/properties.sql
```

`psql` is a PostgreSQL command-line tool and must be installed separately if it
is not available. On Windows, if it is not on `PATH`, call `psql.exe` from
your PostgreSQL installation's `bin` folder. For example, in PowerShell (adjust
the version and installation path):

```powershell
& "C:\Program Files\PostgreSQL\17\bin\psql.exe" -h localhost -U your_user -d your_database -f examples/properties.sql
```

Then import the example CSV:

```sh
rowport examples/properties.csv --table properties --rejects rejects.csv
```

Open `rejects.csv` to see the rejected row and its reason. For your own CSV,
replace the file and table names; the destination table must already exist.

The connection can also be supplied as an environment variable or with `--dsn`.
The order of precedence is `--dsn`, the `DATABASE_URL` environment variable,
then `DATABASE_URL` in `.env`. The table must already exist. The default schema is `public`; use `--schema` to select another. See `rowport --help` for all options.

## More ways to use RowPort

The basic import above works with the defaults. Use the options below only when your file or workflow needs them. For a compact reference, see [all command options](#command-options-at-a-glance).

### Preview rows

```sh
rowport preview properties.csv --delimiter ';' --limit 5
```

Preview displays the original headers and up to five parsed rows as JSON arrays;
it can also show mapped column names. It does not connect to PostgreSQL, insert
data, or validate row types and constraints. It displays data in the terminal.
For example, `rowport preview examples/properties.csv --limit 1` shows:

```text
CSV columns: address, price, rooms
Preview (JSON arrays, original values):
2: ["123 Oak Avenue", "85000", "3"]
Preview only: no database connection or inserts.
```

The number before each row is its CSV record number, with the header as record
1. `--limit` must be a positive integer. Preview reads only the displayed data
records, so it cannot guarantee that the rest of the file is well formed.

### Reuse an import configuration

Copy [examples/import-config.json](examples/import-config.json) and edit it for
your table. Then use `rowport data.csv --config import-config.json`, or add
`--config import-config.json` to `validate` or `preview` after the CSV path.
The JSON keys are `table`, `schema`, `delimiter`, `encoding`, `map` (an array
of `CSV_COLUMN=TABLE_COLUMN` strings), `rejects`, `skip_duplicates`,
`empty_as_null`, and `postgres_csv`. Each command reads the options relevant to it; unknown keys
and invalid value types are errors. Report paths are relative to the current
working directory. Do not put credentials in the config: use `.env`, an
environment variable, or `--dsn` for the database connection.

For example, a configuration for a semicolon-separated file with renamed
columns could look like this:

```json
{
  "table": "properties",
  "schema": "public",
  "delimiter": ";",
  "encoding": "utf-8-sig",
  "map": ["street=address", "bedrooms=rooms"],
  "rejects": "rejects.csv",
  "skip_duplicates": false,
  "empty_as_null": false,
  "postgres_csv": false
}
```

Only include the keys you need. For example, after saving this file as
`import-config.json` in the current directory:

```sh
rowport preview data.csv --config import-config.json
rowport validate data.csv --config import-config.json
rowport data.csv --config import-config.json --dry-run
rowport data.csv --config import-config.json
```

The final command performs the actual import. The two preceding database
commands still require the connection in `.env` or `DATABASE_URL`, or an
explicit `--dsn`. Keep credentials out of files shared with others.

Explicit command options override configuration values. When `--map` is given,
its mappings replace the config's entire `map` list. Use `--no-skip-duplicates`
or `--no-empty-as-null` to override a `true` value in the configuration. Use
`--no-postgres-csv` to override an enabled `postgres_csv` setting.

### Test an import

```sh
rowport data.csv --table properties --rejects rejects.csv --dry-run
```

This executes inserts in a transaction and rolls it back, then writes the
rejection report and shows how many rows would have been saved. It forces
deferred constraints to be checked before rollback; a deferred constraint
failure aborts the run rather than identifying an individual rejected row.
**This is not a side-effect-free simulation:** PostgreSQL sequences can advance
and triggers or functions can perform external effects that a rollback does
not undo. Use a disposable database when those effects matter. Existing report
files are overwritten, just as with a normal import.

A successful dry run reports the rows that would have been inserted, rejected
rows, skipped duplicates if enabled, and the report path. The result reflects
the database state during that run; a later import can differ if data or
constraints change. Without `--dry-run`, valid rows are committed as usual.

### Validate table columns

To check headers before importing, use:

```sh
rowport validate examples/properties.csv --table properties
```

This displays the CSV headers and the table's insertable and required columns.
It reports missing required or unknown columns and exits with a nonzero status
on errors. It does not examine row data or check types and constraints, and it
does not insert anything. Use `--schema` or `--dsn` when needed; the connection
configuration follows the same precedence as the import command.

### Match CSV columns to table columns

When CSV headers differ from the table's column names, use `--map` for each
equivalence. Names are case-sensitive and must match exactly; quote arguments
containing spaces for your shell. Unmapped columns keep their original names.
The same mappings can be used for validation and import:

```sh
rowport validate properties.csv --table properties --map street=address --map bedrooms=rooms
rowport properties.csv --table properties --map street=address --map bedrooms=rooms --rejects rejects.csv
```

Mappings must reference existing CSV columns and distinct insertable table
columns. Rejected rows retain their original CSV values and column order.

### Choose the CSV format

For CSV files using a different delimiter, pass a single character with
`--delimiter` to either validation or import. The default is a comma:

```sh
rowport validate properties.csv --table properties --delimiter ';'
rowport properties.csv --table properties --delimiter ';' --rejects rejects.csv
```

The rejection report itself uses commas, including the encoded `csv_row` field,
regardless of the input delimiter.

Input CSV files use UTF-8 by default (a UTF-8 BOM is accepted). For files in
another encoding, set `--encoding` on import or validation:

```sh
rowport validate datos.csv --table properties --encoding latin-1
rowport datos.csv --table properties --encoding latin-1 --rejects rejects.csv
```

The rejection report is always written in UTF-8. CSV fields can contain up to
16,777,216 characters each. CSV headers must be unique and match insertable
table columns; generated columns and `GENERATED ALWAYS` identity columns cannot
be inserted and should be omitted. `GENERATED BY DEFAULT` identity columns can
be supplied. Columns marked `NOT NULL` without a default
or identity are required; unknown columns prevent the import. Empty values are
sent as empty strings, not `NULL`, unless `--empty-as-null` is used.

### Insert empty fields as NULL

To insert empty fields as SQL `NULL` instead, use:

```sh
rowport data.csv --table properties --rejects rejects.csv --empty-as-null
```

Both quoted and unquoted empty fields become `NULL`; whitespace-only values are
unchanged. PostgreSQL still rejects `NULL` in a `NOT NULL` column. An explicitly
supplied `NULL` does not cause a column default to be applied. Rejected rows
retain their original CSV values in the report.

### Import a PostgreSQL CSV export

PostgreSQL's default `COPY ... FORMAT CSV` distinguishes an unquoted empty
field (`NULL`) from a quoted empty field (`""`, an empty string). To preserve
that distinction, opt into PostgreSQL CSV interpretation:

```sh
rowport preview exported.csv --postgres-csv
rowport validate exported.csv --table properties --postgres-csv
rowport exported.csv --table properties --postgres-csv --rejects rejects.csv
```

An unquoted empty field becomes SQL `NULL`, `""` remains empty text, and
whitespace remains unchanged. Preview displays SQL `NULL` as JSON `null`.
This mode follows the default PostgreSQL CSV null and quoting conventions;
exports using a custom `NULL` marker or custom quote/escape characters are not
supported. Set `--delimiter` or `--encoding` if your export uses different
supported values. `--postgres-csv` cannot be combined with `--empty-as-null`.

In this mode, rejected rows retain the distinction in the report's `csv_row`
field: unquoted empty fields represent `NULL`, and quoted empty fields represent
empty text. The report and its encoded `csv_row` still use comma delimiters.
Without `--postgres-csv`, the existing empty-field behavior is unchanged.

### Understand the import results

PostgreSQL validates types and constraints. Valid rows are inserted in one transaction; invalid rows are captured in the report. A database failure interrupts and rolls back the import. The report contains the CSV record number (header = 1), the original row encoded as CSV, and the rejection reason. RowPort checks the report destination before inserting and stages the report in the destination directory. It replaces the selected output after a successful import, even when there are no rejected rows. If publishing the report fails after commit, the CLI reports that rows were already saved and leaves any existing report in place. The report must be a different file from the input and configuration, including hard links.

When rows are rejected, the console also groups them into broad categories such
as invalid data, unique constraints, missing required values, and incorrect
field counts. Skipped duplicates are counted separately, not as rejections.
The report retains the detailed PostgreSQL reason for each rejected row.
For example, an import with four invalid numeric values can end with:

```text
Rows saved: 199996. Rows rejected: 4. Report: rejects.csv
Rejection summary:
  Invalid data: 4
```

### Skip duplicate keys

By default, unique constraint violations are rejected like other invalid rows.
To skip violations reported on the target table instead, use:

```sh
rowport data.csv --table properties --rejects rejects.csv --skip-duplicates
```

![Demonstration of importing with duplicate rows skipped](docs/skip-duplicates.gif)

This replay shows actual output from a test using a temporary PostgreSQL schema:
one new row is saved, two duplicate rows are skipped, and an invalid row is
written to the rejection report.

Skipped rows are counted separately and do not appear in the rejection report.
This relies on unique constraints or unique indexes in PostgreSQL: without one,
RowPort cannot identify duplicates. A conflict means a unique key already
exists; it does not guarantee that the rest of the row is identical. Violations
reported for another table, including a partition or a table affected by a
trigger, remain rejections. Deferred constraints may fail at commit and roll
back the import instead of being skipped.

Rows are read one at a time, and rejected rows are staged in a temporary file on
disk until the database transaction succeeds. Memory use therefore does not grow
with the number of rows, although disk space is needed for the rejection report.

During import, RowPort prints a progress line every 10,000 processed rows, with
the counts processed, saved pending commit, and rejected. These counts are
provisional: an error can roll back all pending inserts. The final summary is
printed after the transaction commits and the report is written. Progress uses
separate, immediately flushed stdout lines, so it remains readable in Docker
logs and when redirected to a file. Errors go to stderr; use `> import.log 2>&1`
to capture both streams.

On Windows, redirected stdout and stderr use UTF-8 by default so previews and
errors can include Unicode characters. An explicitly configured
`PYTHONIOENCODING` takes precedence.

### Command options at a glance

Both import and `validate` require a CSV file path as a positional argument.
The table below summarizes the options for each command:

| Option | Available for | Default or requirement | What it does |
| --- | --- | --- | --- |
| `--table TABLE` | Import, validate | Required, unless supplied by `--config` | Selects the existing PostgreSQL table. |
| `--rejects FILE` | Import | Required, unless supplied by `--config` | Writes rejected rows and their reasons to a CSV report. |
| `--dsn CONNECTION` | Import, validate | `DATABASE_URL` environment variable, then `.env` | Sets the PostgreSQL connection string and overrides `DATABASE_URL`. |
| `--schema SCHEMA` | Import, validate | `public` | Selects the schema containing the target table. |
| `--config FILE` | Import, validate, preview | None | Loads reusable options from a JSON file. |
| `--delimiter CHAR` | Import, validate, preview | Comma (`,`) | Sets the input CSV delimiter, for example `--delimiter ';'`. |
| `--encoding NAME` | Import, validate, preview | UTF-8 (optional BOM) | Sets the input CSV encoding, for example `--encoding latin-1`. |
| `--map CSV_COLUMN=TABLE_COLUMN` | Import, validate, preview | CSV and table column names must match | Maps a CSV column to a table column; repeat the option for multiple columns. |
| `--postgres-csv` | Import, validate, preview | Off | Uses PostgreSQL's default CSV convention to distinguish `NULL` from empty text. |
| `--skip-duplicates` | Import | Off; unique violations are rejected | Skips eligible unique-constraint conflicts and counts them separately. |
| `--empty-as-null` | Import | Off; empty fields are sent as empty strings | Sends empty fields as SQL `NULL`. |
| `--no-skip-duplicates`, `--no-empty-as-null` | Import | — | Override the corresponding enabled JSON configuration option. |
| `--no-postgres-csv` | Import, validate, preview | — | Overrides `postgres_csv: true` in the configuration file. |
| `--dry-run` | Import | Off | Tests insertion and rolls back the transaction. |
| `--limit N` | Preview | 5 rows | Limits the number of rows displayed. |
| `-h`, `--help` | Import, validate, preview | — | Displays help for the selected command. |

Run `rowport --help`, `rowport validate --help`, or `rowport preview --help` for command help.
Options for `validate` and `preview` can appear before or after the CSV path.
To import a file literally named `validate` or `preview`, use `./validate` or
`./preview` so it is not interpreted as a subcommand.

### Common problems

- **File not found:** check the file path and the directory where you ran the command.
- **Connection failed:** check that PostgreSQL is running and verify the host, port, database and credentials in your connection settings. Connection errors do not print the underlying error, which can contain credentials.
- **Table or columns not found:** check `--table` and `--schema`, then compare the CSV headers with the table or use `--map`.
- **Cannot decode the file:** verify its encoding and pass `--encoding`, for example `--encoding latin-1`.
- **Malformed CSV:** check the input delimiter and quoting; use `--delimiter` if the file is not comma-separated.

For database errors other than connection failures, RowPort reports the database
error message. Per-row errors are detailed in the rejection report.

## Tests

```sh
python -m unittest discover -s tests
```

The unit tests use simulated database responses. Integration tests can be run against a **disposable** PostgreSQL database by setting `ROWPORT_TEST_DSN`; they create and drop a dedicated schema in that database:

```sh
ROWPORT_TEST_DSN="$DATABASE_URL" python -m unittest discover -s tests -v
```

## Code structure

- `scripts/cli.py` handles commands, arguments and console output.
- `scripts/config.py` reads connection settings and reusable configurations.
- `scripts/csv_io.py` reads and interprets CSV input and column mappings.
- `scripts/importer.py` validates the table, imports rows and manages reports.
- `scripts/errors.py` defines errors shared by those modules.

## License

Copyright (c) 2026 Gianni Luigi Messina. RowPort is licensed under the
[MIT License](LICENSE).
