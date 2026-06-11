from __future__ import annotations

import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import metrics  # noqa: E402


def test_metrics_records_privacy_safe_event(tmp_path, monkeypatch):
    monkeypatch.setattr(metrics, "LOG_DIR", tmp_path)
    monkeypatch.setattr(metrics, "CALLS_PATH", tmp_path / "calls.jsonl")

    prompt = "classify secret workflow prompt"
    started = time.perf_counter()
    result = {
        "bucket": "script",
        "confidence": 0.72,
        "ambiguity": False,
        "escalation_flags": ["architecture_or_tradeoff"],
        "candidates": [{"path": "Projects/hermes-config/example.md"}],
    }
    metrics.record_call("classify_task", started, True, args={"prompt": prompt, "repo": "hermes-config"}, result=result)

    event = json.loads((tmp_path / "calls.jsonl").read_text(encoding="utf-8"))
    assert event["tool"] == "classify_task"
    assert event["success"] is True
    assert event["bucket"] == "script"
    assert event["candidate_count"] == 1
    assert event["escalation_flags_count"] == 1
    assert event["service_version"]
    assert "prompt_hash" in event
    assert prompt not in json.dumps(event)


def test_health_stats_include_version_and_counters(tmp_path, monkeypatch):
    monkeypatch.setattr(metrics, "LOG_DIR", tmp_path)
    monkeypatch.setattr(metrics, "CALLS_PATH", tmp_path / "calls.jsonl")
    before = metrics.health_stats()["request_count"]

    started = time.perf_counter()
    metrics.record_call("finish_checklist", started, False, args={}, exc=RuntimeError("boom"))
    stats = metrics.health_stats()

    assert stats["request_count"] == before + 1
    assert stats["error_count"] >= 1
    assert stats["last_error_type"] == "RuntimeError"
    assert stats["service_version"]
    assert stats["process_service_version"]
    assert stats["current_source_version"]
    assert stats["metrics_path"].endswith("calls.jsonl")


def test_record_call_logs_override(tmp_path, monkeypatch):
    monkeypatch.setattr(metrics, "LOG_DIR", tmp_path)
    monkeypatch.setattr(metrics, "CALLS_PATH", tmp_path / "calls.jsonl")

    result = {"bucket": "research", "override": {"from": "script", "to": "research"}}
    metrics.record_call("start_task", time.perf_counter(), True, args={"prompt": "x"}, result=result)

    event = json.loads((tmp_path / "calls.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert event["override_from"] == "script"
    assert event["override_to"] == "research"


def test_record_call_logs_memory_and_correlation_without_prompt(tmp_path, monkeypatch):
    monkeypatch.setattr(metrics, "LOG_DIR", tmp_path)
    monkeypatch.setattr(metrics, "CALLS_PATH", tmp_path / "calls.jsonl")

    prompt = "secret raw prompt text"
    result = {
        "bucket": "research",
        "memory": {"from": "script", "to": "research", "count": 2},
        "task_id": "abc123def456",
        "correlation": "task_id",
    }
    metrics.record_call("start_task", time.perf_counter(), True, args={"prompt": prompt}, result=result)

    event = json.loads((tmp_path / "calls.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert event["memory_from"] == "script"
    assert event["memory_to"] == "research"
    assert event["task_id"] == "abc123def456"
    assert event["correlation"] == "task_id"
    assert prompt not in json.dumps(event)
