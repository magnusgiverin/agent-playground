"""Følger pull requests til de er merget: godkjenning, konflikter og lukking. Bruker gh, ikke Claude."""

import time

from . import github
from .board import NETWORK_ERRORS
from .code import repo
from .config import MERGE
from .display import paint, say, short
from .sessions import save_state

CHECK_EVERY = 60  # sekunder mellom hver sjekk av en PR


def approved(info):
    """Godkjent av et menneske i GitHub, eller merket med godkjent-etiketten (snarveien).
    Et review med «endringer ønsket» blokkerer begge deler til den som ba om dem, godkjenner eller frafaller."""
    if info["reviewDecision"] == "CHANGES_REQUESTED":
        return False
    by_review = "review" in MERGE["approval"] and info["reviewDecision"] == "APPROVED"
    by_label = "label" in MERGE["approval"] and MERGE["label"] in info["labels"]
    return by_review or by_label


def watch_prs(board, snap, state, busy):
    """Sjekker PR-ene til oppgaver i Til gjennomgang og merger de som er godkjent. Oppdaterer snap i tråd med boardet."""
    now = time.time()
    for id, ticket in snap.items():
        entry = state.get(id) or {}
        dev = entry.get("dev") or {}
        if (ticket["status"] != "Til gjennomgang" or not dev.get("pr") or dev.get("pushed")
                or id in busy or now - dev.get("checked", 0) < CHECK_EVERY):
            continue
        dev["checked"] = now
        try:
            if handle(board, snap, id, ticket, entry, github.pr_state(dev["pr"])):
                save_state(state)
        except (RuntimeError, *NETWORK_ERRORS) as error:
            say(paint(f"Klarte ikke å sjekke PR-en til «{ticket['title']}»: {short(error, 200)}", "31"))


def handle(board, snap, id, ticket, entry, info):
    """Gjør det som trengs for én PR. Returnerer om noe ble endret."""
    dev, title, base = entry["dev"], ticket["title"], repo()[1]
    if info["state"] == "CLOSED":
        # lukket uten merge: oppgaven blir stående i Klar til noen kommenterer eller endrer den
        board.update(id, {"Status": {"status": {"name": "Klar"}}, "Pull request": {"url": None}})
        board.comment(id, "🤖 PR-en ble lukket uten å bli merget. Jeg lar oppgaven stå i Klar til noen kommenterer eller endrer den.")
        snap[id]["status"] = "Klar"
        entry.pop("dev")
        entry["failed"] = True  # ikke bygg den på nytt av seg selv; et menneske løfter den ut av «feilet»
        say(paint(f"PR-en til «{title}» ble lukket uten merge.", "33"))
        return True
    if info["state"] == "OPEN" and info["mergeable"] == "CONFLICTING":
        dev["conflict"] = True
        board.update(id, {"Status": {"status": {"name": "Under utvikling"}}})  # agenten løser konflikten der
        board.comment(id, f"🤖 En annen oppgave har endret de samme linjene på {base}. Jeg løser konflikten og oppdaterer PR-en.")
        snap[id]["status"] = "Under utvikling"
        say(paint(f"PR-en til «{title}» har konflikt med {base}. Agenten løser den.", "33"))
        return True
    if info["state"] == "OPEN":
        feedback = github.pr_feedback(dev["pr"])
        if "seen_pr" not in dev:  # en PR vi tar over: det som står der fra før, regnes som sett
            dev["seen_pr"] = [id for id, _ in feedback]
        new = [(id, text) for id, text in feedback if id not in dev["seen_pr"]]
        if new:
            dev["feedback"] = new
            board.update(id, {"Status": {"status": {"name": "Under utvikling"}}})  # agenten tar kommentarene på PR-en
            board.comment(id, f"🤖 Det er kommet {len(new)} kommentar{'er' if len(new) != 1 else ''} på PR-en. Jeg tar dem nå: {dev['pr']}")
            snap[id]["status"] = "Under utvikling"
            say(paint(f"«{title}» fikk {len(new)} ny{'e' if len(new) != 1 else ''} PR-kommentar{'er' if len(new) != 1 else ''}. Agenten tar dem.", "33"))
            return True
    if info["state"] == "OPEN" and approved(info):
        try:
            github.merge_pr(dev["pr"], MERGE["method"])
        except RuntimeError as error:
            if dev.get("merge_error") != str(error):  # si fra én gang per feil
                dev["merge_error"] = str(error)
                board.comment(id, f"🤖 PR-en er godkjent, men jeg fikk ikke merget den: {short(error, 300)}")
                say(paint(f"Klarte ikke å merge PR-en til «{title}»: {short(error, 200)}", "31"))
                return True
            return False
        dev.pop("merge_error", None)
        info = github.pr_state(dev["pr"])
    if info["state"] == "MERGED":
        sha = (info.get("mergeCommit") or {}).get("oid")
        commit_url = f"{repo()[0]}/commit/{sha}" if sha else dev["pr"]
        dev["pushed"] = commit_url
        board.update(id, {"Status": {"status": {"name": "Fullført"}}, "Blokkeringsnotat": {"rich_text": []}})
        board.comment(id, f"🤖 PR-en er merget i {base}: {commit_url}")
        snap[id]["status"] = "Fullført"
        say(paint(f"«{title}» er merget i {base}: {commit_url}", "32"))
        return True
    return False
