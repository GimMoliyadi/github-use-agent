"""HTTP interface for durable research tasks and the self-hosted RepoLens UI."""

from __future__ import annotations

import hmac
import inspect
import logging
import math
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from .config import Settings
from .models import Budget, ResearchRequest
from .provider_settings import ModelConfiguration, public_model_settings

if TYPE_CHECKING:
    from .service import ResearchService

STATIC_DIR = Path(__file__).with_name("static")
logger = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None, service: ResearchService | None = None
) -> FastAPI:
    """Create an app without constructing a service at module import time.

    Service operations are synchronous and run in a thread pool. Research itself
    runs in the service's bounded worker pool, so an HTTP request only schedules
    work and never waits for a complete investigation.
    """
    settings = settings or Settings()
    if service is None:
        from .service import ResearchService

        service = ResearchService(settings)
    research_service = service
    access_token = settings.app_access_token.get_secret_value()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            yield
        finally:
            await invoke(research_service.close)

    app = FastAPI(title="RepoLens", version="0.1.0", lifespan=lifespan)
    app.state.research_service = research_service

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request, exception):
        # Validation responses must never echo API keys, even when another field fails.
        return JSONResponse(status_code=422, content={"detail": [
            {"loc": list(item["loc"]), "msg": item["msg"], "type": item["type"]}
            for item in exception.errors()
        ]})

    async def authorize(authorization: str | None = Header(default=None)) -> None:
        if not access_token:
            return
        scheme, _, supplied = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(
            supplied.encode("utf-8"), access_token.encode("utf-8")
        ):
            raise HTTPException(
                status_code=401,
                detail="需要有效的访问令牌。",
                headers={"WWW-Authenticate": "Bearer"},
            )

    async def invoke(method, *args, **kwargs) -> Any:
        if inspect.iscoroutinefunction(method):
            return await method(*args, **kwargs)
        result = await run_in_threadpool(method, *args, **kwargs)
        return await result if inspect.isawaitable(result) else result

    async def task_operation(method, *args, **kwargs) -> Any:
        try:
            return await invoke(method, *args, **kwargs)
        except KeyError:
            raise HTTPException(status_code=404, detail="研究任务不存在。") from None
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        except Exception as exc:
            from .github import GitHubError
            from .llm import ModelError

            if isinstance(exc, ModelError):
                hints = {
                    400: "中转站拒绝请求，请核对模型名称和请求参数兼容性。",
                    401: "认证失败，请核对该中转站的 API Key。",
                    403: "访问被拒绝，请核对密钥的模型权限和服务商访问限制。",
                    404: "接口或模型不存在，请核对 Base URL 前缀和模型名称。",
                    422: "请求格式不兼容，请核对中转站的聊天补全协议。",
                    429: "请求受限，可能是频率、配额或余额限制，请查看中转站控制台。",
                }
                hint = hints.get(exc.http_status, "请核对中转站配置及服务状态。")
                if exc.code == "timeout":
                    hint = "请求超时，请检查网络或调整 LLM_TIMEOUT。"
                elif exc.code == "connection failure":
                    hint = "连接失败，请检查地址、网络、代理和证书。"
                elif exc.http_status and exc.http_status >= 500:
                    hint = "中转站服务异常，请稍后重试或联系服务商。"
                status = f"HTTP {exc.http_status}，" if exc.http_status else ""
                stages = {"chat": "聊天", "tools": "工具调用", "json_schema": "结构化输出"}
                stage = stages.get(exc.stage, "模型")
                raise HTTPException(
                    status_code=502,
                    detail=f"{stage}检测失败（{status}{exc}）。{hint}",
                ) from None
            if isinstance(exc, GitHubError):
                if exc.code in ("invalid_url", "invalid_ref", "invalid_path"):
                    raise HTTPException(status_code=400, detail=str(exc)) from None
                if exc.code == "rate_limit":
                    headers = {}
                    if exc.retry_after is not None and math.isfinite(exc.retry_after):
                        headers["Retry-After"] = str(max(1, math.ceil(exc.retry_after)))
                    raise HTTPException(
                        status_code=429,
                        detail="GitHub 请求受到限制。请稍后重试，或配置 GITHUB_TOKEN。",
                        headers=headers,
                    ) from None
                if exc.code == "not_found":
                    raise HTTPException(
                        status_code=404, detail="GitHub 仓库或指定版本不存在，或无法公开访问。"
                    ) from None
                raise HTTPException(
                    status_code=502, detail="GitHub 暂时无法访问，请稍后重试。"
                ) from None
            # Do not expose upstream responses, credentials or internal paths.
            logger.error("HTTP service operation failed: %s", type(exc).__name__)
            raise HTTPException(
                status_code=500, detail="请求处理失败，请查看服务日志。"
            ) from None

    api = APIRouter(prefix="/api", dependencies=[Depends(authorize)])

    @api.get("/config")
    def config() -> dict[str, Any]:
        current = getattr(research_service, "settings", settings)
        return {
            "mode": current.model_mode,
            "model": current.llm_model if current.model_mode == "live" else "deterministic-mock",
            "default_budget": Budget().model_dump(),
            "access_token_required": bool(access_token),
        }

    @api.get("/model")
    async def model_configuration():
        if hasattr(research_service, "model_configuration"):
            return await invoke(research_service.model_configuration)
        return public_model_settings(settings)

    @api.put("/model")
    async def configure_model(configuration: ModelConfiguration):
        return await task_operation(research_service.configure_model, configuration)

    @api.post("/model/probe")
    async def probe_model(configuration: ModelConfiguration):
        return await task_operation(research_service.probe_model, configuration)

    @api.get("/tasks")
    async def list_tasks(limit: int = Query(default=30, ge=1, le=100)):
        return await task_operation(research_service.list_tasks, limit=limit)

    @api.post("/tasks", status_code=202)
    async def start_task(request: ResearchRequest):
        return await task_operation(research_service.start, request)

    @api.get("/tasks/{task_id}")
    async def get_task(task_id: str):
        return await task_operation(research_service.get_task, task_id)

    @api.post("/tasks/{task_id}/pause", status_code=202)
    async def pause_task(task_id: str):
        return await task_operation(research_service.pause, task_id)

    @api.post("/tasks/{task_id}/resume", status_code=202)
    async def resume_task(task_id: str):
        return await task_operation(research_service.resume, task_id)

    @api.get("/tasks/{task_id}/report")
    async def download_report(task_id: str):
        task = await task_operation(research_service.get_task, task_id)
        markdown = task.get("report_markdown")
        if not markdown:
            raise HTTPException(status_code=409, detail="该任务尚未生成报告。")
        # The fixed filename prevents unsafe characters from entering headers.
        return Response(
            markdown,
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="repolens-report.md"'},
        )

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(
            STATIC_DIR / "index.html",
            headers={
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "same-origin",
                "Content-Security-Policy": (
                    "default-src 'self'; script-src 'self'; "
                    "style-src 'self' 'unsafe-inline'; connect-src 'self'; "
                    "img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; "
                    "form-action 'self'; object-src 'none'"
                ),
            },
        )

    app.include_router(api)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app
