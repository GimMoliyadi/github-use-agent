# 架构与证据约束

RepoLens 使用一个 LangGraph 研究 Agent。研究目标、模型决策、工具结果、预算、进度和报告保存在图状态中，恢复时重建临时客户端，继续固定版本上的工作。

## 层次与主要数据

`models.py` 定义严格的 Pydantic 合约：`ResearchRequest`、`Budget`、`Usage`、`RepositoryInfo`、`TreeListing`、`SourceFile`、`ToolAction`、`Evidence` 和 `ReportDraft`。未知字段被拒绝；模型产出的动作和报告先通过 schema 验证，再进入应用逻辑。

`github.py` 只读取官方 GitHub REST。仓库 URL 限定 HTTPS 的 `github.com/owner/name` 根地址，拒绝凭据、查询、其他站点和 tree/blob 子页。初始化取得默认分支或用户 Ref 的完整 Commit SHA 及 Tree SHA。文件来自该 SHA 下的 Contents API，或已经索引的 Git Blob；下载字节按 Git 对象格式重新计算 SHA-1。对于无法完整解码、大小不一致、哈希不一致或非 UTF-8 文本，读取失败，禁止引用。

`github_cache.py` 使用 SQLite WAL 缓存不可变对象。分支、标签和默认分支属于可变引用，解析时重新请求；完整 Commit、Tree、Blob 和 Commit+path 内容可复用。递归 Git Tree 被截断时执行有限的广度分层遍历，达到请求或条目上限时保留可用部分，并输出明确的不完整警告。Git Trees API 不提供分页；项目不会把伪造的分页视为完整性保证。

`llm.py` 通过 LangChain `ChatOpenAI` 明确使用 Chat Completions，`LLM_BASE_URL`/`LLM_API_KEY`/`LLM_MODEL` 指向用户中转站。应用单独管理超时、有限重试和每次请求预算，SDK 自动重试被关闭。能力检测验证聊天返回值、实际工具参数和 JSON Schema 值；协议不支持时退回 Pydantic 校验的 JSON 文本。Mock 仅模拟决策和草稿，始终标记来源。

`tools.py` 执行读文件、文本搜索、符号和引用追踪。下载预算与片段预算分开：保存完整合格文本用于引用校验，但只向模型显示有限行数和有限搜索结果。Python 采用 `ast`，其他语言的符号发现属于有限的语法文本线索。静态跨文件候选不等同于运行时调用关系。

`report.py` 校验每条证据的仓库、Commit、路径、字节、Blob、LF 行号、原文、ID 和 URL。事实/推断必须引用已验证证据；无来源结论降级未知，仅文档或配置支撑的实现事实降为推断。自然语言摘要单独标注为未逐项验证。输出 Markdown 对模型和仓库文本做安全转义，代码片段按需要扩大 fenced-code 标记，永久链接仅由应用构造。

## LangGraph 节点与动态路由

实现使用 `StateGraph`、`START`/`END`、`SqliteSaver`、`interrupt` 和 `Command(resume=...)`。每个工具动作通过研究问题、已有证据和剩余预算决定，不依赖预设项目文件清单。

| 节点 | 责任 | 正常后继 |
| --- | --- | --- |
| initialize | 解析并固定仓库版本、取得有限目录树 | gate |
| gate | 在节点边界检查用户暂停 | decide |
| decide | 生成经过 schema 校验的下一动作与计划 | explore 或 draft |
| explore | 执行确定性只读工具，记录结果与支出 | assess |
| assess | 判断预算与新增信息是否值得继续 | gate 或 draft |
| draft | 生成有 evidence ID 的报告草稿；预算不足时保留有限证据 | verify |
| verify | 校验引用、降级无来源结论、渲染报告 | END |
| recover | 对可恢复故障设置 interrupt，等待明确继续 | 原失败节点 |

统一节点包装区分确定性输入失败、预算耗尽、可恢复网络错误和模型失败。不可访问的仓库或无效 Ref 不会伪造报告；速率限制和暂时故障保留已保存状态，并等待恢复。重复动作和连续三次无新增信息会终止探索；`recursion_limit` 是额外的图执行保护。

图状态保存完整源文件和证据；发送给模型的目录、观察和证据视图另有字节/数量上限。大仓库探索是预算内抽样；报告始终说明实际文件覆盖范围。

## 持久化、任务服务与恢复

`storage.py` 保存任务元信息和供 UI 查询的进度快照；图检查点保存精确执行位置。资源计数账本独立记录发出请求前的 API 次数及模型 Token/费用预留，用于强制中断后的保守恢复。SQLite 使用短事务、WAL 和 busy timeout。

`service.py` 通过有限线程池运行任务，工作请求不阻塞 HTTP。默认两个工作线程，最多四个活动/排队任务。`fcntl` 文件锁限制同一目录的单服务进程，防止另一进程启动时误标记正在运行的任务。服务重启将未完成任务标为 interrupted，用户通过 UI 或 `repo-research resume TASK_ID` 明确恢复；不会自动发起可能付费的工作。

正常的节点边界暂停由 LangGraph checkpoint 保证继续位置；强制杀进程可能发生在网络请求发出后、响应落盘前。预留账本保守保留这次支出，但外部提供商可能已收费且恢复时再次请求，因此不承诺付费 HTTP 的 exactly-once。用户应结合中转站账单核对实际费用。

## 预算与成本

控制探索轮数、文件数、累计字节、API 请求、树条目、证据条数、模型调用、Token 和美元费用。API 重试与失败消耗次数；模型调用在发送前预留输入上界和输出额度，失败或缺失 usage 元信息时保守保留预留，恢复不重置已花费资源。

输入估算使用 UTF-8 字节与消息/schema 开销，适用于普通 OpenAI tokenizer 的保守控制，但第三方 tokenizer 和最终收费规则无法由应用证明。若提供商返回 usage，以其记录结算；超过限制后终止进一步调用，无法撤销已发送的请求。美元预算需同时配置输入/输出每百万 Token 价格；否则显示费用未知。

## 安全与分析边界

目标仓库绝不运行。README、注释、测试代码和源码只是数据，模型系统提示明确拒绝其中的指令；可用动作只有应用定义的只读操作。获取数据时限制主机、路径、响应字节、编码、文件大小和错误输出；API 异常正文、凭据及带敏感信息的 transport URL 不进入报告或日志。

公开部署通过 `APP_ACCESS_TOKEN` 保护全部 `/api` 路由；静态页面和 `/health` 公开。页面通过 DOM 安全渲染代码与文字，不把仓库或模型文本当作 HTML。访问令牌保存在浏览器当前 sessionStorage，服务端模型/GitHub 密钥不会返回浏览器。

初版不提供运行时跟踪、任意 shell、克隆后安装、分布式任务队列、私人仓库产品保障或真实模型质量保证。报告引用校验验证来源的存在与准确性，结论是否被证据充分支持仍需人工判断。

## 相关官方文档

- [LangGraph 持久化](https://docs.langchain.com/oss/python/langgraph/persistence)
- [LangGraph Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)
- [GitHub Git Trees REST](https://docs.github.com/en/rest/git/trees)
- [GitHub Git Blobs REST](https://docs.github.com/en/rest/git/blobs)
- [GitHub Contents REST](https://docs.github.com/en/rest/repos/contents)
- [GitHub 速率限制](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api)
