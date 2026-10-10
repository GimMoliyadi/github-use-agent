# 验证记录

日期：2026-10-10（Asia/Shanghai）。只记录实际执行结果；不把编译、Mock、连接器读取混为真实端到端测试。

| 验证项 | 数据/模型 | 状态 |
| --- | --- | --- |
| Python 模块与测试 compileall | 本地代码 | 已通过 |
| 前端 `node --check` | 本地 JS | 已通过 |
| 静态符号、调用与引用校验 | GitHub 连接器实时读取，随后本地快照重放；无 LLM/LangGraph | 已通过 |
| TaskStore WAL、重启标中断、任务用量保留与单进程锁 | 本地 SQLite 手工冒烟 | 已通过 |
| FastAPI 真实进程内 HTTP 请求 | httpx ASGITransport + StubService；无外部 API | 已通过 |
| pytest 全套单元/集成 | GitHub HTTP Mock + Mock/Fake 模型；真正 LangGraph | 已编写，依赖尚未安装，未执行 |
| 直接 Python GitHub REST 全流程 | 真实 GitHub + Mock 模型 | 待网络权限及依赖安装 |
| 响应式浏览器 | 本地 StubService + Playwright Chromium | 待依赖及浏览器安装 |
| 中转站聊天、工具调用、JSON schema | 用户指定真实付费模型 | 缺少配置，未调用 |
| Docker build / 公开部署 | 真实部署 | 未执行 |
| 远端 GitHub 发布 | 授权远端 | 未执行；当前无 git remote |

## 已执行的真实源码检查

仓库：`pypa/sampleproject`。固定 Commit：`621e4974ca25ce531773def586ba3ed8e736b3fc`。

通过已安装的 GitHub 连接器访问官方 REST API，读取 metadata、commit、tree 和四个 blob：README.md、pyproject.toml、src/sample/__init__.py、src/sample/simple.py。所有下载内容都重新计算 `sha1(b'blob ' + byte_length + b'\0' + raw_bytes)`，与 GitHub blob SHA 一致。

`PYTHONPATH=src python examples/validate_snapshot.py` 实际运行通过：4 个 blob、1 个函数定义、1 个静态调用、3 条源码引用。完整快照及可点击固定 SHA 报告保存在 examples/。这是连接器实时读取后的**本地静态重放**，不代表 Python GitHubClient 直连 API 或完整 LangGraph 测试通过。

## 本地 HTTP 冒烟

实际使用系统 Python 自带 FastAPI/httpx、ASGITransport 和 StubService：页面/static/health/config、启动/历史/查询、暂停/恢复、下载报告、400/404/409/422/429/502、Retry-After、未知异常脱敏、Bearer 认证、关闭生命周期均通过。未启动实际研究 worker；不能代替完整端到端验收。

## 下一轮验收命令

```bash
UV_CACHE_DIR=/tmp/repo-research-uv-cache uv sync --extra dev
uv run --no-sync pytest -q
uv run --no-sync ruff check .
uv run --no-sync playwright install chromium
RUN_BROWSER_TESTS=1 uv run --no-sync pytest tests/test_web.py -m browser -q
RUN_LIVE_TESTS=1 uv run --no-sync pytest -m live -q
uv run --no-sync python examples/check_live.py
```

真实模型验收：设置 `MODEL_MODE=live` 与 `LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL`，执行 `repo-research probe`，确认语义探测结果后运行一个小预算研究。无凭据时不要替换其他付费 API。

命令沙箱默认禁用网络；依赖安装的额外网络权限请求此前被用户中止。需要批准该权限才能完成当前未执行项。
