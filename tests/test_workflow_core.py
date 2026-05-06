from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import workflow_core as core  # noqa: E402


def test_every_bucket_has_contract_and_delegate_enum_names_match():
    assert set(core.BUCKETS) == set(core.BUCKET_CONTRACTS)
    assert core.DELEGATE_TASK_BUCKETS == set(core.BUCKETS)
    assert "repo_maintenance" in core.DELEGATE_TASK_BUCKETS
    assert "repo-maintenance" not in core.DELEGATE_TASK_BUCKETS


def test_contract_defaults_for_trivia_ambiguous_heavy_debug():
    assert "Never delegate" in core.BUCKET_CONTRACTS["trivia"]["delegation_default"]
    assert "Do not delegate" in core.BUCKET_CONTRACTS["ambiguous"]["delegation_default"]
    assert core.BUCKET_CONTRACTS["heavy_ops"]["obsidian_required"] is True
    assert core.BUCKET_CONTRACTS["debug"]["obsidian_required"] is True


def test_classifier_real_workflow_examples():
    cases = {
        "fix failing release pipeline screenshot job": "debug",
        "compare options for workflow MCP server": "research",
        "update typo in README": "trivia",
        "add LaunchAgent for workflow MCP": "script",
        "dependency bump and CI cleanup": "repo_maintenance",
        "deploy production Kubernetes cluster migration with rollback": "heavy_ops",
    }
    for prompt, expected in cases.items():
        result = core.classify_task(prompt)
        assert result["bucket"] == expected, result
        assert result["visible_statement"].startswith("Bucket: ")


def test_start_task_packet_for_non_trivia_and_trivia(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    (vault / "Projects" / "hermes-config").mkdir(parents=True)
    (vault / "Projects" / "hermes-config" / "index.md").write_text("# hermes-config\nworkflow mcp scheduled tasks", encoding="utf-8")
    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(vault))

    packet = core.start_task("execute workflow MCP oracle implementation", repo="hermes-config")
    assert packet["bucket"] == "script"
    assert "codex-workflow" in packet["required_skills"]
    assert packet["delegation_should_be_considered"] is True
    assert packet["candidate_notes"]
    assert packet["contract"]["first_move"]

    trivia = core.start_task("what is 2+2")
    assert trivia["bucket"] == "trivia"
    assert trivia["obsidian_required"] is False
    assert trivia["delegation_should_be_considered"] is False


def test_discover_context_absent_vault_is_safe(tmp_path):
    result = core.discover_context("workflow mcp", repo="hermes-config", vault_root=str(tmp_path / "missing"))
    assert result["candidates"] == []
    assert result["warnings"]


def test_discover_context_scopes_and_caps(tmp_path):
    vault = tmp_path / "vault"
    project = vault / "Projects" / "hermes-config"
    knowledge = vault / "Knowledge"
    org = vault / "Organization"
    clipping = vault / "Clippings"
    for p in (project, knowledge, org, clipping):
        p.mkdir(parents=True)
    (project / "index.md").write_text("---\ngenerated_by: hermes-projects-index-watcher\n---\nworkflow mcp hermes-config", encoding="utf-8")
    (project / "workflow-mcp.md").write_text("Workflow MCP LaunchAgent service notes", encoding="utf-8")
    (knowledge / "launchd.md").write_text("Reusable launchd and MCP service pattern", encoding="utf-8")
    (org / "hermes.md").write_text("Hermes workflow mcp operational convention", encoding="utf-8")
    (clipping / "workflow.md").write_text("workflow mcp should not be searched by default", encoding="utf-8")

    result = core.discover_context("workflow mcp launchagent", repo="hermes-config", vault_root=str(vault), max_candidates=3)
    paths = [c["path"] for c in result["candidates"]]
    assert len(paths) == 3
    assert all(not p.startswith("Clippings/") for p in paths)
    assert any(p.startswith("Projects/hermes-config/") for p in paths)
    assert "Projects/hermes-config" in result["searched_roots"]


def test_delegation_suggestions_use_valid_buckets():
    for bucket in core.BUCKETS:
        result = core.suggest_delegation("sample prompt", bucket=bucket)
        for task in result["tasks"]:
            assert task["task_bucket"] in core.DELEGATE_TASK_BUCKETS
    debug = core.suggest_delegation("fix broken release pipeline", bucket="debug")
    assert debug["should_delegate"] is True
    assert any("failure" in t["goal"].lower() for t in debug["tasks"])
    research = core.suggest_delegation("compare workflow options", bucket="research")
    assert len(research["tasks"]) >= 2


def test_finish_checklist_rules():
    debug = core.finish_checklist(
        "debug",
        changed_files=["scheduled-tasks/workflow-mcp/run.sh", "config.yaml"],
        commands_run=["pytest"],
        findings="workflow mcp debug fix",
        repo="hermes-config",
    )
    assert debug["note_action"] == "write_raw_note"
    joined = "\n".join(debug["checklist"])
    assert "plutil" in joined
    assert "hermes config check" in joined

    research = core.finish_checklist("research", findings="workflow mcp options", repo="hermes-config")
    assert research["note_action"] == "ask_user"
    assert research["suggested_note_path"].startswith("Projects/hermes-config/")
