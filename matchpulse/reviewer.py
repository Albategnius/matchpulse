"""Evidence & Quality Reviewer.

Checks every claim of a narrative against the deterministic evidence registry:
  1. Every evidence id cited by a claim must exist.
  2. Every number in the claim text must equal (after rounding) a number found in
     the values of the claim's cited evidence. Numbers with no cited evidence are
     unsupported.
  3. Causal language ("caused", "because of", "menyebabkan", ...) is not allowed —
     the analytics establish association only.
  4. Team names mentioned must be the two teams of this match.
Unsupported claims are removed. If too much is removed, or the headline fails,
the review is REJECTED and the workflow regenerates (bounded) or falls back.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from .narrative import Claim, Narrative

NUM_RE = re.compile(r"(?<![A-Za-z])-?\d+(?:[.,]\d+)?")
CAUSAL_RE = re.compile(
    r"\b(caused|causes|causing|because of|led to|leads to|resulted in|results in|due to|"
    r"menyebabkan|disebabkan oleh(?! tekanan.*tidak)|mengakibatkan|akibat dari|karena)\b",
    re.IGNORECASE,
)
# Negated forms are the *required* hedges, not causal claims.
NEGATED_CAUSAL_RE = re.compile(
    r"(not (proven|proof)[^.]*caused|are not proven to be caused|tidak terbukti disebabkan|bukan bukti bahwa)",
    re.IGNORECASE,
)

MAX_REMOVED_SHARE = 0.3


@dataclass
class ReviewReport:
    status: str = "approved"               # approved | approved_with_removals | rejected
    claims_checked: int = 0
    claims_supported: int = 0
    material_claims: int = 0               # claims containing numbers or interpretations
    material_supported: int = 0
    removed: list[dict[str, Any]] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    @property
    def evidence_coverage(self) -> float:
        return round(self.material_supported / self.material_claims, 3) if self.material_claims else 1.0

    @property
    def unsupported_rate(self) -> float:
        return round(len(self.removed) / self.claims_checked, 3) if self.claims_checked else 0.0

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["evidence_coverage"] = self.evidence_coverage
        d["unsupported_rate"] = self.unsupported_rate
        return d


def _numbers(text: str) -> list[float]:
    out = []
    for tok in NUM_RE.findall(text):
        try:
            out.append(abs(float(tok.replace(",", "."))))
        except ValueError:
            continue
    return out


def _allowed(values: list[Any]) -> set[float]:
    allowed: set[float] = set()
    for v in values:
        items = v if isinstance(v, list) else [v]
        for it in items:
            if isinstance(it, (int, float)) and not isinstance(it, bool):
                f = abs(float(it))
                allowed |= {f, round(f), round(f, 1), round(f, 2), float(int(f))}
            else:
                allowed |= set(_numbers(str(it)))
    return allowed


def check_claim(claim: Claim, registry: dict[str, dict[str, Any]], team_names: set[str],
                other_names: set[str]) -> list[str]:
    problems = []
    missing = [e for e in claim.evidence if e not in registry]
    if missing:
        problems.append(f"unknown evidence id(s): {missing}")
    nums = _numbers(claim.text)
    if nums:
        if not claim.evidence:
            problems.append(f"numbers {nums} without cited evidence")
        else:
            allowed = _allowed([registry[e]["value"] for e in claim.evidence if e in registry])
            bad = [n for n in nums if not any(abs(n - a) < 0.051 for a in allowed)]
            if bad:
                problems.append(f"number(s) {bad} not supported by cited evidence")
    text_wo_hedges = NEGATED_CAUSAL_RE.sub("", claim.text)
    if CAUSAL_RE.search(text_wo_hedges):
        problems.append("causal language not supported by association-only analytics")
    for name in other_names:
        if name and name in claim.text and name not in team_names:
            problems.append(f"mentions a team not in this match: {name}")
    return problems


def review(narrative: Narrative, registry: dict[str, dict[str, Any]], team_names: set[str],
           banned_names: set[str] | None = None) -> tuple[Narrative, ReviewReport]:
    """Return a cleaned narrative (unsupported claims removed) and the review report."""
    rep = ReviewReport()
    banned = banned_names or set()

    def material(c: Claim) -> bool:
        return bool(_numbers(c.text)) or c.kind == "interpretation"

    def judge(c: Claim) -> bool:
        rep.claims_checked += 1
        if material(c):
            rep.material_claims += 1
        problems = check_claim(c, registry, team_names, banned)
        if problems:
            rep.removed.append({"claim_id": c.id, "text": c.text, "problems": problems})
            return False
        rep.claims_supported += 1
        if material(c):
            rep.material_supported += 1
        return True

    headline_ok = judge(narrative.headline)
    for b in narrative.blocks:
        b.claims = [c for c in b.claims if judge(c)]
    narrative.overlay = [o for o in narrative.overlay
                         if not CAUSAL_RE.search(NEGATED_CAUSAL_RE.sub("", o.get("text", "")))]

    if not headline_ok:
        rep.status = "rejected"
        rep.reasons.append("headline failed verification")
    elif rep.claims_checked and len(rep.removed) / rep.claims_checked > MAX_REMOVED_SHARE:
        rep.status = "rejected"
        rep.reasons.append(f"more than {int(MAX_REMOVED_SHARE * 100)}% of claims were unsupported")
    elif rep.removed:
        rep.status = "approved_with_removals"
    return narrative, rep
