# Processing flow

## Modules

| Module | Responsibility |
| --- | --- |
| `models.py` | Validation rule types and configuration checks |
| `processor.py` | CSV cleanup, field validation, duplicate detection, and reports |
| `store.py` | SQLite job records, queue transitions, and activity logs |
| `worker.py` | Background queue processing |
| `app.py` | Uploads, job status, retries, and downloads |
| `static/index.html` | Dashboard layout and API requests |

The dashboard uses `fetch` to submit uploads and polls the API every 2.5 seconds. HTML, CSS, and JavaScript are kept in one file, with no separate frontend build step.

## Upload and processing

An upload is saved under a generated job ID. The filename is retained as display metadata rather than used as a storage path. The API records the rules and queues the job in SQLite.

The worker claims a queued job inside a transaction. `BEGIN IMMEDIATE` protects the selection and status update from competing claims. Each database operation uses a separate connection rather than sharing a connection across threads.

The processor reads records sequentially. Whitespace trimming and configured lowercase conversions happen before field validation. Invalid records go to the rejection report, with individual field failures recorded in the errors report.

Valid records are checked for duplicates using the selected columns. The first valid occurrence of a key is retained. Invalid records never reserve a duplicate key. Keys are compared as strings after cleanup: `"01"` and `"1"` remain different even if both pass integer validation.

Results are written to a staging directory and moved into the results directory after a successful pass. The API only serves downloads for completed jobs.

## Job recovery

A running job is requeued when the server starts. Its source CSV and rules remain on disk, allowing the whole file to be processed again. Processing restarts from the beginning rather than resuming at a saved record number.

Failed jobs support manual retries up to three total attempts. Deterministic input errors, such as missing columns, require a corrected upload. Startup recovery assumes that only one server instance uses the data directory.

## Sample behavior

The included sample contains 12 fictional records. The default rules produce 5 kept records, 5 invalid records, and 2 duplicates. Uppercase email addresses are normalized before comparison. Invalid records include a missing email, an incorrectly formatted email, an out-of-range age, a nonexistent calendar date, and a decimal value in an integer field.

With `deduplicate_by` set to `[]`, the same sample produces 7 kept records, 5 invalid records, and no duplicates. A rule referring to a nonexistent column fails the job and records the reason in its activity log.

Completed jobs remain in the history after a server restart. The interactive API documentation at `/docs` exposes the same operations as the dashboard.

## Scope

The worker thread separates upload handling from processing but is not a distributed task system. SQLite provides durable queue state without a separate database server. The app targets local use with a single server process.
