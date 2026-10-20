"""Live-replay evaluation: how soon is each injected pattern flagged while the match is in progress?

For every ground-truth pattern we replay the match in `step_min` increments and record the
first replay minute at which a matching moment (same pattern, same team, >= 2.5 min overlap
with the truth window) is reported. Latency = first flagged minute - true start minute.
"""

from __future__ import annotations

import statistics
from typing import Any, Iterable, Optional

from .analytics import analyse
from .evaluation import PATTERNS, match
from .shifts import DetectorConfig, detect_shifts
from .synth import SCENARIOS, generate_match


def first_flagged(events, meta, gts, cfg: DetectorConfig, step_min: int = 5) -> list[Optional[float]]:
    first: list[Optional[float]] = [None] * len(gts)
    for until in range(step_min, 90 + step_min, step_min):
        until = min(until, 90)
        ms = detect_shifts(analyse(events, meta, 5, until), cfg)
        for j, g in enumerate(gts):
            if first[j] is not None or g.start_min >= until:
                continue
            if match(ms, [g])["tp"]:
                first[j] = float(until)
        if all(f is not None for f in first):
            break
    return first


def live_latency(seeds: Iterable[int], cfg: Optional[DetectorConfig] = None, step_min: int = 5) -> dict[str, Any]:
    cfg = cfg or DetectorConfig()
    per: dict[str, dict[str, list]] = {p: {"lat": [], "during": [], "missed": []} for p in PATTERNS}
    seeds = list(seeds)
    for key in SCENARIOS:
        for s in seeds:
            events, meta, gts = generate_match(key, s)
            for g, f in zip(gts, first_flagged(events, meta, gts, cfg, step_min)):
                d = per[g.pattern]
                if f is None:
                    d["missed"].append(1)
                    continue
                d["lat"].append(f - g.start_min)
                d["during"].append(1 if f <= g.end_min else 0)
    out: dict[str, Any] = {"step_min": step_min, "seeds": f"{seeds[0]}-{seeds[-1]}", "per_pattern": {}}
    all_lat, all_during, n_total = [], [], 0
    for p, d in per.items():
        n = len(d["lat"]) + len(d["missed"])
        if not n:
            continue
        if p != "low_event":  # whole-match judgement by definition, only possible at 90'
            n_total += n
            all_lat += d["lat"]
            all_during += d["during"]
        out["per_pattern"][p] = {
            "patterns": n,
            "flagged_live": len(d["lat"]),
            "median_latency_min": statistics.median(d["lat"]) if d["lat"] else None,
            "flagged_while_still_running": round(sum(d["during"]) / n, 3),
        }
    out["overall_excludes"] = ["low_event (whole-match judgement, only possible at full time)"]
    out["overall"] = {
        "patterns": n_total,
        "flagged_live": len(all_lat),
        "median_latency_min": statistics.median(all_lat) if all_lat else None,
        "flagged_while_still_running": round(sum(all_during) / n_total, 3) if n_total else None,
    }
    return out
