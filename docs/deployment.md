# 本地与部署指南

## 本地服务

```bash
uv sync --frozen --extra dev
cp .env.example .env
uv run repo-research serve --host 127.0.0.1 --port 8000
```

默认 `MODEL_MODE=mock`；不需要 LLM 密钥，但研究公开仓库需要访问 `https://api.github.com`。可选 `GITHUB_TOKEN` 提高 GitHub REST 额度。`DATA_DIR` 默认为 `./data`；该目录包含任务历史、LangGraph 检查点、资源账本、不可变源码缓存和进程锁。

研究问题、源码和模型输出会发送到用户配置的模型中转站，live 模式部署应选择你愿意接收这些数据的服务。LLM 凭据仅由服务端读取。

无需编辑环境变量即可接入模型：打开页面顶部“模型配置”，填写 Base URL、API Key、模型名称，检测后保存。新任务立即生效；服务端 `DATA_DIR/model-settings.json` 持久化配置，文件权限 `0600`，包含明文密钥，备份时应作为私有凭据保存。页面和任务 API 不返回密钥，浏览器不保存密钥。已保存的 UI 模型配置优先于环境变量；显式 CLI `--mode mock` 不加载保存配置。配置是共享的，仅在无运行任务时可修改，`APP_ACCESS_TOKEN` 同样保护模型配置与检测接口。

## Docker

已实际构建 Python 3.11 镜像并验证健康检查、静态界面、任务 API 和真实 GitHub 研究。运行记录见 [验收文档](validation.md)。

```bash
docker build -t repolens:local .
docker volume create repolens-data
docker run --rm --name repolens \
  -p 127.0.0.1:8000:8000 \
  --env-file .env \
  -e DATA_DIR=/data \
  -v repolens-data:/data \
  repolens:local
```

镜像使用 Python 3.11，通过 `requirements.lock` 固定依赖版本并校验下载哈希，默认 Mock，以非 root 用户运行单个 Uvicorn 进程。镜像中不包含 `.env`、`data/` 或 Git 历史。命名卷保存持久状态；不要把密钥写入镜像或 build 参数。使用主机目录挂载时，确保运行用户 UID/GID 10001 对该目录具有写权限。

本地和 CI 使用 `uv.lock`。修改依赖后运行 `uv lock`，再同步容器锁：

```bash
uv export --frozen --no-dev --no-emit-project --output-file requirements.lock
```

若云环境通过 HTTPS 代理且要求自定义 CA，构建时使用 BuildKit secret，运行时只读挂载 CA；不要禁用证书验证或将环境凭据写入镜像：

```bash
docker build --secret id=proxy_ca,src=/path/to/proxy-ca.pem -t repolens:local .
docker run --rm -p 127.0.0.1:8000:8000 \
  --mount type=bind,src=/path/to/proxy-ca.pem,dst=/run/proxy-ca.pem,readonly \
  -e SSL_CERT_FILE=/run/proxy-ca.pem \
  -v repolens-data:/data repolens:local
```

按环境要求传递 `HTTP_PROXY`/`HTTPS_PROXY`/`NO_PROXY`，保留平台的代理路由。常规环境不需要额外 CA。

live 模式通过 `--env-file` 或宿主环境注入 `MODEL_MODE=live`、`LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL`。使用前运行 CLI probe 验证中转站：

```bash
docker run --rm --env-file .env repolens:local repo-research probe --mode live
```

probe 会请求配置的真实 API，并可能计费；无密钥时不要运行 live probe。

## 对外开放

设置一个足够长的随机 `APP_ACCESS_TOKEN`；浏览器“访问令牌”处输入该值。全部 `/api` 请求需 `Authorization: Bearer ...`。服务仍是共享研究空间，不是具备账号和独立租户隔离的多用户产品；获得同一访问令牌的人可读取任务历史并使用配置的模型。

建议在反向代理后提供 HTTPS，限制请求频率和入口大小。FastAPI API 自身校验字段与预算范围。当前限制最多四个活动/排队任务；公网请求额度和成本策略应由部署者设置。`/health` 公开提供存活检测，不暴露凭据。

只启动一个服务进程，不要加 `--workers 2` 或水平扩展并共享本地 SQLite。`ResearchService` 对 DATA_DIR 获取排他文件锁；同一目录的另一个进程会拒绝启动。需要多副本时，应先替换为集中式任务队列、数据库和明确的任务所有权机制。

## 暂停、恢复与备份

Web UI 在研究时提供暂停；暂停是协作式的，当前 HTTP 请求完成并到达图节点边界后保存。CLI research 中 Ctrl+C 同样请求保存，不代表立即取消外部请求。

```bash
uv run repo-research list
uv run repo-research resume TASK_ID --output data/resumed-report.md
```

CLI resume 与 Web 服务不能同时拥有同一个 DATA_DIR。先停止 Web，再执行 resume，或直接用 Web 的“继续”。恢复时保持原 `MODEL_MODE`，保留原始 DATA_DIR；不要把已有 Mock 任务切换为 live，或删除 checkpoints/cache/usage 数据。live 任务还校验模型、Base URL、输出模式与计费配置的指纹；密钥轮换允许，修改上述配置后拒绝恢复以避免版本和成本混淆。

重启将未完成任务标记为 interrupted，由用户手动继续。额度窗口或开发任务中断后，新任务先阅读 `AGENTS.md`、`PROGRESS.md`，检查 Git 工作区，运行离线测试，再启动应用或恢复历史。

备份前停止服务，复制整个 DATA_DIR；避免仅复制正在写入的 SQLite 主文件而遗漏 WAL。数据目录不应提交到公开仓库。资源账本保守保存发出请求前的预留；强制终止后恢复可能再次请求模型，最终费用需核对提供商账单。

## 验证与排错

- 仓库 URL 被拒绝：使用 `https://github.com/OWNER/REPO`，Ref 单独填写。
- GitHub 速率限制：等待冷却后手动继续，或配置 `GITHUB_TOKEN`；不要重复快速恢复。
- 大文件/二进制/树截断：报告显示有限覆盖，改小研究目标或提高适用预算。
- 中转站能力不兼容：运行 `repo-research probe --mode live`，查看聊天、工具调用、JSON Schema 的独立结果；必要时选择 `LLM_OUTPUT_MODE=json_text`。
- 费用显示未知：同时配置输入与输出每百万 Token 单价；零值表示未配置，不表示免费。
- 同目录服务已存在：停止原进程，或为新服务指定不同的 `--data-dir`，不要删除锁文件来绕过持有锁的进程。

离线检查 `uv run pytest -m "not live and not browser"`；真实 GitHub 检查 `RUN_LIVE_TESTS=1 uv run python examples/check_live.py`；真实 LLM 测试仅在用户提供中转站配置并授权后运行。各类结果要分别记录，不把 Mock 的成功记成真实模型验证。
