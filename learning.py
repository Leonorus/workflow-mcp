#!/usr/bin/env python3
"""Learning layer: feedback capture and exact-match bucket memory.

Sits between server.py and workflow_core. Appends feedback events to
logs/feedback.jsonl — the only place raw prompts persist (local-only,
gitignored) — and folds that file into an in-memory exact-match overlay so a
previously corrected prompt is delivered with its corrected bucket on
re-encounter. workflow_core.classify_task stays pure, so evals never see
memory. Learning failures must never break a tool call.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

import workflow_core as core
from metrics import PROCESS_SERVICE_VERSION

TASK_DIR = Path(__file__).resolve().parent
LOG_DIR = Path(os.environ.get("HERMES_WORKFLOW_MCP_LOG_DIR", str(TASK_DIR / "logs"))).expanduser()
FEEDBACK_PATH = LOG_DIR / "feedback.jsonl"

LOW_CONFIDENCE_THRESHOLD = 0.60
# finish_disagreement is a weaker signal than an explicit override (agents
# legitimately reclassify mid-task), so it needs consistent repetition before
# it may steer future classifications.
FINISH_DISAGREEMENT_ACTIVATION = 2
PENDING_TTL_SECONDS = 6 * 60 * 60
PENDING_CAP = 200
HEURISTIC_REPO_WINDOW_SECONDS = 2 * 60 * 60
HEURISTIC_ANY_WINDOW_SECONDS = 30 * 60

_MEMORY_CACHE: dict[str, Any] = {"file_signature": None, "state": None}
_PENDING: dict[str, dict[str, Any]] = {}


def prompt_signature(prompt: Any) -> str | None:
    """Stable signature over the normalized token set (order/punctuation-insensitive)."""

    if not isinstance(prompt, str) or not prompt.strip():
        return None
    tokens = core._tokens(prompt)
    if not tokens:
        return None
    joined = " ".join(sorted(tokens))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


def record_feedback(event: dict[str, Any]) -> None:
    """Append one feedback event; never raise (mirrors metrics.record_call)."""

    try:
        FEEDBACK_PATH.parent.mkdir(parents=True, exist_ok=True)
        with FEEDBACK_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, sort_keys=True, ensure_ascii=False) + "\n")
    except OSError:
        return


def _file_signature(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return (stat.st_mtime_ns, stat.st_size)


def _iter_feedback(path: Path):
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            yield event


def fold_feedback(path: Path | None = None) -> dict[str, Any]:
    """Fold feedback events (oldest to newest) into the memory overlay state.

    Per prompt signature: the trailing run of corrections toward one bucket
    becomes an overlay entry — immediately if it contains an explicit
    override, after FINISH_DISAGREEMENT_ACTIVATION consistent occurrences
    otherwise. If the two most recent corrections disagree, the signature is
    dropped from the overlay (recorded under conflicts), which also lets a
    later disagreement evict a bad memory.
    """

    path = FEEDBACK_PATH if path is None else path
    by_signature: dict[str, list[dict[str, Any]]] = {}
    for event in _iter_feedback(path):
        corrected = event.get("corrected_bucket")
        signature = event.get("signature")
        if not corrected or corrected == "ambiguous" or corrected not in core.BUCKETS:
            continue
        if not isinstance(signature, str) or not signature:
            continue
        by_signature.setdefault(signature, []).append(
            {
                "bucket": corrected,
                "signal": event.get("signal"),
                "ts": event.get("ts"),
                "prompt": event.get("prompt"),
            }
        )

    overlay: dict[str, dict[str, Any]] = {}
    conflicts: dict[str, dict[str, Any]] = {}
    below_threshold: dict[str, dict[str, Any]] = {}
    for signature, corrections in by_signature.items():
        last = corrections[-1]
        if len(corrections) >= 2 and corrections[-2]["bucket"] != last["bucket"]:
            conflicts[signature] = {
                "buckets": [corrections[-2]["bucket"], last["bucket"]],
                "ts": last["ts"],
                "prompt": last["prompt"],
            }
            continue
        run = []
        for correction in reversed(corrections):
            if correction["bucket"] != last["bucket"]:
                break
            run.append(correction)
        entry = {
            "bucket": last["bucket"],
            "ts": last["ts"],
            "count": len(run),
            "signals": sorted({c["signal"] for c in run if c["signal"]}),
            "prompt": last["prompt"],
        }
        if "explicit_override" not in entry["signals"] and len(run) < FINISH_DISAGREEMENT_ACTIVATION:
            below_threshold[signature] = entry
            continue
        overlay[signature] = entry
    return {"overlay": overlay, "conflicts": conflicts, "below_threshold": below_threshold}


def load_memory() -> dict[str, Any]:
    """Return the folded memory state, re-folding only when feedback.jsonl changed."""

    file_signature = _file_signature(FEEDBACK_PATH)
    if file_signature is None:
        return {"overlay": {}, "conflicts": {}, "below_threshold": {}}
    cache_key = (str(FEEDBACK_PATH), file_signature)
    if _MEMORY_CACHE["file_signature"] != cache_key or _MEMORY_CACHE["state"] is None:
        _MEMORY_CACHE["state"] = fold_feedback(FEEDBACK_PATH)
        _MEMORY_CACHE["file_signature"] = cache_key
    return _MEMORY_CACHE["state"]


def memory_lookup(prompt: Any) -> dict[str, Any] | None:
    signature = prompt_signature(prompt)
    if not signature:
        return None
    entry = load_memory()["overlay"].get(signature)
    if not entry:
        return None
    return {"signature": signature, **entry}


def _now_iso() -> str:
    return _dt.datetime.now(_dt.UTC).isoformat()


def _prune_pending(now: float) -> None:
    expired = [task_id for task_id, entry in _PENDING.items() if now - entry["ts"] > PENDING_TTL_SECONDS]
    for task_id in expired:
        _PENDING.pop(task_id, None)
    while len(_PENDING) > PENDING_CAP:
        oldest = min(_PENDING, key=lambda task_id: _PENDING[task_id]["ts"])
        _PENDING.pop(oldest, None)


def _resolve_pending(task_id: str | None, repo_slug: str | None) -> tuple[str | None, str | None]:
    now = time.time()
    if task_id and task_id in _PENDING and not _PENDING[task_id]["finished"]:
        return task_id, "task_id"
    candidates = sorted(
        (tid for tid, entry in _PENDING.items() if not entry["finished"]),
        key=lambda tid: _PENDING[tid]["ts"],
        reverse=True,
    )
    for tid in candidates:
        entry = _PENDING[tid]
        age = now - entry["ts"]
        if repo_slug:
            if entry["repo"] == repo_slug and age <= HEURISTIC_REPO_WINDOW_SECONDS:
                return tid, "heuristic"
        elif age <= HEURISTIC_ANY_WINDOW_SECONDS:
            return tid, "heuristic"
    return None, None


def _delivery_info(
    prompt: str,
    cwd: str | None,
    repo: str | None,
    already_classified_bucket: str | None,
    memory_bucket: str | None,
    result: dict[str, Any],
) -> dict[str, Any]:
    """Recover predicted/delivered buckets even when `fields` trimmed the packet."""

    if "bucket" in result and "confidence" in result and "ambiguity" in result:
        delivered = result["bucket"]
        confidence = result.get("confidence")
        ambiguity = bool(result.get("ambiguity"))
        if isinstance(result.get("override"), dict):
            predicted, source = result["override"].get("from"), "override"
        elif isinstance(result.get("memory"), dict):
            predicted, source = result["memory"].get("from"), "memory"
        else:
            predicted, source = delivered, "classifier"
        return {"predicted": predicted, "delivered": delivered, "source": source, "confidence": confidence, "ambiguity": ambiguity}

    classification = core.classify_task(prompt, cwd=cwd, repo=repo)
    predicted = classification["bucket"]
    if already_classified_bucket:
        delivered, source = core._validate_bucket(already_classified_bucket), "override"
    elif memory_bucket:
        delivered, source = memory_bucket, "memory"
    else:
        delivered, source = predicted, "classifier"
    return {
        "predicted": predicted,
        "delivered": delivered,
        "source": source,
        "confidence": classification.get("confidence"),
        "ambiguity": bool(classification.get("ambiguity")),
    }


def _track_start(
    task_id: str,
    prompt: str,
    cwd: str | None,
    repo: str | None,
    already_classified_bucket: str | None,
    memory_bucket: str | None,
    result: dict[str, Any],
) -> None:
    info = _delivery_info(prompt, cwd, repo, already_classified_bucket, memory_bucket, result)
    repo_slug = core._repo_name(repo, cwd)
    now = time.time()
    _PENDING[task_id] = {
        "ts": now,
        "prompt": prompt,
        "predicted_bucket": info["predicted"],
        "delivered_bucket": info["delivered"],
        "delivered_source": info["source"],
        "repo": repo_slug,
        "finished": False,
    }
    _prune_pending(now)

    signal = None
    corrected = None
    if info["source"] == "override" and info["predicted"] != info["delivered"]:
        signal, corrected = "explicit_override", info["delivered"]
    elif info["source"] == "classifier":
        if info["ambiguity"] or info["delivered"] == "ambiguous":
            signal = "ambiguous"
        elif isinstance(info["confidence"], (int, float)) and info["confidence"] <= LOW_CONFIDENCE_THRESHOLD:
            signal = "low_confidence"
    if not signal:
        return
    record_feedback(
        {
            "ts": _now_iso(),
            "signal": signal,
            "prompt": prompt,
            "signature": prompt_signature(prompt),
            "predicted_bucket": info["predicted"],
            "delivered_bucket": info["delivered"],
            "delivered_source": info["source"],
            "corrected_bucket": corrected,
            "confidence": info["confidence"],
            "ambiguity": info["ambiguity"],
            "repo": repo_slug,
            "task_id": task_id,
            "correlation": None,
            "service_version": PROCESS_SERVICE_VERSION,
        }
    )


def start_task_with_learning(
    prompt: str,
    cwd: str | None = None,
    repo: str | None = None,
    already_classified_bucket: str | None = None,
    fields: list[str] | None = None,
) -> dict[str, Any]:
    task_id = uuid.uuid4().hex[:12]
    memory = None
    if not already_classified_bucket:
        try:
            memory = memory_lookup(prompt)
        except Exception:
            memory = None
    memory_bucket = memory["bucket"] if memory else None
    memory_meta = (
        {"corrected_ts": memory["ts"], "count": memory["count"], "signals": memory["signals"]} if memory else None
    )
    result = core.start_task(
        prompt,
        cwd=cwd,
        repo=repo,
        already_classified_bucket=already_classified_bucket,
        fields=fields,
        memory_bucket=memory_bucket,
        memory_meta=memory_meta,
        task_id=task_id,
    )
    if not isinstance(result, dict) or result.get("error"):
        return result
    try:
        _track_start(task_id, prompt, cwd, repo, already_classified_bucket, memory_bucket, result)
    except Exception:
        pass
    return result


def _track_finish(
    task_id: str | None,
    finish_bucket: str | None,
    repo: str | None,
    repo_root: str | None,
    result: dict[str, Any],
) -> None:
    repo_slug = core._repo_name(repo, repo_root)
    resolved, correlation = _resolve_pending(task_id, repo_slug)
    result["task_id"] = resolved or task_id
    result["correlation"] = correlation
    if not resolved:
        return
    entry = _PENDING[resolved]
    entry["finished"] = True
    if not finish_bucket or entry["delivered_bucket"] == finish_bucket:
        return
    record_feedback(
        {
            "ts": _now_iso(),
            "signal": "finish_disagreement",
            "prompt": entry["prompt"],
            "signature": prompt_signature(entry["prompt"]),
            "predicted_bucket": entry["predicted_bucket"],
            "delivered_bucket": entry["delivered_bucket"],
            "delivered_source": entry["delivered_source"],
            "corrected_bucket": finish_bucket,
            "confidence": None,
            "ambiguity": None,
            "repo": repo_slug or entry["repo"],
            "task_id": resolved,
            "correlation": correlation,
            "service_version": PROCESS_SERVICE_VERSION,
        }
    )


def finish_checklist_with_learning(
    bucket: str,
    changed_files: list[str] | None = None,
    commands_run: list[str] | None = None,
    findings: str | None = None,
    repo: str | None = None,
    repo_root: str | None = None,
    auto_detect_changes: bool = True,
    subagents_used: list[str] | None = None,
    external_side_effects: list[str] | None = None,
    docs_changed: bool | None = None,
    notes_written: list[str] | None = None,
    skills_loaded: list[str] | None = None,
    skills_updated: list[str] | None = None,
    verification_intent: str | None = None,
    task_id: str | None = None,
) -> dict[str, Any]:
    result = core.finish_checklist(
        bucket=bucket,
        changed_files=changed_files,
        commands_run=commands_run,
        findings=findings,
        repo=repo,
        repo_root=repo_root,
        auto_detect_changes=auto_detect_changes,
        subagents_used=subagents_used,
        external_side_effects=external_side_effects,
        docs_changed=docs_changed,
        notes_written=notes_written,
        skills_loaded=skills_loaded,
        skills_updated=skills_updated,
        verification_intent=verification_intent,
    )
    if not isinstance(result, dict) or result.get("error"):
        return result
    try:
        _track_finish(task_id, result.get("bucket"), repo, repo_root, result)
    except Exception:
        pass
    return result
