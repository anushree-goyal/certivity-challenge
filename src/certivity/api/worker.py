"""One background worker thread, one active run at a time. Runs queue in submission order."""

from __future__ import annotations

import queue
import threading

from certivity.api.pipeline import execute_run
from certivity.api.settings import Settings
from certivity.run_store import RunStore


class RunWorker:
    def __init__(self, store: RunStore, settings: Settings):
        self.store, self.settings = store, settings
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self.active_run: str | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, name="certivity-run-worker", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5) -> None:
        if self._thread and self._thread.is_alive():
            self._queue.put(None)
            self._thread.join(timeout)

    def submit(self, run_id: str) -> None:
        self._queue.put(run_id)

    @property
    def queued(self) -> int:
        return self._queue.qsize()

    def _loop(self) -> None:
        while True:
            run_id = self._queue.get()
            if run_id is None:
                return
            self.active_run = run_id
            try:
                execute_run(run_id, self.store, self.settings)
            finally:
                self.active_run = None
                self._queue.task_done()
