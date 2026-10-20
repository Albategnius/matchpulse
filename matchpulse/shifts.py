"""Explainable match-shift detection.

Every detector compares a *comparison window* against a *baseline* using
configurable thresholds (DetectorConfig). Thresholds were tuned on development
seeds 0-9 only; the evaluation in scripts/evaluate.py uses held-out seeds.

Language rule: detectors describe associations ("coincided with", "consistent
with"), never causation.
"""

from __future__ import annotations

import statistics
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Optional

from .analytics import MatchAnalytics, window_range_counts


@dataclass
class DetectorConfig:
    smooth_windows: int = 3                 # centered rolling mean over N windows
    min_segment_windows: int = 2            # pattern must persist >= 2 windows (10 min at 5-min windows)
    bridge_gap_windows: int = 1             # allow a 1-window gap inside a segment
    dominance_combined: float = 0.66        # mean of completed-pass share and final-third-entry share (smoothed)
    dominance_fte_share: float = 0.60       # share of final-third entries (smoothed), also required
    dominance_min_windows: int = 3          # dominance must persist >= 15 min
    press_high_pressures: float = 6.5       # high pressures per window (smoothed)
    press_linked_turnovers: float = 2.0     # press-linked turnovers per window (smoothed)
    press_vs_median_ratio: float = 1.8      # smoothed value vs the team's match median
    counter_fast_breaks: float = 1.3        # fast breaks per window (smoothed)
    counter_min_total: int = 5              # fast breaks inside the segment
    swing_threshold: float = 0.45           # |smoothed momentum| that counts as control
    swing_min_windows: int = 4              # each control spell must last >= 20 min
    swing_max_gap_min: float = 25.0         # max minutes between control spells
    comeback_from_min: float = 65.0
    low_event_max_shots: int = 12           # total shots (both teams)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Moment:
    id: str
    pattern: str
    team: Optional[str]
    start_min: float
    end_min: float
    baseline: str
    metrics: list[dict[str, Any]]          # {metric, label, team, window_value, baseline_value, delta}
    evidence_event_ids: list[int]
    strength: str                           # low | medium | high
    strength_score: float                   # 0..1
    limitations: list[str]                  # keys, localized in narrative layer
    next_question: str                      # key, localized in narrative layer
    facts: dict[str, Any] = field(default_factory=dict)  # numbers the narrative may cite
    status: str = "final"                   # final | ongoing (live replay: still running at the cut-off)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _smooth(values: list[float], n: int) -> list[float]:
    half = n // 2
    out = []
    for i in range(len(values)):
        span = values[max(0, i - half): i + half + 1]
        out.append(sum(span) / len(span))
    return out


def _segments(flags: list[bool], bridge: int, min_len: int) -> list[tuple[int, int]]:
    segs: list[list[int]] = []
    for i, f in enumerate(flags):
        if not f:
            continue
        if segs and i - segs[-1][1] - 1 <= bridge:
            segs[-1][1] = i
        else:
            segs.append([i, i])
    return [(a, b) for a, b in segs if b - a + 1 >= min_len]


def _strength(score: float) -> str:
    return "high" if score >= 0.67 else "medium" if score >= 0.4 else "low"


def _metric(label_key: str, team: str, window_value: float, baseline_value: float, label: str) -> dict[str, Any]:
    return {"metric": label_key, "label": label, "team": team,
            "window_value": round(window_value, 2), "baseline_value": round(baseline_value, 2),
            "delta": round(window_value - baseline_value, 2)}


def _per_window_rate(counts: dict[str, float], key: str, n_windows: int) -> float:
    return counts[key] / n_windows if n_windows else 0.0


def _baseline_outside(an: MatchAnalytics, a: int, b: int) -> tuple[dict[str, dict[str, float]], int]:
    """Counts summed over all windows OUTSIDE [a, b], plus the number of such windows."""
    teams = (an.meta.home, an.meta.away)
    out = {t: {} for t in teams}
    n = 0
    for w in an.windows:
        if a <= w["index"] <= b:
            continue
        n += 1
        for t in teams:
            for k, v in w["teams"][t].items():
                if k in ("pass_accuracy", "possession_pct"):
                    continue
                out[t][k] = out[t].get(k, 0.0) + v
    return out, n


def _window_events(an: MatchAnalytics, start_min: float, end_min: float,
                   pred: Callable[[Any], bool], limit: int = 40) -> list[int]:
    ids = [e.event_id for e in an.events if start_min * 60 <= e.timestamp_s < end_min * 60 and pred(e)]
    return ids[:limit]


def detect_shifts(an: MatchAnalytics, cfg: Optional[DetectorConfig] = None) -> list[Moment]:
    cfg = cfg or DetectorConfig()
    moments: list[Moment] = []
    moments += _detect_dominance(an, cfg)
    moments += _detect_high_press(an, cfg)
    moments += _detect_counterattack(an, cfg)
    moments += _detect_momentum_swing(an, cfg)
    moments += _detect_comeback(an, cfg)
    moments += _detect_low_event(an, cfg)
    if an.live:
        for m in moments:
            if m.end_min >= an.until_min - 1e-6:
                m.end_min = an.until_min
                m.status = "ongoing"
    moments.sort(key=lambda m: (m.start_min, m.pattern))
    for i, m in enumerate(moments):
        m.id = f"M{i + 1}"
    return moments


# --- detectors ---------------------------------------------------------------

def _share_series(an: MatchAnalytics, team: str, key: str) -> list[float]:
    opp = an.meta.away if team == an.meta.home else an.meta.home
    out = []
    for w in an.windows:
        a, b = w["teams"][team][key], w["teams"][opp][key]
        out.append(a / (a + b) if (a + b) else 0.5)
    return out


def _detect_dominance(an: MatchAnalytics, cfg: DetectorConfig) -> list[Moment]:
    res = []
    W = an.window_min
    for team in (an.meta.home, an.meta.away):
        opp = an.meta.away if team == an.meta.home else an.meta.home
        ps = _smooth(_share_series(an, team, "passes_completed"), cfg.smooth_windows)
        fs = _smooth(_share_series(an, team, "final_third_entries"), cfg.smooth_windows)
        flags = [(p + f) / 2 >= cfg.dominance_combined and f >= cfg.dominance_fte_share for p, f in zip(ps, fs)]
        for a, b in _segments(flags, cfg.bridge_gap_windows, cfg.dominance_min_windows):
            s, e = a * W, (b + 1) * W
            win = window_range_counts(an, s, e)
            base, nb = _baseline_outside(an, a, b)
            nw = b - a + 1
            t_fte, o_fte = win[team]["final_third_entries"], win[opp]["final_third_entries"]
            fte_share = 100 * t_fte / (t_fte + o_fte) if (t_fte + o_fte) else 50.0
            b_fte = base[team].get("final_third_entries", 0)
            b_ofte = base[opp].get("final_third_entries", 0)
            base_share = 100 * b_fte / (b_fte + b_ofte) if (b_fte + b_ofte) else 50.0
            pass_share = 100 * win[team]["passes_completed"] / max(1, win[team]["passes_completed"] + win[opp]["passes_completed"])
            bp = base[team].get("passes_completed", 0)
            bpo = base[opp].get("passes_completed", 0)
            base_pass_share = 100 * bp / max(1, bp + bpo)
            margin = (fte_share - 50) / 50 * 0.5 + min(nw / 6, 1) * 0.5
            score = round(max(0.0, min(1.0, margin)), 2)
            res.append(Moment(
                id="", pattern="dominance", team=team, start_min=s, end_min=e,
                baseline="rest_of_match",
                metrics=[
                    _metric("fte_share_pct", team, fte_share, base_share, "Final-third entry share %"),
                    _metric("completed_pass_share_pct", team, pass_share, base_pass_share, "Completed-pass share %"),
                    _metric("final_third_entries_per_window", team, t_fte / nw,
                            _per_window_rate(base[team], "final_third_entries", nb), "Final-third entries per window"),
                    _metric("shots", team, win[team]["shots"], _per_window_rate(base[team], "shots", nb) * nw,
                            "Shots (vs. baseline rate, same duration)"),
                ],
                evidence_event_ids=_window_events(an, s, e, lambda ev: ev.team == team and an.flags[ev.event_id].final_third_entry),
                strength=_strength(score), strength_score=score,
                limitations=["territory_not_quality", "opponent_strategy"],
                next_question="dominance_q",
                facts={"duration_min": e - s, "fte_share_pct": round(fte_share), "baseline_fte_share_pct": round(base_share),
                       "pass_share_pct": round(pass_share), "shots": int(win[team]["shots"]),
                       "final_third_entries": int(t_fte), "opp_final_third_entries": int(o_fte)},
            ))
    return res


def _detect_high_press(an: MatchAnalytics, cfg: DetectorConfig) -> list[Moment]:
    res = []
    W = an.window_min
    for team in (an.meta.home, an.meta.away):
        hp_raw = [w["teams"][team]["high_pressures"] for w in an.windows]
        pl_raw = [w["teams"][team]["press_linked_turnovers"] for w in an.windows]
        hp, pl = _smooth(hp_raw, cfg.smooth_windows), _smooth(pl_raw, cfg.smooth_windows)
        med = statistics.median(hp_raw) if hp_raw else 0.0
        flags = [h >= cfg.press_high_pressures and p >= cfg.press_linked_turnovers
                 and h >= cfg.press_vs_median_ratio * max(med, 1.0) for h, p in zip(hp, pl)]
        for a, b in _segments(flags, cfg.bridge_gap_windows, cfg.min_segment_windows):
            s, e = a * W, (b + 1) * W
            nw = b - a + 1
            win = window_range_counts(an, s, e)
            base, nb = _baseline_outside(an, a, b)
            hp_w = win[team]["high_pressures"] / nw
            hp_b = _per_window_rate(base[team], "high_pressures", nb)
            pl_w = win[team]["press_linked_turnovers"] / nw
            pl_b = _per_window_rate(base[team], "press_linked_turnovers", nb)
            ratio = hp_w / hp_b if hp_b else hp_w
            score = round(max(0.0, min(1.0, (min(ratio, 4) - 1) / 3 * 0.6 + min(nw / 4, 1) * 0.4)), 2)
            res.append(Moment(
                id="", pattern="high_press", team=team, start_min=s, end_min=e, baseline="rest_of_match",
                metrics=[
                    _metric("high_pressures_per_window", team, hp_w, hp_b, "High pressures per window"),
                    _metric("press_linked_turnovers_per_window", team, pl_w, pl_b, "Press-linked opponent turnovers per window"),
                    _metric("high_turnovers_forced_per_window", team, win[team]["high_turnovers_forced"] / nw,
                            _per_window_rate(base[team], "high_turnovers_forced", nb), "High turnovers forced per window"),
                ],
                evidence_event_ids=_window_events(
                    an, s, e, lambda ev: (ev.team == team and an.flags[ev.event_id].high_pressure)
                    or (ev.team != team and an.flags[ev.event_id].press_linked)),
                strength=_strength(score), strength_score=score,
                limitations=["press_association", "turnover_other_causes"],
                next_question="press_q",
                facts={"duration_min": e - s, "high_pressures": int(win[team]["high_pressures"]),
                       "press_linked_turnovers": int(win[team]["press_linked_turnovers"]),
                       "high_pressures_per_window": round(hp_w, 1), "baseline_per_window": round(hp_b, 1),
                       "ratio": round(ratio, 1)},
            ))
    return res


def _detect_counterattack(an: MatchAnalytics, cfg: DetectorConfig) -> list[Moment]:
    res = []
    W = an.window_min
    for team in (an.meta.home, an.meta.away):
        fb_raw = [w["teams"][team]["fast_breaks"] for w in an.windows]
        fb = _smooth(fb_raw, cfg.smooth_windows)
        flags = [v >= cfg.counter_fast_breaks for v in fb]
        for a, b in _segments(flags, cfg.bridge_gap_windows, cfg.min_segment_windows):
            s, e = a * W, (b + 1) * W
            nw = b - a + 1
            breaks = [x for x in an.fast_breaks if x.team == team and s * 60 <= x.start_s < e * 60]
            if len(breaks) < cfg.counter_min_total:
                continue
            base, nb = _baseline_outside(an, a, b)
            fb_b = _per_window_rate(base[team], "fast_breaks", nb)
            shots = sum(1 for x in breaks if x.ended_in_shot)
            ratio = (len(breaks) / nw) / fb_b if fb_b else len(breaks) / nw
            score = round(max(0.0, min(1.0, (min(ratio, 5) - 1) / 4 * 0.6 + min(len(breaks) / 8, 1) * 0.4)), 2)
            ids = [i for x in breaks for i in x.event_ids][:40]
            res.append(Moment(
                id="", pattern="counterattack", team=team, start_min=s, end_min=e, baseline="rest_of_match",
                metrics=[
                    _metric("fast_breaks_per_window", team, len(breaks) / nw, fb_b, "Fast breaks per window"),
                    _metric("fast_breaks_ending_in_shot", team, shots, 0, "Fast breaks ending in a shot"),
                ],
                evidence_event_ids=ids, strength=_strength(score), strength_score=score,
                limitations=["counter_definition", "opponent_strategy"], next_question="counter_q",
                facts={"duration_min": e - s, "fast_breaks": len(breaks), "fast_break_shots": shots,
                       "fast_breaks_per_window": round(len(breaks) / nw, 1), "baseline_per_window": round(fb_b, 1)},
            ))
    return res


def _detect_momentum_swing(an: MatchAnalytics, cfg: DetectorConfig) -> list[Moment]:
    W = an.window_min
    mom = _smooth([m["momentum"] for m in an.momentum], cfg.smooth_windows)
    home_segs = [(a, b, an.meta.home) for a, b in
                 _segments([v >= cfg.swing_threshold for v in mom], cfg.bridge_gap_windows, cfg.swing_min_windows)]
    away_segs = [(a, b, an.meta.away) for a, b in
                 _segments([v <= -cfg.swing_threshold for v in mom], cfg.bridge_gap_windows, cfg.swing_min_windows)]
    segs = sorted(home_segs + away_segs)
    res = []
    for (a1, b1, t1), (a2, b2, t2) in zip(segs, segs[1:]):
        if t1 == t2:
            continue
        gap = (a2 - b1 - 1) * W
        if gap > cfg.swing_max_gap_min:
            continue
        s, e = max(0, b1 * W), min(90, (a2 + 1) * W)
        before = window_range_counts(an, a1 * W, (b1 + 1) * W)
        after = window_range_counts(an, a2 * W, (b2 + 1) * W)
        n1, n2 = b1 - a1 + 1, b2 - a2 + 1
        mb = sum(mom[a1:b1 + 1]) / n1
        ma = sum(mom[a2:b2 + 1]) / n2
        sign = 1 if t2 == an.meta.home else -1
        swing = abs(ma - mb)
        score = round(max(0.0, min(1.0, swing / 1.2 * 0.7 + min((n1 + n2) / 10, 1) * 0.3)), 2)
        res.append(Moment(
            id="", pattern="momentum_swing", team=t2, start_min=s, end_min=e,
            baseline=f"{a1 * W:.0f}-{(b1 + 1) * W:.0f} min vs {a2 * W:.0f}-{(b2 + 1) * W:.0f} min",
            metrics=[
                _metric("momentum_index", t2, sign * ma, sign * mb, "Momentum index (team view)"),
                _metric("final_third_entries_per_window", t2, after[t2]["final_third_entries"] / n2,
                        before[t2]["final_third_entries"] / n1, "Final-third entries per window"),
                _metric("shots_per_window", t2, after[t2]["shots"] / n2, before[t2]["shots"] / n1, "Shots per window"),
            ],
            evidence_event_ids=_window_events(an, a2 * W, (b2 + 1) * W,
                                              lambda ev: ev.team == t2 and (ev.event_type == "shot" or an.flags[ev.event_id].final_third_entry)),
            strength=_strength(score), strength_score=score,
            limitations=["momentum_index_definition", "score_effects"], next_question="swing_q",
            facts={"from_team": t1, "to_team": t2,
                   "before_start": a1 * W, "before_end": (b1 + 1) * W, "after_start": a2 * W, "after_end": (b2 + 1) * W,
                   "momentum_before": round(sign * mb, 2), "momentum_after": round(sign * ma, 2),
                   "fte_before": int(before[t2]["final_third_entries"]), "fte_after": int(after[t2]["final_third_entries"])},
        ))
    return res


def _detect_comeback(an: MatchAnalytics, cfg: DetectorConfig) -> list[Moment]:
    """A late comeback = a team trailing at some point after `comeback_from_min` that finishes
    ahead, or that recovers a deficit of 2+ goals to draw."""
    if not an.goals:
        return []
    res = []
    home, away = an.meta.home, an.meta.away
    final = an.score()
    for team in (home, away):
        opp = away if team == home else home
        pre = {home: 0, away: 0}
        for g in an.goals:
            if g["minute"] < cfg.comeback_from_min:
                pre = g["score"]
        trailing_late = pre[team] < pre[opp]
        worst = 0
        for g in an.goals:
            d = g["score"][team] - g["score"][opp]
            worst = min(worst, d)
            if g["minute"] >= cfg.comeback_from_min and d < 0:
                trailing_late = True
        late_goals = [g for g in an.goals if g["minute"] >= cfg.comeback_from_min and g["team"] == team]
        diff_final = final[team] - final[opp]
        if not trailing_late or not late_goals:
            continue
        if not (diff_final > 0 or (diff_final == 0 and worst <= -2)):
            continue
        s, e = late_goals[0]["minute"], max(late_goals[-1]["minute"], late_goals[0]["minute"] + 1)
        score_v = round(min(1.0, 0.5 + 0.25 * len(late_goals) + (0.25 if diff_final > 0 else 0)), 2)
        res.append(Moment(
            id="", pattern="late_comeback", team=team, start_min=s, end_min=min(90.0, e),
            baseline=f"score at {cfg.comeback_from_min:.0f}'",
            metrics=[
                _metric("goal_difference", team, diff_final, pre[team] - pre[opp], "Goal difference (team view)"),
                _metric("late_goals", team, len(late_goals), 0, "Goals after the comeback threshold"),
            ],
            evidence_event_ids=[g["event_id"] for g in late_goals],
            strength=_strength(score_v), strength_score=score_v,
            limitations=["comeback_small_sample"], next_question="comeback_q",
            facts={"late_goals": len(late_goals), "goal_minutes": [round(g["minute"]) for g in late_goals],
                   "score_before": f"{pre[home]}-{pre[away]}", "final_score": f"{final[home]}-{final[away]}",
                   "worst_deficit": abs(worst)},
        ))
    return res


def _detect_low_event(an: MatchAnalytics, cfg: DetectorConfig) -> list[Moment]:
    if an.live:  # a whole-match judgement; only made at full time
        return []
    home, away = an.meta.home, an.meta.away
    shots = an.totals[home]["shots"] + an.totals[away]["shots"]
    if shots > cfg.low_event_max_shots:
        return []
    fte = an.totals[home]["final_third_entries"] + an.totals[away]["final_third_entries"]
    score = round(max(0.0, min(1.0, 1 - shots / (cfg.low_event_max_shots + 1))), 2)
    return [Moment(
        id="", pattern="low_event", team=None, start_min=0, end_min=90, baseline="detector threshold",
        metrics=[
            _metric("total_shots", "BOTH", shots, cfg.low_event_max_shots, "Total shots vs. low-event threshold"),
            _metric("total_final_third_entries", "BOTH", fte, 0, "Total final-third entries"),
        ],
        evidence_event_ids=[e.event_id for e in an.events if e.event_type == "shot"][:40],
        strength=_strength(score), strength_score=score,
        limitations=["low_event_threshold"], next_question="low_event_q",
        facts={"total_shots": int(shots), "threshold": cfg.low_event_max_shots, "final_third_entries": int(fte),
               "shots_home": int(an.totals[home]["shots"]), "shots_away": int(an.totals[away]["shots"])},
    )]
