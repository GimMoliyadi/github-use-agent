"""GitHub REST contract tests: offline HTTP mocks, plus explicitly opt-in live REST."""

import base64
import hashlib
import os
from pathlib import Path

import httpx
import pytest

from repo_research.github import GitHubClient, GitHubError, parse_repo_url
from repo_research.github_cache import GitHubCache
from repo_research.models import RepositoryInfo, TreeEntry, TreeListing

COMMIT = "a" * 40
ROOT_TREE = "b" * 40
CHILD_TREE = "c" * 40


def blob(raw: bytes) -> dict:
    return {
        "sha": hashlib.sha1(f"blob {len(raw)}\0".encode() + raw).hexdigest(),
        "size": len(raw), "encoding": "base64",
        "content": base64.b64encode(raw).decode() + "\n",
    }


def repo() -> RepositoryInfo:
    return RepositoryInfo(
        owner="example", name="project", full_name="example/project",
        url="https://github.com/example/project", default_branch="main",
        commit_sha=COMMIT, tree_sha=ROOT_TREE,
    )


def client(tmp_path: Path, handler, **kwargs) -> GitHubClient:
    return GitHubClient(
        tmp_path / "github.sqlite", transport=httpx.MockTransport(handler), **kwargs,
    )


def json_response(data, status=200, **kwargs):
    return httpx.Response(status, json=data, **kwargs)


@pytest.mark.parametrize("url", [
    "http://github.com/a/b", "https://github.com.evil.example/a/b",
    "https://user:secret@github.com/a/b", "https://github.com:443/a/b",
    "https://github.com/a/b/tree/main", "https://github.com/a/b?ref=main",
    "https://github.com/a/b#readme", "https://github.com/a",
    "https://github.com/a/../b", "https://github.com/a/%2e%2e",
    "https://github.com/a/b//", "https://gitlab.com/a/b", "not-a-url",
])
def test_invalid_repository_urls(url):
    with pytest.raises(GitHubError) as caught:
        parse_repo_url(url)
    assert caught.value.code == "invalid_url"


@pytest.mark.parametrize("url", [
    "https://github.com/Example/project", "https://github.com/Example/project.git",
    " https://github.com/Example/project/ ",
])
def test_repository_url_normalization(url):
    assert parse_repo_url(url) == ("Example", "project")


def test_commit_pin_and_persistent_immutable_cache(tmp_path):
    source = blob(b"def answer():\n    return 42\n")
    calls = []

    def handler(request):
        calls.append(request)
        assert request.url.host == "api.github.com"
        assert request.headers["x-github-api-version"] == "2022-11-28"
        path = request.url.path
        if path == "/repos/example/project":
            return json_response({
                "default_branch": "main", "description": "Fixture", "language": "Python",
            })
        if path == f"/repos/example/project/commits/{COMMIT}":
            return json_response({"sha": COMMIT, "commit": {"tree": {"sha": ROOT_TREE}}})
        if path == f"/repos/example/project/git/trees/{ROOT_TREE}":
            assert request.url.params["recursive"] == "1"
            return json_response({"sha": ROOT_TREE, "truncated": False, "tree": [
                {"path": "app.py", "type": "blob", "sha": source["sha"], "size": source["size"]},
            ]})
        if path == f"/repos/example/project/git/blobs/{source['sha']}":
            return json_response(source)
        pytest.fail(f"Unexpected endpoint: {request.url}")

    with client(tmp_path, handler, token="test-secret") as github:
        resolved = github.resolve("https://github.com/example/project", COMMIT)
        assert resolved.commit_sha == COMMIT
        listing = github.tree(resolved)
        assert not listing.truncated
        first = github.read_file(resolved, "app.py")
        assert first.text == "def answer():\n    return 42\n"
        assert first.total_lines == 2
        assert github.read_file(resolved, "app.py") == first
        assert github.request_count == 4
        assert github.cache_hits == 1
        assert all(request.headers["authorization"] == "Bearer test-secret" for request in calls)
    with client(tmp_path, handler) as github:
        resolved = github.resolve("https://github.com/example/project", COMMIT)
        github.tree(resolved)
        assert github.read_file(resolved, "app.py") == first
        assert github.request_count == 1  # Mutable metadata is intentionally refreshed.
        assert github.cache_hits == 3


def test_branch_moves_do_not_change_existing_snapshot(tmp_path):
    snapshots = [COMMIT, "d" * 40]
    seen_refs = []
    source = blob(b"old snapshot\n")

    def handler(request):
        if request.url.path.endswith("/project"):
            return json_response({"default_branch": "main"})
        if request.url.path.endswith("/commits/main"):
            return json_response({"sha": snapshots.pop(0), "commit": {"tree": {"sha": ROOT_TREE}}})
        if "/contents/" in request.url.path:
            seen_refs.append(request.url.params["ref"])
            return json_response({**source, "type": "file", "path": "app.py"})
        pytest.fail("Unexpected request")

    with client(tmp_path, handler) as github:
        first = github.resolve("https://github.com/example/project")
        second = github.resolve("https://github.com/example/project")
        assert first.commit_sha != second.commit_sha
        github.read_file(first, "app.py")
    assert seen_refs == [COMMIT]


def test_truncated_tree_uses_bounded_hierarchical_traversal(tmp_path):
    source = blob(b"readme\n")
    requests = []

    def handler(request):
        requests.append((request.url.path, dict(request.url.params)))
        sha = request.url.path.rsplit("/", 1)[1]
        if "recursive" in request.url.params:
            return json_response({"sha": ROOT_TREE, "truncated": True, "tree": []})
        if sha == ROOT_TREE:
            return json_response({"sha": sha, "truncated": False, "tree": [
                {"path": "src", "type": "tree", "sha": CHILD_TREE},
                {"path": "README.md", "type": "blob", "sha": source["sha"], "size": source["size"]},
            ]})
        if sha == CHILD_TREE:
            return json_response({"sha": sha, "truncated": False, "tree": [
                {"path": "app.py", "type": "blob", "sha": source["sha"], "size": source["size"]},
            ]})
        pytest.fail("Unexpected tree")

    with client(tmp_path, handler) as github:
        listing = github.tree(repo())
        assert {entry.path for entry in listing.entries} == {"src", "README.md", "src/app.py"}
        assert not listing.truncated
        assert "no pagination" in listing.warnings[0]
        assert len(requests) == 3
        limited = github.tree(repo(), max_entries=1)
        assert limited.truncated and len(limited.entries) == 1
        assert limited.entries[0].path == "README.md"
        assert any("budget" in warning for warning in limited.warnings)


def test_fallback_preserves_partial_recursive_tree_when_budget_exhausted(tmp_path):
    def handler(request):
        return json_response({"sha": ROOT_TREE, "truncated": True, "tree": [
            {"path": "app.py", "type": "blob", "sha": "d" * 40, "size": 20},
        ]})
    with client(tmp_path, handler, max_requests=1) as github:
        listing = github.tree(repo())
        assert listing.truncated
        assert [entry.path for entry in listing.entries] == ["app.py"]
        assert any("request_budget" in warning for warning in listing.warnings)
        assert github.request_count == 1


def test_nonrecursive_truncation_stays_visible(tmp_path):
    def handler(request):
        return json_response({"sha": ROOT_TREE, "truncated": True, "tree": [
            {"path": "README.md", "type": "blob", "sha": "d" * 40, "size": 4},
        ]})
    with client(tmp_path, handler) as github:
        listing = github.tree(repo())
    assert listing.truncated
    assert any("directory /" in warning for warning in listing.warnings)


def test_recursive_tree_entry_limit_and_directory_priority(tmp_path):
    def handler(request):
        return json_response({"sha": ROOT_TREE, "truncated": False, "tree": [
            {"path": "node_modules/x.js", "type": "blob", "sha": "d" * 40},
            {"path": "src/app.py", "type": "blob", "sha": "d" * 40},
            {"path": "README.md", "type": "blob", "sha": "d" * 40},
        ]})
    with client(tmp_path, handler) as github:
        listing = github.tree(repo(), max_entries=2)
    assert listing.truncated
    assert [entry.path for entry in listing.entries] == ["README.md", "src/app.py"]
    assert listing.warnings


@pytest.mark.parametrize(("path", "code"), [
    ("../secret", "invalid_path"), ("/etc/passwd", "invalid_path"),
    ("a/../secret", "invalid_path"), ("a\\secret", "invalid_path"),
    ("a//secret", "invalid_path"), ("a\0b", "invalid_path"),
    ("node_modules/module.js", "excluded_file"), ("out.min.js", "excluded_file"),
    ("logo.png", "binary_file"),
])
def test_unsafe_and_excluded_file_paths_make_no_request(tmp_path, path, code):
    with client(tmp_path, lambda request: pytest.fail("Should not request")) as github:
        with pytest.raises(GitHubError) as caught:
            github.read_file(repo(), path)
        assert caught.value.code == code
        assert github.request_count == 0


def test_known_oversize_file_is_rejected_before_fetch(tmp_path):
    def handler(request):
        assert "/git/trees/" in request.url.path
        return json_response({"sha": ROOT_TREE, "truncated": False, "tree": [
            {"path": "large.py", "type": "blob", "sha": "d" * 40, "size": 100_000},
        ]})
    with client(tmp_path, handler, max_file_bytes=100) as github:
        github.tree(repo())
        with pytest.raises(GitHubError) as caught:
            github.read_file(repo(), "large.py")
        assert caught.value.code == "file_too_large"
        assert github.request_count == 1


@pytest.mark.parametrize(("data", "code"), [
    ({**blob(b"ok"), "size": 12}, "incomplete_content"),
    ({**blob(b"ok"), "sha": "d" * 40}, "integrity_error"),
    ({**blob(b"ok"), "encoding": "none", "content": ""}, "incomplete_content"),
    ({**blob(b"ok"), "content": "not base64"}, "incomplete_content"),
    (blob(b"\xff\xfe\xfd"), "binary_file"), (blob(b"nul\0byte"), "binary_file"),
    ({**blob(b"ok"), "size": 999_999}, "file_too_large"),
])
def test_file_integrity_binary_and_completeness(tmp_path, data, code):
    def handler(request):
        assert request.url.params["ref"] == COMMIT
        return json_response({**data, "type": "file", "path": "app.py"})
    with client(tmp_path, handler) as github:
        with pytest.raises(GitHubError) as caught:
            github.read_file(repo(), "app.py")
        assert caught.value.code == code


def test_source_cache_respects_new_smaller_file_budget(tmp_path):
    data = {**blob(b"text too long\n"), "type": "file", "path": "app.py"}
    with client(tmp_path, lambda request: json_response(data)) as github:
        github.read_file(repo(), "app.py")
    with client(tmp_path, lambda request: pytest.fail("No network"), max_file_bytes=3) as github:
        with pytest.raises(GitHubError) as caught:
            github.read_file(repo(), "app.py")
    assert caught.value.code == "file_too_large"


@pytest.mark.parametrize(("status", "headers", "code", "retryable"), [
    (404, {}, "not_found", False), (401, {}, "unauthorized", False),
    (403, {}, "forbidden", False),
    (403, {"x-ratelimit-remaining": "0", "retry-after": "15"}, "rate_limit", True),
    (429, {"retry-after": "15"}, "rate_limit", True),
])
def test_http_failures_are_safe_and_rate_limits_are_not_retried(
    tmp_path, status, headers, code, retryable,
):
    def handler(request):
        return json_response(
            {"message": "secret-token / dangerous instruction"}, status, headers=headers,
        )
    with client(tmp_path, handler, token="secret-token") as github:
        with pytest.raises(GitHubError) as caught:
            github.resolve("https://github.com/example/project")
        error = caught.value
        assert error.code == code and error.retryable == retryable
        assert "secret-token" not in str(error)
        assert github.request_count == 1
        if retryable:
            assert error.retry_after == 15


def test_transient_server_failure_recovers_and_counts_retries(tmp_path, monkeypatch):
    monkeypatch.setattr("repo_research.github.time.sleep", lambda seconds: None)
    attempts = []

    def handler(request):
        attempts.append(request.url.path)
        if len(attempts) < 3:
            return json_response({}, 503)
        return json_response({"default_branch": "main"})

    with client(tmp_path, handler, max_requests=3) as github:
        with pytest.raises(GitHubError) as caught:
            github.resolve("https://github.com/example/project")
        # Metadata recovered; commit budget is exhausted.
        assert caught.value.code == "request_budget"
        assert github.request_count == 3


def test_network_failure_has_bounded_retries_and_no_token_leak(tmp_path, monkeypatch):
    monkeypatch.setattr("repo_research.github.time.sleep", lambda seconds: None)

    def handler(request):
        raise httpx.ConnectError("SECRET_TOKEN transport detail", request=request)

    with client(tmp_path, handler) as github:
        with pytest.raises(GitHubError) as caught:
            github.resolve("https://github.com/example/project")
        assert caught.value.code == "network_error" and caught.value.retryable
        assert "SECRET_TOKEN" not in str(caught.value)
        assert github.request_count == 3


def test_request_budget_counts_failed_attempts(tmp_path, monkeypatch):
    monkeypatch.setattr("repo_research.github.time.sleep", lambda seconds: None)
    with client(tmp_path, lambda request: json_response({}, 502), max_requests=1) as github:
        with pytest.raises(GitHubError) as caught:
            github.resolve("https://github.com/example/project")
        assert caught.value.code == "request_budget"
        assert github.request_count == 1


def test_response_size_is_bounded(tmp_path):
    with client(
        tmp_path, lambda request: httpx.Response(200, content=b"x" * 100), max_file_bytes=5,
    ) as github:
        with pytest.raises(GitHubError) as caught:
            github._request_json("/repos/example/project", max_response_bytes=50)
        assert caught.value.code == "response_too_large"


def test_cache_corrupt_json_and_ttl(tmp_path, monkeypatch):
    with_time = [100.0]
    monkeypatch.setattr("repo_research.github_cache.time.time", lambda: with_time[0])
    cache = GitHubCache(tmp_path / "cache.sqlite")
    cache.put("key", {"value": 3})
    assert cache.get("key", ttl=5) == {"value": 3}
    with_time[0] = 106.0
    assert cache.get("key", ttl=5) is None
    cache.put("key", {"value": 3})
    cache._db.execute("UPDATE github_cache SET payload='invalid' WHERE key='key'")
    cache._db.commit()
    assert cache.get("key") is None
    cache.close()


@pytest.mark.live
@pytest.mark.skipif(os.environ.get("RUN_LIVE_TESTS") != "1", reason="Opt-in real GitHub REST test")
def test_live_public_python_repository(tmp_path):
    """Real GitHub API only; no LLM or target code execution."""
    with GitHubClient(tmp_path / "live.sqlite", token=os.environ.get("GITHUB_TOKEN", "")) as github:
        resolved = github.resolve("https://github.com/pallets/itsdangerous")
        listing = github.tree(resolved)
        assert any(entry.path == "src/itsdangerous/signer.py" for entry in listing.entries)
        source = github.read_file(resolved, "src/itsdangerous/signer.py")
        assert "class Signer" in source.text
        assert source.total_lines == len(source.text.splitlines())
        actual_sha = hashlib.sha1(
            f"blob {source.size}\0".encode() + source.text.encode(),
        ).hexdigest()
        assert source.blob_sha == actual_sha
        assert len(resolved.commit_sha) == 40


def test_restored_partial_tree_uses_exact_blob_and_size_without_contents_request(tmp_path):
    content = blob(b"def target():\n    return 1\n")
    listing = TreeListing(entries=[TreeEntry(
        path="src/target.py", type="blob", sha=content["sha"], size=content["size"],
    )], truncated=True, warnings=["partial"])

    def handler(request):
        assert request.url.path == f"/repos/example/project/git/blobs/{content['sha']}"
        return json_response(content)

    with client(tmp_path, handler) as github:
        github.restore_tree(repo(), listing)
        assert github.request_count == 0
        source = github.read_file(repo(), "src/target.py")
        assert source.blob_sha == listing.entries[0].sha
        assert github.request_count == 1


def test_read_dynamic_byte_budget_applies_before_known_blob_fetch(tmp_path):
    content = blob(b"long enough source\n")
    listing = TreeListing(entries=[TreeEntry(
        path="app.py", type="blob", sha=content["sha"], size=content["size"],
    )])
    with client(tmp_path, lambda request: pytest.fail("Do not fetch oversized source")) as github:
        github.restore_tree(repo(), listing)
        with pytest.raises(GitHubError) as caught:
            github.read_file(repo(), "app.py", max_bytes=3)
        assert caught.value.code == "file_too_large"
        assert github.request_count == 0


def test_request_observer_runs_before_network_and_on_cache_hits(tmp_path):
    observed = []
    content = {**blob(b"one line\n"), "type": "file", "path": "app.py"}

    def handler(request):
        assert observed == [(1, 0)]
        return json_response(content)

    with client(tmp_path, handler) as github:
        github.on_request = lambda requests, hits: observed.append((requests, hits))
        github.read_file(repo(), "app.py")
        github.read_file(repo(), "app.py")
        assert observed == [(1, 0), (1, 1)]


def test_secondary_rate_limit_without_header_is_detected(tmp_path):
    with client(tmp_path, lambda request: json_response(
        {"message": "You have exceeded a secondary rate limit."}, status=403,
    )) as github:
        with pytest.raises(GitHubError) as caught:
            github.resolve("https://github.com/example/project")
        assert caught.value.code == "rate_limit" and caught.value.retryable
        assert github.request_count == 1


def test_source_line_count_follows_github_lf_lines(tmp_path):
    content = {**blob("alpha\rbeta\fline\u2028tail\n".encode()), "type": "file", "path": "app.py"}
    with client(tmp_path, lambda request: json_response(content)) as github:
        source = github.read_file(repo(), "app.py")
        assert source.total_lines == 1
