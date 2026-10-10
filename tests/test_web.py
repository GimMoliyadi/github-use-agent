"""Offline HTTP contract tests. These never call GitHub or an LLM."""

import os
import socket
import threading
import time
from copy import deepcopy

import httpx
import pytest
from fastapi.testclient import TestClient

from repo_research.config import Settings
from repo_research.models import ResearchRequest
from repo_research.web import create_app


class StubService:
    def __init__(self):
        self.closed = False
        self.tasks = {}

    def start(self, request):
        if not request.repo_url.startswith("https://github.com/"):
            raise ValueError("需要公开 GitHub 仓库 URL。")
        task = {
            "id": "offline-task",
            "status": "running",
            "request": request.model_dump(),
            "created_at": "2026-10-10T02:00:00Z",
            "progress": [],
            "usage": {},
            "report": None,
            "report_markdown": "",
        }
        self.tasks[task["id"]] = task
        return deepcopy(task)

    def list_tasks(self, limit=30):
        return [deepcopy(task) for task in list(self.tasks.values())[:limit]]

    def get_task(self, task_id):
        return deepcopy(self.tasks[task_id])

    def pause(self, task_id):
        self.tasks[task_id]["status"] = "paused"
        return self.get_task(task_id)

    def resume(self, task_id):
        self.tasks[task_id]["status"] = "running"
        return self.get_task(task_id)

    def close(self):
        self.closed = True


@pytest.fixture
def service():
    return StubService()


@pytest.fixture
def client(service, tmp_path):
    with TestClient(create_app(Settings(_env_file=None, data_dir=tmp_path), service)) as client:
        yield client
    assert service.closed


def test_self_hosted_ui_and_health(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "RepoLens" in response.text
    assert 'lang="zh-CN"' in response.text
    assert client.get("/static/style.css").status_code == 200
    javascript = client.get("/static/app.js")
    assert javascript.status_code == 200
    assert "innerHTML" not in javascript.text
    assert "textContent" in javascript.text
    assert "https://cdn" not in response.text
    assert "script-src 'self'" in response.headers["content-security-policy"]
    assert client.get("/health").json() == {"status": "ok"}


def test_task_lifecycle_and_report_download(client, service):
    request = {"repo_url": "https://github.com/example/project", "question": "分析入口"}
    created = client.post("/api/tasks", json=request)
    assert created.status_code == 202
    task = created.json()
    assert task["status"] == "running"
    assert client.get("/api/tasks").json()[0]["id"] == task["id"]
    assert client.get(f"/api/tasks/{task['id']}").json()["request"]["question"] == "分析入口"
    assert client.post(f"/api/tasks/{task['id']}/pause").json()["status"] == "paused"
    assert client.post(f"/api/tasks/{task['id']}/resume").json()["status"] == "running"
    assert client.get(f"/api/tasks/{task['id']}/report").status_code == 409
    service.tasks[task["id"]]["report_markdown"] = "# 真实引用\n\n测试报告。"
    report = client.get(f"/api/tasks/{task['id']}/report")
    assert report.status_code == 200
    assert "text/markdown" in report.headers["content-type"]
    assert report.headers["content-disposition"].startswith("attachment;")
    assert "真实引用" in report.text


@pytest.mark.parametrize("path", ["/api/tasks/missing", "/api/tasks/missing/report"])
def test_missing_task_returns_404(client, path):
    assert client.get(path).status_code == 404


@pytest.mark.parametrize("action", ["pause", "resume"])
def test_missing_task_action_returns_404(client, action):
    assert client.post(f"/api/tasks/missing/{action}").status_code == 404


def test_validation_and_invalid_github_url(client):
    assert client.post("/api/tasks", json={}).status_code == 422
    assert client.post(
        "/api/tasks", json={"repo_url": "https://example.com/foo/bar", "question": "研究模块"}
    ).status_code == 400
    assert client.get("/api/tasks?limit=0").status_code == 422
    assert client.get("/api/tasks?limit=101").status_code == 422
    assert client.post(
        "/api/tasks",
        json={
            "repo_url": "https://github.com/example/project",
            "question": "研究模块",
            "budget": {"max_iterations": 1000},
        },
    ).status_code == 422


@pytest.mark.parametrize(
    "payload",
    [
        {"repo_url": 12345, "question": "研究模块"},
        {"repo_url": "https://github.com/example/project", "question": {"text": "研究模块"}},
        {"repo_url": "https://github.com/example/project", "question": "   "},
    ],
)
def test_non_string_and_blank_requests_return_422(client, payload):
    assert client.post("/api/tasks", json=payload).status_code == 422


def test_api_authentication_and_config_secret_redaction(service, tmp_path):
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path,
        app_access_token="session-access-secret",
        llm_api_key="model-key-secret",
        llm_base_url="https://provider.example/secret-route",
        github_token="github-key-secret",
    )
    with TestClient(create_app(settings, service)) as client:
        assert client.get("/").status_code == 200
        assert client.get("/health").status_code == 200
        for path in [
            "/api/config", "/api/tasks", "/api/tasks/unknown", "/api/tasks/unknown/report"
        ]:
            assert client.get(path).status_code == 401
        for path in ["/api/tasks", "/api/tasks/unknown/pause", "/api/tasks/unknown/resume"]:
            assert client.post(path, json={}).status_code == 401
        assert client.get(
            "/api/config", headers={"Authorization": "Bearer wrong"}
        ).status_code == 401
        response = client.get(
            "/api/config", headers={"Authorization": "bearer session-access-secret"}
        )
        assert response.status_code == 200
        assert response.json()["mode"] == "mock"
        assert response.json()["access_token_required"] is True
        assert "max_iterations" in response.json()["default_budget"]
        for secret in [
            "session-access-secret", "model-key-secret", "github-key-secret", "secret-route"
        ]:
            assert secret not in response.text


def test_unexpected_exception_does_not_leak_secrets(client, service):
    def fail(_request):
        raise RuntimeError("upstream failed with secret-api-key")

    service.start = fail
    response = client.post(
        "/api/tasks",
        json={"repo_url": "https://github.com/example/project", "question": "分析入口"},
    )
    assert response.status_code == 500
    assert "secret-api-key" not in response.text


@pytest.mark.parametrize(
    ("code", "expected"),
    [("invalid_url", 400), ("invalid_ref", 400), ("not_found", 404),
     ("rate_limit", 429), ("network_error", 502)],
)
def test_github_error_mapping(client, service, code, expected):
    from repo_research.github import GitHubError

    def fail(_request):
        raise GitHubError(code, "safe GitHub failure", retry_after=12.2)

    service.start = fail
    response = client.post(
        "/api/tasks",
        json={"repo_url": "https://github.com/example/project", "question": "分析入口"},
    )
    assert response.status_code == expected
    if code == "rate_limit":
        assert response.headers["retry-after"] == "13"


@pytest.mark.browser
@pytest.mark.skipif(
    os.environ.get("RUN_BROWSER_TESTS") != "1", reason="Opt in with RUN_BROWSER_TESTS=1"
)
@pytest.mark.parametrize("width", [1440, 390])
def test_browser_responsive_task_flow_and_untrusted_content(service, tmp_path, width):
    """Local browser + stub service, not a live model/GitHub integration test."""
    import uvicorn
    from playwright.sync_api import expect, sync_playwright

    injected = '<img src=x onerror="window.xss=1">'
    task = service.start(
        ResearchRequest(
            repo_url="https://github.com/example/project", question="研究入口 " + injected
        )
    )
    service.tasks[task["id"]].update(
        status="completed",
        model_mode="mock",
        report={
            "title": "源码研究报告",
            "summary": injected,
            "claims": [{"section": "关键入口", "category": "fact", "text": injected,
                        "evidence_ids": ["E1", "E2", "E3"]}],
            "limitations": ["这是离线浏览器测试。"],
        },
        report_markdown="# 模拟报告\n" + injected,
        evidence=[
            {
                "id": "E1", "path": "src/main.py", "start_line": 1, "end_line": 1,
                "url": "javascript:window.xss=2", "excerpt": injected,
                "commit_sha": "a" * 40, "repository": "example/project", "verified": True,
            },
            {
                "id": "E2", "path": "src/main.py", "start_line": 2, "end_line": 2,
                "url": "https://github.com/example/project/blob/" + "a" * 40 + "/src/main.py#L2",
                "excerpt": "unverified", "commit_sha": "a" * 40,
                "repository": "example/project", "verified": False,
            },
            {
                "id": "E3", "path": "src/main.py", "start_line": 3, "end_line": 3,
                "url": "https://github.com/example/project/blob/" + "a" * 40 + "/src/main.py#L3",
                "excerpt": "verified", "commit_sha": "a" * 40,
                "repository": "example/project", "verified": True,
            },
        ],
    )
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    url = f"http://127.0.0.1:{listener.getsockname()[1]}"
    server = uvicorn.Server(uvicorn.Config(
        create_app(Settings(_env_file=None, data_dir=tmp_path), service), log_level="error"
    ))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.02)
        assert server.started, "Local browser test server did not start"
        assert httpx.get(url + "/health").status_code == 200
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=os.environ.get("CHROMIUM_EXECUTABLE_PATH")
            )
            page = browser.new_page(viewport={"width": width, "height": 1000})
            try:
                page.goto(url)
                expect(page.locator("#mode-badge")).to_contain_text("MOCK")
                page.locator(".history-item").first.click()
                expect(page.locator("#report-content")).to_contain_text(injected)
                expect(page.locator("#report-content")).to_contain_text("模型摘要（未逐项验证）")
                expect(page.locator("#task-status")).to_have_text("已完成")
                page.locator(".evidence summary").first.click()
                expect(page.locator(".evidence pre").first).to_have_text(injected)
                assert page.locator("#report-content img").count() == 0
                assert page.locator("a[href^='javascript:']").count() == 0
                assert page.locator(".evidence a").count() == 1
                assert page.locator(".evidence a").first.get_attribute("href").endswith("#L3")
                assert page.evaluate("window.xss || 0") == 0
                assert page.evaluate(
                    "document.documentElement.scrollWidth <= window.innerWidth"
                )
                page.locator("#reuse-button").click()
                page.locator("#question").fill("分析 Python 模块入口与依赖")
                page.locator("#start-button").click()
                expect(page.locator("#task-status")).to_have_text("研究中")
                page.locator("#pause-button").click()
                expect(page.locator("#task-status")).to_have_text("已暂停")
                page.locator("#resume-button").click()
                expect(page.locator("#task-status")).to_have_text("研究中")
                page.screenshot(path=str(tmp_path / f"repolens-{width}.png"), full_page=True)
            finally:
                browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
