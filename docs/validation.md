# 验证记录

日期：2026-10-10（UTC）。依赖安装与网络阻塞已经解决。本文区分真实框架执行、真实 GitHub 数据、模拟模型和未完成的外部交付。最终离线、全新 CLI/UI、重建 Docker 镜像验收均已通过，实际记录已归档。

## 模型检测诊断与 Windows 兼容修复

离线 Linux 套件 **243 passed，8 deselected**，Ruff 通过。新增 MockTransport 验证 400/403/404/422/429/500 状态提示、401 脱敏、可选能力通用 400 降级、完整 endpoint 拒绝。实际 Chromium 的桌面/手机设置与研究流程共 **4 passed，40 deselected**；设置流程验证 HTTP 401 及提示文字，不暴露密钥。没有调用用户真实中转站。

已添加 Windows 原生文件锁与设置保存兼容处理；[本次 CI 38047756535](https://github.com/GimMoliyadi/github-use-agent/actions/runs/38047756535) 在 `c804699` 上 Ubuntu 和 Windows 均成功，原生 Windows **242 passed，1 skipped，8 deselected**，安装与 Ruff 通过；[实际结果 JSON](validation/windows-ci-validation.json)。强制 `fork` 子进程退出用例只在支持 fork 的系统运行；Windows 保留一般暂停/重启恢复测试。POSIX 密钥文件权限为 0600，Windows 继承数据目录的 NTFS 权限。

## 新增 Web 模型配置验收

2026-10-10（Asia/Shanghai）：新增顶部模型配置、Base URL/密码输入/模型 ID、Mock/live 和输出兼容模式、能力检测与保存。服务端 `0600` 原子文件持久化、重启恢复、即刻用于新任务、运行中禁止切换、跨地址禁止复用旧密钥、API/Bearer 认证和错误脱敏均通过。

当前全离线 **233 passed，8 deselected**，全局 Ruff 通过；Chromium 桌面 1440 px 和手机 390 px 的配置→能力检测→保存→刷新→密钥不回显/不进浏览器存储，以及已有研究交互，共 **4 passed，31 deselected**。新增能力检测通过实际 ChatOpenAI/OpenAI SDK 发出请求，全部由 `httpx.MockTransport` 拦截，测试工具/结构化输出成功、降级与认证失败；没有调用真实模型服务。

[桌面配置截图](screenshots/model-settings-1440.png) · [手机配置截图](screenshots/model-settings-390.png)。下方 219 项测试、旧 CI 和 Docker 记录属于前一交付阶段，保留其原始 Commit 与验证范围，不代表新功能已在旧镜像中。

## 上一交付阶段的归档结果

| 验证项 | 实际执行范围 | 已完成结果 |
| --- | --- | --- |
| 完整离线测试 | 真正 LangGraph/SQLite；GitHub 和模型均为 Mock/Fake/MockTransport | **219 passed，6 deselected**；全局 Ruff 通过 |
| Agent 集成测试 | 真正 StateGraph、SqliteSaver、动态工具、报告及恢复 | 17 passed；属于离线套件子集 |
| Service/Storage 测试 | 本地 SQLite、队列、锁、任务历史、服务关闭/重启/恢复 | 22 passed；属于离线套件子集 |
| 模型适配器 | 真正 ChatOpenAI/OpenAI SDK 请求路径，HTTP 和模型响应模拟 | 35 passed；没有真实付费 LLM 请求 |
| Web/API 与浏览器 | 本地 FastAPI；实际 Chromium 1440 px / 390 px | 19 passed，包含 2 个实际浏览器用例 |
| 真实 GitHub pytest | 官方 REST 和真实源码；模型仍为 Mock | 4 passed，56 deselected；最终复测 17.12 秒 |
| sampleproject 完整与定向研究 | 真实 GitHub + 真正 LangGraph + Mock 推理 | 2 个任务完成；各 4 条引用全部独立核验 |
| itsdangerous 浏览器研究 | 实际 Chromium、FastAPI worker、真实 GitHub、Mock 推理 | 9 条验证引用；1440/390 px 无溢出或 JS 错误 |
| Docker Python 3.11 | 实际构建、非 root 启动、health/static/API、真实 GitHub 任务 | 最终镜像通过；quick 研究完成并验证 3 条引用 |
| 用户指定中转站 | 真实聊天、工具调用、结构化输出及研究质量 | 缺少真实模型配置，未调用 |
| 托管 GitHub Actions | 实际 GitHub runner、CPython 3.11.17、冻结安装、Ruff、离线测试 | SUCCESS；Ruff 通过；219 passed / 6 deselected，4.62 秒 |
| 公网 Web 服务 | 公开托管环境 | 未部署 |
| 公开 GitHub 仓库 | 用户授权既有公开空仓库，首次 main push | 已成功发布完整历史；初始提交 7751310 |

套件数量存在重叠，不能把模块测试数量加到完整离线数量上。`deselected` 表示通过 pytest marker 排除的其他用例，不表示被排除用例已通过。证据预算优化增加了 7 个离线用例；此前完整离线结果为 212 passed，6 deselected，最终离线结果为上表的 219 passed。

最终执行记录已归档：[CLI 结果 JSON](validation/results.json)、[UI 结果 JSON](validation/ui-validation.json)、[Docker 结果 JSON](validation/docker-validation.json)。此前 sampleproject 的 13/10 引用属于优化前记录，最新结果为各 4 条；两轮结果分别记录，不混作同一次运行。

## 真实 GitHub 研究与引用核验

Python GitHubClient 直接访问官方 REST，真正 LangGraph 执行了以下两个研究任务。仓库为 [pypa/sampleproject 固定版本](https://github.com/pypa/sampleproject/tree/621e4974ca25ce531773def586ba3ed8e736b3fc)，Commit 为 `621e4974ca25ce531773def586ba3ed8e736b3fc`。

| 用例 | 问题范围 | 实际结果 |
| --- | --- | --- |
| overview | Python 架构、技术栈、打包配置和入口 | completed；4 个文件；4 条核验引用 |
| targeted | src/sample/__init__.py 的 sample.main 定义、入口配置和静态调用位置 | completed；4 个文件；4 条核验引用 |

两个任务各记录 10 次探索、7 次研究 GitHub 请求、0 次 LLM 调用。核验脚本使用与研究任务不同的缓存，从固定 Commit 重新读取每条引用对应的完整文件，核对仓库名、完整 SHA、路径、Git Blob SHA、完整字节、LF 行号、原文和永久链接，并非只检查首条引用或 URL 字符串格式。无效仓库子页 URL 也被正确拒绝，未请求外部 API。

最终记录时间为 `2026-10-10T07:09:08.767800+00:00`。公开归档包含 [结果 JSON](validation/results.json)、[overview 报告](validation/overview-report.md)、[targeted 报告](validation/targeted-report.md)。原始执行数据在本地 `data/live-validation-final/`，该目录被 Git 忽略。较早的 13/10 引用记录时间为 `2026-10-10T07:00:35.509009+00:00`，保留在本地 `data/live-validation-success/`。

最终真实 Web 任务分析了 [pallets/itsdangerous 固定版本](https://github.com/pallets/itsdangerous/tree/672971d66a2ef9f85151e53283113f33d642dabd)，绑定 Commit `672971d66a2ef9f85151e53283113f33d642dabd`。研究 Signer.sign、Signer.unsign 和 Serializer.loads 的代码入口与静态关系，取得 9 条验证引用，10 次探索、3 个文件、14,491 字节、6 次 API 请求、0 次 LLM 调用，停止原因为 `model_sufficient`。记录时间 `2026-10-10T07:09:02.336767+00:00`；[UI 结果 JSON](validation/ui-validation.json) 与 [itsdangerous 报告](validation/itsdangerous-report.md) 已归档。其源码、浏览器和研究 worker 是真实执行，策略与文字由明确标记的 Mock 生成，不能据此评价真实 LLM 的研究质量。

## 恢复、预算与模型兼容性

Agent 和服务测试实际验证了无效 URL、固定版本、定向函数发现、API 故障和限流、重复操作停止、迭代/API/模型报告预算耗尽、引用真实性、节点边界暂停、新 runner 和服务重启后的手动恢复。

强制中断用例创建了真正的 POSIX 子进程：模型请求前保守预留写入独立 SQLite 账本后执行 `os._exit(23)`，父进程创建新 runner，恢复同一 LangGraph thread。已验证原 Commit、检查点、1 次调用和 777 Token 预留均保留。另有初始化树请求中断、默认分支变化后保留原 SHA，以及异常重试不得突破探索轮数预算的用例。这些测试中的仓库、HTTP 和模型响应是模拟数据。

模型协议测试验证聊天返回值、真正的工具参数和结构化对象语义，拒绝仅凭 HTTP 200 判断兼容性；不支持可选特性时采用严格 schema 校验的 JSON 文本。测试还检查 SDK 发出的请求确实带有输出上限、SDK 自动重试关闭、应用重试逐次计数、错误内容脱敏、缺失 usage 时保留预留，以及客户端释放。

预留账本在请求前持久化，LangGraph 使用 `durability="sync"`。强制退出后提供商可能已经收费，恢复会重试，因此不承诺付费 HTTP exactly-once。提供商 tokenization/计费可能与输入估算不同。美元限制只有输入和输出价格均配置时可估算并执行，未配置时显示“费用未知”。引用校验确认来源存在和完整性，不能自动证明所有自然语言推断或运行时调用关系。

最终证据优化限制每个符号/追踪动作生成的引用，复用覆盖范围并合并相邻调用行。索引发现但未附源码的项目显式标记为 `index_only`；应按研究目标读取相应行段后再把它们作为报告证据。相关 7 个新增离线用例已通过，最新真实任务也通过独立引用复核。

## Web 与 Docker

19 个 Web 测试包含两个真实 Chromium 视口：1440 px 桌面和 390 px 手机，验证了任务交互、报告阅读、代码证据展示和不可信内容的安全渲染。浏览器自动化用例使用本地模拟任务服务；上面的真实 itsdangerous 研究属于另一项真实 worker 验收，不能把两者的数据源混写。

- [桌面截图](screenshots/desktop.png)
- [手机截图](screenshots/mobile.png)

最终 Python 3.11 镜像 ID 为 `sha256:672770f2821cb83a7bffe9caad93dabfd23a047f4ca21b0ceefe1a42c7584ed0`。实际以非 root 用户 UID 10001 运行，health、静态页面、脱敏 config、API 与报告下载通过；真实 pypa/sampleproject quick 任务完成 6 次探索、3 个文件、253 字节、6 次 API 请求、3 条验证引用、0 次 LLM 调用，在 `iteration_budget` 合理结束并生成有限报告。记录时间 `2026-10-10T07:11:33.437425+00:00`；详细结果见 [Docker 验证 JSON](validation/docker-validation.json)。Dockerfile 使用 `requirements.lock` 与 `pip --require-hashes` 安装冻结生产依赖，再用 `--no-deps` 安装本地项目。Docker 验证的是本地容器，托管 GitHub Actions 也已成功，公网 Web 尚未部署。

## 可复现命令

从仓库根目录执行。`uv.lock` 已生成，CI 使用冻结依赖安装。

```bash
uv sync --frozen --extra dev
uv run --no-sync pytest -m "not live and not browser" -q
uv run --no-sync ruff check .
CHROMIUM_EXECUTABLE_PATH=/usr/bin/chromium RUN_BROWSER_TESTS=1 uv run --no-sync pytest tests/test_web.py -q
RUN_LIVE_TESTS=1 uv run --no-sync pytest -m live -q
RUN_LIVE_TESTS=1 uv run --no-sync python examples/check_live.py --output-dir data/live-validation
RUN_LIVE_TESTS=1 RUN_BROWSER_TESTS=1 CHROMIUM_EXECUTABLE_PATH=/usr/bin/chromium uv run --no-sync python examples/check_ui_live.py --data-dir data/ui-validation
uv run --no-sync repo-research probe --mode mock
```

首次安装浏览器可执行 `uv run --no-sync playwright install chromium`；已有兼容 Chromium 时使用其实际路径。实时 GitHub 检查会消耗请求额度。两个 live 检查脚本强制 Mock，即使环境配置 live，也不会调用真实 LLM。`check_ui_live.py` 同时需要 GitHub 和浏览器 opt-in，用于运行实际 Web worker 的真实仓库任务并保存执行记录。

真实中转站验收仍待用户提供 `LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL`。配置后先执行 `uv run --no-sync repo-research probe --mode live`，记录聊天、工具、JSON Schema 的独立结果，再完成小预算研究。该探测可能计费；不得换用其他付费 API。没有真实密钥的当前成功记录均不能写成真实模型验证。

## 发布状态与历史

已发布至用户授权的公开仓库 [GimMoliyadi/github-use-agent](https://github.com/GimMoliyadi/github-use-agent)。Root 验证仓库 `isPrivate=false`、`viewerPermission=ADMIN` 且无已有 refs 后，配置 `origin=https://github.com/GimMoliyadi/github-use-agent.git` 并首次正常推送 `main`，完整历史发布于 [77513106a475b4efe9b3beac3608636e3f7737f4](https://github.com/GimMoliyadi/github-use-agent/commit/77513106a475b4efe9b3beac3608636e3f7737f4)，未使用 force。

此前 GitHub CLI 身份已验证，但 GraphQL `createRepository` 与官方 REST `POST /user/repos` 的创建权限均遭拒绝，REST 返回 HTTP 403 `Resource not accessible by integration`。之后用户提供现有的空授权仓库，首次推送成功，解决了发布阻塞；未另外创建仓库。

实际托管 [Offline checks run 38034286129](https://github.com/GimMoliyadi/github-use-agent/actions/runs/38034286129) 已完成 **SUCCESS**，对应完整 head `77513106a475b4efe9b3beac3608636e3f7737f4`，创建时间 `2026-10-10T07:23:56Z`，更新时间 `2026-10-10T07:24:14Z`。实际日志确认 CPython **3.11.17**、Ruff `All checks passed!`、离线测试 **219 passed，6 deselected，4.62 秒**。冻结安装和完整托管结果归档于 [GitHub Actions 验证 JSON](validation/github-actions-validation.json)。这是实际托管结果，不是本地测试的代称；该离线 workflow 没有运行真实 GitHub 研究或真实 LLM。真实付费模型仍缺少配置，公开仓库发布不代表已部署公网 Web 服务。

早期依赖和浏览器网络权限阻塞已经解决，保留在 `PROGRESS.md` 历史区。此前的连接器源码读取、本地静态快照重放和 StubService HTTP 冒烟只证明当时各自有限的范围；已通过的真实 Python REST、LangGraph、浏览器和 Docker 结果如上，取代旧文档中的当前“尚未执行”状态。
