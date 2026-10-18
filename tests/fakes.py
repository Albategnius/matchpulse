"""Scripted chat clients for testing the real Agent Framework Agent path without a cloud model."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Callable

import agent_framework  # noqa: F401
from agent_framework import BaseChatClient, ChatResponse, Message


def _payload(messages: Any) -> dict[str, Any]:
    text = ""
    for m in messages:
        for c in getattr(m, "contents", []) or []:
            t = getattr(c, "text", None)
            if t:
                text += t
    start = text.find("{")
    end = text.find("\n\nThe previous attempt")
    blob = text[start:end] if end > 0 else text[start:]
    return json.loads(blob), ("previous attempt was rejected" in text)


class ScriptedClient(BaseChatClient):
    """`behaviour(payload, is_retry) -> str` decides what the 'model' returns."""

    def __init__(self, behaviour: Callable[[dict[str, Any], bool], str], delay: float = 0.0):
        super().__init__()
        self.behaviour = behaviour
        self.delay = delay
        self.calls = 0

    async def _inner_get_response(self, *, messages, stream, options, **kwargs):
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        payload, retry = _payload(messages)
        text = self.behaviour(payload, retry)
        return ChatResponse(messages=[Message(role="assistant", contents=[text])], response_id=f"fake-{self.calls}")


def faithful(payload: dict[str, Any], retry: bool) -> str:
    """Rewords claims while keeping every number (prefixes a style marker)."""
    claims = {c["id"]: c["text"] for c in payload["claims"] if c["id"] != "headline"}
    head = next((c["text"] for c in payload["claims"] if c["id"] == "headline"), "Match report")
    return json.dumps({"headline": head, "claims": {k: v.replace("—", "-") for k, v in claims.items()}})


def hallucinate_then_fix(payload: dict[str, Any], retry: bool) -> str:
    if retry:
        return faithful(payload, retry)
    claims = {}
    for c in payload["claims"]:
        if c["id"] == "headline":
            continue
        claims[c["id"]] = re.sub(r"\d+", "97", c["text"]) + " This was caused by the press."
    return json.dumps({"headline": "Team won 97-0 because of the press", "claims": claims})


def always_hallucinate(payload: dict[str, Any], retry: bool) -> str:
    return hallucinate_then_fix(payload, False)


def invalid_json(payload: dict[str, Any], retry: bool) -> str:
    return "Sure! Here is the narrative: the home side were great."


def raising(payload: dict[str, Any], retry: bool) -> str:
    raise RuntimeError("503 Service Unavailable (simulated)")
