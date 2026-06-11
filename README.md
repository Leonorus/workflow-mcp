# Workflow MCP Oracle

Read-only local MCP service that turns Filipp's `codex-workflow` policy into structured workflow helpers for Codex.

The canonical policy remains the `codex-workflow` skill. This service only operationalizes stable policy data: bucket classification, bucket contracts, direct Obsidian context candidates, delegation suggestions, and finish checklists. V1 deliberately performs no writes to Obsidian, Codex config, git, or launchd.

## Endpoint and launchd

- Label: `com.filipp.hermes-workflow-mcp`
- Runs in place from this repo: `~/Projects/workflow-mcp/` (`github.com/Leonorus/workflow-mcp`)
- MCP endpoint: `http://127.0.0.1:8813/mcp`
- Health endpoint: `http://127.0.0.1:8813/health`
- Logs: `~/Projects/workflow-mcp/logs/` (gitignored)
- Python: `~/.hermes/hermes-agent/venv/bin/python` because system Python does not include the MCP SDK.

Codex config shape:

```toml
[mcp_servers.workflow]
url = "http://127.0.0.1:8813/mcp"
```

Fresh Codex sessions may need restart to see newly registered MCP tools.

## MCP tools

V2 exposes three MCP tools over nine workflow buckets (`trivia`, `light_ops`, `heavy_ops`, `app_code`, `script`, `debug`, `research`, `repo_maintenance`, `ambiguous`):

- `start_task` — full start packet: bucket, visible statement, Codex skill names, Obsidian requirement, context candidates, delegation hint, contract, finish checklist, `first_move`, `must_not_do_before`, `risk_axes`, `required_evidence`, `bucket_decision`, `reasoning_guard`, and `finish_requirements`. Optional `fields` returns only requested top-level fields; unknown field names come back under `unknown_fields`. When `already_classified_bucket` overrides the classifier, the packet includes `override: {from, to}`. The packet also carries a `task_id` (pass it to `finish_checklist` for learning correlation) and, when the bucket came from a prior correction of the same prompt, a `memory: {from, to, ...}` key.
- `discover_context` — direct-keyword Obsidian candidates from `Projects/<repo>/`, `Knowledge/`, and `Organization/`; accepts either a repo slug or absolute checkout path and returns paths/reasons/snippets only when requested. Optional `inline_top_n` and `inline_max_chars` inline the top candidates' note bodies to avoid duplicate reads. Repo-slug tokens select roots and boost note paths but are excluded from relevance scoring.
- `finish_checklist` — verification/docs/note/memory/skill-maintenance checklist from bucket and changed files. Optional `repo_root` + `auto_detect_changes` asks git for changed/untracked paths instead of trusting caller-supplied `changed_files`. Phase 2 fields include required checks, missing verification/docs/notes/skill actions, `unsafe_to_finalize`, subagent/side-effect review reminders, and final-response requirements. Optional `task_id` (from the matching `start_task` packet) lets start/finish bucket disagreement feed classification learning; without it a repo/time heuristic is used.

`classify_task` and `suggest_delegation` remain importable from `workflow_core` but are no longer exposed as MCP tools (near-zero call volume; `start_task` subsumes both). Unknown bucket names return a structured `{"error": "unknown_bucket", "closest": ...}` result instead of a protocol error. `validate_surfaces` was removed entirely; the service runs straight from this repo, so there is no install copy to drift.

## Telemetry

The MCP wrapper records privacy-safe per-call JSONL metrics to:

```text
~/Projects/workflow-mcp/logs/calls.jsonl
```

Raw prompts are not logged. Events include prompt hash/word count, tool name, success, duration, service version, bucket/confidence where present, escalation flag count, candidate/checklist/task counts, selected fields, classifier overrides (`override_from`/`override_to`), memory hits (`memory_from`/`memory_to`), `task_id`/`correlation`, and error type. `/health` includes uptime, request count, error count, last error type, process-cached service version, current source version, and metrics path.

Analyze telemetry manually:

```bash
~/.hermes/hermes-agent/venv/bin/python ~/Projects/workflow-mcp/analyze_metrics.py --stdout
```

## Learning from history

`learning.py` wraps `start_task`/`finish_checklist` and appends feedback
events to `~/Projects/workflow-mcp/logs/feedback.jsonl` — the one deliberate
exception to the hash-only telemetry rule: raw prompts persist there, but only
on feedback events (explicit override, start/finish bucket disagreement,
ambiguous or low-confidence classification), local-only and gitignored.

Two learning tiers:

- **Exact-match memory (automatic).** The feedback file is folded into an
  in-memory overlay keyed by normalized-token signature. A prompt previously
  corrected via `already_classified_bucket` is re-delivered with the corrected
  bucket immediately; start/finish disagreements need 2 consistent occurrences.
  If the two most recent corrections for a prompt disagree, the entry is
  dropped (self-healing). Memory hits are visible (`memory` in the packet,
  `memory_from`/`memory_to` in telemetry) and never downgrade safety —
  escalation flags and obsidian/reasoning-guard requirements stay
  prompt-derived. To prune a bad memory, delete its line from
  `feedback.jsonl`.
- **Generalized tuning (human-reviewed).** `tune_report.py` mines feedback +
  telemetry into a Markdown report: override/ambiguity rates per bucket,
  overlay status, keyword candidates, score diagnostics, and ready-to-paste
  golden cases. It never edits code or evals.

```bash
~/.hermes/hermes-agent/venv/bin/python ~/Projects/workflow-mcp/tune_report.py            # stdout
~/.hermes/hermes-agent/venv/bin/python ~/Projects/workflow-mcp/tune_report.py --output ~/Obsidian/Work/Daily/Lint/$(date +%F)-workflow-mcp-tuning.md
```

Scheduled telemetry report:

- Task: `workflow-mcp-analyzer`
- Label: `com.filipp.hermes-workflow-mcp-analyzer`
- Normal schedule: Monday 10:30 local time.
- Catch-up: `StartInterval=900` with script state guard, so same-day missed runs retry without backfilling prior Mondays.
- Output: `~/Obsidian/Work/Daily/Lint/YYYY-MM-DD-workflow-mcp-metrics.md`
- Dry-run: `HERMES_WORKFLOW_MCP_ANALYZER_DRY_RUN=1 ~/.hermes/scheduled-tasks/workflow-mcp-analyzer/run.sh`
- Force: `HERMES_WORKFLOW_MCP_ANALYZER_FORCE=1 ~/.hermes/scheduled-tasks/workflow-mcp-analyzer/run.sh`

## Eval corpus

Seed evals live under `evals/`:

- `evals/golden.jsonl` — classification and reasoning-guard adversarial cases.
- `evals/context.jsonl` — fixture-vault context relevance cases.

Run:

```bash
~/.hermes/hermes-agent/venv/bin/python ~/Projects/workflow-mcp/eval.py
```

## Local verification

Run from the repo checkout:

```bash
PY=~/.hermes/hermes-agent/venv/bin/python
cd ~/Projects/workflow-mcp
$PY -m pytest tests -q
$PY -m py_compile *.py
$PY eval.py
$PY smoke.py
zsh -n run.sh
plutil -lint com.filipp.hermes-workflow-mcp.plist
```

## Deploy

launchd runs the server directly from this checkout — there is no separate
install step. After changing service code:

```bash
launchctl kickstart -k "gui/$(id -u)/com.filipp.hermes-workflow-mcp"
curl -fsS http://127.0.0.1:8813/health   # expect tools:3 and this repo's HEAD sha
```

First-time launchd install:

```bash
mkdir -p ~/Projects/workflow-mcp/logs
cp ~/Projects/workflow-mcp/com.filipp.hermes-workflow-mcp.plist \
  ~/Library/LaunchAgents/com.filipp.hermes-workflow-mcp.plist
plutil -lint ~/Library/LaunchAgents/com.filipp.hermes-workflow-mcp.plist
launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/com.filipp.hermes-workflow-mcp.plist
launchctl print "gui/$(id -u)/com.filipp.hermes-workflow-mcp" | sed -n '1,120p'
curl -fsS http://127.0.0.1:8813/health
codex mcp list
codex mcp get workflow
```

If iterating on an already-loaded label:

```bash
launchctl bootout "gui/$(id -u)/com.filipp.hermes-workflow-mcp" || true
launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/com.filipp.hermes-workflow-mcp.plist
```

## Rollback

```bash
launchctl bootout "gui/$(id -u)/com.filipp.hermes-workflow-mcp" || true
rm -f ~/Library/LaunchAgents/com.filipp.hermes-workflow-mcp.plist
# Remove or disable mcp_servers.workflow from ~/.codex/config.toml if needed.
codex mcp list
```

If hooks are later modified to call this service, keep their fallback static and verify they still emit valid `{"context": "..."}` JSON when this service is down.
