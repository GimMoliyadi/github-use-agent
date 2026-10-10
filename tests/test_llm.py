"""Mock transport tests: these do not call a real LLM or GitHub API."""

import json

import httpx
import pytest
from langchain_core.messages import AIMessage
from langchain_openai import ChatOpenAI as RealChatOpenAI

from repo_research.config import Settings
from repo_research.llm import BudgetExceeded, MockModel, ModelError, ModelGateway, make_model
from repo_research.models import Budget, CapabilityReport, Decision, ToolAction, Usage


class ProviderError(Exception):
    def __init__(self, message, status_code):
        super().__init__(message)
        self.status_code = status_code


class ProviderTimeout(Exception):
    pass


class FakeClient:
    """Injectable LangChain-shaped fake: each invocation pops one response."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.mode = "chat"
        self.schema = None
        self.bindings = []
        self.invocations = []

    def bind_tools(self, schemas, **kwargs):
        self.mode = "tools"
        self.schema = schemas[0]
        self.bindings.append({"max_tokens": kwargs["max_tokens"]})
        return self

    def with_structured_output(self, schema, **kwargs):
        self.bindings.append({"max_tokens": kwargs.pop("max_tokens")})
        assert kwargs == {"method": "json_schema", "include_raw": True}
        self.mode = "json_schema"
        self.schema = schema
        return self

    def bind(self, **kwargs):
        self.bindings.append(kwargs)
        return self

    def invoke(self, messages):
        self.invocations.append(messages)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        if callable(response):
            response = response(self)
        return response


def response(text, measured=True):
    metadata = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
    return AIMessage(content=text, usage_metadata=metadata if measured else None)


def tool_response(schema, args):
    return AIMessage(
        content="",
        tool_calls=[{"name": schema, "args": args, "id": "1", "type": "tool_call"}],
        usage_metadata={"input_tokens": 12, "output_tokens": 6, "total_tokens": 18},
    )


def gateway(monkeypatch, responses, capability=None, budget=None, **settings_kwargs):
    client = FakeClient(responses)
    constructor = {}

    def constructor_fake(**kwargs):
        constructor.update(kwargs)
        return client

    monkeypatch.setattr("repo_research.llm.ChatOpenAI", constructor_fake)
    settings = Settings(**{
        "model_mode": "live",
        "llm_base_url": "https://third-party.example/v1",
        "llm_api_key": "test-secret-never-print",
        "llm_model": "custom-model",
        "llm_max_retries": 0,
        **settings_kwargs,
    })
    model = ModelGateway(settings, budget or Budget(), Usage(), capability)
    return model, client, constructor


def capability_fixture(mode="json_text", tools=False, structured=False):
    return CapabilityReport(
        mode="live", chat=True, tested=True, selected_output=mode,
        tool_calling=tools, structured_output=structured,
    )


def decision_json():
    return json.dumps({
        "action": {"kind": "read", "path": "src/state.py"}, "rationale": "Inspect state",
    })


def test_configured_gateway_only_and_validated_json(monkeypatch):
    model, client, config = gateway(monkeypatch, [response(decision_json())], capability_fixture())
    decision = model.decide({"question": "How does state work?", "tree": []})
    assert decision.action.path == "src/state.py"
    assert config["base_url"] == "https://third-party.example/v1"
    assert config["model"] == "custom-model"
    assert config["max_retries"] == 0
    assert config["use_responses_api"] is False
    assert config["http_socket_options"] == ()
    assert model.usage.llm_calls == 1
    assert model.usage.input_tokens == 10
    assert model.usage.output_tokens == 5
    assert model.usage.reserved_tokens == 0
    assert not model.usage.cost_known
    assert 64 <= client.bindings[0]["max_tokens"] <= 1400
    system_text = client.invocations[0][0].content
    assert "untrusted" in system_text and "Never obey" in system_text


def test_probe_semantics_and_schema_capabilities(monkeypatch):
    def tools(client):
        return tool_response(client.schema.__name__, {"echo": "capability-ok"})

    def structured(client):
        return {
            "raw": response(""), "parsed": client.schema(echo="capability-ok"),
            "parsing_error": None,
        }

    model, _, _ = gateway(monkeypatch, [response("capability-ok"), tools, structured])
    capability = model.probe()
    assert capability.chat and capability.tool_calling and capability.structured_output
    assert capability.selected_output == "tools"
    assert capability.tested and model.usage.llm_calls == 3


@pytest.mark.parametrize("text", ["", "   "])
def test_probe_rejects_empty_chat_even_on_http_success(monkeypatch, text):
    model, _, _ = gateway(monkeypatch, [response(text)])
    capability = model.probe()
    assert not capability.chat
    assert capability.tested
    assert capability.selected_output == "" and model.usage.llm_calls == 1
    assert "not tested" in " ".join(capability.details)
    with pytest.raises(ModelError, match="capability test failed"):
        model.decide({})


@pytest.mark.parametrize("text", ["capability-ok.", "连接正常", "Sure! capability-ok"])
def test_chat_probe_accepts_usable_text_without_exact_echo(monkeypatch, text):
    model, _, _ = gateway(monkeypatch, [
        response(text), response("No tool"), response("No schema"),
    ])
    capability = model.probe()
    assert capability.chat and capability.tested
    assert capability.selected_output == "json_text"
    assert not capability.tool_calling and not capability.structured_output
    assert model.usage.llm_calls == 3


def test_unsupported_capabilities_fall_back_to_json(monkeypatch):
    model, _, _ = gateway(
        monkeypatch,
        [
            response("capability-ok"),
            ProviderError("tools unsupported test-secret-never-print", 400),
            ProviderError("json_schema not supported test-secret-never-print", 422),
            response(decision_json()),
        ],
    )
    assert model.probe().selected_output == "json_text"
    assert model.decide({}).action.kind == "read"
    assert model.usage.llm_calls == 4
    assert model.usage.reserved_tokens > 0  # Rejected HTTP calls remain conservatively charged.
    assert "test-secret-never-print" not in model.capabilities.model_dump_json()


def test_runtime_feature_rejection_falls_back(monkeypatch):
    model, _, _ = gateway(
        monkeypatch,
        [ProviderError("function tools unsupported", 400), response(decision_json())],
        capability_fixture("tools", tools=True),
    )
    assert model.decide({}).action.path == "src/state.py"
    assert not model.capabilities.tool_calling
    assert model.capabilities.selected_output == "json_text"
    assert model.usage.llm_calls == 2


def test_runtime_report_schema_incompatibility_uses_json_text(monkeypatch):
    model, _, _ = gateway(
        monkeypatch,
        [ProviderError("Invalid schema for response_format: maxItems is not permitted", 400),
         response('{"title":"Fallback report","claims":[]}')],
        capability_fixture("json_schema", structured=True),
    )
    assert model.write_report({}).title == "Fallback report"
    assert not model.capabilities.structured_output
    assert model.usage.llm_calls == 2


def test_schema_probe_does_not_count_adapter_tool_fallback(monkeypatch):
    def substituted(client):
        return {
            "raw": tool_response("_CapabilityEcho", {"echo": "capability-ok"}),
            "parsed": client.schema(echo="capability-ok"), "parsing_error": None,
        }

    model, _, _ = gateway(
        monkeypatch,
        [response("capability-ok"),
         tool_response("_CapabilityEcho", {"echo": "capability-ok"}), substituted],
    )
    capability = model.probe()
    assert capability.tool_calling and not capability.structured_output


@pytest.mark.parametrize("status", [401, 403, 429, 500])
def test_provider_failure_does_not_trigger_feature_fallback(monkeypatch, status):
    model, client, _ = gateway(
        monkeypatch,
        [ProviderError("unsupported tools SECRET server body", status)],
        capability_fixture("tools", tools=True),
    )
    with pytest.raises(ModelError) as exc_info:
        model.decide({})
    assert "SECRET" not in str(exc_info.value)
    assert "server body" not in str(exc_info.value)
    assert exc_info.value.__cause__ is None
    assert len(client.invocations) == 1
    assert model.capabilities.tool_calling
    assert model.usage.reserved_tokens > 0


@pytest.mark.parametrize("text", ["not JSON", '{"action":{"kind":"execute"},"rationale":"bad"}',
                                 '{"action":{"kind":"finish"},"rationale":"ok","extra":1}'])
def test_malformed_json_is_rejected(monkeypatch, text):
    model, _, _ = gateway(monkeypatch, [response(text)], capability_fixture())
    with pytest.raises(ModelError, match="invalid JSON"):
        model.decide({})


def test_malformed_tool_falls_back_to_validated_json(monkeypatch):
    model, _, _ = gateway(
        monkeypatch, [tool_response("Decision", {"action": {"kind": "execute"}}),
                      response(decision_json())],
        capability_fixture("tools", tools=True),
    )
    assert model.decide({}).action.kind == "read"
    assert model.capabilities.selected_output == "json_text"
    assert model.usage.llm_calls == 2


@pytest.mark.parametrize("mode", ["tools", "json_schema"])
@pytest.mark.parametrize("status", [400, 404, 422, 501])
def test_real_research_schema_rejection_falls_back_once(monkeypatch, mode, status):
    capability = capability_fixture(mode, tools=mode == "tools", structured=mode == "json_schema")
    model, client, _ = gateway(monkeypatch, [
        ProviderError("SECRET relay body", status), response(decision_json()),
    ], capability)
    assert model.decide({}).action.kind == "read"
    assert model.capabilities.selected_output == "json_text"
    assert "SECRET" not in model.capabilities.model_dump_json()
    assert len(client.invocations) == model.usage.llm_calls == 2


def test_runtime_fallback_preserves_call_budget(monkeypatch):
    model, client, _ = gateway(monkeypatch, [
        tool_response("Decision", {"action": {"kind": "execute"}}),
    ], capability_fixture("tools", tools=True), Budget(max_llm_calls=1))
    with pytest.raises(BudgetExceeded, match="call budget"):
        model.decide({})
    assert len(client.invocations) == model.usage.llm_calls == 1


def test_runtime_fallback_still_rejects_unsafe_json_action(monkeypatch):
    model, client, _ = gateway(monkeypatch, [
        response("No tool call"), response('{"action":{"kind":"execute"},"rationale":"bad"}'),
    ], capability_fixture("tools", tools=True))
    with pytest.raises(ModelError, match="invalid JSON"):
        model.decide({})
    assert len(client.invocations) == 2


def test_tool_decision_validated(monkeypatch):
    args = {"action": {"kind": "search", "query": "checkpoint"}, "rationale": "Find storage"}
    model, _, _ = gateway(
        monkeypatch, [tool_response("Decision", args)], capability_fixture("tools", tools=True),
    )
    assert model.decide({}).action.query == "checkpoint"


def test_structured_report(monkeypatch):
    def structured(client):
        return {
            "raw": response(""),
            "parsed": client.schema(title="Test", claims=[]),
            "parsing_error": None,
        }

    model, _, _ = gateway(
        monkeypatch, [structured], capability_fixture("tools", tools=True, structured=True)
    )
    assert model.write_report({"evidence": []}).title == "Test"


def test_budget_reservation_prevents_request(monkeypatch):
    model, client, _ = gateway(monkeypatch, [], capability_fixture(), Budget(max_tokens=100))
    with pytest.raises(BudgetExceeded, match="token budget"):
        model.decide({})
    assert not client.invocations and model.usage.llm_calls == 0


def test_resumed_usage_respects_call_budget(monkeypatch):
    model, client, _ = gateway(monkeypatch, [], capability_fixture(), Budget(max_llm_calls=1))
    model.usage.llm_calls = 1
    with pytest.raises(BudgetExceeded, match="call budget"):
        model.decide({})
    assert not client.invocations


def test_unreported_usage_retains_reservation(monkeypatch):
    model, _, _ = gateway(
        monkeypatch, [response(decision_json(), measured=False)], capability_fixture()
    )
    model.decide({})
    assert model.usage.reserved_tokens > 0
    assert model.usage.input_tokens == model.usage.output_tokens == 0


def test_cost_preflight_and_measured_cost(monkeypatch):
    model, client, _ = gateway(
        monkeypatch, [], capability_fixture(), Budget(max_cost_usd=0),
        llm_input_price_per_million=1, llm_output_price_per_million=2,
    )
    with pytest.raises(BudgetExceeded, match="cost budget"):
        model.decide({})
    assert not client.invocations
    model, _, _ = gateway(
        monkeypatch, [response(decision_json())], capability_fixture(),
        llm_input_price_per_million=1, llm_output_price_per_million=2,
    )
    model.decide({})
    assert model.usage.cost_known
    assert model.usage.cost_usd == pytest.approx(20 / 1_000_000)


def test_timeout_is_sanitized_and_usage_preserved(monkeypatch):
    model, _, _ = gateway(monkeypatch, [ProviderTimeout("KEY body")], capability_fixture())
    with pytest.raises(ModelError, match="timeout") as exc_info:
        model.decide({})
    assert "KEY" not in str(exc_info.value)
    assert model.usage.llm_calls == 1 and model.usage.reserved_tokens > 0


def test_bounded_retries_charge_each_http_attempt(monkeypatch):
    model, client, _ = gateway(
        monkeypatch,
        [ProviderTimeout("KEY"), response(decision_json())],
        capability_fixture(), llm_max_retries=1,
    )
    assert model.decide({}).action.kind == "read"
    assert len(client.invocations) == 2
    assert model.usage.llm_calls == 2
    assert model.usage.reserved_tokens > 0


def test_probe_authentication_failure_is_fatal(monkeypatch):
    model, client, _ = gateway(monkeypatch, [ProviderError("SECRET", 401)])
    with pytest.raises(ModelError, match="authentication"):
        model.probe()
    assert len(client.invocations) == 1


def test_probe_malformed_optional_outputs_use_json_text(monkeypatch):
    model, _, _ = gateway(
        monkeypatch,
        [response("capability-ok"), response("No tool"),
         {"raw": response("{}"), "parsed": None, "parsing_error": ValueError("SECRET")},
         response(decision_json())],
    )
    assert model.probe().selected_output == "json_text"
    assert model.decide({}).action.kind == "read"
    assert "SECRET" not in model.capabilities.model_dump_json()


def test_mock_is_goal_aware_and_no_llm_usage():
    usage = Usage()
    model = make_model(Settings(model_mode="mock"), Budget(), usage)
    assert isinstance(model, MockModel)
    context = {
        "question": "研究 memory 如何实现",
        "tree": [
            {"path": "src/http.py", "type": "blob"},
            {"path": "src/memory.py", "type": "blob"},
            {"path": "docs/overview.md", "type": "blob"},
        ],
    }
    first = model.decide(context)
    assert first.action.path == "src/memory.py"
    assert "MOCK" in first.rationale
    context["files"] = [first.action.path]
    context["attempted"] = [first.action.model_dump_json()]
    context["evidence"] = [{
        "id": "E1", "path": first.action.path, "excerpt": "def remember():\n    pass",
    }]
    second = model.decide(context)
    assert second.action.kind == "symbols"
    context["attempted"].append(second.action.model_dump_json())
    third = model.decide(context)
    assert third.action.kind == "trace" and third.action.symbol == "remember"
    assert usage.llm_calls == usage.input_tokens == usage.output_tokens == 0


def test_mock_never_repeats_and_reports_existing_evidence_only():
    model = MockModel(Settings(), Budget(max_iterations=1), Usage(iterations=1))
    assert model.decide({}).action.kind == "finish"
    report = model.write_report(
        {"question": "tool calls", "evidence": [
            {"id": "E9", "path": "agent.py", "excerpt": "def call_tool():\n    return 1",
             "start_line": 3, "end_line": 4}
        ]}
    )
    assert "模拟" in report.title and "MOCK" in report.summary
    assert report.claims[0].evidence_ids == ["E9"]
    assert report.claims[-1].category == "unknown"
    assert all(set(claim.evidence_ids) <= {"E9"} for claim in report.claims)


def test_mock_goal_changes_file_selection():
    model = MockModel(Settings(), Budget(), Usage())
    tree = [{"path": "memory.py"}, {"path": "state.py"}, {"path": "network.py"}]
    assert model.decide({"question": "状态管理", "tree": tree}).action.path == "state.py"
    assert model.decide({"question": "network", "tree": tree}).action.path == "network.py"


def test_mock_follows_goal_symbol_beyond_initial_excerpt():
    model = MockModel(Settings(), Budget(), Usage())
    symbols = ToolAction(kind="symbols", path="core.py")
    context = {
        "question": "研究 call_tool 的实现",
        "tree": [{"path": "core.py"}],
        "files": ["core.py"],
        "attempted": [symbols.model_dump_json()],
        "observations": [{"kind": "symbols", "path": "core.py", "definitions": [
            {"name": "call_tool", "start_line": 320, "end_line": 400, "evidence_id": "E2"},
        ]}],
        "evidence": [{"id": "E1", "path": "core.py", "start_line": 1, "end_line": 160}],
    }
    action = model.decide(context).action
    assert action.kind == "read" and action.path == "core.py"
    assert action.start_line == 316 and action.end_line == 400


def test_decision_round_trip_contract():
    decision = Decision(action=ToolAction(kind="finish"), rationale="Enough verified evidence")
    assert Decision.model_validate_json(decision.model_dump_json()) == decision


def test_usage_write_ahead_hook_runs_before_transport_and_after_settlement(monkeypatch):
    recorded = []

    def transport_response(client):
        assert len(recorded) == 1
        assert recorded[0].llm_calls == 1 and recorded[0].reserved_tokens > 0
        return response(decision_json())

    model, _, _ = gateway(monkeypatch, [transport_response], capability_fixture())
    model.on_usage = lambda usage: recorded.append(usage.model_copy(deep=True))
    model.decide({})
    assert len(recorded) == 2
    assert recorded[-1].reserved_tokens == 0
    assert recorded[-1].input_tokens == 10


def test_usage_write_ahead_hook_survives_http_failure(monkeypatch):
    model, _, _ = gateway(monkeypatch, [ProviderTimeout("secret")], capability_fixture())
    recorded = []
    model.on_usage = lambda usage: recorded.append(usage.model_copy(deep=True))
    with pytest.raises(ModelError, match="timeout"):
        model.decide({})
    assert len(recorded) == 1
    assert recorded[0].reserved_tokens == model.usage.reserved_tokens > 0


def http_gateway(monkeypatch, handler, capability=None):
    """Real current ChatOpenAI/OpenAI SDK, exclusively intercepted HTTP requests."""
    client = httpx.Client(transport=httpx.MockTransport(handler))

    def adapter(**kwargs):
        return RealChatOpenAI(**kwargs, http_client=client)

    monkeypatch.setattr("repo_research.llm.ChatOpenAI", adapter)
    settings = Settings(
        model_mode="live", llm_base_url="https://third-party.example/v1",
        llm_api_key="mock-http-key", llm_model="configured-relay-model", llm_max_retries=0,
    )
    return ModelGateway(settings, Budget(), Usage(), capability), client


def completion(message, finish_reason="stop"):
    return httpx.Response(200, json={
        "id": "chatcmpl-mock", "object": "chat.completion", "created": 1,
        "model": "configured-relay-model",
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
        "usage": {"prompt_tokens": 7, "completion_tokens": 4, "total_tokens": 11},
    })


def test_real_adapter_mock_http_probes_tools_and_report(monkeypatch):
    requests = []
    recorded = []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        assert request.url == "https://third-party.example/v1/chat/completions"
        assert body["model"] == "configured-relay-model"
        assert body.get("max_tokens", body.get("max_completion_tokens", 0)) > 0
        assert recorded[-1].reserved_tokens > 0  # Ledger reserved before actual HTTP.
        if body.get("tools"):
            name = body["tools"][0]["function"]["name"]
            args = {"echo": "capability-ok"} if name == "_CapabilityEcho" else {
                "action": {"kind": "read", "path": "core.py"}, "rationale": "Inspect goal",
            }
            return completion({
                "role": "assistant", "content": None,
                "tool_calls": [{"id": "call-mock", "type": "function", "function": {
                    "name": name, "arguments": json.dumps(args),
                }}],
            }, "tool_calls")
        schema = body.get("response_format", {}).get("json_schema", {})
        if schema:
            value = {"echo": "capability-ok"} if schema["name"] == "_CapabilityEcho" else {
                "title": "Evidence report", "summary": "Static evidence",
                "claims": [{"section": "Mechanism", "category": "fact",
                            "text": "core.py defines the entry point", "evidence_ids": ["E1"]}],
                "limitations": [],
            }
            return completion({"role": "assistant", "content": json.dumps(value)})
        return completion({"role": "assistant", "content": "capability-ok"})

    model, client = http_gateway(monkeypatch, handler)
    model.on_usage = lambda usage: recorded.append(usage.model_copy(deep=True))
    try:
        capabilities = model.probe()
        assert capabilities.chat and capabilities.tool_calling and capabilities.structured_output
        assert model.decide({"question": "core architecture"}).action.path == "core.py"
        draft = model.write_report({"question": "core architecture", "evidence": [{"id": "E1"}]})
        assert draft.claims[0].evidence_ids == ["E1"]
        assert len(requests) == model.usage.llm_calls == 5
        assert model.usage.input_tokens == 35 and model.usage.output_tokens == 20
        assert model.usage.reserved_tokens == 0
        assert len(recorded) == 10
    finally:
        model.close()
        client.close()


def test_real_adapter_mock_http_unsupported_features_fallback(monkeypatch):
    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        if body.get("tools") or body.get("response_format"):
            feature = "tools" if body.get("tools") else "json_schema"
            return httpx.Response(400, json={"error": {
                "message": f"{feature} unsupported mock-http-key", "type": "invalid_request_error",
            }})
        prompt = body["messages"][-1]["content"]
        if "required_schema" in prompt:
            return completion({"role": "assistant", "content": decision_json()})
        return completion({"role": "assistant", "content": "capability-ok"})

    model, client = http_gateway(monkeypatch, handler)
    try:
        assert model.probe().selected_output == "json_text"
        assert model.decide({"question": "state"}).action.kind == "read"
        assert model.usage.llm_calls == len(requests) == 4
        assert "mock-http-key" not in model.capabilities.model_dump_json()
    finally:
        model.close()
        client.close()


def test_real_adapter_mock_http_timeout_sanitized(monkeypatch):
    def handler(request):
        raise httpx.ReadTimeout("mock-http-key confidential server body", request=request)

    model, client = http_gateway(monkeypatch, handler, capability_fixture())
    try:
        with pytest.raises(ModelError, match="timeout") as exc_info:
            model.decide({})
        assert "mock-http-key" not in str(exc_info.value)
        assert "confidential" not in str(exc_info.value)
        assert model.usage.llm_calls == 1 and model.usage.reserved_tokens > 0
    finally:
        model.close()
        client.close()
