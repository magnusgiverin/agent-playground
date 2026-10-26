"""Innstillingene. To slags:

- **Oppførsel** (modeller, parallelitet, gjennomgang …) ligger i refiner.toml og er lik for alle som kjører agenten.
- **Oppsett for instansen** (hvilket Notion-board, GitHub-repo, Figma-fil og Slack-kanal den kobles til, og
  hemmelighetene) er miljøvariabler. De leses fra miljøet først, og så fra env-fila (`.env`, eller den som er
  valgt med `refine.py --env`). Slik kan hver PC, eller hvert team, koble den samme koden til sine egne systemer.

Alle miljøvariablene står i ENV_VARS og i .env.example.
"""

import os
import re
import socket
import tomllib
import uuid
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent  # prosjektroten

CONFIG = tomllib.loads((HERE / "refiner.toml").read_text())
AGENT = CONFIG.get("agent", {})
DESIGN = CONFIG.get("design", {})

# Env-fila for denne instansen. refine.py setter REFINER_ENV fra --env før noe annet importeres,
# og MCP-serveren i .mcp.json leser den samme fila.
ENV_FILE = HERE / os.environ.get("REFINER_ENV", ".env")

# navn → (påkrevd, hemmelig, forklaring). Rekkefølgen er den i .env.example og i README.
ENV_VARS = {
    "NOTION_API_KEY": (True, True, "nøkkel til Notion-integrasjonen som har tilgang til boardet"),
    "NOTION_BOARD": (True, False, "URL til Notion-siden med oppgaveboardet"),
    "GITHUB_REPO": (False, False, "URL til produktets GitHub-repo (eller sett CODE_LOCAL)"),
    "GITHUB_BRANCH": (False, False, "branchen det bygges mot (standard main)"),
    "CODE_LOCAL": (False, False, "les en lokal mappe i stedet for GitHub (bare raffinering)"),
    "FIGMA_FILE": (False, False, "URL til produktets Figma-fil, der skisser tegnes"),
    "FIGMA_TOKEN": (False, True, "personlig Figma-nøkkel, så frames postes som bilder i Slack"),
    "SLACK_BOT_TOKEN": (False, True, "Bot User OAuth Token (xoxb-…)"),
    "SLACK_APP_TOKEN": (False, True, "App-Level Token for Socket Mode (xapp-…)"),
    "SLACK_CHANNEL": (False, False, "kanal-ID-en Slack-broen bruker (for eksempel C0123ABC)"),
    "SLACK_TEST_OWNER": (False, False, "bare for testing: Notion-bruker-ID som blir Eier på alle oppgaver fra /new"),
    "INSTANCE_NAME": (False, False, "navnet på denne instansen, unikt blant PC-ene som deler boardet"),
}


def _env_file():
    if not ENV_FILE.exists():
        return {}
    values = {}
    for line in ENV_FILE.read_text().splitlines():
        match = re.match(r"\s*(?:export\s+)?([A-Z][A-Z0-9_]*)\s*=\s*(.*?)\s*$", line)
        if match and not line.lstrip().startswith("#"):
            values[match.group(1)] = match.group(2).strip("\"'")
    return values


_FILE = _env_file()


def env(name):
    """En verdi fra miljøet eller env-fila, eller None. Hemmelighetene leses bare her, aldri av agenten."""
    value = os.environ.get(name) or _FILE.get(name)
    return value.strip() if value and value.strip() else None


def _instance_suffix():
    """«» for .env, «kunde-a» for .env.kunde-a. Skiller flere instanser på samme PC fra hverandre."""
    return ENV_FILE.name.removeprefix(".env").lstrip(".-_")


@dataclass(frozen=True)
class Instance:
    """Hva denne instansen er koblet til. Ingenting her er hemmelig, så det kan vises til agenten."""
    name: str
    board: str
    github: str | None
    branch: str
    local: str | None
    figma: str | None
    slack_channel: str | None
    test_owner: str | None

    def describe(self):
        """Oppsettet som linjer, til terminalen og til agentens kontekst."""
        code = f"{self.github}@{self.branch}" if self.github else f"lokal mappe {self.local}" if self.local else "(ikke satt)"
        return [f"Instans: {self.name}", f"Notion-board: {self.board}", f"Kode: {code}",
                f"Figma-fil: {self.figma or '(ikke satt)'}", f"Slack-kanal: {self.slack_channel or '(av)'}"]


INSTANCE = Instance(
    name=env("INSTANCE_NAME") or "-".join(filter(None, [socket.gethostname().split(".")[0].lower(), _instance_suffix()])),
    board=env("NOTION_BOARD") or "",
    github=(env("GITHUB_REPO") or "").rstrip("/").removesuffix(".git") or None,
    branch=env("GITHUB_BRANCH") or "main",
    local=env("CODE_LOCAL"),
    figma=env("FIGMA_FILE"),
    slack_channel=env("SLACK_CHANNEL"),
    test_owner=env("SLACK_TEST_OWNER"),
)
RUN = uuid.uuid4().hex[:6]  # denne kjøringen av instansen; skiller en levende instans fra en som krasjet med samme navn

# Lokale filer for instansen. Med flere instanser på samme PC får hver sin mappe for tilstanden, og sine
# egne arbeidskopier (prefikset), så de ikke tråkker på hverandre.
CACHE = HERE / ".cache"
STATE_DIR = CACHE / _instance_suffix() if _instance_suffix() else CACHE
PREFIX = f"{_instance_suffix()}-" if _instance_suffix() else ""


def missing_settings():
    """Påkrevde miljøvariabler som mangler, til en tydelig feilmelding ved oppstart."""
    missing = [name for name, (required, _, _) in ENV_VARS.items() if required and not env(name)]
    if not (INSTANCE.github or INSTANCE.local):
        missing.append("GITHUB_REPO (eller CODE_LOCAL)")
    return missing


# Reglene agenten trenger i hver økt. De sendes i systemprompten (bufret), i stedet for at
# agenten bruker egne steg på å lese dem med Read.
RULES = ["board.md", "sources.md", ".claude/skills/refine-ticket/page-layout.md",
         ".claude/skills/refine-ticket/definition-of-ready.md", ".claude/skills/notion-write/SKILL.md"]


def rules_prompt():
    parts = [f"<fil sti=\"{path}\">\n{(HERE / path).read_text()}\n</fil>" for path in RULES]
    setup = "\n".join(f"- {line}" for line in INSTANCE.describe())
    return ("Disse regelfilene for raffineringsagenten er allerede lastet. Ikke les dem med Read.\n\n"
            f"Oppsettet for denne instansen (fra miljøvariablene; les aldri .env):\n{setup}\n\n"
            + "\n\n".join(parts))
