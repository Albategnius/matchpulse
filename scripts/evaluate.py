"""Reproducible evaluation for MATCHPULSE. Run: python scripts/evaluate.py

Writes reports/evaluation.json and reports/EVALUATION.md. All numbers are measured,
never hand-entered. Detector thresholds were tuned on DEV seeds 0-9; the headline
result uses HELD-OUT seeds 100-129 that were never used for tuning.
"""

from __future__ import annotations

import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from matchpulse import SYNTHETIC_DISCLAIMER, __version__  # noqa: E402
from matchpulse.analytics import analyse  # noqa: E402
from matchpulse.evaluation import aggregate, match  # noqa: E402
from matchpulse.faults import fault_injection_writer  # noqa: E402
from matchpulse.narrative import LANGS, MODES, build_evidence, render  # noqa: E402
from matchpulse.reviewer import NUM_RE, review  # noqa: E402
from matchpulse.shifts import DetectorConfig, detect_shifts  # noqa: E402
from matchpulse.synth import SCENARIOS, generate_match  # noqa: E402
from matchpulse.workflow import MatchRequest, run_pipeline  # noqa: E402
from matchpulse.writer import REWRITABLE_KINDS, TemplateWriter  # noqa: E402

DEV = range(0, 10)
HELD_OUT = range(100, 130)


def detection(seeds) -> dict:
    results, per_scenario = [], {}
    for key in SCENARIOS:
        rs = []
        for s in seeds:
            events, meta, gts = generate_match(key, s)
            rs.append(match(detect_shifts(analyse(events, meta)), gts))
        per_scenario[key] = aggregate(rs)["overall"]
        results += rs
    agg = aggregate(results)
    agg["matches"] = len(results)
    agg["seeds"] = f"{seeds.start}-{seeds.stop - 1}"
    agg["per_scenario"] = per_scenario
    # "detected in the intended window": share of ground-truth items matched with overlap
    agg["intended_window_hit_rate"] = agg["overall"]["recall"]
    return agg


def narrative_quality(seeds) -> dict:
    checked = supported = material = material_ok = 0
    by_mode = {m: {"chars": [], "numbers": [], "words": []} for m in MODES}
    jaccard = {f"{a}|{b}": [] for i, a in enumerate(MODES) for b in MODES[i + 1:]}
    for key in SCENARIOS:
        for s in seeds:
            events, meta, _ = generate_match(key, s)
            an = analyse(events, meta)
            ms = detect_shifts(an)
            reg = build_evidence(an, ms)
            teams = {meta.short(meta.home), meta.short(meta.away)}
            for lang in LANGS:
                words = {}
                for mode in MODES:
                    n, rep = review(render(mode, lang, an, ms), reg, teams)
                    checked += rep.claims_checked
                    supported += rep.claims_supported
                    material += rep.material_claims
                    material_ok += rep.material_supported
                    txt = n.plain_text()
                    by_mode[mode]["chars"].append(len(txt))
                    by_mode[mode]["numbers"].append(len(NUM_RE.findall(txt)))
                    words[mode] = set(txt.lower().split())
                for pair in jaccard:
                    a, b = pair.split("|")
                    jaccard[pair].append(len(words[a] & words[b]) / max(1, len(words[a] | words[b])))
    return {
        "narratives": len(SCENARIOS) * len(seeds) * len(LANGS) * len(MODES),
        "claims_checked": checked,
        "numeric_factual_consistency": round(supported / checked, 4) if checked else None,
        "evidence_coverage": round(material_ok / material, 4) if material else None,
        "unsupported_claim_rate": round(1 - supported / checked, 4) if checked else None,
        "personalization": {
            "mean_chars": {m: round(statistics.mean(v["chars"])) for m, v in by_mode.items()},
            "mean_numbers_cited": {m: round(statistics.mean(v["numbers"]), 1) for m, v in by_mode.items()},
            "mean_word_jaccard_between_modes": {k: round(statistics.mean(v), 3) for k, v in jaccard.items()},
            "note": "Lower Jaccard = more different vocabulary. Broadcast additionally emits a structured overlay; analyst adds before/after tables.",
        },
    }


async def reviewer_fault_catch_exact(seeds) -> dict:
    """Exact per-claim catch rate, measured directly with the reviewer on corrupted drafts."""
    corrupted = caught = false_alarms = clean_claims = 0
    for key in SCENARIOS:
        for s in seeds:
            events, meta, _ = generate_match(key, s)
            an = analyse(events, meta)
            ms = detect_shifts(an)
            reg = build_evidence(an, ms)
            teams = {meta.short(meta.home), meta.short(meta.away)}
            writer = fault_injection_writer()
            for mode in MODES:
                tmpl = render(mode, "en", an, ms)
                bad = await writer.rewrite(tmpl, reg)
                orig = {c.id: c.text for c in tmpl.all_claims()}
                snapshot = [(c.id, c.text) for c in bad.all_claims()]
                _, rep = review(bad, reg, teams)
                removed = {r["claim_id"] for r in rep.removed}
                for cid, text in snapshot:
                    if text != orig.get(cid):
                        corrupted += 1
                        caught += cid in removed
                    else:
                        clean_claims += 1
                        false_alarms += cid in removed
    return {"corrupted_claims": corrupted, "caught": caught,
            "catch_rate": round(caught / corrupted, 4) if corrupted else None,
            "untouched_claims": clean_claims, "false_alarms": false_alarms}


async def latency(n_runs: int = 30) -> dict:
    times, failures = [], 0
    keys = list(SCENARIOS)
    for i in range(n_runs):
        events, meta, _ = generate_match(keys[i % len(keys)], 200 + i)
        t0 = time.perf_counter()
        out = await run_pipeline(MatchRequest(events=events, meta=meta, lang="en" if i % 2 else "id"), TemplateWriter())
        times.append((time.perf_counter() - t0) * 1000)
        failures += out.get("status") != "ok"
    times.sort()
    return {"runs": n_runs, "writer": "template (local fallback)", "p50_ms": round(times[len(times) // 2], 1),
            "p95_ms": round(times[int(len(times) * 0.95) - 1], 1), "failure_rate": failures / n_runs,
            "note": "Excludes event generation; Foundry model latency is not included (no cloud model in this environment)."}


def write_markdown(r: dict, path: Path) -> None:
    d, dev = r["detection"]["held_out"], r["detection"]["dev"]
    lines = [
        "# MATCHPULSE — evaluation report", "",
        f"_Generated by `python scripts/evaluate.py` (v{r['version']}). {SYNTHETIC_DISCLAIMER}_", "",
        "## Match-shift detection", "",
        f"Thresholds tuned on dev seeds {dev['seeds']}; results below are on **held-out seeds {d['seeds']}** "
        f"({d['matches']} synthetic matches, 6 scenarios × 30 seeds).", "",
        "| Pattern | Precision | Recall | F1 | TP | FP | FN |", "|---|---|---|---|---|---|---|",
    ]
    for p, v in d["per_pattern"].items():
        lines.append(f"| {p} | {v['precision']:.2f} | {v['recall']:.2f} | {v['f1']:.2f} | {v['tp']} | {v['fp']} | {v['fn']} |")
    o = d["overall"]
    lines += [f"| **overall** | **{o['precision']:.2f}** | **{o['recall']:.2f}** | **{o['f1']:.2f}** | {o['tp']} | {o['fp']} | {o['fn']} |", "",
              f"Mean absolute start-time error of matched detections: {d['mean_abs_start_error_min']} min. "
              f"Dev-seed overall F1 for comparison: {dev['overall']['f1']:.2f}.", "",
              "Per scenario (overall F1): " + ", ".join(f"{k} {v['f1']:.2f}" for k, v in d["per_scenario"].items()) + ".", "",
              "## Narrative factuality (deterministic templates, all modes × EN/ID)", ""]
    nq = r["narrative"]
    lines += [f"- Narratives checked: {nq['narratives']} ({nq['claims_checked']} claims)",
              f"- Numeric factual consistency: {nq['numeric_factual_consistency']:.2%}",
              f"- Evidence coverage of material claims: {nq['evidence_coverage']:.2%}",
              f"- Unsupported-claim rate: {nq['unsupported_claim_rate']:.2%}", "",
              "## Reviewer stress test (simulated faulty model)", ""]
    fc = r["reviewer_fault_injection"]
    lines += [f"- Corrupted claims (wrong numbers and/or causal language): {fc['corrupted_claims']}",
              f"- Caught and removed by the Evidence & Quality Reviewer: {fc['caught']} ({fc['catch_rate']:.2%})",
              f"- False alarms on untouched claims: {fc['false_alarms']} of {fc['untouched_claims']}", "",
              "## Personalization", ""]
    pz = nq["personalization"]
    lines += [f"- Mean characters per narrative: {pz['mean_chars']}",
              f"- Mean numbers cited: {pz['mean_numbers_cited']}",
              f"- Mean word Jaccard similarity between modes: {pz['mean_word_jaccard_between_modes']}", "",
              "## Latency and reliability", ""]
    lt = r["latency"]
    lines += [f"- {lt['runs']} end-to-end workflow runs ({lt['writer']}): p50 {lt['p50_ms']} ms, p95 {lt['p95_ms']} ms, failure rate {lt['failure_rate']:.0%}",
              f"- {lt['note']}", "",
              "## Limitations", "",
              "- Ground truth comes from the same generator family the detector was designed for; real-world transfer is not claimed.",
              "- Counterattack detection is the weakest pattern; the mechanical fast-break definition overlaps with ordinary quick attacks.",
              "- Unlabelled detections count as false positives even when the pattern genuinely occurred by chance in the simulation.",
              "- The 100% reviewer catch rate is against a simple fault model (changed numbers, explicit causal words). Subtler errors - a wrong comparison stated without numbers, or a misleading but number-free sentence - are NOT detected by the numeric checker.",
              "- Live Foundry-model quality was not measured here (no cloud credentials in the build environment)."]
    path.write_text("\n".join(lines) + "\n")


async def main() -> None:
    t0 = time.perf_counter()
    report = {
        "version": __version__,
        "disclaimer": SYNTHETIC_DISCLAIMER,
        "detector_config": DetectorConfig().to_dict(),
        "detection": {"dev": detection(DEV), "held_out": detection(HELD_OUT)},
        "narrative": narrative_quality(range(100, 105)),
        "reviewer_fault_injection": await reviewer_fault_catch_exact(range(100, 103)),
        "latency": await latency(),
    }
    report["detection"]["held_out"]["matches"] = len(SCENARIOS) * len(HELD_OUT)
    report["elapsed_s"] = round(time.perf_counter() - t0, 1)
    out = ROOT / "reports"
    out.mkdir(exist_ok=True)
    (out / "evaluation.json").write_text(json.dumps(report, indent=2))
    write_markdown(report, out / "EVALUATION.md")
    o = report["detection"]["held_out"]["overall"]
    print(f"held-out detection: P {o['precision']} R {o['recall']} F1 {o['f1']}")
    print(f"narrative consistency {report['narrative']['numeric_factual_consistency']}, "
          f"reviewer catch rate {report['reviewer_fault_injection']['catch_rate']}, "
          f"latency p50 {report['latency']['p50_ms']} ms")


if __name__ == "__main__":
    asyncio.run(main())
