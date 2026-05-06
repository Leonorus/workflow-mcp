#!/usr/bin/env python3
"""Streamable HTTP MCP wrapper for workflow_core."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

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

from workflow_core import (  # noqa: E402
    classify_task as core_classify_task,
    discover_context as core_discover_context,
    finish_checklist as core_finish_checklist,
    start_task as core_start_task,
    suggest_delegation as core_suggest_delegation,
)

HOST = os.environ.get("HERMES_WORKFLOW_MCP_HOST", "127.0.0.1")
PORT = int(os.environ.get("HERMES_WORKFLOW_MCP_PORT", "8813"))

mcp = FastMCP(
    "workflow",
    host=HOST,
    port=PORT,
    streamable_http_path="/mcp",
    log_level=os.environ.get("HERMES_WORKFLOW_MCP_LOG_LEVEL", "INFO"),
)


@mcp.custom_route("/health", methods=["GET"], include_in_schema=False)
async def health(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "service": "workflow-mcp", "tools": 5})


@mcp.tool()
def classify_task(prompt: str, cwd: str | None = None, repo: str | None = None) -> dict[str, Any]:
    """Classify a prompt into Filipp's codex-workflow bucket taxonomy."""

    return core_classify_task(prompt=prompt, cwd=cwd, repo=repo)


@mcp.tool()
def start_task(
    prompt: str,
    cwd: str | None = None,
    repo: str | None = None,
    session_id: str | None = None,
    already_classified_bucket: str | None = None,
) -> dict[str, Any]:
    """Return bucket, visible statement, required skills, context candidates, delegation hint, and finish checklist."""

    return core_start_task(
        prompt=prompt,
        cwd=cwd,
        repo=repo,
        session_id=session_id,
        already_classified_bucket=already_classified_bucket,
    )


@mcp.tool()
def discover_context(
    prompt: str,
    repo: str | None = None,
    cwd: str | None = None,
    vault_root: str | None = None,
    max_candidates: int = 8,
    include_snippets: bool = False,
) -> dict[str, Any]:
    """Find directly relevant Obsidian note candidates by keyword overlap without dumping broad note content."""

    return core_discover_context(
        prompt=prompt,
        repo=repo,
        cwd=cwd,
        vault_root=vault_root,
        max_candidates=max_candidates,
        include_snippets=include_snippets,
    )


@mcp.tool()
def suggest_delegation(
    prompt: str,
    bucket: str | None = None,
    cwd: str | None = None,
    repo: str | None = None,
) -> dict[str, Any]:
    """Suggest concrete delegate_task workstreams for substantial buckets."""

    return core_suggest_delegation(prompt=prompt, bucket=bucket, cwd=cwd, repo=repo)


@mcp.tool()
def finish_checklist(
    bucket: str,
    changed_files: list[str] | None = None,
    commands_run: list[str] | None = None,
    findings: str | None = None,
    repo: str | None = None,
) -> dict[str, Any]:
    """Return verification/docs/note/memory/skill-maintenance finish requirements."""

    return core_finish_checklist(
        bucket=bucket,
        changed_files=changed_files,
        commands_run=commands_run,
        findings=findings,
        repo=repo,
    )


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
