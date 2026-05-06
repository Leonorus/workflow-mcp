#!/usr/bin/env python3
"""Privacy-safe per-call telemetry for the local Workflow MCP service."""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

TASK_DIR = Path(__file__).resolve().parent
LOG_DIR = Path(os.environ.get("HERMES_WORKFLOW_MCP_LOG_DIR", str(TASK_DIR / "logs"))).expanduser()
CALLS_PATH = LOG_DIR / "calls.jsonl"
STARTED_AT = time.time()
_REQUEST_COUNT = 0
_ERROR_COUNT = 0
_LAST_ERROR_TYPE: str | None = None


def _prompt_summary(prompt: Any) -> dict[str, Any]:
    if not isinstance(prompt, str) or not prompt:
        return {}
    encoded = prompt.encode("utf-8", errors="replace")
    return {
        "prompt_hash": hashlib.sha256(encoded).hexdigest()[:16],
        "prompt_word_count": len(prompt.split()),
    }


def _repo_kind(repo: Any) -> str | None:
    if not isinstance(repo, str) or not repo:
        return None
    if repo.startswith("/") or repo.startswith("~") or "/" in repo or "\\" in repo:
        return "path"
    return "slug"


def _cwd_basename(cwd: Any) -> str | None:
    if not isinstance(cwd, str) or not cwd:
        return None
    return Path(cwd).expanduser().name or None


def _result_summary(result: Any) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {}
    out: dict[str, Any] = {}
    for key in ("bucket", "confidence", "ambiguity", "obsidian_required", "reasoning_guard_required", "should_delegate"):
        if key in result:
            out[key] = result[key]
    if "candidates" in result and isinstance(result["candidates"], list):
        out["candidate_count"] = len(result["candidates"])
    if "warnings" in result and isinstance(result["warnings"], list):
        out["warnings_count"] = len(result["warnings"])
    if "checklist" in result and isinstance(result["checklist"], list):
        out["checklist_count"] = len(result["checklist"])
    if "tasks" in result and isinstance(result["tasks"], list):
        out["tasks_count"] = len(result["tasks"])
    if "delegation_hint" in result and isinstance(result["delegation_hint"], dict):
        tasks = result["delegation_hint"].get("tasks")
        if isinstance(tasks, list):
            out["tasks_count"] = len(tasks)
        if "should_delegate" in result["delegation_hint"]:
            out["should_delegate"] = result["delegation_hint"]["should_delegate"]
    if "candidate_notes" in result and isinstance(result["candidate_notes"], list):
        out["candidate_count"] = len(result["candidate_notes"])
    if "context_warnings" in result and isinstance(result["context_warnings"], list):
        out["warnings_count"] = len(result["context_warnings"])
    return out


def record_call(tool: str, started: float, success: bool, args: dict[str, Any] | None = None, result: Any = None, exc: BaseException | None = None) -> None:
    """Append one privacy-safe JSONL telemetry event; never raise to callers."""

    global _REQUEST_COUNT, _ERROR_COUNT, _LAST_ERROR_TYPE
    _REQUEST_COUNT += 1
    if not success:
        _ERROR_COUNT += 1
        _LAST_ERROR_TYPE = type(exc).__name__ if exc else "unknown"

    args = args or {}
    event: dict[str, Any] = {
        "ts": _dt.datetime.now(_dt.UTC).isoformat(),
        "tool": tool,
        "success": success,
        "duration_ms": round((time.perf_counter() - started) * 1000, 2),
    }
    event.update(_prompt_summary(args.get("prompt")))
    if args.get("session_id"):
        event["session_id"] = args.get("session_id")
    if args.get("fields") is not None:
        event["fields_requested"] = args.get("fields")
    if args.get("inline_top_n") is not None:
        event["inline_top_n"] = args.get("inline_top_n")
    repo_kind = _repo_kind(args.get("repo"))
    if repo_kind:
        event["repo_kind"] = repo_kind
    cwd_basename = _cwd_basename(args.get("cwd"))
    if cwd_basename:
        event["cwd_basename"] = cwd_basename
    if exc:
        event["exception_type"] = type(exc).__name__
    event.update(_result_summary(result))

    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with CALLS_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, sort_keys=True, ensure_ascii=False) + "\n")
    except OSError:
        return


def health_stats() -> dict[str, Any]:
    return {
        "uptime_seconds": round(time.time() - STARTED_AT, 2),
        "request_count": _REQUEST_COUNT,
        "error_count": _ERROR_COUNT,
        "last_error_type": _LAST_ERROR_TYPE,
        "metrics_path": str(CALLS_PATH),
    }
