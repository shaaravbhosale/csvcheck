import csv
import logging
import threading

from .models import Rules
from .processor import process

log = logging.getLogger(__name__)


class Worker:
    def __init__(self, store):
        self.store = store
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=False, name='csv-worker')

    def run_one(self):
        job = self.store.claim()
        if not job:
            return False
        directory = self.store.root / job['id']
        try:
            summary = process(directory / 'input.csv', directory, Rules.model_validate_json(job['rules']))
            self.store.finish(job['id'], summary)
        except (ValueError, UnicodeError, csv.Error) as exc:
            self.store.fail(job['id'], f'Input error: {exc}')
        except Exception:
            log.exception('Job %s failed', job['id'])
            self.store.fail(job['id'], 'Processing failed. Check the server log before retrying.')
        return True

    def run(self):
        while not self.stop.is_set():
            try:
                if not self.run_one():
                    self.stop.wait(0.5)
            except Exception:
                log.exception('Queue polling failed')
                self.stop.wait(1)

    def start(self):
        self.thread.start()

    def close(self):
        self.stop.set()
        self.thread.join()
