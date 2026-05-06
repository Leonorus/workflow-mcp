#!/usr/bin/env python3
"""Streamable HTTP MCP wrapper for workflow_core."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any, Callable, TypeVar

try:
    from mcp.server.fastmcp import FastMCP
    from starlette.requests import Request
    from starlette.responses import JSONResponse
except Exception as exc:  # pragma: no cover - exercised by launch logs, not unit tests.
    print(f"workflow-mcp: failed to import MCP SDK: {exc}", file=sys.stderr)
    raise

TASK_DIR = Path(__file__).resolve().parent
if str(TASK_DIR) not in sys.path:
    sys.path.insert(0, str(TASK_DIR))

from metrics import health_stats, record_call  # noqa: E402
from workflow_core import (  # noqa: E402
    classify_task as core_classify_task,
    discover_context as core_discover_context,
    finish_checklist as core_finish_checklist,
    start_task as core_start_task,
    suggest_delegation as core_suggest_delegation,
)

HOST = os.environ.get("HERMES_WORKFLOW_MCP_HOST", "127.0.0.1")
PORT = int(os.environ.get("HERMES_WORKFLOW_MCP_PORT", "8813"))
TOOL_COUNT = 5

mcp = FastMCP(
    "workflow",
    host=HOST,
    port=PORT,
    streamable_http_path="/mcp",
    log_level=os.environ.get("HERMES_WORKFLOW_MCP_LOG_LEVEL", "INFO"),
)

T = TypeVar("T")


def _with_metrics(tool: str, args: dict[str, Any], fn: Callable[[], T]) -> T:
    started = time.perf_counter()
    try:
        result = fn()
    except Exception as exc:
        record_call(tool, started, False, args=args, exc=exc)
        raise
    record_call(tool, started, True, args=args, result=result)
    return result


@mcp.custom_route("/health", methods=["GET"], include_in_schema=False)
async def health(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "service": "workflow-mcp", "tools": TOOL_COUNT, **health_stats()})


@mcp.tool()
def classify_task(prompt: str, cwd: str | None = None, repo: str | None = None) -> dict[str, Any]:
    """Classify a prompt into Filipp's codex-workflow bucket taxonomy."""

    args = {"prompt": prompt, "cwd": cwd, "repo": repo}
    return _with_metrics("classify_task", args, lambda: core_classify_task(prompt=prompt, cwd=cwd, repo=repo))


@mcp.tool()
def start_task(
    prompt: str,
    cwd: str | None = None,
    repo: str | None = None,
    session_id: str | None = None,
    already_classified_bucket: str | None = None,
    fields: list[str] | None = None,
) -> dict[str, Any]:
    """Return bucket, visible statement, required skills, context candidates, delegation hint, and finish checklist."""

    args = {
        "prompt": prompt,
        "cwd": cwd,
        "repo": repo,
        "session_id": session_id,
        "already_classified_bucket": already_classified_bucket,
        "fields": fields,
    }
    return _with_metrics(
        "start_task",
        args,
        lambda: core_start_task(
            prompt=prompt,
            cwd=cwd,
            repo=repo,
            session_id=session_id,
            already_classified_bucket=already_classified_bucket,
            fields=fields,
        ),
    )


@mcp.tool()
def discover_context(
    prompt: str,
    repo: str | None = None,
    cwd: str | None = None,
    vault_root: str | None = None,
    max_candidates: int = 8,
    include_snippets: bool = False,
    inline_top_n: int = 0,
    inline_max_chars: int = 4000,
) -> dict[str, Any]:
    """Find directly relevant Obsidian note candidates by keyword overlap without dumping broad note content."""

    args = {
        "prompt": prompt,
        "repo": repo,
        "cwd": cwd,
        "vault_root": vault_root,
        "max_candidates": max_candidates,
        "include_snippets": include_snippets,
        "inline_top_n": inline_top_n,
        "inline_max_chars": inline_max_chars,
    }
    return _with_metrics(
        "discover_context",
        args,
        lambda: core_discover_context(
            prompt=prompt,
            repo=repo,
            cwd=cwd,
            vault_root=vault_root,
            max_candidates=max_candidates,
            include_snippets=include_snippets,
            inline_top_n=inline_top_n,
            inline_max_chars=inline_max_chars,
        ),
    )


@mcp.tool()
def suggest_delegation(
    prompt: str,
    bucket: str | None = None,
    cwd: str | None = None,
    repo: str | None = None,
) -> dict[str, Any]:
    """Suggest concrete delegate_task workstreams for substantial buckets."""

    args = {"prompt": prompt, "bucket": bucket, "cwd": cwd, "repo": repo}
    return _with_metrics("suggest_delegation", args, lambda: core_suggest_delegation(prompt=prompt, bucket=bucket, cwd=cwd, repo=repo))


@mcp.tool()
def finish_checklist(
    bucket: str,
    changed_files: list[str] | None = None,
    commands_run: list[str] | None = None,
    findings: str | None = None,
    repo: str | None = None,
    repo_root: str | None = None,
    auto_detect_changes: bool = True,
) -> dict[str, Any]:
    """Return verification/docs/note/memory/skill-maintenance finish requirements."""

    args = {
        "bucket": bucket,
        "changed_files": changed_files,
        "commands_run": commands_run,
        "findings": findings,
        "repo": repo,
        "repo_root": repo_root,
        "auto_detect_changes": auto_detect_changes,
    }
    return _with_metrics(
        "finish_checklist",
        args,
        lambda: core_finish_checklist(
            bucket=bucket,
            changed_files=changed_files,
            commands_run=commands_run,
            findings=findings,
            repo=repo,
            repo_root=repo_root,
            auto_detect_changes=auto_detect_changes,
        ),
    )


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
