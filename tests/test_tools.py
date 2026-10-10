"""Offline static exploration tests; repository code is never executed."""

import json

import pytest

from repo_research.models import (
    Budget,
    RepositoryInfo,
    SourceFile,
    ToolAction,
    TreeEntry,
    TreeListing,
    Usage,
)
from repo_research.tools import ResearchTools, ToolBudgetExceeded, ToolError, source_lines

SHA = "a" * 40
SOURCE = (
    "from pkg.memory import Memory\n"
    "\n"
    "def invoke(tool, state):\n"
    "    result = tool(state)\n"
    "    return result\n"
    "\n"
    "class Agent:\n"
    "    def run(self, state):\n"
    "        return invoke(self.tool, state)\n"
)


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


class DummyClient:
    """An immutable, in-memory GitHub replacement; has no external side effects."""

    def __init__(self, sources):
        self.sources = sources
        self.calls = []

    def read_file(self, repo, path, *, max_bytes=200_000, **kwargs):
        self.calls.append((repo.commit_sha, path, max_bytes))
        return self.sources[path]


def source(path, text=SOURCE):
    return SourceFile(
        path=path,
        text=text,
        blob_sha="b" * 40,
        size=len(text.encode()),
        total_lines=len(source_lines(text)),
    )


def make_tools(repo, sources=None, *, budget=None, entries=None, usage=None):
    sources = sources or {"src/agent.py": source("src/agent.py")}
    client = DummyClient(sources)
    tree = TreeListing(
        entries=entries
        or [
            TreeEntry(path=file.path, sha=file.blob_sha, size=file.size)
            for file in sources.values()
        ]
    )
    usage = usage or Usage()
    tools = ResearchTools(client, repo, tree, budget or Budget(), usage)
    return tools, client, usage


def dump(value):
    return json.dumps(value, ensure_ascii=False, default=str)


def many_functions(count=64):
    return "\n".join(f"def function_{index}():\n    return {index}\n" for index in range(count))


def assert_observation_citations_resolve(result, tools):
    """Every emitted identifier resolves to an exact excerpt from a loaded file."""
    by_id = {item["id"]: item for item in tools.evidence}
    assert set(result["evidence_ids"]) <= by_id.keys()
    for identifier in result["evidence_ids"]:
        item = by_id[identifier]
        file = tools.files[item["path"]]
        assert item["excerpt"] == "\n".join(
            source_lines(file["text"])[item["start_line"] - 1 : item["end_line"]]
        )
        assert item["commit_sha"] == SHA
    for key in ("definitions", "call_sites", "candidate_modules"):
        for relation in result.get(key, []):
            identifier = relation.get("evidence_id")
            if identifier:
                assert identifier in result["evidence_ids"]
            else:
                assert relation["citation_status"] == "index_only"


def test_read_produces_exact_numbered_evidence_and_fixed_commit(repo):
    tools, client, usage = make_tools(repo)
    result = tools.execute(ToolAction(kind="read", path="src/agent.py", start_line=3, end_line=5))
    evidence = result["evidence"]
    assert evidence["excerpt"] == "\n".join(SOURCE.splitlines()[2:5])
    assert evidence["start_line"] == 3
    assert evidence["end_line"] == 5
    assert evidence["commit_sha"] == SHA
    assert f"/blob/{SHA}/src/agent.py#L3-L5" in evidence["url"]
    assert usage.files == 1
    assert usage.bytes == len(SOURCE.encode())
    assert client.calls[0][0] == SHA


@pytest.mark.parametrize(
    "path,kind",
    [
        ("README.md", "documentation"),
        ("docs/architecture.rst", "documentation"),
        ("requirements.txt", "configuration"),
        ("pyproject.toml", "configuration"),
        ("package.json", "configuration"),
        ("src/agent.py", "code"),
    ],
)
def test_citation_category_is_derived_from_source_path(repo, path, kind):
    tools, _, _ = make_tools(repo, {path: source(path)})
    result = tools.execute(ToolAction(kind="read", path=path))
    assert result["evidence"]["kind"] == kind


def test_unicode_separators_and_crlf_do_not_forge_github_line_numbers(repo):
    text = "value = 'one\u2028two\vthree\ffour'\r\nnext_value = 2\r\n"
    tools, _, _ = make_tools(repo, {"src/agent.py": source("src/agent.py", text)})
    read = tools.execute(ToolAction(kind="read", path="src/agent.py", end_line=1))
    assert read["total_lines"] == 2
    assert read["evidence"]["excerpt"] == text.split("\n")[0]
    assert read["evidence"]["url"].endswith("#L1")
    search = tools.execute(ToolAction(kind="search", query="next_value"))
    assert search["matches"][0]["line"] == 2
    assert search["matches"][0]["text"] == "next_value = 2\r"


def test_repeat_read_reuses_loaded_source_without_download_or_double_budget(repo):
    tools, client, usage = make_tools(repo)
    tools.execute(ToolAction(kind="read", path="src/agent.py", start_line=1, end_line=3))
    tools.execute(ToolAction(kind="read", path="src/agent.py", start_line=4, end_line=5))
    assert len(client.calls) == 1
    assert usage.files == 1
    assert usage.bytes == len(SOURCE.encode())
    assert len(tools.files) == 1


def test_repeat_identical_evidence_does_not_grow_evidence_list(repo):
    tools, _, _ = make_tools(repo)
    action = ToolAction(kind="read", path="src/agent.py", start_line=3, end_line=5)
    first = tools.execute(action)
    second = tools.execute(action)
    assert first["evidence"]["id"] == second["evidence"]["id"]
    assert len(tools.evidence) == 1


def test_serialized_tool_state_preserves_budget_and_citations_after_recovery(repo):
    sources = {"src/agent.py": source("src/agent.py"), "src/memory.py": source("src/memory.py")}
    budget = Budget(max_files=1)
    tools, client, usage = make_tools(repo, sources, budget=budget)
    action = ToolAction(kind="read", path="src/agent.py", start_line=3, end_line=5)
    first = tools.execute(action)
    restored_usage = Usage.model_validate_json(usage.model_dump_json())
    restored = ResearchTools(
        client,
        repo,
        tools.tree,
        budget,
        restored_usage,
        files=json.loads(json.dumps(tools.files)),
        evidence=json.loads(json.dumps(tools.evidence)),
    )
    again = restored.execute(action)
    with pytest.raises(ToolBudgetExceeded):
        restored.execute(ToolAction(kind="read", path="src/memory.py"))
    assert len(client.calls) == 1
    assert restored_usage.files == 1
    assert restored_usage.bytes == usage.bytes
    assert again["evidence"]["id"] == first["evidence"]["id"]


def test_symbols_reads_ast_without_importing_or_executing_repository(repo, tmp_path):
    marker = tmp_path / "untrusted_was_executed"
    text = SOURCE + f"\nopen({str(marker)!r}, 'w').write('unsafe')\n"
    tools, _, _ = make_tools(repo, {"src/agent.py": source("src/agent.py", text)})
    result = tools.execute(ToolAction(kind="symbols", path="src/agent.py"))
    assert "invoke" in dump(result["definitions"])
    assert "Agent" in dump(result["definitions"])
    assert "pkg.memory" in dump(result["imports"])
    assert result["confidence"] == "static_syntax"
    assert result["evidence_ids"]
    assert not marker.exists()


def test_symbols_paginate_large_files_without_repeated_downloads(repo):
    text = "\n".join(f"def function_{index}():\n    return {index}" for index in range(40))
    tools, client, _ = make_tools(repo, {"src/agent.py": source("src/agent.py", text)})
    first = tools.execute(ToolAction(kind="symbols", path="src/agent.py"))
    assert first["definition_count"] == 40
    assert first["has_more"] is True
    assert len(first["definitions"]) == 16
    next_page = tools.execute(
        ToolAction(
            kind="symbols",
            path="src/agent.py",
            start_line=first["next_start_line"],
        )
    )
    assert next_page["definitions"][0]["name"] == "function_16"
    assert set(first["evidence_ids"]).isdisjoint(next_page["evidence_ids"])
    assert len(client.calls) == 1


def test_four_symbol_pages_leave_evidence_budget_for_targeted_implementation_trace(repo):
    text = many_functions()
    tools, client, _ = make_tools(repo, {"src/agent.py": source("src/agent.py", text)})
    start_line = 1
    names = []
    metadata_only = []
    for page_number in range(4):
        before = len(tools.evidence)
        page = tools.execute(ToolAction(kind="symbols", path="src/agent.py", start_line=start_line))
        assert len(page["definitions"]) == 16
        assert len(tools.evidence) - before <= 4
        assert_observation_citations_resolve(page, tools)
        names.extend(item["name"] for item in page["definitions"])
        metadata_only.extend(item for item in page["definitions"] if not item.get("evidence_id"))
        if page_number < 3:
            assert page["has_more"] is True
            start_line = page["next_start_line"]
        else:
            assert page["has_more"] is False
    assert names == [f"function_{index}" for index in range(64)]
    assert metadata_only
    assert len(tools.evidence) <= 16 < tools.budget.max_evidence
    assert len(client.calls) == 1

    trace = tools.execute(ToolAction(kind="trace", path="src/agent.py", symbol="function_63"))
    target = next(item for item in trace["definitions"] if item["name"] == "function_63")
    assert target["evidence_id"]
    by_id = {item["id"]: item for item in tools.evidence}
    assert "return 63" in by_id[target["evidence_id"]]["excerpt"]
    assert_observation_citations_resolve(trace, tools)
    assert len(client.calls) == 1


def test_repeated_and_restored_symbol_index_does_not_spend_more_citations(repo):
    text = many_functions()
    tools, client, usage = make_tools(repo, {"src/agent.py": source("src/agent.py", text)})
    action = ToolAction(kind="symbols", path="src/agent.py")
    first = tools.execute(action)
    first_ids = list(first["evidence_ids"])
    count = len(tools.evidence)
    assert 0 < count <= 4
    second = tools.execute(action)
    assert second["evidence_ids"] == first_ids
    assert len(tools.evidence) == count

    restored = ResearchTools(
        client,
        repo,
        tools.tree,
        tools.budget,
        Usage.model_validate_json(usage.model_dump_json()),
        files=json.loads(json.dumps(tools.files)),
        evidence=json.loads(json.dumps(tools.evidence)),
    )
    third = restored.execute(action)
    assert third["evidence_ids"] == first_ids
    assert len(restored.evidence) == count
    assert len(client.calls) == 1
    assert_observation_citations_resolve(third, restored)


def test_targeted_symbol_index_cites_bounded_subset_of_matching_definitions(repo):
    text = many_functions(32)
    tools, _, _ = make_tools(repo, {"src/agent.py": source("src/agent.py", text)})
    result = tools.execute(ToolAction(kind="symbols", path="src/agent.py", query="function_"))
    assert len(result["definitions"]) == 16
    assert 0 < len(tools.evidence) <= 6
    assert any(item.get("citation_status") == "index_only" for item in result["definitions"])
    assert_observation_citations_resolve(result, tools)


def test_targeted_symbols_find_implementation_beyond_initial_read_range(repo):
    text = "# introductory comment\n" * 220 + "def target():\n    return 1\n"
    tools, _, _ = make_tools(repo, {"src/agent.py": source("src/agent.py", text)})
    result = tools.execute(ToolAction(kind="symbols", path="src/agent.py", symbol="target"))
    assert result["definitions"][0]["start_line"] == 221
    assert tools.evidence[0]["url"].endswith("#L221")


def test_trace_reports_lexical_calls_without_claiming_runtime_dispatch(repo):
    tools, _, _ = make_tools(repo)
    result = tools.execute(ToolAction(kind="trace", path="src/agent.py", symbol="invoke"))
    assert "invoke" in dump(result["definitions"])
    assert "invoke" in dump(result["call_sites"])
    assert result["confidence"] == "static_syntax"
    assert result["evidence_ids"]


def test_trace_groups_twelve_calls_into_bounded_exact_citations(repo):
    text = (
        "def orchestrate(state):\n"
        + "".join(f"    state = step_{index}(state)\n" for index in range(12))
        + "    return state\n"
    )
    tools, _, _ = make_tools(repo, {"src/agent.py": source("src/agent.py", text)})
    result = tools.execute(ToolAction(kind="trace", path="src/agent.py", symbol="orchestrate"))
    assert len(result["call_sites"]) == 12
    assert 0 < len(tools.evidence) <= 8
    assert any(item.get("evidence_id") for item in result["call_sites"])
    assert_observation_citations_resolve(result, tools)
    by_id = {item["id"]: item for item in tools.evidence}
    for call in result["call_sites"]:
        if call.get("evidence_id"):
            citation = by_id[call["evidence_id"]]
            assert citation["start_line"] <= call["start_line"]
            assert citation["end_line"] >= call["end_line"]
    assert (
        len({item["evidence_id"] for item in result["call_sites"] if item.get("evidence_id")}) < 12
    )


def test_symbols_and_trace_reuse_existing_broad_source_read(repo):
    text = (
        "def orchestrate(state):\n"
        + "".join(f"    state = step_{index}(state)\n" for index in range(12))
        + "    return state\n"
    )
    tools, client, _ = make_tools(repo, {"src/agent.py": source("src/agent.py", text)})
    read = tools.execute(ToolAction(kind="read", path="src/agent.py"))
    identifier = read["evidence"]["id"]
    symbols = tools.execute(ToolAction(kind="symbols", path="src/agent.py"))
    trace = tools.execute(ToolAction(kind="trace", path="src/agent.py", symbol="orchestrate"))
    assert symbols["evidence_ids"] == [identifier]
    assert trace["evidence_ids"] == [identifier]
    assert len(tools.evidence) == 1
    assert len(client.calls) == 1
    assert_observation_citations_resolve(symbols, tools)
    assert_observation_citations_resolve(trace, tools)


@pytest.mark.parametrize("kind", ["symbols", "trace"])
def test_exhausted_citation_budget_returns_explicit_index_metadata(repo, kind):
    text = many_functions(32)
    tools, _, _ = make_tools(
        repo, {"src/agent.py": source("src/agent.py", text)}, budget=Budget(max_evidence=1)
    )
    tools.execute(ToolAction(kind="read", path="src/agent.py", start_line=1, end_line=1))
    result = tools.execute(ToolAction(kind=kind, path="src/agent.py", symbol="function_31"))
    assert result["definitions"]
    assert all(item["citation_status"] == "index_only" for item in result["definitions"])
    assert all("evidence_id" not in item for item in result["definitions"])
    assert result["evidence_ids"] == []
    assert result["warnings"]
    assert any(
        term in dump(result["warnings"]).lower()
        for term in ("index_only", "metadata", "uncited", "citation budget")
    )
    assert len(tools.evidence) == 1


def test_trace_can_follow_cross_file_import_candidates_with_individual_evidence(repo):
    main = "from pkg.memory import Memory\n\ndef make_memory():\n    return Memory()\n"
    memory = "class Memory:\n    def get(self, key):\n        return self.values[key]\n"
    sources = {
        "src/agent.py": source("src/agent.py", main),
        "src/pkg/memory.py": source("src/pkg/memory.py", memory),
    }
    tools, client, _ = make_tools(repo, sources)
    result = tools.execute(ToolAction(kind="trace", path="src/agent.py", symbol="Memory"))
    assert len(client.calls) == 2
    assert "src/pkg/memory.py" in dump(result["candidate_modules"])
    assert "path_candidates_only" in dump(result["candidate_modules"])
    assert any(item["path"] == "src/pkg/memory.py" for item in result["definitions"])
    paths = {item["path"] for item in tools.evidence}
    assert paths == {"src/agent.py", "src/pkg/memory.py"}


def test_search_has_bounded_file_fanout_and_number_of_results(repo):
    sources = {
        f"src/memory_{index}.py": source(f"src/memory_{index}.py", "memory = True\n" * 30)
        for index in range(9)
    }
    tools, client, _ = make_tools(repo, sources)
    result = tools.execute(ToolAction(kind="search", query="memory"))
    assert len(client.calls) <= 3
    assert len(result["matches"]) <= 12
    assert result["searched_paths"]
    for match in result["matches"]:
        assert match["path"] in sources
        assert match["text"] == "memory = True"
        assert match["line"] >= 1
        assert match["evidence_id"]


def test_search_uses_literal_query_without_regex_evaluation(repo):
    text = "sentinel = '[a-z]+.*'\nordinary = 'zzz'\n"
    tools, _, _ = make_tools(repo, {"src/agent.py": source("src/agent.py", text)})
    result = tools.execute(ToolAction(kind="search", query="[a-z]+.*"))
    assert len(result["matches"]) == 1
    assert result["matches"][0]["line"] == 1


def test_sparse_search_still_downloads_at_most_three_new_files(repo):
    sources = {
        f"src/file_{index}.py": source(f"src/file_{index}.py", "unrelated = True\n")
        for index in range(10)
    }
    tools, client, _ = make_tools(repo, sources)
    result = tools.execute(ToolAction(kind="search", query="missing_function"))
    assert len(client.calls) == 3
    assert result["matches"] == []
    assert result["complete"] is False


@pytest.mark.parametrize("path", ["../secret.py", "/etc/passwd", "src/missing.py"])
def test_unknown_or_unsafe_paths_cannot_trigger_download(repo, path):
    tools, client, _ = make_tools(repo)
    with pytest.raises(ToolError):
        tools.execute(ToolAction(kind="read", path=path))
    assert not client.calls


def test_even_malicious_tree_path_cannot_trigger_download(repo):
    path = "../outside.py"
    tools, client, _ = make_tools(repo, {path: source(path)})
    with pytest.raises(ToolError) as error:
        tools.execute(ToolAction(kind="read", path=path))
    assert error.value.code == "excluded_file"
    assert not client.calls


@pytest.mark.parametrize("path", ["vendor/tool.py", "node_modules/tool.py", "assets/image.png"])
def test_irrelevant_or_binary_paths_are_skipped_before_download(repo, path):
    tools, client, _ = make_tools(repo, {path: source(path)})
    with pytest.raises(ToolError):
        tools.execute(ToolAction(kind="read", path=path))
    assert not client.calls


def test_file_too_large_for_remaining_budget_is_skipped_before_download(repo):
    item = source("src/agent.py")
    entries = [TreeEntry(path=item.path, sha=item.blob_sha, size=5000)]
    tools, client, usage = make_tools(repo, budget=Budget(max_bytes=1000), entries=entries)
    with pytest.raises(ToolBudgetExceeded):
        tools.execute(ToolAction(kind="read", path=item.path))
    assert not client.calls
    assert usage.files == 0


def test_per_file_size_limit_skips_oversized_text_even_with_large_total_budget(repo):
    item = source("src/agent.py")
    entries = [TreeEntry(path=item.path, sha=item.blob_sha, size=200_000)]
    tools, client, _ = make_tools(repo, budget=Budget(max_bytes=400_000), entries=entries)
    with pytest.raises(ToolError) as error:
        tools.execute(ToolAction(kind="read", path=item.path))
    assert error.value.code == "oversized_file"
    assert not client.calls


def test_file_budget_stops_new_reads_but_keeps_existing_evidence_accessible(repo):
    sources = {"src/agent.py": source("src/agent.py"), "src/memory.py": source("src/memory.py")}
    tools, client, usage = make_tools(repo, sources, budget=Budget(max_files=1))
    first = tools.execute(ToolAction(kind="read", path="src/agent.py", start_line=3, end_line=5))
    with pytest.raises(ToolBudgetExceeded):
        tools.execute(ToolAction(kind="read", path="src/memory.py"))
    again = tools.execute(ToolAction(kind="read", path="src/agent.py", start_line=3, end_line=5))
    assert len(client.calls) == 1
    assert usage.files == 1
    assert again["evidence"]["id"] == first["evidence"]["id"]


def test_read_rejects_out_of_range_lines_without_fabricating_evidence(repo):
    tools, _, _ = make_tools(repo)
    with pytest.raises(ToolError):
        tools.execute(ToolAction(kind="read", path="src/agent.py", start_line=500, end_line=600))
    assert not tools.evidence


def test_read_cites_at_most_two_hundred_actual_lines_and_marks_range_clipping(repo):
    text = "value = 1\n" * 500
    tools, _, _ = make_tools(repo, {"src/agent.py": source("src/agent.py", text)})
    result = tools.execute(ToolAction(kind="read", path="src/agent.py", end_line=500))
    assert result["end_line"] == 200
    assert result["truncated_range"] is True
    assert len(result["evidence"]["excerpt"].splitlines()) == 200


def test_evidence_budget_blocks_new_citations_but_reuses_existing_citation(repo):
    tools, _, _ = make_tools(repo, budget=Budget(max_evidence=1))
    action = ToolAction(kind="read", path="src/agent.py", start_line=3, end_line=5)
    first = tools.execute(action)
    with pytest.raises(ToolBudgetExceeded):
        tools.execute(ToolAction(kind="read", path="src/agent.py", start_line=7, end_line=9))
    again = tools.execute(action)
    assert again["evidence"]["id"] == first["evidence"]["id"]
    assert len(tools.evidence) == 1


@pytest.mark.parametrize(
    "changes,code",
    [
        ({"total_lines": 500}, "incomplete_file"),
        ({"path": "src/other.py"}, "source_mismatch"),
        ({"blob_sha": "f" * 40}, "source_mismatch"),
        ({"text": "abc\0def", "total_lines": 1}, "binary_file"),
    ],
)
def test_mismatched_or_incomplete_downloads_never_become_evidence(repo, changes, code):
    original = source("src/agent.py")
    tools, client, _ = make_tools(repo, {"src/agent.py": original})
    client.sources["src/agent.py"] = original.model_copy(update=changes)
    with pytest.raises(ToolError) as error:
        tools.execute(ToolAction(kind="read", path="src/agent.py"))
    assert error.value.code == code
    assert not tools.files
    assert not tools.evidence


def test_invalid_python_is_a_recoverable_static_analysis_result(repo):
    tools, _, _ = make_tools(repo, {"src/broken.py": source("src/broken.py", "def broken(:\n")})
    with pytest.raises(ToolError) as error:
        tools.execute(ToolAction(kind="symbols", path="src/broken.py"))
    assert error.value.code == "parse_error"
    assert tools.files["src/broken.py"]["text"] == "def broken(:\n"
