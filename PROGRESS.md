# Progress

## 2026-10-10 — model diagnostics and native Windows compatibility

- Probe errors now show the failing stage, sanitized upstream HTTP status and
  actionable hints; the browser also exposes safe capability diagnostics.
- Optional feature-only 400/404/422/501 rejections after successful chat can fall
  back to JSON text; authentication, rate limits and outages remain failures.
- Reject full completion/Responses endpoints as Base URL; increased short chat
  probe output allowance for model compatibility. Model ID remains required.
- Replaced unconditional POSIX lock import with POSIX/Windows nonblocking locks;
  Windows settings writes no longer require os.fchmod. Added native Windows CI.
- Local Linux offline: **243 passed, 8 deselected**, Ruff passed. Chromium
  desktop/mobile settings and research flows: **4 passed, 40 deselected**;
  settings tests verify HTTP 401 hints without exposing the key.
  No actual user relay credentials or paid model requests were used.
- The first native Windows run passed model settings but exposed a terminal
  pause acknowledgement race (240 passed, 1 failed, 1 skipped). The worker now
  atomically publishes terminal status and clears pause_requested under the
  same lock as pause(); a deterministic blocked-cleanup regression passes.
- Native Windows validation is pending the new hosted matrix run; preserve
  that distinction until its actual result is available.

## 2026-10-10 — Web model configuration shipped

- Added top-bar “模型配置”: Base URL, password-masked API key, model ID,
  Mock/live mode, output compatibility, capability probe and save/apply.
- Server-only atomic `DATA_DIR/model-settings.json` persistence with `0600`
  permissions. Keys are absent from API responses, validation errors, task
  history and browser storage. Git/Docker ignore secret files even in custom
  data directories. Existing keys cannot be reused at changed endpoints.
- New settings apply without restart to future tasks. Active workers block
  changes; saved task fingerprints and budgets remain enforced. Explicit CLI
  mode and forced-Mock validation bypass persisted UI settings.
- Current offline suite: **233 passed, 8 deselected**, Ruff passed. Actual
  Chromium configuration/probe/save/reload flows at 1440 and 390 px plus existing
  research UI flows: **4 passed, 31 deselected**. Capability requests used the
  actual ChatOpenAI/OpenAI SDK with MockTransport, not a real paid provider.
- Screenshots: `docs/screenshots/model-settings-1440.png` and
  `model-settings-390.png`. Real relay validation still requires user credentials.

## 2026-10-10 — current acceptance status: runnable and tested

The dependency/network block recorded below has been resolved. `uv.lock` locks
the environment; use `uv sync --frozen --extra dev`. The actual LangGraph worker,
public GitHub REST research, browser UI and non-root Python 3.11 Docker image
have run successfully. No real paid LLM has been called.

Published to the user-authorized public repository
[GimMoliyadi/github-use-agent](https://github.com/GimMoliyadi/github-use-agent).
The first normal push uploaded the complete history to `main` at
[`77513106a475b4efe9b3beac3608636e3f7737f4`](https://github.com/GimMoliyadi/github-use-agent/commit/77513106a475b4efe9b3beac3608636e3f7737f4).
`origin` is `https://github.com/GimMoliyadi/github-use-agent.git`; no force push
was used. Hosted Python 3.11
[Offline checks run 38034286129](https://github.com/GimMoliyadi/github-use-agent/actions/runs/38034286129)
completed **SUCCESS** at that initial publish commit: CPython 3.11.17, Ruff
passed, **219 passed / 6 deselected in 4.62 s**. The actual run/log evidence is
archived in [github-actions-validation.json](docs/validation/github-actions-validation.json).
Public website hosting has not been deployed.

Verified results:

- Final offline suite after evidence-budget refinement: **219 passed, 6
  deselected**; global Ruff passed. Earlier stage: 212 passed, 6 deselected.
- Agent integration: **17 passed**, including real `os._exit(23)` worker loss,
  then a new runner restoring the fixed Commit, SQLite checkpoint and durable
  LLM reservation. Service/storage: **22 passed**, including shutdown/reopen/
  manual resume. These counts are subsets of the offline suite.
- Model adapter: **35 passed**, using Mock/Fake models and `httpx.MockTransport`,
  including the actual ChatOpenAI/OpenAI SDK request path. Chat, tools, structured
  output, JSON-text fallback, retries, output limits and secret sanitization were
  verified without calling a real paid provider.
- Web: **19 passed**, including actual Chromium desktop **1440 px** and mobile
  **390 px** flows with simulated task data. Screenshots are in
  `docs/screenshots/`.
- Opt-in real GitHub pytest: **4 passed, 56 deselected** (final rerun: 17.12 s). Actual Python REST and
  LangGraph research of `pypa/sampleproject` completed overview and directed
  `sample.main` cases at `621e4974ca25ce531773def586ba3ed8e736b3fc` with **4** and
  **4** citations after the evidence-budget refinement. Every citation was
  independently reread from GitHub and checked for complete source, Blob SHA,
  exact LF lines and fixed-SHA permalink. Both tasks used Mock and `llm_calls=0`.
- A real browser-driven `pallets/itsdangerous` research task completed at
  `672971d66a2ef9f85151e53283113f33d642dabd`: **9 verified citations**, 10
  iterations, 3 files, 14,491 analyzed bytes, 6 API requests, 0 LLM calls. Actual
  Chromium 1440/390 px showed no horizontal overflow or JavaScript errors.
- Python **3.11** Docker image built and ran as non-root. Health/static/API checks
  and a real GitHub research task passed against the final image: 6 iterations,
  3 files, 253 analyzed bytes, 6 API requests, 3 verified citations, 0 LLM calls.
  The quick task completed a finite report at `iteration_budget`. Hosted offline
  CI passed on Python 3.11; public website hosting is unrun.

The evidence refinement adds bounded goal-focused citations, reuse of covering
ranges, merged call-site ranges and explicit index-only observations. Final
offline and fresh real CLI/UI checks passed. Public-safe execution records and
reports are now archived in `docs/validation/`; earlier sampleproject counts of
13/10 belong to the prior stage and are superseded by the final 4/4 records.
The final Python 3.11 Docker image was built as
`sha256:672770f2821cb83a7bffe9caad93dabfd23a047f4ca21b0ceefe1a42c7584ed0`;
Its final HTTP smoke and research passed, with a public record in
`docs/validation/docker-validation.json`. Docker installs `requirements.lock`
using `pip --require-hashes`, then installs the local project with `--no-deps`.
See
[docs/validation.md](docs/validation.md) for scope and commands.

Stable implementation saved as Git commit `78e3c41`. Final source, screenshots,
public-safe reports and continuation docs are saved in this repository. Portable
delivery copies at `/workspace/repolens-source.zip` and
`/workspace/repolens-history.bundle` are regenerated from the final committed HEAD;
the bundle can restore the entire local Git history. Runtime data and secrets
are excluded from both artifacts. A local Mock Web service can reuse
`data/ui-validation-final/` to display the completed real-source research record.

Still pending:

- User-specified live LLM credentials and real compatibility/quality validation.
  Do not substitute another paid provider. Run the live capability probe, then a
  small research task, only when the requested relay configuration is available.

Earlier GraphQL and REST repository-creation requests were denied (REST HTTP
403). The user subsequently supplied this existing empty public repository;
Root verified ADMIN access and no existing refs, then configured `origin` and
successfully pushed `main`. The creation-permission issue therefore no longer
blocks publication; no other repository was created.

Keep the whole DATA_DIR for recovery: metadata, checkpoints, source cache and
`usage-ledger.sqlite3`. Reservations are durable before HTTP; retries or resumed
requests may still incur extra provider charges. This preserves conservative
spent budgets, not exactly-once paid API execution. Dollar limits need both token
prices; otherwise fees remain explicitly unknown.

## Historical development record

The sections below describe earlier stages and resolved blockers. They are kept
for continuity and do not override the current acceptance status above.

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

