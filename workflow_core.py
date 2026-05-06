#!/usr/bin/env python3
"""Deterministic helpers for Filipp's codex-workflow policy.

V1 is intentionally read-only. The human-readable source of truth remains the
`codex-workflow` skill; this module turns the stable bucket taxonomy and common
start/finish rules into structured data for a tiny local MCP server.
"""

from __future__ import annotations

import datetime as _dt
import os
import re
import subprocess
from pathlib import Path
from typing import Any

BUCKETS: tuple[str, ...] = (
    "trivia",
    "light_ops",
    "heavy_ops",
    "app_code",
    "script",
    "debug",
    "research",
    "repo_maintenance",
    "ambiguous",
)

DELEGATE_TASK_BUCKETS = set(BUCKETS)

BUCKET_DISPLAY = {
    "trivia": "Trivia",
    "light_ops": "Light Ops",
    "heavy_ops": "Heavy Ops",
    "app_code": "App Code",
    "script": "Script",
    "debug": "Debug",
    "research": "Research",
    "repo_maintenance": "Repo-maintenance",
    "ambiguous": "Ambiguous",
}

WORKFLOW_WEIGHT = {
    "trivia": "minimal workflow weight",
    "light_ops": "Light Ops workflow weight",
    "heavy_ops": "Heavy Ops workflow weight",
    "app_code": "App Code workflow weight",
    "script": "Script workflow weight",
    "debug": "Debug workflow weight",
    "research": "Research workflow weight",
    "repo_maintenance": "Repo-maintenance workflow weight",
    "ambiguous": "Ambiguous workflow weight",
}

BUCKET_CONTRACTS: dict[str, dict[str, Any]] = {
    "trivia": {
        "first_move": "Do the tiny change or answer directly.",
        "context_required": "No Obsidian, plan, or companion skill unless the request itself asks for it.",
        "delegation_default": "Never delegate.",
        "verification_and_finish": "Cheap check only if a file or command changed; no note.",
        "obsidian_required": False,
        "reasoning_guard_triggers": [],
        "note_rule": "No Obsidian note.",
    },
    "light_ops": {
        "first_move": "Inspect nearby convention before editing.",
        "context_required": "Use repo-native lint/fmt/schema checks; no Obsidian unless design risk appears.",
        "delegation_default": "Usually direct; optionally one cheap reviewer/validator child.",
        "verification_and_finish": "Targeted lint/fmt/check; summarize and escalate if scope grows.",
        "obsidian_required": False,
        "reasoning_guard_triggers": ["design risk", "scope grows", "prod/secrets/network boundary appears"],
        "note_rule": "Ask whether to take a project note if non-trivial.",
    },
    "heavy_ops": {
        "first_move": "Name blast radius, assumptions, rollback/dry-run path.",
        "context_required": "Consult Obsidian Knowledge Workflow before proposing approach.",
        "delegation_default": "Parallelize read-only discovery, risk review, and validation-plan review; parent keeps destructive actions.",
        "verification_and_finish": "Lint/validate/render/dry-run/smoke as applicable; docs and raw Obsidian note for concrete findings/fixes.",
        "obsidian_required": True,
        "reasoning_guard_triggers": ["prod", "network", "secrets", "permissions", "data migration", "rollback-sensitive paths"],
        "note_rule": "Write a raw Projects/<repo>/YYYY-MM-DD-<slug>.md note for shipped fixes or concrete findings unless duplicate/trivial.",
    },
    "app_code": {
        "first_move": "Define success criteria and inspect existing test shape.",
        "context_required": "Inspect project conventions; use TDD/review skills when behavior changes or tests exist.",
        "delegation_default": "Use implementer/reviewer subagents for independent modules only.",
        "verification_and_finish": "Targeted tests first, broader checks as needed; update docs if API/workflow changed.",
        "obsidian_required": False,
        "reasoning_guard_triggers": ["risky refactor", "cross-module behavior change", "high confidence requested"],
        "note_rule": "Ask whether to take a project note if non-trivial.",
    },
    "script": {
        "first_move": "Define input/output/exit-code, idempotency, and side effects.",
        "context_required": "Inspect runtime/schedule conventions; keep implementation minimal.",
        "delegation_default": "Use one implementer or reviewer child for non-trivial scripts; avoid competing edits to one file.",
        "verification_and_finish": "Syntax check plus safe smoke test; for cron/launchd verify schedule, locking/state, and logs.",
        "obsidian_required": False,
        "reasoning_guard_triggers": ["LaunchAgent", "cron", "hooks", "persistent service", "side effects"],
        "note_rule": "Ask whether to take a project note if non-trivial.",
    },
    "debug": {
        "first_move": "Reproduce or observe the exact failure before fixing.",
        "context_required": "Consult Obsidian for prior incidents/patterns; use systematic debugging for unclear root cause.",
        "delegation_default": "Spawn an independent investigator when the bug is unclear: repro/logs vs code-path/config.",
        "verification_and_finish": "Verify the exact failure is gone; note concrete root cause/fix unless duplicate/trivial.",
        "obsidian_required": True,
        "reasoning_guard_triggers": ["unclear root cause", "multiple plausible causes", "repeated failed fixes"],
        "note_rule": "Write a raw Projects/<repo>/YYYY-MM-DD-<slug>.md note for concrete findings/fixes unless duplicate/trivial.",
    },
    "research": {
        "first_move": "State the question, decision needed, and evidence bar.",
        "context_required": "Use Obsidian for Ops/Infra, Debug, architecture, or reusable research; use upstream docs/source when relevant.",
        "delegation_default": "Use 2-3 researchers when scope permits: upstream, local, and prior Obsidian/org alternatives.",
        "verification_and_finish": "Report facts, assumptions, recommendation, confidence, risks, and next checks; no code changes.",
        "obsidian_required": False,
        "reasoning_guard_triggers": ["architecture", "migration", "tradeoffs", "reusable research"],
        "note_rule": "Ask whether to take a project note if non-trivial.",
    },
    "repo_maintenance": {
        "first_move": "Check git status/diff and preserve unrelated user changes.",
        "context_required": "Inspect affected areas: CI, deps, docs, tests, release metadata, config conventions.",
        "delegation_default": "Split independent inspectors where useful: CI, deps, docs, tests, release metadata.",
        "verification_and_finish": "Run affected checks, report unrelated findings separately, update docs/notes if conventions change.",
        "obsidian_required": False,
        "reasoning_guard_triggers": ["workflow policy", "hooks", "multi-surface config", "architecture conventions"],
        "note_rule": "Ask whether to take a project note if non-trivial.",
    },
    "ambiguous": {
        "first_move": "Ask one concise clarifying question, or proceed only with an explicit low-risk assumption.",
        "context_required": "Do not load extra workflow beyond what is needed to clarify.",
        "delegation_default": "Do not delegate into unclear requirements.",
        "verification_and_finish": "No edits until scope is safe; reclassify after clarification.",
        "obsidian_required": False,
        "reasoning_guard_triggers": ["multiple buckets fit", "scope changes which tools would be called"],
        "note_rule": "No note until clarified and reclassified.",
    },
}

_STOPWORDS = {
    "about", "after", "again", "against", "also", "and", "any", "are", "ask", "before", "being", "can",
    "could", "did", "does", "doing", "done", "for", "from", "get", "has", "have", "how",
    "into", "its", "just", "let", "make", "more", "need", "our", "out", "over", "please", "run",
    "some", "that", "the", "then", "this", "through", "use", "using", "want", "what", "when", "where",
    "with", "work", "would", "you", "your",
}

_KEYWORDS = {
    "debug": (
        "traceback", "stack trace", "failing", "failure", "failed", "error", "exception", "panic", "segfault",
        "broken", "bug", "regression", "not working", "doesn't work", "fix bug", "root cause", "crash", "timeout",
    ),
    "heavy_ops": (
        "production", "prod", "cluster", "kubernetes", "k8s", "terraform", "ansible", "helm", "gitops",
        "network", "tls", "certificate", "secret", "secrets", "token", "iam", "permission", "migration",
        "migrate", "rollback", "database", "stateful", "restore", "disaster", "deploy", "release pipeline",
    ),
    "light_ops": (
        "yaml", "yml", "dockerfile", "docker compose", "ci", "workflow", "nginx", "config", "values.yaml",
        "single file", "small config", "lint", "fmt",
    ),
    "app_code": (
        "api", "endpoint", "feature", "module", "package", "class", "function", "pyproject", "go.mod", "tests",
        "unit test", "integration test", "frontend", "backend", "database model", "schema", "refactor",
    ),
    "script": (
        "script", "cron", "launchd", "launchagent", "scheduled task", "scheduled-task", "automation", "hook",
        "pre_llm_call", "mcp server", "workflow mcp", "run.sh", "plist", "shell", "python glue", "watcher",
        "service", "localhost", "streamable http",
    ),
    "research": (
        "research", "compare", "options", "tradeoff", "tradeoffs", "explain", "how does", "how do", "why",
        "design", "architecture", "investigate", "explore", "evaluate", "recommend", "should we",
    ),
    "repo_maintenance": (
        "dependency", "dependencies", "bump", "upgrade", "cleanup", "clean up", "repo hygiene", "stale config",
        "release metadata", "readme", "docs cleanup", "lint cleanup", "formatting", "renovate", "pin", "unpin",
    ),
    "trivia": (
        "typo", "spelling", "one-line", "one line", "tiny", "casual", "quick question", "simple factual",
    ),
}

_MUTATION_WORDS = (
    "add", "build", "change", "configure", "create", "edit", "execute", "fix", "implement", "install",
    "modify", "patch", "remove", "rename", "set up", "setup", "update", "wire", "write",
)

_DESTRUCTIVE_WORDS = ("apply", "delete", "destroy", "remove", "force", "reset", "drop", "wipe", "terminate")
_ARCHITECTURE_WORDS = ("architecture", "design", "migration", "migrate", "tradeoff", "tradeoffs", "scaling", "performance")
_HERMES_MCP_WORDS = ("hermes", "mcp", "workflow", "codex-workflow", "launchagent", "scheduled-task", "scheduled task")


def _contains(text: str, needles: tuple[str, ...]) -> bool:
    return any(n in text for n in needles)


def _count_contains(text: str, needles: tuple[str, ...]) -> int:
    return sum(1 for n in needles if n in text)


def _tokens(text: str) -> set[str]:
    normalized = re.sub(r"[^A-Za-z0-9_./-]+", " ", text.lower())
    pieces: set[str] = set()
    for raw in normalized.split():
        for part in re.split(r"[/_.-]+", raw):
            if len(part) >= 3 and part not in _STOPWORDS:
                pieces.add(part)
    return pieces


def _repo_from_cwd(cwd: str | None) -> str | None:
    if not cwd:
        return None
    path = Path(cwd).expanduser()
    if not path.is_absolute() or not path.exists():
        return None
    try:
        resolved = path.resolve(strict=True)
    except OSError:
        return None
    return resolved.name or None


def _repo_name(repo: str | None = None, cwd: str | None = None) -> str | None:
    """Return the Obsidian Projects/<repo> slug for repo/cwd inputs.

    Agents commonly pass either a repo slug ("hermes-config") or an absolute
    checkout path ("/Users/.../src/hermes-config"). The Workflow MCP context
    lookup is keyed by the vault project slug, so path-like repo values must be
    normalized before building Projects/<repo> paths or note suggestions.
    """

    if repo:
        raw = repo.strip()
        if raw:
            parts = [p for p in raw.replace("\\", "/").split("/") if p]
            if len(parts) >= 2 and parts[-2].lower() == "projects":
                return parts[-1]
            if raw.startswith("~") or raw.startswith("/") or "/" in raw or "\\" in raw:
                return Path(raw).expanduser().name or None
            return raw
    return _repo_from_cwd(cwd)


def _validate_bucket(bucket: str) -> str:
    normalized = bucket.strip().lower().replace("-", "_")
    if normalized not in BUCKETS:
        raise ValueError(f"unknown bucket {bucket!r}; expected one of {', '.join(BUCKETS)}")
    return normalized


def _visible_statement(bucket: str, why: list[str]) -> str:
    reason = why[0] if why else BUCKET_CONTRACTS[bucket]["first_move"]
    return f"Bucket: {BUCKET_DISPLAY[bucket]} — {reason}; applying {WORKFLOW_WEIGHT[bucket]}."


def classify_task(prompt: str, cwd: str | None = None, repo: str | None = None) -> dict[str, Any]:
    """Classify a prompt into the codex-workflow bucket taxonomy.

    This is deliberately conservative and explainable. It is a routing hint, not
    an LLM replacement; low confidence or conflicting signals return ambiguity.
    """

    raw_prompt = prompt or ""
    text = raw_prompt.lower()
    word_count = len(re.findall(r"\w+", text))
    scores = {bucket: 0 for bucket in BUCKETS}
    reasons: dict[str, list[str]] = {bucket: [] for bucket in BUCKETS}
    escalation_flags: list[str] = []

    if not text.strip():
        scores["ambiguous"] += 4
        reasons["ambiguous"].append("empty prompt has no actionable scope")
    mutation = re.search(
        r"\b(add|build|change|configure|create|edit|execute|fix|install|modify|patch|remove|rename|setup|update|wire|write)\b|set up",
        text,
    ) is not None

    for bucket, needles in _KEYWORDS.items():
        hits = _count_contains(text, needles)
        if hits:
            scores[bucket] += hits * (5 if bucket in {"debug", "trivia"} else 3)
            reasons[bucket].append(f"matched {bucket.replace('_', ' ')} signal(s)")

    if "execute plan" in text or ("plan" in text and mutation):
        scores["script"] += 3
        reasons["script"].append("plan execution usually needs script-style implementation discipline")
    if "workflow mcp" in text or ("mcp" in text and "workflow" in text):
        scores["script"] += 5
        reasons["script"].append("Hermes Workflow MCP service work is standalone automation")
    if "launchagent" in text or "launchd" in text or "plist" in text:
        scores["script"] += 4
        reasons["script"].append("launchd service wrapper or plist work")
    if "readme" in text and "typo" in text:
        scores["trivia"] += 7
        reasons["trivia"].append("tiny documentation typo change")
    if _contains(text, ("dependency", "dependencies", "bump")) and _contains(text, ("ci", "cleanup", "clean up")):
        scores["repo_maintenance"] += 5
        reasons["repo_maintenance"].append("dependency and CI/repo hygiene signals")
    if _contains(text, ("fix failing", "failing test", "pipeline failure", "release pipeline failure")):
        scores["debug"] += 5
        reasons["debug"].append("explicit failure to diagnose")
    if _contains(text, ("compare", "options", "recommend", "should we")) and not mutation:
        scores["research"] += 10
        reasons["research"].append("comparison/recommendation request without immediate edits")
    if _contains(text, ("prod", "production", "secret", "secrets", "terraform", "kubernetes", "cluster", "rollback")):
        escalation_flags.append("ops_boundary")
    if _contains(text, _DESTRUCTIVE_WORDS):
        escalation_flags.append("destructive_or_apply_wording")
    if _contains(text, _ARCHITECTURE_WORDS):
        escalation_flags.append("architecture_or_tradeoff")

    if word_count <= 8 and not mutation and not any(scores[b] for b in ("debug", "heavy_ops", "app_code", "script", "repo_maintenance")):
        scores["trivia"] += 4
        reasons["trivia"].append("short simple prompt")

    # Priority nudges for common conflicts.
    if scores["debug"] and scores["heavy_ops"]:
        if _contains(text, ("prod", "production", "terraform", "cluster", "migration", "rollback", "secrets")):
            scores["heavy_ops"] += 2
            reasons["heavy_ops"].append("debugging crosses high-risk ops boundary")
        else:
            scores["debug"] += 2
            reasons["debug"].append("failure/root-cause signal is more specific than ops context")
    if scores["script"] and scores["heavy_ops"] and _contains(text, ("launchagent", "launchd", "workflow mcp", "scheduled-task")) and not _contains(text, ("prod", "production", "cluster", "terraform", "secret", "secrets")):
        scores["script"] += 3
        reasons["script"].append("local launchd/MCP service work without prod boundary")
    if scores["trivia"] >= 7:
        for bucket in ("light_ops", "repo_maintenance"):
            scores[bucket] = min(scores[bucket], 2)

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    top_bucket, top_score = ranked[0]
    second_bucket, second_score = ranked[1]

    if top_score <= 0:
        top_bucket = "ambiguous" if mutation else "trivia"
        top_score = 3
        reasons[top_bucket].append("no strong workflow-specific signal")
        second_score = 0
    elif top_bucket != "ambiguous" and second_score >= top_score - 1 and top_score < 8:
        top_bucket = "ambiguous"
        reasons[top_bucket].append(f"competing {ranked[0][0]} and {ranked[1][0]} signals")
        top_score = max(top_score, 5)

    if top_bucket == "heavy_ops" and top_score < 7 and not any(flag in escalation_flags for flag in ("ops_boundary", "destructive_or_apply_wording")):
        top_bucket = "light_ops"
        reasons[top_bucket].append("ops/config signal appears small and low-risk")

    margin = max(0, top_score - second_score)
    confidence_cap = 0.85 if top_bucket != "ambiguous" else 0.70
    if top_bucket in {"heavy_ops", "debug"} and escalation_flags:
        confidence_cap = 0.90
    confidence = round(min(confidence_cap, 0.50 + 0.04 * top_score + 0.03 * margin), 2)
    confidence = max(confidence, 0.55 if top_bucket != "ambiguous" else 0.45)
    why = reasons[top_bucket] or [BUCKET_CONTRACTS[top_bucket]["first_move"]]

    architecture_or_research = "architecture_or_tradeoff" in escalation_flags or (top_bucket == "research" and _contains(text, _ARCHITECTURE_WORDS))
    obsidian_required = bool(BUCKET_CONTRACTS[top_bucket]["obsidian_required"] or top_bucket == "heavy_ops" or top_bucket == "debug" or architecture_or_research)
    reasoning_guard_required = bool(
        top_bucket in {"heavy_ops", "debug"}
        or architecture_or_research
        or (top_bucket == "script" and _contains(text, ("launchagent", "launchd", "mcp", "service", "hook")))
    )

    return {
        "bucket": top_bucket,
        "confidence": confidence,
        "ambiguity": top_bucket == "ambiguous" or (second_score >= top_score - 1 and top_score < 8),
        "why": why,
        "scores": {k: v for k, v in scores.items() if v},
        "runner_up": {"bucket": second_bucket, "score": second_score} if second_score else None,
        "escalation_flags": sorted(set(escalation_flags)),
        "obsidian_required": obsidian_required,
        "reasoning_guard_required": reasoning_guard_required,
        "visible_statement": _visible_statement(top_bucket, why),
    }


def _vault_root(vault_root: str | None = None) -> Path:
    raw = vault_root or os.environ.get("OBSIDIAN_VAULT_PATH") or "~/Obsidian/Work"
    return Path(raw).expanduser()


def _snippet_for(text: str, terms: set[str], max_len: int = 220) -> str:
    lower = text.lower()
    positions = [lower.find(t) for t in terms if t and lower.find(t) >= 0]
    if not positions:
        clean = " ".join(text.split())
        return clean[:max_len]
    start = max(0, min(positions) - 80)
    end = min(len(text), start + max_len)
    return " ".join(text[start:end].split())


_CONTEXT_INDEX_CACHE: dict[tuple[str, tuple[str, ...]], dict[str, Any]] = {}
_CONTEXT_INDEX_MAX_CHARS = 12000


def _clamp_int(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, int(value)))


def _context_index_entries(vault: Path, roots: list[Path], warnings: list[str]) -> tuple[list[dict[str, Any]], bool]:
    existing_roots = [root for root in roots if root.exists()]
    cache_key = (str(vault), tuple(str(root) for root in existing_roots))
    signature: list[tuple[str, int, int]] = []
    paths: list[Path] = []
    for root in existing_roots:
        for path in sorted(root.rglob("*.md")):
            if "Clippings" in path.parts:
                continue
            try:
                stat = path.stat()
            except OSError as exc:
                warnings.append(f"could not stat {path.relative_to(vault)}: {exc}")
                continue
            rel = str(path.relative_to(vault))
            signature.append((rel, stat.st_mtime_ns, stat.st_size))
            paths.append(path)

    cached = _CONTEXT_INDEX_CACHE.get(cache_key)
    signature_tuple = tuple(signature)
    if cached and cached.get("signature") == signature_tuple:
        return cached["entries"], True

    entries: list[dict[str, Any]] = []
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            warnings.append(f"could not read {path.relative_to(vault)}: {exc}")
            continue
        rel = str(path.relative_to(vault))
        text_prefix = text[:_CONTEXT_INDEX_MAX_CHARS]
        entries.append(
            {
                "path": path,
                "rel": rel,
                "lower_name": rel.lower(),
                "text_prefix": text_prefix,
                "lower_text": text_prefix.lower(),
                "generated_index": path.name == "index.md" and "generated_by:" in text[:500],
                "is_index": path.name == "index.md",
                "size": len(text),
            }
        )
    _CONTEXT_INDEX_CACHE[cache_key] = {"signature": signature_tuple, "entries": entries}
    return entries, False


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
    """Return direct-keyword Obsidian note candidates without broad note dumps."""

    vault = _vault_root(vault_root)
    warnings: list[str] = []
    candidates: list[dict[str, Any]] = []
    searched_roots: list[str] = []
    if not vault.exists():
        return {"candidates": [], "warnings": [f"vault root not found: {vault}"], "searched_roots": []}

    repo_name = _repo_name(repo, cwd)
    terms = _tokens(prompt)
    if repo_name:
        terms.update(_tokens(repo_name))

    roots: list[Path] = []
    if repo_name:
        roots.append(vault / "Projects" / repo_name)
    roots.extend([vault / "Knowledge", vault / "Organization"])

    for root in roots:
        if root.exists():
            searched_roots.append(str(root.relative_to(vault)))
        elif root.parent.exists() and root.name not in {"Knowledge", "Organization"}:
            warnings.append(f"project notes root not found: {root.relative_to(vault)}")

    if not terms:
        return {"candidates": [], "warnings": warnings + ["no searchable terms derived from prompt"], "searched_roots": searched_roots}

    max_candidates = _clamp_int(max_candidates, 1, 50)
    inline_top_n = _clamp_int(inline_top_n, 0, 3)
    inline_max_chars = _clamp_int(inline_max_chars, 1, 12000)

    entries, index_used = _context_index_entries(vault, roots, warnings)
    scored: list[tuple[int, dict[str, Any], str, int]] = []
    for entry in entries:
        name_hits = {t for t in terms if t in entry["lower_name"]}
        content_hits = {t for t in terms if t in entry["lower_text"]}
        matched = sorted(name_hits | content_hits)
        if not matched:
            continue
        score = len(name_hits) * 4 + len(content_hits)
        if entry["is_index"]:
            score += 2
        if entry["generated_index"] and not name_hits and len(content_hits) < 2:
            continue
        if entry["generated_index"]:
            score -= 1
        if score <= 0:
            continue
        strength = "high" if score >= 8 else "medium" if score >= 4 else "low"
        reason = "project/layer index keyword overlap" if entry["is_index"] else "direct keyword overlap"
        item: dict[str, Any] = {
            "path": entry["rel"],
            "reason": reason,
            "strength": strength,
            "matched_terms": matched[:12],
        }
        if include_snippets:
            item["snippet"] = _snippet_for(entry["text_prefix"], set(matched))
        scored.append((score, item, entry["text_prefix"], entry["size"]))

    seen: set[str] = set()
    for _, item, text, full_size in sorted(scored, key=lambda pair: (-pair[0], pair[1]["path"])):
        if item["path"] in seen:
            continue
        seen.add(item["path"])
        if len(candidates) < inline_top_n:
            item["content"] = text[:inline_max_chars]
            item["content_truncated"] = full_size > inline_max_chars
            item["content_chars"] = len(item["content"])
        candidates.append(item)
        if len(candidates) >= max_candidates:
            break

    return {
        "candidates": candidates,
        "warnings": warnings,
        "searched_roots": searched_roots,
        "index_used": index_used,
        "indexed_files": len(entries),
    }

def required_skills_for(prompt: str, bucket: str) -> list[str]:
    text = prompt.lower()
    skills: list[str] = []
    if bucket not in {"trivia"}:
        skills.append("codex-workflow")
    if _contains(text, _HERMES_MCP_WORDS):
        skills.extend(["hermes-agent", "native-mcp"])
    if "obsidian" in text or bucket in {"debug", "heavy_ops"} or "architecture" in text:
        skills.append("obsidian")
    if bucket == "debug":
        skills.append("systematic-debugging")
    # Preserve order while de-duplicating.
    return list(dict.fromkeys(skills))


def start_task(
    prompt: str,
    cwd: str | None = None,
    repo: str | None = None,
    session_id: str | None = None,
    already_classified_bucket: str | None = None,
    fields: list[str] | None = None,
) -> dict[str, Any]:
    if already_classified_bucket:
        bucket = _validate_bucket(already_classified_bucket)
        classification = classify_task(prompt, cwd=cwd, repo=repo)
        classification["bucket"] = bucket
        classification["visible_statement"] = _visible_statement(bucket, [f"caller supplied {BUCKET_DISPLAY[bucket]} classification"])
        classification["why"] = [f"caller supplied {BUCKET_DISPLAY[bucket]} classification"]
        classification["obsidian_required"] = bool(BUCKET_CONTRACTS[bucket]["obsidian_required"] or bucket in {"debug", "heavy_ops"})
    else:
        classification = classify_task(prompt, cwd=cwd, repo=repo)
        bucket = classification["bucket"]

    repo_name = _repo_name(repo, cwd)
    need_fields = set(fields) if fields is not None else None
    context = {"candidates": [], "warnings": []}
    if need_fields is None or need_fields & {"candidate_notes", "context_warnings"}:
        context = discover_context(prompt, repo=repo_name, cwd=cwd, max_candidates=8, include_snippets=False)
    delegation = {"should_delegate": False, "tasks": [], "warnings": [], "why": "not requested"}
    if need_fields is None or need_fields & {"delegation_should_be_considered", "delegation_hint"}:
        delegation = suggest_delegation(prompt, bucket=bucket, cwd=cwd, repo=repo_name)
    finish = {"checklist": [], "suggested_note_path": None}
    if need_fields is None or need_fields & {"finish_checklist", "suggested_note_path"}:
        finish = finish_checklist(bucket=bucket, changed_files=[], commands_run=[], findings=prompt[:160], repo=repo_name)

    packet = {
        "bucket": bucket,
        "visible_statement": classification["visible_statement"],
        "confidence": classification["confidence"],
        "ambiguity": classification["ambiguity"],
        "why": classification["why"],
        "required_skills": required_skills_for(prompt, bucket),
        "obsidian_required": classification["obsidian_required"],
        "reasoning_guard_required": classification["reasoning_guard_required"],
        "delegation_should_be_considered": delegation["should_delegate"],
        "delegation_hint": delegation,
        "candidate_notes": context["candidates"],
        "context_warnings": context["warnings"],
        "contract": BUCKET_CONTRACTS[bucket],
        "finish_checklist": finish["checklist"],
        "suggested_note_path": finish.get("suggested_note_path"),
        "session_id": session_id,
    }
    if fields is not None:
        requested = [field for field in fields if field in packet]
        return {field: packet[field] for field in requested}
    return packet


def _task(goal: str, context: str, toolsets: list[str], bucket: str, reasoning_effort: str | None = None) -> dict[str, Any]:
    item: dict[str, Any] = {"goal": goal, "context": context, "toolsets": toolsets, "task_bucket": _validate_bucket(bucket)}
    if reasoning_effort:
        item["reasoning_effort"] = reasoning_effort
    return item


def _extract_delegation_signals(prompt: str) -> dict[str, list[str]]:
    paths = sorted(set(re.findall(r"(?<![A-Za-z0-9_])(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+(?:\.(?:py|sh|zsh|yaml|yml|tf|tfvars|md|json|toml|plist|go|ts|tsx|js|jsx)|Dockerfile|dockerfile)\b", prompt)))
    tickets = sorted(set(re.findall(r"\b[A-Z][A-Z0-9]+-\d+\b", prompt)))
    quoted = [item.strip() for item in re.findall(r"['\"]([^'\"]{4,160})['\"]", prompt) if item.strip()]
    error_lines = []
    for line in prompt.splitlines():
        lowered = line.lower()
        if any(word in lowered for word in ("error", "failed", "failure", "traceback", "timeout", "exception")):
            error_lines.append(line.strip()[:180])
    return {"paths": paths[:8], "tickets": tickets[:6], "quoted": quoted[:5], "errors": error_lines[:5]}


def _signal_summary(signals: dict[str, list[str]]) -> str:
    parts: list[str] = []
    if signals["paths"]:
        parts.append("paths: " + ", ".join(signals["paths"]))
    if signals["tickets"]:
        parts.append("tickets: " + ", ".join(signals["tickets"]))
    if signals["quoted"]:
        parts.append("quoted/error text: " + "; ".join(signals["quoted"]))
    if signals["errors"]:
        parts.append("error lines: " + "; ".join(signals["errors"]))
    return " | ".join(parts)


def _delegation_result(should_delegate: bool, why: str, tasks: list[dict[str, Any]], warnings: list[str], signals: dict[str, list[str]]) -> dict[str, Any]:
    return {
        "should_delegate": should_delegate,
        "why": why,
        "tasks": tasks,
        "warnings": warnings,
        "specialization": "specific" if any(signals.values()) else "generic",
        "extracted_signals": signals,
    }


def suggest_delegation(prompt: str, bucket: str | None = None, cwd: str | None = None, repo: str | None = None) -> dict[str, Any]:
    if bucket is None:
        bucket = classify_task(prompt, cwd=cwd, repo=repo)["bucket"]
    bucket = _validate_bucket(bucket)
    repo_name = _repo_name(repo, cwd)
    signals = _extract_delegation_signals(prompt)
    summary = _signal_summary(signals)
    context_lines = [f"Prompt: {prompt}", f"Repo: {repo_name or 'unknown'}", f"Bucket: {bucket}."]
    if summary:
        context_lines.append(f"Extracted signals: {summary}")
    base_context = "\n".join(context_lines)
    focus = f" ({summary})" if summary else ""
    destructive = _contains(prompt.lower(), _DESTRUCTIVE_WORDS)

    if bucket == "trivia":
        return _delegation_result(False, "Trivia work is faster and safer direct.", [], [], signals)
    if bucket == "ambiguous":
        return _delegation_result(False, "Clarify scope before spawning children.", [], ["ambiguous_scope"], signals)
    if bucket == "light_ops":
        return _delegation_result(False, "Light Ops is usually a single surgical edit; consider only a cheap validator.", [_task(f"Validate the proposed small ops/config change for syntax and convention fit{focus}", base_context, ["terminal", "file"], "light_ops", "medium")], [], signals)
    if bucket == "script":
        return _delegation_result(True, "Non-trivial script/service work benefits from one independent reviewer after implementation.", [_task(f"Review script/service implementation for idempotency, launch/runtime safety, and smoke-test coverage{focus}", base_context, ["terminal", "file"], "script", "medium")], ["parent_keeps_launchctl_and_config_mutations"] if destructive else [], signals)
    if bucket == "research":
        return _delegation_result(True, "Research can split into upstream/source, local context, and prior-knowledge streams.", [
            _task(f"Research upstream/source documentation relevant to the decision{focus}", base_context, ["web", "terminal", "file"], "research", "medium"),
            _task(f"Inspect local repo/config context and summarize constraints{focus}", base_context, ["terminal", "file"], "research", "medium"),
            _task(f"Search prior Obsidian/project knowledge for directly relevant notes{focus}", base_context, ["file"], "research", "medium"),
        ], [], signals)
    if bucket == "app_code":
        return _delegation_result(True, "App code usually benefits from focused implementation plus spec/quality review on independent modules.", [
            _task(f"Inspect existing test shape and propose the smallest targeted test plan{focus}", base_context, ["terminal", "file"], "app_code", "medium"),
            _task(f"Review implementation for spec compliance and code quality after parent/implementer changes{focus}", base_context, ["terminal", "file"], "app_code", "medium"),
        ], ["avoid_parallel_implementers_on_same_files"], signals)
    if bucket == "debug":
        return _delegation_result(True, "Unclear bugs need independent reproduction/log inspection before fixing.", [
            _task(f"Independently reproduce or observe the exact failure and list falsifiable hypotheses{focus}", base_context, ["terminal", "file"], "debug", "high"),
            _task(f"Inspect code/config paths related to the failure and propose verification commands{focus}", base_context, ["terminal", "file"], "debug", "high"),
        ], [], signals)
    if bucket == "heavy_ops":
        return _delegation_result(True, "Heavy Ops should parallelize read-only discovery, risk review, and validation planning; parent keeps applies.", [
            _task(f"Read-only discovery: identify blast radius, owners, and affected manifests/services{focus}", base_context, ["terminal", "file"], "heavy_ops", "high"),
            _task(f"Risk review: check prod/secrets/network/rollback boundaries and failure modes{focus}", base_context, ["terminal", "file"], "heavy_ops", "high"),
            _task(f"Validation plan: enumerate lint/render/dry-run/smoke checks before any apply{focus}", base_context, ["terminal", "file"], "heavy_ops", "medium"),
        ], ["parent_keeps_destructive_actions", "dry_run_before_apply"], signals)
    if bucket == "repo_maintenance":
        return _delegation_result(True, "Repo maintenance splits cleanly across CI/deps/docs/tests/release metadata inspectors.", [
            _task(f"Inspect CI/dependency/release metadata changes and validation commands{focus}", base_context, ["terminal", "file"], "repo_maintenance", "medium"),
            _task(f"Review docs/config convention impacts and unrelated local diffs{focus}", base_context, ["terminal", "file"], "repo_maintenance", "medium"),
        ], ["preserve_unrelated_user_changes"], signals)
    raise AssertionError(f"unhandled bucket {bucket}")

def _slug(text: str, fallback: str = "workflow-task") -> str:
    words = re.findall(r"[A-Za-z0-9]+", text.lower())[:8]
    return "-".join(words) or fallback


def _git_changed_files(repo_root: str | None) -> tuple[list[str], list[str]]:
    if not repo_root:
        return [], []
    root = Path(repo_root).expanduser()
    warnings: list[str] = []
    if not root.exists() or not root.is_dir():
        return [], [f"repo_root not found or not a directory: {root}"]

    changed: list[str] = []
    commands = [
        ["git", "-C", str(root), "diff", "--name-only", "HEAD"],
        ["git", "-C", str(root), "ls-files", "--others", "--exclude-standard"],
    ]
    for cmd in commands:
        try:
            proc = subprocess.run(cmd, check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=5)
        except (OSError, subprocess.SubprocessError) as exc:
            warnings.append(f"could not run {' '.join(cmd[:4])}: {exc}")
            continue
        if proc.returncode != 0:
            stderr = " ".join(proc.stderr.split())[:240]
            warnings.append(f"git change detection failed for {root}: {stderr or proc.returncode}")
            continue
        changed.extend(line.strip() for line in proc.stdout.splitlines() if line.strip())
    return sorted(dict.fromkeys(changed)), warnings


def finish_checklist(
    bucket: str,
    changed_files: list[str] | None = None,
    commands_run: list[str] | None = None,
    findings: str | None = None,
    repo: str | None = None,
    repo_root: str | None = None,
    auto_detect_changes: bool = True,
) -> dict[str, Any]:
    bucket = _validate_bucket(bucket)
    caller_changed_files = changed_files or []
    detected_files: list[str] = []
    git_warnings: list[str] = []
    if auto_detect_changes and repo_root:
        detected_files, git_warnings = _git_changed_files(repo_root)
    changed_files = sorted(dict.fromkeys([*caller_changed_files, *detected_files]))
    if detected_files and caller_changed_files:
        changed_files_source = "git+caller"
    elif detected_files:
        changed_files_source = "git"
    elif caller_changed_files:
        changed_files_source = "caller"
    else:
        changed_files_source = "none"
    commands_run = commands_run or []
    repo_name = _repo_name(repo, repo_root) or "repo"
    today = _dt.datetime.now().strftime("%Y-%m-%d")
    slug = _slug(findings or bucket, fallback=bucket)
    checklist: list[str] = []

    if bucket != "trivia":
        checklist.append("State the exact verification commands run, or the exact blocker if verification could not run.")
    else:
        checklist.append("Run only a cheap check if a file or command changed; no note required.")

    if changed_files:
        checklist.append("Review git diff/status and ensure only intended files changed.")
    if any(path.endswith((".py", ".sh", ".zsh")) for path in changed_files):
        checklist.append("Run syntax checks for edited scripts/modules and a safe smoke test.")
    if any("AGENTS.md" in path or "README" in path or "docs" in path for path in changed_files):
        checklist.append("Verify documentation references, commands, and paths still match reality.")
    if any("config.yaml" in path or "mcp_servers" in path for path in changed_files):
        checklist.extend(["Run hermes config check.", "Run hermes mcp list and hermes mcp test for any changed MCP server."])
    if any("scheduled-tasks" in path or path.endswith(".plist") or "LaunchAgents" in path for path in changed_files):
        checklist.extend([
            "Run zsh -n on edited run.sh files.",
            "Run plutil -lint on source and installed LaunchAgent plists.",
            "For loaded jobs, verify launchctl print for the specific label and inspect task-local logs.",
        ])
    if any("codex-workflow" in path or "classify-task-reminder" in path or "obsidian-index" in path or "workflow-mcp" in path for path in changed_files):
        checklist.append("Run workflow-surface validation: live/mirror comparisons, hook syntax/checks where relevant, and git diff --check.")
    if not commands_run and bucket != "trivia":
        checklist.append("Before final response, add at least one targeted verification command or explicitly document why none exists.")

    if bucket in {"heavy_ops", "debug"}:
        note_action = "write_raw_note"
        note_rule = BUCKET_CONTRACTS[bucket]["note_rule"]
        checklist.append("Write a raw Obsidian project note for concrete findings/fixes unless duplicate or trivial.")
    elif bucket in {"ambiguous", "trivia"}:
        note_action = "none"
        note_rule = BUCKET_CONTRACTS[bucket]["note_rule"]
    else:
        note_action = "ask_user"
        note_rule = f"Ask: Take a note for this? Target Projects/{repo_name}/{today}-{slug}.md."
        checklist.append(f"Ask whether to take a project note: Projects/{repo_name}/{today}-{slug}.md.")

    checklist.append("Consider memory/skill maintenance only for durable preferences, environment facts, reusable procedures, or corrected workflow gaps.")

    return {
        "bucket": bucket,
        "note_action": note_action,
        "note_rule": note_rule,
        "suggested_note_path": None if note_action == "none" else f"Projects/{repo_name}/{today}-{slug}.md",
        "changed_files_detected": changed_files,
        "changed_files_source": changed_files_source,
        "git_warnings": git_warnings,
        "checklist": checklist,
    }


__all__ = [
    "BUCKETS",
    "BUCKET_CONTRACTS",
    "DELEGATE_TASK_BUCKETS",
    "classify_task",
    "discover_context",
    "finish_checklist",
    "required_skills_for",
    "start_task",
    "suggest_delegation",
]
