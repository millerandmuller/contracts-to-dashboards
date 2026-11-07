"""Model gateway. Provider and model ID are config. Every call has a hard timeout and a cached fixture (plan B)."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
MODEL = os.environ.get("CTD_MODEL", "claude-sonnet-5-5")
TIMEOUT_S = float(os.environ.get("CTD_LLM_TIMEOUT", "8"))
OFFLINE = os.environ.get("CTD_OFFLINE", "0") == "1"
_down_until = 0.0  # after a failed call, skip the model for a while so a dead network costs 8 s once, not on every page

SYSTEM = ("You write short plain-language text for people who work in shifts. Short sentences, no BI words. "
          "Use only the facts you are given. Never invent numbers or examples.")


def fixture(name: str) -> dict:
    return json.loads((DATA / "fixtures" / name).read_text())


def complete(task: str, facts: str, cached_text: str) -> dict:
    """Return {'text', 'cached', 'why'}. Falls back to the fixture on no key, offline, timeout or any error."""
    if OFFLINE or not os.environ.get("ANTHROPIC_API_KEY"):
        return {"text": cached_text, "cached": True, "why": "offline" if OFFLINE else "no API key"}
    global _down_until
    if time.time() < _down_until:
        return {"text": cached_text, "cached": True, "why": "model unreachable a moment ago"}
    try:
        import anthropic
        client = anthropic.Anthropic(timeout=TIMEOUT_S, max_retries=0)
        r = client.messages.create(model=MODEL, max_tokens=300, system=SYSTEM,
                                   messages=[{"role": "user", "content": f"Task: {task}\nFacts:\n{facts}"}])
        text = "".join(b.text for b in r.content if getattr(b, "type", "") == "text").strip()
        if not text:
            raise ValueError("empty answer")
        return {"text": text, "cached": False, "why": MODEL}
    except Exception as e:  # noqa: BLE001 - any failure degrades to the fixture, visibly
        _down_until = time.time() + 120
        return {"text": cached_text, "cached": True, "why": f"{type(e).__name__}"}
