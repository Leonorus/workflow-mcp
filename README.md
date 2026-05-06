# Workflow MCP Oracle

Read-only local MCP service that turns Filipp's `codex-workflow` policy into structured workflow helpers for Hermes.

The canonical policy remains the `codex-workflow` skill. This service only operationalizes stable policy data: bucket classification, bucket contracts, direct Obsidian context candidates, delegation suggestions, and finish checklists. V1 deliberately performs no writes to Obsidian, Hermes config, git, or launchd.

## Endpoint and launchd

- Label: `com.filipp.hermes-workflow-mcp`
- Source path: `~/.hermes/scheduled-tasks/workflow-mcp/`
- Mirror path: `~/src/hermes-config/scheduled-tasks/workflow-mcp/`
- MCP endpoint: `http://127.0.0.1:8813/mcp`
- Health endpoint: `http://127.0.0.1:8813/health`
- Logs: `~/.hermes/scheduled-tasks/workflow-mcp/logs/`
- Python: `~/.hermes/hermes-agent/venv/bin/python` because system Python does not include the MCP SDK.

Hermes config shape:

```yaml
mcp_servers:
  workflow:
    url: http://127.0.0.1:8813/mcp
    connect_timeout: 30
    timeout: 60
```

Fresh Hermes sessions need `/reload-mcp` or restart to see newly registered tools.

## MCP tools

V1 exposes exactly five tools:

- `start_task` — full start packet: bucket, visible statement, skills, Obsidian requirement, context candidates, delegation hint, contract, and finish checklist.
- `classify_task` — small classification result with confidence, ambiguity, why, and escalation flags.
- `discover_context` — direct-keyword Obsidian candidates from `Projects/<repo>/`, `Knowledge/`, and `Organization/`; returns paths/reasons/snippets only when requested.
- `suggest_delegation` — concrete `delegate_task` workstreams with valid `task_bucket` enum names.
- `finish_checklist` — verification/docs/note/memory/skill-maintenance checklist from bucket and changed files.

## Local verification

```bash
PY=~/.hermes/hermes-agent/venv/bin/python
$PY -m pytest ~/.hermes/scheduled-tasks/workflow-mcp/tests -q
$PY -m py_compile ~/.hermes/scheduled-tasks/workflow-mcp/*.py
zsh -n ~/.hermes/scheduled-tasks/workflow-mcp/run.sh
plutil -lint ~/.hermes/scheduled-tasks/workflow-mcp/com.filipp.hermes-workflow-mcp.plist
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
hermes config check
hermes mcp list
hermes mcp test workflow
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
# Remove or disable mcp_servers.workflow from ~/.hermes/config.yaml if needed.
hermes config check
```

If hooks are later modified to call this service, keep their fallback static and verify they still emit valid `{"context": "..."}` JSON when this service is down.
