"""Opt-in Chromium + real service + real GitHub validation; no real LLM calls."""

from __future__ import annotations

import argparse
import json
import os
import socket
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import uvicorn
from playwright.sync_api import expect, sync_playwright

from repo_research.config import Settings
from repo_research.service import ResearchService
from repo_research.web import create_app

REPOSITORY_URL = "https://github.com/pallets/itsdangerous"
QUESTION = (
    "研究签名和验证的实现：定位 Signer.sign、Signer.unsign 与 Serializer.loads "
    "的关键代码入口和静态调用关系。"
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data/ui-validation"))
    parser.add_argument("--output-dir", type=Path, default=Path("docs/validation"))
    parser.add_argument("--screenshots", type=Path, default=Path("docs/screenshots"))
    parser.add_argument("--chromium", default=os.environ.get("CHROMIUM_EXECUTABLE_PATH"))
    args = parser.parse_args()
    if os.environ.get("RUN_LIVE_TESTS") != "1" or os.environ.get("RUN_BROWSER_TESTS") != "1":
        parser.error("Set RUN_LIVE_TESTS=1 and RUN_BROWSER_TESTS=1 for this external check.")
    args.screenshots.mkdir(parents=True, exist_ok=True)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    # Ignore .env live mode and access tokens; this temporary server binds localhost only.
    settings = Settings(
        _env_file=None, model_mode="mock", app_access_token="", data_dir=args.data_dir,
        load_saved_model=False,
    )
    service = ResearchService(settings, max_workers=1)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    url = f"http://127.0.0.1:{listener.getsockname()[1]}"
    server = uvicorn.Server(uvicorn.Config(create_app(settings, service), log_level="warning"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    errors: list[str] = []
    try:
        deadline = time.monotonic() + 10
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.02)
        assert server.started, "Temporary service did not start"
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=args.chromium)
            page = browser.new_page(viewport={"width": 1440, "height": 1050})
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(url)
            expect(page.locator("#mode-badge")).to_contain_text("MOCK")
            page.locator("#repo-url").fill(REPOSITORY_URL)
            page.locator("#question").fill(QUESTION)
            page.locator("#depth").select_option("standard")
            page.locator("#start-button").click()
            expect(page.locator("#task-panel")).to_be_visible()
            expect(page.locator("#task-status")).to_have_text("已完成", timeout=180000)
            expect(page.locator("#report-content")).to_contain_text("模拟模型结果")
            expect(page.locator("#report-content")).to_contain_text("模型摘要（未逐项验证）")
            assert page.locator(".evidence a").count() > 0
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            task = service.list_tasks()[0]
            assert task["status"] == "completed"
            assert task["model_mode"] == "mock" and task["usage"]["llm_calls"] == 0
            assert all(item["verified"] for item in task["evidence"])
            assert all(
                task["repository"]["commit_sha"] in item["url"] for item in task["evidence"]
            )
            page.locator(".evidence summary").first.focus()
            page.locator(".evidence summary").first.press("Enter")
            expect(page.locator(".evidence pre").first).to_be_visible()
            page.screenshot(path=str(args.screenshots / "desktop.png"), full_page=True)
            page.set_viewport_size({"width": 390, "height": 1000})
            page.locator("#task-panel").scroll_into_view_if_needed()
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            page.screenshot(path=str(args.screenshots / "mobile.png"), full_page=True)
            assert not errors, errors
            record = {
                "recorded_at_utc": datetime.now(UTC).isoformat(), "status": "PASS",
                "validation": "Chromium + actual FastAPI service + real GitHub REST + Mock",
                "repository": task["repository"]["full_name"],
                "commit_sha": task["repository"]["commit_sha"], "question": QUESTION,
                "viewports": [1440, 390], "horizontal_overflow": False,
                "javascript_errors": errors, "model_mode": task["model_mode"],
                "usage": task["usage"], "verified_evidence": len(task["evidence"]),
                "stop_reason": task["stop_reason"], "real_llm_called": False,
            }
            (args.output_dir / "ui-validation.json").write_text(
                json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
            )
            (args.output_dir / "itsdangerous-report.md").write_text(
                task["report_markdown"], encoding="utf-8",
            )
            print(json.dumps(record, ensure_ascii=False))
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=30)
        listener.close()
        if thread.is_alive():
            raise RuntimeError("Temporary service did not stop")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
