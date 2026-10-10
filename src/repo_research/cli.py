"""Command-line access to the same research service and persisted task history."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

from .config import Settings
from .github import GitHubError
from .llm import ModelError, make_model
from .models import Budget, ResearchRequest, Usage
from .service import ResearchService
from .storage import TaskStore


def _settings(arguments: argparse.Namespace) -> Settings:
    overrides = {}
    if arguments.mode is not None:
        overrides["model_mode"] = arguments.mode
    if arguments.data_dir is not None:
        overrides["data_dir"] = arguments.data_dir
    return Settings(**overrides)


def _print_json(value) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def _wait_for_task(service: ResearchService, task_id: str) -> dict:
    seen = None
    print(f"Task: {task_id}", file=sys.stderr)
    try:
        while True:
            task = service.get_task(task_id)
            progress = (task["status"], len(task["progress"]))
            if progress != seen:
                print(f"{progress[0]}: {progress[1]} saved steps", file=sys.stderr)
                seen = progress
            if task["status"] not in {"pending", "running"}:
                return task
            time.sleep(0.25)
    except KeyboardInterrupt:
        try:
            service.pause(task_id)
        except ValueError:
            pass
        print("Pause requested; saving checkpoint before exit.", file=sys.stderr)
        service.close()
        return service.get_task(task_id)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="repo-research", description="Commit-pinned GitHub research with code evidence."
    )
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--mode", choices=["mock", "live"], default=None)
    common.add_argument("--data-dir", type=Path, default=None)
    subcommands = parser.add_subparsers(dest="command", required=True)
    serve = subcommands.add_parser("serve", parents=[common], help="Start the web interface")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    research = subcommands.add_parser("research", parents=[common], help="Research a repository")
    research.add_argument("repo_url")
    research.add_argument("--question", required=True)
    research.add_argument("--ref", default=None)
    research.add_argument("--depth", choices=["quick", "standard", "deep"], default="standard")
    research.add_argument("--output", type=Path, help="Save the final Markdown report")
    research.add_argument("--max-iterations", type=int)
    research.add_argument("--max-files", type=int)
    research.add_argument("--max-api-requests", type=int)
    research.add_argument("--max-llm-calls", type=int)
    research.add_argument("--max-tokens", type=int)
    research.add_argument("--max-cost-usd", type=float)
    resume = subcommands.add_parser("resume", parents=[common], help="Resume an interrupted task")
    resume.add_argument("task_id")
    resume.add_argument("--output", type=Path)
    history = subcommands.add_parser("list", parents=[common], help="Read stored task history")
    history.add_argument("--limit", type=int, default=30)
    subcommands.add_parser("probe", parents=[common], help="Test configured model API capabilities")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    arguments = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    try:
        settings = _settings(arguments)
        if arguments.command == "serve":
            import uvicorn

            from .web import create_app

            uvicorn.run(create_app(settings), host=arguments.host, port=arguments.port, workers=1)
            return 0
        if arguments.command == "list":
            _print_json(TaskStore(settings.data_dir).list(arguments.limit))
            return 0
        if arguments.command == "probe":
            usage = Usage()
            model = make_model(settings, Budget(), usage)
            try:
                capabilities = model.probe()
                _print_json(
                    {"capabilities": capabilities.model_dump(), "usage": usage.model_dump()}
                )
                return 0 if capabilities.chat or settings.model_mode == "mock" else 1
            finally:
                close = getattr(model, "close", None)
                if callable(close):
                    close()

        service = ResearchService(settings)
        try:
            if arguments.command == "research":
                request = ResearchRequest(
                    repo_url=arguments.repo_url, question=arguments.question,
                    ref=arguments.ref, depth=arguments.depth,
                )
                overrides = {
                    name: getattr(arguments, name)
                    for name in (
                        "max_iterations", "max_files", "max_api_requests", "max_llm_calls",
                        "max_tokens", "max_cost_usd",
                    )
                    if getattr(arguments, name) is not None
                }
                if overrides:
                    from .service import budget_for_request

                    request.budget = Budget.model_validate(
                        budget_for_request(request).model_dump() | overrides
                    )
                task = service.start(request)
            else:
                task = service.resume(arguments.task_id)
            result = _wait_for_task(service, task["id"])
            markdown = result.get("report_markdown", "")
            if markdown:
                if arguments.output is not None:
                    arguments.output.parent.mkdir(parents=True, exist_ok=True)
                    arguments.output.write_text(markdown, encoding="utf-8")
                print(markdown)
            else:
                _print_json(result)
            return 0 if result["status"] == "completed" else 1
        finally:
            service.close()
    except Exception as exc:
        # Pydantic/provider exceptions can include input values or credentials.
        # Only our explicitly sanitized error classes and exact service
        # ValueErrors are safe to show. ValidationError is a ValueError subclass.
        if isinstance(exc, (GitHubError, ModelError)) or type(exc) is ValueError:
            print(f"Command failed: {exc}", file=sys.stderr)
        else:
            print(f"Command failed ({type(exc).__name__}); check configuration.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
