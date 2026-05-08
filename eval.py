#!/usr/bin/env python3
"""Seed eval runner for Workflow MCP classifier and context routing."""

from __future__ import annotations

import argparse
import json
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any
import sys

TASK_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TASK_DIR))

import workflow_core as core  # noqa: E402


def _rows(path: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


def run_golden(path: Path) -> dict[str, Any]:
    cases = _rows(path)
    failures: list[dict[str, Any]] = []
    confusion: Counter[tuple[str, str]] = Counter()
    acceptable = 0
    for case in cases:
        result = core.classify_task(case["prompt"], cwd=case.get("cwd"), repo=case.get("repo"))
        expected = case["bucket_primary"]
        actual = result["bucket"]
        confusion[(expected, actual)] += 1
        if actual in case.get("bucket_acceptable", [expected]):
            acceptable += 1
        else:
            failures.append({"case_id": case["case_id"], "expected": expected, "actual": actual, "result": result})
        if "reasoning_guard_required" in case and result["reasoning_guard_required"] != case["reasoning_guard_required"]:
            failures.append({"case_id": case["case_id"], "expected_reasoning_guard": case["reasoning_guard_required"], "actual_reasoning_guard": result["reasoning_guard_required"]})
    exact = sum(1 for (expected, actual), count in confusion.items() if expected == actual for _ in range(count))
    return {
        "cases": len(cases),
        "exact_accuracy": round(exact / len(cases), 3) if cases else 1.0,
        "acceptable_accuracy": round(acceptable / len(cases), 3) if cases else 1.0,
        "confusion": {f"{expected}->{actual}": count for (expected, actual), count in sorted(confusion.items())},
        "failures": failures,
    }


def run_context(path: Path) -> dict[str, Any]:
    cases = _rows(path)
    failures: list[dict[str, Any]] = []
    recall_hits = 0
    reciprocal_ranks: list[float] = []
    with tempfile.TemporaryDirectory() as tmp:
        vault = Path(tmp) / "vault"
        for case in cases:
            for note in case.get("fixture_notes", []):
                p = vault / note["path"]
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(note["content"], encoding="utf-8")
            result = core.discover_context(case["prompt"], repo=case.get("repo"), vault_root=str(vault), max_candidates=5)
            paths = [item["path"] for item in result["candidates"]]
            must = case.get("must_read", [])
            if all(p in paths for p in must):
                recall_hits += 1
            else:
                failures.append({"case_id": case["case_id"], "missing_must_read": [p for p in must if p not in paths], "paths": paths})
            if must:
                ranks = [paths.index(p) + 1 for p in must if p in paths]
                reciprocal_ranks.append(1 / min(ranks) if ranks else 0.0)
            forbidden = [p for p in case.get("forbidden_top5", []) if p in paths]
            if forbidden:
                failures.append({"case_id": case["case_id"], "forbidden_top5_present": forbidden, "paths": paths})
    return {
        "cases": len(cases),
        "recall_at_5": round(recall_hits / len(cases), 3) if cases else 1.0,
        "mrr_at_3": round(sum(reciprocal_ranks) / len(reciprocal_ranks), 3) if reciprocal_ranks else 1.0,
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Workflow MCP seed evals")
    parser.add_argument("--golden", default=str(TASK_DIR / "evals/golden.jsonl"))
    parser.add_argument("--context", default=str(TASK_DIR / "evals/context.jsonl"))
    args = parser.parse_args()
    report = {
        "golden": run_golden(Path(args.golden)),
        "context": run_context(Path(args.context)),
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    if report["golden"]["failures"] or report["context"]["failures"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
