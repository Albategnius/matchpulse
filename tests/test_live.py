"""Live replay: analysis up to minute N must only use information available at that minute."""

from fastapi.testclient import TestClient

from matchpulse.analytics import analyse
from matchpulse.api import app
from matchpulse.live_eval import first_flagged, live_latency
from matchpulse.narrative import MODES, build_evidence, render
from matchpulse.reviewer import review
from matchpulse.shifts import DetectorConfig, detect_shifts
from matchpulse.synth import generate_match


def test_until_min_uses_only_past_events():
    events, meta, _ = generate_match("late_comeback", 5)
    an = analyse(events, meta, 5, 60)
    assert an.live and an.until_min == 60
    assert len(an.windows) == 12
    assert all(e.timestamp_s < 3600 for e in an.events)
    assert an.score() == {"HOME": 0, "AWAY": 2}            # the comeback has not happened yet
    assert analyse(events, meta, 5, 90).score() == {"HOME": 3, "AWAY": 2}


def test_full_match_unchanged_by_live_support():
    events, meta, _ = generate_match("dominance", 7)
    a, b = analyse(events, meta), analyse(events, meta, 5, 90)
    assert a.totals == b.totals and len(a.windows) == len(b.windows) == 18 and not a.live


def test_live_does_not_peek_into_the_future():
    """Moments detected at minute N must be identical if events after N are deleted."""
    events, meta, _ = generate_match("momentum_swing", 7)
    past_only = [e for e in events if e.timestamp_s < 50 * 60]
    a = detect_shifts(analyse(events, meta, 5, 50))
    b = detect_shifts(analyse(past_only, meta, 5, 50))
    assert [m.to_dict() for m in a] == [m.to_dict() for m in b]


def test_ongoing_status_and_low_event_only_at_full_time():
    events, meta, _ = generate_match("momentum_swing", 7)
    ms = detect_shifts(analyse(events, meta, 5, 35))
    assert ms and ms[-1].status == "ongoing" and ms[-1].end_min == 35
    low, meta2, _ = generate_match("low_event", 7)
    assert not any(m.pattern == "low_event" for m in detect_shifts(analyse(low, meta2, 5, 60)))
    assert any(m.pattern == "low_event" for m in detect_shifts(analyse(low, meta2)))


def test_live_narratives_pass_review_and_cite_minute():
    events, meta, _ = generate_match("high_press", 4)
    for until in (25, 50, 70):
        an = analyse(events, meta, 5, until)
        ms = detect_shifts(an)
        reg = build_evidence(an, ms)
        for mode in MODES:
            for lang in ("en", "id"):
                n, rep = review(render(mode, lang, an, ms), reg, {meta.short(meta.home), meta.short(meta.away)})
                assert rep.status == "approved", (until, mode, lang, rep.removed)
                if mode != "broadcast" or lang == "en":
                    assert str(until) in n.plain_text()


def test_first_flagged_and_latency_report():
    events, meta, gts = generate_match("momentum_swing", 7)
    first = first_flagged(events, meta, gts, DetectorConfig())
    assert first[0] == 25.0                                  # HOME dominance from 10' flagged at 25'
    rep = live_latency(range(7, 8))
    assert rep["overall"]["patterns"] >= 6 and "low_event" not in str(rep["overall"])


def test_api_live_mode():
    c = TestClient(app)
    d = c.post("/api/analyze", json={"scenario": "momentum_swing", "seed": 7, "until_min": 45}).json()
    assert d["status"] == "ok" and d["analytics"]["live"] and d["analytics"]["until_min"] == 45
    started = {g["pattern"] + g["team"]: g["started"] for g in d["ground_truth"]}
    assert started["dominanceHOME"] and not started["dominanceAWAY"]
    assert "Score at 45'" in " ".join(c["text"] for b in d["narratives"]["casual"]["blocks"] for c in b["claims"])
    assert c.post("/api/analyze", json={"scenario": "dominance", "until_min": 0}).status_code == 422
