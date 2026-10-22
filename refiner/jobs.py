"""Bestemmer hva som skal skje neste, og kjører det i riktig økt."""

import time
import uuid

from .board import DONE, INBOX, NETWORK_ERRORS
from .claude import COMPACT, run_claude
from .code import sync_code
from .display import dim, paint, short
from .sessions import news_for, save_state, session_exists


def next_job(board, snap, state):
    """Finner det neste som skal gjøres: (oppgave-ID, slags jobb, mor-ID, hvorfor), eller None."""
    # 1. Oppgaver som har en økt og der eieren har gjort noe: fortsett økta
    for id, ticket in snap.items():
        entry = state.get(id)
        if not entry or entry.get("failed"):
            continue
        news = news_for(ticket, entry.get("seen", {}))
        if not news or (ticket["status"] == "Trenger avklaring" and not ticket["ready"]):
            continue  # ingenting nytt, eller satt på pause
        kind = "after_klar" if ticket["status"] in DONE else "answered"
        return id, kind, None, f"{' og '.join(news)} → fortsetter økta"

    # 2. Paraplyer der alle delene er Klar: avslutt dem i morens økt
    for id, entry in state.items():
        ticket = snap.get(id)
        children = [c for c, e in state.items() if e.get("parent") == id]
        if (ticket and ticket["status"] == "Raffinering" and children and not entry.get("failed")
                and all(snap.get(c, {}).get("status") in DONE for c in children)):
            return id, "umbrella", None, "alle delene er Klar → avslutter paraplyen"

    # 3. Økter som ble avbrutt midt i en runde (står fortsatt i Raffinering, uten å være paraply)
    for id, entry in state.items():
        ticket = snap.get(id)
        is_umbrella = any(e.get("parent") == id for e in state.values())
        if ticket and ticket["status"] == "Raffinering" and not is_umbrella and not entry.get("failed"):
            return id, "interrupted", None, "forrige kjøring ble avbrutt → fortsetter økta"

    # 4. Nye oppgaver i Innboks: start en økt (eller fork morens økt for oppdelte deler)
    for id, ticket in snap.items():
        if not (ticket["ready"] and ticket["status"] in INBOX) or state.get(id, {}).get("session"):
            continue
        if len(ticket["owners"]) != 1:
            board.reject(id, ticket)
            print(paint(f"● «{ticket['title']}» mangler nøyaktig én Eier, så jeg fjernet haken og sa ifra i en kommentar.", "33"))
            continue
        parent = board.split_from(id)
        if parent and state.get(parent, {}).get("session"):
            return id, "fork", parent, "ny del → starter en økt med morens kontekst"
        return id, "new", None, "ny oppgave i Innboks → starter en ny økt"
    return None


def context(ticket, code, commit, comments):
    """Det refine.py allerede har hentet, så agenten slipper å bruke steg og tokens på det."""
    yes_no = "ja" if ticket["ready"] else "nei"
    lines = [
        "Kontekst fra refine.py (allerede hentet, ikke hent det på nytt):",
        f"- Kodeargumenter: {code} (siste commit: {commit})",
        f"- Egenskaper: Status={ticket['status'] or '(ingen)'} · Eier=user://{ticket['owners'][0] if ticket['owners'] else '(ingen)'}"
        f" · Etiketter={', '.join(ticket['tags']) or '(ingen)'} · Størrelse={ticket['size'] or '(ingen)'}"
        f" · Figma-lenke={ticket['figma'] or '(ingen)'} · Blokkert av={len(ticket['blocked_by'])} oppgave(r)"
        f" · Klar for pipeline={yes_no}",
    ]
    if comments:
        lines.append("- Kommentarer fra mennesker du ikke har sett ennå:")
        lines += [f"  > {short(text, 1500)}" for _, text in comments]
    else:
        lines.append("- Ingen nye kommentarer fra mennesker.")
    return "\n".join(lines)


def run_job(board, state, job, snap, verbose):
    id, kind, parent, why = job
    ticket = snap[id]
    entry = state.setdefault(id, {"title": ticket["title"]})
    if kind not in ("new", "fork") and not session_exists(entry.get("session", "")):
        kind, why = "new", why + " (fant ikke økta, starter en ny)"  # økta er borte, start på nytt fra Notion

    try:
        code, commit = sync_code()
    except RuntimeError as error:
        print(paint(f"\nKodesynken feilet: {error}", "31"))
        time.sleep(60)
        return
    seen = entry.get("seen", {}).get("comment")
    comments = ticket["comments"] or board.human_comments(id)
    ids = [c for c, _ in comments]
    unseen = comments[ids.index(seen) + 1:] if seen in ids else comments
    url, title = ticket["url"], ticket["title"]
    ask = {
        "new": f"/refine-ticket {url}",
        "fork": f"/refine-ticket {url}\n\nDette er en del som er delt ut fra «{snap.get(parent, {}).get('title', 'moroppgaven')}». "
                "Du har konteksten fra den oppgaven (Notion, Figma og koden), så bruk den, og les bare det som er nytt for denne delen.",
        "answered": f"Eieren har svart på eller endret «{title}» ({url}). Fortsett raffineringen der du slapp, slik /refine-ticket "
                    "steg 0, «Fortsett», beskriver. Oppgaven står i Trenger avklaring, så vakten trengs ikke.",
        "after_klar": f"Noen har kommentert eller endret «{title}» ({url}) etter at den ble Klar. Følg /refine-ticket steg 0, "
                      "«Fortsett»: kjør vakten for denne oppgaven og svar på kommentaren. Er det en reell endring i kravene, raffinerer du videre.",
        "umbrella": f"Alle delene av paraplyoppgaven «{title}» ({url}) er nå Klar eller lenger. Avslutt paraplyen: sett den til Klar, "
                    "tøm Blokkeringsnotat, legg til en logglinje med Tilstand, og post «🤖 Alle delene er klare.»",
        "interrupted": f"Forrige kjøring på «{title}» ({url}) ble avbrutt før den var ferdig. Fortsett der du slapp, "
                       "og sjekk i Notion hva som faktisk ble skrevet før du skriver mer.",
    }[kind]
    prompt = ask + "\n\n" + context(ticket, code, commit, unseen)

    if kind == "new":
        entry["session"] = str(uuid.uuid4())
        flags = ["--session-id", entry["session"]]
    elif kind == "fork":
        entry["session"], entry["parent"] = str(uuid.uuid4()), parent
        flags = ["--resume", state[parent]["session"], "--fork-session", "--session-id", entry["session"]]
    else:
        flags = ["--resume", entry["session"]]
    # det eieren har gjort så langt, regnes som sett; det som kommer under økta, plukkes opp neste gang
    entry["seen"] = {"comment": ticket["comment"], "edited": ticket["edited"]}
    entry["title"] = title
    save_state(state)

    print(paint(f"\n=== {time.strftime('%H:%M:%S')} «{title}»: {why} ===", "1"))
    print(dim(f"Økt …{entry['session'][-6:]} · kode {commit}"))
    ok, cost = run_claude(prompt, flags, verbose)
    entry["cost"] = round(entry.get("cost", 0) + cost, 4)

    entry["attempts"] = 0 if ok else entry.get("attempts", 0) + 1
    if entry["attempts"] >= 3:
        entry["failed"] = True
        print(paint(f"«{title}» har feilet 3 ganger på rad. Jeg lar den være til noen gjør noe med den.", "31"))
    time.sleep(3)  # gi Notion et øyeblikk på å vise agentens egne endringer
    try:
        status = board.snapshot().get(id, {}).get("status")
    except NETWORK_ERRORS:
        status = None
    if status in DONE:
        entry["closed"] = True
        print(paint(f"«{title}» er Klar. Økta er avsluttet. Totalt for oppgaven: ${entry['cost']:.2f}", "32"))
    elif status == "Trenger avklaring" and ok:
        # komprimer før hvilen: når eieren svarer, sendes da et kort sammendrag i stedet for hele historikken
        print(dim(f"«{title}» venter på svar fra eieren. Komprimerer økta før den hviler…"))
        compacted, cost = run_claude(COMPACT, ["--resume", entry["session"]], verbose)
        entry["cost"] = round(entry["cost"] + cost, 4)
        print(dim(f"Økta hviler til noe skjer. Så langt for oppgaven: ${entry['cost']:.2f}"))
    save_state(state)
