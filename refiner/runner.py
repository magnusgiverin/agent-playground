"""Kjører én jobb fra scheduler.py i riktig Claude-økt, med lås i Notion så ingen annen instans tar den samtidig.

En oppgave har opptil to økter: raffineringsøkta (entry["session"]) fra Innboks til Klar, og utviklingsøkta
(entry["dev"]["session"]) fra Klar til main. Utviklingsøkta forkes fra raffineringsøkta, så den husker
Figma-framene og koden. Har denne instansen ikke økta (en annen instans startet oppgaven), starter den en ny
økt som leser alt fra Notion og fortsetter fra branchen på GitHub.
"""

import time
import uuid

from . import lease as leases
from .board import AVKLARING, FULLFORT, KLAR, NETWORK_ERRORS, RAFFINERING, UTVIKLING
from .claude import COMPACT, DEV_COMPACT, run_claude
from .code import checkpoint, commits_ahead, prepare_workdir, rebase_on_main, sync_code, uncommitted, workdir
from .config import AGENT, CACHE, INSTANCE
from .display import dim, paint, say, set_tag, short
from .scheduler import PYTHON_KINDS
from .sessions import record, save_state, session_exists, session_total
from .shipping import review_and_publish

RETRY_AFTER = [60, 300, 900]                        # vent 1, 5 og 15 minutter etter en feil før neste forsøk
COMPACT_AT = int(AGENT.get("compact_at", 60_000))   # komprimer bare økter som faktisk er store (tokens per steg)
ASSUME = {"on": False}                              # settes av refine.py --antakelser: spør bare om det absolutt kritiske
DEV_KINDS = {"develop", "dev_feedback", "dev_continue", "dev_conflict", "review"}
ROOT = CACHE.parent


def run_job(board, state, job, snap, verbose):
    """Tar låsen, kjører jobben og slipper låsen igjen, uansett hvordan jobben ender."""
    id, kind, _, _ = job
    ticket = snap[id]
    set_tag(short(ticket["title"], 28))
    lease = leases.Lease(board, id, ticket["title"])
    if not lease.claim(ticket.get("human_at", 0)):
        return
    outcome = "hviler"
    try:
        if kind in PYTHON_KINDS:
            python_job(board, kind, id, ticket)
        else:
            outcome = claude_job(board, state, job, snap, verbose, lease)
            if outcome == "feilet":
                board.comment(id, "🤖 Agenten har feilet tre ganger på rad på denne oppgaven, så den står stille. Kommenter "
                                  "eller endre oppgaven, så prøver en agent igjen.")
    except Exception:
        state[id]["retry_after"] = time.time() + RETRY_AFTER[0]  # ikke prøv igjen i hver runde hvis feilen består
        raise
    finally:
        lease.release(outcome)


def python_job(board, kind, id, ticket):
    if kind == "reject":
        board.reject(id, ticket)
        say(paint(f"● «{ticket['title']}» mangler nøyaktig én Eier, så jeg fjernet haken og sa ifra i en kommentar.", "33"))
    elif kind == "umbrella_ready":
        board.move(id, KLAR)
        board.set_blocker(id, f"Paraply: delt opp i {len(ticket['blocked_by'])} oppgaver. Fullføres når alle er Fullført.")
        board.comment(id, "🤖 Alle delene er klare.")
        say(paint(f"«{ticket['title']}»: alle delene er Klar → Klar", "32"))
    elif kind == "close_umbrella":
        board.move(id, FULLFORT)
        board.set_blocker(id, "")
        board.comment(id, "🤖 Alle delene er Fullført, så denne er ferdig.")
        say(paint(f"«{ticket['title']}»: alle delene er Fullført → Fullført", "32"))


def context(ticket, code, commit, comments, snap, id):
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
    # andre agenter som jobber nå, på denne eller andre PC-er, så agenten ikke tråkker på arbeidet deres
    now = time.time()
    others = [(t["title"], t["status"], t["lease"]["instance"]) for other, t in snap.items()
              if other != id and t.get("lease") and t["lease"]["state"] == "jobber" and t["lease"]["at"] > now]
    if others:
        lines.append("- Andre agenter jobber samtidig på: " + "; ".join(
            f"«{short(title, 50)}» ({status}, {where})" for title, status, where in others[:8]))
    if ASSUME["on"]:
        lines.append("- Modus: flere antakelser. Spør bare om det som er absolutt kritisk, og anta resten "
                     "(se «Modus: flere antakelser» i definition-of-ready.md).")
    if comments:
        lines.append("- Kommentarer fra mennesker du ikke har sett ennå:")
        lines += [f"  > {short(text, 1500)}" for _, text in comments]
    else:
        lines.append("- Ingen nye kommentarer fra mennesker.")
    return "\n".join(lines)


def dev_rules(folder, branch, base):
    return (f"Arbeidskopi: {folder}/repo, på branchen {branch} (laget fra {base}). Commit på branchen. Når du skriver "
            f"{folder}/rapport.md, går endringen til en kritisk gjennomgang (oppgaven flyttes til Til gjennomgang), og "
            f"blir den godkjent, legger refine.py branchen oppå siste {base} og fører den rett inn i {base}, uten PR. "
            "Skriv den bare når alle sjekkene er grønne.\n\n"
            "Kommandoregler (gjelder også om du husker noe annet): bruk aldri `cd`. Kjør én kommando om gangen med "
            f"arbeidskopien som argument: `git -C {folder}/repo status|diff|log|add|commit -m \"…\"|rm <fil>|mv <fra> <til>|fetch|rebase`, "
            f"`npm --prefix {folder}/repo install|run <skript>|test`, `ls {folder}/repo`, `mkdir -p {folder}/repo/…`. "
            "Les og skriv filer med Read, Write og Edit. Lag package.json selv i stedet for `npm create`.")


def claude_job(board, state, job, snap, verbose, lease):
    """Kjører én Claude-tur for oppgaven. Returnerer hvordan låsen skal slippes: «hviler» eller «feilet»."""
    id, kind, parent, why = job
    ticket = snap[id]
    entry = state[id]  # lagt inn av hovedløkka før jobben startet
    develop = kind in DEV_KINDS
    if kind in ("answered", "after_klar", "interrupted") and not session_exists(entry.get("session")):
        kind, why = "new", why + " (har ikke økta her, starter en ny fra Notion)"
    if kind in ("dev_feedback", "dev_continue", "dev_conflict") and not session_exists((entry.get("dev") or {}).get("session")):
        kind, why = "develop", why + " (har ikke utviklingsøkta her, starter en ny)"

    try:
        code, commit = sync_code()
        if develop:
            path, branch, base = prepare_workdir(id, ticket["title"])
            lease.on_beat = lambda: checkpoint(path, branch, base)  # del commitene mens agenten jobber
            if (kind in ("develop", "dev_continue") and commits_ahead(path, base) and not uncommitted(path)
                    and not rebase_on_main(path, base)):
                kind, why = "dev_conflict", why + " (branchen har konflikt med main)"
    except RuntimeError as error:
        say(paint(f"Git feilet: {error}", "31"))
        entry["retry_after"] = time.time() + RETRY_AFTER[0]
        return "hviler"

    if kind == "review":  # Til gjennomgang: ingen Claude-tur for utvikleren, rett til gjennomgangen
        entry.setdefault("dev", {}).setdefault("branch", branch)
        entry["title"] = ticket["title"]
        report = path.parent / "rapport.md"
        if not commits_ahead(path, base):
            board.move(id, UTVIKLING)
            board.comment(id, f"🤖 Oppgaven sto i Til gjennomgang, men branchen har ingen endringer mot {base}. Bygger den på nytt.")
            return "hviler"
        if not report.exists():  # gjennomgangen ble avbrutt på en annen PC: rapporten var lokal der
            report.write_text(f"{ticket['title']}\n\nRapporten ble skrevet på en annen instans. Se commitene på branchen {branch}.")
        say(paint(f"=== {time.strftime('%H:%M:%S')} «{ticket['title']}»: {why} ===", "1"))
        review_and_publish(board, state, id, entry, ticket, path, branch, base, verbose, lease)
        save_state(state)
        return "hviler"

    seen = entry.get("seen", {}).get("comment")
    comments = ticket["comments"] or board.human_comments(id)
    ids = [c for c, _ in comments]
    unseen = comments[ids.index(seen) + 1:] if seen in ids else comments
    seen_inline = set(entry.get("seen", {}).get("inline", []))
    unseen += [(c, f"(kommentar på en linje i oppgaven) {text}") for c, text in ticket.get("inline", []) if c not in seen_inline]
    url, title = ticket["url"], ticket["title"]

    if develop:
        folder = workdir(id).relative_to(ROOT)
        where = dev_rules(folder, branch, base)
        ask = {
            "develop": f"/develop-ticket {url}\n\n{where}",
            "dev_feedback": f"Det har kommet nye kommentarer på «{title}» ({url}) mens den er i {ticket['status']}. "
                            f"Følg /develop-ticket steg 0, «Fortsett»: svar på dem, og fiks koden hvis det trengs. {where}",
            "dev_continue": f"Forrige utvikling av «{title}» ({url}) ble avbrutt. Fortsett der du slapp. Sjekk med git status "
                            f"og git log hva som allerede er gjort. {where}",
            "dev_conflict": f"Commitene for «{title}» ({url}) kan ikke legges oppå siste {base}, fordi en annen oppgave har "
                            f"endret de samme linjene. Kjør `git -C {folder}/repo fetch origin {base}` og "
                            f"`git -C {folder}/repo rebase origin/{base}`, løs konfliktene med Edit, `git -C {folder}/repo add <fil>`, "
                            f"`git -C {folder}/repo -c core.editor=true rebase --continue`, kjør alle "
                            f"sjekkene på nytt, og skriv rapport.md når alt er grønt. {where}",
        }[kind]
        if kind == "develop" and commits_ahead(path, base):
            ask += (f"\n\nBranchen har allerede {commits_ahead(path, base)} commit(s) fra en tidligere runde, kanskje på en annen "
                    "PC. Les `git log` og diffen først, og fortsett derfra i stedet for å begynne på nytt.")
    else:
        ask = {
            "new": f"/refine-ticket {url}",
            "fork": f"/refine-ticket {url}\n\nDette er en del som er delt ut fra «{snap.get(parent, {}).get('title', 'moroppgaven')}». "
                    "Du har konteksten fra den oppgaven (Notion, Figma og koden), så bruk den, og les bare det som er nytt for denne delen.",
            "answered": f"Eieren har svart på eller endret «{title}» ({url}). Fortsett raffineringen der du slapp, slik /refine-ticket "
                        "steg 0, «Fortsett», beskriver. Oppgaven står i Trenger avklaring, så vakten trengs ikke.",
            "after_klar": f"Noen har kommentert eller endret «{title}» ({url}) etter at den ble Klar. Følg /refine-ticket steg 0, "
                          "«Fortsett»: kjør vakten for denne oppgaven og svar på kommentaren. Er det en reell endring i kravene, raffinerer du videre.",
            "interrupted": f"Forrige kjøring på «{title}» ({url}) ble avbrutt før den var ferdig. Fortsett der du slapp, "
                           "og sjekk i Notion hva som faktisk ble skrevet før du skriver mer.",
        }[kind]
        if kind == "new" and ticket["status"] not in (None, "Innboks"):
            ask += (f"\n\nOppgaven står allerede i {ticket['status']}: en annen agent (kanskje på en annen PC) har jobbet på den. "
                    "Les Raffinering-seksjonen og kommentarene, og fortsett derfra i stedet for å begynne på nytt.")
    prompt = ask + "\n\n" + context(ticket, code, commit, unseen, snap, id)

    baseline = 0.0  # hva økta allerede har kostet; Claude Code oppgir totalen, ikke kostnaden for kallet
    if kind == "new":
        entry["session"] = str(uuid.uuid4())
        flags = ["--session-id", entry["session"]]
    elif kind == "fork":
        entry["session"], entry["parent"] = str(uuid.uuid4()), parent
        flags = ["--resume", state[parent]["session"], "--fork-session", "--session-id", entry["session"]]
        baseline = session_total(state, state[parent]["session"])  # forken arver morens total (None: ukjent)
    elif kind == "develop":
        entry["dev"] = {"session": str(uuid.uuid4()), "branch": branch}
        if session_exists(entry.get("session")):  # husk Figma og koden fra raffineringen
            flags = ["--resume", entry["session"], "--fork-session", "--session-id", entry["dev"]["session"]]
            baseline = session_total(state, entry["session"])
        else:
            flags = ["--session-id", entry["dev"]["session"]]
        if ticket["status"] != UTVIKLING:
            board.move(id, UTVIKLING)
    elif develop:
        flags = ["--resume", entry["dev"]["session"]]
    else:
        flags = ["--resume", entry["session"]]
    # det eieren har gjort så langt, regnes som sett; det som kommer under økta, plukkes opp neste gang
    entry["seen"] = {"comment": ticket["comment"], "edited": ticket["edited"],
                     "inline": [c for c, _ in ticket.get("inline", [])]}
    entry["title"] = title
    save_state(state)

    session = entry["dev"]["session"] if develop else entry["session"]
    if flags[:1] == ["--resume"] and "--fork-session" not in flags:
        baseline = session_total(state, session)
    known = baseline is not None
    say(paint(f"=== {time.strftime('%H:%M:%S')} «{title}»: {why} ===", "1"))
    say(dim(f"Økt …{session[-6:]} · {INSTANCE.name} · kode {commit}" + (f" · branch {branch} → {base}" if develop else "")))
    run = run_claude(prompt, flags, verbose, "develop" if develop else "refine", baseline or 0.0, lease)
    if lease.lost.is_set():
        save_state(state)
        return "hviler"  # en annen instans har tatt over; den eier låsen nå (release rører den ikke)
    ok = run["ok"] and not leases.STOPPING.is_set()
    record(state, entry, session, kind, run, known)
    if develop:
        checkpoint(path, branch, base)
        if ok:
            review_and_publish(board, state, id, entry, ticket, path, branch, base, verbose, lease)

    time.sleep(3)  # gi Notion et øyeblikk på å vise agentens egne endringer
    try:
        now = board.snapshot().get(id, {})
    except NETWORK_ERRORS:
        now = {}
    status = now.get("status")
    if ok and status == RAFFINERING and not develop:
        ok = False  # økta sa ferdig, men oppgaven står fortsatt i Raffinering: regn det som en feil, så den ikke går i ring
        say(paint(f"«{title}» står fortsatt i Raffinering etter økta.", "33"))
    outcome = after_run(state, entry, title, ok)
    if status == KLAR and not develop:
        say(paint(f"«{title}» er Klar. Utviklingen starter av seg selv. Så langt: ${entry.get('cost', 0):.2f}", "32"))
    elif status == FULLFORT:
        say(paint(f"«{title}» er ført inn i main og Fullført. Totalt for oppgaven: ${entry.get('cost', 0):.2f}", "32"))
    elif ok and (status == AVKLARING or (status == UTVIKLING and now.get("blocker"))):
        if run["context"] >= COMPACT_AT:
            # komprimer før hvilen: når eieren svarer, sendes da et kort sammendrag i stedet for hele historikken
            say(dim(f"«{title}» venter på svar. Økta er stor (~{run['context'] // 1000}k tokens), så den komprimeres først…"))
            compact = run_claude(DEV_COMPACT if develop else COMPACT, ["--resume", session], verbose,
                                 "develop" if develop else "refine", session_total(state, session) or 0.0, lease)
            record(state, entry, session, "compact", compact)
        say(dim(f"«{title}» venter på svar fra eieren, og økta hviler til noe skjer. Så langt for oppgaven: ${entry.get('cost', 0):.2f}"))
    save_state(state)
    return outcome


def after_run(state, entry, title, ok):
    """Teller feil og bestemmer når neste forsøk kan komme. Returnerer hvordan låsen skal slippes."""
    if leases.STOPPING.is_set():
        return "ledig"  # avbrutt fordi instansen stopper; det er ikke oppgavens feil
    entry["attempts"] = 0 if ok else entry.get("attempts", 0) + 1
    entry.pop("retry_after", None)
    if entry["attempts"] >= len(RETRY_AFTER):
        entry["failed"] = True
        say(paint(f"«{title}» har feilet {entry['attempts']} ganger på rad. Jeg lar den være til noen gjør noe med den.", "31"))
        return "feilet"
    if not ok:
        wait = RETRY_AFTER[entry["attempts"] - 1]
        entry["retry_after"] = time.time() + wait
        say(paint(f"«{title}» feilet. Prøver igjen om {wait // 60} min.", "33"))
    return "hviler"
