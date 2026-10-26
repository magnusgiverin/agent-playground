"""Lokal tilstand for instansen: hvilken Claude-økt som hører til hvilken oppgave, og hva den har kostet.

Øktene ligger på denne PC-en (~/.claude/projects), så denne tilstanden er lokal. Alt som må deles mellom
instansene (hvem som har en oppgave, Slack-tråden, branchen), ligger i Notion og på GitHub i stedet.
Mister vi denne fila, starter oppgavene på nytt fra det som står i Notion.
"""

import json
import threading
import time
from pathlib import Path

from .config import STATE_DIR

STATE = STATE_DIR / "sessions.json"
RUNS = STATE_DIR / "runs.jsonl"  # én linje per Claude-kall: hva, hvor lenge, og hva det kostet
LOCK = threading.Lock()


def load_state():
    try:
        return json.loads(STATE.read_text())
    except (FileNotFoundError, ValueError):
        return {}


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
        tmp = STATE.with_suffix(".tmp")
        tmp.write_text(text)
        tmp.replace(STATE)  # atomisk, så et krasj midt i skrivingen ikke ødelegger fila


def session_exists(session_id):
    return bool(session_id) and any((Path.home() / ".claude" / "projects").glob(f"*/{session_id}.jsonl"))


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


def session_total(state, session):
    """Hva Claude Code sier at en økt har kostet så langt. Fortsettelser og forker arver dette beløpet.
    None betyr ukjent: økta er fra før kostnaden ble målt riktig."""
    for entry in state.values():
        if session in entry.get("totals", {}):
            return entry["totals"][session]
    return None if session_exists(session) else 0.0


def record(state, entry, session, kind, run, known=True):
    """Legger kallets egen kostnad til oppgaven, husker øktas total, og skriver kallet til kjøreloggen.
    Med known=False var øktas tidligere total ukjent, så kallets kostnad kan ikke regnes ut og telles ikke."""
    entry.setdefault("totals", {})[session] = run["total"]
    if known:
        entry["cost"] = round(entry.get("cost", 0) + run["cost"], 4)
    line = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "ticket": entry.get("title"), "kind": kind, "session": session,
            "ok": run["ok"], "cost": round(run["cost"], 4) if known else None, "session_total": round(run["total"], 4),
            "turns": run["turns"], "seconds": round(run["seconds"], 1), "tokens_per_turn": run["context"]}
    RUNS.parent.mkdir(parents=True, exist_ok=True)
    with RUNS.open("a") as file:
        file.write(json.dumps(line, ensure_ascii=False) + "\n")
