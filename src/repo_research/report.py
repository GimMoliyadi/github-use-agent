"""Citation validation and a renderer that never trusts model-generated links."""

from __future__ import annotations

import hashlib
import html
import re
from collections import defaultdict
from pathlib import PurePosixPath
from typing import Any

from pydantic import ValidationError

from repo_research.models import Evidence, ReportDraft, RepositoryInfo, SourceFile, Usage
from repo_research.tools import (
    MAX_READ_LINES,
    eligible_path,
    evidence_identifier,
    evidence_url,
    source_kind,
    source_lines,
)

SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")


def _git_blob_sha(text: str) -> str:
    content = text.encode("utf-8")
    header = b"blob " + str(len(content)).encode("ascii") + b"\0"
    return hashlib.sha1(header + content).hexdigest()


def _safe_text(text: str) -> str:
    """Escape HTML, Markdown links and block syntax in every model/repository label."""
    text = html.escape(str(text).replace("\r", " ").replace("\n", " "), quote=True)
    # GFM renderers can auto-link bare URLs even after Markdown link escaping.
    text = re.sub(
        r"(?i)\b(https?|ftp|javascript|data|mailto):",
        lambda match: match[1] + "\u200b:",
        text,
    )
    text = re.sub(r"(?i)\bwww\.", "www\u200b.", text)
    return re.sub(r"([\\`*_{}\[\]()#!|>])", r"\\\1", text)


def _fenced_source(text: str, path: str) -> str:
    longest = max((len(match[0]) for match in re.finditer(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    languages = {
        ".py": "python",
        ".js": "javascript",
        ".ts": "typescript",
        ".tsx": "tsx",
        ".json": "json",
        ".toml": "toml",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".rs": "rust",
        ".go": "go",
        ".sh": "bash",
    }
    language = languages.get(PurePosixPath(path).suffix, "text")
    return f"{fence}{language}\n{text}\n{fence}"


def verify_evidence(
    repo: RepositoryInfo, files: dict, evidence: list[dict]
) -> tuple[list[dict], list[str]]:
    """Verify immutable provenance against full fetched bytes, not LLM quotations.

    Verification establishes that a citation exists at the pinned commit. It does
    not automatically prove that a natural-language claim follows from its text.
    """
    verified: list[dict] = []
    warnings: list[str] = []
    if not SHA_PATTERN.fullmatch(repo.commit_sha) or repo.full_name != f"{repo.owner}/{repo.name}":
        return [], ["Repository metadata has no valid pinned full commit SHA."]
    raw_items = [item.model_dump() if isinstance(item, Evidence) else item for item in evidence]
    duplicates: dict[str, list[dict]] = defaultdict(list)
    for item in raw_items:
        if isinstance(item, dict) and isinstance(item.get("id"), str):
            duplicates[item["id"]].append(item)
    conflicting_ids = {
        identifier
        for identifier, items in duplicates.items()
        if any(
            {key: value for key, value in item.items() if key != "verified"}
            != {key: value for key, value in items[0].items() if key != "verified"}
            for item in items[1:]
        )
    }
    seen: set[str] = set()
    sources: dict[str, SourceFile | None] = {}
    for raw in raw_items:
        identifier = raw.get("id", "<invalid>") if isinstance(raw, dict) else "<invalid>"
        try:
            item = Evidence.model_validate(raw)
            if item.id in conflicting_ids:
                raise ValueError("conflicting citation identifier")
            if item.id in seen:
                continue
            if item.repository != repo.full_name or item.commit_sha != repo.commit_sha:
                raise ValueError("repository or commit differs from pinned metadata")
            if not eligible_path(item.path) or item.path not in files:
                raise ValueError("file is absent from complete fetched sources")
            if item.path not in sources:
                try:
                    source = SourceFile.model_validate(files[item.path])
                    if (
                        source.path != item.path
                        or source.total_lines != len(source_lines(source.text))
                        or source.size != len(source.text.encode("utf-8"))
                        or not SHA_PATTERN.fullmatch(source.blob_sha)
                        or _git_blob_sha(source.text) != source.blob_sha
                    ):
                        raise ValueError("source integrity metadata mismatch")
                    sources[item.path] = source
                except (ValidationError, ValueError, UnicodeError):
                    sources[item.path] = None
            source = sources[item.path]
            if source is None or item.blob_sha != source.blob_sha:
                raise ValueError("blob does not match complete fetched bytes")
            lines = source_lines(source.text)
            if not (
                1 <= item.start_line <= item.end_line <= len(lines)
                and item.end_line - item.start_line + 1 <= MAX_READ_LINES
            ):
                raise ValueError("citation line range does not exist or exceeds limit")
            if item.excerpt != "\n".join(lines[item.start_line - 1 : item.end_line]):
                raise ValueError("quotation differs from exact source lines")
            canonical_url = evidence_url(repo, item.path, item.start_line, item.end_line)
            if item.url != canonical_url:
                raise ValueError("URL differs from immutable program-generated permalink")
            expected_id = evidence_identifier(
                repo.full_name,
                repo.commit_sha,
                item.path,
                item.start_line,
                item.end_line,
                source.blob_sha,
            )
            if item.id != expected_id:
                raise ValueError("identifier differs from citation provenance")
            if item.kind != source_kind(item.path):
                raise ValueError("source category differs from file path")
            item.verified = True
            verified.append(item.model_dump())
            seen.add(item.id)
        except (ValidationError, ValueError, TypeError, UnicodeError) as error:
            warnings.append(f"Rejected citation {identifier}: {str(error).splitlines()[0]}.")
    return verified, warnings


def finalize_report(
    draft: ReportDraft,
    repo: RepositoryInfo | None,
    files: dict,
    evidence: list[dict],
    question: str,
    usage: Usage,
    stop_reason: str,
    model_mode: str,
) -> dict[str, Any]:
    """Downgrade unsupported claims and render only validated permanent links."""
    verified, warnings = (
        verify_evidence(repo, files, evidence)
        if repo
        else ([], ["Repository initialization did not complete; no source evidence was verified."])
    )
    by_id = {item["id"]: item for item in verified}
    final = draft.model_copy(deep=True)
    for claim in final.claims:
        valid_ids = list(
            dict.fromkeys(identifier for identifier in claim.evidence_ids if identifier in by_id)
        )
        if len(valid_ids) != len(claim.evidence_ids):
            warnings.append("Removed invalid or duplicate evidence references from a report claim.")
        claim.evidence_ids = valid_ids
        if claim.category != "unknown" and not valid_ids:
            claim.category = "unknown"
            warnings.append("An unsupported claim was downgraded to unconfirmed.")
        if claim.category == "fact" and valid_ids:
            kinds = {by_id[identifier]["kind"] for identifier in valid_ids}
            if "code" not in kinds:
                claim.category = "inference"
                warnings.append(
                    "A claim supported only by documentation/configuration was downgraded; "
                    "it does not directly confirm implementation."
                )
    limitations = [
        "静态分析不会执行仓库代码；跨文件候选和调用语法不能确认动态分派或运行时行为。",
        f"仅分析已获取的 {len(files)} 个文件；预算内的抽样搜索不代表完整仓库覆盖。",
        "引用校验确认固定 Commit 下的文件、字节、行号和原文；"
        "自然语言结论仍需人工判断证据是否充分。",
    ]
    if model_mode == "mock":
        limitations.append("本报告使用 Mock 模型生成研究决策与草稿；代码证据来自配置的数据源。")
    if not usage.cost_known:
        limitations.append("未配置或未获得可核对的模型价格信息，实际美元费用未知。")
    limitations.append(f"研究停止原因：{stop_reason}。")
    final.limitations = list(dict.fromkeys([*limitations, *final.limitations]))[:30]

    lines = [f"# {_safe_text(final.title)}", ""]
    if model_mode == "mock":
        lines += ["**模拟模式（Mock）：研究策略与文字由确定性模拟模型生成。**", ""]
    lines += [f"研究目标：{_safe_text(question)}", ""]
    if repo:
        lines += [
            f"仓库：{_safe_text(repo.full_name)}  ",
            f"固定 Commit：`{_safe_text(repo.commit_sha)}`",
            "",
        ]
    if final.summary:
        lines += [
            "## 模型摘要（未逐项验证）",
            "",
            _safe_text(final.summary),
            "",
            "技术结论以以下带来源的发现为准。",
            "",
        ]
    sections: dict[str, list] = {}
    for claim in final.claims:
        sections.setdefault(claim.section, []).append(claim)
    labels = {"fact": "有引用的事实", "inference": "基于证据的推断", "unknown": "尚未确认"}
    cited_ids: list[str] = []
    for section, claims in sections.items():
        lines += [f"## {_safe_text(section)}", ""]
        for claim in claims:
            references = []
            for identifier in claim.evidence_ids:
                item = by_id[identifier]
                label = f"{_safe_text(item['path'])}:L{item['start_line']}-L{item['end_line']}"
                references.append(f"[{label}]({item['url']})")
                if identifier not in cited_ids:
                    cited_ids.append(identifier)
            suffix = " 来源：" + " · ".join(references) if references else "（证据不足）"
            lines += [f"- **{labels[claim.category]}**：{_safe_text(claim.text)}{suffix}"]
        lines.append("")
    if not final.claims:
        lines += ["## 尚未确认", "", "当前没有足够证据生成技术结论。", ""]
    if cited_ids:
        lines += ["## 代码证据", ""]
        for identifier in cited_ids:
            item = by_id[identifier]
            label = f"{_safe_text(item['path'])}，L{item['start_line']}–L{item['end_line']}"
            lines += [
                f"### [{label}]({item['url']})",
                "",
                f"来源类型：{_safe_text(item['kind'])}；Blob：`{item['blob_sha']}`",
                "",
                _fenced_source(item["excerpt"], item["path"]),
                "",
            ]
    lines += ["## 限制与未确认事项", ""]
    lines += [f"- {_safe_text(limitation)}" for limitation in final.limitations]
    lines += ["", "## 资源使用", ""]
    lines += [
        f"- 探索轮数：{usage.iterations}；文件：{usage.files}；分析字节：{usage.bytes}。",
        f"- GitHub 请求：{usage.api_requests}；缓存命中：{usage.cache_hits}；"
        f"模型调用：{usage.llm_calls}。",
        f"- 模型 Token：输入 {usage.input_tokens}，输出 {usage.output_tokens}。",
        (
            f"- 已记录费用：${usage.cost_usd:.6f}。"
            if usage.cost_known
            else "- 实际模型费用：未知。"
        ),
    ]
    if warnings:
        lines += ["", "## 引用与结论校验提示", ""]
        lines += [f"- {_safe_text(warning)}" for warning in dict.fromkeys(warnings)]
    return {
        "draft": final.model_dump(),
        "evidence": verified,
        "markdown": "\n".join(lines) + "\n",
        "warnings": list(dict.fromkeys(warnings)),
    }
