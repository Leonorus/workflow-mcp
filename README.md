# Workflow MCP Oracle

Read-only local MCP service that turns Filipp's `codex-workflow` policy into structured workflow helpers for Codex.

The canonical policy remains the `codex-workflow` skill. This service only operationalizes stable policy data: bucket classification, bucket contracts, direct Obsidian context candidates, delegation suggestions, and finish checklists. V1 deliberately performs no writes to Obsidian, Codex config, git, or launchd.

## Endpoint and launchd

- Label: `com.filipp.hermes-workflow-mcp`
- Source path: `~/.hermes/scheduled-tasks/workflow-mcp/`
- Mirror path: `~/src/hermes-config/scheduled-tasks/workflow-mcp/`
- MCP endpoint: `http://127.0.0.1:8813/mcp`
- Health endpoint: `http://127.0.0.1:8813/health`
- Logs: `~/.hermes/scheduled-tasks/workflow-mcp/logs/`
- Python: `~/.hermes/hermes-agent/venv/bin/python` because system Python does not include the MCP SDK.

Codex config shape:

```toml
[mcp_servers.workflow]
url = "http://127.0.0.1:8813/mcp"
```

Fresh Codex sessions may need restart to see newly registered MCP tools.

## MCP tools

V2 exposes three MCP tools over nine workflow buckets (`trivia`, `light_ops`, `heavy_ops`, `app_code`, `script`, `debug`, `research`, `repo_maintenance`, `ambiguous`):

- `start_task` — full start packet: bucket, visible statement, Codex skill names, Obsidian requirement, context candidates, delegation hint, contract, finish checklist, `first_move`, `must_not_do_before`, `risk_axes`, `required_evidence`, `bucket_decision`, `reasoning_guard`, and `finish_requirements`. Optional `fields` returns only requested top-level fields; unknown field names come back under `unknown_fields`. When `already_classified_bucket` overrides the classifier, the packet includes `override: {from, to}`.
- `discover_context` — direct-keyword Obsidian candidates from `Projects/<repo>/`, `Knowledge/`, and `Organization/`; accepts either a repo slug or absolute checkout path and returns paths/reasons/snippets only when requested. Optional `inline_top_n` and `inline_max_chars` inline the top candidates' note bodies to avoid duplicate reads. Repo-slug tokens select roots and boost note paths but are excluded from relevance scoring.
- `finish_checklist` — verification/docs/note/memory/skill-maintenance checklist from bucket and changed files. Optional `repo_root` + `auto_detect_changes` asks git for changed/untracked paths instead of trusting caller-supplied `changed_files`. Phase 2 fields include required checks, missing verification/docs/notes/skill actions, `unsafe_to_finalize`, subagent/side-effect review reminders, and final-response requirements.

`classify_task` and `suggest_delegation` remain importable from `workflow_core` but are no longer exposed as MCP tools (near-zero call volume; `start_task` subsumes both). Unknown bucket names return a structured `{"error": "unknown_bucket", "closest": ...}` result instead of a protocol error. `validate_surfaces` was removed entirely; compare live vs mirror manually (`diff -r`/`cmp`) when needed.

## Telemetry

The MCP wrapper records privacy-safe per-call JSONL metrics to:

```text
~/.hermes/scheduled-tasks/workflow-mcp/logs/calls.jsonl
```

Raw prompts are not logged. Events include prompt hash/word count, tool name, success, duration, service version, bucket/confidence where present, escalation flag count, candidate/checklist/task counts, selected fields, classifier overrides (`override_from`/`override_to` — mine these into `evals/golden.jsonl`), and error type. `/health` includes uptime, request count, error count, last error type, process-cached service version, current source version, and metrics path.

Analyze telemetry manually:

```bash
~/.hermes/hermes-agent/venv/bin/python ~/.hermes/scheduled-tasks/workflow-mcp/analyze_metrics.py --stdout
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
~/.hermes/hermes-agent/venv/bin/python ~/.hermes/scheduled-tasks/workflow-mcp/eval.py
```

## Local verification

```bash
PY=~/.hermes/hermes-agent/venv/bin/python
$PY -m pytest ~/.hermes/scheduled-tasks/workflow-mcp/tests -q
$PY -m py_compile ~/.hermes/scheduled-tasks/workflow-mcp/*.py
$PY ~/.hermes/scheduled-tasks/workflow-mcp/eval.py
zsh -n ~/.hermes/scheduled-tasks/workflow-mcp/run.sh
zsh -n ~/.hermes/scheduled-tasks/workflow-mcp-analyzer/run.sh
plutil -lint ~/.hermes/scheduled-tasks/workflow-mcp/com.filipp.hermes-workflow-mcp.plist
plutil -lint ~/.hermes/scheduled-tasks/workflow-mcp-analyzer/com.filipp.hermes-workflow-mcp-analyzer.plist
$PY ~/.hermes/scheduled-tasks/workflow-mcp/analyze_metrics.py --stdout
$PY ~/.hermes/scheduled-tasks/workflow-mcp/smoke.py
```

After installing/loading launchd:

```bash
mkdir -p ~/.hermes/scheduled-tasks/workflow-mcp/logs
cp ~/.hermes/scheduled-tasks/workflow-mcp/com.filipp.hermes-workflow-mcp.plist \
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
