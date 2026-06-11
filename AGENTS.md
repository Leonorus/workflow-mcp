# workflow-mcp

Local MCP service (streamable HTTP, `http://127.0.0.1:8813/mcp`) that turns
the codex-workflow policy into structured helpers: `start_task`,
`discover_context`, `finish_checklist`. Pure stdlib + MCP SDK; no writes to
Obsidian, git, or launchd — the only local writes are the two JSONL logs.

## Layout

- `workflow_core.py` — all policy logic (classifier, contracts, context
  discovery, delegation, finish checklist). Deterministic, no I/O beyond
  vault reads and git change detection; never reads learning state, so
  evals stay memory-free.
- `learning.py` — feedback capture and exact-match bucket memory. Appends
  feedback events (overrides, start/finish bucket disagreement, ambiguous or
  low-confidence calls) to `logs/feedback.jsonl` and folds that file into an
  in-memory overlay: a previously corrected prompt is re-delivered with its
  corrected bucket (visible as `memory` in the packet). `server.py` routes
  `start_task`/`finish_checklist` through these wrappers. Correlation:
  `start_task` returns a `task_id`; pass it to `finish_checklist` (heuristic
  repo/time fallback when missing).
- `server.py` — FastMCP HTTP wrapper, one thin tool per core function.
- `metrics.py` — privacy-safe JSONL telemetry (prompt hashes only); `/health`
  stats.
- `analyze_metrics.py` — weekly telemetry report (run by the
  `workflow-mcp-analyzer` task in hermes-config).
- `tune_report.py` — human-reviewed tuning loop: mines `feedback.jsonl` +
  `calls.jsonl` into proposed keyword candidates, score diagnostics, and
  ready-to-paste golden cases. It never edits code or evals; apply by hand
  behind the eval gate.
- `evals/` — golden classification cases and fixture-vault context cases.
  Use `$PY tune_report.py` to mine corrections into `evals/golden.jsonl`.

## Commands

```bash
PY=~/.hermes/hermes-agent/venv/bin/python   # system Python lacks the MCP SDK
$PY -m pytest tests -q
$PY -m py_compile *.py
$PY eval.py        # must stay green; add eval cases BEFORE classifier changes
$PY smoke.py
zsh -n run.sh
plutil -lint com.filipp.hermes-workflow-mcp.plist
```

## Deploy

launchd (`com.filipp.hermes-workflow-mcp`) runs `run.sh` directly from this
checkout — no install/copy step. After changing service code:

```bash
launchctl kickstart -k "gui/$(id -u)/com.filipp.hermes-workflow-mcp"
curl -fsS http://127.0.0.1:8813/health   # expect tools:3 and the repo HEAD sha
```

If the plist itself changed, re-copy it to `~/Library/LaunchAgents/` and
bootout/bootstrap instead of kickstart. Telemetry lives in `logs/` (gitignored).

Never reload launchd before tests/eval/smoke pass. Open agent sessions cache
tool schemas until restarted.

## Conventions

- TDD: failing test or eval case first for behavior changes.
- Classifier changes must keep all `evals/golden.jsonl` cases green; intent
  (analysis vs mutation verbs) outranks subject-domain keywords.
- Telemetry stays privacy-safe: never log raw prompts or note contents —
  deliberate exception: `logs/feedback.jsonl` stores raw prompts on feedback
  events only (local-only, gitignored). It is the single source of truth for
  bucket memory; pruning a bad memory = deleting its line.
- A memory/override-supplied bucket must never downgrade safety: escalation
  flags, `obsidian_required`, `reasoning_guard_required` stay prompt-derived.
- Learning must never break a tool call: wrappers swallow their own errors.
- Conventional commit subjects, no Jira prefix (personal repo).
