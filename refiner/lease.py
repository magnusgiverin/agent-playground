"""Låser i Notion, så flere instanser (på flere PC-er) kan dele det samme boardet.

Hver oppgave har egenskapen «Agent», som sier hvilken instans som har den, og i hvilken tilstand:

    magnus-mbp · jobber · 2026-10-26T12:10:00Z · a1b2c3     (en agent jobber nå; låsen utløper kl. 12:10)
    magnus-mbp · hviler · 2026-10-26T11:58:00Z · a1b2c3     (venter på svar eller utvikling; magnus-mbp er «hjemmet»)
    magnus-mbp · feilet · 2026-10-26T11:58:00Z · a1b2c3     (feilet tre ganger; venter på et menneske)
    magnus-mbp · ledig · 2026-10-26T11:58:00Z · a1b2c3      (instansen ble stoppet; hvem som helst tar over med en gang)

Reglene:
- **jobber** og ikke utløpt: ingen andre rører oppgaven. Instansen fornyer låsen mens agenten jobber
  (HEARTBEAT), så den utløper bare hvis instansen er borte (krasj, nettverk, lokket lukket).
- **jobber**, men utløpt: instansen er borte, og hvem som helst tar over med en gang.
- **hviler**: hjemmet har økta (konteksten), så det får første sjanse. Har ingenting skjedd på
  TAKEOVER_AFTER sekunder etter siste aktivitet, tar en annen instans over og leser alt fra Notion.
- **feilet**: ingen rører den før et menneske har gjort noe etterpå.
- **ledig**: instansen ble stoppet (Ctrl-C) midt i, og slapp oppgaven. Hvem som helst tar over med en gang.
- Samme instansnavn, men en annen kjøring: det er vår egen fra før en omstart, og den er fri for oss.

Notion har ingen atomisk «sett hvis», så å ta en lås er: sjekk, skriv, vent litt, les igjen. Den som står der
etterpå, har låsen. Taperen ser det og lar oppgaven være.
"""

import random
import re
import threading
import time
from datetime import datetime, timezone

from .board import NETWORK_ERRORS
from .config import AGENT, INSTANCE, RUN
from .display import dim, paint, say

PROPERTY = "Agent"
TTL = int(AGENT.get("lease_minutes", 10)) * 60           # så lenge en lås varer uten fornyelse
HEARTBEAT = TTL // 3                                       # hvor ofte låsen fornyes mens agenten jobber
TAKEOVER_AFTER = int(AGENT.get("takeover_minutes", 5)) * 60  # så lenge hjemmet får på seg før andre tar over
SETTLE = 3                                                 # sekunder fra skriving til kontroll-lesing
STOPPING = threading.Event()                               # settes når instansen stopper: låsene slippes som «ledig»
ACTIVE = set()                                             # låsene denne instansen holder nå

FORMAT = re.compile(r"^\s*(?P<instance>[^·]+?)\s*·\s*(?P<state>jobber|hviler|feilet|ledig)\s*·\s*(?P<at>\S+)\s*·\s*(?P<run>\w+)\s*$")


def parse(text):
    """Låsen i «Agent», som dict (instance, state, at, run), eller None hvis den er tom eller ukjent."""
    match = FORMAT.match(text or "")
    if not match:
        return None
    try:
        at = datetime.fromisoformat(match["at"].replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None
    return {"instance": match["instance"], "state": match["state"], "at": at, "run": match["run"]}


def format_lease(state, at):
    stamp = datetime.fromtimestamp(at, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"{INSTANCE.name} · {state} · {stamp} · {RUN}"


def mine(lease):
    return bool(lease) and lease["instance"] == INSTANCE.name


def home(ticket):
    """Instansen som sist hadde oppgaven, eller None."""
    return (ticket.get("lease") or {}).get("instance")


def holder(ticket, now=None):
    """Hvem som jobber på oppgaven akkurat nå (en annen instans med en levende lås), ellers None."""
    lease, now = ticket.get("lease"), now or time.time()
    if lease and lease["state"] == "jobber" and lease["at"] > now and not mine(lease):
        return lease["instance"]
    return None


def available(ticket, busy=False, now=None):
    """Kan denne instansen starte en jobb på oppgaven nå? busy: vi har allerede en agent på den."""
    lease, now = ticket.get("lease"), now or time.time()
    if busy:
        return False
    if not lease or mine(lease):
        return True  # ingen har den, eller det er vår egen (også fra før en omstart)
    if lease["state"] == "ledig":
        return True  # instansen ble stoppet og slapp den
    if lease["state"] == "jobber":
        return lease["at"] <= now  # utløpt: instansen er borte
    if lease["state"] == "feilet":
        return ticket.get("human_at", 0) > lease["at"]  # et menneske har gjort noe etter feilen
    # hviler hos en annen: hjemmet får første sjanse
    return now - max(lease["at"], ticket.get("human_at", 0)) >= TAKEOVER_AFTER


def read(board, page_id):
    page = board.api("GET", f"pages/{page_id}")
    return parse("".join(t["plain_text"] for t in page["properties"][PROPERTY]["rich_text"]))


def write(board, page_id, text):
    board.update(page_id, {PROPERTY: {"rich_text": [{"text": {"content": text}}] if text else []}})


class Lease:
    """Låsen på én oppgave mens en agent jobber på den. Fornyes i en egen tråd til release()."""

    def __init__(self, board, page_id, title):
        self.board, self.page_id, self.title = board, page_id, title
        self.lost = threading.Event()   # settes hvis en annen instans har tatt oppgaven
        self.proc = None                # Claude-prosessen som kjører nå; stoppes hvis låsen tapes
        self.on_beat = None             # kalles ved hver fornyelse (for eksempel for å pushe branchen)
        self._stop = threading.Event()

    def claim(self, human_at=0):
        """Tar låsen. Returnerer False hvis en annen instans har den, eller tok den samtidig."""
        try:
            current = read(self.board, self.page_id)
            if not available({"lease": current, "human_at": human_at}):
                return False
            write(self.board, self.page_id, format_lease("jobber", time.time() + TTL))
            time.sleep(SETTLE + random.random() * 2)  # la en som skrev samtidig, bli synlig
            after = read(self.board, self.page_id)
        except NETWORK_ERRORS:
            return False
        if not (mine(after) and after["run"] == RUN):
            say(dim(f"«{self.title}» ble tatt av {after['instance'] if after else 'en annen'} samtidig. Lar den være."))
            return False
        ACTIVE.add(self)
        threading.Thread(target=self._beat, daemon=True).start()
        return True

    def _beat(self):
        while not self._stop.wait(HEARTBEAT):
            try:
                current = read(self.board, self.page_id)
                if current and not (mine(current) and current["run"] == RUN):
                    say(paint(f"«{self.title}» er tatt over av {current['instance']}. Stopper agenten her.", "33"))
                    self.lost.set()
                    if self.proc and self.proc.poll() is None:
                        self.proc.terminate()
                    return
                write(self.board, self.page_id, format_lease("jobber", time.time() + TTL))
                if self.on_beat:
                    self.on_beat()
            except NETWORK_ERRORS:
                continue  # prøv igjen ved neste fornyelse; låsen varer TTL

    def release(self, state="hviler"):
        """Slipper låsen: hviler (vi er fortsatt hjemmet), feilet, eller ledig (instansen stopper)."""
        self._stop.set()
        ACTIVE.discard(self)
        if STOPPING.is_set():
            state = "ledig"
        if self.lost.is_set():
            return
        try:
            write(self.board, self.page_id, format_lease(state, time.time()))
        except NETWORK_ERRORS:
            say(dim(f"Fikk ikke sluppet låsen på «{self.title}». Den utløper av seg selv om {TTL // 60} min."))


def stop_all():
    """Instansen stopper: stopp agentene, så jobbene slipper låsene sine som «ledig» og andre tar over med en gang."""
    STOPPING.set()
    for held in list(ACTIVE):
        if held.proc and held.proc.poll() is None:
            held.proc.terminate()
