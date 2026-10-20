# Metric definitions

All coordinates are 0–100 and recorded in the **acting team's frame** (that team attacks towards x = 100). Constants live in `matchpulse/analytics.py` and `matchpulse/shifts.py`.

## Event-level flags

| Flag | Formula |
|---|---|
| Progressive action | successful pass/carry with `end_x − x ≥ 10` and `end_x ≥ 40` |
| Final-third entry | successful pass/carry with `x < 66.67 ≤ end_x` |
| Turnover | pass or carry with outcome `fail` |
| High pressure | pressure event with `x ≥ 50` (opponent half) |
| Press-linked turnover | turnover within 5 s after an opponent pressure located within 15 units (x) and 20 units (y) of the ball, after converting frames (`100 − x`, `100 − y`) |
| High turnover forced (credited to opponent) | turnover at `x < 50` in the losing team's frame |
| Fast break | possession whose first event is at `x < 50` (not a kickoff) and reaches `x ≥ 66.67` (event location or successful end location) within 15 s |

## Possession

Possession belongs to the team of the latest pass, carry or shot, or to a team winning the ball with a successful tackle, interception or ball recovery. Pressure, clearance and foul events do not change possession. A goal ends the possession. Possession % = share of match seconds between possession starts.

## Totals and windows

Counts per team: shots, shots on target (`goal` + `saved`), goals, passes, completed passes, pass accuracy (`completed / attempted`), progressive actions, final-third entries, pressures, high pressures, turnovers, high turnovers forced, press-linked turnovers, fast breaks. The same counts are computed in tumbling windows (default 5 minutes, configurable 1–15 in the API).

## Momentum index

Attacking Threat Units (ATU) per team and window: `final-third entries + 3 × shots + high turnovers forced`.
Momentum (home view) = `(ATU_home − ATU_away) / (ATU_home + ATU_away)`, in [−1, 1], 0 if both are zero. A 3-window trailing mean is also reported. This is a descriptive share, not a predictive model.

## Match state

After each goal, each team's state is `leading`, `level` or `trailing`; transitions are listed with minute and score.

## Detectors (defaults, `DetectorConfig`)

Series are smoothed with a centered 3-window mean. Flagged windows are merged into segments, bridging one-window gaps.

| Pattern | Rule | Baseline |
|---|---|---|
| Dominance | mean(completed-pass share, final-third-entry share) ≥ 0.66 and FTE share ≥ 0.60, for ≥ 3 windows | rest of match |
| High press | high pressures ≥ 6.5 per window, press-linked turnovers ≥ 2.0 per window, and ≥ 1.8 × the team's match median, for ≥ 2 windows | rest of match |
| Counterattack | fast breaks ≥ 1.3 per window for ≥ 2 windows and ≥ 5 fast breaks in the segment | rest of match |
| Momentum swing | a control spell (|momentum| ≥ 0.45 for ≥ 4 windows) for one team followed within 25 min by a spell for the other | first spell vs. second spell |
| Late comeback | team trailing at or after 65' that finishes ahead, or recovers a ≥ 2-goal deficit to draw | score at 65' |
| Low-event match | total shots ≤ 12 | fixed threshold |

Evidence strength (0–1) combines the size of the effect and its duration; it is a ranking aid, not a probability.

## Live replay

`analyse(events, meta, window_min, until_min)` keeps only events with `timestamp < until_min × 60` and only complete windows; totals include every event before the cut-off. A detected moment whose window reaches the cut-off is marked `ongoing` and its end is clamped to the cut-off. `low_event` is evaluated only at full time. Time-to-flag in `reports/EVALUATION.md` replays held-out matches in 5-minute steps.

## Not computed

xG, ball or player speed, distance covered, pressing intensity from tracking, or any metric needing positional tracking data.
