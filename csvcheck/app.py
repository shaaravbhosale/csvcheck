import csv
import os
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from .models import Rules
from .store import Store
from .worker import Worker

BASE = Path(__file__).resolve().parent
MAX_BYTES = 20 * 1024 * 1024


def create_app(data_dir=None, start_worker=True):
    @asynccontextmanager
    async def lifespan(app):
        root = Path(data_dir or os.environ.get('CSVCHECK_DATA_DIR', 'data')).resolve()
        app.state.store = Store(root)
        app.state.store.recover()
        worker = Worker(app.state.store)
        if start_worker:
            worker.start()
        try:
            yield
        finally:
            if start_worker:
                worker.close()

    app = FastAPI(title='CSVCheck', version='1.0.0', lifespan=lifespan)

    def get_job(job_id):
        job = app.state.store.get(job_id)
        if not job:
            raise HTTPException(404, 'Job not found.')
        return job

    @app.get('/api/health')
    def health():
        return {'status': 'ok'}

    @app.post('/api/jobs', status_code=202)
    async def upload(file: UploadFile = File(...), rules: str = Form('{}')):
        try:
            config = Rules.model_validate_json(rules)
        except ValidationError as exc:
            await file.close()
            raise HTTPException(422, '; '.join(e['msg'] for e in exc.errors()))
        if not file.filename or not file.filename.lower().endswith('.csv'):
            await file.close()
            raise HTTPException(400, 'Choose a .csv file.')
        job_id = uuid4().hex
        directory = app.state.store.root / job_id
        directory.mkdir()
        saved = False
        try:
            size = 0
            with (directory / 'input.csv').open('wb') as target:
                while chunk := await file.read(64 * 1024):
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise HTTPException(413, 'File exceeds the 20 MiB limit.')
                    target.write(chunk)
            if size == 0:
                raise HTTPException(400, 'The uploaded file is empty.')
            filename = file.filename.replace('\\', '/').split('/')[-1][:200]
            app.state.store.create(job_id, filename, config)
            saved = True
        finally:
            await file.close()
            if not saved:
                shutil.rmtree(directory, ignore_errors=True)
        return get_job(job_id)

    @app.get('/api/jobs')
    def jobs(limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0)):
        return app.state.store.list(limit, offset)

    @app.get('/api/jobs/{job_id}')
    def detail(job_id: str):
        return get_job(job_id)

    @app.post('/api/jobs/{job_id}/retry', status_code=202)
    def retry(job_id: str):
        get_job(job_id)
        if not app.state.store.retry(job_id):
            raise HTTPException(409, 'Only failed jobs with fewer than 3 attempts can be retried.')
        return get_job(job_id)

    @app.get('/api/jobs/{job_id}/files/{artifact}')
    def download(job_id: str, artifact: str):
        job = get_job(job_id)
        if artifact not in ('cleaned.csv', 'rejected.csv', 'errors.csv', 'summary.json'):
            raise HTTPException(404, 'File not found.')
        if job['status'] != 'completed':
            raise HTTPException(409, 'Results are available after the job completes.')
        path = app.state.store.root / job['id'] / 'results' / artifact
        if not path.is_file():
            raise HTTPException(404, 'Result file is missing.')
        return FileResponse(path, filename=artifact, media_type='application/json' if artifact.endswith('.json') else 'text/csv')

    @app.get('/api/sample')
    def sample():
        return FileResponse(BASE / 'sample.csv', filename='sample.csv', media_type='text/csv')

    app.mount('/', StaticFiles(directory=BASE / 'static', html=True), name='dashboard')
    return app


app = create_app()
