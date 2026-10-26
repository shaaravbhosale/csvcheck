# Validation notes

Checked with Python 3.12 in the build environment.

- `python -m pytest -q`: 11 passed. The installed Starlette version emits a TestClient/httpx deprecation warning; it doesn't fail the tests.
- Dashboard DOM integration: sample selection, form submission, polling, result counts, download URL, and safe rendering of HTML-looking file names and CSV values passed against a running local server.
- Generated 100,000-record CSV: 90,000 distinct emails and 10,000 duplicate emails; the result was 90,000 kept, 10,000 duplicates, and zero invalid records. The processor took 0.4955 seconds in this one run. This is not an end-to-end latency measurement or a Windows performance guarantee.

A full browser and visual layout check could not be completed in the build environment because a browser binary was unavailable. The dashboard has responsive CSS, but its appearance should still be checked locally on desktop and mobile widths.
