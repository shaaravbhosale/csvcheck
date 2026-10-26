import csv
import io
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from csvcheck.app import create_app
from csvcheck.models import Rules
from csvcheck.store import Store
from csvcheck.worker import Worker


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path, start_worker=False)) as c:
        yield c


def submit(client, text, rules=None, name='test.csv'):
    return client.post('/api/jobs', files={'file': (name, text, 'text/csv')},
                       data={'rules': json.dumps(rules or {})})


def run(client):
    assert Worker(client.app.state.store).run_one()


def test_sample_pipeline(client):
    rules = {'deduplicate_by': ['email'], 'columns': [
        {'name': 'email', 'kind': 'email', 'required': True, 'lowercase': True},
        {'name': 'age', 'kind': 'integer', 'minimum': 0, 'maximum': 120},
        {'name': 'joined', 'kind': 'date', 'required': True}]}
    sample = (Path(__file__).parents[1] / 'csvcheck/sample.csv').read_bytes()
    response = submit(client, sample, rules)
    assert response.status_code == 202
    job_id = response.json()['id']
    assert client.get(f'/api/jobs/{job_id}/files/cleaned.csv').status_code == 409
    run(client)
    job = client.get(f'/api/jobs/{job_id}').json()
    s = job['summary']
    assert job['status'] == 'completed'
    assert (s['total'], s['kept'], s['invalid'], s['duplicates']) == (12, 5, 5, 2)
    assert s['preview'][0]['name'] == 'Alice Morgan'
    assert s['preview'][0]['email'] == 'alice@example.com'
    cleaned = client.get(f'/api/jobs/{job_id}/files/cleaned.csv')
    assert len(list(csv.DictReader(io.StringIO(cleaned.text)))) == 5
    errors = client.get(f'/api/jobs/{job_id}/files/errors.csv')
    assert len(list(csv.DictReader(io.StringIO(errors.text)))) == 5
    assert client.get(f'/api/jobs/{job_id}/files/summary.json').json()['total'] == 12
    assert client.post(f'/api/jobs/{job_id}/retry').status_code == 409


def test_invalid_row_does_not_claim_duplicate_key(client):
    job = submit(client, 'id,email\nno,a@b.com\n2,a@b.com\n3,a@b.com\n', {
        'deduplicate_by': ['email'], 'columns': [{'name': 'id', 'kind': 'integer'}]}).json()
    run(client)
    summary = client.get('/api/jobs/' + job['id']).json()['summary']
    assert (summary['kept'], summary['invalid'], summary['duplicates']) == (1, 1, 1)


def test_bad_headers_and_retry_limit(client):
    job_id = submit(client, 'id,id\n1,2\n').json()['id']
    for attempt in range(1, 4):
        run(client)
        job = client.get('/api/jobs/' + job_id).json()
        assert job['status'] == 'failed'
        assert job['attempts'] == attempt
        assert client.get(f'/api/jobs/{job_id}/files/cleaned.csv').status_code == 409
        retry = client.post(f'/api/jobs/{job_id}/retry')
        assert retry.status_code == (202 if attempt < 3 else 409)


def test_missing_column_and_malformed_row(client):
    a = submit(client, 'id\n1\n', {'columns': [{'name': 'email'}]}).json()['id']
    run(client)
    assert 'Missing columns: email' in client.get('/api/jobs/' + a).json()['error']
    b = submit(client, 'id,name\n1,A\n2,B,extra\n3,C\n').json()['id']
    run(client)
    summary = client.get('/api/jobs/' + b).json()['summary']
    assert summary['kept'] == 2 and summary['invalid'] == 1


def test_bad_uploads_leave_no_job(client):
    assert submit(client, '').status_code == 400
    assert submit(client, 'id\n1', name='image.png').status_code == 400
    assert submit(client, 'id\n1', {'columns': [{'name': 'age', 'kind': 'unknown'}]}).status_code == 422
    assert submit(client, 'id\n1', {'columns': [{'name': 'age', 'kind': 'number', 'minimum': 10, 'maximum': 0}]}).status_code == 422
    assert client.get('/api/jobs').json()['total'] == 0
    assert not [p for p in client.app.state.store.root.iterdir() if p.is_dir()]


def test_upload_limit(client, monkeypatch):
    monkeypatch.setattr('csvcheck.app.MAX_BYTES', 10)
    assert submit(client, 'id\n' + '1' * 20).status_code == 413
    assert client.get('/api/jobs').json()['total'] == 0


def test_utf8_bom_quoted_newlines_and_nonfinite_numbers(client):
    text = '\ufeffid,note,value\n1,"a,b\nnext line",2.5\n2,test,NaN\n3,test,inf\n'
    job = submit(client, text.encode(), {'columns': [{'name': 'value', 'kind': 'number'}]}).json()
    run(client)
    s = client.get('/api/jobs/' + job['id']).json()['summary']
    assert s['kept'] == 1 and s['invalid'] == 2
    assert s['preview'][0]['note'] == 'a,b\nnext line'


def test_queue_survives_restart(tmp_path):
    with TestClient(create_app(tmp_path, start_worker=False)) as first:
        job_id = submit(first, 'id\n1\n').json()['id']
        assert first.app.state.store.claim()['id'] == job_id
    with TestClient(create_app(tmp_path, start_worker=False)) as second:
        assert second.get('/api/jobs/' + job_id).json()['status'] == 'queued'
        run(second)
        job = second.get('/api/jobs/' + job_id).json()
        assert job['status'] == 'completed' and job['attempts'] == 2
        assert any('interrupted' in e['message'] for e in job['events'])


def test_two_workers_do_not_claim_same_job(tmp_path):
    store = Store(tmp_path)
    for i in range(6):
        store.create(str(i), 'test.csv', Rules())
    with ThreadPoolExecutor(max_workers=4) as pool:
        claimed = list(pool.map(lambda _: store.claim(), range(8)))
    ids = [j['id'] for j in claimed if j]
    assert len(ids) == 6 and len(set(ids)) == 6


def test_pagination_and_missing_resources(client):
    for i in range(3):
        submit(client, f'id\n{i}\n')
    page = client.get('/api/jobs?limit=2&offset=2').json()
    assert page['total'] == 3 and len(page['items']) == 1
    assert client.get('/api/jobs?limit=0').status_code == 422
    assert client.get('/api/jobs/missing').status_code == 404
    assert client.post('/api/jobs/missing/retry').status_code == 404
    assert client.get('/').status_code == 200


def test_background_worker_end_to_end(tmp_path):
    import time
    with TestClient(create_app(tmp_path)) as client:
        job_id = submit(client, 'id\n1\n').json()['id']
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            job = client.get('/api/jobs/' + job_id).json()
            if job['status'] in ('completed', 'failed'):
                break
            time.sleep(.03)
        assert job['status'] == 'completed'
        assert client.get('/api/health').json() == {'status': 'ok'}
