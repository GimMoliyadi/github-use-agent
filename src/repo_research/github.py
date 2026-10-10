"""Read-only, budgeted access to public repositories through GitHub REST.

Every code read is pinned to a full commit SHA. Fetched content is data, never
instructions: this module never clones, imports, installs or executes target code.
"""

import base64
import binascii
import hashlib
import json
import re
import time
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from .github_cache import GitHubCache
from .models import RepositoryInfo, SourceFile, TreeEntry, TreeListing

SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")
OWNER_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
IGNORED_PARTS = frozenset(
    {".git", ".venv", "venv", "node_modules", "__pycache__", "dist", "build", ".next"}
)
BINARY_SUFFIXES = frozenset(
    {".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".zip", ".gz", ".bz2", ".xz",
     ".7z", ".tar", ".woff", ".woff2", ".ttf", ".mp3", ".mp4", ".webm", ".exe", ".dll",
     ".so", ".dylib", ".pyc", ".pyo", ".whl", ".sqlite", ".db", ".bin", ".class"}
)


class GitHubError(Exception):
    """Safe, actionable error. API bodies, tokens and transport URLs are omitted."""

    def __init__(
        self, code: str, message: str, *, retryable: bool = False,
        retry_after: float | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.retry_after = retry_after


def parse_repo_url(repo_url: str) -> tuple[str, str]:
    """Accept only a repository root; branch/commit selection uses a separate ref."""
    try:
        url = urlsplit(repo_url.strip())
        valid_host = url.hostname == "github.com" and url.netloc.lower() == "github.com"
    except (ValueError, AttributeError):
        raise GitHubError("invalid_url", "Use https://github.com/OWNER/REPOSITORY.") from None
    if (
        url.scheme != "https" or not valid_host
        or url.username is not None or url.password is not None
        or url.query or url.fragment
    ):
        raise GitHubError(
            "invalid_url", "Use an HTTPS github.com repository root without credentials or query."
        )
    root_path = url.path[:-1] if url.path.endswith("/") else url.path
    parts = root_path.split("/")
    if len(parts) != 3 or parts[0] != "":
        raise GitHubError(
            "invalid_url", "Use the repository root URL; specify branches or commits in ref."
        )
    owner, name = parts[1], parts[2]
    if name.endswith(".git"):
        name = name[:-4]
    if not OWNER_RE.fullmatch(owner) or not NAME_RE.fullmatch(name) or name in {".", ".."}:
        raise GitHubError("invalid_url", "The repository owner or name is invalid.")
    return owner, name


def _sha(value: Any) -> str:
    if not isinstance(value, str) or not SHA_RE.fullmatch(value):
        raise GitHubError("invalid_response", "GitHub did not return a full Git object SHA.")
    return value.lower()


def _path(path: str) -> str:
    if (
        not isinstance(path, str) or not path or path.startswith("/") or "\\" in path
        or any(ord(char) < 32 for char in path)
        or any(part in {"", ".", ".."} for part in path.split("/"))
        or len(path) > 4096
    ):
        raise GitHubError("invalid_path", "Use a relative repository file path without traversal.")
    return path


def _size(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise GitHubError("invalid_response", "GitHub returned an invalid file size.")
    return value


def _priority(entry: TreeEntry) -> tuple:
    parts = PurePosixPath(entry.path).parts
    leaf = parts[-1].lower()
    important = leaf in {
        "readme.md", "readme.rst", "pyproject.toml", "package.json", "cargo.toml", "go.mod",
        "setup.py", "requirements.txt", "main.py", "__init__.py",
    }
    return (bool(set(parts) & IGNORED_PARTS), entry.path.count("/"), not important, entry.path)


class GitHubClient:
    def __init__(
        self, cache_path: Path, token: str = "", timeout: float = 20,
        max_requests: int = 50, max_file_bytes: int = 250_000,
        transport: httpx.BaseTransport | None = None,
    ):
        if max_requests < 0 or max_file_bytes < 1:
            raise ValueError(
                "Request and file budgets must be non-negative and positive respectively"
            )
        headers = {
            "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "repo-research-agent/0.1",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._client = httpx.Client(
            base_url="https://api.github.com", headers=headers, timeout=timeout,
            transport=transport, follow_redirects=False,
        )
        self._cache = GitHubCache(Path(cache_path))
        self.max_requests = max_requests
        self.max_file_bytes = max_file_bytes
        self.request_count = 0
        self.cache_hits = 0
        # Persist counters before each network attempt, including failed/retried ones.
        self.on_request: Callable[[int, int], None] | None = None
        self._blobs: dict[tuple[str, str, str], tuple[str, int]] = {}

    def close(self) -> None:
        self._client.close()
        self._cache.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    @staticmethod
    def _retry_after(response: httpx.Response) -> float | None:
        header = response.headers.get("retry-after")
        if header:
            try:
                return max(0, float(header))
            except ValueError:
                try:
                    seconds = (parsedate_to_datetime(header) - datetime.now(UTC)).total_seconds()
                    return max(0, seconds)
                except (TypeError, ValueError, OverflowError):
                    pass
        reset = response.headers.get("x-ratelimit-reset")
        if reset:
            try:
                return max(0, float(reset) - time.time())
            except ValueError:
                pass
        return None

    def _request_json(
        self, endpoint: str, *, params: dict | None = None, cache_key: str | None = None,
        max_response_bytes: int = 20_000_000,
    ) -> Any:
        if cache_key:
            cached = self._cache.get(cache_key)
            if cached is not None:
                self.cache_hits += 1
                if self.on_request is not None:
                    self.on_request(self.request_count, self.cache_hits)
                return cached
        for attempt in range(3):
            if self.request_count >= self.max_requests:
                raise GitHubError("request_budget", "GitHub API request budget exhausted.")
            self.request_count += 1
            if self.on_request is not None:
                self.on_request(self.request_count, self.cache_hits)
            try:
                with self._client.stream("GET", endpoint, params=params) as response:
                    status = response.status_code
                    if status in {403, 429}:
                        rate_limited = (
                            status == 429 or response.headers.get("x-ratelimit-remaining") == "0"
                            or "retry-after" in response.headers
                        )
                        if status == 403 and not rate_limited:
                            body = bytearray()
                            for chunk in response.iter_bytes():
                                body.extend(chunk[: max(0, 65_536 - len(body))])
                                if len(body) >= 65_536:
                                    break
                            message = body.decode("utf-8", errors="replace").lower()
                            rate_limited = "rate limit" in message or "abuse detection" in message
                        raise GitHubError(
                            "rate_limit" if rate_limited else "forbidden",
                            "GitHub API rate limit reached; resume after the cooldown."
                            if rate_limited else "GitHub API denied access to this resource.",
                            retryable=rate_limited, retry_after=self._retry_after(response),
                        )
                    if status == 404:
                        raise GitHubError(
                            "not_found",
                            "GitHub repository, ref or file was not found.",
                        )
                    if status == 401:
                        raise GitHubError(
                            "unauthorized",
                            "GitHub rejected the configured access token.",
                        )
                    if 500 <= status <= 599:
                        if attempt < 2:
                            time.sleep(0.1 * (2**attempt))
                            continue
                        raise GitHubError(
                            "upstream_error", "GitHub API is temporarily unavailable.",
                            retryable=True,
                        )
                    if status < 200 or status >= 300:
                        raise GitHubError("http_error", f"GitHub API returned HTTP {status}.")
                    raw = bytearray()
                    for chunk in response.iter_bytes():
                        raw.extend(chunk)
                        if len(raw) > max_response_bytes:
                            raise GitHubError(
                                "response_too_large",
                                "GitHub response exceeds the safe read budget.",
                            )
                    try:
                        result = json.loads(raw)
                    except (ValueError, UnicodeError):
                        raise GitHubError(
                            "invalid_response",
                            "GitHub returned invalid JSON.",
                        ) from None
                    if not isinstance(result, (dict, list)):
                        raise GitHubError(
                            "invalid_response",
                            "GitHub returned an invalid response.",
                        )
                    if cache_key:
                        self._cache.put(cache_key, result)
                    return result
            except httpx.TransportError:
                if attempt < 2:
                    time.sleep(0.1 * (2**attempt))
                    continue
                raise GitHubError(
                    "network_error", "Could not reach GitHub API after bounded retries.",
                    retryable=True,
                ) from None
        raise AssertionError("unreachable")

    def resolve(self, repo_url: str, ref: str | None = None) -> RepositoryInfo:
        owner, name = parse_repo_url(repo_url)
        if ref is not None and (
            not ref.strip() or len(ref) > 200 or any(ord(char) < 32 for char in ref)
        ):
            raise GitHubError("invalid_ref", "Ref must be a nonempty branch, tag or commit SHA.")
        endpoint = f"/repos/{owner}/{name}"
        metadata = self._request_json(endpoint, max_response_bytes=1_000_000)
        if not isinstance(metadata, dict) or not isinstance(metadata.get("default_branch"), str):
            raise GitHubError("invalid_response", "GitHub repository metadata is incomplete.")
        pinned_ref = ref or metadata["default_branch"]
        commit = self._request_json(
            f"{endpoint}/commits/{quote(pinned_ref, safe='')}",
            params={"per_page": "1"},  # Resolve SHA; changed-file pages are not research input.
            cache_key=(f"commit:{owner}/{name}:{ref.lower()}"
                       if ref and SHA_RE.fullmatch(ref) else None),
            max_response_bytes=5_000_000,
        )
        try:
            commit_sha = _sha(commit["sha"])
            tree_sha = _sha(commit["commit"]["tree"]["sha"])
        except (KeyError, TypeError):
            raise GitHubError("invalid_response", "GitHub commit metadata is incomplete.") from None
        if ref and SHA_RE.fullmatch(ref) and commit_sha != ref.lower():
            raise GitHubError(
                "integrity_error",
                "GitHub resolved a different commit than requested.",
            )
        return RepositoryInfo(
            owner=owner, name=name, full_name=f"{owner}/{name}",
            url=f"https://github.com/{owner}/{name}",
            description=str(metadata.get("description") or ""),
            default_branch=metadata["default_branch"], commit_sha=commit_sha, tree_sha=tree_sha,
            language=metadata.get("language"), stars=metadata.get("stargazers_count", 0),
            requested_ref=ref,
        )

    def _tree_data(self, repo: RepositoryInfo, sha: str, recursive: bool) -> dict:
        data = self._request_json(
            f"/repos/{repo.owner}/{repo.name}/git/trees/{_sha(sha)}",
            params={"recursive": "1"} if recursive else None,
            cache_key=f"tree:{repo.full_name}:{sha}:{int(recursive)}",
        )
        if (
            not isinstance(data, dict) or not isinstance(data.get("tree"), list)
            or not isinstance(data.get("truncated"), bool)
            or _sha(data.get("sha")) != sha.lower()
        ):
            raise GitHubError(
                "invalid_response",
                "GitHub tree metadata is incomplete or inconsistent.",
            )
        return data

    def _entry(self, repo: RepositoryInfo, item: dict, prefix: str = "") -> TreeEntry:
        try:
            path = _path(prefix + item["path"])
            kind = item["type"]
            if kind not in {"blob", "tree", "commit"}:
                raise KeyError("type")
            entry = TreeEntry(
                path=path, type=kind, sha=_sha(item["sha"]), size=_size(item.get("size", 0)),
            )
        except (TypeError, KeyError):
            raise GitHubError(
                "invalid_response",
                "GitHub tree contains an invalid entry.",
            ) from None
        if kind == "blob":
            self._blobs[(repo.full_name, repo.commit_sha, path)] = (entry.sha, entry.size)
        return entry

    def tree(self, repo: RepositoryInfo, max_entries: int = 6000) -> TreeListing:
        if max_entries < 1:
            raise ValueError("Tree entry budget must be positive")
        warnings: list[str] = []
        try:
            data = self._tree_data(repo, repo.tree_sha, True)
        except GitHubError as error:
            if error.code != "response_too_large":
                raise
            # A large recursive response is a traversal choice, not a fatal repo error.
            warnings.append(
                "Recursive tree response exceeded the safe byte budget; "
                "using bounded hierarchical traversal. Git trees have no pagination API."
            )
            data = {"tree": [], "truncated": True}
        entries = sorted((self._entry(repo, item) for item in data["tree"]), key=_priority)
        if not data["truncated"]:
            return TreeListing(
                entries=entries[:max_entries], truncated=len(entries) > max_entries,
                warnings=[f"Tree entry budget ({max_entries}) reached; listing is incomplete."]
                if len(entries) > max_entries else [],
            )
        if not warnings:
            warnings.append(
                "GitHub truncated its recursive tree response; "
                "using bounded hierarchical traversal. "
                "Git trees have no pagination API."
            )
        collected: dict[str, TreeEntry] = {}
        pending = deque([("", repo.tree_sha)])
        truncated = False
        while pending:
            prefix, sha = pending.popleft()
            try:
                current = self._tree_data(repo, sha, False)
            except GitHubError as error:
                # Partial observations remain useful; the report must expose their limitation.
                warnings.append(
                    f"Hierarchical traversal stopped: {error.code}; listing is incomplete."
                )
                truncated = True
                break
            if current["truncated"]:
                warnings.append(
                    f"GitHub truncated directory {prefix or '/'}; listing is incomplete."
                )
                truncated = True
            children = sorted(
                (self._entry(repo, item, prefix) for item in current["tree"]), key=_priority,
            )
            budget_hit = False
            for entry in children:
                if len(collected) >= max_entries:
                    budget_hit = True
                    truncated = True
                    break
                collected[entry.path] = entry
                if entry.type == "tree":
                    if set(PurePosixPath(entry.path).parts) & IGNORED_PARTS:
                        warnings.append(f"Excluded dependency/generated subtree: {entry.path}.")
                        truncated = True
                    else:
                        pending.append((entry.path + "/", entry.sha))
            if len(collected) >= max_entries and (pending or budget_hit):
                warnings.append(
                    f"Tree entry budget ({max_entries}) reached; listing is incomplete."
                )
                truncated = True
                break
        if not collected:
            # The partial recursive response can still contain valuable evidence paths.
            collected = {entry.path: entry for entry in entries[:max_entries]}
            truncated = True
        return TreeListing(
            entries=sorted(collected.values(), key=_priority),
            truncated=truncated, warnings=warnings,
        )

    def restore_tree(self, repo: RepositoryInfo, listing: TreeListing) -> None:
        """Restore the pinned blob index from a durable, possibly partial tree.

        No network calls occur. Missing paths stay missing; a partial tree never
        authorizes an assumed blob or claims complete repository coverage.
        """
        _sha(repo.commit_sha)
        _sha(repo.tree_sha)
        for entry in listing.entries:
            if entry.type == "blob":
                self._blobs[(repo.full_name, repo.commit_sha, _path(entry.path))] = (
                    _sha(entry.sha), _size(entry.size),
                )

    def _decode(
        self, data: dict, path: str, expected_sha: str | None = None,
        max_bytes: int | None = None,
    ) -> SourceFile:
        limit = self.max_file_bytes if max_bytes is None else min(self.max_file_bytes, max_bytes)
        size = _size(data.get("size"))
        if size > limit:
            raise GitHubError("file_too_large", "File exceeds the configured per-file byte budget.")
        sha = _sha(data.get("sha"))
        if expected_sha and sha != expected_sha:
            raise GitHubError(
                "integrity_error",
                "File SHA differs from the pinned repository tree.",
            )
        if data.get("encoding") != "base64" or not isinstance(data.get("content"), str):
            raise GitHubError(
                "incomplete_content",
                "GitHub did not provide complete base64 file content.",
            )
        try:
            compact = "".join(data["content"].split())
            if len(compact) > ((limit + 2) // 3) * 4:
                raise GitHubError(
                    "file_too_large",
                    "Encoded file exceeds the per-file byte budget.",
                )
            raw = base64.b64decode(compact, validate=True)
        except (binascii.Error, ValueError):
            raise GitHubError(
                "incomplete_content",
                "GitHub file content is malformed or incomplete.",
            ) from None
        if len(raw) != size:
            raise GitHubError(
                "incomplete_content",
                "File content length does not match GitHub metadata.",
            )
        actual_sha = hashlib.sha1(f"blob {len(raw)}\0".encode() + raw).hexdigest()
        if actual_sha != sha:
            raise GitHubError("integrity_error", "File content does not match its Git blob SHA.")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise GitHubError(
                "binary_file",
                "File is binary or does not use UTF-8 encoding.",
            ) from None
        control_count = sum(byte < 32 and byte not in {9, 10, 12, 13} for byte in raw)
        if b"\0" in raw or control_count > max(1, len(raw) // 100):
            raise GitHubError(
                "binary_file",
                "Binary/control-byte content is excluded from static reads.",
            )
        return SourceFile(
            path=path, text=text, blob_sha=sha, size=size,
            total_lines=0 if not text else text.count("\n") + int(not text.endswith("\n")),
        )

    def read_file(
        self, repo: RepositoryInfo, path: str, *, max_bytes: int | None = None,
    ) -> SourceFile:
        if max_bytes is not None and max_bytes < 0:
            raise ValueError("File read byte budget must be non-negative")
        limit = self.max_file_bytes if max_bytes is None else min(self.max_file_bytes, max_bytes)
        path = _path(path)
        parts = PurePosixPath(path).parts
        if set(parts) & IGNORED_PARTS or path.endswith((".min.js", ".min.css", ".map")):
            raise GitHubError("excluded_file", "Generated or dependency directories are excluded.")
        if PurePosixPath(path).suffix.lower() in BINARY_SUFFIXES:
            raise GitHubError("binary_file", "Binary file types are excluded from static reads.")
        _sha(repo.commit_sha)
        cache_key = f"source:{repo.full_name}:{repo.commit_sha}:{path}"
        known = self._blobs.get((repo.full_name, repo.commit_sha, path))
        cached = self._cache.get(cache_key)
        if cached is not None:
            try:
                source = SourceFile.model_validate(cached)
                if source.path != path:
                    raise ValueError("Incorrect cached path")
                # Validate persisted source content, including a lower budget on a later request.
                result = self._decode(
                    {"size": source.size, "sha": source.blob_sha, "encoding": "base64",
                     "content": base64.b64encode(source.text.encode("utf-8")).decode("ascii")},
                    path,
                    expected_sha=known[0] if known else None, max_bytes=limit,
                )
            except GitHubError as error:
                if error.code == "file_too_large":
                    raise
                self._cache.delete(cache_key)
            except (ValueError, TypeError, KeyError):
                self._cache.delete(cache_key)
            else:
                self.cache_hits += 1
                if self.on_request is not None:
                    self.on_request(self.request_count, self.cache_hits)
                return result
        response_budget = ((limit + 2) // 3) * 4 + limit // 20 + 8192
        if known:
            sha, size = known
            if size > limit:
                raise GitHubError(
                    "file_too_large",
                    "File exceeds the configured per-file byte budget.",
                )
            data = self._request_json(
                f"/repos/{repo.owner}/{repo.name}/git/blobs/{sha}",
                cache_key=f"blob:{repo.full_name}:{sha}", max_response_bytes=response_budget,
            )
            if not isinstance(data, dict):
                raise GitHubError("invalid_response", "GitHub returned invalid blob metadata.")
            source = self._decode(data, path, sha, max_bytes=limit)
        else:
            data = self._request_json(
                f"/repos/{repo.owner}/{repo.name}/contents/{quote(path, safe='/')}",
                params={"ref": repo.commit_sha}, max_response_bytes=response_budget,
            )
            if not isinstance(data, dict) or data.get("type") != "file":
                raise GitHubError("unsupported_file", "Path is a directory, symlink or submodule.")
            if data.get("path") != path:
                raise GitHubError(
                    "integrity_error",
                    "GitHub returned content for a different file path.",
                )
            source = self._decode(data, path, max_bytes=limit)
        self._cache.put(cache_key, source.model_dump())
        return source
