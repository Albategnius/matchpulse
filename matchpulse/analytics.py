"""Deterministic analytics engine. Every formula is documented in docs/METRICS.md.

Nothing here is estimated by an AI model: these numbers are the single source of
truth that all narratives must be traced back to. Metrics that the synthetic schema
cannot support (xG, ball speed, player speed, distance covered, tracking data) are
deliberately NOT computed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from .schema import Event, MatchMeta, MATCH_SECONDS

FINAL_THIRD_X = 200 / 3          # 66.67
OPP_HALF_X = 50.0
PROGRESSIVE_MIN_DX = 10.0
PROGRESSIVE_MIN_END_X = 40.0
PRESS_LINK_SECONDS = 5.0
FAST_BREAK_SECONDS = 15.0

METRIC_LABELS = {
    "shots": "Shots",
    "shots_on_target": "Shots on target",
    "goals": "Goals",
    "passes": "Passes attempted",
    "passes_completed": "Passes completed",
    "pass_accuracy": "Pass accuracy %",
    "progressive_actions": "Progressive actions",
    "final_third_entries": "Final-third entries",
    "pressures": "Pressures",
    "high_pressures": "High pressures (opp. half)",
    "turnovers": "Turnovers (ball losses)",
    "high_turnovers_forced": "High turnovers forced",
    "press_linked_turnovers": "Turnovers within 5s of a press",
    "fast_breaks": "Fast breaks",
    "possession_pct": "Possession % (time)",
    "events": "Events",
}


@dataclass
class EventFlags:
    progressive: bool = False
    final_third_entry: bool = False
    turnover: bool = False
    high_pressure: bool = False
    press_linked: bool = False  # this turnover happened within 5s of an opponent pressure nearby


@dataclass
class FastBreak:
    team: str
    start_s: float
    reach_s: float
    start_x: float
    event_ids: list[int]
    ended_in_shot: bool


@dataclass
class MatchAnalytics:
    meta: MatchMeta
    window_min: int
    totals: dict[str, dict[str, float]]
    windows: list[dict[str, Any]]
    momentum: list[dict[str, Any]]
    goals: list[dict[str, Any]]
    state_transitions: list[dict[str, Any]]
    fast_breaks: list[FastBreak]
    flags: dict[int, EventFlags] = field(repr=False)
    events: list[Event] = field(repr=False)
    until_min: float = 90.0                # live replay: analysis horizon (90 = full time)

    @property
    def live(self) -> bool:
        return self.until_min < 90.0

    def score(self) -> dict[str, int]:
        return {t: int(self.totals[t]["goals"]) for t in (self.meta.home, self.meta.away)}

    def to_dict(self) -> dict[str, Any]:
        return {
            "window_min": self.window_min,
            "totals": self.totals,
            "windows": self.windows,
            "momentum": self.momentum,
            "goals": self.goals,
            "state_transitions": self.state_transitions,
            "fast_breaks": [fb.__dict__ for fb in self.fast_breaks],
            "score": self.score(),
            "until_min": self.until_min,
            "live": self.live,
        }


def _empty_counts() -> dict[str, float]:
    return {k: 0.0 for k in METRIC_LABELS if k not in ("pass_accuracy", "possession_pct")}


def compute_flags(events: list[Event]) -> dict[int, EventFlags]:
    flags: dict[int, EventFlags] = {}
    recent_pressures: list[Event] = []
    for e in events:
        f = EventFlags()
        if e.event_type in ("pass", "carry") and e.outcome == "success" and e.end_x is not None:
            dx = e.end_x - e.x
            f.progressive = dx >= PROGRESSIVE_MIN_DX and e.end_x >= PROGRESSIVE_MIN_END_X
            f.final_third_entry = e.x < FINAL_THIRD_X <= e.end_x
        if e.event_type in ("pass", "carry") and e.outcome == "fail":
            f.turnover = True
            # Linked to a press if the opponent pressed within 5s and within ~15 units.
            for p in recent_pressures:
                if (p.team != e.team and 0 <= e.timestamp_s - p.timestamp_s <= PRESS_LINK_SECONDS
                        and abs((100 - p.x) - e.x) <= 15 and abs((100 - p.y) - e.y) <= 20):
                    f.press_linked = True
                    break
        if e.event_type == "pressure":
            f.high_pressure = e.x >= OPP_HALF_X
            recent_pressures.append(e)
        recent_pressures = [p for p in recent_pressures if e.timestamp_s - p.timestamp_s <= PRESS_LINK_SECONDS]
        flags[e.event_id] = f
    return flags


def detect_fast_breaks(events: list[Event]) -> list[FastBreak]:
    """A fast break = a possession won in the team's own half (x < 50) that reaches the
    final third (x >= 66.7) within 15 seconds of the possession starting."""
    by_pos: dict[int, list[Event]] = {}
    for e in events:
        if e.possession_id is not None and e.team == e.possession_team:
            by_pos.setdefault(e.possession_id, []).append(e)
    breaks: list[FastBreak] = []
    for evs in by_pos.values():
        first = evs[0]
        if first.event_type == "kickoff" or first.x >= OPP_HALF_X:
            continue
        reached: Optional[float] = None
        ids: list[int] = []
        shot = False
        for e in evs:
            if e.timestamp_s - first.timestamp_s > FAST_BREAK_SECONDS:
                break
            ids.append(e.event_id)
            far = max(e.x, e.end_x if (e.end_x is not None and e.outcome == "success") else 0)
            if e.event_type == "shot":
                far = max(far, e.x)
                shot = True
            if far >= FINAL_THIRD_X and reached is None:
                reached = e.timestamp_s
        if reached is not None:
            breaks.append(FastBreak(first.team, first.timestamp_s, reached, first.x, ids, shot))
    return breaks


def _possession_seconds(events: list[Event], lo: float, hi: float,
                        horizon: float = MATCH_SECONDS) -> dict[str, float]:
    """Seconds of possession per team inside [lo, hi), from possession start times."""
    starts: list[tuple[float, str]] = []
    last_pid = None
    for e in events:
        if e.possession_id != last_pid and e.possession_team:
            starts.append((e.timestamp_s, e.possession_team))
            last_pid = e.possession_id
    out: dict[str, float] = {}
    for i, (s, team) in enumerate(starts):
        end = starts[i + 1][0] if i + 1 < len(starts) else horizon
        a, b = max(s, lo), min(end, hi)
        if b > a:
            out[team] = out.get(team, 0.0) + (b - a)
    return out


def _accumulate(counts: dict[str, dict[str, float]], e: Event, f: EventFlags,
                fast_ids: set[int], opponent: dict[str, str]) -> None:
    c = counts[e.team]
    c["events"] += 1
    if e.event_type == "shot":
        c["shots"] += 1
        if e.outcome in ("goal", "saved"):
            c["shots_on_target"] += 1
        if e.outcome == "goal":
            c["goals"] += 1
    if e.event_type == "pass":
        c["passes"] += 1
        if e.outcome == "success":
            c["passes_completed"] += 1
    if f.progressive:
        c["progressive_actions"] += 1
    if f.final_third_entry:
        c["final_third_entries"] += 1
    if e.event_type == "pressure":
        c["pressures"] += 1
        if f.high_pressure:
            c["high_pressures"] += 1
    if f.turnover:
        c["turnovers"] += 1
        opp = counts[opponent[e.team]]
        if e.x < OPP_HALF_X:
            opp["high_turnovers_forced"] += 1
        if f.press_linked:
            opp["press_linked_turnovers"] += 1


def _finalize(counts: dict[str, dict[str, float]], poss: dict[str, float]) -> None:
    total_poss = sum(poss.values())
    for team, c in counts.items():
        c["pass_accuracy"] = round(100 * c["passes_completed"] / c["passes"], 1) if c["passes"] else 0.0
        c["possession_pct"] = round(100 * poss.get(team, 0.0) / total_poss, 1) if total_poss else 0.0


def threat_units(c: dict[str, float]) -> float:
    """Attacking Threat Units (ATU) = final-third entries + 3 x shots + high turnovers forced."""
    return c["final_third_entries"] + 3 * c["shots"] + c["high_turnovers_forced"]


def analyse(events: list[Event], meta: MatchMeta, window_min: int = 5,
            until_min: Optional[float] = None) -> MatchAnalytics:
    """Compute all deterministic analytics.

    `until_min` (live replay) restricts the analysis to events before that minute, as if
    the match were being watched live. Only complete windows are used for window-level
    analysis; totals include every event up to the cut-off.
    """
    if window_min < 1 or window_min > 45:
        raise ValueError("window_min must be between 1 and 45")
    if until_min is not None and not (1 <= until_min <= 90):
        raise ValueError("until_min must be between 1 and 90")
    horizon = MATCH_SECONDS if until_min is None or until_min >= 90 else until_min * 60
    if horizon < MATCH_SECONDS:
        events = [e for e in events if e.timestamp_s < horizon]
    teams = (meta.home, meta.away)
    opponent = {meta.home: meta.away, meta.away: meta.home}
    flags = compute_flags(events)
    breaks = detect_fast_breaks(events)
    fast_ids = {i for fb in breaks for i in fb.event_ids}

    totals = {t: _empty_counts() for t in teams}
    for e in events:
        _accumulate(totals, e, flags[e.event_id], fast_ids, opponent)
    for fb in breaks:
        totals[fb.team]["fast_breaks"] += 1
    _finalize(totals, _possession_seconds(events, 0, horizon, horizon))

    # Tumbling windows
    w_sec = window_min * 60
    n_windows = max(1, int(horizon // w_sec))
    windows: list[dict[str, Any]] = []
    buckets: list[list[Event]] = [[] for _ in range(n_windows)]
    for e in events:
        idx = int(e.timestamp_s // w_sec)
        if horizon == MATCH_SECONDS:
            idx = min(idx, n_windows - 1)
        if idx < n_windows:
            buckets[idx].append(e)
    for i in range(n_windows):
        lo, hi = i * w_sec, (i + 1) * w_sec
        counts = {t: _empty_counts() for t in teams}
        for e in buckets[i]:
            _accumulate(counts, e, flags[e.event_id], fast_ids, opponent)
        for fb in breaks:
            if lo <= fb.start_s < hi:
                counts[fb.team]["fast_breaks"] += 1
        _finalize(counts, _possession_seconds(events, lo, hi, horizon))
        windows.append({
            "index": i, "start_min": lo / 60, "end_min": hi / 60,
            "event_count": len(buckets[i]), "empty": len(buckets[i]) == 0,
            "teams": counts,
        })

    # Momentum: ATU share, HOME perspective, in [-1, 1]
    momentum = []
    for w in windows:
        uh, ua = threat_units(w["teams"][meta.home]), threat_units(w["teams"][meta.away])
        raw = (uh - ua) / (uh + ua) if (uh + ua) > 0 else 0.0
        momentum.append({"index": w["index"], "start_min": w["start_min"], "end_min": w["end_min"],
                         "atu_home": uh, "atu_away": ua, "momentum": round(raw, 3)})
    for i, m in enumerate(momentum):  # 3-window rolling mean (current + 2 previous)
        span = momentum[max(0, i - 2): i + 1]
        m["rolling"] = round(sum(s["momentum"] for s in span) / len(span), 3)

    # Goals and match-state transitions
    goals, transitions = [], []
    score = {t: 0 for t in teams}
    for e in events:
        if e.event_type == "shot" and e.outcome == "goal":
            before = dict(score)
            score[e.team] += 1
            goals.append({"minute": round(e.minute, 1), "team": e.team, "player_id": e.player_id,
                          "event_id": e.event_id, "score": dict(score)})
            st_before, st_after = _state(before, e.team, opponent), _state(score, e.team, opponent)
            if st_before != st_after:
                transitions.append({"minute": round(e.minute, 1), "team": e.team, "event_id": e.event_id,
                                    "from": st_before, "to": st_after, "score": dict(score)})
    return MatchAnalytics(meta, window_min, totals, windows, momentum, goals, transitions, breaks, flags, events,
                          until_min=round(horizon / 60, 2))


def _state(score: dict[str, int], team: str, opponent: dict[str, str]) -> str:
    d = score[team] - score[opponent[team]]
    return "leading" if d > 0 else "trailing" if d < 0 else "level"


def window_range_counts(an: MatchAnalytics, start_min: float, end_min: float) -> dict[str, dict[str, float]]:
    """Sum window counts for windows fully inside [start_min, end_min)."""
    teams = (an.meta.home, an.meta.away)
    out = {t: _empty_counts() for t in teams}
    poss = {t: 0.0 for t in teams}
    for w in an.windows:
        if w["start_min"] >= start_min - 1e-9 and w["end_min"] <= end_min + 1e-9:
            for t in teams:
                for k in out[t]:
                    out[t][k] += w["teams"][t][k]
                poss[t] += w["teams"][t]["possession_pct"] * (w["end_min"] - w["start_min"])
    _finalize_from_pct(out, poss)
    return out


def _finalize_from_pct(counts: dict[str, dict[str, float]], poss_weighted: dict[str, float]) -> None:
    total = sum(poss_weighted.values())
    for team, c in counts.items():
        c["pass_accuracy"] = round(100 * c["passes_completed"] / c["passes"], 1) if c["passes"] else 0.0
        c["possession_pct"] = round(100 * poss_weighted[team] / total, 1) if total else 0.0
