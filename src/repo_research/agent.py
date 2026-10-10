"""One bounded research agent: model decisions, deterministic tools, durable graph.

No target code is run. All node outputs are JSON-serializable; failures and spent
budgets are checkpointed before the recovery interrupt asks for a manual retry.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from .config import Settings
from .github import GitHubClient, GitHubError
from .llm import BudgetExceeded, ModelError, make_model
from .models import (
    Budget,
    CapabilityReport,
    DraftClaim,
    ReportDraft,
    RepositoryInfo,
    ResearchRequest,
    ToolAction,
    TreeListing,
    Usage,
)
from .report import finalize_report
from .tools import ResearchTools, ToolBudgetExceeded, ToolError, eligible_path


class ResearchState(TypedDict, total=False):
    request: dict
    budget: dict
    usage: dict
    repository: dict | None
    tree: dict
    files: dict
    evidence: list[dict]
    observations: list[dict]
    attempted: list[str]
    plan: list[str]
    decision: dict | None
    draft: dict
    report: dict | None
    report_markdown: str
    progress: list[dict]
    status: str
    stop_reason: str
    error: str | None
    failed_node: str
    capabilities: dict | None
    warnings: list[str]
    stalled: int


def effective_budget(request: ResearchRequest) -> Budget:
    if request.budget is not None:
        return request.budget
    if request.depth == "quick":
        return Budget(
            max_iterations=6,
            max_files=6,
            max_api_requests=20,
            max_llm_calls=11,
            max_tokens=40_000,
            max_bytes=180_000,
        )
    if request.depth == "deep":
        return Budget(
            max_iterations=24,
            max_files=32,
            max_api_requests=100,
            max_llm_calls=32,
            max_tokens=160_000,
            max_bytes=900_000,
        )
    return Budget()


def initial_state(request: ResearchRequest, budget: Budget) -> ResearchState:
    return ResearchState(
        request=request.model_dump(),
        budget=budget.model_dump(),
        usage=Usage().model_dump(),
        repository=None,
        tree=TreeListing().model_dump(),
        files={},
        evidence=[],
        observations=[],
        attempted=[],
        plan=[],
        decision=None,
        progress=[],
        status="running",
        stop_reason="",
        error=None,
        failed_node="",
        capabilities=None,
        warnings=[],
        stalled=0,
        report=None,
        report_markdown="",
    )


class ResearchRunner:
    def __init__(
        self,
        settings: Settings,
        checkpoint_path: Path,
        pause_event: threading.Event | None = None,
        client_factory: Callable | None = None,
        model_factory: Callable | None = None,
    ) -> None:
        self.settings = settings
        self.checkpoint_path = Path(checkpoint_path)
        self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        self.pause_event = pause_event or threading.Event()
        self.client_factory = client_factory or GitHubClient
        self.model_factory = model_factory or make_model
        self.client: Any = None
        self.model: Any = None
        self.tools: ResearchTools | None = None
        self.usage = Usage()
        self.budget = Budget()
        self._journal: sqlite3.Connection | None = None
        self._journal_lock = threading.RLock()
        self._task_id = ""

    def _persist_usage(self, usage: Usage | None = None) -> None:
        if self._journal is None:
            return
        if usage is not None:
            self.usage = usage
        self.usage.api_requests = self.client.request_count
        self.usage.cache_hits = self.client.cache_hits
        with self._journal_lock:
            self._journal.execute(
                "INSERT INTO usage_ledger(task_id, data) VALUES (?, ?) "
                "ON CONFLICT(task_id) DO UPDATE SET data=excluded.data",
                (self._task_id, self.usage.model_dump_json()),
            )
            self._journal.commit()

    def _request_reserved(self, requests: int, hits: int) -> None:
        self.usage.api_requests = requests
        self.usage.cache_hits = hits
        self._persist_usage()

    def _restore(self, state: ResearchState) -> None:
        """Rebuild ephemeral adapters from durable data without resetting counters."""
        self.budget = Budget.model_validate(state["budget"])
        self.usage = Usage.model_validate(state["usage"])
        if self._journal is not None:
            row = self._journal.execute(
                "SELECT data FROM usage_ledger WHERE task_id=?",
                (self._task_id,),
            ).fetchone()
            if row:
                # A request reservation can be newer than the last completed graph node.
                self.usage = Usage.model_validate_json(row[0])
        self.client.request_count = self.usage.api_requests
        self.client.cache_hits = self.usage.cache_hits
        capability = state.get("capabilities")
        if self.model is None:
            self.model = self.model_factory(
                self.settings,
                self.budget,
                self.usage,
                CapabilityReport.model_validate(capability) if capability else None,
            )
        else:
            self.model.budget, self.model.usage = self.budget, self.usage
            if capability:
                self.model.capabilities = CapabilityReport.model_validate(capability)
        self.model.on_usage = self._persist_usage
        self.client.on_request = self._request_reserved
        self.tools = None
        if state.get("repository"):
            repo = RepositoryInfo.model_validate(state["repository"])
            tree = TreeListing.model_validate(state["tree"])
            restore_tree = getattr(self.client, "restore_tree", None)
            if callable(restore_tree):
                restore_tree(repo, tree)
            self.tools = ResearchTools(
                self.client,
                repo,
                tree,
                self.budget,
                self.usage,
                files=dict(state.get("files", {})),
                evidence=list(state.get("evidence", [])),
            )

    def _account(self) -> dict:
        self.usage.api_requests = self.client.request_count
        self.usage.cache_hits = self.client.cache_hits
        self._persist_usage()
        result = {"usage": self.usage.model_dump()}
        if self.model is not None:
            result["capabilities"] = self.model.capabilities.model_dump()
        if self.tools is not None:
            result.update(files=self.tools.files, evidence=self.tools.evidence)
        return result

    def _event(self, state: ResearchState, node: str, message: str, **updates) -> dict:
        event = {"node": node, "message": message, "time": datetime.now(UTC).isoformat()}
        return {
            **self._account(),
            "progress": [*state.get("progress", []), event][-160:],
            **updates,
        }

    def _context(self, state: ResearchState) -> dict:
        """Expose a bounded goal-ranked view, keeping the full ledger in checkpoints."""
        question = state["request"]["question"]
        terms = re.findall(r"[a-z_][a-z_0-9]{2,}", question.lower())
        for keyword, related in {
            "工具": ["tool", "invoke"],
            "状态": ["state", "graph"],
            "记忆": ["memory", "store"],
            "入口": ["main", "cli"],
            "架构": ["core", "app"],
        }.items():
            if keyword in question:
                terms.extend(related)
        entries = [
            e for e in state["tree"]["entries"] if e["type"] == "blob" and eligible_path(e["path"])
        ]
        entries.sort(
            key=lambda e: (
                -sum(term in e["path"].lower() for term in terms if len(term) > 2),
                e["path"].count("/"),
                e["path"],
            )
        )
        # Absolute byte bound also prevents unusual long file names bloating prompts.
        view: list[dict] = []
        size = 0
        for entry in entries:
            item = {"path": entry["path"], "size": entry["size"], "type": "blob"}
            size += len(json.dumps(item, ensure_ascii=False).encode())
            if size > 10_000 or len(view) >= 120:
                break
            view.append(item)
        # Evidence excerpts stay exact; omission is explicit and never silently truncated.
        evidence = []
        size = 0
        for item in reversed(state.get("evidence", [])):
            item_size = len(json.dumps(item, ensure_ascii=False).encode())
            if size + item_size > 16_000:
                continue
            size += item_size
            evidence.append(item)
        observations = []
        size = 0
        for item in reversed(state.get("observations", [])[-6:]):
            encoded = json.dumps(item, ensure_ascii=False)
            size += len(encoded.encode())
            if size <= 8000:
                observations.append(item)
        return {
            "question": question,
            "repo": state.get("repository"),
            "tree": view,
            "tree_view_partial": len(view) < len(entries) or state["tree"]["truncated"],
            "tree_total_listed": len(entries),
            "plan": state.get("plan", []),
            "evidence": list(reversed(evidence)),
            "observations": list(reversed(observations)),
            "attempted": state.get("attempted", []),
            "files": list(state.get("files", {})),
            "remaining": {
                "iterations": self.budget.max_iterations - self.usage.iterations,
                "files": self.budget.max_files - self.usage.files,
                "api_requests": self.budget.max_api_requests - self.usage.api_requests,
                "tokens": self.budget.max_tokens
                - self.usage.input_tokens
                - self.usage.output_tokens
                - self.usage.reserved_tokens,
            },
            "stop_reason": state.get("stop_reason", ""),
            "step": self.usage.iterations,
        }

    def _initialize(self, state: ResearchState) -> dict:
        pin = (
            self._journal.execute(
                "SELECT data FROM repo_pins WHERE task_id=?",
                (self._task_id,),
            ).fetchone()
            if self._journal
            else None
        )
        repo = (
            RepositoryInfo.model_validate(state["repository"])
            if state.get("repository")
            else RepositoryInfo.model_validate_json(pin[0])
            if pin
            else self.client.resolve(state["request"]["repo_url"], state["request"].get("ref"))
        )
        if self._journal is not None:
            self._journal.execute(
                "INSERT OR IGNORE INTO repo_pins(task_id,data) VALUES (?,?)",
                (self._task_id, repo.model_dump_json()),
            )
            self._journal.commit()
        # If tree fetching fails, pinning is preserved via error wrapper below.
        self._pending_repo = repo.model_dump()
        tree = self.client.tree(repo, max_entries=self.budget.max_tree_entries)
        return self._event(
            state,
            "initialize",
            f"固定版本 {repo.commit_sha[:12]}；目录包含 {len(tree.entries)} 项。",
            repository=repo.model_dump(),
            tree=tree.model_dump(),
            warnings=tree.warnings,
            status="running",
            error=None,
            failed_node="",
        )

    def _gate(self, state: ResearchState) -> dict:
        if self.pause_event.is_set():
            interrupt({"reason": "user_pause", "message": "已在节点边界保存，可继续。"})
            self.pause_event.clear()
        return {"status": "running", "error": None}

    def _decide(self, state: ResearchState) -> dict:
        if self.usage.iterations >= self.budget.max_iterations:
            return self._event(
                state,
                "decide",
                "达到最大研究迭代次数。",
                stop_reason="iteration_budget",
                decision=None,
            )
        if self.usage.api_requests >= self.budget.max_api_requests and not state["files"]:
            return self._event(
                state, "decide", "API 预算已耗尽。", stop_reason="api_budget", decision=None
            )
        decision = self.model.decide(self._context(state))
        action_key = json.dumps(decision.action.model_dump(), sort_keys=True)
        if decision.action.kind == "finish":
            return self._event(
                state,
                "decide",
                decision.rationale,
                decision=decision.model_dump(),
                plan=decision.plan,
                stop_reason="model_sufficient",
            )
        if action_key in state["attempted"]:
            return self._event(
                state,
                "decide",
                "模型重复已完成操作，结束以避免循环。",
                decision=None,
                stop_reason="repeated_action",
            )
        return self._event(
            state, "decide", decision.rationale, decision=decision.model_dump(), plan=decision.plan
        )

    def _explore(self, state: ResearchState) -> dict:
        assert self.tools is not None
        if self.usage.iterations >= self.budget.max_iterations:
            return self._event(
                state, "explore", "恢复操作已达到迭代预算。", stop_reason="iteration_budget"
            )
        action = ToolAction.model_validate(state["decision"]["action"])
        key = json.dumps(action.model_dump(), sort_keys=True)
        before = (len(self.tools.files), len(self.tools.evidence))
        self.usage.iterations += 1
        self._persist_usage()
        try:
            observation = self.tools.execute(action)
        except ToolError as exc:
            if isinstance(exc, ToolBudgetExceeded):
                return self._event(
                    state,
                    "explore",
                    str(exc),
                    stop_reason="tool_budget",
                    attempted=[*state["attempted"], key],
                )
            observation = {"action": action.model_dump(), "error": str(exc), "code": exc.code}
        except GitHubError as exc:
            if exc.code in {
                "file_too_large",
                "binary_file",
                "invalid_path",
                "not_a_file",
                "unsupported_encoding",
                "invalid_response",
                "not_found",
            }:
                observation = {"action": action.model_dump(), "error": str(exc), "code": exc.code}
            else:
                raise
        new_information = before != (len(self.tools.files), len(self.tools.evidence))
        # Symbols/trace reveal new relationships without downloading files.
        new_information |= bool(
            observation.get("definitions")
            or observation.get("call_sites")
            or observation.get("matches")
            or observation.get("imports")
        )
        return self._event(
            state,
            "explore",
            f"{action.kind}: {action.path or action.query or action.symbol}",
            observations=[*state["observations"], observation][-40:],
            attempted=[*state["attempted"], key],
            stalled=0 if new_information else state.get("stalled", 0) + 1,
        )

    def _assess(self, state: ResearchState) -> dict:
        reason = state.get("stop_reason", "")
        if self.usage.iterations >= self.budget.max_iterations:
            reason = reason or "iteration_budget"
        if state.get("stalled", 0) >= 3:
            reason = reason or "no_new_information"
        return self._event(state, "assess", "整理证据并检查继续探索的价值。", stop_reason=reason)

    def _fallback_draft(self, state: ResearchState, reason: str) -> ReportDraft:
        # Deterministic fallback is not secretly another model/provider.
        claims = [
            DraftClaim(
                section="已保存的源码位置",
                category="fact",
                text=f"已读取 {item['path']} 第 {item['start_line']}–{item['end_line']} 行。"
                "此条仅确认源码位置，不确认实现机制。",
                evidence_ids=[item["id"]],
            )
            for item in state.get("evidence", [])[:12]
        ]
        claims.append(DraftClaim(section="未确认事项", category="unknown", text=reason))
        return ReportDraft(
            title="有限证据研究报告",
            claims=claims,
            limitations=[reason, *state.get("warnings", [])][:30],
        )

    def _draft(self, state: ResearchState) -> dict:
        try:
            draft = self.model.write_report(self._context(state))
        except BudgetExceeded:
            draft = self._fallback_draft(state, "模型预算耗尽；保存真实引用，尚未完成语义研究。")
        except ModelError:
            # Retrying report generation is explicit via recovery; never hide outage.
            raise
        return self._event(
            state, "draft", "生成结论草稿；下一步逐项校验源码引用。", draft=draft.model_dump()
        )

    def _verify(self, state: ResearchState) -> dict:
        draft = ReportDraft.model_validate(state["draft"])
        draft.limitations = [*state.get("warnings", []), *draft.limitations][:30]
        result = finalize_report(
            draft,
            RepositoryInfo.model_validate(state["repository"]) if state.get("repository") else None,
            state["files"],
            state["evidence"],
            state["request"]["question"],
            self.usage,
            state.get("stop_reason", ""),
            self.settings.model_mode,
        )
        return self._event(
            state,
            "verify",
            f"校验完成；有效引用 {len(result['evidence'])} 条。",
            report=result["draft"],
            report_markdown=result["markdown"],
            evidence=result["evidence"],
            status="completed",
            error=None,
        )

    def _recover(self, state: ResearchState) -> dict:
        interrupt(
            {"reason": "recoverable_error", "node": state["failed_node"], "message": state["error"]}
        )
        return {"status": "running", "error": None}

    def _wrapped(self, node: str, fn: Callable) -> Callable:
        def execute(state: ResearchState) -> dict:
            self._restore(state)
            try:
                return fn(state)
            except (BudgetExceeded, ToolBudgetExceeded) as exc:
                return self._event(
                    state,
                    node,
                    str(exc),
                    stop_reason="budget_exhausted",
                    error=None,
                    decision=None,
                    draft=self._fallback_draft(state, str(exc)).model_dump(),
                )
            except GitHubError as exc:
                changes = {}
                if node == "initialize" and getattr(self, "_pending_repo", None):
                    changes["repository"] = self._pending_repo
                if exc.code == "request_budget":
                    return self._event(
                        state,
                        node,
                        str(exc),
                        stop_reason="api_budget",
                        draft=self._fallback_draft(state, str(exc)).model_dump(),
                        **changes,
                    )
                if exc.retryable:
                    return self._event(
                        state,
                        node,
                        str(exc),
                        status="interrupted",
                        error=str(exc),
                        failed_node=node,
                        **changes,
                    )
                return self._event(
                    state, node, str(exc), status="failed", error=str(exc), **changes
                )
            except ModelError as exc:
                return self._event(
                    state, node, str(exc), status="interrupted", error=str(exc), failed_node=node
                )

        return execute

    def _route(self, state: ResearchState, normal: str) -> str:
        if state.get("status") == "failed":
            return END
        if state.get("error"):
            return "recover"
        if state.get("stop_reason") and normal in {"gate", "explore"}:
            return "draft" if state.get("repository") else "verify"
        return normal

    def _build(self, saver: SqliteSaver):
        builder = StateGraph(ResearchState)
        for name, fn in (
            ("initialize", self._initialize),
            ("gate", self._gate),
            ("decide", self._decide),
            ("explore", self._explore),
            ("assess", self._assess),
            ("draft", self._draft),
            ("verify", self._verify),
            ("recover", self._recover),
        ):
            builder.add_node(name, self._wrapped(name, fn))
        builder.add_edge(START, "initialize")
        for name, normal in (
            ("initialize", "gate"),
            ("gate", "decide"),
            ("decide", "explore"),
            ("explore", "assess"),
            ("assess", "gate"),
            ("draft", "verify"),
        ):
            builder.add_conditional_edges(name, lambda s, normal=normal: self._route(s, normal))
        builder.add_conditional_edges("recover", lambda s: s["failed_node"])
        builder.add_edge("verify", END)
        return builder.compile(checkpointer=saver)

    def run(
        self,
        task_id: str,
        request: ResearchRequest,
        budget: Budget,
        resume: bool = False,
        on_update: Callable[[dict], None] | None = None,
    ) -> dict:
        self.client = self.client_factory(
            cache_path=self.settings.data_dir / "github-cache.sqlite",
            token=(self.settings.github_token.get_secret_value()),
            timeout=self.settings.github_timeout,
            max_requests=budget.max_api_requests,
        )
        connection = sqlite3.connect(self.checkpoint_path, check_same_thread=False, timeout=30)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=30000")
        self._task_id = task_id
        self._journal = sqlite3.connect(
            self.checkpoint_path.parent / "usage-ledger.sqlite3",
            timeout=30,
            check_same_thread=False,
        )
        self._journal.execute("PRAGMA journal_mode=WAL")
        self._journal.execute("PRAGMA synchronous=FULL")
        self._journal.execute("PRAGMA busy_timeout=30000")
        self._journal.execute(
            "CREATE TABLE IF NOT EXISTS usage_ledger (task_id TEXT PRIMARY KEY, data TEXT NOT NULL)"
        )
        self._journal.execute(
            "CREATE TABLE IF NOT EXISTS repo_pins (task_id TEXT PRIMARY KEY, data TEXT NOT NULL)"
        )
        self._journal.commit()
        saver = SqliteSaver(connection)
        graph = self._build(saver)
        config = {
            "configurable": {"thread_id": task_id},
            "recursion_limit": budget.max_iterations * 5 + 30,
        }
        try:
            if resume:
                snapshot = graph.get_state(config)
                if not snapshot.values:
                    # Queued task may never have reached START before process shutdown.
                    input_value = initial_state(request, budget)
                elif not snapshot.next:
                    raise ValueError("No pending LangGraph checkpoint exists for this task.")
                else:
                    input_value = Command(resume=True) if snapshot.interrupts else None
            else:
                input_value = initial_state(request, budget)
            for state in graph.stream(input_value, config, stream_mode="values", durability="sync"):
                if on_update:
                    on_update(dict(state))
            snapshot = graph.get_state(config)
            result = dict(snapshot.values)
            if snapshot.interrupts:
                result["status"] = "interrupted" if result.get("error") else "paused"
            if on_update:
                on_update(result)
            return result
        finally:
            connection.close()
            if self._journal is not None:
                self._journal.close()
                self._journal = None
            self.close()

    def close(self) -> None:
        if self.client is not None:
            self.client.close()
            self.client = None
        if self.model is not None:
            close = getattr(self.model, "close", None)
            if callable(close):
                close()
            self.model = None
