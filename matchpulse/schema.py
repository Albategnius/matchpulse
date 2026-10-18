"""Event schema, validation and normalization.

Coordinate convention (documented in docs/METRICS.md):
  * x, y, end_x, end_y are normalized to 0..100.
  * Every event is recorded in the *acting team's* frame: that team attacks toward x = 100.
  * So a pressure event by team B at x = 75 happened in B's attacking half (a high press).
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from typing import Any, Iterable, Literal, Optional

from pydantic import BaseModel, Field, ValidationError, field_validator

MATCH_SECONDS = 90 * 60

ON_BALL_TYPES = {"pass", "carry", "shot"}
DEFENSIVE_TYPES = {"pressure", "tackle", "interception", "ball_recovery", "clearance", "foul"}
EVENT_TYPES = ON_BALL_TYPES | DEFENSIVE_TYPES | {"kickoff"}
OUTCOMES = {"success", "fail", "goal", "saved", "off_target", "blocked", "none"}

CSV_COLUMNS = [
    "match_id", "event_id", "timestamp_s", "period", "team", "player_id", "event_type",
    "outcome", "x", "y", "end_x", "end_y", "possession_id", "possession_team", "scenario",
]


class Event(BaseModel):
    match_id: str
    event_id: int = Field(ge=0)
    timestamp_s: float = Field(ge=0, le=MATCH_SECONDS + 600)
    period: int = Field(ge=1, le=2)
    team: str = Field(min_length=1)
    player_id: str = Field(min_length=1)
    event_type: str
    outcome: str = "none"
    x: float = Field(ge=0, le=100)
    y: float = Field(ge=0, le=100)
    end_x: Optional[float] = Field(default=None, ge=0, le=100)
    end_y: Optional[float] = Field(default=None, ge=0, le=100)
    possession_id: Optional[int] = None
    possession_team: Optional[str] = None
    scenario: Optional[str] = None

    @field_validator("event_type")
    @classmethod
    def _known_type(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in EVENT_TYPES:
            raise ValueError(f"unknown event_type '{v}'")
        return v

    @field_validator("outcome")
    @classmethod
    def _known_outcome(cls, v: str) -> str:
        v = (v or "none").strip().lower()
        if v not in OUTCOMES:
            raise ValueError(f"unknown outcome '{v}'")
        return v

    @property
    def minute(self) -> float:
        return self.timestamp_s / 60.0


class MatchMeta(BaseModel):
    match_id: str
    home: str = "HOME"
    away: str = "AWAY"
    home_name: str = "Home"
    away_name: str = "Away"
    home_short: Optional[str] = None
    away_short: Optional[str] = None
    scenario: Optional[str] = None
    seed: Optional[int] = None
    synthetic: bool = True

    def short(self, team: Optional[str]) -> str:
        if team == self.home:
            return self.home_short or self.home_name
        if team == self.away:
            return self.away_short or self.away_name
        return ""


@dataclass
class ValidationReport:
    total_rows: int = 0
    valid_rows: int = 0
    dropped: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    fatal: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.fatal is None

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_rows": self.total_rows,
            "valid_rows": self.valid_rows,
            "dropped_count": len(self.dropped),
            "dropped_sample": self.dropped[:10],
            "warnings": self.warnings,
            "fatal": self.fatal,
            "ok": self.ok,
        }


class MatchDataError(ValueError):
    """Raised when a dataset cannot be analysed safely."""

    def __init__(self, message: str, report: Optional[ValidationReport] = None):
        super().__init__(message)
        self.report = report


def _clean(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        s = value.strip()
        return None if s == "" or s.lower() in {"nan", "null", "none"} else s
    return value


def validate_events(
    rows: Iterable[dict[str, Any]], meta: Optional[MatchMeta] = None
) -> tuple[list[Event], MatchMeta, ValidationReport]:
    """Validate raw rows, drop invalid ones with reasons, and normalize the rest.

    Fatal problems (raise MatchDataError): no valid events, more than two teams,
    teams inconsistent with metadata, more than one match_id.
    """
    report = ValidationReport()
    events: list[Event] = []
    for i, raw in enumerate(rows):
        report.total_rows += 1
        row = {k: _clean(v) for k, v in raw.items() if k in CSV_COLUMNS}
        if row.get("outcome") is None:
            row["outcome"] = "none"
        try:
            events.append(Event(**row))
        except ValidationError as exc:
            first = exc.errors()[0]
            report.dropped.append(
                {"row": i, "field": ".".join(str(p) for p in first.get("loc", [])), "reason": first.get("msg")}
            )

    if not events:
        report.fatal = "No valid events after validation."
        raise MatchDataError(report.fatal, report)

    match_ids = {e.match_id for e in events}
    if len(match_ids) > 1:
        report.fatal = f"Dataset contains {len(match_ids)} match_ids; upload one match at a time."
        raise MatchDataError(report.fatal, report)

    teams = sorted({e.team for e in events})
    if len(teams) != 2:
        report.fatal = f"Expected exactly 2 teams, found {len(teams)}: {teams}."
        raise MatchDataError(report.fatal, report)

    if meta is None:
        meta = MatchMeta(match_id=events[0].match_id, home=teams[0], away=teams[1],
                         home_name=teams[0], away_name=teams[1], scenario=events[0].scenario)
    elif set(teams) != {meta.home, meta.away}:
        report.fatal = f"Teams in events {teams} do not match metadata {[meta.home, meta.away]}."
        raise MatchDataError(report.fatal, report)

    events = normalize_events(events, report)
    report.valid_rows = len(events)

    if events[-1].timestamp_s < 45 * 60:
        report.warnings.append("Events end before half-time; second-half analysis will be empty.")
    gaps = [b.timestamp_s - a.timestamp_s for a, b in zip(events, events[1:])]
    if gaps and max(gaps) > 300:
        report.warnings.append(
            f"Largest gap between events is {max(gaps) / 60:.1f} min — some windows may be empty (missing events)."
        )
    return events, meta, report


def normalize_events(events: list[Event], report: Optional[ValidationReport] = None) -> list[Event]:
    """Sort by time, remove duplicate event ids, fix period, and derive possession.

    Possession rule (deterministic): possession belongs to the team of the latest
    on-ball event (pass, carry, shot), or to a team that wins the ball with a
    successful tackle / interception / ball_recovery. Pressure, clearance and foul
    events do not change possession. A new possession_id starts whenever the
    possessing team changes, and after every goal.
    """
    seen: set[int] = set()
    unique: list[Event] = []
    for e in sorted(events, key=lambda ev: (ev.timestamp_s, ev.event_id)):
        if e.event_id in seen:
            if report is not None:
                report.warnings.append(f"Duplicate event_id {e.event_id} removed.")
            continue
        seen.add(e.event_id)
        unique.append(e)

    pos_id = 0
    pos_team: Optional[str] = None
    out: list[Event] = []
    for e in unique:
        period = 1 if e.timestamp_s < 45 * 60 else 2
        gains = e.event_type in ON_BALL_TYPES or e.event_type == "kickoff" or (
            e.event_type in {"tackle", "interception", "ball_recovery"} and e.outcome == "success"
        )
        if gains and e.team != pos_team:
            pos_id += 1
            pos_team = e.team
        out.append(e.model_copy(update={"period": period, "possession_id": pos_id, "possession_team": pos_team}))
        if e.event_type == "shot" and e.outcome == "goal":
            pos_team = None  # restart: next kickoff opens a new possession
    return out


def events_to_csv(events: list[Event]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=CSV_COLUMNS)
    writer.writeheader()
    for e in events:
        writer.writerow({k: ("" if v is None else v) for k, v in e.model_dump().items()})
    return buf.getvalue()


def csv_to_rows(text: str) -> list[dict[str, Any]]:
    text = "\n".join(line for line in text.lstrip("﻿").splitlines() if not line.startswith("#"))
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        raise MatchDataError("CSV has no header row.")
    missing = {"match_id", "event_id", "timestamp_s", "team", "player_id", "event_type", "x", "y"} - set(
        reader.fieldnames
    )
    if missing:
        raise MatchDataError(f"CSV is missing required columns: {sorted(missing)}")
    rows = []
    for r in reader:
        if not r.get("period"):
            try:
                r["period"] = "1" if float(r.get("timestamp_s") or 0) < 2700 else "2"
            except ValueError:
                r["period"] = "1"
        rows.append(r)
    return rows
