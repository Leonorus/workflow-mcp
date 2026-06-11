# workflow-mcp

Local read-only MCP service (streamable HTTP, `http://127.0.0.1:8813/mcp`) that
turns the codex-workflow policy into structured helpers: `start_task`,
`discover_context`, `finish_checklist`. Pure stdlib + MCP SDK; no writes to
Obsidian, git, or launchd.

## Layout

- `workflow_core.py` — all policy logic (classifier, contracts, context
  discovery, delegation, finish checklist). Deterministic, no I/O beyond
  vault reads and git change detection.
- `server.py` — FastMCP HTTP wrapper, one thin tool per core function.
- `metrics.py` — privacy-safe JSONL telemetry (prompt hashes only, never raw
  prompts); `/health` stats.
- `analyze_metrics.py` — weekly telemetry report (run by the
  `workflow-mcp-analyzer` task in hermes-config).
- `evals/` — golden classification cases and fixture-vault context cases.
  Mine `override_from`/`override_to` telemetry into `evals/golden.jsonl`.

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
- Telemetry stays privacy-safe: never log raw prompts or note contents.
- Conventional commit subjects, no Jira prefix (personal repo).
