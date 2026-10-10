"use strict";

// Repository content, model responses and task metadata are all untrusted text.
// Build DOM nodes directly; never interpolate them into HTML or executable markup.
const $ = (id) => document.getElementById(id);
const state = { config: null, task: null, selected: null, pollTimer: null, token: sessionStorage.getItem("repolens.access") || "", sequence: 0 };
const statuses = { pending: "等待中", running: "研究中", paused: "已暂停", interrupted: "可恢复", completed: "已完成", failed: "失败" };
const nodes = { initialize: "固定仓库版本", bootstrap: "初步结构分析", plan: "制定研究计划", decide: "选择探索方向", explore: "探索代码", tools: "读取与检索", organize: "整理证据", review: "判断证据充分性", assess: "检查证据充分性", draft: "生成研究草稿", gate: "保存研究状态", verify: "验证结论与引用", report: "生成研究报告", finalize: "完成研究" };
const stopReasons = { model_sufficient: "当前证据已足以结束探索", iteration_budget: "达到最大探索迭代预算", api_budget: "达到 GitHub 请求预算", tool_budget: "达到文件、字节或证据读取预算", budget_exhausted: "研究预算耗尽，保留已有证据", repeated_action: "重复已完成的操作，停止以避免循环", no_new_information: "连续探索没有获得新信息" };
const budgetFields = [
  ["max_iterations", "探索迭代", 1, 40, 1], ["max_files", "读取文件", 1, 80, 1],
  ["max_api_requests", "GitHub 请求", 3, 300, 1], ["max_llm_calls", "模型调用", 0, 60, 1],
  ["max_tokens", "Token 上限", 100, 500000, 1], ["max_cost_usd", "费用上限 / USD", 0, 100, 0.01],
  ["max_bytes", "源码字节", 1000, 4000000, 1], ["max_tree_entries", "目录项", 10, 30000, 1],
  ["max_evidence", "证据条数", 1, 100, 1]
];

function element(tag, className, text) {
  const item = document.createElement(tag);
  if (className) item.className = className;
  if (text !== undefined && text !== null) item.textContent = String(text);
  return item;
}
function clear(node) { node.replaceChildren(); }
function notice(message, error = false) { $("notice").textContent = message; $("notice").className = error ? "notice error" : "notice"; $("notice").hidden = !message; }
function dateLabel(value, timeOnly = false) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat("zh-CN", timeOnly ? { hour: "2-digit", minute: "2-digit", second: "2-digit" } : { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }).format(date);
}
function repositoryLabel(request) {
  try { const url = new URL(request.repo_url); return url.pathname.replace(/^\//, "").replace(/\.git\/?$/, "").replace(/\/$/, ""); }
  catch { return request.repo_url || "仓库研究"; }
}
function safeGithubURL(value) {
  try { const url = new URL(value); return url.protocol === "https:" && url.hostname === "github.com" && !url.username && !url.password ? url.href : null; }
  catch { return null; }
}
function githubLink(text, value) {
  const url = safeGithubURL(value);
  if (!url) return element("span", "", text);
  const link = element("a", "", text); link.href = url; link.target = "_blank"; link.rel = "noopener noreferrer"; return link;
}
async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (state.token) headers.set("Authorization", `Bearer ${state.token}`);
  if (options.body) headers.set("Content-Type", "application/json");
  let response;
  try { response = await fetch(path, { ...options, headers, cache: "no-store" }); }
  catch { throw new Error("无法连接服务，请检查网络或服务是否正在运行。"); }
  if (!response.ok) {
    if (response.status === 401) { $("auth-panel").hidden = false; $("access-token").focus(); }
    let detail = "请求失败，请稍后重试。";
    try { const data = await response.json(); detail = Array.isArray(data.detail) ? data.detail.map((item) => `${item.loc?.slice(1).join(".") || "输入"}：${item.msg}`).join("\n") : data.detail || detail; } catch { /* Safe default for non-JSON upstream errors. */ }
    throw new Error(String(detail));
  }
  return response;
}
async function jsonApi(path, options) { return (await api(path, options)).json(); }

async function openModelSettings() {
  const config = await jsonApi("/api/model");
  $("model-mode").value = config.mode === "mock" && !config.key_configured ? "live" : config.mode;
  $("model-base-url").value = config.base_url || "";
  $("model-name").value = config.model || "";
  $("model-output-mode").value = config.output_mode || "auto";
  $("model-api-key").value = "";
  $("model-key-status").textContent = config.key_configured ? "已保存密钥。保持留空可继续使用；更换 Base URL 时必须填写新密钥。" : "尚未配置密钥。";
  $("model-result").hidden = true;
  $("model-panel").hidden = false;
  $("model-base-url").focus();
}
async function applyModelSettings(probe) {
  const save = $("model-save"), check = $("model-probe"), result = $("model-result");
  save.disabled = true; check.disabled = true;
  result.hidden = false; result.className = "notice"; result.textContent = probe ? "正在检测聊天、工具调用和结构化输出…" : "正在保存配置…";
  const configuration = { mode: $("model-mode").value, base_url: $("model-base-url").value.trim(), api_key: $("model-api-key").value, model: $("model-name").value.trim(), output_mode: $("model-output-mode").value };
  try {
    const response = await jsonApi(probe ? "/api/model/probe" : "/api/model", { method: probe ? "POST" : "PUT", body: JSON.stringify(configuration) });
    if (probe) {
      const capability = response.capabilities;
      const tools = !capability.chat ? "未测试" : capability.tool_calling ? "支持" : "未通过";
      const schema = !capability.chat ? "未测试" : capability.structured_output ? "支持" : "未通过";
      const output = capability.chat ? `采用 ${capability.selected_output}。` : "尚未选择输出方式。";
      result.textContent = capability.mode === "mock" ? "Mock 模式：未调用真实模型 API。" : `聊天：${capability.chat ? "通过" : "失败"}；工具调用：${tools}；JSON Schema：${schema}。${output}本次模型调用 ${response.usage.llm_calls} 次。检测不会自动保存，确认后点击保存并应用。`;
      if (capability.mode !== "mock") {
        if (!capability.chat) {
          result.className = "notice error";
          result.textContent += " 接口已返回响应，但没有可用的聊天文本；可能输出被截断、过滤或接口格式不兼容，不能据此判断密钥无效。";
        } else if (!capability.tool_calling || !capability.structured_output) {
          result.textContent += " 聊天连接可用，缺少的可选能力将使用兼容输出方式。";
        }
        if (capability.details?.length) result.textContent += ` 诊断：${capability.details.join("；")}`;
      }
    } else {
      $("model-api-key").value = "";
      await connect();
      $("model-key-status").textContent = response.key_configured ? "密钥已保存；留空可继续使用。" : "未配置密钥。";
      result.textContent = "已保存并应用。新研究将使用此配置，重启服务后仍保留。";
    }
  } catch (error) { result.className = "notice error"; result.textContent = error.message; }
  finally { save.disabled = false; check.disabled = false; }
}

function renderBudgetFields(defaults) {
  clear($("budget-fields"));
  budgetFields.forEach(([key, label, min, max, step]) => {
    const wrapper = element("div"); const title = element("label", "", label); title.htmlFor = `budget-${key}`;
    const input = element("input"); input.id = `budget-${key}`; input.type = "number"; input.min = min; input.max = max; input.step = step; input.value = defaults[key]; input.disabled = !$("custom-budget").checked;
    wrapper.append(title, input); $("budget-fields").append(wrapper);
  });
}
async function connect() {
  state.config = await jsonApi("/api/config");
  const isMock = state.config.mode === "mock";
  $("mode-badge").textContent = isMock ? "MOCK · 模拟推理" : `LIVE · ${state.config.model || "模型"}`;
  $("mode-badge").className = `badge mode-badge${isMock ? " mock" : ""}`;
  $("mode-badge").title = isMock ? "使用确定性模拟模型；GitHub 数据仍可来自真实 API。模拟报告不代表真实 LLM 推理效果。" : "使用配置的第三方模型接口";
  renderBudgetFields(state.config.default_budget);
  $("auth-panel").hidden = true; $("start-button").disabled = false; notice(""); await loadHistory();
}
async function loadHistory() {
  const tasks = await jsonApi("/api/tasks?limit=30");
  clear($("history"));
  if (!tasks.length) { $("history").append(element("p", "muted empty-history", "还没有研究记录。开始第一项研究吧。")); return; }
  tasks.forEach((task) => {
    const request = task.request || {};
    const button = element("button", `history-item${task.id === state.selected ? " active" : ""}`); button.type = "button";
    if (task.id === state.selected) button.setAttribute("aria-current", "true");
    const title = element("span", "history-item-repo", task.repository?.full_name || repositoryLabel(request));
    const question = element("span", "history-item-question", request.question || "仓库研究"); question.title = request.question || "";
    const meta = element("span", "history-item-meta"); meta.append(element("span", "history-status", statuses[task.status] || task.status), element("span", "", dateLabel(task.created_at)));
    button.append(title, question, meta); button.addEventListener("click", () => openTask(task.id).catch((error) => notice(error.message, true))); $("history").append(button);
  });
}
function resetTaskView() {
  state.selected = null; state.task = null; state.sequence += 1; clearTimeout(state.pollTimer);
  $("task-panel").hidden = true; $("request-card").hidden = false; $("research-intro").hidden = false; notice("");
  document.querySelectorAll(".history-item").forEach((item) => { item.classList.remove("active"); item.removeAttribute("aria-current"); });
}
async function openTask(id) {
  clearTimeout(state.pollTimer); state.selected = id; const sequence = ++state.sequence;
  const task = await jsonApi(`/api/tasks/${encodeURIComponent(id)}`);
  if (state.selected !== id || state.sequence !== sequence) return;
  state.task = task; $("request-card").hidden = true; $("research-intro").hidden = true; $("task-panel").hidden = false; notice(""); renderTask(task);
  await loadHistory(); schedulePoll(sequence);
}
function schedulePoll(sequence) {
  if (!state.task || !["pending", "running"].includes(state.task.status)) return;
  state.pollTimer = setTimeout(async () => {
    state.pollTimer = null;
    try {
      const task = await jsonApi(`/api/tasks/${encodeURIComponent(state.selected)}`);
      if (sequence !== state.sequence) return;
      state.task = task; renderTask(task);
      if (!["pending", "running"].includes(task.status)) await loadHistory();
      schedulePoll(sequence);
    } catch (error) { notice(`${error.message} 已保留当前任务；可从研究记录重新打开。`, true); }
  }, 1600);
}
function renderTask(task) {
  const request = task.request || {}; const repo = task.repository;
  $("task-title").textContent = repo?.full_name || repositoryLabel(request); $("task-question").textContent = request.question || "";
  $("task-status").textContent = statuses[task.status] || task.status; $("task-status").className = `badge status-badge ${task.status}`;
  clear($("task-meta"));
  if (repo) {
    $("task-meta").append(githubLink(repo.full_name, repo.url));
    const sha = element("span"); sha.append(element("span", "", "Commit "), element("code", "", repo.commit_sha?.slice(0, 12) || ""));
    const shaURL = safeGithubURL(`${repo.url}/tree/${repo.commit_sha}`);
    if (shaURL) { const link = githubLink("", shaURL); link.append(sha); $("task-meta").append(link); } else $("task-meta").append(sha);
    if (repo.language) $("task-meta").append(element("span", "", repo.language));
  }
  $("task-meta").append(element("span", "", `创建于 ${dateLabel(task.created_at)}`));
  $("pause-button").hidden = !["pending", "running"].includes(task.status); $("resume-button").hidden = !["paused", "interrupted"].includes(task.status); $("download-button").hidden = !task.report_markdown;
  $("pause-button").disabled = !!task.pause_requested;
  $("pause-button").textContent = task.pause_requested ? "暂停请求已提交" : "暂停研究";
  $("task-error").textContent = task.error || ""; $("task-error").hidden = !task.error;
  $("stop-reason").textContent = task.stop_reason ? `结束说明：${stopReasons[task.stop_reason] || task.stop_reason}` : task.pause_requested && ["pending", "running"].includes(task.status) ? "已请求暂停，将在当前步骤结束后保存状态。" : "";
  renderProgress(task.progress || []); renderUsage(task); renderReport(task);
}
function renderProgress(progress) {
  const container = $("progress-list"); const nearBottom = container.scrollHeight - container.scrollTop - container.clientHeight < 50; clear(container);
  $("progress-count").textContent = `${progress.length} 个记录`;
  if (!progress.length) { container.append(element("li", "progress-empty", "任务已创建，等待探索开始。")); return; }
  progress.forEach((item, index) => {
    const entry = element("li", `progress-item${index === progress.length - 1 ? " latest" : ""}`);
    entry.append(element("time", "", dateLabel(item.time, true)), element("strong", "", nodes[item.node] || item.node || "执行步骤"), element("p", "", item.message || "")); container.append(entry);
  });
  if (nearBottom) container.scrollTop = container.scrollHeight;
}
function renderUsage(task) {
  const usage = task.usage || {}; const budget = task.budget || task.effective_budget || task.request?.budget || state.config?.default_budget || {}; clear($("usage-list"));
  const tokens = (usage.input_tokens || 0) + (usage.output_tokens || 0) + (usage.reserved_tokens || 0);
  [["探索迭代", usage.iterations, budget.max_iterations], ["读取文件", usage.files, budget.max_files], ["GitHub 请求", usage.api_requests, budget.max_api_requests], ["模型调用", usage.llm_calls, budget.max_llm_calls], ["Token（含预留）", tokens, budget.max_tokens]].forEach(([label, used = 0, max]) => {
    const row = element("div", "usage-row"); const title = element("div", "usage-label"); title.append(element("span", "", label), element("strong", "", `${Number(used).toLocaleString("zh-CN")}${max !== undefined ? ` / ${Number(max).toLocaleString("zh-CN")}` : ""}`));
    const bar = element("div", "usage-bar"); const fill = element("div", `usage-fill${max !== undefined && used >= max ? " exhausted" : ""}`); fill.style.width = `${Math.max(0, Math.min(100, max > 0 ? used / max * 100 : 0))}%`; bar.append(fill); row.append(title, bar); $("usage-list").append(row);
  });
  const cost = usage.cost_known ? `估算费用 $${Number(usage.cost_usd || 0).toFixed(4)}${budget.max_cost_usd !== undefined ? ` / $${Number(budget.max_cost_usd).toFixed(2)}` : ""}` : "费用未计算 · 未配置模型单价或使用 Mock";
  $("usage-list").append(element("p", "usage-cost", `${cost} · 缓存命中 ${usage.cache_hits || 0}`));
}
function renderEvidence(evidence) {
  const box = element("details", "evidence"); const summary = element("summary");
  const location = `${evidence.path}:${evidence.start_line}–${evidence.end_line}`;
  summary.append(evidence.verified ? githubLink(location, evidence.url) : element("span", "", location), element("span", "evidence-label", evidence.verified ? "已核验 · 查看源码" : "尚未核验 · 引用链接不可用"));
  const meta = element("p", "evidence-meta", `${evidence.repository || ""} · ${evidence.commit_sha || ""} · ${evidence.kind || "code"}`);
  box.append(summary, meta, element("pre", "", evidence.excerpt || "")); return box;
}
function renderReport(task) {
  const report = task.report; const available = !!report || !!task.report_markdown;
  $("report-empty").hidden = available; $("report-content").hidden = !available; clear($("report-content"));
  if (!available) return;
  const content = $("report-content"); const header = element("div", "report-header"); const title = element("h2", "", report?.title || "仓库研究报告"); title.id = "report-title";
  header.append(title);
  if (report?.summary) header.append(element("p", "small-label", "模型摘要（未逐项验证）"), element("p", "", report.summary));
  content.append(header);
  if ((task.model_mode || task.mode || state.config?.mode) === "mock") content.append(element("p", "report-warning", "模拟模型结果：此任务使用确定性 Mock 推理。源码引用仍应经过真实内容核验；结论质量不代表真实 LLM 能力。"));
  const evidenceById = new Map((task.evidence || []).map((item) => [item.id, item])); const sections = new Map();
  (report?.claims || []).forEach((claim) => {
    const sectionName = claim.section || "关键发现";
    if (!sections.has(sectionName)) { const section = element("section", "report-section"); section.append(element("h3", "", sectionName)); sections.set(sectionName, section); content.append(section); }
    const item = element("div", "claim"); const text = element("div", "claim-text"); text.append(element("span", `claim-badge ${claim.category}`, { fact: "源码事实", inference: "代码推断", unknown: "尚未确认" }[claim.category] || "尚未确认"), element("p", "", claim.text)); item.append(text);
    const refs = element("div", "citations"); (claim.evidence_ids || []).forEach((id) => { const evidence = evidenceById.get(id); if (evidence) refs.append(renderEvidence(evidence)); else refs.append(element("p", "hint", `引用 ${id} 未找到，无法在界面展示。`)); });
    if (refs.childNodes.length) item.append(refs); sections.get(sectionName).append(item);
  });
  if (report?.limitations?.length) { const section = element("section", "report-section"); section.append(element("h3", "", "限制与未确认事项")); const list = element("ul", "limitations"); report.limitations.forEach((item) => list.append(element("li", "", item))); section.append(list); content.append(section); }
  if (task.report_markdown) { const source = element("details", "markdown-source"); source.append(element("summary", "", "查看完整 Markdown 报告"), element("pre", "", task.report_markdown)); content.append(source); }
}
async function taskAction(action) {
  if (!state.selected) return;
  const id = state.selected; const button = $(`${action}-button`); button.disabled = true;
  try { const task = await jsonApi(`/api/tasks/${encodeURIComponent(id)}/${action}`, { method: "POST" }); if (state.selected !== id) return; state.task = task; renderTask(task); await loadHistory(); clearTimeout(state.pollTimer); schedulePoll(state.sequence); }
  catch (error) { notice(error.message, true); }
  finally { button.disabled = action === "pause" && !!state.task?.pause_requested; }
}

$("research-form").addEventListener("submit", async (event) => {
  event.preventDefault(); const button = $("start-button"); button.disabled = true; button.textContent = "创建任务…"; notice("");
  try {
    const request = { repo_url: $("repo-url").value.trim(), question: $("question").value.trim(), ref: $("ref").value.trim() || null, depth: $("depth").value };
    if (request.question.length < 3) throw new Error("请输入至少 3 个字符的研究问题。");
    if ($("custom-budget").checked) { request.budget = {}; budgetFields.forEach(([key]) => { request.budget[key] = Number($(`budget-${key}`).value); }); }
    const task = await jsonApi("/api/tasks", { method: "POST", body: JSON.stringify(request) }); await openTask(task.id); $("task-panel").scrollIntoView({ block: "start", behavior: "smooth" });
  } catch (error) { notice(error.message, true); }
  finally { button.disabled = false; button.textContent = "开始研究 →"; }
});
$("custom-budget").addEventListener("change", () => { budgetFields.forEach(([key]) => { const input = $(`budget-${key}`); if (input) input.disabled = !$("custom-budget").checked; }); });
$("new-research").addEventListener("click", () => { resetTaskView(); $("repo-url").focus(); });
$("reuse-button").addEventListener("click", () => { const request = state.task?.request || {}; $("repo-url").value = request.repo_url || ""; $("question").value = request.question || ""; $("ref").value = request.ref || ""; $("depth").value = request.depth || "standard"; $("custom-budget").checked = !!request.budget; if (request.budget) renderBudgetFields(request.budget); else if (state.config) renderBudgetFields(state.config.default_budget); resetTaskView(); $("question").focus(); });
$("refresh-history").addEventListener("click", () => loadHistory().catch((error) => notice(error.message, true)));
$("pause-button").addEventListener("click", () => taskAction("pause")); $("resume-button").addEventListener("click", () => taskAction("resume"));
$("download-button").addEventListener("click", async () => { try { const response = await api(`/api/tasks/${encodeURIComponent(state.selected)}/report`); const blob = await response.blob(); const url = URL.createObjectURL(blob); const link = element("a"); link.href = url; link.download = "repolens-report.md"; document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000); } catch (error) { notice(error.message, true); } });
$("access-toggle").addEventListener("click", () => { $("auth-panel").hidden = !$("auth-panel").hidden; if (!$("auth-panel").hidden) $("access-token").focus(); });
$("model-toggle").addEventListener("click", () => openModelSettings().catch((error) => notice(error.message, true)));
$("model-close").addEventListener("click", () => { $("model-panel").hidden = true; $("model-api-key").value = ""; });
$("model-form").addEventListener("submit", (event) => { event.preventDefault(); applyModelSettings(false); });
$("model-probe").addEventListener("click", () => applyModelSettings(true));
$("auth-form").addEventListener("submit", async (event) => { event.preventDefault(); const candidate = $("access-token").value.trim(); state.token = candidate; try { await connect(); sessionStorage.setItem("repolens.access", candidate); $("access-token").value = ""; } catch (error) { sessionStorage.removeItem("repolens.access"); state.token = ""; notice(error.message, true); } });
$("clear-token").addEventListener("click", () => { state.token = ""; sessionStorage.removeItem("repolens.access"); $("access-token").value = ""; $("start-button").disabled = true; resetTaskView(); clear($("history")); connect().catch((error) => notice(error.message, true)); });
document.addEventListener("visibilitychange", () => { if (!document.hidden && state.selected && !state.pollTimer) openTask(state.selected).catch((error) => notice(error.message, true)); });
connect().catch((error) => notice(error.message, true));
