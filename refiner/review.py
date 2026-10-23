"""Følger pull requests til de er merget (godkjenning, konflikter, lukking, med gh og uten Claude), og kjører
AI-review i en fersk økt før PR-en åpnes."""

import fnmatch
import re
import time
import uuid

from . import github
from .board import NETWORK_ERRORS
from .claude import run_claude
from .code import git, repo
from .config import CLASSIFY, HERE, MERGE
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
            to_manual(board, id, dev, "en reviewer har skrevet på PR-en")
            board.update(id, {"Status": {"status": {"name": "Under utvikling"}}})  # agenten tar kommentarene på PR-en
            board.comment(id, f"🤖 Det er kommet {len(new)} kommentar{'er' if len(new) != 1 else ''} på PR-en. Jeg tar dem nå: {dev['pr']}")
            snap[id]["status"] = "Under utvikling"
            say(paint(f"«{title}» fikk {len(new)} ny{'e' if len(new) != 1 else ''} PR-kommentar{'er' if len(new) != 1 else ''}. Agenten tar dem.", "33"))
            return True
    ready, via_auto = info["state"] == "OPEN" and approved(info), False
    if (info["state"] == "OPEN" and not ready and MERGE["auto_lane"] and dev.get("lane") == "auto"
            and info["reviewDecision"] != "CHANGES_REQUESTED"):
        ready = via_auto = auto_ready(board, id, dev)
    if ready:
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
        if via_auto:
            board.comment(id, "🤖 Auto-løpet: CI er grønn og AI-reviewen var ren, så PR-en merges uten menneske.")
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


# --- Klassifisering og løp -------------------------------------------------------------------------

def to_manual(board, id, dev, why):
    """Tar en auto-PR ut av auto-løpet, med etikett og kommentar. Gjør ingenting hvis den allerede er manuell."""
    if dev.get("lane") != "auto":
        return
    dev["lane"], dev["lane_reasons"] = "manual", [why]
    try:
        github.set_lane(dev["pr"], "manual")
    except RuntimeError as error:
        say(paint(f"Klarte ikke å sette etiketten manual: {short(error, 200)}", "33"))
    board.comment(id, f"🤖 PR-en går i manuelt løp: {why}. Den må godkjennes av et menneske.")


def ci_state(checks):
    """none (ingen sjekker), fail, pending eller pass, ut fra [(navn, status)] fra gh."""
    buckets = {bucket for _, bucket in checks}
    if not checks:
        return "none"
    if buckets & {"fail", "cancel"}:
        return "fail"
    if "pending" in buckets:
        return "pending"
    return "pass"


def auto_ready(board, id, dev):
    """Kan en auto-PR merges nå, uten menneske? Venter mens CI pågår, og faller tilbake til manuelt løp uten CI eller ved feil."""
    checks = github.pr_checks(dev["pr"])
    state = ci_state(checks)
    if state == "pending":
        return False
    if state == "pass":
        return True
    to_manual(board, id, dev, "repoet har ingen CI-sjekker" if state == "none"
              else "CI feilet: " + ", ".join(name for name, bucket in checks if bucket in ("fail", "cancel")))
    return False


def classify(ticket, path, base, dev):
    """(løp, årsaker): «auto» bare hvis alt under stemmer, ellers «manual» med årsakene. Leser diffen i arbeidskopien."""
    reasons = []
    files = lines = 0
    sensitive = []
    for row in git("-C", str(path), "diff", "--numstat", f"origin/{base}...HEAD").splitlines():
        added, deleted, name = row.split("\t", 2)
        files += 1
        lines += (int(added) if added.isdigit() else 0) + (int(deleted) if deleted.isdigit() else 0)
        if any(fnmatch.fnmatch(candidate, pattern) for pattern in CLASSIFY["sensitive"]
               for candidate in (name, "/" + name, name.rsplit("/", 1)[-1])):
            sensitive.append(name)
    if lines > CLASSIFY["max_lines"]:
        reasons.append(f"{lines} endrede linjer (grensen er {CLASSIFY['max_lines']})")
    if files > CLASSIFY["max_files"]:
        reasons.append(f"{files} endrede filer (grensen er {CLASSIFY['max_files']})")
    if sensitive:
        reasons.append("sensitive filer: " + ", ".join(sensitive[:5]))
    if ticket.get("size") in CLASSIFY["manual_sizes"]:
        reasons.append(f"oppgaven er størrelse {ticket['size']}")
    if set(ticket.get("tags", [])) & set(CLASSIFY["manual_tags"]):
        reasons.append("oppgaven er merket " + ", ".join(sorted(set(ticket["tags"]) & set(CLASSIFY["manual_tags"]))))
    if not MERGE["ai_review"]:
        reasons.append("AI-review er av")
    elif not dev.get("review_clean"):
        reasons.append("AI-reviewen var ikke ren")
    if not reasons and not MERGE["auto_lane"]:
        reasons.append("auto-løpet er av (ellers ville denne vært auto)")
    return ("manual" if reasons else "auto"), reasons


# --- AI-review før PR-en åpnes -------------------------------------------------------------------

BLOCKING = ("alvorlig", "middels")  # disse sendes tilbake til dev-agenten, «liten» havner bare i PR-beskrivelsen
FINDING = re.compile(r"^\s*\d+\.\s*\[(alvorlig|middels|liten)\]\s*(.+)$", re.I | re.M)


def parse_review(text):
    """(rent, funn) fra reviewen. funn er [(alvorlighet, tekst)]. Uten funn regnes det som rent."""
    findings = [(severity.lower(), body.strip()) for severity, body in FINDING.findall(text)]
    return not findings, findings


def ai_review(ticket, path, branch, base, verbose=False):
    """Kjører review-økta i en fersk økt (ingen --resume eller --fork-session, så den ikke kjenner dev-agentens historikk).
    Returnerer (kjøring, økt-ID, reviewen eller None). Reviewen er økta sitt siste svar, fra «Resultat:» og utover."""
    folder = path.parent.relative_to(HERE)
    session = str(uuid.uuid4())
    prompt = (f"/review-ticket {ticket['url']}\n\nArbeidskopi: {folder}/repo, på branchen {branch}. Diffen er mot origin/{base}. "
              f"Svar med reviewen som siste melding, i formatet skillen beskriver. Dette er en automatisk økt uten noe menneske som kan svare.\n\nKommandoregler: bruk aldri `cd`. Kjør én kommando om gangen, for eksempel "
              f"`git -C {folder}/repo diff origin/{base}...HEAD`, `git -C {folder}/repo log origin/{base}..HEAD`, og "
              f"`ls {folder}/repo`. Les filer med Read, og søk med Grep og Glob med path satt til {folder}/repo.")
    run = run_claude(prompt, ["--session-id", session], verbose, review=True)
    start = run["result"].find("Resultat:")
    return run, session, run["result"][start:] if start >= 0 else None
