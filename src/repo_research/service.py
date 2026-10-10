"""Bounded worker scheduling around a checkpointed, single-task research graph."""

from __future__ import annotations

import fcntl
import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

from .config import Settings
from .github import parse_repo_url
from .models import Budget, ResearchRequest
from .storage import TaskStore

logger = logging.getLogger(__name__)


def budget_for_request(request: ResearchRequest) -> Budget:
    if request.budget is not None:
        return request.budget.model_copy(deep=True)
    if request.depth == "quick":
        return Budget(max_iterations=6, max_files=8, max_bytes=180_000, max_llm_calls=12)
    if request.depth == "deep":
        return Budget(max_iterations=24, max_files=32, max_bytes=800_000, max_llm_calls=32,
                      max_tokens=160_000, max_api_requests=100)
    return Budget()


class ResearchService:
    """One process, at most four active tasks, and independent task connections.

    Startup never restarts billable work automatically. Interrupted tasks keep
    their original model mode and budgets and require an explicit resume.
    """

    def __init__(
        self, settings: Settings, client_factory=None, model_factory=None, max_workers: int = 2
    ) -> None:
        if not 1 <= max_workers <= 4:
            raise ValueError("Use between one and four research workers.")
        self.settings = settings
        self.store = TaskStore(settings.data_dir)
        self._owner_lock = (self.store.data_dir / "service.lock").open("a")
        try:
            fcntl.flock(self._owner_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self._owner_lock.close()
            raise ValueError("A research service already owns this data directory.") from None
        self.store.mark_interrupted()
        self.client_factory = client_factory
        self.model_factory = model_factory
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="research")
        self._lock = threading.RLock()
        self._jobs: dict[str, tuple[threading.Event, Future]] = {}
        self._closed = False

    def _check_capacity(self) -> None:
        if self._closed:
            raise ValueError("Research service is closed.")
        if len(self._jobs) >= 4:
            raise ValueError("Research queue is full. Wait for an active task to finish.")

    def _submit(self, task: dict, *, resume: bool) -> None:
        task_id = task["id"]
        pause_event = threading.Event()
        # Caller holds _lock: an immediately completing worker cannot remove its
        # entry before the future has been installed.
        try:
            future = self._executor.submit(self._run, task, pause_event, resume)
        except Exception:
            self.store.patch(task_id, status="failed", error="Worker scheduling failed.")
            raise
        self._jobs[task_id] = (pause_event, future)

    def start(self, request: ResearchRequest) -> dict[str, Any]:
        # Invalid URLs do not consume queue slots or create phantom history.
        parse_repo_url(request.repo_url)
        with self._lock:
            self._check_capacity()
            task = self.store.create(request, budget_for_request(request), self.settings.model_mode)
            self._submit(task, resume=False)
        return task

    def get_task(self, task_id: str) -> dict[str, Any]:
        return self.store.get(task_id)

    def list_tasks(self, limit: int = 30) -> list[dict[str, Any]]:
        return self.store.list(limit)

    def resume(self, task_id: str) -> dict[str, Any]:
        with self._lock:
            task = self.store.get(task_id)
            if task_id in self._jobs:
                raise ValueError("This task is already queued or running.")
            if task["status"] not in {"paused", "interrupted"}:
                raise ValueError("Only paused or interrupted tasks can be resumed.")
            if task["model_mode"] != self.settings.model_mode:
                raise ValueError("Resume requires the task's original model mode.")
            self._check_capacity()
            task = self.store.patch(
                task_id, status="pending", error=None, pause_requested=False, stop_reason=""
            )
            self._submit(task, resume=True)
        return task

    def pause(self, task_id: str) -> dict[str, Any]:
        with self._lock:
            task = self.store.get(task_id)
            job = self._jobs.get(task_id)
            if job is None or task["status"] not in {"pending", "running"}:
                raise ValueError("Only queued or running tasks can be paused.")
            job[0].set()
            # Pause is cooperative at graph boundaries, not a claim that an
            # in-flight HTTP call has already been cancelled.
            return self.store.patch(task_id, pause_requested=True)

    def _create_runner(self, pause_event: threading.Event):
        from .agent import ResearchRunner

        return ResearchRunner(
            self.settings, self.store.checkpoint_path, pause_event=pause_event,
            client_factory=self.client_factory, model_factory=self.model_factory,
        )

    def _run(self, task: dict, pause_event: threading.Event, resume: bool) -> None:
        task_id = task["id"]
        runner = None
        try:
            self.store.patch(task_id, status="running")
            runner = self._create_runner(pause_event)

            def update(state: dict) -> None:
                fields = {
                    key: value for key, value in state.items()
                    if key in self.store.MUTABLE_FIELDS and key != "pause_requested"
                }
                self.store.patch(task_id, **fields)

            state = runner.run(
                task_id, ResearchRequest.model_validate(task["request"]),
                Budget.model_validate(task["budget"]), resume=resume, on_update=update,
            )
            update(state)
            self.store.patch(task_id, pause_requested=False)
        except Exception as exc:
            # Upstream exception text can contain URL credentials, source data,
            # provider bodies or local paths. Keep only its class name.
            logger.error("Research worker failed (%s): %s", task_id, type(exc).__name__)
            self.store.patch(
                task_id, status="interrupted", pause_requested=False,
                error=f"Research interrupted ({type(exc).__name__}).",
                stop_reason="Saved state is retained. Resume to retry the interrupted step.",
            )
        finally:
            if runner is not None:
                try:
                    runner.close()
                except Exception as exc:
                    logger.error("Research cleanup failed: %s", type(exc).__name__)
            with self._lock:
                self._jobs.pop(task_id, None)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            for pause_event, _future in self._jobs.values():
                pause_event.set()
        # Each configured HTTP request has a finite timeout. Graceful shutdown
        # waits for workers to reach graph boundaries and flush checkpoints.
        self._executor.shutdown(wait=True, cancel_futures=False)
        fcntl.flock(self._owner_lock.fileno(), fcntl.LOCK_UN)
        self._owner_lock.close()
