"""Narrative writers.

* TemplateWriter — deterministic, always available (local fallback mode).
* LLMWriter — a Microsoft Agent Framework `Agent` backed by a Microsoft Foundry
  model (FoundryChatClient). It may only REWORD existing claims for the target
  audience; it cannot add claims or change which evidence a claim cites. Its output
  is then verified number-by-number by the Quality Reviewer.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
import re
from typing import Any, Optional, Protocol

from .narrative import Claim, Narrative

log = logging.getLogger("matchpulse.writer")

REWRITABLE_KINDS = {"observation", "interpretation", "recap"}

STYLE = {
    "casual": "Write for a casual fan: warm, simple everyday words, no jargon, at most 25 words per sentence.",
    "analyst": "Write for a tactical analyst: precise, neutral, keep every metric name and number, no hype.",
    "broadcast": "Write for live TV/radio: punchy present tense, keep the minute prefix like \"72' —\", max 110 characters per line.",
}
LANG_NAME = {"en": "English", "id": "Bahasa Indonesia"}

INSTRUCTIONS = """You are the Narrative & Personalization Agent of MATCHPULSE, a match-intelligence system
working ONLY on synthetic football data (fictional teams; never refer to real clubs, players or the Premier League).
You receive claims that were generated from verified deterministic statistics, each with an id and the evidence
values it may use. Rewrite each claim for the requested audience and language.

Hard rules:
1. Use ONLY numbers that appear in that claim's evidence values or in the original claim. Never invent numbers.
2. Do not add new facts, players, or teams. Do not merge or split claims. Keep every claim id.
3. Describe associations, never causation: do not write "caused", "because of", "led to", "due to",
   "menyebabkan", "karena", "akibat". Prefer "coincided with", "consistent with" / "bersamaan dengan", "konsisten dengan".
4. Return ONLY a JSON object, no markdown fences: {"headline": "...", "claims": {"<claim id>": "<rewritten text>", ...}}
"""


class NarrativeWriter(Protocol):
    name: str

    async def rewrite(self, narrative: Narrative, registry: dict[str, dict[str, Any]],
                      feedback: Optional[list[str]] = None) -> Narrative: ...


class WriterError(RuntimeError):
    pass


class TemplateWriter:
    name = "template"

    async def rewrite(self, narrative: Narrative, registry: dict[str, dict[str, Any]],
                      feedback: Optional[list[str]] = None) -> Narrative:
        out = copy.deepcopy(narrative)
        out.source = "template"
        return out


def _strip_json(text: str) -> dict[str, Any]:
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.IGNORECASE | re.MULTILINE).strip()
    start, end = t.find("{"), t.rfind("}")
    if start < 0 or end <= start:
        raise WriterError("model output contained no JSON object")
    try:
        data = json.loads(t[start:end + 1])
    except json.JSONDecodeError as exc:
        raise WriterError(f"model output was not valid JSON: {exc.msg}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("claims"), dict) or not isinstance(data.get("headline"), str):
        raise WriterError("model output did not match schema {headline: str, claims: {id: str}}")
    return data


class LLMWriter:
    """Agent Framework Agent wrapper with timeout and strict output parsing."""

    def __init__(self, chat_client: Any, label: str, timeout_s: float = 25.0):
        from agent_framework import Agent

        self.agent = Agent(client=chat_client, name="NarrativePersonalizationAgent", instructions=INSTRUCTIONS)
        self.name = label
        self.timeout_s = timeout_s

    def _prompt(self, n: Narrative, registry: dict[str, dict[str, Any]], feedback: Optional[list[str]]) -> str:
        items = []
        for c in n.all_claims():
            if c.id != "headline" and c.kind not in REWRITABLE_KINDS:
                continue
            items.append({"id": c.id, "text": c.text,
                          "evidence": {e: registry[e]["value"] for e in c.evidence if e in registry}})
        payload = {"audience": n.mode, "style": STYLE[n.mode], "language": LANG_NAME[n.lang], "claims": items}
        msg = json.dumps(payload, ensure_ascii=False, default=str)
        if feedback:
            msg += "\n\nThe previous attempt was rejected by the Evidence Reviewer for these reasons:\n- " + "\n- ".join(feedback)
            msg += "\nFix them. Use only the evidence values given."
        return msg

    async def rewrite(self, narrative: Narrative, registry: dict[str, dict[str, Any]],
                      feedback: Optional[list[str]] = None) -> Narrative:
        try:
            resp = await asyncio.wait_for(self.agent.run(self._prompt(narrative, registry, feedback)), self.timeout_s)
        except asyncio.TimeoutError as exc:
            raise WriterError(f"model call timed out after {self.timeout_s:.0f}s") from exc
        except WriterError:
            raise
        except Exception as exc:  # network, auth, quota, content filter...
            raise WriterError(f"model call failed: {type(exc).__name__}: {str(exc)[:200]}") from exc
        data = _strip_json(resp.text or "")
        out = copy.deepcopy(narrative)
        out.source = self.name
        new = data["claims"]
        if data["headline"].strip():
            out.headline = Claim(out.headline.id, data["headline"].strip(), out.headline.evidence, out.headline.kind)
        for b in out.blocks:
            for c in b.claims:
                if c.kind in REWRITABLE_KINDS and isinstance(new.get(c.id), str) and new[c.id].strip():
                    c.text = new[c.id].strip()
        return out


def make_writer_from_env() -> tuple[NarrativeWriter, dict[str, Any]]:
    """Return (writer, status). Uses Microsoft Foundry when configured, else local templates."""
    endpoint = os.getenv("FOUNDRY_PROJECT_ENDPOINT", "").strip()
    model = os.getenv("FOUNDRY_MODEL", "").strip()
    if os.getenv("MATCHPULSE_LLM", "auto").lower() == "off" or not (endpoint and model):
        return TemplateWriter(), {"mode": "local-template", "foundry_configured": False,
                                  "detail": "FOUNDRY_PROJECT_ENDPOINT / FOUNDRY_MODEL not set — deterministic templates used."}
    try:
        import agent_framework  # noqa: F401  (import order matters for the foundry namespace)
        from agent_framework.foundry import FoundryChatClient
        from azure.identity.aio import DefaultAzureCredential

        client = FoundryChatClient(project_endpoint=endpoint, model=model, credential=DefaultAzureCredential())
        timeout = float(os.getenv("MATCHPULSE_LLM_TIMEOUT", "25"))
        return LLMWriter(client, f"foundry:{model}", timeout), {
            "mode": "foundry", "foundry_configured": True, "model": model,
            "detail": "Microsoft Foundry model via Agent Framework FoundryChatClient (Entra ID / managed identity)."}
    except Exception as exc:  # missing package or bad config: stay usable
        log.warning("Foundry writer unavailable: %s", exc)
        return TemplateWriter(), {"mode": "local-template", "foundry_configured": True,
                                  "detail": f"Foundry configured but client failed to initialise: {type(exc).__name__}"}
