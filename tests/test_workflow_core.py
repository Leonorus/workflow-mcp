from __future__ import annotations

from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import workflow_core as core  # noqa: E402


def test_every_bucket_has_contract_and_delegate_enum_names_match():
    assert set(core.BUCKETS) == set(core.BUCKET_CONTRACTS)
    assert set(core.BUCKETS) == set(core.BUCKET_SKILL_NAMES)
    assert core.DELEGATE_TASK_BUCKETS == set(core.BUCKETS)
    assert "repo_maintenance" in core.DELEGATE_TASK_BUCKETS
    assert "repo-maintenance" not in core.DELEGATE_TASK_BUCKETS
    assert core.BUCKET_SKILL_NAMES["debug"] == "workflow-debug-contract"


def test_contract_defaults_for_trivia_ambiguous_heavy_debug():
    assert "Never delegate" in core.BUCKET_CONTRACTS["trivia"]["delegation_default"]
    assert "Do not delegate" in core.BUCKET_CONTRACTS["ambiguous"]["delegation_default"]
    assert core.BUCKET_CONTRACTS["heavy_ops"]["obsidian_required"] is True
    assert core.BUCKET_CONTRACTS["debug"]["obsidian_required"] is True


def test_classifier_real_workflow_examples():
    cases = {
        "fix failing release pipeline screenshot job": "debug",
        "check": "debug",
        "check regression status": "debug",
        "compare options for workflow MCP server": "research",
        "update typo in README": "trivia",
        "add LaunchAgent for workflow MCP": "script",
        "dependency bump and CI cleanup": "repo_maintenance",
        "deploy production Kubernetes cluster migration with rollback": "heavy_ops",
        "add prod deploy matrix labels only in GitLab CI, no secret values and no runtime config changes": "light_ops",
        "code review only": "research",
        "repo investigation only, no edits": "research",
        "read-only investigation only": "research",
        "debug this failure read-only": "debug",
        "make it better": "ambiguous",
    }
    for prompt, expected in cases.items():
        result = core.classify_task(prompt)
        assert result["bucket"] == expected, result
        assert result["visible_statement"].startswith("Bucket: ")


def test_task_style_check_routes_to_debug_without_checklist_overmatch():
    bare = core.classify_task("check")
    assert bare["bucket"] == "debug"
    assert bare["obsidian_required"] is True
    assert bare["reasoning_guard_required"] is True
    assert "task-style check implies investigation" in bare["why"]

    state_check = core.classify_task("check status")
    assert state_check["bucket"] == "debug"

    checklist = core.classify_task("finish checklist")
    assert checklist["bucket"] != "debug"

    packet = core.start_task("check", repo="hermes-config")
    assert packet["bucket"] == "debug"
    assert "workflow-debug-contract" in packet["required_skills"]
    assert "systematic-debugging" not in packet["required_skills"]
    assert "obsidian" not in packet["required_skills"]
    assert "codex-knowledge" in packet["required_skills"]
    assert packet["reasoning_guard"]["required"] is True


def test_start_task_packet_for_non_trivia_and_trivia(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    (vault / "Projects" / "hermes-config").mkdir(parents=True)
    (vault / "Projects" / "hermes-config" / "index.md").write_text("# hermes-config\nworkflow mcp scheduled tasks", encoding="utf-8")
    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(vault))

    packet = core.start_task("execute workflow MCP oracle implementation", repo="hermes-config")
    assert packet["bucket"] == "script"
    assert "codex-workflow" in packet["required_skills"]
    assert "workflow-script-contract" in packet["required_skills"]
    assert "hermes-agent" not in packet["required_skills"]
    assert "native-mcp" not in packet["required_skills"]
    assert packet["delegation_should_be_considered"] is True
    assert packet["first_move"]
    assert packet["must_not_do_before"]
    assert packet["bucket_decision"]["selected"] == "script"
    assert packet["reasoning_guard"]["required"] is True
    assert packet["finish_requirements"]["must_call_finish_checklist"] is True
    assert packet["context_candidates"]
    assert packet["contract"]["first_move"]

    trivia = core.start_task("what is 2+2")
    assert trivia["bucket"] == "trivia"
    assert trivia["obsidian_required"] is False
    assert "workflow-trivia-contract" in trivia["required_skills"]
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
    (knowledge / "generic.md").write_text("checklist context start finish user provided", encoding="utf-8")
    (knowledge / "current-status-required-context.md").write_text("current status required context user provided", encoding="utf-8")
    (org / "hermes.md").write_text("Hermes workflow mcp operational convention", encoding="utf-8")
    (clipping / "workflow.md").write_text("workflow mcp should not be searched by default", encoding="utf-8")

    result = core.discover_context("workflow mcp launchagent", repo="hermes-config", vault_root=str(vault), max_candidates=3)
    paths = [c["path"] for c in result["candidates"]]
    assert len(paths) == 3
    assert all(not p.startswith("Clippings/") for p in paths)
    assert any(p.startswith("Projects/hermes-config/") for p in paths)
    assert "Knowledge/generic.md" not in paths
    assert result["candidates"][0]["match_class"] in {"must_read", "likely_relevant"}
    assert "Projects/hermes-config" in result["searched_roots"]

    generic = core.discover_context("current status required context", vault_root=str(vault), max_candidates=1)
    assert generic["candidates"]
    assert generic["candidates"][0]["match_class"] != "must_read"
    assert "why_not_stronger" in generic["candidates"][0]


def test_workflow_namespace_terms_are_searchable():
    tokens = core._tokens("hermes repo task code workflow mcp")
    assert {"hermes", "repo", "task", "code", "workflow", "mcp"} <= tokens


def test_discover_context_can_inline_top_candidate_content(tmp_path):
    vault = tmp_path / "vault"
    project = vault / "Projects" / "hermes-config"
    project.mkdir(parents=True)
    body = "Workflow MCP effectiveness roadmap body with enough text to truncate."
    (project / "workflow-mcp.md").write_text(body, encoding="utf-8")
    (project / "other.md").write_text("Workflow MCP secondary note", encoding="utf-8")

    result = core.discover_context(
        "workflow mcp effectiveness",
        repo="hermes-config",
        vault_root=str(vault),
        max_candidates=2,
        inline_top_n=1,
        inline_max_chars=24,
    )

    assert len(result["candidates"]) == 2
    assert result["candidates"][0]["content"] == body[:24]
    assert result["candidates"][0]["content_truncated"] is True
    assert "content" not in result["candidates"][1]

    long_body = "UniqueLongMarker " + ("x" * 13000)
    (project / "unique-long-marker.md").write_text(long_body, encoding="utf-8")
    long_result = core.discover_context(
        "unique long marker",
        repo="hermes-config",
        vault_root=str(vault),
        max_candidates=1,
        inline_top_n=1,
        inline_max_chars=12000,
    )
    assert long_result["candidates"][0]["content_chars"] == 12000
    assert long_result["candidates"][0]["content_truncated"] is True


def test_context_index_refreshes_after_file_change(tmp_path):
    vault = tmp_path / "vault"
    project = vault / "Projects" / "hermes-config"
    project.mkdir(parents=True)
    note = project / "workflow-mcp.md"
    note.write_text("Workflow MCP old topic", encoding="utf-8")

    first = core.discover_context("old topic", repo="hermes-config", vault_root=str(vault))
    assert first["candidates"]

    note.write_text("Workflow MCP new unique marker", encoding="utf-8")
    second = core.discover_context("unique marker", repo="hermes-config", vault_root=str(vault))
    assert second["candidates"]


def test_absolute_repo_paths_are_normalized_for_context_and_notes(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    project = vault / "Projects" / "hermes-config"
    project.mkdir(parents=True)
    (project / "index.md").write_text("# hermes-config\nworkflow mcp scheduled tasks", encoding="utf-8")
    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(vault))

    absolute_repo = "/Users/filipp.vysokov/src/hermes-config"
    packet = core.start_task("rewrite workflow to use Workflow MCP", repo=absolute_repo, cwd=absolute_repo)
    assert packet["context_warnings"] == []
    assert packet["context_candidates"]
    assert packet["context_candidates"][0]["path"].startswith("Projects/hermes-config/")
    assert packet["suggested_note_path"].startswith("Projects/hermes-config/")

    checklist = core.finish_checklist("script", findings="workflow mcp rewrite", repo=absolute_repo)
    assert checklist["suggested_note_path"].startswith("Projects/hermes-config/")
    assert "Projects//" not in checklist["suggested_note_path"]


def test_delegation_suggestions_use_valid_buckets():
    for bucket in core.BUCKETS:
        result = core.suggest_delegation("sample prompt", bucket=bucket)
        for task in result["tasks"]:
            assert task["task_bucket"] in core.DELEGATE_TASK_BUCKETS
    debug = core.suggest_delegation('fix broken release pipeline error "screenshot artifact missing" in .gitlab-ci.yml', bucket="debug")
    assert debug["should_delegate"] is True
    assert debug["specialization"] == "specific"
    assert ".gitlab-ci.yml" in debug["extracted_signals"]["paths"]
    assert any(".gitlab-ci.yml" in t["goal"] for t in debug["tasks"])
    assert any("screenshot artifact missing" in t["goal"] for t in debug["tasks"])
    assert all("Mutation boundary: read-only" in t["context"] for t in debug["tasks"])
    assert all("Parent will verify" in t["context"] for t in debug["tasks"])
    research = core.suggest_delegation("compare workflow options", bucket="research")
    assert len(research["tasks"]) >= 2
    light_ops = core.suggest_delegation("add prod deploy matrix labels only in GitLab CI, no secret values and no runtime config changes", bucket="light_ops")
    assert light_ops["should_delegate"] is True
    assert light_ops["read_only_delegation_authorized"] is True
    assert all("Read-only delegation is explicitly user-authorized" in t["context"] for t in light_ops["tasks"])


def test_read_only_review_and_investigation_delegate():
    prompts = [
        "code review only",
        "repo investigation only, no edits",
        "read-only investigation only",
        "investigate and review this repo without edits",
    ]
    for prompt in prompts:
        packet = core.start_task(prompt)
        assert packet["bucket"] == "research", packet
        assert packet["delegation_should_be_considered"] is True
        assert packet["delegation_hint"]["read_only_delegation_authorized"] is True
        assert all("Mutation boundary: read-only" in task["context"] for task in packet["delegation_hint"]["tasks"])

    debug_packet = core.start_task("debug this failure read-only")
    assert debug_packet["bucket"] == "debug"
    assert debug_packet["delegation_should_be_considered"] is True


def test_start_task_override_preserves_prompt_derived_risk(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    (vault / "Knowledge").mkdir(parents=True)
    (vault / "Knowledge" / "architecture.md").write_text("architecture tradeoff note", encoding="utf-8")
    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(vault))

    packet = core.start_task(
        "compare architecture tradeoffs for workflow MCP but keep this as light ops",
        repo="hermes-config",
        already_classified_bucket="light_ops",
    )

    assert packet["bucket"] == "light_ops"
    assert packet["bucket_decision"]["selected"] == "light_ops"
    assert packet["override"]["to"] == "light_ops"
    assert packet["override"]["from"] in core.BUCKETS
    assert packet["obsidian_required"] is True
    assert packet["reasoning_guard_required"] is True
    assert "workflow-light-ops-contract" in packet["required_skills"]
    assert "obsidian" not in packet["required_skills"]
    assert "codex-knowledge" in packet["required_skills"]
    assert packet["context_candidates"]


def test_start_task_can_return_selected_fields(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    (vault / "Projects" / "hermes-config").mkdir(parents=True)
    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(vault))

    packet = core.start_task(
        "compare workflow MCP options",
        repo="hermes-config",
        fields=["bucket", "visible_statement"],
    )
    assert set(packet) == {"bucket", "visible_statement"}
    assert packet["bucket"] == "research"


def test_repo_from_missing_relative_cwd_returns_none():
    assert core._repo_from_cwd("relative/missing/path") is None


def test_finish_checklist_can_detect_git_changes(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True)
    (repo / "README.md").write_text("initial", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    (repo / "scheduled-tasks").mkdir()
    (repo / "scheduled-tasks" / "run.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (repo / "config.yaml").write_text("mcp_servers: {}\n", encoding="utf-8")

    result = core.finish_checklist("script", repo="hermes-config", repo_root=str(repo), commands_run=["pytest"])
    joined = "\n".join(result["checklist"])
    assert "scheduled-tasks/run.sh" in result["changed_files_detected"]
    assert "config.yaml" in result["changed_files_detected"]
    assert result["changed_files_source"] == "git"
    assert "zsh -n" in joined
    assert "codex mcp list" in joined


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
    assert "codex mcp list" in joined
    assert debug["unsafe_to_finalize"] is True
    assert debug["missing_notes"]
    complete = core.finish_checklist(
        "script",
        changed_files=["scheduled-tasks/workflow-mcp/server.py", "scheduled-tasks/workflow-mcp/com.filipp.hermes-workflow-mcp.plist"],
        commands_run=["pytest", "py_compile", "smoke.py", "plutil -lint", "launchctl print", "cmp -s live mirror"],
        findings="workflow mcp complete",
        repo="hermes-config",
        skills_updated=["codex-workflow"],
    )
    assert complete["unsafe_to_finalize"] is False
    assert complete["required_checks"]

    research = core.finish_checklist("research", findings="workflow mcp options", repo="hermes-config")
    assert research["note_action"] == "ask_user"
    assert research["suggested_note_path"].startswith("Projects/hermes-config/")


def test_unknown_bucket_returns_structured_error():
    result = core.finish_checklist("opsy")
    assert result["error"] == "unknown_bucket"
    assert result["valid_buckets"] == list(core.BUCKETS)
    assert result["closest"] in core.BUCKETS
    packet = core.start_task("fix prod tls", already_classified_bucket="heavy-opsx")
    assert packet["error"] == "unknown_bucket"


def test_repo_slug_tokens_do_not_inflate_relevance(tmp_path):
    vault = tmp_path / "vault"
    project = vault / "Projects" / "filipp.vysokov"
    project.mkdir(parents=True)
    (project / "certbot-renewal.md").write_text("filipp vysokov certbot wildcard renewal blocked", encoding="utf-8")
    (project / "retry-logic.md").write_text("retry logic backoff improvements", encoding="utf-8")

    result = core.discover_context("improve retry logic backoff", repo="filipp.vysokov", vault_root=str(vault))
    by_path = {c["path"]: c for c in result["candidates"]}
    assert result["candidates"][0]["path"] == "Projects/filipp.vysokov/retry-logic.md"
    certbot = by_path.get("Projects/filipp.vysokov/certbot-renewal.md")
    assert certbot is None or certbot["match_class"] != "must_read"


def test_start_task_packet_has_no_duplicate_aliases():
    packet = core.start_task("execute workflow MCP oracle implementation", repo="hermes-config")
    assert "candidate_notes" not in packet
    assert "delegation" not in packet
    assert packet["context_candidates"] is not None
    assert packet["delegation_hint"]["should_delegate"] is True


def test_start_task_reports_unknown_fields():
    packet = core.start_task("compare workflow MCP options", fields=["bucket", "guards", "statement"])
    assert packet["bucket"] == "research"
    assert packet["unknown_fields"]["requested"] == ["guards", "statement"]
    assert "visible_statement" in packet["unknown_fields"]["valid"]
