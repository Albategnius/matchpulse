"""Unit tests: schema, generator reproducibility, analytics formulas, detection, narratives, reviewer."""

import pytest

from matchpulse.analytics import FINAL_THIRD_X, analyse, compute_flags, threat_units
from matchpulse.evaluation import match
from matchpulse.narrative import MODES, build_evidence, render
from matchpulse.reviewer import check_claim, review
from matchpulse.narrative import Claim
from matchpulse.schema import Event, MatchDataError, MatchMeta, csv_to_rows, events_to_csv, validate_events
from matchpulse.shifts import detect_shifts
from matchpulse.synth import SCENARIOS, generate_match


def ev(i, t, team, etype, outcome="success", x=50, y=50, end_x=None, end_y=None):
    return Event(match_id="T", event_id=i, timestamp_s=t, period=1 if t < 2700 else 2, team=team,
                 player_id=f"{team[0]}01", event_type=etype, outcome=outcome, x=x, y=y, end_x=end_x, end_y=end_y)


META = MatchMeta(match_id="T", home="HOME", away="AWAY", home_short="H", away_short="A")


# --- generator ---------------------------------------------------------------------
def test_generator_is_reproducible():
    a, _, _ = generate_match("high_press", 11)
    b, _, _ = generate_match("high_press", 11)
    c, _, _ = generate_match("high_press", 12)
    assert [e.model_dump() for e in a] == [e.model_dump() for e in b]
    assert [e.model_dump() for e in a] != [e.model_dump() for e in c]


@pytest.mark.parametrize("key", list(SCENARIOS))
def test_every_scenario_is_valid_and_labelled_synthetic(key):
    events, meta, gts = generate_match(key, 3)
    assert meta.synthetic and meta.match_id.startswith("SYN-")
    assert 1000 < len(events) < 2500
    assert gts, "every scenario has ground truth"
    assert all(e.scenario == key for e in events)
    assert all(0 <= e.x <= 100 and 0 <= e.y <= 100 for e in events)
    ts = [e.timestamp_s for e in events]
    assert ts == sorted(ts)


def test_scripted_comeback_score():
    events, meta, _ = generate_match("late_comeback", 5)
    an = analyse(events, meta)
    assert an.score() == {"HOME": 3, "AWAY": 2}
    assert [t["to"] for t in an.state_transitions if t["team"] == "HOME"][-1] == "leading"


def test_csv_roundtrip():
    events, meta, _ = generate_match("dominance", 2)
    text = "# SYNTHETIC\n" + events_to_csv(events)
    back, meta2, rep = validate_events(csv_to_rows(text), meta)
    assert rep.ok and rep.valid_rows == len(events)
    assert [e.possession_id for e in back] == [e.possession_id for e in events]


# --- schema failure cases ----------------------------------------------------------------
def test_invalid_timestamps_and_coordinates_are_dropped_not_fatal():
    events, meta, _ = generate_match("dominance", 1)
    rows = [e.model_dump() for e in events]
    rows[5]["timestamp_s"] = -10
    rows[6]["x"] = 140
    rows[7]["event_type"] = "teleport"
    out, _, rep = validate_events(rows, meta)
    assert len(rep.dropped) == 3 and rep.ok
    assert len(out) == len(events) - 3


def test_inconsistent_teams_is_fatal():
    events, meta, _ = generate_match("dominance", 1)
    rows = [e.model_dump() for e in events]
    rows[10]["team"] = "THIRD"
    with pytest.raises(MatchDataError, match="Expected exactly 2 teams"):
        validate_events(rows, meta)


def test_teams_not_matching_metadata_is_fatal():
    events, _, _ = generate_match("dominance", 1)
    with pytest.raises(MatchDataError, match="do not match metadata"):
        validate_events([e.model_dump() for e in events], MatchMeta(match_id="x", home="A", away="B"))


def test_empty_dataset_is_fatal():
    with pytest.raises(MatchDataError):
        validate_events([], None)


def test_missing_columns_rejected():
    with pytest.raises(MatchDataError, match="missing required columns"):
        csv_to_rows("match_id,team\nX,HOME\n")


def test_missing_events_produce_warning_and_empty_windows():
    events, meta, _ = generate_match("dominance", 1)
    rows = [e.model_dump() for e in events if not (20 * 60 <= e.timestamp_s < 32 * 60)]
    out, meta2, rep = validate_events(rows, meta)
    assert any("gap" in w for w in rep.warnings)
    an = analyse(out, meta2)
    assert any(w["empty"] for w in an.windows)
    detect_shifts(an)  # must not crash on empty windows


# --- analytics formulas ------------------------------------------------------------------
def test_progressive_and_final_third_flags():
    e1 = ev(0, 10, "HOME", "pass", x=50, end_x=70)    # +20 and crosses 66.67
    e2 = ev(1, 12, "HOME", "pass", x=30, end_x=38)    # +8: not progressive
    e3 = ev(2, 14, "HOME", "pass", "fail", x=60, end_x=80)
    f = compute_flags([e1, e2, e3])
    assert f[0].progressive and f[0].final_third_entry
    assert not f[1].progressive and not f[1].final_third_entry
    assert f[2].turnover and not f[2].final_third_entry
    assert FINAL_THIRD_X == pytest.approx(66.667, abs=1e-2)


def test_press_linked_turnover_requires_time_and_proximity():
    press = ev(0, 100, "AWAY", "pressure", "none", x=70, y=50)       # = HOME x 30
    lost = ev(1, 103, "HOME", "pass", "fail", x=30, y=50, end_x=35, end_y=50)
    late = ev(2, 120, "HOME", "pass", "fail", x=30, y=50, end_x=35, end_y=50)
    f = compute_flags([press, lost, late])
    assert f[0].high_pressure and f[1].press_linked and not f[2].press_linked


def test_totals_and_threat_units():
    events = [ev(0, 0, "HOME", "kickoff"), ev(1, 5, "HOME", "pass", x=50, end_x=70),
              ev(2, 9, "HOME", "shot", "goal", x=90, end_x=100), ev(3, 60, "AWAY", "kickoff"),
              ev(4, 65, "AWAY", "pass", "fail", x=40, end_x=45), ev(5, 66, "HOME", "interception", x=55)]
    an = analyse(events, META)
    h = an.totals["HOME"]
    assert (h["shots"], h["goals"], h["final_third_entries"], h["high_turnovers_forced"]) == (1, 1, 1, 1)
    assert threat_units(h) == 1 + 3 * 1 + 1
    assert an.totals["AWAY"]["turnovers"] == 1
    assert an.score() == {"HOME": 1, "AWAY": 0}


def test_window_size_validation():
    events, meta, _ = generate_match("dominance", 1)
    with pytest.raises(ValueError):
        analyse(events, meta, window_min=0)
    assert len(analyse(events, meta, window_min=10).windows) == 9


# --- detection regression fixtures (fixed seeds) -------------------------------------------
@pytest.mark.parametrize("key,seed", [("dominance", 7), ("momentum_swing", 7), ("late_comeback", 5),
                                      ("low_event", 7), ("high_press", 4)])
def test_expected_patterns_detected_on_fixed_seeds(key, seed):
    events, meta, gts = generate_match(key, seed)
    res = match(detect_shifts(analyse(events, meta)), gts)
    assert not res["fn"], f"missed {res['fn']}"


def test_moment_has_explainability_fields():
    events, meta, _ = generate_match("high_press", 4)
    for m in detect_shifts(analyse(events, meta)):
        assert m.metrics and m.baseline and m.limitations and m.next_question
        assert 0 <= m.strength_score <= 1 and m.strength in {"low", "medium", "high"}
        assert m.evidence_event_ids


# --- narratives & reviewer ---------------------------------------------------------------------
@pytest.fixture(scope="module")
def swing():
    events, meta, _ = generate_match("momentum_swing", 7)
    an = analyse(events, meta)
    ms = detect_shifts(an)
    return an, ms, build_evidence(an, ms), {meta.short(meta.home), meta.short(meta.away)}


def test_modes_are_genuinely_different(swing):
    an, ms, _, _ = swing
    texts = {m: render(m, "en", an, ms) for m in MODES}
    assert texts["broadcast"].overlay and not texts["casual"].overlay
    assert any(b.table for b in texts["analyst"].blocks)
    words = {m: set(n.plain_text().lower().split()) for m, n in texts.items()}
    for a in MODES:
        for b in MODES:
            if a < b:
                j = len(words[a] & words[b]) / len(words[a] | words[b])
                assert j < 0.6, (a, b, j)


def test_bahasa_indonesia_output(swing):
    an, ms, _, _ = swing
    t = render("analyst", "id", an, ms).plain_text()
    assert "Konsisten dengan" in t and "Consistent with" not in t


def test_all_template_claims_pass_review(swing):
    an, ms, reg, teams = swing
    for mode in MODES:
        for lang in ("en", "id"):
            _, rep = review(render(mode, lang, an, ms), reg, teams)
            assert rep.status == "approved" and rep.evidence_coverage == 1.0


def test_reviewer_catches_wrong_number_causation_and_missing_evidence(swing):
    _, _, reg, teams = swing
    ok = Claim("a", "Final score 1-1.", ["match.score_home", "match.score_away"])
    wrong = Claim("b", "Final score 4-1.", ["match.score_home", "match.score_away"])
    causal = Claim("c", "The press caused the goal.", [])
    nocite = Claim("d", "They had 12 shots.", [])
    ghost = Claim("e", "Nice.", ["does.not.exist"])
    assert check_claim(ok, reg, teams, set()) == []
    assert check_claim(wrong, reg, teams, set())
    assert check_claim(causal, reg, teams, set())
    assert check_claim(nocite, reg, teams, set())
    assert check_claim(ghost, reg, teams, set())


def test_hedged_negation_is_not_flagged_as_causal(swing):
    _, _, reg, teams = swing
    c = Claim("h", "The turnovers coincided with pressure but are not proven to be caused by it.", [])
    assert check_claim(c, reg, teams, set()) == []


def test_contradictory_evidence_rejected(swing):
    """A claim citing the final score but stating the opposite result is rejected."""
    an, ms, reg, teams = swing
    n = render("casual", "en", an, ms)
    n.headline.text = "Northbridge won 3-0"
    _, rep = review(n, reg, teams)
    assert rep.status == "rejected"
