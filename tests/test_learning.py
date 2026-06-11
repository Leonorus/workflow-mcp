from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import learning  # noqa: E402
import tune_report  # noqa: E402


def _use_tmp_feedback(tmp_path, monkeypatch):
    feedback = tmp_path / "feedback.jsonl"
    monkeypatch.setattr(learning, "FEEDBACK_PATH", feedback)
    monkeypatch.setattr(learning, "LOG_DIR", tmp_path)
    monkeypatch.setattr(learning, "_MEMORY_CACHE", {"file_signature": None, "state": None})
    return feedback


def _correction(prompt, corrected, signal="explicit_override", ts="2026-06-12T10:00:00+00:00"):
    return {
        "ts": ts,
        "signal": signal,
        "prompt": prompt,
        "signature": learning.prompt_signature(prompt),
        "predicted_bucket": "script",
        "delivered_bucket": corrected if signal == "explicit_override" else "script",
        "delivered_source": "override" if signal == "explicit_override" else "classifier",
        "corrected_bucket": corrected,
        "confidence": 0.7,
        "ambiguity": False,
        "repo": "workflow-mcp",
        "task_id": "t1",
        "correlation": None,
        "service_version": "test",
    }


def test_prompt_signature_normalizes_order_and_punctuation():
    a = learning.prompt_signature("Restart the workflow MCP service!")
    b = learning.prompt_signature("service restart... workflow (MCP)")
    assert a is not None
    assert a == b


def test_prompt_signature_empty_or_stopword_only_is_none():
    assert learning.prompt_signature("") is None
    assert learning.prompt_signature("   ") is None
    assert learning.prompt_signature("the and for") is None
    assert learning.prompt_signature(None) is None


def test_fold_explicit_override_creates_entry(tmp_path, monkeypatch):
    feedback = _use_tmp_feedback(tmp_path, monkeypatch)
    prompt = "analyze workflow mcp server options"
    learning.record_feedback(_correction(prompt, "research"))
    state = learning.fold_feedback(feedback)
    sig = learning.prompt_signature(prompt)
    assert state["overlay"][sig]["bucket"] == "research"
    assert state["overlay"][sig]["count"] == 1
    assert "explicit_override" in state["overlay"][sig]["signals"]


def test_fold_latest_wins_when_consistent_tail(tmp_path, monkeypatch):
    feedback = _use_tmp_feedback(tmp_path, monkeypatch)
    prompt = "tune launchagent for backups"
    learning.record_feedback(_correction(prompt, "script", ts="2026-06-01T00:00:00+00:00"))
    learning.record_feedback(_correction(prompt, "script", ts="2026-06-02T00:00:00+00:00"))
    state = learning.fold_feedback(feedback)
    sig = learning.prompt_signature(prompt)
    assert state["overlay"][sig]["bucket"] == "script"
    assert state["overlay"][sig]["count"] == 2


def test_fold_conflicting_recent_corrections_drop_entry(tmp_path, monkeypatch):
    feedback = _use_tmp_feedback(tmp_path, monkeypatch)
    prompt = "update deployment manifests for staging"
    learning.record_feedback(_correction(prompt, "light_ops", ts="2026-06-01T00:00:00+00:00"))
    learning.record_feedback(_correction(prompt, "heavy_ops", ts="2026-06-02T00:00:00+00:00"))
    state = learning.fold_feedback(feedback)
    sig = learning.prompt_signature(prompt)
    assert sig not in state["overlay"]
    assert state["conflicts"][sig]["buckets"] == ["light_ops", "heavy_ops"]


def test_fold_finish_disagreement_needs_two_consistent(tmp_path, monkeypatch):
    feedback = _use_tmp_feedback(tmp_path, monkeypatch)
    prompt = "investigate failing pipeline screenshot job"
    learning.record_feedback(_correction(prompt, "debug", signal="finish_disagreement"))
    state = learning.fold_feedback(feedback)
    sig = learning.prompt_signature(prompt)
    assert sig not in state["overlay"]
    assert state["below_threshold"][sig]["bucket"] == "debug"

    learning.record_feedback(_correction(prompt, "debug", signal="finish_disagreement", ts="2026-06-13T00:00:00+00:00"))
    state = learning.fold_feedback(feedback)
    assert state["overlay"][sig]["bucket"] == "debug"
    assert state["overlay"][sig]["count"] == 2


def test_fold_ignores_ambiguous_and_invalid_corrections(tmp_path, monkeypatch):
    feedback = _use_tmp_feedback(tmp_path, monkeypatch)
    learning.record_feedback(_correction("vague prompt words", "ambiguous"))
    learning.record_feedback(_correction("another vague prompt", "not_a_bucket"))
    state = learning.fold_feedback(feedback)
    assert state["overlay"] == {}
    assert state["conflicts"] == {}


def test_memory_lookup_and_cache_invalidation(tmp_path, monkeypatch):
    _use_tmp_feedback(tmp_path, monkeypatch)
    prompt = "compare options for workflow mcp memory overlay"
    assert learning.memory_lookup(prompt) is None
    learning.record_feedback(_correction(prompt, "research"))
    hit = learning.memory_lookup(prompt)
    assert hit is not None
    assert hit["bucket"] == "research"

    learning.record_feedback(_correction(prompt, "app_code", ts="2026-06-13T00:00:00+00:00"))
    assert learning.memory_lookup(prompt) is None  # conflict dropped after re-fold


def test_record_feedback_tolerates_unwritable_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(learning, "FEEDBACK_PATH", Path("/nonexistent-root/feedback.jsonl"))
    learning.record_feedback({"signal": "explicit_override"})  # must not raise


def test_feedback_file_is_raw_jsonl(tmp_path, monkeypatch):
    feedback = _use_tmp_feedback(tmp_path, monkeypatch)
    prompt = "fix failing release pipeline screenshot job"
    learning.record_feedback(_correction(prompt, "debug"))
    event = json.loads(feedback.read_text(encoding="utf-8"))
    assert event["prompt"] == prompt
    assert event["corrected_bucket"] == "debug"


def _use_clean_state(tmp_path, monkeypatch):
    feedback = _use_tmp_feedback(tmp_path, monkeypatch)
    monkeypatch.setattr(learning, "_PENDING", {})
    vault = tmp_path / "vault"
    (vault / "Projects" / "workflow-mcp").mkdir(parents=True)
    monkeypatch.setenv("OBSIDIAN_VAULT_PATH", str(vault))
    return feedback


def _read_events(feedback):
    if not feedback.exists():
        return []
    return [json.loads(line) for line in feedback.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_start_wrapper_records_explicit_override_and_memory_applies(tmp_path, monkeypatch):
    feedback = _use_clean_state(tmp_path, monkeypatch)
    prompt = "compare runtime tradeoffs of workflow MCP transport layers"

    first = learning.start_task_with_learning(prompt, repo="workflow-mcp", already_classified_bucket="app_code")
    assert first["bucket"] == "app_code"
    events = _read_events(feedback)
    assert len(events) == 1
    assert events[0]["signal"] == "explicit_override"
    assert events[0]["corrected_bucket"] == "app_code"
    assert events[0]["prompt"] == prompt

    second = learning.start_task_with_learning(prompt, repo="workflow-mcp")
    assert second["bucket"] == "app_code"
    assert second["memory"]["to"] == "app_code"
    assert "memory match" in second["visible_statement"]
    assert "task_id" in second


def test_start_wrapper_memory_preserves_prompt_derived_risk(tmp_path, monkeypatch):
    feedback = _use_clean_state(tmp_path, monkeypatch)
    prompt = "plan production cluster terraform migration with rollback for workflow service"
    learning.start_task_with_learning(prompt, repo="workflow-mcp", already_classified_bucket="light_ops")
    assert _read_events(feedback)[0]["signal"] == "explicit_override"

    packet = learning.start_task_with_learning(prompt, repo="workflow-mcp")
    assert packet["bucket"] == "light_ops"
    assert packet["memory"]["to"] == "light_ops"
    assert packet["obsidian_required"] is True
    assert packet["reasoning_guard_required"] is True


def test_start_wrapper_override_beats_memory(tmp_path, monkeypatch):
    _use_clean_state(tmp_path, monkeypatch)
    prompt = "compare runtime tradeoffs of workflow MCP transport layers"
    learning.start_task_with_learning(prompt, repo="workflow-mcp", already_classified_bucket="app_code")
    packet = learning.start_task_with_learning(prompt, repo="workflow-mcp", already_classified_bucket="research")
    assert packet["bucket"] == "research"
    assert packet["override"]["to"] == "research"
    assert "memory" not in packet


def test_start_wrapper_records_ambiguous_and_low_confidence(tmp_path, monkeypatch):
    feedback = _use_clean_state(tmp_path, monkeypatch)
    learning.start_task_with_learning("make it better", repo="workflow-mcp")
    events = _read_events(feedback)
    assert len(events) == 1
    assert events[0]["signal"] == "ambiguous"
    assert events[0]["corrected_bucket"] is None


def test_start_wrapper_confident_clean_call_writes_nothing(tmp_path, monkeypatch):
    feedback = _use_clean_state(tmp_path, monkeypatch)
    result = learning.start_task_with_learning(
        "deploy production Kubernetes cluster migration with rollback", repo="workflow-mcp"
    )
    assert result["bucket"] == "heavy_ops"
    assert _read_events(feedback) == []


def test_start_wrapper_bucket_error_passthrough(tmp_path, monkeypatch):
    feedback = _use_clean_state(tmp_path, monkeypatch)
    result = learning.start_task_with_learning("update typo in README", already_classified_bucket="bogus")
    assert result["error"] == "unknown_bucket"
    assert _read_events(feedback) == []


def test_finish_wrapper_task_id_roundtrip_records_disagreement(tmp_path, monkeypatch):
    feedback = _use_clean_state(tmp_path, monkeypatch)
    prompt = "deploy production Kubernetes cluster migration with rollback"
    packet = learning.start_task_with_learning(prompt, repo="workflow-mcp")
    assert packet["bucket"] == "heavy_ops"

    result = learning.finish_checklist_with_learning(bucket="debug", repo="workflow-mcp", task_id=packet["task_id"])
    assert result["task_id"] == packet["task_id"]
    assert result["correlation"] == "task_id"
    events = _read_events(feedback)
    assert len(events) == 1
    assert events[0]["signal"] == "finish_disagreement"
    assert events[0]["delivered_bucket"] == "heavy_ops"
    assert events[0]["corrected_bucket"] == "debug"
    assert events[0]["prompt"] == prompt


def test_finish_wrapper_agreement_writes_nothing(tmp_path, monkeypatch):
    feedback = _use_clean_state(tmp_path, monkeypatch)
    prompt = "deploy production Kubernetes cluster migration with rollback"
    packet = learning.start_task_with_learning(prompt, repo="workflow-mcp")
    result = learning.finish_checklist_with_learning(bucket="heavy_ops", repo="workflow-mcp", task_id=packet["task_id"])
    assert result["correlation"] == "task_id"
    assert _read_events(feedback) == []


def test_finish_wrapper_heuristic_repo_fallback(tmp_path, monkeypatch):
    feedback = _use_clean_state(tmp_path, monkeypatch)
    prompt = "deploy production Kubernetes cluster migration with rollback"
    learning.start_task_with_learning(prompt, repo="workflow-mcp")
    result = learning.finish_checklist_with_learning(bucket="debug", repo="workflow-mcp")
    assert result["correlation"] == "heuristic"
    events = _read_events(feedback)
    assert len(events) == 1
    assert events[0]["correlation"] == "heuristic"


def test_finish_wrapper_no_pending_no_event(tmp_path, monkeypatch):
    feedback = _use_clean_state(tmp_path, monkeypatch)
    result = learning.finish_checklist_with_learning(bucket="debug", repo="workflow-mcp")
    assert result["correlation"] is None
    assert result["task_id"] is None
    assert _read_events(feedback) == []


def test_finish_wrapper_stale_pending_not_matched(tmp_path, monkeypatch):
    feedback = _use_clean_state(tmp_path, monkeypatch)
    prompt = "deploy production Kubernetes cluster migration with rollback"
    learning.start_task_with_learning(prompt, repo="workflow-mcp")
    for entry in learning._PENDING.values():
        entry["ts"] -= learning.HEURISTIC_REPO_WINDOW_SECONDS + 60
    result = learning.finish_checklist_with_learning(bucket="debug", repo="workflow-mcp")
    assert result["correlation"] is None
    assert _read_events(feedback) == []


def test_tune_report_proposes_keyword_and_golden_case(tmp_path, monkeypatch):
    feedback = _use_tmp_feedback(tmp_path, monkeypatch)
    # 'kustomize' is not an existing heavy_ops keyword and appears in 2 corrected prompts
    learning.record_feedback(_correction("roll out kustomize overlays to the fleet", "heavy_ops"))
    learning.record_feedback(_correction("refactor kustomize bases for new env", "heavy_ops", ts="2026-06-13T00:00:00+00:00"))
    calls = tmp_path / "calls.jsonl"
    calls.write_text(
        json.dumps({"tool": "start_task", "bucket": "script", "override_from": "script", "override_to": "heavy_ops", "confidence": 0.7})
        + "\n",
        encoding="utf-8",
    )
    golden = tmp_path / "golden.jsonl"
    golden.write_text(
        json.dumps({"case_id": "x", "prompt": "update typo in README", "bucket_primary": "trivia", "bucket_acceptable": ["trivia"]})
        + "\n",
        encoding="utf-8",
    )

    report = tune_report.build_report(feedback, calls, golden)
    assert "kustomize" in report
    assert '"case_id": "mined-' in report
    assert '"bucket_primary": "heavy_ops"' in report
    assert "script->heavy_ops" in report


def test_tune_report_filters_existing_keywords_and_dedupes_golden(tmp_path, monkeypatch):
    feedback = _use_tmp_feedback(tmp_path, monkeypatch)
    # 'terraform' is already a heavy_ops keyword -> must not be proposed
    prompt = "terraform module cleanup pass one"
    learning.record_feedback(_correction(prompt, "heavy_ops"))
    learning.record_feedback(_correction("terraform module cleanup pass two", "heavy_ops", ts="2026-06-13T00:00:00+00:00"))
    golden = tmp_path / "golden.jsonl"
    golden.write_text(
        json.dumps({"case_id": "existing", "prompt": prompt, "bucket_primary": "heavy_ops", "bucket_acceptable": ["heavy_ops"]}) + "\n",
        encoding="utf-8",
    )

    report = tune_report.build_report(feedback, tmp_path / "missing-calls.jsonl", golden)
    keyword_section = report.split("## Keyword candidates")[1].split("## Score diagnostics")[0]
    assert "terraform" not in keyword_section
    golden_section = report.split("## Proposed golden cases")[1]
    assert prompt not in golden_section


def test_tune_report_runs_on_hash_only_calls(tmp_path, monkeypatch):
    _use_tmp_feedback(tmp_path, monkeypatch)
    calls = tmp_path / "calls.jsonl"
    calls.write_text(
        json.dumps({"tool": "start_task", "bucket": "debug", "prompt_hash": "abc", "confidence": 0.9}) + "\n",
        encoding="utf-8",
    )
    report = tune_report.build_report(tmp_path / "feedback.jsonl", calls, tmp_path / "missing-golden.jsonl")
    assert "Workflow MCP tuning report" in report
    assert "`debug` | 1" in report


def test_finish_disagreement_evicts_bad_memory_then_relearns(tmp_path, monkeypatch):
    feedback = _use_clean_state(tmp_path, monkeypatch)
    prompt = "compare runtime tradeoffs of workflow MCP transport layers"
    learning.start_task_with_learning(prompt, repo="workflow-mcp", already_classified_bucket="app_code")

    packet = learning.start_task_with_learning(prompt, repo="workflow-mcp")
    assert packet["memory"]["to"] == "app_code"
    learning.finish_checklist_with_learning(bucket="research", repo="workflow-mcp", task_id=packet["task_id"])

    # conflict between app_code and research corrections drops the memory
    packet = learning.start_task_with_learning(prompt, repo="workflow-mcp")
    assert "memory" not in packet
    events = _read_events(feedback)
    assert events[-1]["signal"] == "finish_disagreement"
