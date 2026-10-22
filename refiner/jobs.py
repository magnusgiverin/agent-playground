"""Bestemmer hva som skal skje neste, og kjører det i riktig økt.

En oppgave har opptil to økter: raffineringsøkta (entry["session"]) fra Innboks til Klar, og
utviklingsøkta (entry["dev"]["session"]) fra Klar til koden er pushet til main. Utviklingsøkta
forkes fra raffineringsøkta, så den husker Figma-framene og koden. Agenten jobber på en egen
branch per oppgave. Når den melder at alt er grønt (rapport.md), legger Python branchen oppå siste
main og pusher den rett inn i main. Det lages ingen PR.
"""

import json
import threading
import time
import uuid

from .board import DONE, INBOX, NETWORK_ERRORS
from .claude import COMPACT, DEV_COMPACT, run_claude
from .code import commits_ahead, git, prepare_workdir, push_to_main, rebase_on_main, repo, sync_code, uncommitted, workdir
from .config import AGENT, HERE
from .display import dim, paint, say, set_tag, short
from .sessions import news_for, save_state, session_exists

DEVELOPING = {"Under utvikling"}
PUBLISH_LOCK = threading.Lock()  # én rebase og push til main om gangen, så to ferdige oppgaver ikke kolliderer
RUNS = HERE / ".cache" / "runs.jsonl"  # én linje per Claude-kall: hva, hvor lenge, og hva det kostet
RETRY_AFTER = [60, 300, 900]           # vent 1, 5 og 15 minutter etter en feil før neste forsøk
COMPACT_AT = int(AGENT.get("compact_at", 60_000))  # komprimer bare økter som faktisk er store (tokens per steg)


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


def is_umbrella(id, ticket, state):
    return any(e.get("parent") == id for e in state.values()) or ticket.get("blocker", "").startswith("Delt opp i")


def next_jobs(board, snap, state, busy=()):
    """Alt som kan startes nå, i prioritert rekkefølge: (oppgave-ID, slags jobb, mor-ID, hvorfor).
    Oppgaver som allerede har en agent i gang (busy), hoppes over, og hver oppgave får høyst én jobb."""
    jobs, taken = [], set(busy)

    now = time.time()

    def add(id, kind, parent, why):
        if state.get(id, {}).get("retry_after", 0) > now:
            return  # feilet nylig: vent litt før neste forsøk
        if id not in taken:
            taken.add(id)
            jobs.append((id, kind, parent, why))

    # 1. Oppgaver der et menneske har gjort noe: fortsett riktig økt
    for id, ticket in snap.items():
        entry = state.get(id)
        if id in taken or not entry or entry.get("failed") or ticket["status"] == "Fullført":
            continue
        news = news_for(ticket, entry.get("seen", {}))
        if not news or (ticket["status"] == "Trenger avklaring" and not ticket["ready"]):
            continue  # ingenting nytt, eller satt på pause
        what = " og ".join(news)
        if ticket["status"] in DEVELOPING and entry.get("dev"):
            add(id, "dev_feedback", None, f"{what} → fortsetter utviklingsøkta")
        elif entry.get("session"):
            kind = "after_klar" if ticket["status"] in DONE else "answered"
            add(id, kind, None, f"{what} → fortsetter raffineringsøkta")

    # 2. Paraplyer der alle delene er Klar: avslutt dem i morens økt
    for id, entry in state.items():
        ticket = snap.get(id)
        children = [c for c, e in state.items() if e.get("parent") == id]
        if (ticket and ticket["status"] == "Raffinering" and children and not entry.get("failed")
                and all(snap.get(c, {}).get("status") in DONE for c in children)):
            add(id, "umbrella", None, "alle delene er Klar → avslutter paraplyen")

    # 3. Økter som ble avbrutt midt i en runde
    for id, entry in state.items():
        ticket = snap.get(id)
        if not ticket or entry.get("failed"):
            continue
        if ticket["status"] == "Raffinering" and not is_umbrella(id, ticket, state) and entry.get("session"):
            add(id, "interrupted", None, "forrige raffinering ble avbrutt → fortsetter økta")
        dev = entry.get("dev")
        if ticket["status"] == "Under utvikling" and dev and dev.get("conflict"):
            add(id, "dev_conflict", None, "konflikt med main → agenten løser den")
        elif ticket["status"] == "Under utvikling" and dev and not dev.get("pushed") and not ticket["blocker"]:
            add(id, "dev_continue", None, "forrige utvikling ble avbrutt → fortsetter utviklingsøkta")

    # 4. Nye oppgaver i Innboks: start en økt (eller fork morens økt for oppdelte deler)
    for id, ticket in snap.items():
        if id in taken or not (ticket["ready"] and ticket["status"] in INBOX) or state.get(id, {}).get("session"):
            continue
        if len(ticket["owners"]) != 1:
            board.reject(id, ticket)
            say(paint(f"● «{ticket['title']}» mangler nøyaktig én Eier, så jeg fjernet haken og sa ifra i en kommentar.", "33"))
            continue
        parent = board.split_from(id)
        if parent in taken:
            continue  # morens økt kjører (eller starter nå); forken venter til den er ferdig, så den får med alt
        if parent and state.get(parent, {}).get("session"):
            add(id, "fork", parent, "ny del → starter en økt med morens kontekst")
        else:
            add(id, "new", None, "ny oppgave i Innboks → starter en ny økt")

    # 5a. Oppgaver fra den gamle PR-flyten (Til gjennomgang med en branch): før branchen inn i main, uten Claude
    old = [(id, state[id]["dev"]) for id, ticket in snap.items()
           if ticket["status"] == "Til gjennomgang" and (state.get(id, {}).get("dev") or {}).get("branch")
           and not state[id]["dev"].get("pushed") and not state[id]["dev"].get("conflict")]
    for id, dev in sorted(old, key=lambda item: int((item[1].get("pr") or "/0").rsplit("/", 1)[-1] or 0)):
        add(id, "to_main", None, "ligger i Til gjennomgang → fører branchen inn i main")  # eldste PR først

    # 5. Klar-oppgaver: start utviklingen, uansett om løkka raffinerte dem eller de ble flyttet dit
    for id, ticket in snap.items():
        entry = state.get(id, {})
        if (id in taken or ticket["status"] != "Klar" or entry.get("dev") or entry.get("failed")
                or not (ticket["ready"] or entry.get("session")) or is_umbrella(id, ticket, state)):
            continue
        if any(snap.get(b, {}).get("status", "Fullført") != "Fullført" for b in ticket["blocked_by"]):
            continue  # venter på at oppgavene den bygger på, er ført inn i main
        add(id, "develop", None, "Klar → starter utviklingen")

    # 6. Oppgaver i pipelinen som løkka ikke har en økt for (laget, flyttet eller raffinert uten den): ta dem over
    for id, ticket in snap.items():
        entry = state.get(id, {})
        if id in taken or not ticket["ready"] or entry.get("failed") or entry.get("session") or entry.get("dev"):
            continue
        last = ticket["all_comments"][-1] if ticket["all_comments"] else None
        if ticket["status"] == "Raffinering" and not is_umbrella(id, ticket, state):
            add(id, "new", None, "står i Raffinering uten agent → tar den over")
        elif ticket["status"] == "Trenger avklaring" and last and last["human"]:
            add(id, "new", None, "ubesvart kommentar i Trenger avklaring → tar den over")
        elif ticket["status"] == "Under utvikling":
            add(id, "develop", None, "står i Under utvikling uten agent → tar den over")
    return jobs


def adoptable(snap, state):
    """Oppgavene løkka kommer til å ta over fra boardet, til oversikten ved oppstart."""
    return [(t["title"], t["status"]) for id, t in snap.items()
            if t["ready"] and not state.get(id, {}).get("session") and not state.get(id, {}).get("dev")
            and t["status"] in ("Raffinering", "Trenger avklaring", "Klar", "Under utvikling")]


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
    entry = state[id]  # lagt inn av hovedløkka før jobben startet
    set_tag(short(ticket["title"], 28))
    if kind == "to_main":
        return finish_old_pr(board, state, id, ticket)
    develop = kind in ("develop", "dev_feedback", "dev_continue", "dev_conflict")
    if kind in ("answered", "after_klar", "umbrella", "interrupted") and not session_exists(entry.get("session", "")):
        kind, why = "new", why + " (fant ikke økta, starter en ny)"  # økta er borte, start på nytt fra Notion
    if kind in ("dev_feedback", "dev_continue", "dev_conflict") and not session_exists(entry["dev"].get("session", "")):
        kind, why = "develop", why + " (fant ikke utviklingsøkta, starter en ny)"

    try:
        code, commit = sync_code()
        if develop:
            path, branch, base = prepare_workdir(id, ticket["title"])
    except RuntimeError as error:
        say(paint(f"Git feilet: {error}", "31"))
        time.sleep(60)
        return
    seen = entry.get("seen", {}).get("comment")
    comments = ticket["comments"] or board.human_comments(id)
    ids = [c for c, _ in comments]
    unseen = comments[ids.index(seen) + 1:] if seen in ids else comments
    seen_inline = set(entry.get("seen", {}).get("inline", []))
    unseen += [(c, f"(kommentar på en linje i oppgaven) {text}") for c, text in ticket.get("inline", []) if c not in seen_inline]
    url, title = ticket["url"], ticket["title"]

    if develop:
        folder = workdir(id).relative_to(HERE)
        where = (f"Arbeidskopi: {folder}/repo, på branchen {branch} (laget fra {base}). Commit på branchen. Når du skriver "
                 f"{folder}/rapport.md, legger refine.py branchen oppå siste {base} og fører den rett inn i {base}, uten PR. "
                 "Skriv den bare når alle sjekkene er grønne.\n\n"
                 "Kommandoregler (gjelder også om du husker noe annet): bruk aldri `cd`. Kjør én kommando om gangen med "
                 f"arbeidskopien som argument: `git -C {folder}/repo status|diff|log|add|commit -m \"…\"|fetch|rebase`, "
                 f"`npm --prefix {folder}/repo install|run <skript>|test`, `ls {folder}/repo`, `mkdir -p {folder}/repo/…`. "
                 "Les og skriv filer med Read, Write og Edit. Lag package.json selv i stedet for `npm create`.")
        ask = {
            "develop": f"/develop-ticket {url}\n\n{where}",
            "dev_feedback": f"Det har kommet nye kommentarer på «{title}» ({url}) mens den er i {ticket['status']}. "
                            f"Følg /develop-ticket steg 0, «Fortsett»: svar på dem, og fiks koden hvis det trengs. {where}",
            "dev_continue": f"Forrige utvikling av «{title}» ({url}) ble avbrutt. Fortsett der du slapp. Sjekk med git status "
                            f"og git log hva som allerede er gjort. {where}",
            "dev_conflict": f"Commitene dine for «{title}» ({url}) kan ikke legges oppå siste {base}, fordi en annen oppgave har "
                            f"endret de samme linjene. Kjør `git -C {folder}/repo fetch origin {base}` og "
                            f"`git -C {folder}/repo rebase origin/{base}`, løs konfliktene med Edit, `git -C {folder}/repo add <fil>`, "
                            f"`git -C {folder}/repo -c core.editor=true rebase --continue`, kjør alle "
                            f"sjekkene på nytt, og skriv rapport.md når alt er grønt. {where}",
        }[kind]
    else:
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
        if session_exists(entry.get("session", "")):  # husk Figma og koden fra raffineringen
            flags = ["--resume", entry["session"], "--fork-session", "--session-id", entry["dev"]["session"]]
            baseline = session_total(state, entry["session"])
        else:
            flags = ["--session-id", entry["dev"]["session"]]
        board.update(id, {"Status": {"status": {"name": "Under utvikling"}}})
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
    say(dim(f"Økt …{session[-6:]} · kode {commit}" + (f" · branch {branch} → {base}" if develop else "")))
    run = run_claude(prompt, flags, verbose, develop, baseline or 0.0)
    ok = run["ok"]
    record(state, entry, session, kind, run, known)
    entry["attempts"] = 0 if ok else entry.get("attempts", 0) + 1
    entry.pop("retry_after", None)
    if entry["attempts"] >= len(RETRY_AFTER):
        entry["failed"] = True
        say(paint(f"«{title}» har feilet {entry['attempts']} ganger på rad. Jeg lar den være til noen gjør noe med den.", "31"))
    elif not ok:
        wait = RETRY_AFTER[entry["attempts"] - 1]
        entry["retry_after"] = time.time() + wait
        say(paint(f"«{title}» feilet. Prøver igjen om {wait // 60} min.", "33"))

    if develop:
        publish(board, id, entry, ticket, path, base)
    time.sleep(3)  # gi Notion et øyeblikk på å vise agentens egne endringer
    try:
        now = board.snapshot().get(id, {})
    except NETWORK_ERRORS:
        now = {}
    status = now.get("status")
    if status == "Klar" and not develop:
        say(paint(f"«{title}» er Klar. Utviklingen starter av seg selv. Så langt: ${entry['cost']:.2f}", "32"))
    elif status == "Fullført":
        say(paint(f"«{title}» er pushet til main og Fullført. Totalt for oppgaven: ${entry['cost']:.2f}", "32"))
    elif ok and (status == "Trenger avklaring" or (status == "Under utvikling" and now.get("blocker"))):
        if run["context"] >= COMPACT_AT:
            # komprimer før hvilen: når eieren svarer, sendes da et kort sammendrag i stedet for hele historikken
            say(dim(f"«{title}» venter på svar. Økta er stor (~{run['context'] // 1000}k tokens), så den komprimeres først…"))
            compact = run_claude(DEV_COMPACT if develop else COMPACT, ["--resume", session], verbose, develop,
                                 session_total(state, session) or 0.0)
            record(state, entry, session, "compact", compact)
        say(dim(f"«{title}» venter på svar fra eieren, og økta hviler til noe skjer. Så langt for oppgaven: ${entry['cost']:.2f}"))
    save_state(state)


def finish_old_pr(board, state, id, ticket):
    """En oppgave fra før PR-ene ble fjernet: før branchen inn i main som om agenten nettopp var ferdig."""
    entry = state[id]
    path, base = workdir(id) / "repo", repo()[1]
    if not (path / ".git").is_dir():
        say(paint(f"«{ticket['title']}»: fant ikke arbeidskopien, så den må bygges på nytt.", "33"))
        entry.pop("dev", None)
        board.update(id, {"Status": {"status": {"name": "Klar"}}})
        save_state(state)
        return
    say(paint(f"=== {time.strftime('%H:%M:%S')} «{ticket['title']}»: fører branchen fra PR-en inn i main ===", "1"))
    (path.parent / "rapport.md").write_text(f"{ticket['title']}\n\nFørt inn i main fra den tidligere PR-en: {entry['dev'].get('pr')}")
    publish(board, id, entry, ticket, path, base)
    save_state(state)


def publish(board, id, entry, ticket, path, base):
    """Fører oppgavens branch inn i main, men bare når agenten har meldt at alt er grønt (rapport.md)."""
    with PUBLISH_LOCK:
        _publish(board, id, entry, ticket, path, base)


def _publish(board, id, entry, ticket, path, base):
    report = path.parent / "rapport.md"
    if not report.exists():
        return  # ikke ferdig, eller venter på svar: ingenting pushes
    try:
        if uncommitted(path):  # agenten skal committe selv, men ingenting skal gå tapt
            git("-C", str(path), "add", "-A")
            git("-C", str(path), "commit", "--quiet", "-m", f"Fullfør {ticket['title']}")
        if not commits_ahead(path, base):
            report.unlink()
            return
        if not rebase_on_main(path, base):
            entry["dev"]["conflict"] = True
            board.update(id, {"Status": {"status": {"name": "Under utvikling"}}})  # agenten løser konflikten der
            board.comment(id, f"🤖 En annen oppgave har endret de samme linjene på {base}. Jeg løser konflikten og prøver igjen.")
            say(paint(f"Konflikt med {base} for «{ticket['title']}». Agenten løser den i neste runde.", "33"))
            return
        sha = push_to_main(path, base)
        commit_url = f"{repo()[0]}/commit/{sha}"
        summary = report.read_text().strip()
        report.unlink()  # neste runde må melde ferdig på nytt
        entry["dev"].update(pushed=commit_url, conflict=False)
        board.update(id, {"Status": {"status": {"name": "Fullført"}}, "Pull request": {"url": commit_url},
                          "Blokkeringsnotat": {"rich_text": []}})
        board.comment(id, f"🤖 Branchen {entry['dev'].get('branch', '')} er ført inn i {base}: {commit_url}\n\n{short(summary, 1500)}")
        say(paint(f"{entry['dev'].get('branch', 'Branchen')} er ført inn i {base}: {commit_url}", "32"))
    except (RuntimeError, *NETWORK_ERRORS) as error:
        say(paint(f"Klarte ikke å pushe «{ticket['title']}»: {short(error, 200)}", "31"))
        board.comment(id, f"🤖 Koden er committet på branchen {entry['dev'].get('branch', '')}, men jeg fikk ikke ført den inn i {base}: {short(error, 300)}")
