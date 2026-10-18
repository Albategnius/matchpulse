"""Synthetic Match Lab — reproducible, football-realistic event streams with ground truth.

The simulator is a possession-chain model. At every on-ball action the possessing
team either keeps the ball (pass/carry progressing toward x = 100), loses it
(interception/tackle by the opponent), or shoots. Scenario "modifiers" change the
team-level parameters inside specific time windows; those windows are recorded as
ground-truth annotations so the detector can be evaluated objectively.

Everything here is fictional and labelled SYNTHETIC.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any, Optional

from .schema import Event, MatchMeta, normalize_events

HALF = 45 * 60
FULL = 90 * 60

DEFAULT_MODS: dict[str, float] = {
    "security": 1.0,   # multiplier on ball-loss probability (lower = keeps the ball)
    "progress": 4.0,   # mean forward progress (x units) of a successful pass/carry
    "press": 0.10,     # probability of pressing an opponent action in the opponent's own half
    "press_win": 0.8,  # extra relative ball-loss probability for the opponent when pressed
    "counter": 0.03,   # probability that a ball won in own half becomes a fast break
    "shot": 1.0,       # multiplier on shot probability in the attacking zone
    "finish": 1.0,     # multiplier on goal probability of a shot
    "tempo": 3.8,      # mean seconds per on-ball action
}

TEAM_NAMES = {"HOME": "Northbridge Athletic", "AWAY": "Harbour City Rovers"}
TEAM_SHORT = {"HOME": "Northbridge", "AWAY": "Harbour City"}


@dataclass
class Segment:
    start_min: float
    end_min: float
    team: str  # "HOME", "AWAY" or "*"
    mods: dict[str, float]


@dataclass
class GroundTruth:
    pattern: str
    team: Optional[str]
    start_min: float
    end_min: float
    note: str

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass
class Scenario:
    key: str
    title: str
    title_id: str
    description: str
    segments: list[Segment]
    ground_truth: list[GroundTruth]
    scripted_goals: list[tuple[float, str]] = field(default_factory=list)
    random_goals: bool = True


DOMINANT = {"security": 0.5, "progress": 5.5, "shot": 0.55}
DOMINATED = {"security": 1.7, "progress": 3.0}

SCENARIOS: dict[str, Scenario] = {
    "dominance": Scenario(
        key="dominance",
        title="Sustained team dominance",
        title_id="Dominasi tim berkelanjutan",
        description="Northbridge controls territory and the ball from 15' to 60', then the game rebalances.",
        segments=[Segment(15, 60, "HOME", DOMINANT), Segment(15, 60, "AWAY", DOMINATED)],
        ground_truth=[GroundTruth("dominance", "HOME", 15, 60, "Injected territorial control for HOME")],
    ),
    "high_press": Scenario(
        key="high_press",
        title="High pressing and forced turnovers",
        title_id="Pressing tinggi dan turnover paksa",
        description="Harbour City presses high in two spells (20'-35', 60'-75'), forcing turnovers in Northbridge's half.",
        segments=[
            Segment(20, 35, "AWAY", {"press": 0.6, "press_win": 1.8}),
            Segment(60, 75, "AWAY", {"press": 0.6, "press_win": 1.8}),
        ],
        ground_truth=[
            GroundTruth("high_press", "AWAY", 20, 35, "Injected high press #1"),
            GroundTruth("high_press", "AWAY", 60, 75, "Injected high press #2"),
        ],
    ),
    "counterattack": Scenario(
        key="counterattack",
        title="Counterattacking",
        title_id="Serangan balik",
        description="Northbridge has more of the ball; Harbour City breaks fast in transition during 30'-50' and 65'-80'.",
        segments=[
            Segment(0, 90, "HOME", {"security": 0.85, "progress": 4.4}),
            Segment(30, 50, "AWAY", {"counter": 0.85}),
            Segment(65, 80, "AWAY", {"counter": 0.85}),
        ],
        ground_truth=[
            GroundTruth("counterattack", "AWAY", 30, 50, "Injected fast breaks #1"),
            GroundTruth("counterattack", "AWAY", 65, 80, "Injected fast breaks #2"),
        ],
    ),
    "momentum_swing": Scenario(
        key="momentum_swing",
        title="Momentum swing",
        title_id="Perubahan momentum",
        description="Northbridge dominates 10'-40'; after the break Harbour City takes control 50'-80'.",
        segments=[
            Segment(10, 40, "HOME", DOMINANT), Segment(10, 40, "AWAY", DOMINATED),
            Segment(50, 80, "AWAY", DOMINANT), Segment(50, 80, "HOME", DOMINATED),
        ],
        ground_truth=[
            GroundTruth("dominance", "HOME", 10, 40, "Injected control for HOME"),
            GroundTruth("dominance", "AWAY", 50, 80, "Injected control for AWAY"),
            GroundTruth("momentum_swing", "AWAY", 35, 60, "Control passes from HOME to AWAY"),
        ],
    ),
    "late_comeback": Scenario(
        key="late_comeback",
        title="Late comeback",
        title_id="Comeback di akhir laga",
        description="Harbour City leads 0-2; Northbridge pushes from 65' and scores three late goals.",
        segments=[Segment(65, 90, "HOME", DOMINANT), Segment(65, 90, "AWAY", DOMINATED)],
        scripted_goals=[(21, "AWAY"), (54, "AWAY"), (72, "HOME"), (81, "HOME"), (88, "HOME")],
        random_goals=False,
        ground_truth=[
            GroundTruth("dominance", "HOME", 65, 90, "Injected late pressure by HOME"),
            GroundTruth("late_comeback", "HOME", 70, 90, "HOME recovers from 0-2 to 3-2"),
        ],
    ),
    "low_event": Scenario(
        key="low_event",
        title="Low-event defensive match",
        title_id="Laga defensif minim peluang",
        description="Both teams play cautiously: slow progression, very few shots.",
        segments=[Segment(0, 90, "*", {"progress": 2.6, "shot": 0.25, "security": 1.15, "tempo": 4.4})],
        random_goals=False,
        ground_truth=[GroundTruth("low_event", None, 0, 90, "Injected low chance creation for both teams")],
    ),
}


class _Sim:
    def __init__(self, scenario: Scenario, seed: int, match_id: str):
        self.sc = scenario
        self.rng = random.Random(seed)
        self.match_id = match_id
        self.events: list[dict[str, Any]] = []
        self.t = 0.0
        self.eid = 0

    # --- helpers -----------------------------------------------------------
    def mods(self, team: str) -> dict[str, float]:
        m = dict(DEFAULT_MODS)
        minute = self.t / 60
        for s in self.sc.segments:
            if s.start_min <= minute < s.end_min and s.team in (team, "*"):
                m.update(s.mods)
        return m

    @staticmethod
    def other(team: str) -> str:
        return "AWAY" if team == "HOME" else "HOME"

    def player(self, team: str, attacking_x: float) -> str:
        # crude role weighting: defenders deep, forwards high
        if attacking_x < 30:
            pool = [2, 3, 4, 5, 6]
        elif attacking_x < 66:
            pool = [4, 5, 6, 7, 8, 10, 11]
        else:
            pool = [7, 8, 9, 10, 11]
        return f"{team[0]}{self.rng.choice(pool):02d}"

    def emit(self, team: str, etype: str, outcome: str, x: float, y: float,
             end_x: Optional[float] = None, end_y: Optional[float] = None,
             dt: float = 0.0, player: Optional[str] = None) -> None:
        self.events.append({
            "match_id": self.match_id,
            "event_id": self.eid,
            "timestamp_s": round(max(0.0, min(self.t + dt, FULL - 0.1)), 1),
            "period": 1 if self.t < HALF else 2,
            "team": team,
            "player_id": player or self.player(team, x),
            "event_type": etype,
            "outcome": outcome,
            "x": round(_clamp(x), 1),
            "y": round(_clamp(y), 1),
            "end_x": None if end_x is None else round(_clamp(end_x), 1),
            "end_y": None if end_y is None else round(_clamp(end_y), 1),
            "scenario": self.sc.key,
        })
        self.eid += 1

    # --- simulation --------------------------------------------------------
    def run(self) -> list[dict[str, Any]]:
        rng = self.rng
        goals = sorted(self.sc.scripted_goals)
        team, x, y = "HOME", 50.0, 50.0
        self.emit(team, "kickoff", "success", 50, 50, player=f"{team[0]}09")
        fast = False
        fast_started = 0.0
        second_half_started = False

        while self.t < FULL - 5:
            if not second_half_started and self.t >= HALF:
                second_half_started = True
                self.t = float(HALF)
                team, x, y, fast = "AWAY", 50.0, 50.0, False
                self.emit(team, "kickoff", "success", 50, 50, player=f"{team[0]}09")
                self.t += 2
                continue
            if goals and self.t >= goals[0][0] * 60:
                _, gteam = goals.pop(0)
                team = self._scripted_goal(gteam)
                x, y, fast = 50.0, 50.0, False
                continue

            m, d = self.mods(team), self.mods(self.other(team))
            if fast and self.t - fast_started > 20:
                fast = False

            # Shot decision in the attacking zone
            if x >= 72:
                shot_p = (0.015 + 0.26 * ((x - 72) / 28) ** 2) * m["shot"]
                if fast:
                    shot_p *= 1.6
                if rng.random() < shot_p:
                    team, x, y = self._shot(team, x, y, m)
                    fast = False
                    continue

            # Defending team may press the action
            pressed = False
            press_p = d["press"] if x < 50 else 0.10
            if rng.random() < press_p:
                pressed = True
                self.emit(self.other(team), "pressure", "none", 100 - x, 100 - y, dt=-0.4)

            fail_p = (0.07 + 0.16 * x / 100) * m["security"]
            if pressed:
                fail_p *= 1 + d["press_win"]
            if fast:
                fail_p *= 0.6
            fail_p = min(fail_p, 0.85)

            etype = "pass" if rng.random() < 0.78 else "carry"
            dx = rng.gauss(17, 5) if fast else rng.gauss(m["progress"], 9)
            if etype == "carry":
                dx *= 0.6
            ex = _clamp(x + dx, 2, 97)
            ey = _clamp(y + rng.gauss(0, 14), 2, 98)
            tempo = rng.gauss(2.2, 0.5) if fast else rng.gauss(m["tempo"], 1.1)

            if rng.random() < fail_p:
                lost_x = _clamp(x + dx * rng.uniform(0.2, 0.8), 1, 99)
                self.emit(team, etype, "fail", x, y, lost_x, ey)
                self.t += max(1.0, tempo)
                winner = self.other(team)
                wx, wy = 100 - lost_x, 100 - ey
                win_type = "interception" if etype == "pass" else "tackle"
                self.emit(winner, win_type, "success", wx, wy)
                team, x, y = winner, wx, wy
                self.t += 1.5
                # Deep clearance under pressure
                if x < 18 and rng.random() < 0.3:
                    self.emit(team, "clearance", "success", x, y, 55, rng.uniform(10, 90))
                    self.t += 3
                    contester = team if rng.random() < 0.5 else self.other(team)
                    cx = 55 if contester == team else 45
                    self.emit(contester, "ball_recovery", "success", cx, rng.uniform(10, 90))
                    team, x = contester, cx
                    self.t += 1.5
                fast = x < 50 and rng.random() < self.mods(team)["counter"]
                fast_started = self.t
            else:
                self.emit(team, etype, "success", x, y, ex, ey)
                x, y = ex, ey
                self.t += max(1.0, tempo)
        return self.events

    def _shot(self, team: str, x: float, y: float, m: dict[str, float]) -> tuple[str, float, float]:
        rng = self.rng
        p_goal = min(0.3, 0.025 + 0.10 * max(0.0, (x - 75) / 25)) * m["finish"]
        if not self.sc.random_goals:
            p_goal = 0.0
        r = rng.random()
        if r < p_goal:
            self.emit(team, "shot", "goal", x, y, 100, 50)
            self.t += 45
            conceding = self.other(team)
            self.emit(conceding, "kickoff", "success", 50, 50, player=f"{conceding[0]}09")
            self.t += 2
            return conceding, 50.0, 50.0
        outcome = rng.choices(["saved", "off_target", "blocked"], [0.35, 0.4, 0.25])[0]
        self.emit(team, "shot", outcome, x, y, 100, 50)
        self.t += 3
        opp = self.other(team)
        if outcome == "blocked":
            bx = 100 - x + rng.uniform(0, 6)
            self.emit(opp, "ball_recovery", "success", bx, 100 - y)
            self.t += 2
            return opp, bx, 100 - y
        self.t += 15 if outcome == "off_target" else 4
        self.emit(opp, "ball_recovery", "success", 6, 50, player=f"{opp[0]}01")
        self.t += 2
        return opp, 6.0, 50.0

    def _scripted_goal(self, team: str) -> str:
        rng = self.rng
        x, y = 58.0, rng.uniform(30, 70)
        self.emit(team, "ball_recovery", "success", x, y)
        self.t += 1.5
        for _ in range(3):
            ex = min(x + rng.uniform(8, 13), 90)
            ey = _clamp(y + rng.gauss(0, 10), 25, 75)
            self.emit(team, "pass", "success", x, y, ex, ey)
            x, y = ex, ey
            self.t += rng.uniform(1.8, 3.0)
        self.emit(team, "shot", "goal", x, y, 100, 50, player=f"{team[0]}09")
        self.t += 45
        conceding = self.other(team)
        self.emit(conceding, "kickoff", "success", 50, 50, player=f"{conceding[0]}09")
        self.t += 2
        return conceding


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def generate_match(scenario_key: str, seed: int = 7) -> tuple[list[Event], MatchMeta, list[GroundTruth]]:
    """Generate one synthetic match. Same (scenario, seed) always yields identical events."""
    if scenario_key not in SCENARIOS:
        raise KeyError(f"Unknown scenario '{scenario_key}'. Options: {sorted(SCENARIOS)}")
    sc = SCENARIOS[scenario_key]
    match_id = f"SYN-{scenario_key.upper()}-{seed}"
    raw = _Sim(sc, seed, match_id).run()
    events = normalize_events([Event(**r) for r in raw])
    meta = MatchMeta(
        match_id=match_id, home="HOME", away="AWAY",
        home_name=TEAM_NAMES["HOME"], away_name=TEAM_NAMES["AWAY"],
        home_short=TEAM_SHORT["HOME"], away_short=TEAM_SHORT["AWAY"],
        scenario=scenario_key, seed=seed, synthetic=True,
    )
    return events, meta, list(sc.ground_truth)


def scenario_catalog() -> list[dict[str, Any]]:
    return [
        {"key": s.key, "title": s.title, "title_id": s.title_id, "description": s.description,
         "ground_truth": [g.to_dict() for g in s.ground_truth]}
        for s in SCENARIOS.values()
    ]
