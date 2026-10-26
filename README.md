# CSVCheck

A small web app for checking CSV files before using them in another project. Upload a file, configure column rules, and get a cleaned CSV plus separate reports for invalid and duplicate records.

The backend is Python, FastAPI, and SQLite. The frontend is plain HTML, CSS, and JavaScript. A background worker processes one upload at a time, so an upload request doesn't need to wait for the whole file to finish.

## Run it on Windows

Use Python 3.12 or newer. Open PowerShell in this folder:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn csvcheck.app:app --host 127.0.0.1 --port 8000
```

For newer Python installations, `py` can be used instead of `py -3.12`.

Open **http://127.0.0.1:8000**. The API docs are at **http://127.0.0.1:8000/docs**. Press Ctrl+C in the terminal to stop the server.

On macOS/Linux:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m uvicorn csvcheck.app:app --host 127.0.0.1 --port 8000
```

## Try a check

1. Click **Load sample**. This selects a sample file and fills in its rules.
2. Click **Run check**.
3. The sample should finish with **12 total, 5 kept, 5 invalid, and 2 duplicates**.
4. Download the cleaned file or open the errors report to see which fields failed.

The sample uses fictional people. Rules must match the uploaded file's column names. The configuration `{"columns": []}` applies whitespace cleanup without field validation.

```json
{
  "trim_whitespace": true,
  "deduplicate_by": ["email"],
  "columns": [
    {"name": "email", "kind": "email", "required": true, "lowercase": true},
    {"name": "age", "kind": "integer", "minimum": 0, "maximum": 120},
    {"name": "joined", "kind": "date", "required": true}
  ]
}
```

Supported types are `text`, `email`, `integer`, `number`, and `date`. Numeric bounds are inclusive. Blank optional values pass. Email checks are a simple format check, not verification that a mailbox exists. Dates must be real calendar dates in `YYYY-MM-DD` format.

Headers are trimmed before matching. Values are trimmed when `trim_whitespace` is true, and `lowercase` applies before validation and duplicate detection. Values otherwise stay as strings; numeric validation doesn't rewrite how a number is formatted.

Duplicate detection uses the selected columns together. It keeps the first **valid** occurrence of each normalized key. With no `deduplicate_by` columns, duplicates are kept. Blank keys are compared like other values; add a required rule if blanks shouldn't be accepted.

## Output files

| File | Contents |
| --- | --- |
| `cleaned.csv` | Valid, unique records, including all original columns |
| `rejected.csv` | Source record number, reason, and original values encoded as JSON |
| `errors.csv` | Each failed column check and its source record number |
| `summary.json` | Counts, issues by column, processing time, and the first 10 cleaned records |

Source record numbers count CSV records, including the header as record 1. A quoted field containing a newline is still one record. Fully empty lines are counted as malformed records. A row with the wrong number of fields is rejected; a structurally broken CSV, missing rule column, invalid UTF-8, or invalid header fails the job.

`total = kept + invalid + duplicates`. The normalized count overlaps those categories and counts records whose values changed during cleanup. Processing time covers the processor, not the upload or time waiting in the queue.

## What happens in the backend

`app.py` saves an upload using a generated ID, then inserts a queued job in SQLite. The worker claims it in a transaction, processes the CSV, and updates the status. Each job has an activity log. Finished jobs stay available after a restart.

The processor streams through the CSV rather than loading every row at once. The set of duplicate keys still grows with the number of distinct records. Output is written to a staging folder and renamed when processing succeeds. Downloads are only available for completed jobs, so a failed job doesn't expose a partial result.

If the server stops during processing, the next startup puts running jobs back in the queue. This reruns the entire file; it doesn't resume from the last row. Failed jobs can be manually retried up to three total attempts. Retrying a file with the same invalid headers will still fail; corrected files require a new upload.

## API

| Method | Route | Purpose |
| --- | --- | --- |
| POST | `/api/jobs` | Multipart upload with a `file` and a JSON string named `rules` |
| GET | `/api/jobs?limit=20&offset=0` | Paginated history |
| GET | `/api/jobs/{id}` | Status, summary, rules, and activity |
| POST | `/api/jobs/{id}/retry` | Retry a failed job |
| GET | `/api/jobs/{id}/files/{artifact}` | Download a result |
| GET | `/api/sample` | Download the sample CSV |
| GET | `/api/health` | Basic server health |

Use `/docs` to try the API without writing a client.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
```

The tests cover the sample pipeline, normalization, duplicate ordering, malformed rows and headers, size limits, failed jobs, restart recovery, pagination, concurrent queue claims, and the actual background worker. GitHub Actions runs them on pushes and pull requests.

## Files

- `csvcheck/app.py`: API routes and app startup
- `csvcheck/store.py`: SQLite queries and job transitions
- `csvcheck/processor.py`: CSV cleanup and validation
- `csvcheck/worker.py`: worker thread
- `csvcheck/models.py`: rule validation
- `csvcheck/static/index.html`: the dashboard, including its CSS and JavaScript
- `tests/test_app.py`: API and processing tests

## Current limits

This is a local, single-user tool. Run **one server process**, with no `--workers` option or overlapping server instances sharing the same data folder. Startup recovery assumes the old worker is gone. There is no login system, so this version is not intended to be exposed publicly.

Uploads are limited to 20 MiB, 100,000 data records, and 100 columns. Inputs must be UTF-8 comma-separated CSVs. This version doesn't support Excel workbooks, custom delimiters, or automatic type inference. Values aren't escaped for spreadsheet formula execution, so open files from untrusted sources in a text editor rather than a spreadsheet.

Jobs, uploads, and results are stored in `data/`, which Git ignores. There is no automatic retention policy; shut down the app and delete that folder to clear the history. The `CSVCHECK_DATA_DIR` environment variable configures an alternative storage location.

Possible next changes: a form-based rule editor, saved rule profiles, and job cancellation.

## References

- [FastAPI file uploads](https://fastapi.tiangolo.com/tutorial/request-files/)
- [Python sqlite3 documentation](https://docs.python.org/3.12/library/sqlite3.html)
