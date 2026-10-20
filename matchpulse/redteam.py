"""Red-team suite for the Evidence & Quality Reviewer.

Each fault type rewrites ONE verified claim the way a careless or hallucinating language
model might. The reviewer then checks the corrupted claim exactly as it does in production
(evidence check + comparison with the verified template claim). We report, per fault
type, how many corrupted claims were caught, and the false-alarm rate on faithful
paraphrases that change wording but not facts.

`qualitative_distortion` is included on purpose as a fault the reviewer is NOT designed
to catch (it changes meaning without touching numbers, teams, hedges or sentence count),
so the report shows the limits of the approach, not only its strengths.
"""

from __future__ import annotations

import random
import re
from dataclasses import replace
from typing import Any, Callable, Iterable, Optional

from .analytics import analyse
from .narrative import LANGS, MODES, Claim, build_evidence, render
from .reviewer import NUM_RE, PAIR_RES, check_against_reference, check_claim
from .shifts import detect_shifts
from .synth import SCENARIOS, generate_match

Fault = Callable[[str, dict[str, Any], random.Random], Optional[str]]


def _num_tokens(text: str) -> list[re.Match]:
    return list(NUM_RE.finditer(text))


def _swap_token(text: str, m: re.Match, new: str) -> str:
    return text[:m.start()] + new + text[m.end():]


def wrong_number(text, ctx, rng):
    toks = _num_tokens(text)
    if not toks:
        return None
    m = rng.choice(toks)
    raw = m.group()
    sep = "," if "," in raw else "."
    val = float(raw.replace(",", "."))
    new = val + rng.choice([3, 5, 7, 12])
    s = f"{new:.1f}".replace(".", sep) if sep in raw else str(int(new))
    return _swap_token(text, m, s)


def off_by_one(text, ctx, rng):
    toks = [m for m in _num_tokens(text) if not any(c in m.group() for c in ".,")]
    if not toks:
        return None
    m = rng.choice(toks)
    return _swap_token(text, m, str(int(m.group()) + rng.choice([-1, 1])))


def team_swap(text, ctx, rng):
    a, b = ctx["home"], ctx["away"]
    if a in text and b in text:
        return text.replace(a, "\0").replace(b, a).replace("\0", b)
    if a in text:
        return text.replace(a, b)
    if b in text:
        return text.replace(b, a)
    return None


def reversed_comparison(text, ctx, rng):
    for rx in PAIR_RES:
        m = rx.search(text)
        if m and m.group(1) != m.group(2):
            seg = m.group(0)
            flipped = seg[:m.start(1) - m.start()] + m.group(2) + seg[m.end(1) - m.start():m.start(2) - m.start()] \
                + m.group(1) + seg[m.end(2) - m.start():]
            return text[:m.start()] + flipped + text[m.end():]
    return None


CAUSAL_SWAPS = [("consistent with", "caused by"), ("coincided with", "caused"), ("Consistent with", "Caused by"),
                ("konsisten dengan", "disebabkan oleh"), ("Konsisten dengan", "Disebabkan oleh"),
                ("bersamaan dengan", "akibat dari")]


def causal_claim(text, ctx, rng):
    for a, b in CAUSAL_SWAPS:
        if a in text:
            return text.replace(a, b, 1)
    return None


def overclaim(text, ctx, rng):
    if ctx["lang"] == "en":
        return "This proves that " + text[0].lower() + text[1:]
    return "Ini membuktikan bahwa " + text[0].lower() + text[1:]


HEDGE_CUTS = [r";? ?association, not proof", r",? but are not proven to be caused by it", r";? ?ini asosiasi, bukan bukti",
              r",? namun tidak terbukti disebabkan olehnya", r"^Consistent with ", r"^Konsisten dengan ",
              r"coincided with", r"bersamaan dengan"]
HEDGE_REPL = {"coincided with": "came from", "bersamaan dengan": "berasal dari"}


def hedge_removal(text, ctx, rng):
    """Strip every hedge so the claim reads as an unqualified statement of fact."""
    new = text
    for pat in HEDGE_CUTS:
        new = re.sub(pat, HEDGE_REPL.get(pat, ""), new).strip()
    if not new or new == text:
        return None
    return new[0].upper() + new[1:]


def fabricated_sentence(text, ctx, rng):
    team = rng.choice([ctx["home"], ctx["away"]])
    extra = (f" {team}'s captain was substituted at half-time." if ctx["lang"] == "en"
             else f" Kapten {team} diganti saat jeda.")
    return text.rstrip() + extra


def qualitative_distortion(text, ctx, rng):
    """Meaning-changing but number-, team- and hedge-preserving. Expected to ESCAPE the reviewer."""
    if ctx["lang"] == "en":
        return "Only marginally, " + text[0].lower() + text[1:]
    return "Hanya tipis, " + text[0].lower() + text[1:]


FAULTS: dict[str, Fault] = {
    "wrong_number": wrong_number,
    "off_by_one": off_by_one,
    "team_swap": team_swap,
    "reversed_comparison": reversed_comparison,
    "causal_claim": causal_claim,
    "overclaim": overclaim,
    "hedge_removal": hedge_removal,
    "fabricated_sentence": fabricated_sentence,
    "qualitative_distortion": qualitative_distortion,
}
EXPECTED_TO_ESCAPE = {"qualitative_distortion"}

PARAPHRASES = {
    "en": [("made", "produced"), ("pressed high", "pressed high up the pitch"), ("Final score", "Full-time score"),
           ("launched", "put together"), ("camped in", "spent long spells in"), ("—", "-"),
           ("Long spells near goal usually mean more chances", "More time near goal tends to bring more chances"),
           ("Evidence strength", "Strength of evidence"), ("the rest of the match", "the remainder of the match")],
    "id": [("mencatat", "membukukan"), ("Skor akhir", "Hasil akhir"), ("melancarkan", "melepaskan"), ("—", "-"),
           ("Kekuatan bukti", "Tingkat kekuatan bukti"), ("sisa pertandingan", "sisa laga")],
}


def paraphrase(text: str, lang: str) -> Optional[str]:
    new = text
    for a, b in PARAPHRASES[lang]:
        new = new.replace(a, b)
    return new if new != text else None


def _caught(claim: Claim, ref: Claim, reg, names) -> bool:
    return bool(check_claim(claim, reg, names, set()) + check_against_reference(claim, ref, names, reg))


def run_redteam(seeds: Iterable[int], until_options=(None, 60), seed_rng: int = 2026) -> dict[str, Any]:
    rng = random.Random(seed_rng)
    stats = {k: {"applied": 0, "caught": 0, "examples_escaped": []} for k in FAULTS}
    para = {"applied": 0, "false_alarms": 0, "examples": []}
    for key in SCENARIOS:
        for s in seeds:
            events, meta, _ = generate_match(key, s)
            names = {meta.short(meta.home), meta.short(meta.away), meta.home_name, meta.away_name}
            for until in until_options:
                an = analyse(events, meta, 5, until)
                ms = detect_shifts(an)
                reg = build_evidence(an, ms)
                for mode in MODES:
                    for lang in LANGS:
                        ref_n = render(mode, lang, an, ms)
                        ctx = {"home": meta.short(meta.home), "away": meta.short(meta.away), "lang": lang}
                        for ref in ref_n.all_claims():
                            for name, fault in FAULTS.items():
                                bad_text = fault(ref.text, ctx, rng)
                                if not bad_text or bad_text == ref.text:
                                    continue
                                st = stats[name]
                                st["applied"] += 1
                                if _caught(replace(ref, text=bad_text), ref, reg, names):
                                    st["caught"] += 1
                                elif len(st["examples_escaped"]) < 3:
                                    st["examples_escaped"].append(bad_text[:160])
                            p = paraphrase(ref.text, lang)
                            if p:
                                para["applied"] += 1
                                if _caught(replace(ref, text=p), ref, reg, names):
                                    para["false_alarms"] += 1
                                    if len(para["examples"]) < 3:
                                        para["examples"].append(p[:160])
    for st in stats.values():
        st["catch_rate"] = round(st["caught"] / st["applied"], 4) if st["applied"] else None
    designed = [k for k in FAULTS if k not in EXPECTED_TO_ESCAPE]
    tot_a = sum(stats[k]["applied"] for k in designed)
    tot_c = sum(stats[k]["caught"] for k in designed)
    return {
        "faults": stats,
        "designed_fault_types": designed,
        "expected_to_escape": sorted(EXPECTED_TO_ESCAPE),
        "overall_designed": {"applied": tot_a, "caught": tot_c, "catch_rate": round(tot_c / tot_a, 4) if tot_a else None},
        "faithful_paraphrases": dict(para, false_alarm_rate=round(para["false_alarms"] / para["applied"], 4)
                                     if para["applied"] else None),
    }
