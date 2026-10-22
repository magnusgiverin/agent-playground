"""Hvilken Claude-økt som hører til hvilken oppgave, lagret i .cache/sessions.json."""

import json
import threading
from pathlib import Path

from .config import HERE

STATE = HERE / ".cache" / "sessions.json"


def load_state():
    try:
        return json.loads(STATE.read_text())
    except (FileNotFoundError, ValueError):
        return {}


LOCK = threading.Lock()


def save_state(state):
    """Lagrer øktene. Flere agenter kan være ferdige samtidig, så skrivingen skjer én om gangen."""
    with LOCK:
        for _ in range(3):
            try:
                text = json.dumps(state, indent=2, ensure_ascii=False)
                break
            except RuntimeError:  # en annen tråd endret en oppgave akkurat nå: prøv igjen
                continue
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(text)


def session_exists(session_id):
    return any((Path.home() / ".claude" / "projects").glob(f"*/{session_id}.jsonl"))


def news_for(ticket, seen):
    """Hva et menneske har gjort med oppgaven siden økta sist så på den."""
    news = []
    if ticket["comment"] and ticket["comment"] != seen.get("comment"):
        news.append("ny kommentar")
    if ticket["edited"] and ticket["edited"] != seen.get("edited"):
        news.append("oppgaven er endret")
    if set(c for c, _ in ticket.get("inline", [])) - set(seen.get("inline", [])):
        news.append("ny linjekommentar")
    return news
