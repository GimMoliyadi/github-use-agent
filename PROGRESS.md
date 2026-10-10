# Progress

## 2026-10-10 — initial scaffold

- Empty managed workspace; project created at `/workspace/repo-research-agent`.
- Selected stable PyPI releases: LangGraph 1.2.14, langgraph-checkpoint-sqlite 3.1.1, langchain-openai 1.7.0.
- No LLM configuration present: real-provider tests pending; deterministic Mock will be labeled.
- Environment HTTP policy unrestricted; exec commands need sandbox network grants.
- GitHub connector identity available; CLI authentication needs verification before remote publication.
- Next: implement fixed-SHA GitHub access, model compatibility, checkpointed workflow and Web UI.

## 2026-10-10 — integrated implementation saved; full validation blocked

Implemented:

- Actual LangGraph StateGraph with model decisions, deterministic read/search/symbols/trace tools, bounded loop, anti-repeat/no-new-information exits, drafting and source verification.
- SQLite checkpointer with `durability="sync"`, manual pause/recovery interrupts, preserved fixed Commit and node progress.
- Separate synchronous write-ahead usage ledger reserves API/LLM attempts before HTTP, plus durable initialization Commit pin. A resumed task preserves conservative spent usage; this does not guarantee exactly-once provider execution.
- SHA-pinned official GitHub REST, persistent immutable cache, bounded hierarchical tree fallback, UTF-8/size/binary guards and real Git blob validation. Checkpoint tree restores blob indices without another API tree request.
- OpenAI-compatible ChatOpenAI adapter, semantic chat/tool/JSON-schema probes, validated JSON-text fallback, explicit Mock, sanitized errors, retries/timeouts and token/call/cost budgets.
- FastAPI + Chinese responsive UI, history, progress, verified evidence links, report download, cooperative pause/resume and optional bearer access token. Unverified summary is labeled; repository/model text uses safe DOM.
- Bounded workers, single DATA_DIR process lock, manual startup recovery, no-budget-reset missing-checkpoint guard, live configuration fingerprint.
- CLI, offline unit/integration test sources, opt-in real GitHub and browser test sources, Chinese README/architecture/deployment docs, Dockerfile, MIT license and GitHub Actions CI configuration.

Actually verified:

- `python -m compileall -q src tests examples`, `node --check src/repo_research/static/app.js`, `git diff --check` passed.
- Real official GitHub data fetched through installed GitHub connector for `pypa/sampleproject` at Commit `621e4974ca25ce531773def586ba3ed8e736b3fc`; four full blobs independently SHA-checked. `PYTHONPATH=src python examples/validate_snapshot.py` passed: 1 function, 1 call, 3 verified citations. This is local static replay of connector-fetched source, **not** direct Python API or LangGraph validation.
- SQLite task/history/startup/usage/process-lock/fingerprint manual smoke passed.
- System-Python FastAPI/httpx ASGI HTTP smoke with StubService passed static/config/auth/task/history/pause/resume/download/error/secret-sanitization/lifespan checks. This does not test the actual LangGraph worker.

Not yet verified / delivered:

- Full pytest and ruff: not run; `.venv` exists but has no project/dev dependencies. LangGraph is not installed yet. Do not claim the complete product has run.
- Direct Python real GitHub workflow and true LangGraph integration: tests written, not run.
- Browser: `/usr/bin/chromium` exists, but sandbox denies socket/setsockopt without a network grant; browser test not passed.
- Real LLM: `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` unavailable; no third-party paid calls were made.
- Docker/hosted CI/deployment: config written, not executed.
- Public GitHub repository/push: no remote is configured. GitHub connector identity exists; initial CLI auth check reported invalid GH_TOKEN, but network-authorized verification/publishing did not run. No remote URL is invented.

Blocking condition:

The environment HTTP policy is unrestricted, but command sandbox network remains restricted. `uv sync --extra dev` additional-network permission calls were repeatedly interrupted by the user before execution. An asynchronous request now asks to approve installation/test network permission. Continue only after that required approval arrives; no alternative paid API is authorized.

Continuation commands (requires command network grant for install and live/browser checks):

```bash
cd /workspace/repo-research-agent
UV_CACHE_DIR=/tmp/repo-research-uv-cache uv sync --extra dev
uv run --no-sync pytest -m "not live and not browser" -q
uv run --no-sync ruff check .
CHROMIUM_EXECUTABLE_PATH=/usr/bin/chromium RUN_BROWSER_TESTS=1 uv run --no-sync pytest tests/test_web.py -m browser -q
RUN_LIVE_TESTS=1 uv run --no-sync pytest -m live -q
RUN_LIVE_TESTS=1 uv run --no-sync python examples/check_live.py --output-dir data/live-validation
uv run --no-sync repo-research serve --host 0.0.0.0 --port 8000
```

Fix actual failures before claiming MVP acceptance. Generate `uv.lock`, switch CI to frozen sync, add actual test results to `docs/validation.md`, and capture browser screenshots. With user-provided relay credentials, run live capability probe then a small-budget task; do not switch providers. Verify CLI GitHub authorization and only then create/push the public repository. Preserve DATA_DIR for task recovery; don't commit its content or credentials.

