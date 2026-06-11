#!/usr/bin/env python3
"""Mine feedback/telemetry into a human-reviewed classifier tuning report.

Reads logs/feedback.jsonl (raw-prompt feedback events) and logs/calls.jsonl
(hash-only telemetry) and proposes — never applies — keyword candidates,
weight diagnostics, and ready-to-paste evals/golden.jsonl cases. Apply edits
by hand, then keep the eval gate green.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from collections import Counter
from pathlib import Path
from typing import Any

import learning
import workflow_core as core

TASK_DIR = Path(__file__).resolve().parent
DEFAULT_FEEDBACK = TASK_DIR / "logs" / "feedback.jsonl"
DEFAULT_CALLS = TASK_DIR / "logs" / "calls.jsonl"
DEFAULT_GOLDEN = TASK_DIR / "evals" / "golden.jsonl"

KEYWORD_MIN_PROMPTS = 2
NEAR_MISS_JACCARD = 0.8


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows


def _counter_table(title: str, counter: Counter[Any], limit: int = 20) -> list[str]:
    lines = [f"### {title}", "", "| Value | Count |", "|---|---:|"]
    if not counter:
        lines.append("| _none_ | 0 |")
    else:
        for value, count in counter.most_common(limit):
            lines.append(f"| `{value}` | {count} |")
    lines.append("")
    return lines


def _calls_section(calls: list[dict[str, Any]]) -> list[str]:
    start_calls = [row for row in calls if row.get("tool") == "start_task" and row.get("bucket")]
    per_bucket: dict[str, dict[str, int]] = {}
    for row in start_calls:
        bucket = row["bucket"] if not row.get("override_from") else row["override_from"]
        stats = per_bucket.setdefault(bucket, {"total": 0, "override": 0, "ambiguous": 0, "low_confidence": 0})
        stats["total"] += 1
        if row.get("override_from"):
            stats["override"] += 1
        if row.get("ambiguity") is True:
            stats["ambiguous"] += 1
        confidence = row.get("confidence")
        if isinstance(confidence, (int, float)) and confidence <= learning.LOW_CONFIDENCE_THRESHOLD:
            stats["low_confidence"] += 1

    overrides = Counter(
        f"{row.get('override_from')}->{row.get('override_to')}" for row in calls if row.get("override_from")
    )
    memory_hits = Counter(
        f"{row.get('memory_from')}->{row.get('memory_to')}" for row in calls if row.get("memory_from")
    )

    lines = [
        "## Aggregate telemetry (calls.jsonl, hash-only)",
        "",
        f"- start_task calls with a bucket: {len(start_calls)}",
        "",
        "### Per predicted bucket",
        "",
        "| Bucket | Calls | Override rate | Ambiguous rate | Low-confidence rate |",
        "|---|---:|---:|---:|---:|",
    ]
    for bucket, stats in sorted(per_bucket.items(), key=lambda kv: -kv[1]["total"]):
        total = stats["total"]
        lines.append(
            f"| `{bucket}` | {total} | {stats['override'] / total * 100:.1f}% | "
            f"{stats['ambiguous'] / total * 100:.1f}% | {stats['low_confidence'] / total * 100:.1f}% |"
        )
    lines.append("")
    lines.extend(_counter_table("Override pairs (from->to)", overrides))
    lines.extend(_counter_table("Memory hits (from->to)", memory_hits))
    return lines


def _memory_section(state: dict[str, Any], events: list[dict[str, Any]]) -> list[str]:
    lines = ["## Memory overlay status (feedback.jsonl)", ""]
    lines.append(f"- Active entries: {len(state['overlay'])}")
    lines.append(f"- Conflict-dropped: {len(state['conflicts'])}")
    lines.append(f"- Below activation threshold: {len(state['below_threshold'])}")
    lines.append("")
    if state["overlay"]:
        lines.extend(["### Active entries", "", "| Signature | Bucket | Count | Signals | Prompt |", "|---|---|---:|---|---|"])
        for signature, entry in sorted(state["overlay"].items()):
            prompt = (entry.get("prompt") or "")[:80]
            lines.append(f"| `{signature}` | `{entry['bucket']}` | {entry['count']} | {', '.join(entry['signals'])} | {prompt} |")
        lines.append("")
    if state["conflicts"]:
        lines.extend(["### Conflict-dropped (review and prune feedback.jsonl)", "", "| Signature | Buckets | Prompt |", "|---|---|---|"])
        for signature, entry in sorted(state["conflicts"].items()):
            lines.append(f"| `{signature}` | {' vs '.join(entry['buckets'])} | {(entry.get('prompt') or '')[:80]} |")
        lines.append("")
    if state["below_threshold"]:
        lines.extend(["### Below threshold (finish disagreements awaiting confirmation)", "", "| Signature | Bucket | Count | Prompt |", "|---|---|---:|---|"])
        for signature, entry in sorted(state["below_threshold"].items()):
            lines.append(f"| `{signature}` | `{entry['bucket']}` | {entry['count']} | {(entry.get('prompt') or '')[:80]} |")
        lines.append("")

    token_sets: dict[str, set[str]] = {}
    for event in events:
        signature = event.get("signature")
        prompt = event.get("prompt")
        if isinstance(signature, str) and isinstance(prompt, str) and signature not in token_sets:
            tokens = core._tokens(prompt)
            if tokens:
                token_sets[signature] = tokens
    near_misses = []
    signatures = sorted(token_sets)
    for i, sig_a in enumerate(signatures):
        for sig_b in signatures[i + 1 :]:
            a, b = token_sets[sig_a], token_sets[sig_b]
            jaccard = len(a & b) / len(a | b)
            if jaccard >= NEAR_MISS_JACCARD:
                near_misses.append((sig_a, sig_b, jaccard))
    if near_misses:
        lines.extend(["### Near-miss signature pairs (evidence for/against fuzzy matching)", "", "| A | B | Jaccard |", "|---|---|---:|"])
        for sig_a, sig_b, jaccard in near_misses:
            lines.append(f"| `{sig_a}` | `{sig_b}` | {jaccard:.2f} |")
        lines.append("")
    return lines


def _corrections_by_bucket(events: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    """Map target bucket -> {signature: latest prompt corrected to it}."""

    out: dict[str, dict[str, str]] = {}
    for event in events:
        corrected = event.get("corrected_bucket")
        signature = event.get("signature")
        prompt = event.get("prompt")
        if not corrected or corrected not in core.BUCKETS or corrected == "ambiguous":
            continue
        if not isinstance(signature, str) or not isinstance(prompt, str):
            continue
        out.setdefault(corrected, {})[signature] = prompt
    return out


def _keyword_section(events: list[dict[str, Any]], golden: list[dict[str, Any]]) -> list[str]:
    lines = ["## Keyword candidates (review before editing _KEYWORDS)", ""]
    other_bucket_tokens: dict[str, set[str]] = {}
    for case in golden:
        bucket = case.get("bucket_primary")
        tokens = core._tokens(case.get("prompt") or "")
        other_bucket_tokens.setdefault(bucket, set()).update(tokens)

    emitted = False
    for bucket, prompts in sorted(_corrections_by_bucket(events).items()):
        token_counts: Counter[str] = Counter()
        for prompt in prompts.values():
            token_counts.update(core._tokens(prompt))
        existing = core._KEYWORDS.get(bucket, ())
        candidates = []
        for token, count in token_counts.most_common():
            if count < KEYWORD_MIN_PROMPTS:
                continue
            if token in core._STOPWORDS:
                continue
            if any(token in keyword or keyword in token for keyword in existing):
                continue
            golden_conflict = any(
                token in tokens for other, tokens in other_bucket_tokens.items() if other != bucket
            )
            if golden_conflict:
                continue
            candidates.append((token, count))
        if not candidates:
            continue
        emitted = True
        lines.extend([f"### -> `{bucket}`", "", "| Token | Corrected prompts containing it |", "|---|---:|"])
        for token, count in candidates:
            lines.append(f"| `{token}` | {count} |")
        lines.append("")
    if not emitted:
        lines.extend(["_No keyword candidates yet (need >=2 corrected prompts sharing a clean token)._", ""])
    return lines


def _diagnostics_section(events: list[dict[str, Any]]) -> list[str]:
    lines = ["## Score diagnostics (corrected prompts re-run through classify_task)", ""]
    rows = []
    for bucket, prompts in sorted(_corrections_by_bucket(events).items()):
        for signature, prompt in sorted(prompts.items()):
            classification = core.classify_task(prompt)
            scores = classification.get("scores") or {}
            target_score = scores.get(bucket, 0)
            winner = classification["bucket"]
            winner_score = scores.get(winner, 0)
            if winner == bucket:
                verdict = "now correct"
            elif target_score == 0:
                verdict = "target scored 0 -> needs a keyword"
            else:
                verdict = f"lost by {winner_score - target_score} -> consider weight/heuristic nudge"
            rows.append((signature, bucket, winner, target_score, winner_score, verdict, prompt[:60]))
    if not rows:
        return lines + ["_No corrections recorded yet._", ""]
    lines.extend(["| Signature | Target | Winner | Target score | Winner score | Verdict | Prompt |", "|---|---|---|---:|---:|---|---|"])
    for signature, bucket, winner, target_score, winner_score, verdict, prompt in rows:
        lines.append(f"| `{signature}` | `{bucket}` | `{winner}` | {target_score} | {winner_score} | {verdict} | {prompt} |")
    lines.append("")
    return lines


def _golden_section(state: dict[str, Any], golden: list[dict[str, Any]]) -> list[str]:
    lines = ["## Proposed golden cases (paste into evals/golden.jsonl)", ""]
    existing_prompts = {case.get("prompt") for case in golden}
    existing_ids = {case.get("case_id") for case in golden}
    proposals = []
    for signature, entry in sorted(state["overlay"].items()):
        prompt = entry.get("prompt")
        if not prompt or prompt in existing_prompts:
            continue
        case_id = f"mined-{signature}"
        if case_id in existing_ids:
            continue
        proposals.append(
            json.dumps(
                {
                    "case_id": case_id,
                    "prompt": prompt,
                    "bucket_primary": entry["bucket"],
                    "bucket_acceptable": [entry["bucket"]],
                },
                ensure_ascii=False,
            )
        )
    if not proposals:
        return lines + ["_Nothing new to propose._", ""]
    lines.append("```jsonl")
    lines.extend(proposals)
    lines.extend(["```", ""])
    return lines


def build_report(feedback_path: Path, calls_path: Path, golden_path: Path) -> str:
    events = _load_jsonl(feedback_path)
    calls = _load_jsonl(calls_path)
    golden = _load_jsonl(golden_path)
    state = learning.fold_feedback(feedback_path)

    lines = [
        f"# Workflow MCP tuning report — {dt.date.today().isoformat()}",
        "",
        f"- Feedback events: {len(events)} (`{feedback_path}`)",
        f"- Telemetry rows: {len(calls)} (`{calls_path}`)",
        f"- Golden cases: {len(golden)} (`{golden_path}`)",
        "",
    ]
    lines.extend(_calls_section(calls))
    lines.extend(_memory_section(state, events))
    lines.extend(_keyword_section(events, golden))
    lines.extend(_diagnostics_section(events))
    lines.extend(_golden_section(state, golden))
    lines.extend(
        [
            "## Gate",
            "",
            "Apply edits by hand (golden cases BEFORE classifier changes), then:",
            "",
            "```bash",
            "PY=~/.hermes/hermes-agent/venv/bin/python",
            "$PY -m pytest tests -q && $PY eval.py",
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Propose classifier tuning from feedback history")
    parser.add_argument("--feedback", default=str(DEFAULT_FEEDBACK), help="Path to feedback.jsonl")
    parser.add_argument("--calls", default=str(DEFAULT_CALLS), help="Path to calls.jsonl")
    parser.add_argument("--golden", default=str(DEFAULT_GOLDEN), help="Path to evals/golden.jsonl")
    parser.add_argument("--output", help="Report path; defaults to stdout")
    args = parser.parse_args()

    report = build_report(Path(args.feedback).expanduser(), Path(args.calls).expanduser(), Path(args.golden).expanduser())
    if not args.output:
        print(report)
        return 0
    output = Path(args.output).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(report + "\n", encoding="utf-8")
    print(f"workflow-mcp tuning report written: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
