"""Evidence-linked narratives for three audiences (casual, analyst, broadcast) in
English and Bahasa Indonesia.

Every sentence is a Claim that lists the evidence ids it relies on. The evidence
registry is built only from deterministic analytics, so the Quality Reviewer can
check every number in every sentence against its source.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from .analytics import MatchAnalytics
from .schema import MatchMeta
from .shifts import Moment

MODES = ("casual", "analyst", "broadcast")
LANGS = ("en", "id")


@dataclass
class Claim:
    id: str
    text: str
    evidence: list[str]
    kind: str = "observation"   # observation | interpretation | limitation | question | recap


@dataclass
class Block:
    kind: str
    title: str
    claims: list[Claim]
    moment_id: Optional[str] = None
    table: Optional[list[dict[str, Any]]] = None


@dataclass
class Narrative:
    mode: str
    lang: str
    headline: Claim
    blocks: list[Block]
    overlay: list[dict[str, Any]] = field(default_factory=list)
    source: str = "template"

    def all_claims(self) -> list[Claim]:
        return [self.headline] + [c for b in self.blocks for c in b.claims]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def plain_text(self) -> str:
        return " ".join(c.text for c in self.all_claims())


# --- localisation -------------------------------------------------------------

PATTERN_LABEL = {
    "en": {"dominance": "Sustained dominance", "high_press": "High press", "counterattack": "Counterattacking threat",
           "momentum_swing": "Momentum swing", "late_comeback": "Late comeback", "low_event": "Low-event match"},
    "id": {"dominance": "Dominasi berkelanjutan", "high_press": "Pressing tinggi", "counterattack": "Ancaman serangan balik",
           "momentum_swing": "Perubahan momentum", "late_comeback": "Comeback di akhir laga", "low_event": "Laga minim peluang"},
}
STRENGTH = {"en": {"high": "high", "medium": "medium", "low": "low"},
            "id": {"high": "tinggi", "medium": "sedang", "low": "rendah"}}

LIMITATIONS = {
    "en": {
        "territory_not_quality": "Territory and ball circulation are not the same as chance quality; this dataset has no xG.",
        "opponent_strategy": "The opponent may have chosen to sit deep, so the pattern may reflect both teams' plans.",
        "press_association": "Turnovers are counted as press-linked when they occur within 5 seconds of a nearby pressure; that is an association, not proof the press caused them.",
        "turnover_other_causes": "Some ball losses would have happened without pressure (risky passes, poor touches).",
        "counter_definition": "A fast break is defined mechanically: won in own half, final third within 15 seconds.",
        "momentum_index_definition": "The momentum index is a simple share of attacking threat units, not a predictive model.",
        "score_effects": "Changes in the score can change both teams' risk-taking, which also moves momentum.",
        "comeback_small_sample": "A comeback is defined from goals alone; a handful of events decides it.",
        "low_event_threshold": "'Low-event' uses a fixed shot threshold; it does not judge entertainment value.",
    },
    "id": {
        "territory_not_quality": "Penguasaan wilayah dan sirkulasi bola tidak sama dengan kualitas peluang; data ini tidak memiliki xG.",
        "opponent_strategy": "Lawan mungkin sengaja bertahan dalam, sehingga pola ini bisa mencerminkan rencana kedua tim.",
        "press_association": "Turnover dihitung terkait pressing bila terjadi dalam 5 detik setelah tekanan di dekatnya; itu asosiasi, bukan bukti bahwa pressing penyebabnya.",
        "turnover_other_causes": "Sebagian kehilangan bola tetap akan terjadi tanpa tekanan (umpan berisiko, kontrol buruk).",
        "counter_definition": "Serangan cepat didefinisikan secara mekanis: bola direbut di separuh sendiri, mencapai sepertiga akhir dalam 15 detik.",
        "momentum_index_definition": "Indeks momentum hanyalah porsi unit ancaman serangan, bukan model prediksi.",
        "score_effects": "Perubahan skor dapat mengubah keberanian kedua tim, yang juga menggeser momentum.",
        "comeback_small_sample": "Comeback ditentukan hanya dari gol; beberapa kejadian saja sudah menentukannya.",
        "low_event_threshold": "'Minim peluang' memakai ambang tembakan tetap; ini tidak menilai seberapa menghibur laganya.",
    },
}

NEXT_Q = {
    "en": {
        "dominance_q": "Did the territorial share translate into shots from central zones, or mostly wide entries?",
        "press_q": "Where on the pitch were the press-linked turnovers won, and how quickly did they reach a shot?",
        "counter_q": "Which players started the fast breaks, and were they triggered by the same type of ball loss?",
        "swing_q": "Was the swing preceded by a substitution, a goal or a change in pressing height?",
        "comeback_q": "Did the trailing team's chance creation rise before the goals, or did the goals come from few chances?",
        "low_event_q": "Were shots suppressed by compact defending or by the attacking teams' own caution in possession?",
    },
    "id": {
        "dominance_q": "Apakah dominasi wilayah berubah menjadi tembakan dari area tengah, atau kebanyakan masuk dari sayap?",
        "press_q": "Di area mana turnover hasil pressing terjadi, dan seberapa cepat berujung tembakan?",
        "counter_q": "Pemain mana yang memulai serangan cepat, dan apakah dipicu jenis kehilangan bola yang sama?",
        "swing_q": "Apakah perubahan ini didahului pergantian pemain, gol, atau perubahan tinggi pressing?",
        "comeback_q": "Apakah produksi peluang tim yang tertinggal naik sebelum gol, atau gol datang dari sedikit peluang?",
        "low_event_q": "Apakah tembakan ditekan oleh pertahanan rapat atau oleh kehati-hatian tim saat menguasai bola?",
    },
}

UI = {
    "en": {"key": "Key moment", "why": "Why it matters", "recap": "Match recap", "evidence": "Evidence",
           "interp": "Interpretation (hypothesis)", "conf": "Confidence", "limits": "Limitations",
           "next": "Next question", "narration": "Live narration", "no_moment": "No pattern shift passed the detection thresholds."},
    "id": {"key": "Momen kunci", "why": "Mengapa penting", "recap": "Rekap laga", "evidence": "Bukti",
           "interp": "Interpretasi (hipotesis)", "conf": "Keyakinan", "limits": "Keterbatasan",
           "next": "Pertanyaan lanjutan", "narration": "Narasi langsung", "no_moment": "Tidak ada perubahan pola yang melewati ambang deteksi."},
}


METRIC_LABEL_ID = {
    "fte_share_pct": "Porsi penetrasi sepertiga akhir %",
    "completed_pass_share_pct": "Porsi umpan sukses %",
    "final_third_entries_per_window": "Penetrasi sepertiga akhir per jendela",
    "shots": "Tembakan (vs. laju pembanding, durasi sama)",
    "high_pressures_per_window": "Tekanan tinggi per jendela",
    "press_linked_turnovers_per_window": "Turnover lawan terkait pressing per jendela",
    "high_turnovers_forced_per_window": "Turnover tinggi yang dipaksakan per jendela",
    "fast_breaks_per_window": "Serangan cepat per jendela",
    "fast_breaks_ending_in_shot": "Serangan cepat berujung tembakan",
    "momentum_index": "Indeks momentum (sudut pandang tim)",
    "shots_per_window": "Tembakan per jendela",
    "goal_difference": "Selisih gol (sudut pandang tim)",
    "late_goals": "Gol setelah ambang comeback",
    "total_shots": "Total tembakan vs. ambang laga minim peluang",
    "total_final_third_entries": "Total penetrasi sepertiga akhir",
}


def metric_label(met: dict[str, Any], lang: str) -> str:
    return METRIC_LABEL_ID.get(met["metric"], met["label"]) if lang == "id" else met["label"]


METHOD_CONSTANTS = {"press_link_seconds": 5, "fast_break_seconds": 15, "comeback_from_min": 65}
LIMITATION_EVIDENCE = {
    "press_association": ["method.press_link_seconds"],
    "counter_definition": ["method.fast_break_seconds"],
}


def fmt(v: Any, lang: str) -> str:
    if isinstance(v, float) and not v.is_integer():
        s = f"{v:.1f}"
        return s.replace(".", ",") if lang == "id" else s
    return str(int(v)) if isinstance(v, (int, float)) else str(v)


# --- evidence registry ----------------------------------------------------------

def build_evidence(an: MatchAnalytics, moments: list[Moment]) -> dict[str, dict[str, Any]]:
    """Flat registry: evidence id -> {value, label, source}. Only deterministic values."""
    meta = an.meta
    reg: dict[str, dict[str, Any]] = {}
    score = an.score()
    for side, team in (("home", meta.home), ("away", meta.away)):
        t = an.totals[team]
        for key in ("goals", "shots", "shots_on_target", "final_third_entries", "possession_pct",
                    "pass_accuracy", "high_pressures", "fast_breaks"):
            reg[f"match.{side}.{key}"] = {"value": t[key], "label": f"{meta.short(team)} {key}", "source": "totals"}
    for key, val in METHOD_CONSTANTS.items():
        reg[f"method.{key}"] = {"value": val, "label": key, "source": "method"}
    reg["match.score_home"] = {"value": score[meta.home], "label": "home goals", "source": "goals"}
    reg["match.score_away"] = {"value": score[meta.away], "label": "away goals", "source": "goals"}
    for m in moments:
        reg[f"{m.id}.start_min"] = {"value": m.start_min, "label": "window start (min)", "source": m.id}
        reg[f"{m.id}.end_min"] = {"value": m.end_min, "label": "window end (min)", "source": m.id}
        reg[f"{m.id}.strength_score"] = {"value": m.strength_score, "label": "evidence strength", "source": m.id}
        for k, v in m.facts.items():
            reg[f"{m.id}.{k}"] = {"value": v, "label": k, "source": m.id}
        for met in m.metrics:
            for f in ("window_value", "baseline_value", "delta"):
                reg[f"{m.id}.{met['metric']}.{f}"] = {"value": met[f], "label": f"{met['label']} ({f})", "source": m.id}
    return reg


def _ranked(moments: list[Moment]) -> list[Moment]:
    order = {"late_comeback": 0, "momentum_swing": 1, "dominance": 2, "high_press": 3, "counterattack": 4, "low_event": 5}
    return sorted(moments, key=lambda m: (-m.strength_score, order.get(m.pattern, 9), m.start_min))


# --- per-pattern sentence templates -------------------------------------------------
# Each returns (text, evidence ids). `f` = moment facts, `T` = team short name, `O` = opponent.

def _one_liner(m: Moment, lang: str, T: str, O: str) -> tuple[str, list[str]]:
    f, i, s, e = m.facts, m.id, int(m.start_min), int(m.end_min)
    ev_t = [f"{i}.start_min", f"{i}.end_min"]
    en = lang == "en"
    if m.pattern == "dominance":
        return ((f"{s}'–{e}': {T} made {f['fte_share_pct']}% of the match's entries into the final third."
                 if en else f"{s}'–{e}': {T} mencatat {f['fte_share_pct']}% dari seluruh penetrasi ke sepertiga akhir."),
                ev_t + [f"{i}.fte_share_pct"])
    if m.pattern == "high_press":
        return ((f"{s}'–{e}': {T} pressed high {f['high_pressures']} times, and {O} lost the ball {f['press_linked_turnovers']} times soon after a press."
                 if en else f"{s}'–{e}': {T} menekan tinggi {f['high_pressures']} kali, dan {O} kehilangan bola {f['press_linked_turnovers']} kali tak lama setelah ditekan."),
                ev_t + [f"{i}.high_pressures", f"{i}.press_linked_turnovers"])
    if m.pattern == "counterattack":
        return ((f"{s}'–{e}': {T} launched {f['fast_breaks']} fast breaks from their own half."
                 if en else f"{s}'–{e}': {T} melancarkan {f['fast_breaks']} serangan cepat dari separuh lapangan sendiri."),
                ev_t + [f"{i}.fast_breaks"])
    if m.pattern == "momentum_swing":
        return ((f"Around {s}'–{e}', control moved from {O} to {T}."
                 if en else f"Sekitar menit {s}–{e}, kendali laga berpindah dari {O} ke {T}."), ev_t)
    if m.pattern == "late_comeback":
        mins = ", ".join(f"{x}'" for x in f["goal_minutes"])
        return ((f"{T} scored {f['late_goals']} late goals ({mins}) to go from {f['score_before']} to {f['final_score']}."
                 if en else f"{T} mencetak {f['late_goals']} gol di akhir laga ({mins}) dan mengubah skor {f['score_before']} menjadi {f['final_score']}."),
                [f"{i}.late_goals", f"{i}.goal_minutes", f"{i}.score_before", f"{i}.final_score"])
    # low_event
    return ((f"Only {f['total_shots']} shots in the whole match: {f['shots_home']} for the hosts, {f['shots_away']} for the visitors."
             if en else f"Hanya {f['total_shots']} tembakan sepanjang laga: {f['shots_home']} untuk tuan rumah, {f['shots_away']} untuk tim tamu."),
            [f"{i}.total_shots", f"{i}.shots_home", f"{i}.shots_away"])


def _headline(m: Optional[Moment], lang: str, T: str, O: str, an: MatchAnalytics) -> tuple[str, list[str]]:
    meta, sc = an.meta, an.score()
    en = lang == "en"
    if m is None:
        return ((f"{meta.short(meta.home)} {sc[meta.home]}–{sc[meta.away]} {meta.short(meta.away)}: no clear shift in the game's pattern"
                 if en else f"{meta.short(meta.home)} {sc[meta.home]}–{sc[meta.away]} {meta.short(meta.away)}: tak ada perubahan pola yang jelas"),
                ["match.score_home", "match.score_away"])
    s, e = int(m.start_min), int(m.end_min)
    ids = [f"{m.id}.start_min", f"{m.id}.end_min"]
    H = {
        "dominance": (f"{T} ran the game from {s}' to {e}'", f"{T} menguasai laga dari menit {s} hingga {e}"),
        "high_press": (f"{T}'s high press squeezed {O} ({s}'–{e}')", f"Pressing tinggi {T} menekan {O} ({s}'–{e}')"),
        "counterattack": (f"{T} carried a threat on the break ({s}'–{e}')", f"{T} mengancam lewat serangan balik ({s}'–{e}')"),
        "momentum_swing": (f"The game swung towards {T} around {s}'–{e}'", f"Laga berbalik ke arah {T} sekitar menit {s}–{e}"),
        "late_comeback": (f"{T} turned the game around late", f"{T} membalikkan keadaan di akhir laga"),
        "low_event": ("A tight, low-chance contest", "Laga ketat dengan sedikit peluang"),
    }[m.pattern]
    if m.pattern in ("late_comeback", "low_event"):
        ids = []
    return (H[0] if en else H[1]), ids


def _why(m: Moment, lang: str, T: str, O: str) -> tuple[str, list[str]]:
    f, i = m.facts, m.id
    en = lang == "en"
    if m.pattern == "dominance":
        return ((f"Long spells near goal usually mean more chances: {T} had {f['shots']} shots in that period."
                 if en else f"Lama berada dekat gawang lawan biasanya berarti lebih banyak peluang: {T} melepaskan {f['shots']} tembakan di periode itu."),
                [f"{i}.shots"])
    if m.pattern == "high_press":
        return ((f"Winning the ball high up the pitch puts a team close to goal straight away; {O}'s build-up was under strain."
                 if en else f"Merebut bola di area tinggi membuat tim langsung dekat ke gawang; build-up {O} berada di bawah tekanan."), [])
    if m.pattern == "counterattack":
        return ((f"Fast breaks catch defences out of shape; {f['fast_break_shots']} of them ended with a shot."
                 if en else f"Serangan cepat menangkap pertahanan yang belum rapi; {f['fast_break_shots']} di antaranya berakhir dengan tembakan."),
                [f"{i}.fast_break_shots"])
    if m.pattern == "momentum_swing":
        return ((f"{T} went from {f['fte_before']} to {f['fte_after']} final-third entries between the two spells."
                 if en else f"Penetrasi {T} ke sepertiga akhir naik dari {f['fte_before']} menjadi {f['fte_after']} di antara dua periode itu."),
                [f"{i}.fte_before", f"{i}.fte_after"])
    if m.pattern == "late_comeback":
        return ((f"Recovering from {f['worst_deficit']} goals down this late is rare and changes the whole story of the match."
                 if en else f"Bangkit dari defisit {f['worst_deficit']} gol di akhir laga itu langka dan mengubah cerita pertandingan."),
                [f"{i}.worst_deficit"])
    return (("Few shots means every chance carried extra weight." if en
             else "Sedikit tembakan berarti setiap peluang punya bobot lebih."), [])


def _observation(m: Moment, lang: str, T: str, O: str) -> tuple[str, list[str]]:
    """Analyst observation: compares comparison window with baseline, numbers only from metrics."""
    i, en = m.id, lang == "en"
    parts, ids = [], []
    for met in m.metrics[:3]:
        w, b = fmt(met["window_value"], lang), fmt(met["baseline_value"], lang)
        ids += [f"{i}.{met['metric']}.window_value", f"{i}.{met['metric']}.baseline_value"]
        label = metric_label(met, lang)
        parts.append(f"{label}: {w} (baseline {b})" if en else f"{label}: {w} (pembanding {b})")
    lead = ("Compared with " + _baseline_text(m, lang) + " — " if en else "Dibandingkan " + _baseline_text(m, lang) + " — ")
    if m.pattern == "momentum_swing":
        ids += [f"{i}.before_start", f"{i}.before_end", f"{i}.after_start", f"{i}.after_end"]
    if m.pattern == "late_comeback":
        ids += ["method.comeback_from_min"]
    return lead + "; ".join(parts) + ".", ids


def _baseline_text(m: Moment, lang: str) -> str:
    en = lang == "en"
    if m.baseline == "rest_of_match":
        return "the rest of the match" if en else "sisa pertandingan"
    if m.pattern == "momentum_swing":
        f = m.facts
        return (f"{f['before_start']}'–{f['before_end']}' vs {f['after_start']}'–{f['after_end']}'" if en
                else f"menit {f['before_start']}–{f['before_end']} vs {f['after_start']}–{f['after_end']}")
    if m.pattern == "late_comeback":
        return "the score at 65'" if en else "skor pada menit 65"
    return "the detector threshold" if en else "ambang detektor"


def _interpretation(m: Moment, lang: str, T: str, O: str) -> tuple[str, list[str]]:
    en = lang == "en"
    t = {
        "dominance": (f"Consistent with {T} holding sustained territorial control.",
                      f"Konsisten dengan {T} memegang kendali wilayah secara berkelanjutan."),
        "high_press": (f"Consistent with an aggressive high press by {T}; the turnovers coincided with pressure but are not proven to be caused by it.",
                       f"Konsisten dengan pressing tinggi yang agresif dari {T}; turnover terjadi bersamaan dengan tekanan, namun tidak terbukti disebabkan olehnya."),
        "counterattack": (f"Consistent with {T} looking to attack quickly in transition rather than through long possession.",
                          f"Konsisten dengan {T} memilih menyerang cepat saat transisi, bukan lewat penguasaan bola panjang."),
        "momentum_swing": (f"Consistent with a change in which team was setting the terms of the game.",
                           f"Konsisten dengan pergantian tim yang mengendalikan jalannya laga."),
        "late_comeback": (f"The late goals coincided with {T} spending more time in the attacking third; association, not proof.",
                          f"Gol-gol akhir bersamaan dengan {T} lebih lama berada di sepertiga serang; ini asosiasi, bukan bukti."),
        "low_event": ("Consistent with two cautious approaches that limited shooting opportunities.",
                      "Konsisten dengan pendekatan hati-hati kedua tim yang membatasi peluang menembak."),
    }[m.pattern]
    return (t[0] if en else t[1]), []


def _broadcast_line(m: Moment, lang: str, T: str, O: str) -> tuple[str, list[str]]:
    f, i, s, e = m.facts, m.id, int(m.start_min), int(m.end_min)
    en = lang == "en"
    if m.pattern == "dominance":
        return ((f"{e}' — {T} camped in the final third since {s}': {f['fte_share_pct']}% of entries."
                 if en else f"{e}' — {T} bertahan di sepertiga akhir sejak menit {s}: {f['fte_share_pct']}% penetrasi."),
                [f"{i}.end_min", f"{i}.start_min", f"{i}.fte_share_pct"])
    if m.pattern == "high_press":
        return ((f"{e}' — {T} pressing high: {f['high_pressures']} pressures, {f['press_linked_turnovers']} quick turnovers."
                 if en else f"{e}' — {T} menekan tinggi: {f['high_pressures']} tekanan, {f['press_linked_turnovers']} turnover cepat."),
                [f"{i}.end_min", f"{i}.high_pressures", f"{i}.press_linked_turnovers"])
    if m.pattern == "counterattack":
        return ((f"{e}' — {T} dangerous on the break: {f['fast_breaks']} fast breaks since {s}'."
                 if en else f"{e}' — {T} berbahaya lewat serangan balik: {f['fast_breaks']} serangan cepat sejak menit {s}."),
                [f"{i}.end_min", f"{i}.fast_breaks", f"{i}.start_min"])
    if m.pattern == "momentum_swing":
        return ((f"{e}' — The tide has turned: {T} now on top." if en else f"{e}' — Arus berbalik: kini {T} di atas angin."),
                [f"{i}.end_min"])
    if m.pattern == "late_comeback":
        last = f["goal_minutes"][-1]
        return ((f"{last}' — Comeback complete! {T} lead {f['final_score']}." if en
                 else f"{last}' — Comeback tuntas! {T} kini unggul {f['final_score']}."),
                [f"{i}.goal_minutes", f"{i}.final_score"]) if f["final_score"] else ("", [])
    return ((f"90' — Just {f['total_shots']} shots all game." if en else f"90' — Hanya {f['total_shots']} tembakan sepanjang laga."),
            [f"{i}.total_shots", f"{i}.end_min"])


def _overlay(m: Moment, lang: str, T: str, O: str) -> dict[str, Any]:
    f, en = m.facts, lang == "en"
    metric = {
        "dominance": (("Final-third entry share" if en else "Porsi penetrasi sepertiga akhir"), f"{f.get('fte_share_pct')}%"),
        "high_press": (("High pressures" if en else "Tekanan tinggi"), str(f.get("high_pressures"))),
        "counterattack": (("Fast breaks" if en else "Serangan cepat"), str(f.get("fast_breaks"))),
        "momentum_swing": (("Momentum index" if en else "Indeks momentum"), fmt(f.get("momentum_after", 0), lang)),
        "late_comeback": (("Score" if en else "Skor"), str(f.get("final_score"))),
        "low_event": (("Total shots" if en else "Total tembakan"), str(f.get("total_shots"))),
    }[m.pattern]
    headline = PATTERN_LABEL[lang][m.pattern].upper() + (f" · {T}" if m.team else "")
    text, ids = _broadcast_line(m, lang, T, O)
    return {
        "id": m.id, "pattern": m.pattern, "team": m.team, "team_name": T or None,
        "timestamp_min": int(m.end_min), "window": [m.start_min, m.end_min],
        "headline": headline[:42], "metric_label": metric[0], "metric_value": metric[1],
        "text": text[:110], "strength": m.strength, "evidence_ids": m.evidence_event_ids[:10],
        "synthetic": True,
    }


# --- composition -------------------------------------------------------------------

def render(mode: str, lang: str, an: MatchAnalytics, moments: list[Moment]) -> Narrative:
    if mode not in MODES or lang not in LANGS:
        raise ValueError(f"mode must be one of {MODES}, lang one of {LANGS}")
    meta: MatchMeta = an.meta
    ranked = _ranked(moments)
    top = ranked[0] if ranked else None
    names = lambda m: (meta.short(m.team), meta.short(meta.away if m.team == meta.home else meta.home)) if m.team else ("", "")
    T, O = names(top) if top else ("", "")
    h_text, h_ids = _headline(top, lang, T, O, an)
    headline = Claim("headline", h_text, h_ids, "observation")
    u = UI[lang]
    blocks: list[Block] = []
    sc = an.score()
    recap_text = (f"Final score: {meta.short(meta.home)} {sc[meta.home]}–{sc[meta.away]} {meta.short(meta.away)}."
                  if lang == "en" else
                  f"Skor akhir: {meta.short(meta.home)} {sc[meta.home]}–{sc[meta.away]} {meta.short(meta.away)}.")
    recap_claim = Claim("recap.score", recap_text, ["match.score_home", "match.score_away"], "recap")

    if mode == "casual":
        if top:
            t, ids = _one_liner(top, lang, T, O)
            blocks.append(Block("key_moment", u["key"], [Claim("key.1", t, ids)], top.id))
            t, ids = _why(top, lang, T, O)
            blocks.append(Block("why", u["why"], [Claim("why.1", t, ids, "interpretation")], top.id))
        recap = [recap_claim]
        for k, m in enumerate(ranked[1:3], start=2):
            t2, o2 = names(m)
            t, ids = _one_liner(m, lang, t2, o2)
            recap.append(Claim(f"recap.{k}", t, ids, "recap"))
        if not ranked:
            recap.append(Claim("recap.none", u["no_moment"], [], "recap"))
        blocks.append(Block("recap", u["recap"], recap))
        return Narrative(mode, lang, headline, blocks)

    if mode == "analyst":
        for m in ranked:
            t2, o2 = names(m)
            label = PATTERN_LABEL[lang][m.pattern] + (f" — {t2}" if t2 else "")
            title = f"{label} · {int(m.start_min)}'–{int(m.end_min)}'"
            obs, obs_ids = _observation(m, lang, t2, o2)
            interp, interp_ids = _interpretation(m, lang, t2, o2)
            conf = (f"Evidence strength: {STRENGTH[lang][m.strength]} ({fmt(m.strength_score, lang)})." if lang == "en"
                    else f"Kekuatan bukti: {STRENGTH[lang][m.strength]} ({fmt(m.strength_score, lang)}).")
            claims = [
                Claim(f"{m.id}.obs", obs, obs_ids, "observation"),
                Claim(f"{m.id}.interp", interp, interp_ids, "interpretation"),
                Claim(f"{m.id}.conf", conf, [f"{m.id}.strength_score"], "observation"),
            ] + [Claim(f"{m.id}.lim{j}", LIMITATIONS[lang][k], LIMITATION_EVIDENCE.get(k, []), "limitation") for j, k in enumerate(m.limitations)] + [
                Claim(f"{m.id}.next", NEXT_Q[lang][m.next_question], [], "question")]
            table = [dict(met, label=metric_label(met, lang)) for met in m.metrics]
            blocks.append(Block("moment", title, claims, m.id, table=table))
        if not ranked:
            blocks.append(Block("moment", u["no_moment"], [Claim("none", u["no_moment"], [], "observation")]))
        blocks.append(Block("recap", u["recap"], [recap_claim]))
        return Narrative(mode, lang, headline, blocks)

    # broadcast — chronological narration + overlay payload
    lines = []
    for m in sorted(moments, key=lambda x: x.end_min):
        t2, o2 = names(m)
        t, ids = _broadcast_line(m, lang, t2, o2)
        if t:
            lines.append(Claim(f"{m.id}.line", t, ids, "observation"))
    if not lines:
        lines.append(Claim("none", u["no_moment"], [], "observation"))
    lines.append(Claim("recap.score", (f"FT — {meta.short(meta.home)} {sc[meta.home]}–{sc[meta.away]} {meta.short(meta.away)}."),
                       ["match.score_home", "match.score_away"], "recap"))
    blocks.append(Block("narration", u["narration"], lines))
    overlay = [_overlay(m, lang, *names(m)) for m in sorted(moments, key=lambda x: x.end_min)]
    return Narrative(mode, lang, headline, blocks, overlay)
