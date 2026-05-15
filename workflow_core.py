#!/usr/bin/env python3
"""Deterministic helpers for Filipp's codex-workflow policy.

V1 is intentionally read-only. The human-readable source of truth remains the
`codex-workflow` skill; this module turns the stable bucket taxonomy and common
start/finish rules into structured data for a tiny local MCP server.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import plistlib
import re
import subprocess
import tomllib
import urllib.request
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

BUCKET_SKILL_NAMES = {
    "trivia": "workflow-trivia-contract",
    "light_ops": "workflow-light-ops-contract",
    "heavy_ops": "workflow-heavy-ops-contract",
    "app_code": "workflow-app-code-contract",
    "script": "workflow-script-contract",
    "debug": "workflow-debug-contract",
    "research": "workflow-research-contract",
    "repo_maintenance": "workflow-repo-maintenance-contract",
    "ambiguous": "workflow-ambiguous-contract",
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

_CONTEXT_GENERIC_TERMS = {
    "ask", "bucket", "case", "cases", "check", "checklist", "concrete", "context", "current", "exact",
    "finish", "follow", "gate", "improve", "improvements", "note", "only", "phase", "provided", "read",
    "report", "required", "review", "richer", "start", "status", "task", "text", "user",
}
_CONTEXT_DOMAIN_TERMS = {
    "codex", "codex-workflow", "hermes", "mcp", "workflow", "workflow-mcp", "launchagent", "launchd", "scheduled",
    "scheduled-task", "scheduled-tasks", "metrics", "telemetry", "eval", "corpus", "obsidian", "analyzer",
    "validate", "surfaces", "surface", "plist", "server", "health", "hook", "hooks",
}
_CONTEXT_EXACT_PHRASES = ("workflow mcp", "codex workflow", "finish checklist", "start task", "validate surfaces")


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
    normalized_text = re.sub(r"\s+", " ", text.strip(" \t\n\r.!?"))
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
    task_style_check = (
        normalized_text == "check"
        or re.search(
            r"^check\s+(?:status|state|health|failure|failures|failing|error|errors|bug|bugs|regression|regressions|broken|logs?|tests?|ci|pipeline|build|issue|issues|problem|problems)\b",
            normalized_text,
        ) is not None
    )
    if task_style_check and not any(scores[b] for b in ("heavy_ops", "app_code", "script", "repo_maintenance")):
        scores["debug"] += 6
        reasons["debug"].append("task-style check implies investigation")
    if _contains(text, ("compare", "options", "recommend", "should we")) and not mutation:
        scores["research"] += 10
        reasons["research"].append("comparison/recommendation request without immediate edits")
    if _contains(text, ("prod", "production", "secret", "secrets", "terraform", "kubernetes", "cluster", "rollback")):
        escalation_flags.append("ops_boundary")
    if _contains(text, _DESTRUCTIVE_WORDS):
        escalation_flags.append("destructive_or_apply_wording")
    if _contains(text, _ARCHITECTURE_WORDS):
        escalation_flags.append("architecture_or_tradeoff")

    if word_count <= 5 and text.strip() in {"make it better", "improve it", "fix it", "do it"}:
        scores["ambiguous"] += 6
        reasons["ambiguous"].append("vague prompt changes scope and required tools")
    elif word_count <= 8 and not mutation and not any(scores[b] for b in ("debug", "heavy_ops", "app_code", "script", "repo_maintenance")):
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
    ci_only_prod = (
        _contains(text, ("ci", "pipeline", "matrix", "labels"))
        and _contains(text, ("prod", "production", "deploy"))
        and _contains(text, ("no secret", "no secrets", "no runtime", "ci-only", "ci only", "labels only"))
        and not _contains(text, ("apply", "cluster", "terraform", "rbac", "network policy", "helm values"))
    )
    if ci_only_prod:
        scores["light_ops"] += 7
        scores["heavy_ops"] = min(scores["heavy_ops"], 5)
        reasons["light_ops"].append("production wording appears limited to CI-only matrix/list wiring")
        if "ops_boundary" in escalation_flags:
            escalation_flags.remove("ops_boundary")
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


def _context_term_weight(term: str) -> int:
    if term in _CONTEXT_DOMAIN_TERMS:
        return 3
    if term in _CONTEXT_GENERIC_TERMS:
        return 0
    if re.search(r"\d", term) or len(term) >= 10:
        return 2
    return 1


def _context_match_class(
    score: int,
    matched: list[str],
    domain_hits: set[str],
    exact_phrase_hit: bool,
    name_hits: set[str] | None = None,
) -> tuple[str, str | None]:
    informative_hits = {term for term in matched if _context_term_weight(term) > 0}
    informative_name_hits = {term for term in (name_hits or set()) if _context_term_weight(term) > 0}
    if exact_phrase_hit or len(domain_hits) >= 2 or (score >= 12 and (len(informative_hits) >= 2 or bool(informative_name_hits))):
        return "must_read", None
    if score >= 5 and informative_hits:
        return "likely_relevant", None
    if domain_hits:
        return "likely_relevant", None
    return "weak_match", "Only generic/low-weight terms matched; read only if higher-ranked candidates are insufficient."


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
    prompt_lower = prompt.lower()
    repo_terms = _tokens(repo_name or "")
    for entry in entries:
        name_hits = {t for t in terms if t in entry["lower_name"]}
        content_hits = {t for t in terms if t in entry["lower_text"]}
        matched = sorted(name_hits | content_hits)
        if not matched:
            continue
        domain_hits = {t for t in matched if t in _CONTEXT_DOMAIN_TERMS or t in repo_terms}
        exact_phrase_hit = any(
            phrase in prompt_lower and phrase.replace(" ", "-") in entry["lower_name"].replace("_", "-")
            for phrase in _CONTEXT_EXACT_PHRASES
        )
        score = 0
        for term in name_hits:
            score += max(1, _context_term_weight(term)) * 4
        for term in content_hits:
            score += _context_term_weight(term)
        if repo_terms and name_hits & repo_terms:
            score += 5
        if exact_phrase_hit:
            score += 8
        if entry["is_index"]:
            score += 2
        if entry["generated_index"] and not name_hits and len([t for t in content_hits if _context_term_weight(t) > 0]) < 2:
            continue
        if entry["generated_index"]:
            score -= 1
        if score <= 0:
            continue
        match_class, why_not_stronger = _context_match_class(score, matched, domain_hits, exact_phrase_hit, name_hits)
        strength = "high" if match_class == "must_read" else "medium" if match_class == "likely_relevant" else "low"
        reason = "project/layer index keyword overlap" if entry["is_index"] else "direct keyword overlap"
        item: dict[str, Any] = {
            "path": entry["rel"],
            "reason": reason,
            "strength": strength,
            "match_class": match_class,
            "score": score,
            "matched_terms": matched[:12],
        }
        if why_not_stronger:
            item["why_not_stronger"] = why_not_stronger
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

def required_skills_for(
    prompt: str,
    bucket: str,
    obsidian_required: bool | None = None,
    reasoning_guard_required: bool | None = None,
) -> list[str]:
    text = prompt.lower()
    bucket = _validate_bucket(bucket)
    skills: list[str] = []
    if bucket not in {"trivia"}:
        skills.append("codex-workflow")
    skills.append(BUCKET_SKILL_NAMES[bucket])
    if _contains(text, _HERMES_MCP_WORDS):
        skills.extend(["hermes-agent", "native-mcp"])
    if obsidian_required is None:
        obsidian_required = bool("obsidian" in text or BUCKET_CONTRACTS[bucket]["obsidian_required"] or "architecture" in text)
    if obsidian_required:
        skills.append("obsidian")
    if bucket == "debug":
        skills.append("systematic-debugging")
    # A reasoning guard is an output contract rather than a separate tool, but keep
    # codex-workflow present when the selected bucket is otherwise trivial/ambiguous
    # and the prompt-derived risk still requires evidence-gated reasoning.
    if reasoning_guard_required and "codex-workflow" not in skills and bucket != "trivia":
        skills.insert(0, "codex-workflow")
    # Preserve order while de-duplicating.
    return list(dict.fromkeys(skills))


def _bucket_decision(classification: dict[str, Any], bucket: str) -> dict[str, Any]:
    runner_up = classification.get("runner_up") or {}
    why = classification.get("why") or []
    override_hint = None
    if runner_up.get("bucket"):
        display = BUCKET_DISPLAY.get(runner_up["bucket"], runner_up["bucket"])
        override_hint = f"If gathered evidence fits {display} better, call start_task with already_classified_bucket='{runner_up['bucket']}'."
    return {
        "selected": bucket,
        "runner_up": runner_up or None,
        "selected_because": why,
        "not_runner_up_because": "Selected bucket has stronger policy-specific score or caller override." if runner_up else None,
        "override_hint": override_hint,
    }


def _risk_axes_for(prompt: str, bucket: str, classification: dict[str, Any]) -> list[str]:
    axes = list(classification.get("escalation_flags") or [])
    text = prompt.lower()
    if bucket == "script" and _contains(text, ("launchagent", "launchd", "plist", "service", "hook", "scheduled")):
        axes.append("persistent_service_or_schedule")
    if bucket in {"debug", "heavy_ops"}:
        axes.append("requires_evidence_before_action")
    if classification.get("obsidian_required"):
        axes.append("prior_context_required")
    return sorted(dict.fromkeys(axes))


def _must_not_do_before(bucket: str, risk_axes: list[str]) -> list[str]:
    rules: dict[str, list[str]] = {
        "trivia": ["Do not add process overhead unless the tiny change touches files or commands."],
        "light_ops": ["Do not widen scope beyond the small config/convention change without reclassifying."],
        "heavy_ops": ["Do not apply or mutate remote/prod state before naming blast radius, rollback/dry-run path, and validation."],
        "app_code": ["Do not implement broad refactors before defining success criteria and existing test shape."],
        "script": ["Do not install or reload persistent jobs before syntax checks and a safe smoke/dry-run path."],
        "debug": ["Do not patch before reproducing or observing the exact failure and naming falsifiable hypotheses."],
        "research": ["Do not present assumptions as facts; separate source-backed evidence from recommendations."],
        "repo_maintenance": ["Do not stage broad or unrelated local changes; preserve existing user work."],
        "ambiguous": ["Do not edit files until scope is clarified or a low-risk assumption is explicit."],
    }
    out = list(rules[bucket])
    if "prior_context_required" in risk_axes:
        out.append("Do not make Obsidian/project claims before reading the directly relevant candidate notes.")
    return out


def _required_evidence(bucket: str, risk_axes: list[str]) -> list[str]:
    evidence = ["Exact file paths or commands inspected for the intended change."] if bucket != "trivia" else []
    if bucket in {"debug", "heavy_ops"} or "requires_evidence_before_action" in risk_axes:
        evidence.append("Tool-backed evidence for the failure/risk/blast radius before changing behavior.")
    if "persistent_service_or_schedule" in risk_axes:
        evidence.extend(["launchd plist semantics", "script idempotency/lock/state behavior", "task-local logs or dry-run output"])
    return evidence


def _finish_requirements(bucket: str, finish: dict[str, Any]) -> dict[str, Any]:
    return {
        "must_call_finish_checklist": bucket != "trivia",
        "note_action": finish.get("note_action"),
        "suggested_note_path": finish.get("suggested_note_path"),
        "must_state_verification": bucket != "trivia",
        "must_review_diff": True,
    }


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
        original_bucket = classification["bucket"]
        prompt_obsidian_required = bool(classification.get("obsidian_required"))
        prompt_reasoning_guard_required = bool(classification.get("reasoning_guard_required"))
        classification["bucket"] = bucket
        classification["visible_statement"] = _visible_statement(bucket, [f"caller supplied {BUCKET_DISPLAY[bucket]} classification"])
        classification["why"] = [f"caller supplied {BUCKET_DISPLAY[bucket]} classification"]
        classification["obsidian_required"] = bool(
            prompt_obsidian_required
            or BUCKET_CONTRACTS[bucket]["obsidian_required"]
            or bucket in {"debug", "heavy_ops"}
        )
        classification["reasoning_guard_required"] = bool(
            prompt_reasoning_guard_required
            or bucket in {"debug", "heavy_ops"}
            or (bucket == "script" and _contains(prompt.lower(), ("launchagent", "launchd", "mcp", "service", "hook")))
        )
        classification["override"] = {"from": original_bucket, "to": bucket}
        classification["ambiguity"] = bool(classification.get("ambiguity") or original_bucket != bucket)
    else:
        classification = classify_task(prompt, cwd=cwd, repo=repo)
        bucket = classification["bucket"]

    repo_name = _repo_name(repo, cwd)
    need_fields = set(fields) if fields is not None else None
    context = {"candidates": [], "warnings": []}
    if need_fields is None or need_fields & {"candidate_notes", "context_candidates", "context_warnings"}:
        context = discover_context(prompt, repo=repo_name, cwd=cwd, max_candidates=8, include_snippets=False)
    delegation = {"should_delegate": False, "tasks": [], "warnings": [], "why": "not requested"}
    if need_fields is None or need_fields & {"delegation_should_be_considered", "delegation_hint", "delegation"}:
        delegation = suggest_delegation(prompt, bucket=bucket, cwd=cwd, repo=repo_name)
    finish = {"checklist": [], "suggested_note_path": None}
    if need_fields is None or need_fields & {"finish_checklist", "finish_requirements", "suggested_note_path"}:
        finish = finish_checklist(bucket=bucket, changed_files=[], commands_run=[], findings=prompt[:160], repo=repo_name)

    risk_axes = _risk_axes_for(prompt, bucket, classification)
    first_move = BUCKET_CONTRACTS[bucket]["first_move"]
    packet = {
        "bucket": bucket,
        "visible_statement": classification["visible_statement"],
        "confidence": classification["confidence"],
        "ambiguity": classification["ambiguity"],
        "why": classification["why"],
        "bucket_decision": _bucket_decision(classification, bucket),
        "first_move": first_move,
        "must_not_do_before": _must_not_do_before(bucket, risk_axes),
        "risk_axes": risk_axes,
        "required_evidence": _required_evidence(bucket, risk_axes),
        "success_criteria": [BUCKET_CONTRACTS[bucket]["verification_and_finish"]],
        "required_skills": required_skills_for(prompt, bucket, classification["obsidian_required"], classification["reasoning_guard_required"]),
        "obsidian_required": classification["obsidian_required"],
        "reasoning_guard_required": classification["reasoning_guard_required"],
        "reasoning_guard": {
            "required": classification["reasoning_guard_required"],
            "triggers": BUCKET_CONTRACTS[bucket].get("reasoning_guard_triggers", []),
            "risk_axes": risk_axes,
        },
        "delegation_should_be_considered": delegation["should_delegate"],
        "delegation_hint": delegation,
        "delegation": delegation,
        "candidate_notes": context["candidates"],
        "context_candidates": context["candidates"],
        "context_warnings": context["warnings"],
        "contract": BUCKET_CONTRACTS[bucket],
        "finish_checklist": finish["checklist"],
        "finish_requirements": _finish_requirements(bucket, finish),
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
    contract = BUCKET_CONTRACTS[bucket]
    local_risk_axes = _risk_axes_for(prompt, bucket, classify_task(prompt, cwd=cwd, repo=repo))
    forbidden = "; ".join(_must_not_do_before(bucket, local_risk_axes)[:2])
    context_lines = [
        f"Bucket: {bucket}.",
        "Mutation boundary: read-only; do not mutate files, git history, remote systems, notes, or runtime config.",
        f"Exact goal from parent prompt: {prompt}",
        f"Repo: {repo_name or 'unknown'}",
        f"First-move contract: {contract['first_move']}",
        f"Forbidden early action: {forbidden}",
        "Allowed toolsets are the toolsets attached to this delegate_task only.",
        "Expected output: concise facts with paths/URLs/commands, risks, recommendation, confidence, unknowns, and verification suggestions.",
        "Parent will verify this summary before claiming success.",
    ]
    if summary:
        context_lines.append(f"Relevant paths/URLs/errors/signals: {summary}")
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


def _required_checks_for_files(changed_files: list[str]) -> list[dict[str, str]]:
    checks: list[dict[str, str]] = []

    def add(key: str, command_hint: str, reason: str) -> None:
        if not any(item["key"] == key for item in checks):
            checks.append({"key": key, "command_hint": command_hint, "reason": reason})

    if any(path.endswith(".py") for path in changed_files):
        add("python_tests", "pytest and/or py_compile for edited Python", "Python code changed")
    if any(path.endswith((".sh", ".zsh")) or path.endswith("run.sh") for path in changed_files):
        add("shell_syntax", "zsh -n or sh -n on edited scripts", "Shell script changed")
    if any(path.endswith(".plist") for path in changed_files):
        add("plist_lint", "plutil -lint on source and installed plists", "LaunchAgent plist changed")
        add("launchctl_print", "launchctl print for loaded labels", "LaunchAgent schedule/runtime changed")
    if any("config.yaml" in path or "mcp_servers" in path for path in changed_files):
        add("hermes_config", "hermes config check", "Hermes config changed")
        add("mcp_test", "hermes mcp test for changed MCP servers", "MCP config changed")
    if any("workflow-mcp" in path for path in changed_files):
        add("workflow_smoke", "workflow-mcp smoke.py and direct MCP smoke", "Workflow MCP changed")
        add("surface_validation", "validate_surfaces or live/mirror comparisons", "Workflow surfaces changed")
    if any("README" in path or "AGENTS.md" in path or "/docs/" in path for path in changed_files):
        add("docs_review", "review docs commands and paths", "Documentation changed")
    return checks


def _command_covers_check(command: str, check_key: str) -> bool:
    c = command.lower()
    matchers = {
        "python_tests": ("pytest", "py_compile"),
        "shell_syntax": ("zsh -n", "sh -n"),
        "plist_lint": ("plutil -lint",),
        "launchctl_print": ("launchctl print",),
        "hermes_config": ("hermes config check",),
        "mcp_test": ("hermes mcp test",),
        "workflow_smoke": ("smoke.py", "call_tool('start_task'", 'call_tool("start_task"'),
        "surface_validation": ("validate_surfaces", "cmp -s", "diff --check"),
        "docs_review": ("readme", "docs", "documentation"),
    }
    return any(token in c for token in matchers.get(check_key, (check_key,)))


def finish_checklist(
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

    required_checks = _required_checks_for_files(changed_files)
    missing_verification = [
        check for check in required_checks
        if not any(_command_covers_check(command, check["key"]) for command in commands_run)
    ]
    if not commands_run and bucket != "trivia":
        missing_verification.append({"key": "verification_summary", "command_hint": "run or explicitly block at least one targeted verification", "reason": "non-trivia task"})

    docs_paths = [path for path in changed_files if "README" in path or "AGENTS.md" in path or "/docs/" in path]
    workflow_surface_paths = [path for path in changed_files if "workflow-mcp" in path or "codex-workflow" in path or "hooks/" in path]
    missing_docs: list[str] = []
    if docs_changed is False and (docs_paths or workflow_surface_paths):
        missing_docs.append("Documentation/workflow surface changed but docs_changed=false.")
    missing_notes: list[str] = []
    if note_action == "write_raw_note" and not notes_written:
        missing_notes.append("Raw project note required for this bucket/findings and none were reported.")
    missing_skill_or_memory_action: list[str] = []
    if workflow_surface_paths and not skills_updated:
        missing_skill_or_memory_action.append("Workflow surface changed; confirm codex-workflow/hermes-agent skill references are still current or update them.")
    external_side_effects_review = [
        f"Verify side effect outcome and rollback/undo path: {item}" for item in (external_side_effects or [])
    ]
    subagent_verification_required = [
        f"Parent must verify subagent result before claiming success: {item}" for item in (subagents_used or [])
    ]
    unsafe_to_finalize = bool(
        missing_verification
        or missing_notes
        or (bucket in {"heavy_ops", "debug"} and external_side_effects and not commands_run)
    )
    final_response_must_include = ["Bucket/workflow used", "Files changed", "Verification commands/results"]
    if unsafe_to_finalize:
        final_response_must_include.append("Explicit blockers or missing checks")
    if note_action == "ask_user":
        final_response_must_include.append("Ask whether to take a project note")

    return {
        "bucket": bucket,
        "note_action": note_action,
        "note_rule": note_rule,
        "suggested_note_path": None if note_action == "none" else f"Projects/{repo_name}/{today}-{slug}.md",
        "changed_files_detected": changed_files,
        "changed_files_source": changed_files_source,
        "git_warnings": git_warnings,
        "required_checks": required_checks,
        "missing_verification": missing_verification,
        "missing_docs": missing_docs,
        "missing_notes": missing_notes,
        "missing_skill_or_memory_action": missing_skill_or_memory_action,
        "external_side_effects_review": external_side_effects_review,
        "subagent_verification_required": subagent_verification_required,
        "unsafe_to_finalize": unsafe_to_finalize,
        "final_response_must_include": final_response_must_include,
        "verification_intent": verification_intent,
        "checklist": checklist,
    }


def _read_text_safe(path: Path) -> tuple[str | None, str | None]:
    try:
        return path.read_text(encoding="utf-8", errors="replace"), None
    except OSError as exc:
        return None, str(exc)


def _surface_check(name: str, status: str, detail: str, path: str | None = None) -> dict[str, str]:
    item = {"name": name, "status": status, "detail": detail}
    if path:
        item["path"] = path
    return item


def validate_surfaces(
    repo_root: str | None = None,
    live_root: str | None = None,
    mirror_root: str | None = None,
    health_url: str = "http://127.0.0.1:8813/health",
    health_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Read-only drift checks for Workflow MCP, workflow skills, hooks, plist, config, and health."""

    home = Path.home()
    repo_path = Path(repo_root).expanduser() if repo_root else home / "src/hermes-config"
    live_task = Path(live_root).expanduser() if live_root else home / ".hermes/scheduled-tasks/workflow-mcp"
    mirror_task = Path(mirror_root).expanduser() if mirror_root else repo_path / "scheduled-tasks/workflow-mcp"
    checks: list[dict[str, str]] = []
    drift: list[dict[str, str]] = []
    suggested_fix_plan: list[str] = []

    for label, path in (("live_task", live_task), ("mirror_task", mirror_task), ("repo_root", repo_path)):
        checks.append(_surface_check(label, "ok" if path.exists() else "error", "exists" if path.exists() else "missing", str(path)))

    compare_pairs = [
        ("workflow_core", live_task / "workflow_core.py", mirror_task / "workflow_core.py"),
        ("server", live_task / "server.py", mirror_task / "server.py"),
        ("metrics", live_task / "metrics.py", mirror_task / "metrics.py"),
        ("run_sh", live_task / "run.sh", mirror_task / "run.sh"),
        ("workflow_plist", live_task / "com.filipp.hermes-workflow-mcp.plist", mirror_task / "com.filipp.hermes-workflow-mcp.plist"),
        ("codex_workflow_skill", home / ".hermes/skills/codex-workflow/SKILL.md", repo_path / "skills/codex-workflow/SKILL.md"),
        ("classify_hook", home / ".hermes/hooks/classify-task-reminder.py", repo_path / "hooks/classify-task-reminder.py"),
        ("obsidian_hook", home / ".hermes/hooks/obsidian-index.py", repo_path / "hooks/obsidian-index.py"),
    ]
    for name, left, right in compare_pairs:
        if not left.exists() or not right.exists():
            checks.append(_surface_check(name, "warn", f"cannot compare missing file(s): {left.exists()} {right.exists()}"))
            continue
        same = left.read_bytes() == right.read_bytes()
        checks.append(_surface_check(name, "ok" if same else "warn", "live and mirror match" if same else "live/mirror drift"))
        if not same:
            drift.append({"surface": name, "live": str(left), "mirror": str(right), "kind": "content_mismatch"})

    installed_plist = home / "Library/LaunchAgents/com.filipp.hermes-workflow-mcp.plist"
    source_plist = live_task / "com.filipp.hermes-workflow-mcp.plist"
    if installed_plist.exists() and source_plist.exists():
        same = installed_plist.read_bytes() == source_plist.read_bytes()
        checks.append(_surface_check("installed_workflow_plist", "ok" if same else "warn", "installed plist matches live source" if same else "installed plist drift", str(installed_plist)))
        if not same:
            drift.append({"surface": "installed_workflow_plist", "live": str(source_plist), "installed": str(installed_plist), "kind": "content_mismatch"})
        try:
            data = plistlib.loads(installed_plist.read_bytes())
            label = data.get("Label")
            checks.append(_surface_check("installed_workflow_plist_label", "ok" if label == "com.filipp.hermes-workflow-mcp" else "error", f"label={label}", str(installed_plist)))
        except Exception as exc:
            checks.append(_surface_check("installed_workflow_plist_parse", "error", str(exc), str(installed_plist)))
    else:
        checks.append(_surface_check("installed_workflow_plist", "warn", "source or installed plist missing", str(installed_plist)))

    config_path = home / ".hermes/config.yaml"
    config_text, config_err = _read_text_safe(config_path)
    if config_text is None:
        checks.append(_surface_check("hermes_config", "error", config_err or "unreadable", str(config_path)))
    else:
        has_workflow = "workflow:" in config_text and "127.0.0.1:8813/mcp" in config_text
        checks.append(_surface_check("hermes_config_workflow_mcp", "ok" if has_workflow else "error", "workflow MCP configured" if has_workflow else "workflow MCP config missing", str(config_path)))

    # Codex readiness: hooks can remind Codex to use Workflow MCP, but the
    # runtime also needs MCP wiring and local split skills available.
    codex_root = home / ".codex"
    codex_config_path = codex_root / "config.toml"
    codex_config_text, codex_config_err = _read_text_safe(codex_config_path)
    if codex_config_text is None:
        checks.append(_surface_check("codex_config", "warn", codex_config_err or "unreadable", str(codex_config_path)))
    else:
        try:
            codex_config_data = tomllib.loads(codex_config_text)
            workflow_cfg = (codex_config_data.get("mcp_servers") or {}).get("workflow") or {}
            workflow_url = str(workflow_cfg.get("url", ""))
            has_codex_workflow = workflow_url == "http://127.0.0.1:8813/mcp"
            detail = f"workflow url={workflow_url or '<missing>'}"
        except Exception as exc:
            has_codex_workflow = False
            detail = f"config parse failed: {exc}"
        checks.append(_surface_check("codex_config_workflow_mcp", "ok" if has_codex_workflow else "error", detail, str(codex_config_path)))

    codex_hooks_json = codex_root / "hooks.json"
    codex_hooks_text, codex_hooks_err = _read_text_safe(codex_hooks_json)
    if codex_hooks_text is None:
        checks.append(_surface_check("codex_hooks_json", "warn", codex_hooks_err or "unreadable", str(codex_hooks_json)))
    else:
        try:
            json.loads(codex_hooks_text)
            checks.append(_surface_check("codex_hooks_json", "ok", "valid JSON", str(codex_hooks_json)))
        except json.JSONDecodeError as exc:
            checks.append(_surface_check("codex_hooks_json", "error", f"invalid JSON: {exc}", str(codex_hooks_json)))

    for hook_name in ("classify-task-reminder.sh", "obsidian-index.sh"):
        hook_path = codex_root / "hooks" / hook_name
        if not hook_path.exists():
            checks.append(_surface_check(f"codex_{hook_name}", "warn", "missing", str(hook_path)))
            continue
        try:
            proc = subprocess.run(["sh", "-n", str(hook_path)], check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=5)
            ok = proc.returncode == 0
            detail = "shell syntax ok" if ok else (proc.stderr.strip()[:240] or f"exit {proc.returncode}")
            checks.append(_surface_check(f"codex_{hook_name}", "ok" if ok else "error", detail, str(hook_path)))
        except Exception as exc:
            checks.append(_surface_check(f"codex_{hook_name}", "error", str(exc), str(hook_path)))

    codex_skill = codex_root / "skills/codex-workflow/SKILL.md"
    source_skill = repo_path / "skills/codex-workflow/SKILL.md"
    if codex_skill.exists() and source_skill.exists():
        codex_text = codex_skill.read_text(encoding="utf-8", errors="replace")
        required_policy_terms = (
            "mcp_workflow_start_task",
            "mcp_workflow_finish_checklist",
            "Workflow/Obsidian MCP before Obsidian claims",
            "Candidate paths are routing metadata",
        )
        aligned = all(term in codex_text for term in required_policy_terms)
        detail = "Codex codex-workflow contains required Workflow MCP policy terms" if aligned else "Codex codex-workflow missing required Workflow MCP policy terms"
        checks.append(_surface_check("codex_workflow_skill_codex", "ok" if aligned else "warn", detail, str(codex_skill)))
        if not aligned:
            drift.append({"surface": "codex_workflow_skill_codex", "codex": str(codex_skill), "source": str(source_skill), "kind": "policy_alignment_missing"})
    else:
        checks.append(_surface_check("codex_workflow_skill_codex", "warn", "Codex or source codex-workflow skill missing", str(codex_skill)))

    source_contract_root = repo_path / "skills/workflow-contracts"
    hermes_contract_root = home / ".hermes/skills/workflow-contracts"
    codex_contract_root = codex_root / "skills"
    for skill_name in BUCKET_SKILL_NAMES.values():
        source_skill_path = source_contract_root / skill_name / "SKILL.md"
        hermes_skill_path = hermes_contract_root / skill_name / "SKILL.md"
        codex_skill_path = codex_contract_root / skill_name / "SKILL.md"
        for surface, target_path in (("hermes", hermes_skill_path), ("codex", codex_skill_path)):
            check_name = f"{surface}_{skill_name}"
            if not source_skill_path.exists() or not target_path.exists():
                checks.append(_surface_check(check_name, "warn", "source or installed contract skill missing", str(target_path)))
                continue
            same = source_skill_path.read_bytes() == target_path.read_bytes()
            checks.append(_surface_check(check_name, "ok" if same else "warn", "contract skill matches source" if same else "contract skill drift", str(target_path)))
            if not same:
                drift.append({"surface": check_name, "source": str(source_skill_path), "installed": str(target_path), "kind": "content_mismatch"})

    try:
        if health_payload is not None:
            health = health_payload
            health_source = "internal"
        else:
            with urllib.request.urlopen(health_url, timeout=3) as response:
                health = json.loads(response.read().decode("utf-8"))
            health_source = health_url
        ok = health.get("status") == "ok"
        checks.append(_surface_check("workflow_health", "ok" if ok else "error", json.dumps(health, sort_keys=True)[:500], health_source))
        current = health.get("current_source_version") or health.get("service_version")
        process = health.get("process_service_version")
        if process and current and process != current:
            drift.append({"surface": "workflow_health_version", "process_service_version": str(process), "current_source_version": str(current), "kind": "process_stale"})
    except Exception as exc:
        checks.append(_surface_check("workflow_health", "error", str(exc), health_url))

    if drift:
        suggested_fix_plan.extend([
            "Sync mirror/live Workflow MCP files with rsync excluding logs and pycache.",
            "Copy changed plists to ~/Library/LaunchAgents and kickstart only affected labels.",
            "Run tests, py_compile, smoke.py, plutil, hermes config check, and hermes mcp test workflow.",
        ])
    statuses = {item["status"] for item in checks}
    status = "error" if "error" in statuses else "warn" if "warn" in statuses or drift else "ok"
    return {"status": status, "checks": checks, "drift": drift, "suggested_fix_plan": suggested_fix_plan}


__all__ = [
    "BUCKETS",
    "BUCKET_CONTRACTS",
    "BUCKET_SKILL_NAMES",
    "DELEGATE_TASK_BUCKETS",
    "classify_task",
    "discover_context",
    "finish_checklist",
    "required_skills_for",
    "start_task",
    "suggest_delegation",
    "validate_surfaces",
]
