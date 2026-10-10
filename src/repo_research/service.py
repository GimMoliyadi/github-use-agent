"""Bounded worker scheduling around a checkpointed, single-task research graph."""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

from .config import Settings
from .github import parse_repo_url
from .models import Budget, ResearchRequest
from .process_lock import ProcessLock
from .provider_settings import (
    ModelConfiguration,
    candidate_settings,
    load_model_settings,
    public_model_settings,
    save_model_settings,
)
from .storage import TaskStore

logger = logging.getLogger(__name__)


def budget_for_request(request: ResearchRequest) -> Budget:
    from .agent import effective_budget

    return effective_budget(request).model_copy(deep=True)


def model_fingerprint(settings: Settings) -> str | None:
    """Only a digest is persisted; keys are neither persisted nor hashed.

    Even a configured endpoint can embed credentials, so no part of that URL
    enters the task payload in plaintext. Key rotation remains possible.
    """
    if settings.model_mode == "mock":
        return None
    configuration = {
        "mode": settings.model_mode, "model": settings.llm_model,
        "base_url": settings.llm_base_url, "output_mode": settings.llm_output_mode,
        "input_price": settings.llm_input_price_per_million,
        "output_price": settings.llm_output_price_per_million,
    }
    encoded = json.dumps(configuration, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


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
        self.settings = load_model_settings(settings)
        self.store = TaskStore(settings.data_dir)
        self._owner_lock = ProcessLock(self.store.data_dir / "service.lock")
        self.store.mark_interrupted()
        self.client_factory = client_factory
        self.model_factory = model_factory
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="research")
        self._lock = threading.RLock()
        self._jobs: dict[str, tuple[threading.Event, Future]] = {}
        self._closed = False

    def model_configuration(self) -> dict:
        with self._lock:
            return public_model_settings(self.settings)

    def configure_model(self, configuration: ModelConfiguration) -> dict:
        with self._lock:
            if self._closed or self._jobs:
                raise ValueError("请先暂停或完成正在运行的研究，再修改模型配置。")
            selected = candidate_settings(self.settings, configuration)
            save_model_settings(selected)
            self.settings = selected
            return public_model_settings(selected)

    def probe_model(self, configuration: ModelConfiguration) -> dict:
        from .llm import make_model
        from .models import Usage

        with self._lock:
            selected = candidate_settings(self.settings, configuration)
        # Probe has its own small budget and never changes running task settings.
        usage = Usage()
        model = make_model(
            selected,
            Budget(max_llm_calls=3 * (selected.llm_max_retries + 1), max_tokens=12000),
            usage,
        )
        try:
            return {"capabilities": model.probe().model_dump(), "usage": usage.model_dump()}
        finally:
            if hasattr(model, "close"):
                model.close()

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
            task = self.store.create(
                request, budget_for_request(request), self.settings.model_mode,
                model_fingerprint=model_fingerprint(self.settings),
            )
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
            if self.settings.model_mode == "live" and (
                task.get("model_fingerprint") != model_fingerprint(self.settings)
            ):
                raise ValueError("Resume requires the task's original provider, model and prices.")
            has_saved_work = bool(
                task.get("progress") or task.get("repository") or task.get("evidence")
            ) or any(
                isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0
                for value in task.get("usage", {}).values()
            )
            if has_saved_work and not self._checkpoint_exists(task_id):
                raise ValueError(
                    "The task checkpoint is missing. Restore its saved data directory; "
                    "spent budget cannot be reset."
                )
            self._check_capacity()
            task = self.store.patch(
                task_id, status="pending", error=None, pause_requested=False, stop_reason=""
            )
            self._submit(task, resume=True)
        return task

    def _checkpoint_exists(self, task_id: str) -> bool:
        if not self.store.checkpoint_path.exists():
            return False
        from langgraph.checkpoint.sqlite import SqliteSaver

        connection = sqlite3.connect(self.store.checkpoint_path, timeout=15)
        connection.execute("PRAGMA busy_timeout=15000")
        try:
            checkpoint = SqliteSaver(connection).get_tuple({"configurable": {"thread_id": task_id}})
            return checkpoint is not None
        finally:
            connection.close()

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
            with self._lock:
                if pause_event.is_set():
                    self.store.patch(
                        task_id, status="paused", pause_requested=False,
                        stop_reason="Paused before repository initialization.",
                    )
                    return
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
                try:
                    self.store.patch(task_id, pause_requested=False)
                except Exception as exc:
                    logger.error("Research status cleanup failed: %s", type(exc).__name__)
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
        self._owner_lock.close()
