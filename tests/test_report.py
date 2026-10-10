"""Offline report tests: all source material is deterministic fixture data."""

import hashlib
from urllib.parse import quote

import pytest

from repo_research.models import (
    DraftClaim,
    Evidence,
    ReportDraft,
    RepositoryInfo,
    SourceFile,
    Usage,
)
from repo_research.report import finalize_report, verify_evidence
from repo_research.tools import evidence_identifier, evidence_url

SHA = "a" * 40
SOURCE = "def invoke(tool, state):\n    result = tool(state)\n    return result\n"


def git_blob_sha(text):
    raw = text.encode()
    return hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()


BLOB = git_blob_sha(SOURCE)


@pytest.fixture
def repo():
    return RepositoryInfo(
        owner="example",
        name="agent",
        full_name="example/agent",
        url="https://github.com/example/agent",
        default_branch="main",
        commit_sha=SHA,
        tree_sha="c" * 40,
        language="Python",
    )


@pytest.fixture
def files():
    return {
        "src/agent.py": SourceFile(
            path="src/agent.py",
            text=SOURCE,
            blob_sha=BLOB,
            size=len(SOURCE.encode()),
            total_lines=3,
        )
    }


@pytest.fixture
def evidence():
    return Evidence(
        id=evidence_identifier("example/agent", SHA, "src/agent.py", 1, 3, BLOB),
        repository="example/agent",
        commit_sha=SHA,
        path="src/agent.py",
        start_line=1,
        end_line=3,
        excerpt=SOURCE.rstrip("\n"),
        blob_sha=BLOB,
        url=f"https://github.com/example/agent/blob/{SHA}/src/agent.py#L1-L3",
    )


def as_dict(value):
    return value.model_dump() if hasattr(value, "model_dump") else value


def test_real_source_excerpt_becomes_verified(repo, files, evidence):
    verified, warnings = verify_evidence(repo, files, [evidence])
    assert len(verified) == 1
    item = as_dict(verified[0])
    assert item["verified"] is True
    assert item["excerpt"] == SOURCE.rstrip("\n")
    assert item["commit_sha"] == SHA
    assert not warnings


@pytest.mark.parametrize(
    "change",
    [
        {"repository": "attacker/agent"},
        {"commit_sha": "d" * 40},
        {"path": "src/missing.py"},
        {"blob_sha": "e" * 40},
        {"id": "forged-id"},
        {"start_line": 0},
        {"end_line": 99},
        {"start_line": 3, "end_line": 1},
        {"excerpt": "def invoke(tool, state):\n    tool.delete_everything()"},
    ],
)
def test_forged_citations_are_rejected(repo, files, evidence, change):
    forged = evidence.model_copy(update=change)
    verified, warnings = verify_evidence(repo, files, [forged])
    assert not verified
    assert warnings


def test_line_verification_counts_lf_only_and_preserves_exact_crlf_bytes(repo):
    text = "value = 'one\u2028two\vthree\ffour'\r\nnext_value = 2\r\n"
    file = SourceFile(
        path="src/lines.py",
        text=text,
        blob_sha=git_blob_sha(text),
        size=len(text.encode()),
        total_lines=2,
    )
    item = Evidence(
        id=evidence_identifier(repo.full_name, SHA, file.path, 2, 2, file.blob_sha),
        repository=repo.full_name,
        commit_sha=SHA,
        path=file.path,
        start_line=2,
        end_line=2,
        excerpt="next_value = 2\r",
        blob_sha=file.blob_sha,
        url=evidence_url(repo, file.path, 2, 2),
    )
    verified, warnings = verify_evidence(repo, {file.path: file}, [item])
    assert len(verified) == 1
    assert not warnings
    forged = item.model_copy(update={"start_line": 3, "end_line": 3})
    assert not verify_evidence(repo, {file.path: file}, [forged])[0]


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "https://evil.example/pretend-proof",
        "https://github.com/example/agent/blob/main/src/agent.py#L1-L3",
        f"https://github.com/example/agent/blob/{SHA}/src/other.py#L1-L3",
    ],
)
def test_untrusted_urls_never_survive_verification(repo, files, evidence, url):
    verified, warnings = verify_evidence(repo, files, [evidence.model_copy(update={"url": url})])
    if verified:
        assert as_dict(verified[0])["url"] == evidence.url
    else:
        assert warnings


def test_unicode_and_spaces_have_commit_pinned_encoded_permalink(repo, evidence):
    path = "src/测试 tool.py"
    source = SourceFile(
        path=path, text=SOURCE, blob_sha=BLOB, size=len(SOURCE.encode()), total_lines=3
    )
    canonical = f"https://github.com/example/agent/blob/{SHA}/{quote(path, safe='/')}#L1-L3"
    item = evidence.model_copy(
        update={
            "path": path,
            "url": canonical,
            "id": evidence_identifier("example/agent", SHA, path, 1, 3, BLOB),
        }
    )
    verified, _ = verify_evidence(repo, {path: source}, [item])
    assert len(verified) == 1
    assert as_dict(verified[0])["url"] == canonical


def test_evidence_without_loaded_source_is_not_trusted(repo, evidence):
    verified, warnings = verify_evidence(repo, {}, [evidence.model_copy(update={"verified": True})])
    assert verified == []
    assert warnings


def test_mutated_source_and_excerpt_cannot_bypass_blob_integrity(repo, files, evidence):
    altered = SOURCE.replace("tool(state)", "tool.delete_everything()")
    files["src/agent.py"] = files["src/agent.py"].model_copy(
        update={
            "text": altered,
            "size": len(altered.encode()),
        }
    )
    forged = evidence.model_copy(update={"excerpt": altered.rstrip("\n")})
    verified, warnings = verify_evidence(repo, files, [forged])
    assert not verified
    assert warnings


def test_conflicting_duplicate_identifiers_are_rejected(repo, files, evidence):
    first = evidence.model_dump()
    conflict = evidence.model_copy(update={"excerpt": "forged quoted code"}).model_dump()
    verified, warnings = verify_evidence(repo, files, [first, conflict])
    assert not verified
    assert warnings


def test_identical_duplicate_citations_are_deduplicated(repo, files, evidence):
    verified, warnings = verify_evidence(repo, files, [evidence.model_dump()] * 2)
    assert len(verified) == 1
    assert not warnings


def test_claims_missing_valid_evidence_are_marked_unknown(repo, files, evidence):
    draft = ReportDraft(
        title="Tool execution",
        summary="Static code research",
        claims=[
            DraftClaim(
                category="fact", text="invoke calls the provided tool", evidence_ids=[evidence.id]
            ),
            DraftClaim(category="fact", text="Memory is persisted to Redis", evidence_ids=["E99"]),
            DraftClaim(category="fact", text="All calls are asynchronous", evidence_ids=[]),
        ],
    )
    result = finalize_report(
        draft,
        repo,
        files,
        [evidence],
        "How are tools called?",
        Usage(),
        "evidence_sufficient",
        "mock",
    )
    claims = as_dict(result["draft"])["claims"]
    assert claims[0]["category"] == "fact"
    assert all(claim["category"] == "unknown" for claim in claims[1:])
    assert claims[1]["evidence_ids"] == []
    assert SHA in result["markdown"]
    assert evidence.url in result["markdown"]
    assert "mock" in result["markdown"].lower() or "模拟" in result["markdown"]


def test_documentation_does_not_prove_implementation(repo, evidence):
    text = "# Agent\nThis framework supports durable memory.\n"
    blob = git_blob_sha(text)
    file = SourceFile(
        path="README.md", text=text, blob_sha=blob, size=len(text.encode()), total_lines=2
    )
    doc = evidence.model_copy(
        update={
            "path": file.path,
            "start_line": 2,
            "end_line": 2,
            "excerpt": text.splitlines()[1],
            "kind": "documentation",
            "blob_sha": blob,
            "url": evidence_url(repo, file.path, 2, 2),
            "id": evidence_identifier(repo.full_name, SHA, file.path, 2, 2, blob),
        }
    )
    draft = ReportDraft(
        claims=[
            DraftClaim(
                category="fact",
                text="Memory is implemented with durable disk persistence",
                evidence_ids=[doc.id],
            )
        ]
    )
    result = finalize_report(
        draft,
        repo,
        {file.path: file},
        [doc],
        "Explain memory implementation",
        Usage(),
        "budget_exhausted",
        "mock",
    )
    claim = as_dict(result["draft"])["claims"][0]
    assert claim["category"] in {"inference", "unknown"}
    assert result["warnings"]


def test_documentation_cannot_masquerade_as_code_evidence(repo, evidence):
    path = "README.md"
    file = SourceFile(
        path=path, text=SOURCE, blob_sha=BLOB, size=len(SOURCE.encode()), total_lines=3
    )
    forged = evidence.model_copy(
        update={
            "path": path,
            "kind": "code",
            "id": evidence_identifier(repo.full_name, SHA, path, 1, 3, BLOB),
            "url": evidence_url(repo, path, 1, 3),
        }
    )
    verified, warnings = verify_evidence(repo, {path: file}, [forged])
    if verified:
        assert as_dict(verified[0])["kind"] == "documentation"
    else:
        assert warnings


def test_report_escapes_untrusted_html_and_markdown_links(repo, files, evidence):
    hostile = "<img src=x onerror=alert(1)> [click](javascript:alert(1))"
    draft = ReportDraft(
        title=hostile,
        summary=hostile,
        claims=[
            DraftClaim(
                section=hostile,
                category="fact",
                text=hostile,
                evidence_ids=[evidence.id],
            )
        ],
        limitations=[hostile],
    )
    result = finalize_report(
        draft, repo, files, [evidence], hostile, Usage(), "budget_exhausted", "mock"
    )
    rendered = result["markdown"]
    assert "<img" not in rendered
    assert "[click](javascript:" not in rendered
    assert evidence.url in rendered


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example/pretend-proof",
        "http://evil.example/pretend-proof",
        "HTTPS://evil.example/pretend-proof",
    ],
)
def test_untrusted_bare_urls_cannot_become_autolinked_citations(repo, files, evidence, url):
    draft = ReportDraft(
        title=url,
        summary=url,
        claims=[
            DraftClaim(
                category="fact",
                text=f"See {url} for implementation",
                evidence_ids=[evidence.id],
            )
        ],
    )
    result = finalize_report(
        draft, repo, files, [evidence], url, Usage(), "evidence_sufficient", "mock"
    )
    assert url not in result["markdown"]
    assert evidence.url in result["markdown"]


def test_repository_backticks_cannot_break_out_of_source_code_fence(repo):
    text = 'payload = """\n```\n<img src=x onerror=alert(1)>\n"""\n'
    file = SourceFile(
        path="src/payload.py",
        text=text,
        blob_sha=git_blob_sha(text),
        size=len(text.encode()),
        total_lines=4,
    )
    item = Evidence(
        id=evidence_identifier(repo.full_name, SHA, file.path, 1, 4, file.blob_sha),
        repository=repo.full_name,
        commit_sha=SHA,
        path=file.path,
        start_line=1,
        end_line=4,
        excerpt=text.rstrip("\n"),
        blob_sha=file.blob_sha,
        url=evidence_url(repo, file.path, 1, 4),
    )
    draft = ReportDraft(
        claims=[
            DraftClaim(
                category="fact",
                text="The source contains a payload string",
                evidence_ids=[item.id],
            )
        ]
    )
    result = finalize_report(
        draft,
        repo,
        {file.path: file},
        [item],
        "Inspect strings",
        Usage(),
        "evidence_sufficient",
        "mock",
    )
    assert "````python\n" in result["markdown"]
    assert text.rstrip("\n") + "\n````" in result["markdown"]
