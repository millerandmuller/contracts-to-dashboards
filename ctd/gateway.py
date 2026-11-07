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


def live() -> bool:
    """True when a model call may be tried at all."""
    return not OFFLINE and bool(os.environ.get("ANTHROPIC_API_KEY")) and time.time() >= _down_until


def _ask(system: str, user: str, max_tokens: int, timeout: float | None = None) -> str:
    import anthropic
    client = anthropic.Anthropic(timeout=timeout or TIMEOUT_S, max_retries=0)
    r = client.messages.create(model=MODEL, max_tokens=max_tokens, system=system, messages=[{"role": "user", "content": user}])
    text = "".join(b.text for b in r.content if getattr(b, "type", "") == "text").strip()
    if not text:
        raise ValueError("empty answer")
    return text


def _fallback_reason() -> str | None:
    if OFFLINE:
        return "offline"
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return "no API key"
    if time.time() < _down_until:
        return "model unreachable a moment ago"
    return None


def complete(task: str, facts: str, cached_text: str, timeout: float | None = None) -> dict:
    """Return {'text', 'cached', 'why'}. Falls back to the fixture on no key, offline, timeout or any error."""
    why = _fallback_reason()
    if why:
        return {"text": cached_text, "cached": True, "why": why}
    global _down_until
    try:
        return {"text": _ask(SYSTEM, f"Task: {task}\nFacts:\n{facts}", 300, timeout), "cached": False, "why": MODEL}
    except Exception as e:  # noqa: BLE001 - any failure degrades to the fixture, visibly
        _down_until = time.time() + 120
        return {"text": cached_text, "cached": True, "why": f"{type(e).__name__}"}


JSON_SYSTEM = ("You read short answers from a shift worker and propose fields for a role contract. Reply with one JSON object only, no prose. "
               "Use only the allowed values you are given. For every field give the exact words from the answers that it comes from.")


def complete_json(task: str, facts: str) -> dict:
    """Return {'data': dict | None, 'cached': bool, 'why': str}. data is None whenever the model did not answer usably."""
    why = _fallback_reason()
    if why:
        return {"data": None, "cached": True, "why": why}
    global _down_until
    try:
        text = _ask(JSON_SYSTEM, f"Task: {task}\nFacts:\n{facts}", 700)
        text = text[text.index("{"): text.rindex("}") + 1]
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("not an object")
        return {"data": data, "cached": False, "why": MODEL}
    except Exception as e:  # noqa: BLE001
        _down_until = time.time() + 120
        return {"data": None, "cached": True, "why": f"{type(e).__name__}"}
