"""JSON-serializable contracts shared by tools, graph, model and API."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Budget(Contract):
    max_iterations: int = Field(default=12, ge=1, le=40)
    max_files: int = Field(default=16, ge=1, le=80)
    max_bytes: int = Field(default=400_000, ge=1000, le=4_000_000)
    max_api_requests: int = Field(default=50, ge=3, le=300)
    max_llm_calls: int = Field(default=18, ge=0, le=60)
    max_tokens: int = Field(default=80_000, ge=100, le=500_000)
    max_cost_usd: float = Field(default=2, ge=0, le=100)
    max_tree_entries: int = Field(default=6000, ge=10, le=30_000)
    max_evidence: int = Field(default=40, ge=1, le=100)


class Usage(Contract):
    iterations: int = 0
    files: int = 0
    bytes: int = 0
    api_requests: int = 0
    cache_hits: int = 0
    llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    reserved_tokens: int = 0
    cost_usd: float = 0
    cost_known: bool = False


class ResearchRequest(Contract):
    repo_url: str = Field(min_length=10, max_length=300)
    question: str = Field(min_length=3, max_length=4000)
    ref: str | None = Field(default=None, max_length=200)
    depth: Literal["quick", "standard", "deep"] = "standard"
    budget: Budget | None = None

    @field_validator("repo_url", "question", mode="before")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()


class RepositoryInfo(Contract):
    owner: str
    name: str
    full_name: str
    url: str
    description: str = ""
    default_branch: str
    commit_sha: str
    tree_sha: str
    language: str | None = None
    stars: int = 0
    requested_ref: str | None = None


class TreeEntry(Contract):
    path: str
    type: Literal["blob", "tree", "commit"] = "blob"
    sha: str
    size: int = 0


class TreeListing(Contract):
    entries: list[TreeEntry] = Field(default_factory=list)
    truncated: bool = False
    warnings: list[str] = Field(default_factory=list)


class SourceFile(Contract):
    path: str
    text: str
    blob_sha: str
    size: int
    total_lines: int


class ToolAction(Contract):
    kind: Literal["read", "search", "symbols", "trace", "finish"]
    path: str = ""
    query: str = ""
    symbol: str = ""
    start_line: int = Field(default=1, ge=1)
    end_line: int = Field(default=160, ge=1)


class Decision(Contract):
    action: ToolAction
    rationale: str = Field(max_length=1500)
    plan: list[str] = Field(default_factory=list, max_length=12)


class Evidence(Contract):
    id: str
    repository: str
    commit_sha: str
    path: str
    start_line: int
    end_line: int
    excerpt: str
    blob_sha: str
    url: str
    kind: Literal["code", "documentation", "configuration"] = "code"
    verified: bool = False


class DraftClaim(Contract):
    section: str = Field(default="关键发现", max_length=100)
    category: Literal["fact", "inference", "unknown"]
    text: str = Field(min_length=1, max_length=3000)
    evidence_ids: list[str] = Field(default_factory=list, max_length=8)


class ReportDraft(Contract):
    title: str = Field(default="仓库研究报告", max_length=200)
    summary: str = Field(default="", max_length=4000)
    claims: list[DraftClaim] = Field(default_factory=list, max_length=40)
    limitations: list[str] = Field(default_factory=list, max_length=30)


class CapabilityReport(Contract):
    mode: str
    chat: bool = False
    tool_calling: bool = False
    structured_output: bool = False
    selected_output: str = "json_text"
    tested: bool = False
    details: list[str] = Field(default_factory=list)

