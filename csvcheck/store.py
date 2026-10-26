import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


class Store:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / 'jobs.sqlite3'
        with self.connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    filename TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued',
                    rules TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    error TEXT,
                    summary TEXT
                );
                CREATE INDEX IF NOT EXISTS jobs_queue ON jobs(status, created_at);
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL REFERENCES jobs(id),
                    at TEXT NOT NULL,
                    message TEXT NOT NULL
                );
            ''')

    @contextmanager
    def connect(self):
        # Each request/worker gets its own connection rather than sharing one across threads.
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    def event(self, db, job_id, message):
        db.execute('INSERT INTO events(job_id,at,message) VALUES (?,?,?)', (job_id, now(), message))

    def create(self, job_id, filename, rules):
        with self.connect() as db:
            db.execute('INSERT INTO jobs(id,filename,rules,created_at) VALUES (?,?,?,?)',
                       (job_id, filename, rules.model_dump_json(), now()))
            self.event(db, job_id, 'Upload saved; job queued.')

    def recover(self):
        with self.connect() as db:
            rows = db.execute("SELECT id FROM jobs WHERE status='running'").fetchall()
            for row in rows:
                db.execute("UPDATE jobs SET status='queued', started_at=NULL WHERE id=?", (row['id'],))
                self.event(db, row['id'], 'Previous run interrupted; queued again on startup.')

    def claim(self):
        with self.connect() as db:
            # The read and status update must hold the same write lock.
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created_at, rowid LIMIT 1").fetchone()
            if row is None:
                return None
            db.execute("UPDATE jobs SET status='running', started_at=?, attempts=attempts+1 WHERE id=?",
                       (now(), row['id']))
            self.event(db, row['id'], 'Worker started processing.')
            return dict(row)

    def finish(self, job_id, summary):
        with self.connect() as db:
            db.execute("UPDATE jobs SET status='completed', finished_at=?, summary=?, error=NULL WHERE id=?",
                       (now(), json.dumps(summary), job_id))
            self.event(db, job_id, f"Completed: {summary['kept']} kept, {summary['invalid']} invalid, {summary['duplicates']} duplicates.")

    def fail(self, job_id, error):
        with self.connect() as db:
            db.execute("UPDATE jobs SET status='failed', finished_at=?, error=? WHERE id=?", (now(), error, job_id))
            self.event(db, job_id, error)

    def retry(self, job_id):
        with self.connect() as db:
            changed = db.execute("UPDATE jobs SET status='queued', error=NULL, summary=NULL, started_at=NULL, finished_at=NULL WHERE id=? AND status='failed' AND attempts<3",
                                 (job_id,)).rowcount
            if changed:
                self.event(db, job_id, 'Manual retry requested.')
            return bool(changed)

    def decode(self, row):
        item = dict(row)
        item['rules'] = json.loads(item['rules'])
        item['summary'] = json.loads(item['summary']) if item['summary'] else None
        return item

    def get(self, job_id):
        with self.connect() as db:
            row = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
            if not row:
                return None
            item = self.decode(row)
            item['events'] = [dict(e) for e in db.execute('SELECT at,message FROM events WHERE job_id=? ORDER BY id', (job_id,))]
            return item

    def list(self, limit, offset):
        with self.connect() as db:
            total = db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]
            rows = db.execute('SELECT * FROM jobs ORDER BY created_at DESC, rowid DESC LIMIT ? OFFSET ?', (limit, offset))
            return {'total': total, 'items': [self.decode(r) for r in rows]}
