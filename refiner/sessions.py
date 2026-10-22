"""Hvilken Claude-økt som hører til hvilken oppgave, lagret i .cache/sessions.json."""

import json
from pathlib import Path

from .config import HERE

STATE = HERE / ".cache" / "sessions.json"


def load_state():
    try:
        return json.loads(STATE.read_text())
    except (FileNotFoundError, ValueError):
        return {}


def save_state(state):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=2, ensure_ascii=False))


def session_exists(session_id):
    return any((Path.home() / ".claude" / "projects").glob(f"*/{session_id}.jsonl"))


def news_for(ticket, seen):
    """Hva et menneske har gjort med oppgaven siden økta sist så på den."""
    news = []
    if ticket["comment"] and ticket["comment"] != seen.get("comment"):
        news.append("ny kommentar")
    if ticket["edited"] and ticket["edited"] != seen.get("edited"):
        news.append("oppgaven er endret")
    return news
