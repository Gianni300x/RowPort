# RowPort

RowPort is a command-line tool for importing CSV files into PostgreSQL while
identifying the rows that could not be inserted. It saves valid rows, writes
rejected rows and their reasons to a CSV report, and lets you retry those rows
after correcting them.

**CSV file → PostgreSQL import → rejection report → correct and retry**

RowPort works with an existing PostgreSQL table. To check an import without
saving rows, use `--dry-run`.

## When to use RowPort

- **Migrate records:** import a CSV exported from another system and review
  rows that do not match the destination table's rules.
- **Load customer or product data:** save valid records while keeping a report
  of invalid values to correct and retry.
- **Check an import first:** run a dry run to find rejected rows before
  committing the import. See [dry-run limitations](#test-an-import) for
  database side effects that a rollback may not undo.

## Start here

Once RowPort is installed and connected to PostgreSQL, a basic import needs only
your CSV, an existing table, and a path for rejected rows. Create a `reports`
directory in the project first (`mkdir -p reports` on macOS/Linux, or
`New-Item -ItemType Directory -Force reports` in PowerShell), then run:

```sh
rowport data.csv --table products --rejects reports/rejects.csv
```

Valid rows are inserted into `products`; invalid rows and their reasons are
written to `reports/rejects.csv`. RowPort prints progress and a final summary. By
default, the CSV uses UTF-8 and comma-separated column names that match the
table. [Set up your database and run an example](#usage), or try the
[Docker demo](#quick-demo-with-docker) first.

Need to change how a file is read, preview it, or test without committing rows?
See [more ways to use RowPort](#more-ways-to-use-rowport). You do not need to
configure those options for a basic import.

## Interactive demo

![Interactive import with progress, rejected rows and retry](docs/rowport-demo.gif)

This accelerated replay shows real output from imports into a temporary
PostgreSQL schema: 1,998 rows are saved, two prices are corrected in a CSV
editor, and the two rejected rows are imported with `rowport retry`. The
correction step is summarized on screen; the editor itself is not recorded.

## Quick demo with Docker

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

Open `.env` in a text editor and replace the placeholders. For example:

```dotenv
DATABASE_URL=postgresql://your_user:your_password@localhost:5432/your_database
```

- `your_user` and `your_password` are the PostgreSQL credentials for the database
  you want to use. Ask the database administrator if you do not know them.
- `localhost` means PostgreSQL runs on this computer; otherwise use the host
  supplied by your administrator or provider. `5432` is the default port; use
  your database's port if it is different.
- `your_database` is the name of an existing database. RowPort imports into an
  existing table within it; it does not create the database or table for you.

Use the same host, port, user and database when running `psql` below. If the
username or password contains URL-special characters, percent-encode them (for
example, `@` becomes `%40`). The `.env` file is ignored by Git because it can
contain credentials; do not share it. The URL can optionally be surrounded by
single or double quotes, and a UTF-8 BOM is accepted.

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
rowport examples/properties.csv --table properties --rejects reports/rejects.csv
```

Open `reports/rejects.csv` to see the rejected row and its reason. For your own CSV,
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
  "rejects": "reports/rejects.csv",
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
rowport data.csv --table properties --rejects reports/rejects.csv --dry-run
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
rowport properties.csv --table properties --map street=address --map bedrooms=rooms --rejects reports/rejects.csv
```

Mappings must reference existing CSV columns and distinct insertable table
columns. Rejected rows retain their original CSV values and column order.

### Choose the CSV format

For CSV files using a different delimiter, pass a single character with
`--delimiter` to either validation or import. The default is a comma:

```sh
rowport validate properties.csv --table properties --delimiter ';'
rowport properties.csv --table properties --delimiter ';' --rejects reports/rejects.csv
```

The rejection report uses commas and UTF-8 regardless of the input format.

Input CSV files use UTF-8 by default (a UTF-8 BOM is accepted). For files in
another encoding, set `--encoding` on import or validation:

```sh
rowport validate datos.csv --table properties --encoding latin-1
rowport datos.csv --table properties --encoding latin-1 --rejects reports/rejects.csv
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
rowport data.csv --table properties --rejects reports/rejects.csv --empty-as-null
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
rowport exported.csv --table properties --postgres-csv --rejects reports/rejects.csv
```

An unquoted empty field becomes SQL `NULL`, `""` remains empty text, and
whitespace remains unchanged. Preview displays SQL `NULL` as JSON `null`.
This mode follows the default PostgreSQL CSV null and quoting conventions;
exports using a custom `NULL` marker or custom quote/escape characters are not
supported. Set `--delimiter` or `--encoding` if your export uses different
supported values. `--postgres-csv` cannot be combined with `--empty-as-null`.

In this mode, the report preserves the distinction: unquoted empty fields
represent `NULL`, while quoted empty fields represent empty text. Use
`--postgres-csv` again when retrying it. Spreadsheet programs may rewrite CSV
quoting and lose this distinction; use a text editor if it matters.

### Understand the import results

PostgreSQL validates types and constraints. Valid rows are inserted in one transaction; invalid rows are captured in the report. A database failure interrupts and rolls back the import. The report contains the original CSV columns followed by `rowport_record` (header = 1), `rowport_error`, `rowport_extra`, and `rowport_encoding`. If a source column already uses a diagnostic name, underscores are appended to the diagnostic name to avoid a collision. Rows with missing fields are padded with empty values; extra fields are retained as a JSON array in `rowport_extra`. This replaces the previous `row_number,csv_row,reason` report format. RowPort checks the report destination before inserting and stages the report in the destination directory. It replaces the selected output after a successful import, even when there are no rejected rows. If publishing the report fails after commit, the CLI reports that rows were already saved and leaves any existing report in place. The report must be a different file from the input and configuration, including hard links.

Rejection reports contain the original data, which may be sensitive. The
examples use the Git-ignored `reports/` directory. Keep source CSV files and
reports outside a repository or make sure their paths are ignored before using
`git add`; custom paths are not automatically protected.

To reduce spreadsheet formula injection, new reports prefix potentially risky
text and headers with an apostrophe and mark the encoding in `rowport_encoding`.
`rowport retry` reverses that escaping; older reports with three diagnostic
columns remain supported. Spreadsheet programs vary and may remove or rewrite
prefixes when saving: for untrusted CSVs, prefer a plain text or CSV editor
that does not evaluate formulas, and preserve the encoding column and prefixes.
Do not assume that an edited or re-exported file remains safe to open in a
spreadsheet. When editing encoded values, preserve the leading apostrophe; a
literal value that starts with an apostrophe must use two. SQL NULL and empty
text remain distinct with `--postgres-csv`.

### Correct and retry rejected rows

Open the report in a CSV editor and correct the original columns. Keep the four
diagnostic columns in place: `rowport retry` checks the encoding and extra
fields but does not insert these diagnostic columns.
For a row with too many fields, inspect `rowport_extra`, move any needed values
to the original columns, and clear that cell. Fill in any missing fields. Save
as UTF-8 CSV, preserving all columns and their order, then use a **different**
path for the new rejection report:

```sh
rowport retry reports/rejects.csv --table products --rejects reports/retry-rejects.csv
```

For the `examples/properties.csv` import above, `reports/rejects.csv` contains a row
like this (the exact PostgreSQL detail can differ):

| address | price | rooms | rowport_record | rowport_error | rowport_extra | rowport_encoding |
| --- | --- | --- | --- | --- | --- | --- |
| 456 Maple Street | not a price | 2 | 3 | A value does not match the expected data type. PostgreSQL: invalid input syntax for type numeric: "not a price" | *(empty)* | rowport-spreadsheet-v1 |

In your CSV editor, change `price` from `not a price` to a valid number such as
`95000`. Leave the other cells and the four diagnostic columns as they are,
then save the file. From the same directory, run:

```sh
rowport retry reports/rejects.csv --table properties --rejects reports/retry-rejects.csv
```

A successful retry reports `Rows saved: 1. Rows rejected: 0.` The original
`reports/rejects.csv` remains available; `reports/retry-rejects.csv` contains only rows that
were still rejected, if any. If you save the corrected file under a different
name, replace `reports/rejects.csv` in the command with that name.

Use the same `--schema`, column mappings and import behavior as the original
import, if applicable. Reports use comma delimiters and UTF-8 even if the
original file did not; `retry` reads that format regardless of `--delimiter`
and `--encoding`. For PostgreSQL exports, keep `--postgres-csv` and preserve CSV
quoting when editing. Previously saved rows are not in the report, so a retry
only processes the rejected rows. Rows still invalid appear in the new report.
Older reports with three diagnostic columns remain supported; the original
`row_number,csv_row,reason` format cannot be retried with this command.

When rows are rejected, the console also groups them into broad categories such
as invalid data, unique constraints, missing required values, and incorrect
field counts. Skipped duplicates are counted separately, not as rejections.
For common database errors, `rowport_error` starts with a plain-language
explanation followed by PostgreSQL's original message.
For example, an import with four invalid numeric values can end with:

```text
Rows saved: 199996. Rows rejected: 4. Report: reports/rejects.csv
Rejection summary:
  Invalid data: 4
```

### Skip duplicate keys

By default, unique constraint violations are rejected like other invalid rows.
To skip violations reported on the target table instead, use:

```sh
rowport data.csv --table properties --rejects reports/rejects.csv --skip-duplicates
```

![Demonstration of importing with duplicate rows skipped](docs/skip-duplicates.gif)

This accelerated terminal replay shows actual output from a test using a
temporary PostgreSQL schema: one new row is saved, two duplicate rows are
skipped, and an invalid row is written to the rejection report.

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
RowPort uses a savepoint for each row to isolate rejected values while keeping
the import in one transaction. Network latency, database constraints, triggers,
indexes and the number of rejected rows can affect import speed.

In an interactive terminal, RowPort displays a progress bar and percentage on a
single updating line. It first reads the CSV once to count records, then reads it
again for the import; this keeps memory usage bounded but adds a preliminary
read for large files. The percentage counts processed rows, including rejected
and skipped rows. Even at 100%, saved rows remain pending until the database
transaction commits. The final summary appears after the report is written.

When stdout is redirected, including typical Docker logs, or the input is not a
regular file, RowPort instead prints a separate progress line every 10,000 rows.
These lines are flushed immediately and include pending and rejected counts.
Errors go to stderr; use `> import.log 2>&1` to capture both streams.

On Windows, redirected stdout and stderr use UTF-8 by default so previews and
errors can include Unicode characters. An explicitly configured
`PYTHONIOENCODING` takes precedence.

### Use RowPort in scripts

For import and retry, add `--summary-json FILE` to save a machine-readable
summary and `--fail-on-rejects` to return exit code **3** when any rows are
rejected:

```sh
rowport data.csv --table products --rejects reports/rejects.csv --summary-json reports/summary.json --fail-on-rejects
```

`--fail-on-rejects` changes the exit status only: valid rows are still committed.
Use `--dry-run` to roll back instead. Skipped duplicates do not trigger code 3.
Without this option, completed imports still return 0 even with rejected rows.
Both options are command-line only; they are not JSON configuration keys.

| Exit code | Meaning |
| --- | --- |
| `0` | Completed; may include rejections unless `--fail-on-rejects` is enabled. |
| `1` | Input, database, or output error. Check the error message; a failure after commit does not undo saved rows. |
| `2` | Invalid command arguments or configuration. |
| `3` | Completed with rejected rows and `--fail-on-rejects` enabled. |
| `130` | Interrupted. Check the database and reports before retrying. |

For example, a summary for two input rows, one saved and one rejected, is:

```json
{
  "format_version": 1,
  "status": "completed_with_rejections",
  "command": "import",
  "schema": "public",
  "table": "products",
  "dry_run": false,
  "rows_processed": 2,
  "rows_saved": 1,
  "rows_would_save": null,
  "rows_rejected": 1,
  "duplicates_skipped": 0,
  "rejection_categories": {"Invalid data": 1},
  "duration_seconds": 0.125,
  "exit_code": 3
}
```

`status` is `completed` when there are no rejections; `command` is `retry` for
a retry. In a dry run, `rows_saved` is 0 and `rows_would_save` contains the
number of rows that passed insertion. `rows_processed` includes saved (or
would-save), rejected and skipped rows. Duration measures elapsed time from
input/output checks through import and rejection-report publication, including
the preliminary counting pass in interactive terminals; JSON publication is
excluded. Categories contain counts, without original PostgreSQL error messages.
The summary excludes credentials, connection details, file paths and row values;
schema and table names are included.

The destination directory must exist. The summary must be separate from the
input, configuration and rejection report, including hard links. Its destination
is checked before connecting to PostgreSQL. After completion, RowPort atomically
replaces the JSON file; an earlier file is preserved if the run fails. **Always
check the process exit status:** an existing summary may belong to a previous
run. No failure summary is generated. Rejection-report and JSON publication are
separate operations after the database transaction: if JSON publication fails,
RowPort returns 1 and reports that valid rows were already saved (or the dry run
rolled back) and the rejection report was written.

### Command options at a glance

Import, `retry`, and `validate` require a CSV file path as a positional argument.
`retry` accepts import options but always reads its report as comma-separated UTF-8.
The table below summarizes the options for each command:

| Option | Available for | Default or requirement | What it does |
| --- | --- | --- | --- |
| `--table TABLE` | Import, retry, validate | Required, unless supplied by `--config` | Selects the existing PostgreSQL table. |
| `--rejects FILE` | Import, retry | Required, unless supplied by `--config` | Writes rejected rows and their reasons to a CSV report. |
| `--dsn CONNECTION` | Import, retry, validate | `DATABASE_URL` environment variable, then `.env` | Sets the PostgreSQL connection string and overrides `DATABASE_URL`. |
| `--schema SCHEMA` | Import, retry, validate | `public` | Selects the schema containing the target table. |
| `--config FILE` | Import, retry, validate, preview | None | Loads reusable options from a JSON file. |
| `--delimiter CHAR` | Import, validate, preview | Comma (`,`) | Sets the input CSV delimiter, for example `--delimiter ';'`. |
| `--encoding NAME` | Import, validate, preview | UTF-8 (optional BOM) | Sets the input CSV encoding, for example `--encoding latin-1`. |
| `--map CSV_COLUMN=TABLE_COLUMN` | Import, retry, validate, preview | CSV and table column names must match | Maps a CSV column to a table column; repeat the option for multiple columns. |
| `--postgres-csv` | Import, retry, validate, preview | Off | Uses PostgreSQL's default CSV convention to distinguish `NULL` from empty text. |
| `--skip-duplicates` | Import, retry | Off; unique violations are rejected | Skips eligible unique-constraint conflicts and counts them separately. |
| `--empty-as-null` | Import, retry | Off; empty fields are sent as empty strings | Sends empty fields as SQL `NULL`. |
| `--no-skip-duplicates`, `--no-empty-as-null` | Import, retry | — | Override the corresponding enabled JSON configuration option. |
| `--no-postgres-csv` | Import, retry, validate, preview | — | Overrides `postgres_csv: true` in the configuration file. |
| `--dry-run` | Import, retry | Off | Tests insertion and rolls back the transaction. |
| `--summary-json FILE` | Import, retry | None | Atomically writes a JSON completion summary. |
| `--fail-on-rejects` | Import, retry | Off | Returns exit code 3 on completed runs with rejections; valid rows still commit unless using dry-run. |
| `--limit N` | Preview | 5 rows | Limits the number of rows displayed. |
| `-h`, `--help` | Import, retry, validate, preview | — | Displays help for the selected command. |

Run `rowport --help`, `rowport retry --help`, `rowport validate --help`, or `rowport preview --help` for command help.
Options for `validate` and `preview` can appear before or after the CSV path.
To import a file literally named `retry`, `validate` or `preview`, use `./retry`, `./validate` or
`./preview` so it is not interpreted as a subcommand.

### Common problems

- **File not found:** check the file path and the directory where you ran the command.
- **Connection failed:** check that PostgreSQL is running and verify the host, port, database and credentials in your connection settings. Connection errors do not print the underlying error, which can contain credentials.
- **Access denied:** verify that the database user has the required permissions on the schema and table.
- **Import canceled or interrupted:** check the database and report before retrying; an interruption can occur after the database commits.
- **Disk full:** free space on the report destination and retry. If the CLI says rows were saved but the report could not be written, check the database before retrying.
- **Table or columns not found:** check `--table` and `--schema`, then compare the CSV headers with the table or use `--map`.
- **Cannot decode the file:** verify its encoding and pass `--encoding`, for example `--encoding latin-1`.
- **Malformed CSV:** check the input delimiter and quoting; use `--delimiter` if the file is not comma-separated.

For other database errors, RowPort may display the database error message;
some common errors use a concise explanation instead. Per-row errors are
detailed in the rejection report.

## Tests

```sh
python -m unittest discover -s tests
```

The unit tests use simulated database responses. Integration tests can be run against a **disposable** PostgreSQL database by setting `ROWPORT_TEST_DSN`; they create and drop a dedicated schema in that database:

```sh
ROWPORT_TEST_DSN="$DATABASE_URL" python -m unittest discover -s tests -v
```

GitHub Actions runs the full suite with a disposable PostgreSQL service on Linux
and the CLI and progress unit tests on Windows, using Python 3.10 and 3.14.
The workflow runs for pushes, pull requests, and manual dispatches. Windows
integration with PostgreSQL is not part of this workflow.

## Performance benchmark

With Docker running, use this separate benchmark project:

```sh
docker compose -f compose.benchmark.yaml up --build --exit-code-from benchmark --attach benchmark
```

It starts a temporary PostgreSQL database with no published ports, generates
synthetic products and runs six sequential imports: **100,000 and 1,000,000
rows**, each with **0%, 1% and 10%** invalid numeric prices. The CSV also includes
Unicode, commas, quotes and multiline text. Each import starts with an empty
table with a primary key, required columns and check constraints. The table
schema is removed afterward; the temporary database is discarded when its
container stops. This project does not use the demo database or your `.env`.

The terminal shows elapsed seconds, processed rows per second and peak resident
memory (RSS). Results are saved to **`reports/benchmark.json` on your computer**
(`/reports/benchmark.json` inside the container). A run is marked verified only
when the exit code, CLI summary, PostgreSQL row count and rejection report agree
with the generated data. Every rejection is checked for the expected record ID
and a nonempty error reason. Results include environment versions and source
hashes for later comparisons. A failure returns a nonzero code and preserves
an existing result file; check the process exit status before using that file.

To select smaller datasets or repeat each case three times:

```sh
docker compose -f compose.benchmark.yaml run --build --rm benchmark --rows 100000 --reject-percent 0 1 10 --repeat 3 --output /reports/benchmark.json
docker compose -f compose.benchmark.yaml down
```

Run `down` after either Docker command to remove the stopped containers and
network. The `run` variant leaves the database running until `down`.

For an existing **disposable** PostgreSQL database on macOS or Linux, activate
the installed Python environment, create the output directory, and run:

```sh
mkdir -p reports
ROWPORT_BENCHMARK_DSN="postgresql://your_user:your_password@localhost:5432/disposable_database" python -m scripts.benchmark
```

The benchmark deliberately requires `ROWPORT_BENCHMARK_DSN` and does not fall
back to `DATABASE_URL` or `.env`. It creates a uniquely named `rowport_bench_…`
schema and removes it on completion or handled failure. Force-killing the
harness or losing the database connection can leave that schema behind. It
never truncates tables outside its own schema. Use Docker on Windows for RSS
measurement. `--timeout` sets the per-import timeout in seconds (default 1800).
Requested rejection counts are rounded down to whole rows.

**Measurement limits:** elapsed time covers CLI execution, including connection,
commit and report publication; it excludes worker startup, data generation and
verification. Peak RSS includes the Python runtime in a fresh worker for each
import, excluding PostgreSQL and the benchmark harness. Output is noninteractive,
so the terminal's preliminary counting pass is not measured. Runs do not reset
OS or database caches. The default is one measurement per case; use `--repeat`
and compare equivalent environments before drawing performance conclusions.
Docker results depend on its CPU, RAM and temporary-database limits; allow disk
and memory for the database, CSV and rejection report. These are observations
for one workload, not a throughput guarantee or a measurement of remote-network
latency, duplicates, foreign keys, triggers or additional indexes.

## Code structure

- `scripts/cli.py` handles commands, arguments and console output.
- `scripts/config.py` reads connection settings and reusable configurations.
- `scripts/csv_io.py` reads and interprets CSV input and column mappings.
- `scripts/importer.py` validates the table, imports rows and manages reports.
- `scripts/progress.py` renders the interactive progress bar.
- `scripts/errors.py` defines errors shared by those modules.
- `scripts/benchmark.py` generates synthetic workloads and verifies performance measurements.

## License

Copyright (c) 2026 Gianni Luigi Messina. RowPort is licensed under the
[MIT License](LICENSE).
