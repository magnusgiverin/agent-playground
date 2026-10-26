"""Fra ferdig branch til main: kritisk gjennomgang, rettelser, og push. Python pusher; agentene kan ikke.

Kolonnene følger det som faktisk skjer:
    Under utvikling ──(rapport.md: alt grønt)──▶ Til gjennomgang ──(GODKJENT)──▶ main ──▶ Fullført
                    ◀──(ENDRINGER: utviklingsøkta fikser)──┘
"""

import threading
import time
import uuid

from .board import FULLFORT, GJENNOMGANG, NETWORK_ERRORS, UTVIKLING
from .claude import run_claude
from .code import (TICKET_TRAILER, TIDY_TRAILER, checkpoint, commits_ahead, delete_branch, git, prepare_workdir,
                   push_to_main, rebase_on_main, repo, tickets_since_tidy, uncommitted)
from .config import AGENT, CACHE, INSTANCE
from .display import dim, paint, say, set_tag, short
from .lease import STOPPING
from .scheduler import TIDY_EVERY
from .sessions import record, session_total

PUBLISH_LOCK = threading.Lock()  # én rebase og push om gangen i denne instansen; mellom instansene avgjør GitHub (push_to_main)
REVIEW_ROUNDS = int(AGENT.get("review_rounds", 2))  # runder gjennomgangen kan be om endringer før eieren må inn
ROOT = CACHE.parent


def review(entry, title, url, path, branch, base, verbose, what="oppgaven", lease=None):
    """En egen gjennomgangsøkt, uten utviklerens kontekst. Returnerer (godkjent, teksten i review.md)."""
    verdict = path.parent / "review.md"
    verdict.unlink(missing_ok=True)
    folder = path.parent.relative_to(ROOT)
    contract = ("" if url.startswith("http") else
                "Det finnes ingen Notion-oppgave. Kontrakten er reglene i /tidy-notes: bare docs/agents er endret, og notatene "
                "stemmer med koden.\n")
    prompt = (f"/review-change {url}\n\nGjennomgå endringen for {what} «{title}» før den føres inn i {base}.\n{contract}"
              f"- Arbeidskopi: {folder}/repo, branch {branch}. Diffen: `git -C {folder}/repo diff origin/{base}...HEAD`\n"
              f"- Utviklerens rapport: {folder}/rapport.md\n- Skriv dommen i: {folder}/review.md\n"
              f"Bruk aldri `cd`. Kjør sjekkene med `npm --prefix {folder}/repo …`.")
    session = str(uuid.uuid4())
    say(dim(f"Kritisk gjennomgang før {base} (egen økt …{session[-6:]})…"))
    run = run_claude(prompt, ["--session-id", session], verbose, "review", lease=lease)
    record({}, entry, session, "review", run)
    text = verdict.read_text().strip() if verdict.exists() else ""
    return text.splitlines()[0].strip().upper() == "GODKJENT" if text else False, text


def review_and_publish(board, state, id, entry, ticket, path, branch, base, verbose, lease):
    """Ingen kode går inn i main uten at en annen agent har godkjent den. Ved funn fikser utviklingsøkta dem.
    Oppgaven står i Til gjennomgang mens gjennomgangen pågår, og i Under utvikling mens funnene fikses."""
    report = path.parent / "rapport.md"
    for round in range(REVIEW_ROUNDS + 1):
        if not report.exists() or lease.lost.is_set() or STOPPING.is_set():
            return  # ikke ferdig, venter på svar, eller tatt over av en annen instans
        board.move(id, GJENNOMGANG)
        approved, verdict = review(entry, ticket["title"], ticket["url"], path, branch, base, verbose, lease=lease)
        if lease.lost.is_set() or STOPPING.is_set():
            return  # oppgaven blir stående i Til gjennomgang, og gjennomgås på nytt av den som tar den
        if not verdict:
            # ingen dom er en feil i gjennomgangen, ikke et funn i koden: prøv gjennomgangen igjen senere
            failures = entry["review_failures"] = entry.get("review_failures", 0) + 1
            if failures >= 3:
                board.set_blocker(id, "Gjennomgangen klarer ikke å skrive en dom. Se kommentarene.")
                board.comment(id, "🤖 Gjennomgangen før main har ikke klart å skrive en dom tre ganger på rad, så endringen står "
                                  "i Til gjennomgang. Det er en feil i oppsettet, ikke i koden. Kommenter når det er rettet.")
                say(paint(f"«{ticket['title']}»: gjennomgangen skrev ingen dom tre ganger. Venter på et menneske.", "31"))
            else:
                entry["retry_after"] = time.time() + 300
                say(paint(f"«{ticket['title']}»: gjennomgangen skrev ingen dom. Prøver igjen om 5 min.", "33"))
            return
        entry.pop("review_failures", None)
        if approved:
            say(paint("Gjennomgangen godkjente endringen.", "32"))
            entry.setdefault("dev", {})["review"] = verdict
            publish(board, id, entry, ticket, path, base)
            return
        findings = verdict
        board.move(id, UTVIKLING)
        if round == REVIEW_ROUNDS:
            board.set_blocker(id, "Gjennomgangen fant problemer som ikke ble løst. Se kommentarene.")
            board.comment(id, f"🤖 Gjennomgangen før main fant problemer også etter {REVIEW_ROUNDS} runder med rettelser, så jeg "
                              f"fører det ikke inn i main. Si hva som skal gjøres, eller godta det slik det er.\n\n{short(findings, 1500)}")
            say(paint(f"«{ticket['title']}»: ikke godkjent etter {REVIEW_ROUNDS} runder. Venter på eieren.", "33"))
            report.unlink()
            return
        say(paint(f"Gjennomgangen ba om endringer (runde {round + 1} av {REVIEW_ROUNDS}). Utviklingsøkta fikser dem.", "33"))
        board.comment(id, f"🤖 Gjennomgangen før main ba om endringer. Jeg fikser dem.\n\n{short(findings, 1500)}")
        report.unlink()
        session = (entry.get("dev") or {}).get("session")
        if not session:
            return  # gjennomgangen ble tatt over fra en annen instans uten utviklingsøkt: neste runde bygger videre (develop)
        folder = path.parent.relative_to(ROOT)
        fix = (f"En kritisk gjennomgang av endringen din for «{ticket['title']}» ba om endringer før den kan føres inn i {base}:\n\n"
               f"{findings}\n\nFiks alt under «Må fikses», commit, kjør alle sjekkene, og skriv {folder}/rapport.md på nytt når "
               f"alt er grønt. Er du uenig i et funn, forklar hvorfor i rapporten. Bruk aldri `cd`. Kjør git og npm med "
               f"arbeidskopien som argument ({folder}/repo).")
        run = run_claude(fix, ["--resume", session], verbose, "develop", session_total(state, session) or 0.0, lease)
        record(state, entry, session, "review_fix", run)
        checkpoint(path, branch, base)


def publish(board, id, entry, ticket, path, base):
    """Fører oppgavens branch inn i main, men bare når agenten har meldt at alt er grønt (rapport.md)."""
    with PUBLISH_LOCK:
        _publish(board, id, entry, ticket, path, base)


def _publish(board, id, entry, ticket, path, base):
    report = path.parent / "rapport.md"
    if not report.exists():
        return  # ikke ferdig, eller venter på svar: ingenting pushes
    dev = entry.setdefault("dev", {})
    branch = dev.get("branch") or git("-C", str(path), "rev-parse", "--abbrev-ref", "HEAD")
    try:
        if uncommitted(path):  # agenten skal committe selv, men ingenting skal gå tapt
            git("-C", str(path), "add", "-A")
            git("-C", str(path), "commit", "--quiet", "-m", f"Fullfør {ticket['title']}")
        if not commits_ahead(path, base):
            report.unlink()
            board.move(id, UTVIKLING)
            board.set_blocker(id, "Agenten meldte ferdig, men branchen har ingen endringer. Se kommentarene.")
            board.comment(id, f"🤖 Agenten meldte at den var ferdig, men branchen {branch} har ingen endringer mot {base}. "
                              "Si om oppgaven allerede er løst, eller hva som mangler.")
            return
        sha = push_to_main(path, base, f"{TICKET_TRAILER}: {ticket['url']}") if rebase_on_main(path, base) else None
        if not sha:
            report.unlink()
            board.move(id, UTVIKLING)  # agenten løser konflikten der (scheduler ser konflikten og ber om det)
            board.comment(id, f"🤖 En annen oppgave har endret de samme linjene på {base}. Jeg løser konflikten og prøver igjen.")
            say(paint(f"Konflikt med {base} for «{ticket['title']}». Agenten løser den i neste runde.", "33"))
            return
        commit_url = f"{repo()[0]}/commit/{sha}"
        summary = report.read_text().strip()
        report.unlink()  # neste runde må melde ferdig på nytt
        dev.update(pushed=commit_url)
        delete_branch(path, branch)
        board.move(id, FULLFORT, **{"Pull request": {"url": commit_url}, "Blokkeringsnotat": {"rich_text": []}})
        board.comment(id, f"🤖 Branchen {branch} er ført inn i {base}: {commit_url}\n\n{short(summary, 1500)}")
        say(paint(f"{branch} er ført inn i {base}: {commit_url}", "32"))
    except (RuntimeError, *NETWORK_ERRORS) as error:
        say(paint(f"Klarte ikke å pushe «{ticket['title']}»: {short(error, 200)}", "31"))
        board.comment(id, f"🤖 Koden er committet på branchen {branch}, men jeg fikk ikke ført den inn i {base}: {short(error, 300)}. "
                          "Jeg prøver igjen.")


def run_tidy(board, verbose):
    """Rydder i docs/agents på en egen branch, og går gjennom samme gjennomgang som en oppgave før main.
    Bare én instans rydder om gangen (låsen er en ref på GitHub, se tidy_lock)."""
    set_tag("rydding")
    try:
        path, branch, base = prepare_workdir(f"rydding-{time.strftime('%Y%m%d%H%M')}", "Rydd i agentnotatene", shared=False)
    except RuntimeError as error:
        say(paint(f"Git feilet: {error}", "31"))
        return
    if not tidy_lock(path, take=True):
        say(dim("En annen instans rydder i agentnotatene nå."))
        return
    try:
        if (tickets_since_tidy(path, f"origin/{base}") or 0) < TIDY_EVERY:
            say(dim("En annen instans har nettopp ryddet i agentnotatene."))
            return
        folder = path.parent.relative_to(ROOT)
        say(paint(f"=== {time.strftime('%H:%M:%S')} Rydder i agentnotatene (branch {branch}) ===", "1"))
        entry = {"title": "Rydd i agentnotatene", "dev": {"branch": branch}}
        prompt = (f"/tidy-notes\n\nArbeidskopi: {folder}/repo, branch {branch}. Skriv rapporten i {folder}/rapport.md. "
                  f"Bruk aldri `cd`. Kjør git med `git -C {folder}/repo …`.")
        session = str(uuid.uuid4())
        run = run_claude(prompt, ["--session-id", session], verbose, "tidy")
        record({}, entry, session, "tidy", run)
        trailer = f"{TIDY_TRAILER}: {time.strftime('%Y-%m-%d')} {INSTANCE.name}"
        if not (path.parent / "rapport.md").exists() or not commits_ahead(path, base):
            # ingenting å rydde: merk det likevel på main, så telleren starter på nytt for alle instansene
            git("-C", str(path), "commit", "--quiet", "--allow-empty", "-m", "Rydd i agentnotatene: ingenting å rydde")
            say(dim("Ingenting å rydde i agentnotatene."))
        else:
            approved, verdict = review(entry, "Rydd i agentnotatene", "(ingen Notion-oppgave)", path, branch, base, verbose, "ryddingen")
            if not approved:
                say(paint(f"Ryddingen ble ikke godkjent, så den føres ikke inn i {base}:\n{short(verdict, 400)}", "33"))
                git("-C", str(path), "reset", "--hard", "--quiet", f"origin/{base}")
                git("-C", str(path), "commit", "--quiet", "--allow-empty", "-m", "Rydd i agentnotatene: ikke godkjent, prøves senere")
        with PUBLISH_LOCK:
            if not rebase_on_main(path, base):
                say(paint("Ryddingen fikk konflikt med main. Den prøves igjen neste gang.", "33"))
                return
            sha = push_to_main(path, base, trailer)
            if sha:
                say(paint(f"Ryddingen er ført inn i {base}: {repo()[0]}/commit/{sha}", "32"))
    except RuntimeError as error:
        say(paint(f"Ryddingen feilet: {short(error, 200)}", "31"))
    finally:
        tidy_lock(path, take=False)


TIDY_REF = "refs/heads/refiner/laas-rydding"


def tidy_lock(path, take):
    """En lås på GitHub: en ref som bare kan lages hvis den ikke finnes (--force-with-lease med tom forventning).
    Det er atomisk, så to instanser kan ikke ta den samtidig. En lås eldre enn to timer er etter en instans som
    døde, og tas over."""
    if not take:
        git("-C", str(path), "push", "--quiet", "origin", f":{TIDY_REF}", check=False)
        return True
    stamp = git("-C", str(path), "ls-remote", "origin", TIDY_REF, check=False)
    if stamp:
        git("-C", str(path), "fetch", "--quiet", "origin", TIDY_REF, check=False)
        age = time.time() - int(git("-C", str(path), "log", "-1", "--format=%ct", "FETCH_HEAD", check=False) or 0)
        if age < 2 * 3600:
            return False
        git("-C", str(path), "push", "--quiet", "origin", f":{TIDY_REF}", check=False)  # foreldet: fjern den
    lock = git("-C", str(path), "commit-tree", "HEAD^{tree}", "-p", "HEAD", "-m", f"Rydding startet av {INSTANCE.name}")
    try:
        git("-C", str(path), "push", "--quiet", f"--force-with-lease={TIDY_REF}:", "origin", f"{lock}:{TIDY_REF}")
        return True
    except RuntimeError:
        return False
