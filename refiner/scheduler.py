"""Hva som skal skje neste. Ingen Claude her, bare regler.

Agendaen leses fra boardet, ikke fra minnet til denne instansen. For hver kolonne finnes det én ting å gjøre
når ingen agent jobber på oppgaven, så en oppgave aldri blir liggende fordi en instans stoppet midt i:

    Innboks (Klar for pipeline)   → raffiner (eller avvis hvis den ikke har nøyaktig én Eier)
    Raffinering                   → fortsett raffineringen (egen økt hvis vi har den, ellers les alt fra Notion)
    Trenger avklaring             → vent på svar; når det kommer, fortsett
    Klar                          → bygg (når alt den er Blokkert av, er Fullført)
    Under utvikling               → fortsett utviklingen (fra branchen på GitHub), eller vent hvis den har et Blokkeringsnotat
    Til gjennomgang               → gjennomgå og før inn i main
    paraplyer                     → Klar når alle delene er Klar, Fullført når alle er Fullført

Låsene i Notion (lease.py) bestemmer hvem som får gjøre det, når flere instanser deler boardet.
"""

import time

from . import lease
from .board import AVKLARING, DONE, FULLFORT, GJENNOMGANG, INBOX, KLAR, RAFFINERING, UTVIKLING
from .code import tickets_since_tidy
from .config import AGENT
from .sessions import news_for

TIDY = "rydding"                                   # jobb-ID for ryddingen, som ikke hører til noen oppgave
TIDY_EVERY = int(AGENT.get("tidy_every", 5))       # rydd i agentnotatene etter så mange oppgaver i main
UMBRELLA_NOTES = ("Delt opp i", "Paraply:")        # Blokkeringsnotatet til en paraply, før og etter at den er Klar
PYTHON_KINDS = {"reject", "umbrella_ready", "close_umbrella"}  # jobber som ikke trenger Claude


def is_umbrella(id, ticket, state):
    return any(e.get("parent") == id for e in state.values()) or ticket.get("blocker", "").startswith(UMBRELLA_NOTES)


def parts_in(ticket, snap, statuses):
    """Er alle delene (Blokkert av) i en av disse kolonnene? Deler som ikke er i snapshotet, regnes som Fullført."""
    return all(snap.get(b, {}).get("status", FULLFORT) in statuses for b in ticket["blocked_by"])


def next_jobs(board, snap, state, busy=()):
    """Alt som kan startes nå, i prioritert rekkefølge: (oppgave-ID, slags jobb, mor-ID, hvorfor).
    Oppgaver som allerede har en agent her (busy) eller hos en annen instans, hoppes over, og hver
    oppgave får høyst én jobb."""
    jobs, taken, now = [], set(busy), time.time()

    def add(id, kind, parent, why):
        if id in taken or state.get(id, {}).get("retry_after", 0) > now:
            return  # har en agent, eller feilet nylig: vent litt før neste forsøk
        if id in snap and not lease.available(snap[id], now=now):
            return  # en annen instans har den, eller den er hjemme hos en annen som får første sjanse
        taken.add(id)
        jobs.append((id, kind, parent, why))

    def failed(id):
        return state.get(id, {}).get("failed")

    # 1. Et menneske har gjort noe, og vi har økta: fortsett den med alt den husker
    for id, ticket in snap.items():
        entry = state.get(id)
        if not entry or failed(id) or ticket["status"] == FULLFORT:
            continue
        news = news_for(ticket, entry.get("seen", {}))
        if not news or (ticket["status"] == AVKLARING and not ticket["ready"]):
            continue  # ingenting nytt, eller satt på pause
        what = " og ".join(news)
        if ticket["status"] in (UTVIKLING, GJENNOMGANG) and entry.get("dev"):
            add(id, "dev_feedback", None, f"{what} → fortsetter utviklingsøkta")
        elif entry.get("session"):
            add(id, "after_klar" if ticket["status"] in DONE else "answered", None, f"{what} → fortsetter raffineringsøkta")

    # 2. Paraplyer, uten Claude
    for id, ticket in snap.items():
        if not is_umbrella(id, ticket, state) or not ticket["blocked_by"] or failed(id):
            continue
        if ticket["status"] != FULLFORT and parts_in(ticket, snap, {FULLFORT}):
            add(id, "close_umbrella", None, "alle delene er Fullført → paraplyen er ferdig")
        elif ticket["status"] == RAFFINERING and parts_in(ticket, snap, DONE):
            add(id, "umbrella_ready", None, "alle delene er Klar → paraplyen er Klar")

    # 3. Det som står på agendaen i hver kolonne, når ingen agent jobber på det
    for id, ticket in snap.items():
        entry, status = state.get(id, {}), ticket["status"]
        ours = bool(entry.get("session") or entry.get("dev"))
        if failed(id) or is_umbrella(id, ticket, state) or not (ticket["ready"] or ours):
            continue
        dev = entry.get("dev") or {}
        if status == RAFFINERING:
            if entry.get("session"):
                add(id, "interrupted", None, "raffineringen ble avbrutt → fortsetter økta")
            else:
                add(id, "new", None, "står i Raffinering uten agent → tar den over")
        elif status == AVKLARING and not entry.get("session") and ticket["ready"]:
            last = ticket["all_comments"][-1] if ticket["all_comments"] else None
            if last and last["human"]:
                add(id, "new", None, "ubesvart kommentar i Trenger avklaring → tar den over")
        elif status == GJENNOMGANG and not ticket["blocker"]:
            add(id, "review", None, "står i Til gjennomgang → gjennomgår og fører inn i main")
        elif status == UTVIKLING and not ticket["blocker"]:
            if dev.get("session"):
                add(id, "dev_continue", None, "utviklingen ble avbrutt → fortsetter utviklingsøkta")
            else:
                add(id, "develop", None, "står i Under utvikling uten agent → tar den over")
        elif status == KLAR and parts_in(ticket, snap, {FULLFORT}):
            add(id, "develop", None, "Klar → starter utviklingen")

    # 4. Nye oppgaver i Innboks: start en økt (eller fork morens økt for oppdelte deler)
    for id, ticket in snap.items():
        if not (ticket["ready"] and ticket["status"] in INBOX) or state.get(id, {}).get("session") or id in taken:
            continue
        if len(ticket["owners"]) != 1:
            add(id, "reject", None, "mangler nøyaktig én Eier → fjerner haken")
            continue
        parent = board.split_from(id)
        if parent in taken:
            continue  # morens økt kjører (eller starter nå); forken venter til den er ferdig, så den får med alt
        if parent and state.get(parent, {}).get("session"):
            add(id, "fork", parent, "ny del → starter en økt med morens kontekst")
        else:
            add(id, "new", None, "ny oppgave i Innboks → starter en ny økt")

    # 5. Rydd i agentnotatene når nok oppgaver har gått inn i main siden sist (talt fra main, av alle instansene)
    if TIDY not in taken and (tickets_since_tidy() or 0) >= TIDY_EVERY:
        taken.add(TIDY)
        jobs.append((TIDY, "tidy", None, f"{TIDY_EVERY} oppgaver i main siden sist → rydder i agentnotatene"))
    return jobs


def waiting_for(id, ticket, snap, state):
    """Hvorfor en oppgave i pipelinen ikke startes nå, eller None hvis ingenting holder den igjen."""
    holder = lease.holder(ticket)
    if holder:
        return f"en agent jobber på den hos {holder}"
    if state.get(id, {}).get("failed") or (ticket.get("lease") or {}).get("state") == "feilet":
        return "feilet tre ganger; venter på at noen kommenterer eller endrer den"
    if not lease.available(ticket):
        return f"hviler hos {lease.home(ticket)}, som får første sjanse"
    if is_umbrella(id, ticket, state):
        return "paraply; venter på delene"
    if ticket["status"] == AVKLARING:
        return "venter på svar fra eieren"
    if ticket["blocker"]:
        return f"venter på eieren: {ticket['blocker']}"
    open_parts = [snap[b]["title"] for b in ticket["blocked_by"] if snap.get(b, {}).get("status", FULLFORT) != FULLFORT]
    if open_parts and ticket["status"] == KLAR:
        return f"Blokkert av {len(open_parts)} som ikke er Fullført («{open_parts[0][:40]}»{' …' if len(open_parts) > 1 else ''})"
    return None


def agenda(board, snap, state):
    """Til oversikten: jobbene som starter nå, og (tittel, status, hvorfor) for det i pipelinen som venter."""
    jobs = next_jobs(board, snap, state)
    started = {job[0] for job in jobs}
    waiting = [(t["title"], t["status"], waiting_for(id, t, snap, state)) for id, t in snap.items()
               if id not in started and t["ready"] and t["status"] not in INBOX and t["status"] != FULLFORT]
    return jobs, [w for w in waiting if w[2]]
