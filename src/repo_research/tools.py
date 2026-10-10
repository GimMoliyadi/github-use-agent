"""Budgeted, read-only repository tools. Source text is data, never instructions."""

from __future__ import annotations

import ast
import hashlib
import re
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from repo_research.models import (
    Budget,
    Evidence,
    RepositoryInfo,
    SourceFile,
    ToolAction,
    TreeListing,
    Usage,
)

if TYPE_CHECKING:
    from repo_research.github import GitHubClient

MAX_FILE_BYTES = 120_000
MAX_READ_LINES = 200
MAX_NEW_FILES_PER_ACTION = 3
MAX_SEARCH_MATCHES = 12
IGNORED_DIRECTORIES = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "vendor",
        "dist",
        "build",
        "__pycache__",
        ".next",
        ".cache",
        "coverage",
        "site-packages",
        "target",
        "assets",
        "images",
    }
)
BINARY_EXTENSIONS = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".ico",
        ".svg",
        ".pdf",
        ".zip",
        ".gz",
        ".tar",
        ".woff",
        ".woff2",
        ".ttf",
        ".mp4",
        ".mp3",
        ".so",
        ".dll",
        ".exe",
        ".pyc",
        ".sqlite",
        ".db",
        ".lock",
        ".ipynb",
    }
)
DOCUMENT_EXTENSIONS = frozenset({".md", ".rst", ".adoc", ".txt"})
CONFIG_EXTENSIONS = frozenset({".toml", ".json", ".yaml", ".yml", ".ini", ".cfg", ".xml"})


def source_lines(text: str) -> list[str]:
    """GitHub line anchors follow LF, not Unicode/vertical-tab splitlines rules."""
    if not text:
        return []
    lines = text.split("\n")
    if text.endswith("\n"):
        lines.pop()
    return lines


class ToolError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class ToolBudgetExceeded(ToolError):
    def __init__(self, message: str):
        super().__init__("budget_exhausted", message)


def evidence_identifier(
    repository: str, commit_sha: str, path: str, start_line: int, end_line: int, blob_sha: str
) -> str:
    parts = (repository, commit_sha, path, str(start_line), str(end_line), blob_sha)
    return "ev_" + hashlib.sha256("\0".join(parts).encode()).hexdigest()[:20]


def evidence_url(repo: RepositoryInfo, path: str, start_line: int, end_line: int) -> str:
    repository = f"{quote(repo.owner, safe='')}/{quote(repo.name, safe='')}"
    suffix = f"#L{start_line}" + (f"-L{end_line}" if end_line != start_line else "")
    return f"https://github.com/{repository}/blob/{repo.commit_sha}/{quote(path, safe='/')}{suffix}"


def source_kind(path: str) -> str:
    filename = PurePosixPath(path).name.lower()
    extension = PurePosixPath(path).suffix.lower()
    if extension in CONFIG_EXTENSIONS or filename in {"dockerfile", "makefile", "requirements.txt"}:
        return "configuration"
    if extension in DOCUMENT_EXTENSIONS or filename.startswith(("readme", "license", "changelog")):
        return "documentation"
    return "code"


def eligible_path(path: str) -> bool:
    parts = PurePosixPath(path).parts
    return (
        bool(parts)
        and not path.startswith("/")
        and ".." not in parts
        and not any(part.lower() in IGNORED_DIRECTORIES for part in parts[:-1])
        and PurePosixPath(path).suffix.lower() not in BINARY_EXTENSIONS
    )


def _priority(path: str, query: str = "") -> tuple[int, int, str]:
    lowered = path.lower()
    terms = [term.lower() for term in re.findall(r"[\w]+", query) if len(term) > 1]
    score = sum(8 for term in terms if term in lowered)
    name = PurePosixPath(path).name.lower()
    if name in {"pyproject.toml", "package.json", "setup.py", "requirements.txt", "cargo.toml"}:
        score += 5
    if name.startswith("readme"):
        score += 4
    if lowered.startswith(("src/", "lib/", "packages/")):
        score += 3
    if name in {"__init__.py", "main.py", "app.py", "index.ts", "index.js"}:
        score += 2
    if any(part in {"test", "tests", "examples", "docs"} for part in lowered.split("/")):
        score -= 2
    return (-score, path.count("/"), path)


def _node_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _node_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return "<dynamic expression>"


class _PythonIndex(ast.NodeVisitor):
    def __init__(self) -> None:
        self.scope: list[str] = []
        self.definitions: list[dict[str, Any]] = []
        self.imports: list[dict[str, Any]] = []
        self.calls: list[dict[str, Any]] = []

    def _definition(self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> None:
        name = ".".join([*self.scope, node.name])
        self.definitions.append(
            {
                "name": node.name,
                "qualified_name": name,
                "kind": "class" if isinstance(node, ast.ClassDef) else "function",
                "start_line": node.lineno,
                "end_line": node.end_lineno or node.lineno,
            }
        )
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._definition(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._definition(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._definition(node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.imports.append(
                {
                    "module": alias.name,
                    "name": "",
                    "alias": alias.asname or alias.name.split(".")[0],
                    "level": 0,
                    "line": node.lineno,
                    "end_line": node.end_lineno or node.lineno,
                }
            )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            self.imports.append(
                {
                    "module": node.module or "",
                    "name": alias.name,
                    "alias": alias.asname or alias.name,
                    "level": node.level,
                    "line": node.lineno,
                    "end_line": node.end_lineno or node.lineno,
                }
            )

    def visit_Call(self, node: ast.Call) -> None:
        self.calls.append(
            {
                "callee": _node_name(node.func),
                "caller": ".".join(self.scope) or "<module>",
                "start_line": node.lineno,
                "end_line": node.end_lineno or node.lineno,
            }
        )
        self.generic_visit(node)


class ResearchTools:
    """Each action has bounded downloads; no target module is imported or executed."""

    def __init__(
        self,
        client: GitHubClient,
        repo: RepositoryInfo,
        tree: TreeListing,
        budget: Budget,
        usage: Usage,
        files: dict[str, dict] | None = None,
        evidence: list[dict] | None = None,
    ) -> None:
        self.client = client
        self.repo = repo
        self.tree = tree
        self.budget = budget
        self.usage = usage
        self.files = files if files is not None else {}
        self.evidence = evidence if evidence is not None else []
        self.entries = {entry.path: entry for entry in tree.entries if entry.type == "blob"}
        self._new_files = 0
        self._new_citations = 0

    def execute(self, action: ToolAction) -> dict[str, Any]:
        self._new_files = 0
        self._new_citations = 0
        if action.kind == "finish":
            return {"kind": "finish", "message": "Exploration complete."}
        return getattr(self, f"_{action.kind}")(action)

    def _load(self, path: str) -> SourceFile:
        entry = self.entries.get(path)
        if entry is None:
            raise ToolError("file_not_found", "File is absent from the pinned repository tree.")
        if not eligible_path(path):
            raise ToolError("excluded_file", "Binary, generated or vendored file is excluded.")
        if path in self.files:
            source = SourceFile.model_validate(self.files[path])
            if source.path != path or source.blob_sha != entry.sha:
                raise ToolError(
                    "source_mismatch", "Cached file differs from the pinned tree entry."
                )
            if source.total_lines != len(source_lines(source.text)):
                raise ToolError("incomplete_file", "Cached file line metadata is inconsistent.")
            return source
        remaining = self.budget.max_bytes - self.usage.bytes
        if self.usage.files >= self.budget.max_files or remaining <= 0:
            raise ToolBudgetExceeded("The repository file or byte budget is exhausted.")
        if self._new_files >= MAX_NEW_FILES_PER_ACTION:
            raise ToolError(
                "action_file_limit", "This action may download at most three new files."
            )
        if entry.size > MAX_FILE_BYTES:
            raise ToolError("oversized_file", "File exceeds the per-file static analysis limit.")
        if entry.size > remaining:
            raise ToolBudgetExceeded("The next file would exceed the remaining byte budget.")
        source = self.client.read_file(self.repo, path, max_bytes=min(MAX_FILE_BYTES, remaining))
        if not isinstance(source, SourceFile):
            source = SourceFile.model_validate(source)
        self.usage.bytes += len(source.text.encode("utf-8"))
        self._new_files += 1
        if source.size > remaining or source.size > MAX_FILE_BYTES:
            raise ToolBudgetExceeded("Downloaded file exceeded its declared size or byte budget.")
        if source.path != path or source.blob_sha != entry.sha:
            raise ToolError(
                "source_mismatch", "Returned file does not match its pinned tree entry."
            )
        if "\0" in source.text:
            raise ToolError("binary_file", "Binary content cannot become source evidence.")
        if source.total_lines != len(source_lines(source.text)) or source.size != len(
            source.text.encode("utf-8")
        ):
            raise ToolError(
                "incomplete_file", "Source line metadata does not match complete content."
            )
        self.files[path] = source.model_dump()
        self.usage.files += 1
        return source

    def _cite(self, source: SourceFile, start: int, end: int) -> dict[str, Any]:
        lines = source_lines(source.text)
        if start < 1 or end < start or end > len(lines) or end - start + 1 > MAX_READ_LINES:
            raise ToolError("invalid_lines", "Citation must identify 1–200 actual source lines.")
        identifier = evidence_identifier(
            self.repo.full_name, self.repo.commit_sha, source.path, start, end, source.blob_sha
        )
        for item in self.evidence:
            if item["id"] == identifier:
                return item
        if len(self.evidence) >= self.budget.max_evidence:
            raise ToolBudgetExceeded("The evidence citation budget is exhausted.")
        evidence = Evidence(
            id=identifier,
            repository=self.repo.full_name,
            commit_sha=self.repo.commit_sha,
            path=source.path,
            start_line=start,
            end_line=end,
            excerpt="\n".join(lines[start - 1 : end]),
            blob_sha=source.blob_sha,
            url=evidence_url(self.repo, source.path, start, end),
            kind=source_kind(source.path),
            verified=False,
        ).model_dump()
        self.evidence.append(evidence)
        return evidence

    def _goal_cite(
        self, source: SourceFile, start: int, end: int, *, limit: int, allow_new: bool = True
    ) -> dict[str, Any] | None:
        """Reuse exact source coverage; indexes must leave room for deeper goal research."""
        lines = source_lines(source.text)
        covering = [
            item
            for item in self.evidence
            if item.get("repository") == self.repo.full_name
            and item.get("commit_sha") == self.repo.commit_sha
            and item.get("path") == source.path
            and item.get("blob_sha") == source.blob_sha
            and 1 <= item.get("start_line", 0) <= start <= end <= item.get("end_line", 0)
            and item["end_line"] <= len(lines)
            and item["end_line"] - item["start_line"] + 1 <= MAX_READ_LINES
            and item.get("excerpt") == "\n".join(lines[item["start_line"] - 1 : item["end_line"]])
            and item.get("id")
            == evidence_identifier(
                self.repo.full_name,
                self.repo.commit_sha,
                source.path,
                item["start_line"],
                item["end_line"],
                source.blob_sha,
            )
            and item.get("url")
            == evidence_url(self.repo, source.path, item["start_line"], item["end_line"])
        ]
        if covering:
            return min(covering, key=lambda item: item["end_line"] - item["start_line"])
        if (
            not allow_new
            or self._new_citations >= limit
            or len(self.evidence) >= self.budget.max_evidence
        ):
            return None
        evidence = self._cite(source, start, end)
        self._new_citations += 1
        return evidence

    @staticmethod
    def _attach_citation(item: dict, evidence: dict | None, evidence_ids: list[str]) -> None:
        item["citation_status"] = "cited" if evidence else "index_only"
        if evidence:
            item["evidence_id"] = evidence["id"]
            evidence_ids.append(evidence["id"])

    def _read(self, action: ToolAction) -> dict[str, Any]:
        if action.end_line < action.start_line:
            raise ToolError("invalid_lines", "End line precedes start line.")
        source = self._load(action.path)
        if action.start_line > source.total_lines:
            raise ToolError("invalid_lines", "Start line is beyond the complete file.")
        end = min(action.end_line, action.start_line + MAX_READ_LINES - 1, source.total_lines)
        evidence = self._cite(source, action.start_line, end)
        return {
            "kind": "read",
            "path": action.path,
            "start_line": action.start_line,
            "end_line": end,
            "total_lines": source.total_lines,
            "evidence": evidence,
            "truncated_range": end < action.end_line,
        }

    def _candidate_paths(self, query: str, prefix: str = "") -> list[str]:
        paths = [
            path
            for path, entry in self.entries.items()
            if eligible_path(path) and entry.size <= MAX_FILE_BYTES and path.startswith(prefix)
        ]
        return sorted(paths, key=lambda path: (path not in self.files, *_priority(path, query)))

    def _search(self, action: ToolAction) -> dict[str, Any]:
        query = action.query.strip()
        if not query or len(query) > 200:
            raise ToolError("invalid_query", "Literal search query must contain 1–200 characters.")
        matches: list[dict] = []
        searched: list[str] = []
        warnings: list[str] = []
        candidates = self._candidate_paths(query, action.path)
        for path in candidates:
            if path not in self.files and self._new_files >= MAX_NEW_FILES_PER_ACTION:
                continue
            try:
                source = self._load(path)
            except ToolBudgetExceeded:
                if not searched:
                    raise
                warnings.append(
                    "Remaining file budget exhausted; these search results are partial."
                )
                break
            except ToolError as error:
                warnings.append(f"Skipped {path}: {error.code}.")
                continue
            searched.append(path)
            for number, line in enumerate(source_lines(source.text), 1):
                if query.casefold() in line.casefold():
                    evidence = self._cite(source, number, number)
                    matches.append(
                        {
                            "path": path,
                            "line": number,
                            "text": line,
                            "evidence_id": evidence["id"],
                        }
                    )
                    if len(matches) >= MAX_SEARCH_MATCHES:
                        break
            if len(matches) >= MAX_SEARCH_MATCHES:
                break
        return {
            "kind": "search",
            "query": query,
            "matches": matches,
            "searched_paths": searched,
            "complete": len(searched) == len(candidates) and len(matches) < MAX_SEARCH_MATCHES,
            "warnings": warnings + ["Literal search covers only the listed budget-selected files."],
        }

    def _index(self, source: SourceFile) -> tuple[list[dict], list[dict], list[dict], str]:
        if source.path.endswith(".py"):
            try:
                parsed = ast.parse(source.text, filename=source.path)
                index = _PythonIndex()
                index.visit(parsed)
            except (SyntaxError, RecursionError, ValueError) as error:
                raise ToolError(
                    "parse_error", f"Python source cannot be parsed: {type(error).__name__}."
                )
            return index.definitions, index.imports, index.calls, "static_syntax"
        definitions = []
        pattern = re.compile(
            r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?"
            r"(?:function|class|interface|struct|enum|def|fn)\s+([A-Za-z_$][\w$]*)"
        )
        for number, line in enumerate(source_lines(source.text), 1):
            found = pattern.match(line)
            if found:
                definitions.append(
                    {
                        "name": found[1],
                        "qualified_name": found[1],
                        "kind": "lexical_definition",
                        "start_line": number,
                        "end_line": number,
                    }
                )
        return definitions, [], [], "lexical_only"

    def _symbols(self, action: ToolAction) -> dict[str, Any]:
        source = self._load(action.path)
        definitions, imports, calls, confidence = self._index(source)
        term = action.symbol or action.query
        matching = [
            definition
            for definition in definitions
            if not term or term in definition["qualified_name"]
        ]
        remaining = [item for item in matching if item["start_line"] >= action.start_line]
        selected = remaining[:16]
        evidence_ids = []
        for index, definition in enumerate(selected):
            evidence = self._goal_cite(
                source,
                definition["start_line"],
                definition["start_line"],
                limit=6 if term else 4,
                allow_new=index < (6 if term else 4),
            )
            self._attach_citation(definition, evidence, evidence_ids)
        warnings = ["AST and lexical names do not resolve runtime dispatch or evaluate imports."]
        if any(item["citation_status"] == "index_only" for item in selected):
            warnings.append(
                "Uncited definitions are navigation metadata only. Read or trace a goal-selected "
                "line range before using them as report evidence."
            )
        return {
            "kind": "symbols",
            "path": source.path,
            "definitions": selected,
            "imports": imports[:40],
            "calls": calls[:60],
            "evidence_ids": list(dict.fromkeys(evidence_ids)),
            "confidence": confidence,
            "definition_count": len(definitions),
            "matching_definition_count": len(matching),
            "has_more": len(remaining) > len(selected),
            "next_start_line": remaining[16]["start_line"] if len(remaining) > 16 else None,
            "imports_truncated": len(imports) > 40,
            "calls_truncated": len(calls) > 60,
            "new_citations": self._new_citations,
            "warnings": warnings,
        }

    def _module_candidates(self, source_path: str, imported: dict) -> list[str]:
        module = imported["module"].replace(".", "/")
        level = imported["level"]
        if level:
            parent_parts = list(PurePosixPath(source_path).parent.parts)
            if level > len(parent_parts) + 1:
                return []
            base = "/".join(parent_parts[: len(parent_parts) - level + 1])
            module = "/".join(part for part in (base, module) if part)
        suffixes = [f"{module}.py", f"{module}/__init__.py"] if module else []
        if imported["name"] not in {"", "*"}:
            child = "/".join(part for part in (module, imported["name"]) if part)
            suffixes += [f"{child}.py", f"{child}/__init__.py"]
        return sorted(
            {
                path
                for path in self.entries
                if eligible_path(path)
                and any(
                    path == suffix or (not level and path.endswith("/" + suffix))
                    for suffix in suffixes
                )
            }
        )[:6]

    def _trace(self, action: ToolAction) -> dict[str, Any]:
        source = self._load(action.path)
        symbol = (action.symbol or action.query).strip()
        if not symbol:
            raise ToolError(
                "invalid_symbol", "Tracing requires a function, class or lexical call name."
            )
        definitions, imports, calls, confidence = self._index(source)
        exact = [item for item in definitions if item["qualified_name"] == symbol]
        matching = exact or [item for item in definitions if item["name"] == symbol.split(".")[-1]]
        selected = matching[:8]
        names = {item["qualified_name"] for item in matching}
        matched_calls = [
            call
            for call in calls
            if call["callee"] == symbol
            or call["callee"].split(".")[-1] == symbol.split(".")[-1]
            or call["caller"] in names
            or any(call["caller"].startswith(name + ".") for name in names)
        ]
        # Include incoming goal calls before sampling a long function's outgoing calls.
        call_sites = sorted(
            matched_calls,
            key=lambda item: (
                item["callee"].split(".")[-1] != symbol.split(".")[-1],
                item["start_line"],
            ),
        )[:12]
        evidence_ids: list[str] = []
        warnings = [
            "Call sites are syntax observations, not a resolved runtime call graph.",
            "Imports identify candidate files; dynamic dispatch and external code "
            "remain unverified.",
        ]
        for index, definition in enumerate(selected):
            evidence = self._goal_cite(
                source,
                definition["start_line"],
                min(definition["end_line"], definition["start_line"] + 79),
                limit=8,
                allow_new=index < 2,
            )
            definition["path"] = source.path
            self._attach_citation(definition, evidence, evidence_ids)

        # Nearby calls share one exact snippet instead of consuming a citation per call.
        groups: list[list[dict]] = []
        for call in sorted(call_sites, key=lambda item: item["start_line"]):
            call["path"] = source.path
            if groups and call["start_line"] - groups[-1][0]["start_line"] < MAX_READ_LINES:
                groups[-1].append(call)
            else:
                groups.append([call])
        groups.sort(
            key=lambda group: (
                not any(item["callee"].split(".")[-1] == symbol.split(".")[-1] for item in group),
                group[0]["start_line"],
            )
        )
        for index, group in enumerate(groups):
            first_line = group[0]["start_line"]
            end_line = min(max(item["end_line"] for item in group), first_line + MAX_READ_LINES - 1)
            evidence = self._goal_cite(source, first_line, end_line, limit=8, allow_new=index < 2)
            for call in group:
                self._attach_citation(call, evidence, evidence_ids)
                if evidence and call["end_line"] > evidence["end_line"]:
                    call["citation_partial"] = True

        roots = {call["callee"].split(".")[0] for call in call_sites} | {symbol.split(".")[0]}
        relevant_imports = [item for item in imports if item["alias"] in roots]
        candidate_modules = []
        candidate_citation_index = 0
        candidate_definitions_truncated = False
        for import_index, imported in enumerate(relevant_imports[:6]):
            candidates = self._module_candidates(source.path, imported)
            import_evidence = self._goal_cite(
                source,
                imported["line"],
                min(imported["end_line"], imported["line"] + MAX_READ_LINES - 1),
                limit=8,
                allow_new=import_index < 2,
            )
            module = {
                **imported,
                "candidate_paths": candidates,
                "resolution": "path_candidates_only",
            }
            self._attach_citation(module, import_evidence, evidence_ids)
            candidate_modules.append(module)
            wanted_names = {imported["name"] or symbol.split(".")[-1]}
            for call in call_sites:
                prefix = imported["alias"] + "."
                if call["callee"].startswith(prefix):
                    tail = call["callee"][len(prefix) :]
                    wanted_names.add(tail)
                    wanted_names.add(tail.split(".")[0])
                    if imported["name"]:
                        wanted_names.add(imported["name"] + "." + tail)
            for path in candidates:
                if self._new_files >= MAX_NEW_FILES_PER_ACTION and path not in self.files:
                    warnings.append("Cross-file exploration reached this action's file limit.")
                    break
                try:
                    candidate = self._load(path)
                    other_definitions, _, _, _ = self._index(candidate)
                except ToolBudgetExceeded:
                    warnings.append(
                        "Cross-file exploration reached the remaining file/byte budget."
                    )
                    break
                except ToolError as error:
                    warnings.append(f"Skipped candidate {path}: {error.code}.")
                    continue
                for definition in other_definitions:
                    if definition["qualified_name"] not in wanted_names:
                        continue
                    if len(selected) >= 12:
                        candidate_definitions_truncated = True
                        break
                    evidence = self._goal_cite(
                        candidate,
                        definition["start_line"],
                        definition["start_line"],
                        limit=8,
                        allow_new=candidate_citation_index < 2,
                    )
                    candidate_citation_index += 1
                    item = {**definition, "path": path}
                    self._attach_citation(item, evidence, evidence_ids)
                    selected.append(item)
        evidence_limited = any(
            item.get("citation_status") == "index_only"
            for item in [*selected, *call_sites, *candidate_modules]
        )
        if evidence_limited:
            warnings.append(
                "Some relationships are uncited navigation metadata. Read the displayed "
                "goal-selected ranges before using them as report evidence."
            )
        return {
            "kind": "trace",
            "path": source.path,
            "symbol": symbol,
            "definitions": selected,
            "call_sites": call_sites,
            "candidate_modules": candidate_modules,
            "evidence_ids": list(dict.fromkeys(evidence_ids)),
            "confidence": confidence,
            "definitions_truncated": len(matching) > 8 or candidate_definitions_truncated,
            "call_sites_truncated": len(matched_calls) > 12,
            "imports_truncated": len(relevant_imports) > 6,
            "new_citations": self._new_citations,
            "evidence_limited": evidence_limited,
            "warnings": list(dict.fromkeys(warnings)),
        }
