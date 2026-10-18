"""Detection evaluation against synthetic ground truth.

Matching rule: a detected moment matches a ground-truth annotation when the
pattern is identical, the team is identical (or both are None), and the time
intervals overlap by at least `min_overlap_min` minutes (or the detected moment
lies inside the annotation, for short events such as goals).
A ground-truth item may be matched by at most one detection (greedy by overlap).
"""

from __future__ import annotations

from typing import Any, Iterable

from .shifts import Moment
from .synth import GroundTruth

PATTERNS = ["dominance", "high_press", "counterattack", "momentum_swing", "late_comeback", "low_event"]


def _overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def match(moments: list[Moment], gts: list[GroundTruth], min_overlap_min: float = 2.5) -> dict[str, Any]:
    pairs = []
    for i, m in enumerate(moments):
        for j, g in enumerate(gts):
            if m.pattern != g.pattern or m.team != g.team:
                continue
            ov = _overlap(m.start_min, m.end_min, g.start_min, g.end_min)
            inside = g.start_min <= m.start_min and m.end_min <= g.end_min
            if ov >= min_overlap_min or (inside and ov > 0):
                pairs.append((ov, i, j))
    pairs.sort(reverse=True)
    used_m, used_g, matched = set(), set(), []
    for ov, i, j in pairs:
        if i in used_m or j in used_g:
            continue
        used_m.add(i)
        used_g.add(j)
        g, m = gts[j], moments[i]
        matched.append({"moment": m.id, "pattern": m.pattern, "team": m.team, "overlap_min": round(ov, 1),
                        "start_error_min": round(m.start_min - g.start_min, 1)})
    return {
        "tp": len(matched),
        "fp": [{"pattern": m.pattern, "team": m.team, "start_min": m.start_min, "end_min": m.end_min}
               for i, m in enumerate(moments) if i not in used_m],
        "fn": [g.to_dict() for j, g in enumerate(gts) if j not in used_g],
        "matched": matched,
    }


def prf(tp: int, fp: int, fn: int) -> dict[str, float]:
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return {"precision": round(p, 3), "recall": round(r, 3), "f1": round(f, 3), "tp": tp, "fp": fp, "fn": fn}


def aggregate(results: Iterable[dict[str, Any]]) -> dict[str, Any]:
    per = {p: {"tp": 0, "fp": 0, "fn": 0} for p in PATTERNS}
    start_errors: list[float] = []
    for r in results:
        for mt in r["matched"]:
            per[mt["pattern"]]["tp"] += 1
            start_errors.append(abs(mt["start_error_min"]))
        for fp in r["fp"]:
            per[fp["pattern"]]["fp"] += 1
        for fn in r["fn"]:
            per[fn["pattern"]]["fn"] += 1
    tot = {k: sum(v[k] for v in per.values()) for k in ("tp", "fp", "fn")}
    return {
        "per_pattern": {p: prf(**v) for p, v in per.items()},
        "overall": prf(**tot),
        "mean_abs_start_error_min": round(sum(start_errors) / len(start_errors), 2) if start_errors else None,
    }
