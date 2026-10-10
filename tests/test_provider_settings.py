"""UI model settings tests: HTTP and model inference are strictly simulated."""

import json
import os
import socket
import threading
import time
from concurrent.futures import Future
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from langchain_openai import ChatOpenAI

from repo_research.config import Settings
from repo_research.provider_settings import ModelConfiguration, load_model_settings
from repo_research.service import ResearchService
from repo_research.web import create_app

KEY = "offline-ui-test-key"
PAYLOAD = {
    "mode": "live", "base_url": "https://relay.example/v1", "api_key": KEY,
    "model": "test-model", "output_mode": "auto",
}


def test_save_apply_keep_key_and_restart_without_secret_response(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path)
    with TestClient(create_app(settings)) as client:
        response = client.put("/api/model", json=PAYLOAD)
        assert response.status_code == 200
        assert KEY not in response.text and "api_key" not in response.json()
        assert response.json()["key_configured"]
        assert client.get("/api/config").json()["mode"] == "live"
        assert client.get("/api/config").json()["model"] == "test-model"
        assert KEY not in client.get("/api/model").text
        changed = client.put("/api/model", json={**PAYLOAD, "api_key": "", "model": "new-model"})
        assert changed.status_code == 200
        assert KEY not in client.get("/api/tasks").text
    path = tmp_path / "model-settings.json"
    assert json.loads(path.read_text())["api_key"] == KEY
    assert path.stat().st_mode & 0o777 == 0o600
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/model").json()["model"] == "new-model"
        assert client.get("/api/config").json()["mode"] == "live"
        assert client.app.state.research_service.settings.llm_api_key.get_secret_value() == KEY
    assert load_model_settings(
        settings.model_copy(update={"load_saved_model": False})
    ).model_mode == "mock"


def test_failed_save_does_not_apply_and_corrupt_file_does_not_expose_key(tmp_path, monkeypatch):
    def fail_save(_settings):
        raise OSError("simulated write failure")

    monkeypatch.setattr("repo_research.service.save_model_settings", fail_save)
    with TestClient(create_app(Settings(_env_file=None, data_dir=tmp_path))) as client:
        response = client.put("/api/model", json=PAYLOAD)
        assert response.status_code == 500 and KEY not in response.text
        assert client.get("/api/config").json()["mode"] == "mock"
    (tmp_path / "model-settings.json").write_text(KEY)
    with pytest.raises(ValueError) as error:
        load_model_settings(Settings(_env_file=None, data_dir=tmp_path))
    assert KEY not in str(error.value)


def test_explicit_mock_override_and_configuration_fingerprint_for_new_tasks(tmp_path):
    from repo_research.models import ResearchRequest
    from repo_research.service import model_fingerprint

    service = ResearchService(Settings(_env_file=None, data_dir=tmp_path))
    try:
        service.configure_model(ModelConfiguration(**PAYLOAD))
        # Isolate scheduling: verify task metadata and runner configuration before any HTTP.
        service._submit = lambda task, resume: None
        task = service.start(ResearchRequest(
            repo_url="https://github.com/example/project", question="研究代码入口",
        ))
        assert task["model_mode"] == "live"
        assert task["model_fingerprint"] == model_fingerprint(service.settings)
        runner = service._create_runner(threading.Event())
        assert runner.settings.llm_api_key.get_secret_value() == KEY
        assert KEY not in json.dumps(task)
    finally:
        service.close()
    with TestClient(create_app(Settings(
        _env_file=None, data_dir=tmp_path, load_saved_model=False,
    ))) as client:
        assert client.get("/api/config").json()["mode"] == "mock"


def test_old_key_cannot_be_sent_to_changed_endpoint(tmp_path):
    with TestClient(create_app(Settings(_env_file=None, data_dir=tmp_path))) as client:
        assert client.put("/api/model", json=PAYLOAD).status_code == 200
        response = client.put("/api/model", json={
            **PAYLOAD, "base_url": "https://another.example/v1", "api_key": "",
        })
        assert response.status_code == 400 and KEY not in response.text
        assert client.get("/api/model").json()["base_url"] == PAYLOAD["base_url"]


@pytest.mark.parametrize("change", [
    {"base_url": "file:///etc/passwd"}, {"base_url": "https://user:pass@relay.example"},
    {"base_url": "https://relay.example/v1?api_key=" + KEY},
    {"api_key": {"secret": KEY}}, {"mode": "invalid", "api_key": KEY},
])
def test_invalid_configuration_never_echoes_credentials(tmp_path, change):
    with TestClient(create_app(Settings(_env_file=None, data_dir=tmp_path))) as client:
        response = client.put("/api/model", json={**PAYLOAD, **change})
        assert response.status_code == 422
        assert KEY not in response.text and "input" not in response.text
        assert not (tmp_path / "model-settings.json").exists()


def test_settings_endpoints_require_application_token(tmp_path):
    with TestClient(create_app(Settings(
        _env_file=None, data_dir=tmp_path, app_access_token="test-access",
    ))) as client:
        assert client.get("/api/model").status_code == 401
        assert client.put("/api/model", json=PAYLOAD).status_code == 401
        assert client.post("/api/model/probe", json=PAYLOAD).status_code == 401
        response = client.put(
            "/api/model", json=PAYLOAD, headers={"Authorization": "Bearer test-access"},
        )
        assert response.status_code == 200 and KEY not in response.text


def test_configuration_change_rejected_while_worker_active(tmp_path):
    service = ResearchService(Settings(_env_file=None, data_dir=tmp_path))
    try:
        service._jobs["test-active"] = (threading.Event(), Future())
        with pytest.raises(ValueError, match="暂停"):
            service.configure_model(ModelConfiguration(**PAYLOAD))
        assert service.settings.model_mode == "mock"
        assert not (tmp_path / "model-settings.json").exists()
    finally:
        service._jobs.clear()
        service.close()


def install_mock_relay(monkeypatch, unsupported=False, unauthorized=False):
    """Actual ChatOpenAI/OpenAI SDK, with every request intercepted locally."""
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        body = requests[-1]
        assert request.url == "https://relay.example/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer " + KEY
        assert body["model"] == "test-model"
        if unauthorized:
            return httpx.Response(401, json={"error": {"message": KEY}})
        if unsupported and (body.get("tools") or body.get("response_format")):
            feature = "tools" if body.get("tools") else "json_schema"
            return httpx.Response(400, json={"error": {"message": feature + " unsupported"}})
        message = {"role": "assistant", "content": "capability-ok"}
        reason = "stop"
        if body.get("tools"):
            name = body["tools"][0]["function"]["name"]
            message.update(content=None, tool_calls=[{
                "id": "call-1", "type": "function", "function": {
                    "name": name, "arguments": '{"echo":"capability-ok"}',
                },
            }])
            reason = "tool_calls"
        elif body.get("response_format"):
            message["content"] = '{"echo":"capability-ok"}'
        return httpx.Response(200, json={
            "id": "chatcmpl-ui", "object": "chat.completion", "created": 1,
            "model": "test-model", "choices": [{
                "index": 0, "message": message, "finish_reason": reason,
            }], "usage": {"prompt_tokens": 7, "completion_tokens": 4, "total_tokens": 11},
        })

    transport_client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(
        "repo_research.llm.ChatOpenAI",
        lambda **kwargs: ChatOpenAI(**kwargs, http_client=transport_client),
    )
    return requests


@pytest.mark.parametrize("unsupported", [False, True])
def test_probe_real_adapter_with_mock_transport_does_not_save(tmp_path, monkeypatch, unsupported):
    requests = install_mock_relay(monkeypatch, unsupported=unsupported)
    with TestClient(create_app(Settings(_env_file=None, data_dir=tmp_path))) as client:
        response = client.post("/api/model/probe", json=PAYLOAD)
        assert response.status_code == 200, response.text
        capabilities = response.json()["capabilities"]
        assert capabilities["chat"] and capabilities["tested"]
        assert capabilities["tool_calling"] is not unsupported
        assert capabilities["structured_output"] is not unsupported
        assert capabilities["selected_output"] == ("json_text" if unsupported else "tools")
        assert len(requests) == response.json()["usage"]["llm_calls"] == 3
        assert KEY not in response.text
        assert client.get("/api/config").json()["mode"] == "mock"
        assert not (tmp_path / "model-settings.json").exists()


def test_probe_error_is_sanitized_and_missing_fields_fail_without_http(tmp_path, monkeypatch):
    requests = install_mock_relay(monkeypatch, unauthorized=True)
    with TestClient(create_app(Settings(_env_file=None, data_dir=tmp_path))) as client:
        missing = client.post("/api/model/probe", json={**PAYLOAD, "api_key": ""})
        assert missing.status_code == 400 and not requests
        response = client.post("/api/model/probe", json=PAYLOAD)
        assert response.status_code == 502 and KEY not in response.text
        assert "authentication" in response.text


@pytest.mark.browser
@pytest.mark.skipif(os.environ.get("RUN_BROWSER_TESTS") != "1", reason="Browser opt-in")
@pytest.mark.parametrize("width", [1440, 390])
def test_browser_configure_probe_save_and_no_key_storage(tmp_path, monkeypatch, width):
    import uvicorn
    from playwright.sync_api import expect, sync_playwright

    requests = install_mock_relay(monkeypatch)
    server = uvicorn.Server(uvicorn.Config(
        create_app(Settings(_env_file=None, data_dir=tmp_path)), log_level="error",
    ))
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.02)
        assert server.started
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=os.environ.get("CHROMIUM_EXECUTABLE_PATH"),
            )
            page = browser.new_page(viewport={"width": width, "height": 1000})
            try:
                page.goto(f"http://127.0.0.1:{listener.getsockname()[1]}")
                page.locator("#model-toggle").click()
                expect(page.locator("#model-panel")).to_be_visible()
                expect(page.locator("#model-mode")).to_have_value("live")
                page.locator("#model-base-url").fill(PAYLOAD["base_url"])
                page.locator("#model-api-key").fill(KEY)
                page.locator("#model-name").fill(PAYLOAD["model"])
                page.locator("#model-probe").click()
                expect(page.locator("#model-result")).to_contain_text("聊天：通过", timeout=20000)
                assert len(requests) == 3
                page.locator("#model-save").click()
                expect(page.locator("#model-result")).to_contain_text("已保存并应用")
                expect(page.locator("#mode-badge")).to_contain_text("LIVE")
                expect(page.locator("#model-api-key")).to_have_value("")
                assert KEY not in page.evaluate("JSON.stringify(sessionStorage)")
                assert KEY not in page.evaluate("JSON.stringify(localStorage)")
                page.reload()
                page.locator("#model-toggle").click()
                expect(page.locator("#model-key-status")).to_contain_text("已保存密钥")
                expect(page.locator("#model-api-key")).to_have_value("")
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                if os.environ.get("MODEL_SCREENSHOT_DIR"):
                    folder = Path(os.environ["MODEL_SCREENSHOT_DIR"])
                    folder.mkdir(parents=True, exist_ok=True)
                    page.screenshot(
                        path=str(folder / f"model-settings-{width}.png"), full_page=True,
                    )
            finally:
                browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
