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
from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

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
NEGATED_PROOF_RE = re.compile(r"(not proven|not proof|no proof|tidak terbukti|bukan bukti)", re.IGNORECASE)
OVERCLAIM_RE = re.compile(
    r"\b(proves?|proved|proven|proof that|definitely|certainly|undoubtedly|guarantee[sd]?|"
    r"membuktikan|terbukti|bukti bahwa|pasti|tanpa diragukan|dipastikan)\b",
    re.IGNORECASE,
)
HEDGE_RE = re.compile(
    r"(consistent with|coincided with|associat|not proof|not proven|hypothes|"
    r"konsisten dengan|bersamaan dengan|asosiasi|bukan bukti|tidak terbukti|hipotesis)",
    re.IGNORECASE,
)
_NUM = r"-?\d+(?:[.,]\d+)?"
PAIR_RES = [
    re.compile(rf"from ({_NUM}) to ({_NUM})", re.IGNORECASE),
    re.compile(rf"dari ({_NUM}) menjadi ({_NUM})", re.IGNORECASE),
    re.compile(rf"({_NUM}) \((?:baseline|pembanding) ({_NUM})\)", re.IGNORECASE),
]
# Split after sentence punctuation, or before a capitalised word that follows a minute mark or a number
# (headlines have no final full stop). Counts are only ever compared with the reference claim split the same way.
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=\S)|(?<=['\d])\s+(?=[A-Z])")

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
    if OVERCLAIM_RE.search(NEGATED_PROOF_RE.sub("", claim.text)):
        problems.append("overclaims certainty (proof/definitely) beyond descriptive statistics")
    for name in other_names:
        if name and name in claim.text and name not in team_names:
            problems.append(f"mentions a team not in this match: {name}")
    return problems


def _team_sequence(text: str, names: set[str]) -> list[str]:
    """Teams in order of first mention (short names only; full names contain them)."""
    shorts = [n for n in names if n and not any(m != n and m and m in n for m in names)]
    found = sorted((text.find(n), n) for n in shorts if n in text)
    return [n for _, n in found]


def _pairs(text: str) -> set[tuple[str, str]]:
    out = set()
    for rx in PAIR_RES:
        for a, b in rx.findall(text):
            out.add((a.replace(",", "."), b.replace(",", ".")))
    return out


def _sentence_count(text: str) -> int:
    return len([x for x in SENTENCE_SPLIT_RE.split(text.strip()) if x.strip()])


def _strict_match(n: float, values: list[Any]) -> bool:
    for v in values:
        for it in (v if isinstance(v, list) else [v]):
            if isinstance(it, (int, float)) and not isinstance(it, bool):
                f = abs(float(it))
                if any(abs(n - a) < 0.051 for a in (f, round(f, 1), float(round(f)))):
                    return True
            elif any(abs(n - a) < 0.051 for a in _numbers(str(it))):
                return True
    return False


def check_against_reference(claim: Claim, ref: Claim, team_names: set[str],
                            registry: Optional[dict[str, dict[str, Any]]] = None) -> list[str]:
    """Compare a reworded claim with the verified template claim it was derived from."""
    problems = []
    if claim.text == ref.text:
        return problems
    ref_counts = Counter(round(x, 2) for x in _numbers(ref.text))
    new_counts = Counter(round(x, 2) for x in _numbers(claim.text))
    extra = new_counts - ref_counts
    repeated = [v for v in extra if v in ref_counts]
    if repeated:
        problems.append(f"number(s) {repeated} used more often than in the verified claim")
    novel = [v for v in extra if v not in ref_counts]
    if novel and registry is not None:
        cited = [registry[e]["value"] for e in claim.evidence if e in registry]
        loose = [v for v in novel if not _strict_match(v, cited)]
        if loose:
            problems.append(f"number(s) {loose} differ from the verified claim and do not exactly match cited evidence")
    ref_teams, new_teams = _team_sequence(ref.text, team_names), _team_sequence(claim.text, team_names)
    if ref_teams and new_teams and new_teams != ref_teams:
        problems.append(f"team attribution changed ({' → '.join(ref_teams)} became {' → '.join(new_teams)})")
    ref_pairs, new_pairs = _pairs(ref.text), _pairs(claim.text)
    reversed_pairs = [p for p in new_pairs if p not in ref_pairs and (p[1], p[0]) in ref_pairs]
    if reversed_pairs:
        problems.append(f"comparison reversed: {reversed_pairs}")
    if HEDGE_RE.search(ref.text) and not HEDGE_RE.search(claim.text):
        problems.append("uncertainty hedge removed from an associative claim")
    if _sentence_count(claim.text) > _sentence_count(ref.text):
        problems.append("adds sentence(s) that are not in the verified claim")
    return problems


def review(narrative: Narrative, registry: dict[str, dict[str, Any]], team_names: set[str],
           banned_names: set[str] | None = None,
           reference: Optional[Narrative] = None) -> tuple[Narrative, ReviewReport]:
    """Return a cleaned narrative (unsupported claims removed) and the review report.

    `reference` is the deterministic template narrative the draft was derived from; when
    given, every reworded claim is also compared with its source claim.
    """
    rep = ReviewReport()
    banned = banned_names or set()
    refs = {c.id: c for c in reference.all_claims()} if reference is not None else {}

    def material(c: Claim) -> bool:
        return bool(_numbers(c.text)) or c.kind == "interpretation"

    def judge(c: Claim) -> bool:
        rep.claims_checked += 1
        if material(c):
            rep.material_claims += 1
        problems = check_claim(c, registry, team_names, banned)
        if c.id in refs:
            problems += check_against_reference(c, refs[c.id], team_names, registry)
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
