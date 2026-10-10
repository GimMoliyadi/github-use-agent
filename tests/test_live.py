"""Real official GitHub API integration; opt-in and no real LLM calls."""

import importlib.util
import os
from pathlib import Path

import pytest

from repo_research.github import GitHubError, parse_repo_url

_spec = importlib.util.spec_from_file_location(
    "check_live", Path(__file__).resolve().parents[1] / "examples" / "check_live.py",
)
assert _spec is not None and _spec.loader is not None
_check_live = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_check_live)
run_case = _check_live.run_case

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("RUN_LIVE_TESTS") != "1", reason="Set RUN_LIVE_TESTS=1 for real GitHub REST",
    ),
]


@pytest.mark.parametrize("case", ["overview", "targeted"])
def test_live_sampleproject_end_to_end_and_every_citation(tmp_path, case):
    state, record = run_case(case, tmp_path)
    assert state["status"] == "completed"
    assert record["verified_citations"] > 0
    assert record["real_llm_called"] is False
    assert state["usage"]["llm_calls"] == 0


def test_live_suite_invalid_url_has_no_external_request():
    """This case intentionally validates before any external GitHub request."""
    with pytest.raises(GitHubError) as caught:
        parse_repo_url("https://github.com/pypa/sampleproject/tree/main")
    assert caught.value.code == "invalid_url"
