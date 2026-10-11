"""In-memory job manager for the asynchronous API (submit -> poll)."""
from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any, Callable


class JobManager:
    def __init__(self, max_jobs: int = 1000):
        self.max_jobs = max_jobs
        self._jobs: dict[str, dict[str, Any]] = {}
        self._tasks: set[asyncio.Task] = set()

    def submit(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> str:
        """Must be called from inside a running event loop (e.g. an `async def` endpoint)."""
        job_id = uuid.uuid4().hex
        self._jobs[job_id] = {"id": job_id, "status": "queued", "result": None, "error": None,
                              "created_at": time.time(), "finished_at": None}
        while len(self._jobs) > self.max_jobs:
            self._jobs.pop(next(iter(self._jobs)))
        task = asyncio.get_running_loop().create_task(self._run(job_id, fn, args, kwargs))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return job_id

    async def _run(self, job_id: str, fn: Callable[..., Any], args: tuple, kwargs: dict) -> None:
        job = self._jobs.get(job_id)
        if job is None:
            return
        job["status"] = "running"
        try:
            job["result"] = await asyncio.to_thread(fn, *args, **kwargs)
            job["status"] = "done"
        except Exception as exc:  # noqa: BLE001
            job["status"], job["error"] = "failed", f"{type(exc).__name__}: {exc}"
        finally:
            job["finished_at"] = time.time()

    def get(self, job_id: str) -> dict[str, Any] | None:
        return self._jobs.get(job_id)
