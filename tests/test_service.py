"""Offline persistence/scheduling tests; external GitHub and LLM calls are mocked."""

import base64
import hashlib
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from repo_research.config import Settings
from repo_research.github import GitHubClient, GitHubError
from repo_research.models import Budget, ResearchRequest
from repo_research.service import ResearchService, budget_for_request, model_fingerprint
from repo_research.storage import TaskStore


def request(**kwargs):
    return ResearchRequest(
        repo_url="https://github.com/example/project", question="研究 invoke 的实现机制", **kwargs
    )


def wait_terminal(service, task_id, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        task = service.get_task(task_id)
        if task["status"] not in {"pending", "running"}:
            return task
        time.sleep(0.01)
    pytest.fail("Offline worker did not complete within its test deadline.")


def test_task_store_survives_restart_and_preserves_request_budget_usage(tmp_path):
    store = TaskStore(tmp_path)
    initial = store.create(request(), Budget(max_iterations=3), "mock")
    updated = store.patch(
        initial["id"], status="running", usage={"llm_calls": 2, "iterations": 1},
        progress=[{"node": "explore", "message": "读取真实文件"}],
    )
    recovered = TaskStore(tmp_path)
    assert recovered.mark_interrupted() == 1
    task = recovered.get(initial["id"])
    assert task["status"] == "interrupted"
    assert task["request"] == updated["request"]
    assert task["budget"] == updated["budget"]
    assert task["usage"] == updated["usage"]
    assert task["progress"] == updated["progress"]
    assert recovered.mark_interrupted() == 0
    assert recovered.list()[0]["id"] == initial["id"]


@pytest.mark.parametrize("invalid", ["../task", "' OR 1=1 --", "not-a-uuid", "", None])
def test_task_store_rejects_non_uuid_identifiers(tmp_path, invalid):
    store = TaskStore(tmp_path)
    with pytest.raises(KeyError):
        store.get(invalid)
    with pytest.raises(KeyError):
        store.patch(invalid, status="completed")


def test_task_identity_and_budget_cannot_be_rewritten(tmp_path):
    store = TaskStore(tmp_path)
    task = store.create(request(), Budget(), "mock")
    with pytest.raises(ValueError):
        store.patch(task["id"], budget={"max_iterations": 100})
    with pytest.raises(ValueError):
        store.patch(task["id"], id="other")


def test_concurrent_partial_patches_do_not_lose_other_fields(tmp_path):
    store = TaskStore(tmp_path)
    task = store.create(request(), Budget(), "mock")
    barrier = threading.Barrier(2)

    def patch(field, value):
        barrier.wait()
        store.patch(task["id"], **{field: value})

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(patch, "usage", {"iterations": 3}),
            executor.submit(patch, "report_markdown", "# 保留报告"),
        ]
        for future in futures:
            future.result()
    saved = store.get(task["id"])
    assert saved["usage"] == {"iterations": 3}
    assert saved["report_markdown"] == "# 保留报告"


def test_budget_depth_and_explicit_override_share_graph_defaults():
    from repo_research.agent import effective_budget

    for depth in ("quick", "standard", "deep"):
        selected = request(depth=depth)
        assert budget_for_request(selected) == effective_budget(selected)
    custom = Budget(max_iterations=2, max_files=1)
    assert budget_for_request(request(depth="deep", budget=custom)) == custom


def test_service_rejects_bad_url_before_creating_history(tmp_path):
    service = ResearchService(Settings(_env_file=None, data_dir=tmp_path))
    try:
        with pytest.raises(GitHubError) as caught:
            service.start(
                ResearchRequest(repo_url="https://evil.example/a/b", question="what is this?")
            )
        assert caught.value.code == "invalid_url"
        assert service.list_tasks() == []
    finally:
        service.close()


def test_only_one_service_can_own_directory_and_history_read_remains_available(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path)
    first = ResearchService(settings)
    try:
        with pytest.raises(ValueError, match="already owns"):
            ResearchService(settings)
        assert TaskStore(tmp_path).list() == []
    finally:
        first.close()
    second = ResearchService(settings)
    second.close()


def test_bounded_queue_and_cooperative_pause(tmp_path, monkeypatch):
    entered = threading.Event()
    released = threading.Event()

    class BlockingRunner:
        def __init__(self, pause):
            self.pause = pause

        def run(self, task_id, _request, _budget, **kwargs):
            entered.set()
            assert released.wait(10)
            state = {"status": "paused" if self.pause.is_set() else "completed"}
            kwargs["on_update"](state)
            return state

        def close(self):
            pass

    service = ResearchService(Settings(_env_file=None, data_dir=tmp_path), max_workers=1)
    monkeypatch.setattr(service, "_create_runner", BlockingRunner)
    try:
        tasks = [service.start(request()) for _ in range(4)]
        assert entered.wait(2)
        with pytest.raises(ValueError, match="queue is full"):
            service.start(request())
        paused = service.pause(tasks[0]["id"])
        assert paused["pause_requested"] is True
        with pytest.raises(ValueError, match="already queued"):
            service.resume(tasks[0]["id"])
        released.set()
        result = wait_terminal(service, tasks[0]["id"])
        assert result["status"] == "paused"
        assert result["pause_requested"] is False
        for task in tasks[1:]:
            assert wait_terminal(service, task["id"])["status"] == "completed"
    finally:
        released.set()
        service.close()


def test_worker_error_is_sanitized_and_recovery_preserves_saved_budget(tmp_path, monkeypatch):
    observed_budgets = []

    class FailingRunner:
        def run(self, task_id, _request, budget, **kwargs):
            observed_budgets.append((budget.model_dump(), kwargs["resume"]))
            kwargs["on_update"]({"usage": {"llm_calls": 3}, "progress": [{"node": "plan"}]})
            if not kwargs["resume"]:
                raise RuntimeError("secret-api-key https://sensitive.example/provider")
            return {"status": "completed", "usage": {"llm_calls": 4}}

        def close(self):
            pass

    service = ResearchService(Settings(_env_file=None, data_dir=tmp_path))
    monkeypatch.setattr(service, "_create_runner", lambda _pause: FailingRunner())
    # This scheduling test simulates the persisted checkpoint. Real saver
    # existence is exercised by the full LangGraph integration below.
    monkeypatch.setattr(service, "_checkpoint_exists", lambda _task_id: True)
    try:
        task = service.start(request(budget=Budget(max_iterations=2)))
        failed = wait_terminal(service, task["id"])
        assert failed["status"] == "interrupted"
        assert "RuntimeError" in failed["error"]
        assert "secret" not in failed["error"]
        assert failed["usage"] == {"llm_calls": 3}
        # Terminal status can be saved just before the worker leaves _jobs.
        for _ in range(100):
            if task["id"] not in service._jobs:
                break
            time.sleep(0.01)
        service.resume(task["id"])
        assert wait_terminal(service, task["id"])["usage"] == {"llm_calls": 4}
        assert observed_budgets == [(task["budget"], False), (task["budget"], True)]
    finally:
        service.close()


def test_startup_marks_abandoned_task_interrupted_without_automatic_work(tmp_path, monkeypatch):
    store = TaskStore(tmp_path)
    task = store.create(request(), Budget(), "mock")
    store.patch(task["id"], status="running", usage={"llm_calls": 7})
    service = ResearchService(Settings(_env_file=None, data_dir=tmp_path))
    try:
        assert not service._jobs
        recovered = service.get_task(task["id"])
        assert recovered["status"] == "interrupted"
        assert recovered["usage"] == {"llm_calls": 7}
        with pytest.raises(ValueError, match="original model mode"):
            service.settings.model_mode = "live"
            service.resume(task["id"])
    finally:
        service.close()


def test_service_full_langgraph_run_with_mocked_github_and_mock_model(tmp_path):
    """Real LangGraph/SQLite/tools/reporting, simulated HTTP and model reasoning."""
    commit, tree_sha = "a" * 40, "b" * 40
    source = "def invoke(tool, state):\n    return tool(state)\n"
    raw = source.encode()
    blob_sha = hashlib.sha1(f"blob {len(raw)}\0".encode() + raw).hexdigest()

    def handler(http_request):
        path = http_request.url.path
        if path == "/repos/example/project":
            return httpx.Response(200, json={"default_branch": "main", "language": "Python"})
        if path == f"/repos/example/project/commits/{commit}":
            return httpx.Response(200, json={"sha": commit, "commit": {"tree": {"sha": tree_sha}}})
        if path == f"/repos/example/project/git/trees/{tree_sha}":
            return httpx.Response(200, json={"sha": tree_sha, "truncated": False, "tree": [
                {"path": "core.py", "type": "blob", "sha": blob_sha, "size": len(raw)},
            ]})
        if path == f"/repos/example/project/git/blobs/{blob_sha}":
            return httpx.Response(200, json={
                "sha": blob_sha, "size": len(raw), "encoding": "base64",
                "content": base64.b64encode(raw).decode(),
            })
        pytest.fail(f"Unexpected offline GitHub endpoint: {path}")

    def client_factory(**kwargs):
        return GitHubClient(**kwargs, transport=httpx.MockTransport(handler))

    service = ResearchService(
        Settings(_env_file=None, data_dir=tmp_path), client_factory=client_factory
    )
    try:
        task = service.start(request(ref=commit))
        result = wait_terminal(service, task["id"])
        assert result["status"] == "completed", result["error"]
        assert result["model_mode"] == "mock"
        assert result["repository"]["commit_sha"] == commit
        assert result["evidence"]
        for evidence in result["evidence"]:
            assert evidence["verified"] is True
            assert evidence["commit_sha"] == commit
            assert evidence["path"] == "core.py"
            assert 1 <= evidence["start_line"] <= evidence["end_line"] <= 2
            assert f"/blob/{commit}/core.py#L" in evidence["url"]
        assert "MOCK" in result["report_markdown"]
        assert result["usage"]["llm_calls"] == 0
        assert service._checkpoint_exists(task["id"])
        assert TaskStore(tmp_path).get(task["id"])["report_markdown"] == result["report_markdown"]
    finally:
        service.close()


def test_cli_list_reads_history_without_interrupting_running_service(tmp_path, capsys):
    from repo_research.cli import main

    service = ResearchService(Settings(_env_file=None, data_dir=tmp_path))
    task = service.store.create(request(), Budget(), "mock")
    service.store.patch(task["id"], status="running")
    try:
        assert main(["list", "--data-dir", str(tmp_path), "--mode", "mock"]) == 0
        assert task["id"] in capsys.readouterr().out
        assert service.get_task(task["id"])["status"] == "running"
    finally:
        service.close()


def test_cli_mock_probe_is_explicitly_labelled_and_spends_no_calls(tmp_path, capsys):
    import json

    from repo_research.cli import main

    assert main(["probe", "--data-dir", str(tmp_path), "--mode", "mock"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["capabilities"]["mode"] == "mock"
    assert "MOCK" in result["capabilities"]["details"][0]
    assert result["usage"]["llm_calls"] == 0


def test_resume_refuses_missing_checkpoint_after_saved_work(tmp_path):
    service = ResearchService(Settings(_env_file=None, data_dir=tmp_path))
    try:
        task = service.store.create(request(), Budget(), "mock")
        service.store.patch(task["id"], status="interrupted", usage={"llm_calls": 1})
        with pytest.raises(ValueError, match="spent budget cannot be reset"):
            service.resume(task["id"])
        assert not service._jobs
        assert service.get_task(task["id"])["usage"] == {"llm_calls": 1}
        service.store.patch(task["id"], usage={}, progress=[{"node": "initialize"}])
        with pytest.raises(ValueError, match="checkpoint is missing"):
            service.resume(task["id"])
    finally:
        service.close()


def test_live_configuration_fingerprint_hides_endpoint_and_excludes_api_key(tmp_path):
    import json

    from pydantic import SecretStr

    settings = Settings(
        _env_file=None, data_dir=tmp_path, model_mode="live",
        llm_base_url="https://user:password@relay.invalid/v1", llm_api_key="fake-key",
        llm_model="fixture-model", llm_input_price_per_million=1,
    )
    fingerprint = model_fingerprint(settings)
    assert len(fingerprint) == 64
    rotated = settings.model_copy(update={"llm_api_key": SecretStr("new-fake-key")})
    assert model_fingerprint(rotated) == fingerprint
    for change in (
        {"llm_model": "another-model"}, {"llm_base_url": "https://different.invalid/v1"},
        {"llm_input_price_per_million": 2}, {"llm_output_price_per_million": 3},
    ):
        assert model_fingerprint(settings.model_copy(update=change)) != fingerprint
    assert model_fingerprint(settings.model_copy(update={"model_mode": "mock"})) is None

    service = ResearchService(settings)
    try:
        task = service.store.create(request(), Budget(), "live", model_fingerprint=fingerprint)
        service.store.patch(task["id"], status="interrupted")
        serialized = json.dumps(service.get_task(task["id"]))
        assert "relay.invalid" not in serialized
        assert "password" not in serialized
        assert "fake-key" not in serialized
        service.settings = settings.model_copy(update={"llm_model": "another-model"})
        with pytest.raises(ValueError, match="original provider, model and prices"):
            service.resume(task["id"])
        assert not service._jobs
    finally:
        service.close()
