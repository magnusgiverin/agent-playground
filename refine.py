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
import os
import sys
import time
from pathlib import Path

# Kjør i det virtuelle miljøet hvis det finnes, så Slack-biblioteket er med selv om du skriver python3 refine.py
VENV_PYTHON = Path(__file__).resolve().parent / ".venv" / "bin" / "python"
if VENV_PYTHON.exists() and Path(sys.prefix).resolve() != VENV_PYTHON.parent.parent.resolve():
    os.execv(VENV_PYTHON, [str(VENV_PYTHON), __file__, *sys.argv[1:]])

from refiner.board import Board, NETWORK_ERRORS
from refiner.display import COLOR, SPINNER, dim, paint, short
from refiner.jobs import next_job, run_job
from refiner.sessions import load_state, news_for, save_state
from refiner import slack as slack_bridge


def watch(verbose, poll):
    board, state = Board(), load_state()
    board.check_schema()
    slack = None
    if slack_bridge.enabled():
        try:
            slack = slack_bridge.Slack(board, state)
            print(dim("Slack er koblet til: /new lager oppgaver, og hver oppgave får sin egen tråd."))
        except ValueError as error:
            print(paint(f"{error} Fortsetter uten Slack.", "33"))
        except ImportError:
            print(paint("Slack-nøklene står i .env, men slack_bolt er ikke installert. Kjør med .venv/bin/python refine.py "
                        "(se README). Fortsetter uten Slack.", "33"))
    frame, checked, snap = 0, "–", {}
    while True:
        try:
            snap = board.snapshot()
            checked = time.strftime("%H:%M:%S")
            # en menneskelig handling løfter en oppgave ut av «feilet»
            for id, entry in state.items():
                if entry.get("failed") and id in snap and news_for(snap[id], entry.get("seen", {})):
                    entry.pop("failed"), entry.update(attempts=0)
            if slack and slack.sync(snap, state):
                save_state(state)
            job = next_job(board, snap, state)
        except NETWORK_ERRORS as error:
            job, checked = None, paint(f"feil: {short(error, 60)}", "31")
        if job:
            if COLOR:
                print("\r\033[K", end="")
            run_job(board, state, job, snap, verbose)
            continue
        waiting = sum(1 for id, e in state.items() if snap.get(id, {}).get("status") == "Trenger avklaring")
        resting = dim(f" · {waiting} økt{'er' if waiting != 1 else ''} hviler" if waiting else "")
        deadline = time.monotonic() + poll
        if not COLOR:
            print(f"Venter på nye oppgaver eller kommentarer (sist sjekket {checked})")
        while time.monotonic() < deadline:
            if slack and slack.wake.is_set():  # noen svarte i Slack eller brukte /new: sjekk med en gang
                slack.wake.clear()
                break
            if COLOR:
                print(f"\r\033[K{paint(SPINNER[frame % len(SPINNER)], '36')} Venter på nye oppgaver eller kommentarer"
                      + dim(f" · sist sjekket {checked}") + resting, end="", flush=True)
            frame += 1
            time.sleep(0.1)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--poll", type=float, default=20, metavar="SEKUNDER", help="hvor ofte boardet sjekkes mens den venter (standard 20)")
    parser.add_argument("--verbose", action="store_true", help="skriv også ut hele inndata og svar for hvert verktøykall")
    args = parser.parse_args()
    try:
        watch(args.verbose, args.poll)
    except KeyboardInterrupt:
        print(dim("\nStoppet. Øktene er lagret, så neste gang fortsetter de der de slapp."))


if __name__ == "__main__":
    main()
