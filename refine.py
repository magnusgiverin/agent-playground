#!/usr/bin/env python3
"""Raffinerings- og utviklingsagenten: én Claude-økt per oppgave, som husker konteksten hele veien.

    python3 refine.py                     # start løkka: følger boardet til du trykker Ctrl-C
    python3 refine.py --env .env.kunde-a  # en annen instans, koblet til systemene i den fila
    python3 refine.py --oppsett           # vis hva denne instansen er koblet til, og stopp
    python3 refine.py --verbose           # skriv også ut hele inndata og svar for hvert verktøykall
    python3 refine.py --antakelser        # agentene antar mer og spør bare om det som er absolutt kritisk

Løkka sjekker boardet selv via Notion API (billig, ingen Claude), og gjør det som står på agendaen i hver
kolonne (se refiner/scheduler.py). Hver jobb tar en lås i Notion først, så flere instanser på flere PC-er
kan dele boardet uten å ta den samme oppgaven. Stopper en instans, tar de andre over der den slapp.

Selve arbeidet ligger i skillene i .claude/skills, oppførselen i refiner.toml, oppsettet for instansen i
miljøvariablene (.env) og verktøytilgangene i .claude/settings.json.
"""

import argparse
import fcntl
import os
import signal
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# Kjør i det virtuelle miljøet hvis det finnes, så Slack-biblioteket er med selv om du skriver python3 refine.py
VENV_PYTHON = Path(__file__).resolve().parent / ".venv" / "bin" / "python"
if __name__ == "__main__" and VENV_PYTHON.exists() and Path(sys.prefix).resolve() != VENV_PYTHON.parent.parent.resolve():
    os.execv(VENV_PYTHON, [str(VENV_PYTHON), __file__, *sys.argv[1:]])

# --env må være kjent før refiner importeres, fordi oppsettet leses når modulene lastes. REFINER_ENV arves
# også av Claude og av MCP-serveren i .mcp.json, så de bruker den samme fila.
if "--env" in sys.argv[1:-1]:
    os.environ["REFINER_ENV"] = sys.argv[sys.argv.index("--env") + 1]

from refiner import lease as leases
from refiner import slack as slack_bridge
from refiner.board import AVKLARING, INBOX, NETWORK_ERRORS, Board, InlineWatcher
from refiner.config import AGENT, ENV_FILE, ENV_VARS, INSTANCE, STATE_DIR, env, missing_settings
from refiner.display import COLOR, SPINNER, dim, paint, say, short, status_line
from refiner.runner import ASSUME, run_job
from refiner.scheduler import TIDY, agenda, next_jobs
from refiner.sessions import load_state, news_for, save_state
from refiner.shipping import run_tidy


def single_instance():
    """Bare én refine.py per instans på denne PC-en. Mellom PC-ene passer låsene i Notion på."""
    lockfile = STATE_DIR / "refine.lock"
    lockfile.parent.mkdir(parents=True, exist_ok=True)
    handle = lockfile.open("w")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit(f"Instansen «{INSTANCE.name}» kjører allerede på denne PC-en. Stopp den først (Ctrl-C i det vinduet), "
                         "eller start en annen instans med --env.")
    handle.write(str(os.getpid()))
    handle.flush()
    return handle  # låsen holdes så lenge fila er åpen


def show_setup():
    """Oppsettet for instansen, uten hemmeligheter. Brukes også av skillene når de kjøres interaktivt."""
    print(f"Env-fil: {ENV_FILE.name}{'' if ENV_FILE.exists() else ' (finnes ikke, bruker bare miljøet)'}")
    for line in INSTANCE.describe():
        print(line)
    secrets = [name for name, (_, secret, _) in ENV_VARS.items() if secret]
    print("Hemmeligheter: " + ", ".join(f"{name} {'✓' if env(name) else '–'}" for name in secrets))
    missing = missing_settings()
    if missing:
        print("Mangler: " + ", ".join(missing))


def start_slack(board, state):
    if not slack_bridge.enabled():
        return None
    try:
        slack = slack_bridge.Slack(board, state)
        say(dim("Slack er koblet til: /new lager oppgaver, og hver oppgave får sin egen tråd."))
        return slack
    except ValueError as error:
        say(paint(f"{error} Fortsetter uten Slack.", "33"))
    except ImportError:
        say(paint("Slack-nøklene er satt, men slack_bolt er ikke installert. Installer det med "
                  ".venv/bin/pip install -r requirements.txt (se README). Fortsetter uten Slack.", "33"))
    return None


def overview(board, state):
    """Hva instansen starter med, og hva som venter og hvorfor, når den starter."""
    try:
        snap = board.snapshot()
        jobs, waiting = agenda(board, snap, state)
    except NETWORK_ERRORS:
        return
    if jobs:
        say(paint(f"Starter ({len(jobs)}):", "1"))
        for id, _, _, why in jobs:
            say(f"  · «{short(snap[id]['title'] if id in snap else 'Rydder i agentnotatene', 60)}»: {why}")
    if waiting:
        say(paint(f"Venter ({len(waiting)}):", "1"))
        for title, status, why in waiting:
            say(f"  · {status:<18} «{short(title, 50)}»: {why}")
    if not jobs:
        say(dim("Ingenting kan startes nå. Løkka starter av seg selv når noe av det over endrer seg."))


def watch(verbose, poll):
    missing = missing_settings()
    if missing:
        raise SystemExit(f"Mangler oppsett: {', '.join(missing)}. Sett dem i {ENV_FILE.name} eller i miljøet (se .env.example).")
    lock = single_instance()  # noqa: F841 (holdes åpen)
    board, state = Board(), load_state()
    board.check_schema()
    say(paint(f"Instans {INSTANCE.name}", "1") + dim(f" · {INSTANCE.board} · {INSTANCE.github or INSTANCE.local}"))
    slack = start_slack(board, state)
    overview(board, state)
    inline = InlineWatcher(board)
    parallel = int(AGENT.get("parallel", 3))
    pool = ThreadPoolExecutor(max_workers=parallel)
    running = {}  # oppgave-ID → (jobb, tittel)
    say(dim(f"Kjører opptil {parallel} agenter samtidig ([agent] parallel i refiner.toml)."))
    if ASSUME["on"]:
        say(paint("Modus: flere antakelser. Agentene spør bare om det som er absolutt kritisk.", "33"))
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
                inline.waiting = {id for id, t in snap.items() if t["status"] == AVKLARING
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
                if id == TIDY:  # ryddingen hører ikke til noen oppgave
                    running[id] = (pool.submit(run_tidy, board, verbose), "Rydder i agentnotatene")
                    continue
                state.setdefault(id, {"title": snap[id]["title"]})  # bare hovedløkka legger til oppgaver
                running[id] = (pool.submit(run_job, board, state, job, snap, verbose), snap[id]["title"])

            waiting = sum(1 for t in snap.values() if t["ready"] and (t["status"] == AVKLARING or (t["status"] == "Under utvikling" and t["blocker"])))
            elsewhere = sum(1 for t in snap.values() if leases.holder(t))
            extra = (f" · {waiting} venter på eieren" if waiting else "") + (f" · {elsewhere} hos andre instanser" if elsewhere else "")
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
                            + dim(f" · sist sjekket {checked}{extra}"))
                frame += 1
                time.sleep(0.1)
    finally:
        # stopp agentene og vent til jobbene har sluppet låsene sine («ledig»), så andre instanser tar over med en gang
        leases.stop_all()
        if running:
            say(dim(f"\nStopper {len(running)} agent{'er' if len(running) != 1 else ''} og slipper oppgavene…"))
        pool.shutdown(wait=True, cancel_futures=True)
        save_state(state)


def stop(*_):
    raise KeyboardInterrupt


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--env", metavar="FIL", default=".env", help="env-fila med oppsettet for instansen (standard .env)")
    parser.add_argument("--oppsett", action="store_true", help="vis hva instansen er koblet til (uten hemmeligheter), og stopp")
    parser.add_argument("--poll", type=float, default=20, metavar="SEKUNDER", help="hvor ofte boardet sjekkes mens den venter (standard 20)")
    parser.add_argument("--verbose", action="store_true", help="skriv også ut hele inndata og svar for hvert verktøykall")
    parser.add_argument("--antakelser", action="store_true",
                        help="agentene antar mer og spør bare om det som er absolutt kritisk (sikkerhet, data, noe som ikke kan angres)")
    args = parser.parse_args()
    if args.oppsett:
        show_setup()
        return
    ASSUME["on"] = args.antakelser
    # lukkes terminalen eller stoppes prosessen, stopper vi like ryddig som ved Ctrl-C
    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, stop)
    try:
        watch(args.verbose, args.poll)
    except KeyboardInterrupt:
        say(dim("\nStoppet. Oppgavene er sluppet, så andre instanser kan ta over, og øktene her er lagret til neste gang."))


if __name__ == "__main__":
    main()
