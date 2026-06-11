#!/usr/bin/env python3
"""Direct core smoke checks for workflow-mcp without an agent conversation."""

from __future__ import annotations

import json
from pathlib import Path
import sys

TASK_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TASK_DIR))

from workflow_core import classify_task, finish_checklist, start_task, suggest_delegation  # noqa: E402

CASES = [
    ("debug", lambda: classify_task("check")),
    ("debug", lambda: classify_task("fix failing test in workflow server")),
    ("research", lambda: classify_task("compare workflow MCP implementation options")),
    ("research", lambda: classify_task("analyze the workflow MCP server and propose improvements")),
    ("trivia", lambda: classify_task("update typo in README")),
    ("script", lambda: classify_task("add launchd service for workflow MCP")),
    ("repo_maintenance", lambda: classify_task("dependency bump and CI cleanup")),
]


def main() -> int:
    out = []
    for expected, fn in CASES:
        result = fn()
        out.append({"expected": expected, "actual": result["bucket"], "confidence": result["confidence"]})
        if result["bucket"] != expected:
            print(json.dumps(out, indent=2), file=sys.stderr)
            raise SystemExit(f"expected {expected}, got {result['bucket']}")
    packet = start_task("execute workflow MCP oracle implementation", repo="hermes-config")
    assert packet["bucket"] == "script", packet
    assert "codex-workflow" in packet["required_skills"], packet
    assert "hermes-agent" not in packet["required_skills"], packet
    assert "native-mcp" not in packet["required_skills"], packet
    delegation = suggest_delegation("review GitLab release pipeline failure", bucket="debug")
    assert delegation["should_delegate"] and delegation["tasks"], delegation
    checklist = finish_checklist(
        bucket="debug",
        changed_files=["scheduled-tasks/workflow-mcp/server.py", "config.yaml"],
        commands_run=["pytest"],
        findings="workflow mcp smoke",
        repo="hermes-config",
    )
    assert checklist["note_action"] == "write_raw_note", checklist
    print(json.dumps({
        "classifications": out,
        "start_task_bucket": packet["bucket"],
        "checklist_items": len(checklist["checklist"]),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
