"""Multi-agent orchestration with Microsoft Agent Framework (agent-framework-core).

Graph (typed message routing, built with WorkflowBuilder):

    EventAnalyticsAgent --AnalyticsPacket--> TacticalInsightAgent --InsightPacket-->
    NarrativeAgent --DraftPacket--> EvidenceQualityReviewer
         ^                                   |
         +--------- RegenerateRequest -------+   (bounded: max_regenerations)

The reviewer is the only node that yields the workflow output. Every agent appends a
trace record (input summary, output summary, duration, status, next hop) so the UI
and docs can show exactly what happened.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from agent_framework import Executor, WorkflowBuilder, WorkflowContext, handler

from . import SYNTHETIC_DISCLAIMER
from .analytics import MatchAnalytics, analyse
from .narrative import MODES, Narrative, build_evidence, render
from .reviewer import ReviewReport, review
from .schema import Event, MatchDataError, MatchMeta, ValidationReport, validate_events
from .shifts import DetectorConfig, Moment, detect_shifts
from .writer import NarrativeWriter, TemplateWriter, WriterError


# --- messages (explicit input/output schemas between agents) -----------------------

@dataclass
class MatchRequest:
    rows: Optional[list[dict[str, Any]]] = None     # raw rows (CSV upload) -> validated
    events: Optional[list[Event]] = None            # already-validated events (generator)
    meta: Optional[MatchMeta] = None
    window_min: int = 5
    until_min: Optional[float] = None               # live replay horizon (None = full match)
    lang: str = "en"
    modes: tuple[str, ...] = MODES
    config: DetectorConfig = field(default_factory=DetectorConfig)
    trace: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class AnalyticsPacket:
    req: MatchRequest
    analytics: MatchAnalytics
    validation: ValidationReport


@dataclass
class InsightPacket:
    prev: AnalyticsPacket
    moments: list[Moment]
    registry: dict[str, dict[str, Any]]


@dataclass
class DraftPacket:
    insight: InsightPacket
    templates: dict[str, Narrative]
    drafts: dict[str, Narrative]
    writer_errors: dict[str, str] = field(default_factory=dict)
    attempt: int = 1
    review_history: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class RegenerateRequest:
    draft: DraftPacket
    feedback: dict[str, list[str]]


def _now() -> float:
    return time.perf_counter()


def _trace(req: MatchRequest, agent: str, t0: float, status: str, summary: str, next_hop: Optional[str]) -> None:
    req.trace.append({"step": len(req.trace) + 1, "agent": agent, "status": status,
                      "duration_ms": round((_now() - t0) * 1000, 1), "summary": summary, "handoff_to": next_hop})


def _error_result(req: MatchRequest, message: str, validation: Optional[ValidationReport]) -> dict[str, Any]:
    return {"status": "error", "error": message, "validation": validation.to_dict() if validation else None,
            "trace": req.trace, "disclaimer": SYNTHETIC_DISCLAIMER}


# --- agents ---------------------------------------------------------------------------

class EventAnalyticsAgent(Executor):
    """Validates the event stream and computes deterministic metrics. Never invents statistics."""

    def __init__(self) -> None:
        super().__init__(id="event_analytics_agent")

    @handler
    async def run(self, req: MatchRequest, ctx: WorkflowContext[AnalyticsPacket, dict]) -> None:
        t0 = _now()
        try:
            if req.rows is not None:
                events, meta, report = validate_events(req.rows, req.meta)
            else:
                events, meta = req.events or [], req.meta
                if not events or meta is None:
                    raise MatchDataError("No events supplied.")
                report = ValidationReport(total_rows=len(events), valid_rows=len(events))
            an = analyse(events, meta, req.window_min, req.until_min)
        except (MatchDataError, ValueError) as exc:
            rep = getattr(exc, "report", None)
            _trace(req, "Event Analytics Agent", t0, "failed", str(exc), None)
            await ctx.yield_output(_error_result(req, str(exc), rep))
            return
        sc = an.score()
        _trace(req, "Event Analytics Agent", t0, "ok",
               f"{report.valid_rows} valid events ({len(report.dropped)} dropped); "
               f"{len(an.windows)} windows of {req.window_min} min; score {sc[meta.home]}-{sc[meta.away]}"
               + (f" (live replay at {an.until_min:.0f}')" if an.live else ""),
               "Tactical Insight Agent")
        await ctx.send_message(AnalyticsPacket(req, an, report))


class TacticalInsightAgent(Executor):
    """Detects candidate match shifts against baselines and retrieves supporting evidence."""

    def __init__(self) -> None:
        super().__init__(id="tactical_insight_agent")

    @handler
    async def run(self, pkt: AnalyticsPacket, ctx: WorkflowContext[InsightPacket]) -> None:
        t0 = _now()
        moments = detect_shifts(pkt.analytics, pkt.req.config)
        registry = build_evidence(pkt.analytics, moments)
        summary = ", ".join(f"{m.id} {m.pattern} {m.team or 'both'} {m.start_min:.0f}-{m.end_min:.0f}'" for m in moments) or "no shift passed thresholds"
        _trace(pkt.req, "Tactical Insight Agent", t0, "ok",
               f"{len(moments)} moment(s): {summary}; {len(registry)} evidence items", "Narrative & Personalization Agent")
        await ctx.send_message(InsightPacket(pkt, moments, registry))


class NarrativeAgent(Executor):
    """Turns verified insights into audience-specific narratives (template draft, then optional Foundry rewrite)."""

    def __init__(self, writer: NarrativeWriter) -> None:
        super().__init__(id="narrative_agent")
        self.writer = writer

    async def _write(self, modes: list[str], templates: dict[str, Narrative], registry: dict[str, Any],
                     feedback: dict[str, list[str]]) -> tuple[dict[str, Narrative], dict[str, str]]:
        async def one(mode: str) -> tuple[str, Narrative, Optional[str]]:
            try:
                return mode, await self.writer.rewrite(templates[mode], registry, feedback.get(mode)), None
            except WriterError as exc:
                fallback = await TemplateWriter().rewrite(templates[mode], registry)
                return mode, fallback, str(exc)
        results = await asyncio.gather(*(one(m) for m in modes))
        drafts = {m: n for m, n, _ in results}
        errors = {m: e for m, _, e in results if e}
        return drafts, errors

    @handler
    async def first(self, pkt: InsightPacket, ctx: WorkflowContext[DraftPacket]) -> None:
        t0 = _now()
        req, an = pkt.prev.req, pkt.prev.analytics
        templates = {m: render(m, req.lang, an, pkt.moments) for m in req.modes}
        drafts, errors = await self._write(list(req.modes), templates, pkt.registry, {})
        status = "ok" if not errors else "degraded"
        detail = f"{len(drafts)} narratives ({', '.join(req.modes)}) in '{req.lang}' via {self.writer.name}"
        if errors:
            detail += f"; writer errors -> template fallback for {sorted(errors)}: {next(iter(errors.values()))}"
        _trace(req, "Narrative & Personalization Agent", t0, status, detail, "Evidence & Quality Reviewer")
        await ctx.send_message(DraftPacket(pkt, templates, drafts, errors, attempt=1))

    @handler
    async def regenerate(self, msg: RegenerateRequest, ctx: WorkflowContext[DraftPacket]) -> None:
        t0 = _now()
        d = msg.draft
        req = d.insight.prev.req
        modes = list(msg.feedback)
        new, errors = await self._write(modes, d.templates, d.insight.registry, msg.feedback)
        d.drafts.update(new)
        d.writer_errors.update(errors)
        d.attempt += 1
        _trace(req, "Narrative & Personalization Agent", t0, "ok" if not errors else "degraded",
               f"regeneration attempt {d.attempt} for {modes} using reviewer feedback", "Evidence & Quality Reviewer")
        await ctx.send_message(d)


class EvidenceQualityReviewer(Executor):
    """Checks every claim against the evidence registry; requests regeneration or falls back safely."""

    def __init__(self, max_regenerations: int = 1) -> None:
        super().__init__(id="evidence_quality_reviewer")
        self.max_regenerations = max_regenerations

    @handler
    async def run(self, d: DraftPacket, ctx: WorkflowContext[RegenerateRequest, dict]) -> None:
        t0 = _now()
        ins = d.insight
        an, req = ins.prev.analytics, ins.prev.req
        meta = an.meta
        teams = {meta.short(meta.home), meta.short(meta.away), meta.home_name, meta.away_name}
        reviewed: dict[str, Narrative] = {}
        reports: dict[str, ReviewReport] = {}
        feedback: dict[str, list[str]] = {}
        for mode, draft in d.drafts.items():
            cleaned, rep = review(draft, ins.registry, teams)
            reviewed[mode], reports[mode] = cleaned, rep
            d.review_history.append({"attempt": d.attempt, "mode": mode, "source": draft.source,
                                     "status": rep.status, "removed": rep.removed[:5], "reasons": rep.reasons})
            if rep.status == "rejected" and draft.source != "template":
                feedback[mode] = rep.reasons + [f"{r['claim_id']}: {'; '.join(r['problems'])}" for r in rep.removed[:6]]

        if feedback and d.attempt <= self.max_regenerations:
            _trace(req, "Evidence & Quality Reviewer", t0, "regenerate",
                   f"rejected {sorted(feedback)}; requesting regeneration ({d.attempt}/{self.max_regenerations})",
                   "Narrative & Personalization Agent")
            await ctx.send_message(RegenerateRequest(d, feedback))
            return

        fallbacks = []
        for mode, rep in list(reports.items()):
            if rep.status == "rejected":
                tmpl, trep = review(await TemplateWriter().rewrite(d.templates[mode], ins.registry), ins.registry, teams)
                trep.reasons.insert(0, f"model draft rejected ({'; '.join(rep.reasons)}); safe template fallback used")
                reviewed[mode], reports[mode] = tmpl, trep
                fallbacks.append(mode)
        removed = sum(len(r.removed) for r in reports.values())
        checked = sum(r.claims_checked for r in reports.values())
        _trace(req, "Evidence & Quality Reviewer", t0, "ok" if not fallbacks else "fallback",
               f"checked {checked} claims, removed {removed}; fallbacks: {fallbacks or 'none'}", None)
        await ctx.yield_output(_result(d, reviewed, reports, fallbacks))


def _result(d: DraftPacket, narratives: dict[str, Narrative], reports: dict[str, ReviewReport],
            fallbacks: list[str]) -> dict[str, Any]:
    ins = d.insight
    an, req, validation = ins.prev.analytics, ins.prev.req, ins.prev.validation
    by_id = {e.event_id: e for e in an.events}
    evidence_events = {m.id: [by_id[i].model_dump() for i in m.evidence_event_ids if i in by_id] for m in ins.moments}
    key_events = [e.model_dump() for e in an.events if e.event_type == "shot"]
    return {
        "status": "ok",
        "disclaimer": SYNTHETIC_DISCLAIMER,
        "meta": an.meta.model_dump(),
        "validation": validation.to_dict(),
        "analytics": an.to_dict(),
        "moments": [m.to_dict() for m in ins.moments],
        "evidence_registry": ins.registry,
        "evidence_events": evidence_events,
        "key_events": key_events,
        "narratives": {m: n.to_dict() for m, n in narratives.items()},
        "reviews": {m: r.to_dict() for m, r in reports.items()},
        "fallbacks": fallbacks,
        "writer_errors": d.writer_errors,
        "regeneration_attempts": d.attempt - 1,
        "review_history": d.review_history,
        "detector_config": req.config.to_dict(),
        "lang": req.lang,
        "trace": req.trace,
    }


# --- entry point ----------------------------------------------------------------------

def build_workflow(writer: NarrativeWriter, max_regenerations: int = 1):
    ea, ti = EventAnalyticsAgent(), TacticalInsightAgent()
    na, qr = NarrativeAgent(writer), EvidenceQualityReviewer(max_regenerations)
    return (WorkflowBuilder(name="matchpulse-evidence-to-story", start_executor=ea, max_iterations=20)
            .add_edge(ea, ti).add_edge(ti, na).add_edge(na, qr).add_edge(qr, na)
            .build())


async def run_pipeline(req: MatchRequest, writer: Optional[NarrativeWriter] = None,
                       timeout_s: float = 120.0, max_regenerations: int = 1) -> dict[str, Any]:
    writer = writer or TemplateWriter()
    t0 = _now()
    wf = build_workflow(writer, max_regenerations)
    try:
        result = await asyncio.wait_for(wf.run(req), timeout_s)
    except asyncio.TimeoutError:
        return _error_result(req, f"Workflow exceeded {timeout_s:.0f}s timeout.", None)
    framework_events = []
    for ev in result:
        et = getattr(ev, "type", None)
        if et in ("executor_invoked", "executor_completed", "output"):
            framework_events.append({"type": str(et), "executor": getattr(ev, "executor_id", None)})
    outputs = result.get_outputs()
    if not outputs:
        return _error_result(req, "Workflow finished without output.", None)
    out = outputs[-1]
    out["orchestration"] = {
        "framework": "Microsoft Agent Framework (agent-framework-core)",
        "workflow": "matchpulse-evidence-to-story",
        "writer": writer.name,
        "framework_events": framework_events,
        "total_ms": round((_now() - t0) * 1000, 1),
    }
    return out
