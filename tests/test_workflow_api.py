"""Integration tests: Agent Framework workflow (with scripted model failures) and the HTTP API."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from matchpulse.api import app
from matchpulse.synth import generate_match
from matchpulse.workflow import MatchRequest, run_pipeline
from matchpulse.writer import LLMWriter, TemplateWriter
from tests.fakes import ScriptedClient, always_hallucinate, faithful, hallucinate_then_fix, invalid_json, raising


def _run(writer, **kw):
    events, meta, _ = generate_match("high_press", 4)
    return asyncio.run(run_pipeline(MatchRequest(events=events, meta=meta, **kw), writer))


def agents(out):
    return [t["agent"] for t in out["trace"]]


def test_template_pipeline_runs_four_agents():
    out = _run(TemplateWriter())
    assert out["status"] == "ok"
    assert agents(out) == ["Event Analytics Agent", "Tactical Insight Agent",
                           "Narrative & Personalization Agent", "Evidence & Quality Reviewer"]
    assert out["orchestration"]["framework"].startswith("Microsoft Agent Framework")
    invoked = [e["executor"] for e in out["orchestration"]["framework_events"] if e["type"] == "executor_invoked"]
    assert invoked == ["event_analytics_agent", "tactical_insight_agent", "narrative_agent", "evidence_quality_reviewer"]
    assert set(out["narratives"]) == {"casual", "analyst", "broadcast"}


def test_faithful_model_output_is_approved():
    out = _run(LLMWriter(ScriptedClient(faithful), "fake"))
    assert out["fallbacks"] == [] and out["regeneration_attempts"] == 0
    assert all(n["source"] == "fake" for n in out["narratives"].values())


def test_hallucination_triggers_bounded_regeneration():
    out = _run(LLMWriter(ScriptedClient(hallucinate_then_fix), "fake"))
    assert out["regeneration_attempts"] == 1
    assert any(h["status"] == "rejected" for h in out["review_history"])
    assert all(r["status"] == "approved" for r in out["reviews"].values())
    assert agents(out).count("Evidence & Quality Reviewer") == 2


def test_persistent_hallucination_falls_back_to_template():
    out = _run(LLMWriter(ScriptedClient(always_hallucinate), "fake"))
    assert sorted(out["fallbacks"]) == ["analyst", "broadcast", "casual"]
    assert all(n["source"] == "template" for n in out["narratives"].values())
    assert all(r["status"] == "approved" for r in out["reviews"].values())


@pytest.mark.parametrize("behaviour", [invalid_json, raising])
def test_invalid_output_or_service_down_degrades_safely(behaviour):
    out = _run(LLMWriter(ScriptedClient(behaviour), "fake"))
    assert out["status"] == "ok"
    assert set(out["writer_errors"]) == {"casual", "analyst", "broadcast"}
    assert all(n["source"] == "template" for n in out["narratives"].values())


def test_model_timeout_degrades_safely():
    out = _run(LLMWriter(ScriptedClient(faithful, delay=1.0), "slow", timeout_s=0.2))
    assert "timed out" in next(iter(out["writer_errors"].values()))


def test_invalid_data_returns_error_result_not_exception():
    out = asyncio.run(run_pipeline(MatchRequest(rows=[{"match_id": "x"}]), TemplateWriter()))
    assert out["status"] == "error" and out["trace"][0]["status"] == "failed"


# --- API ---------------------------------------------------------------------------------
client = TestClient(app)


def test_health_and_scenarios():
    h = client.get("/api/health").json()
    assert h["status"] == "ok" and h["synthetic_data_only"] is True
    assert len(client.get("/api/scenarios").json()["scenarios"]) == 6


def test_analyze_default_demo_without_api_key():
    r = client.post("/api/analyze", json={"scenario": "momentum_swing", "seed": 7, "lang": "id"})
    d = r.json()
    assert r.status_code == 200 and d["status"] == "ok" and d["lang"] == "id"
    assert d["detection_check"]["fn"] == []
    assert "SYNTHETIC" in d["disclaimer"]


def test_analyze_fault_injection_mode_shows_rejections():
    d = client.post("/api/analyze", json={"scenario": "late_comeback", "seed": 5, "writer": "fault_injection"}).json()
    assert d["regeneration_attempts"] == 1
    assert any(h["status"] == "rejected" for h in d["review_history"])


def test_upload_roundtrip_and_bad_upload():
    csv = client.get("/api/dataset", params={"scenario": "dominance", "seed": 2}).text
    assert csv.startswith("# SYNTHETIC")
    d = client.post("/api/analyze/upload", json={"csv": csv}).json()
    assert d["status"] == "ok"
    bad = client.post("/api/analyze/upload", json={"csv": "a,b\n1,2\n"})
    assert bad.status_code == 422 and bad.json()["status"] == "error"


def test_unknown_scenario_404():
    assert client.post("/api/analyze", json={"scenario": "nope"}).status_code == 404


def test_dashboard_served():
    r = client.get("/")
    assert r.status_code == 200 and "MATCHPULSE" in r.text and "Synthetic data" in r.text
