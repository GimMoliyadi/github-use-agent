"""Reproduce static evidence checks using a real, recorded GitHub snapshot.

This is an offline replay of data fetched through the installed GitHub connector.
It does not replace tests of LangGraph, a real LLM, or direct Python HTTP access.
"""

import hashlib
import json
from pathlib import Path

from repo_research.models import (
    Budget,
    DraftClaim,
    ReportDraft,
    RepositoryInfo,
    SourceFile,
    ToolAction,
    TreeListing,
    Usage,
)
from repo_research.report import finalize_report
from repo_research.tools import ResearchTools, source_lines


def main() -> None:
    root = Path(__file__).parent
    snap = json.loads((root / "sampleproject-snapshot.json").read_text())
    repo = RepositoryInfo(
        owner="pypa",
        name="sampleproject",
        full_name="pypa/sampleproject",
        url="https://github.com/pypa/sampleproject",
        default_branch="main",
        commit_sha=snap["commit"]["sha"],
        tree_sha=snap["commit"]["commit"]["tree"]["sha"],
    )
    files = {}
    for item in snap["blobs"]:
        raw = item["text"].encode()
        actual = hashlib.sha1(f"blob {len(raw)}\0".encode() + raw).hexdigest()
        assert actual == item["sha"] and len(raw) == item["size"]
        files[item["path"]] = SourceFile(
            path=item["path"],
            text=item["text"],
            blob_sha=actual,
            size=len(raw),
            total_lines=len(source_lines(item["text"])),
        )

    class RecordedClient:
        def read_file(self, repo, path, **kwargs):
            return files[path]

    entries = [
        {k: v for k, v in item.items() if k in {"path", "type", "sha", "size"}}
        for item in snap["tree"]["tree"]
    ]
    usage = Usage()
    tools = ResearchTools(RecordedClient(), repo, TreeListing(entries=entries), Budget(), usage)
    observation = tools.execute(ToolAction(kind="symbols", path="src/sample/__init__.py"))
    trace = tools.execute(ToolAction(kind="trace", path="src/sample/__init__.py", symbol="main"))
    draft = ReportDraft(
        title="真实源码静态验证（GitHub 连接器读取；无 LLM）",
        claims=[
            DraftClaim(
                section="入口",
                category="fact",
                text="src/sample/__init__.py 定义 main，并调用 print 输出占位提示。",
                evidence_ids=[item["id"] for item in tools.evidence],
            )
        ],
        limitations=["仅验证真实源码静态工具与引用；未运行 LangGraph、LLM 或 Python 直连 API。"],
    )
    result = finalize_report(
        draft,
        repo,
        tools.files,
        tools.evidence,
        "定向分析 sample.main",
        usage,
        "connector_snapshot_static_smoke",
        "mock",
    )
    assert result["evidence"] and all(item["verified"] for item in result["evidence"])
    (root / "sampleproject-static-report.md").write_text(result["markdown"])
    print(
        json.dumps(
            {
                "source": "REAL GitHub REST via connector; OFFLINE static replay; no LangGraph/LLM",
                "commit": repo.commit_sha,
                "downloaded_blobs": len(files),
                "verified_evidence": len(result["evidence"]),
                "definitions": len(observation["definitions"]),
                "call_sites": len(trace["call_sites"]),
            }
        )
    )


if __name__ == "__main__":
    main()
