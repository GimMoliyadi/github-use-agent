"""Opt-in live GitHub validation. Research decisions use Mock; no LLM is called.

Run from the repository root:
  RUN_LIVE_TESTS=1 uv run python examples/check_live.py --output-dir data/live-validation
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from repo_research.agent import ResearchRunner, effective_budget
from repo_research.config import Settings
from repo_research.github import GitHubClient, GitHubError, parse_repo_url
from repo_research.models import RepositoryInfo, ResearchRequest
from repo_research.tools import evidence_url, source_lines

REPOSITORY_URL = "https://github.com/pypa/sampleproject"
CASES = {
    "overview": "研究 Python 项目的架构、技术栈、打包配置及代码入口。",
    "targeted": "研究 src/sample/__init__.py 中 sample.main 函数的定义、入口配置和静态调用位置。",
}


def live_settings(data_dir: Path) -> Settings:
    """Force Mock even if the user's .env enables a billable real model."""
    return Settings(_env_file=None, model_mode="mock", data_dir=data_dir)


def validate_live_evidence(state: dict, data_dir: Path, settings: Settings) -> int:
    """Independently reread complete files from GitHub at the pinned commit.

    The verification uses a separate cache database from the research task. It
    checks every citation, not only the first/most convenient evidence item.
    """
    repository = RepositoryInfo.model_validate(state["repository"])
    assert len(repository.commit_sha) == 40
    assert state["evidence"], "Research produced no code evidence"
    files = {}
    with GitHubClient(
        data_dir / "independent-verification.sqlite",
        token=settings.github_token.get_secret_value(),
        timeout=settings.github_timeout, max_requests=100,
    ) as github:
        for item in state["evidence"]:
            assert item["verified"] is True
            assert item["repository"] == repository.full_name
            assert item["commit_sha"] == repository.commit_sha
            path = item["path"]
            if path not in files:
                files[path] = github.read_file(repository, path)
            source = files[path]
            lines = source_lines(source.text)
            assert source.total_lines == len(lines)
            start, end = item["start_line"], item["end_line"]
            assert 1 <= start <= end <= len(lines)
            assert item["excerpt"] == "\n".join(lines[start - 1:end])
            assert item["blob_sha"] == source.blob_sha
            assert item["url"] == evidence_url(repository, path, start, end)
            assert f"/blob/{repository.commit_sha}/" in item["url"]
    return len(state["evidence"])


def run_case(case: str, output_dir: Path) -> tuple[dict, dict]:
    data_dir = output_dir / case
    settings = live_settings(data_dir)
    request = ResearchRequest(repo_url=REPOSITORY_URL, question=CASES[case], depth="standard")
    runner = ResearchRunner(settings, data_dir / "checkpoints.sqlite3")
    state = runner.run(f"live-{case}", request, effective_budget(request))
    if state.get("status") != "completed":
        raise RuntimeError(f"Live GitHub case {case} stopped with {state.get('status')}")
    assert "Mock" in state["report_markdown"] or "MOCK" in state["report_markdown"]
    evidence_count = validate_live_evidence(state, data_dir, settings)
    if case == "targeted":
        assert any(
            item["path"] == "src/sample/__init__.py" and "def main" in item["excerpt"]
            for item in state["evidence"]
        ), "Directed research did not cite the target main function"
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / f"{case}-report.md").write_text(state["report_markdown"], encoding="utf-8")
    record = {
        "case": case, "validation": "real GitHub REST + deterministic Mock model",
        "repository": state["repository"]["full_name"],
        "commit_sha": state["repository"]["commit_sha"],
        "status": state["status"], "question": request.question,
        "verified_citations": evidence_count,
        "citation_check": (
            "all paths, pinned commit, complete bytes, blob SHA, exact LF lines, permalink"
        ),
        "usage": state["usage"], "stop_reason": state["stop_reason"],
        "real_llm_called": False,
    }
    return state, record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("data/live-validation"))
    args = parser.parse_args()
    if os.environ.get("RUN_LIVE_TESTS") != "1":
        parser.error("Set RUN_LIVE_TESTS=1 to authorize this explicit real GitHub API check.")
    records = []
    for case in CASES:
        _state, record = run_case(case, args.output_dir)
        records.append(record)
        print(f"{case}: {record['status']}; {record['verified_citations']} verified citations")
    try:
        parse_repo_url("https://github.com/pypa/sampleproject/tree/main")
    except GitHubError as error:
        assert error.code == "invalid_url"
        records.append({"case": "invalid_url", "status": "passed", "external_api_called": False})
    else:
        raise AssertionError("Non-root URL was unexpectedly accepted")
    record = {
        "recorded_at_utc": datetime.now(UTC).isoformat(),
        "scope": "Real GitHub source access; research reasoning/report text simulated by Mock",
        "results": records,
    }
    (args.output_dir / "results.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    print(f"Saved actual validation records to {args.output_dir / 'results.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
