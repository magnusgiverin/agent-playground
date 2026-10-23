"""Innstillingene fra refiner.toml og .env, og regelfilene som lastes inn i hver økt."""

import re
import tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent  # prosjektroten

CONFIG = tomllib.loads((HERE / "refiner.toml").read_text())
AGENT = CONFIG.get("agent", {})
SLACK = CONFIG.get("slack", {})
# Hva som skjer når utviklingen er ferdig: «pr» (PR som må godkjennes) eller «main» (rett inn i main)
MERGE = {"mode": "pr", "method": "squash", "approval": "review+label", "label": "godkjent",
         "ai_review": True, "review_rounds": 2, "review_model": "", **CONFIG.get("merge", {})}


def env(name):
    """En verdi fra .env, eller None. Hemmelighetene leses bare her, aldri av agenten."""
    text = (HERE / ".env").read_text() if (HERE / ".env").exists() else ""
    match = re.search(rf"^{name}=(.+)$", text, re.M)
    return match.group(1).strip() if match and match.group(1).strip() else None
# Reglene agenten trenger i hver økt. De sendes i systemprompten (bufret), i stedet for at
# agenten bruker egne steg på å lese dem med Read.
RULES = ["board.md", "sources.md", ".claude/skills/refine-ticket/page-layout.md",
         ".claude/skills/refine-ticket/definition-of-ready.md", ".claude/skills/notion-write/SKILL.md"]


def rules_prompt():
    parts = [f"<fil sti=\"{path}\">\n{(HERE / path).read_text()}\n</fil>" for path in RULES]
    return ("Disse regelfilene for raffineringsagenten er allerede lastet. Ikke les dem med Read.\n\n"
            + "\n\n".join(parts))
