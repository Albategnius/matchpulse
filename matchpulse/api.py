"""FastAPI service: dashboard + JSON API. Run: uvicorn matchpulse.api:app --port 8000"""

from __future__ import annotations

import json
import logging
import os
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Literal, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import SYNTHETIC_DISCLAIMER, __version__
from .evaluation import match
from .faults import fault_injection_writer
from .schema import MatchDataError, csv_to_rows, events_to_csv
from .shifts import Moment
from .synth import SCENARIOS, generate_match, scenario_catalog
from .workflow import MatchRequest, run_pipeline
from .writer import TemplateWriter, make_writer_from_env

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("matchpulse.api")

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
REPORTS = ROOT.parent / "reports"
MAX_CSV_BYTES = 5_000_000

app = FastAPI(title="MATCHPULSE", version=__version__,
              description="Evidence-first match intelligence on SYNTHETIC football events.")
app.mount("/static", StaticFiles(directory=STATIC), name="static")

WRITER, WRITER_STATUS = make_writer_from_env()
_CACHE: "OrderedDict[str, dict[str, Any]]" = OrderedDict()
_STATS = {"requests": 0, "failures": 0, "latencies_ms": []}


class AnalyzeRequest(BaseModel):
    scenario: str = "momentum_swing"
    seed: int = Field(default=7, ge=0, le=1_000_000)
    window_min: int = Field(default=5, ge=1, le=15)
    lang: Literal["en", "id"] = "en"
    writer: Literal["auto", "template", "fault_injection"] = "auto"
    until_min: Optional[float] = Field(default=None, ge=1, le=90)


class UploadRequest(BaseModel):
    csv: str
    window_min: int = Field(default=5, ge=1, le=15)
    lang: Literal["en", "id"] = "en"
    writer: Literal["auto", "template", "fault_injection"] = "auto"


def _writer(choice: str):
    if choice == "template":
        return TemplateWriter()
    if choice == "fault_injection":
        return fault_injection_writer()
    return WRITER


def _remember(key: str, value: dict[str, Any]) -> None:
    _CACHE[key] = value
    _CACHE.move_to_end(key)
    while len(_CACHE) > 48:
        _CACHE.popitem(last=False)


async def _run(req: MatchRequest, writer_choice: str) -> dict[str, Any]:
    t0 = time.perf_counter()
    _STATS["requests"] += 1
    writer = _writer(writer_choice)
    try:
        out = await run_pipeline(req, writer, timeout_s=float(os.getenv("MATCHPULSE_WORKFLOW_TIMEOUT", "90")))
    except Exception as exc:  # the API must stay usable
        log.exception("pipeline failure")
        _STATS["failures"] += 1
        return {"status": "error", "error": f"Internal pipeline error: {type(exc).__name__}", "trace": req.trace,
                "disclaimer": SYNTHETIC_DISCLAIMER}
    if out.get("status") != "ok":
        _STATS["failures"] += 1
    ms = round((time.perf_counter() - t0) * 1000, 1)
    _STATS["latencies_ms"] = (_STATS["latencies_ms"] + [ms])[-200:]
    out["latency_ms"] = ms
    out["writer_status"] = {**WRITER_STATUS, "selected": writer_choice, "active": writer.name}
    return out


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/health")
def health() -> dict[str, Any]:
    lat = _STATS["latencies_ms"]
    return {"status": "ok", "version": __version__, "synthetic_data_only": True,
            "writer": WRITER_STATUS, "requests": _STATS["requests"], "failures": _STATS["failures"],
            "median_latency_ms": sorted(lat)[len(lat) // 2] if lat else None}


@app.get("/api/scenarios")
def scenarios() -> dict[str, Any]:
    return {"scenarios": scenario_catalog(), "disclaimer": SYNTHETIC_DISCLAIMER}


@app.post("/api/analyze")
async def analyze(body: AnalyzeRequest) -> JSONResponse:
    if body.scenario not in SCENARIOS:
        raise HTTPException(404, f"Unknown scenario '{body.scenario}'")
    key = json.dumps(body.model_dump(), sort_keys=True)
    if key in _CACHE and body.writer != "fault_injection":
        return JSONResponse({**_CACHE[key], "cached": True})
    events, meta, gts = generate_match(body.scenario, body.seed)
    out = await _run(MatchRequest(events=events, meta=meta, window_min=body.window_min, lang=body.lang,
                                  until_min=body.until_min), body.writer)
    if out.get("status") == "ok":
        moments = [Moment(**m) for m in out["moments"]]
        until = out["analytics"]["until_min"]
        started = [g for g in gts if g.start_min < until]   # live: only patterns that have begun
        out["ground_truth"] = [dict(g.to_dict(), started=g.start_min < until) for g in gts]
        out["detection_check"] = match(moments, started)
        _remember(key, out)
    return JSONResponse(out)


@app.post("/api/analyze/upload")
async def analyze_upload(body: UploadRequest) -> JSONResponse:
    if len(body.csv.encode()) > MAX_CSV_BYTES:
        raise HTTPException(413, "CSV larger than 5 MB")
    try:
        rows = csv_to_rows(body.csv)
    except MatchDataError as exc:
        return JSONResponse({"status": "error", "error": str(exc), "disclaimer": SYNTHETIC_DISCLAIMER}, status_code=422)
    out = await _run(MatchRequest(rows=rows, window_min=body.window_min, lang=body.lang), body.writer)
    return JSONResponse(out, status_code=200 if out.get("status") == "ok" else 422)


@app.get("/api/dataset")
def dataset(scenario: str = Query("momentum_swing"), seed: int = Query(7, ge=0, le=1_000_000),
            format: Literal["csv", "json"] = "csv"):
    if scenario not in SCENARIOS:
        raise HTTPException(404, f"Unknown scenario '{scenario}'")
    events, meta, gts = generate_match(scenario, seed)
    name = f"matchpulse_synthetic_{scenario}_seed{seed}"
    headers = {"Content-Disposition": f'attachment; filename="{name}.{format}"'}
    if format == "csv":
        return PlainTextResponse(f"# {SYNTHETIC_DISCLAIMER}\n" + events_to_csv(events), headers=headers,
                                 media_type="text/csv")
    payload = {"disclaimer": SYNTHETIC_DISCLAIMER, "meta": meta.model_dump(),
               "ground_truth": [g.to_dict() for g in gts], "events": [e.model_dump() for e in events]}
    return JSONResponse(payload, headers=headers)


@app.get("/api/evaluation")
def evaluation() -> JSONResponse:
    path = REPORTS / "evaluation.json"
    if not path.exists():
        return JSONResponse({"status": "missing", "detail": "Run: python scripts/evaluate.py"}, status_code=404)
    return JSONResponse(json.loads(path.read_text()))
