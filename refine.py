#!/usr/bin/env python3
"""Raffineringsagenten: én Claude-økt per oppgave, som husker konteksten hele veien.

    python3 refine.py              # start løkka: følger boardet til du trykker Ctrl-C
    python3 refine.py --verbose    # skriv også ut hele inndata og svar for hvert verktøykall

Løkka sjekker boardet selv via Notion API (billig, ingen Claude). Når en oppgave i Innboks
har «Klar for pipeline» og én Eier, starter den en Claude-økt for den oppgaven
(/refine-ticket). Økta leser Notion, Figma og koden, og stopper av seg selv når oppgaven er
Klar eller Trenger avklaring. Mens den venter på svar, kjører ingenting, så det koster ingen
tokens. Når eieren kommenterer eller endrer oppgaven, fortsetter løkka den samme økta
(--resume), med alt den har lest fra før. Oppgaver som deles opp, får hver sin økt som
starter med morens kontekst (--fork-session).

Selve arbeidet ligger i skillene i .claude/skills, innstillingene i refiner.toml og
verktøytilgangene i .claude/settings.json. Hvilke økter som hører til hvilke oppgaver,
lagres i .cache/sessions.json.
"""

import argparse
import fcntl
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# Kjør i det virtuelle miljøet hvis det finnes, så Slack-biblioteket er med selv om du skriver python3 refine.py
VENV_PYTHON = Path(__file__).resolve().parent / ".venv" / "bin" / "python"
if __name__ == "__main__" and VENV_PYTHON.exists() and Path(sys.prefix).resolve() != VENV_PYTHON.parent.parent.resolve():
    os.execv(VENV_PYTHON, [str(VENV_PYTHON), __file__, *sys.argv[1:]])

from refiner.board import Board, InlineWatcher, NETWORK_ERRORS
from refiner.code import repo
from refiner.config import AGENT
from refiner.display import COLOR, SPINNER, dim, paint, say, short, status_line
from refiner.jobs import adoptable, next_jobs, run_job
from refiner.sessions import load_state, news_for, save_state
from refiner import slack as slack_bridge


LOCKFILE = Path(__file__).resolve().parent / ".cache" / "refine.lock"


def single_instance():
    """Bare én refine.py om gangen. Status i Notion er ingen lås, så to prosesser kunne tatt samme oppgave."""
    LOCKFILE.parent.mkdir(parents=True, exist_ok=True)
    handle = LOCKFILE.open("w")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit("En annen refine.py kjører allerede. Stopp den først (Ctrl-C i det vinduet).")
    handle.write(str(os.getpid()))
    handle.flush()
    return handle  # låsen holdes så lenge fila er åpen


def watch(verbose, poll):
    lock = single_instance()  # noqa: F841 (holdes åpen)
    try:
        repo()  # stopp med en gang hvis repoet ikke er satt, før noen økt bruker tokens
    except RuntimeError as error:
        raise SystemExit(str(error))
    board, state = Board(), load_state()
    board.check_schema()
    slack = None
    if slack_bridge.enabled():
        try:
            slack = slack_bridge.Slack(board, state)
            say(dim("Slack er koblet til: /new lager oppgaver, og hver oppgave får sin egen tråd."))
        except ValueError as error:
            say(paint(f"{error} Fortsetter uten Slack.", "33"))
        except ImportError:
            say(paint("Slack-nøklene står i .env, men slack_bolt er ikke installert. Installer det med "
                      ".venv/bin/pip install -r requirements.txt (se README). Fortsetter uten Slack.", "33"))
    try:
        first = board.snapshot()
        found = adoptable(first, state)
        if found:
            say(paint(f"Tar over {len(found)} oppgave{'r' if len(found) != 1 else ''} fra boardet:", "1"))
            for title, status in found:
                say(f"  · {status:<18} «{short(title, 70)}»")
        inbox = sum(1 for t in first.values() if t["ready"] and t["status"] in ("Innboks", None))
        if inbox:
            say(f"  · {inbox} ny{'e' if inbox != 1 else ''} i Innboks")
    except NETWORK_ERRORS:
        pass
    inline = InlineWatcher(board)
    parallel = int(AGENT.get("parallel", 3))
    pool = ThreadPoolExecutor(max_workers=parallel)
    running = {}  # oppgave-ID → (jobb, tittel)
    say(dim(f"Kjører opptil {parallel} agenter samtidig ([agent] parallel i refiner.toml)."))
    frame, checked, snap = 0, "–", {}
    try:
        while True:
            for id, (future, title) in list(running.items()):
                if future.done():
                    running.pop(id)
                    if future.exception():
                        say(paint(f"«{title}» stoppet med en feil: {short(future.exception(), 200)}", "31"))
            try:
                snap = board.snapshot()
                checked = time.strftime("%H:%M:%S")
                for id, comments in inline.found.items():  # linjekommentarer fra den trege sjekken
                    if id in snap:
                        snap[id]["inline"] = comments
                inline.waiting = {id for id, t in snap.items() if t["status"] == "Trenger avklaring"
                                  or (t["status"] == "Under utvikling" and t["blocker"])}
                # en menneskelig handling løfter en oppgave ut av «feilet»
                for id, entry in list(state.items()):
                    if (entry.get("failed") or entry.get("retry_after")) and id in snap and news_for(snap[id], entry.get("seen", {})):
                        entry.pop("failed", None), entry.pop("retry_after", None), entry.update(attempts=0)
                if slack and slack.sync(snap, state):
                    save_state(state)
                jobs = next_jobs(board, snap, state, busy=set(running))
            except NETWORK_ERRORS as error:
                jobs, checked = [], paint(f"feil: {short(error, 60)}", "31")
            for job in jobs[:max(0, parallel - len(running))]:
                id = job[0]
                state.setdefault(id, {"title": snap[id]["title"]})  # bare hovedløkka legger til oppgaver
                running[id] = (pool.submit(run_job, board, state, job, snap, verbose), snap[id]["title"])

            waiting = sum(1 for id in state if snap.get(id, {}).get("status") == "Trenger avklaring")
            resting = f" · {waiting} hviler" if waiting else ""
            working = (f"{len(running)} agent{'er' if len(running) != 1 else ''} jobber: "
                       + ", ".join(f"«{short(t, 30)}»" for _, t in running.values())) if running else ""
            if not COLOR:
                say(working or f"Venter på nye oppgaver eller kommentarer (sist sjekket {checked})")
            deadline = time.monotonic() + poll
            while time.monotonic() < deadline:
                if slack and slack.wake.is_set():  # noen svarte i Slack eller brukte /new: sjekk med en gang
                    slack.wake.clear()
                    break
                if inline.wake.is_set():  # den trege sjekken fant en ny linjekommentar
                    inline.wake.clear()
                    break
                if any(future.done() for future, _ in running.values()):
                    break  # en agent ble ferdig: speil til Slack og start neste med en gang
                spinner = paint(SPINNER[frame % len(SPINNER)], '36')
                status_line(f"{spinner} {working or 'Venter på nye oppgaver eller kommentarer'}"
                            + dim(f" · sist sjekket {checked}{resting}"))
                frame += 1
                time.sleep(0.1)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--poll", type=float, default=20, metavar="SEKUNDER", help="hvor ofte boardet sjekkes mens den venter (standard 20)")
    parser.add_argument("--verbose", action="store_true", help="skriv også ut hele inndata og svar for hvert verktøykall")
    args = parser.parse_args()
    try:
        watch(args.verbose, args.poll)
    except KeyboardInterrupt:
        say(dim("\nStoppet. Øktene er lagret, så neste gang fortsetter de der de slapp."))


if __name__ == "__main__":
    main()
