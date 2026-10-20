"""Reviewer red-team: each designed fault type is caught; faithful paraphrases are not flagged."""

import random
from dataclasses import replace

import pytest

from matchpulse.analytics import analyse
from matchpulse.narrative import build_evidence, render
from matchpulse.redteam import EXPECTED_TO_ESCAPE, FAULTS, paraphrase, run_redteam
from matchpulse.reviewer import check_against_reference, check_claim, review
from matchpulse.shifts import detect_shifts
from matchpulse.synth import generate_match


@pytest.fixture(scope="module")
def ctx():
    events, meta, _ = generate_match("momentum_swing", 7)
    an = analyse(events, meta)
    ms = detect_shifts(an)
    names = {meta.short(meta.home), meta.short(meta.away), meta.home_name, meta.away_name}
    return an, ms, build_evidence(an, ms), names, meta


def _flagged(claim, ref, reg, names):
    return check_claim(claim, reg, names, set()) + check_against_reference(claim, ref, names, reg)


def test_team_swap_with_identical_numbers_is_caught(ctx):
    an, ms, reg, names, meta = ctx
    ref = next(c for c in render("casual", "en", an, ms).all_claims() if c.id == "key.1")
    swapped = ref.text.replace("Northbridge", "\0").replace("Harbour City", "Northbridge").replace("\0", "Harbour City")
    assert any("team attribution" in p for p in _flagged(replace(ref, text=swapped), ref, reg, names))


def test_reversed_comparison_is_caught(ctx):
    an, ms, reg, names, meta = ctx
    ref = next(c for c in render("casual", "en", an, ms).all_claims() if c.id == "why.1")
    assert "from 8 to 14" in ref.text
    bad = replace(ref, text=ref.text.replace("from 8 to 14", "from 14 to 8"))
    assert any("reversed" in p for p in _flagged(bad, ref, reg, names))


def test_hedge_removal_and_overclaim_are_caught(ctx):
    an, ms, reg, names, meta = ctx
    ref = next(c for c in render("analyst", "en", an, ms).all_claims() if c.kind == "interpretation")
    unhedged = replace(ref, text=ref.text.replace("Consistent with a change", "There was a change"))
    assert any("hedge" in p for p in _flagged(unhedged, ref, reg, names))
    proof = replace(ref, text="This proves that " + ref.text[0].lower() + ref.text[1:])
    assert any("overclaim" in p for p in _flagged(proof, ref, reg, names))


def test_moved_number_is_caught(ctx):
    an, ms, reg, names, meta = ctx
    ref = next(c for c in render("casual", "en", an, ms).all_claims() if c.id == "recap.score")
    assert "1–1" in ref.text
    bad = replace(ref, text=ref.text.replace("1–1", "1–1–1"))
    assert any("more often" in p for p in _flagged(bad, ref, reg, names))


@pytest.mark.parametrize("name", [k for k in FAULTS if k not in EXPECTED_TO_ESCAPE])
def test_each_designed_fault_is_mostly_caught(name):
    r = run_redteam(range(3, 4), until_options=(None,))
    assert r["faults"][name]["applied"] > 0
    assert r["faults"][name]["catch_rate"] >= 0.85


def test_faithful_paraphrases_not_flagged(ctx):
    an, ms, reg, names, meta = ctx
    n = 0
    for mode in ("casual", "analyst", "broadcast"):
        for lang in ("en", "id"):
            for ref in render(mode, lang, an, ms).all_claims():
                p = paraphrase(ref.text, lang)
                if p:
                    n += 1
                    assert not _flagged(replace(ref, text=p), ref, reg, names), p
    assert n >= 5


def test_reference_review_keeps_templates_clean(ctx):
    an, ms, reg, names, meta = ctx
    for mode in ("casual", "analyst", "broadcast"):
        t = render(mode, "id", an, ms)
        _, rep = review(render(mode, "id", an, ms), reg, names, reference=t)
        assert rep.status == "approved" and not rep.removed
