"""Fault injection for demonstrating and testing the Evidence & Quality Reviewer.

`FaultInjectionClient` is an Agent Framework chat client that SIMULATES a faulty
language model: on the first attempt it replaces numbers with wrong values and adds
causal claims; when the reviewer sends feedback it returns a faithful draft. The UI
labels this mode clearly as a simulation — it is never presented as a live model.
"""

from __future__ import annotations

import json
import re
from typing import Any

import agent_framework  # noqa: F401
from agent_framework import BaseChatClient, ChatResponse, Message

from .writer import LLMWriter


def _read_payload(messages: Any) -> tuple[dict[str, Any], bool]:
    text = ""
    for m in messages:
        for c in getattr(m, "contents", []) or []:
            t = getattr(c, "text", None)
            if t:
                text += t
    retry = "previous attempt was rejected" in text
    cut = text.find("\n\nThe previous attempt")
    blob = text[text.find("{"): cut if cut > 0 else len(text)]
    return json.loads(blob), retry


class FaultInjectionClient(BaseChatClient):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    async def _inner_get_response(self, *, messages, stream, options, **kwargs):
        self.calls += 1
        payload, retry = _read_payload(messages)
        claims = {c["id"]: c["text"] for c in payload["claims"] if c["id"] != "headline"}
        head = next((c["text"] for c in payload["claims"] if c["id"] == "headline"), "")
        if not retry:
            claims = {k: re.sub(r"\d+", lambda m: str(int(m.group()) + 7), v) for k, v in claims.items()}
            first = next(iter(claims), None)
            if first:
                claims[first] += (" This happened because of the press." if payload["language"] == "English"
                                  else " Ini terjadi karena pressing.")
        out = json.dumps({"headline": head, "claims": claims}, ensure_ascii=False)
        return ChatResponse(messages=[Message(role="assistant", contents=[out])], response_id=f"fault-{self.calls}")


def fault_injection_writer() -> LLMWriter:
    return LLMWriter(FaultInjectionClient(), "simulated-faulty-model", timeout_s=10)
