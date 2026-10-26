"""Git uten Claude: en skrivebeskyttet kopi til raffinering, og arbeidskopier til utvikling.

Arbeidskopiene er lokale, men branchene deles: etter hver utviklingsrunde (og mens agenten jobber) pusher
Python oppgavens branch til GitHub (checkpoint). Stopper en instans, fortsetter en annen fra den branchen.
Agenten selv kan aldri pushe. Når en oppgave er ført inn i main, slettes branchen igjen.
"""

import os
import re
import subprocess
import threading
import time

from .config import CACHE, INSTANCE, PREFIX

WORK = CACHE / "work"
TICKET_TRAILER = "Refiner-oppgave"   # i commiten som førte en oppgave inn i main
TIDY_TRAILER = "Refiner-rydding"     # i commiten som ryddet i agentnotatene
HISTORY = 300                        # så mange commits på main leses for å telle oppgaver siden siste rydding


def repo():
    """(URL, branch) for produktrepoet, fra GITHUB_REPO og GITHUB_BRANCH."""
    if not INSTANCE.github:
        raise RuntimeError("Sett GITHUB_REPO (i miljøet eller i env-fila) til repoet agenten skal jobbe i.")
    return INSTANCE.github, INSTANCE.branch


def git(*args, check=True):
    result = subprocess.run(["git", "-c", "advice.detachedHead=false", *args], capture_output=True, text=True,
                            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
    if check and result.returncode:
        raise RuntimeError(f"git {args[0] if args[0] != '-C' else args[2]} feilet: {result.stderr.strip()[:300]}")
    return result.stdout.strip()


def remote_state(url, branch):
    """«ok» hvis branchen finnes, «empty» hvis repoet ikke har noen brancher ennå. Feiler ellers."""
    heads = git("ls-remote", "--heads", url)
    if not heads:
        return "empty"
    if not any(line.endswith(f"refs/heads/{branch}") for line in heads.splitlines()):
        raise RuntimeError(f"Repoet har ingen branch som heter «{branch}». Sett riktig GITHUB_BRANCH.")
    return "ok"


_SYNC = threading.Lock()
_LAST = {"at": 0.0, "result": None}


def sync_code():
    """Synker den skrivebeskyttede kopien raffineringen leser, høyst én gang i minuttet og én om gangen."""
    with _SYNC:
        if _LAST["result"] and time.monotonic() - _LAST["at"] < 60:
            return _LAST["result"]
        _LAST.update(result=_sync_code(), at=time.monotonic())
        return _LAST["result"]


def code_copy():
    """Mappa med den skrivebeskyttede kopien av repoet."""
    return CACHE / "code" / f"{PREFIX}{repo()[0].rsplit('/', 1)[-1]}"


def _sync_code():
    """Returnerer kodeargumentene og commiten."""
    if INSTANCE.local:
        path = (CACHE.parent / INSTANCE.local).resolve()
        return f"code={path}", "lokal mappe"
    url, branch = repo()
    path = code_copy()
    if remote_state(url, branch) == "empty":
        return f"code=(tomt repo, ingen kode ennå) github={url}@{branch}", "tomt repo"
    try:
        if (path / ".git").is_dir():
            # nok historikk til å telle oppgaver siden siste rydding (tidy_due), men fortsatt en grunn kopi
            git("-C", str(path), "fetch", "--quiet", "--depth", str(HISTORY), "origin", branch)
            git("-C", str(path), "reset", "--hard", "--quiet", "FETCH_HEAD")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            git("clone", "--depth", str(HISTORY), "--branch", branch, "--single-branch", "--quiet", url, str(path))
    except RuntimeError as error:
        raise RuntimeError(f"{error}. Sjekk at `git clone {url}` virker.") from error
    commit = git("-C", str(path), "log", "-1", "--format=%h %s")
    return f"code={path.relative_to(CACHE.parent)} github={url}@{branch}", commit


def tickets_since_tidy(path=None, ref="HEAD"):
    """Hvor mange oppgaver som er ført inn i main siden siste rydding, av alle instansene. Leses fra
    commit-trailerne på main, så det er felles for alle som deler repoet. None hvis det ikke kan leses."""
    if INSTANCE.local or not INSTANCE.github:
        return None
    path = path or code_copy()
    if not (path / ".git").is_dir():
        return None
    log = git("-C", str(path), "log", f"-{HISTORY}", "--format=%(trailers:only,unfold)%x00", ref, check=False)
    tickets = set()
    for message in log.split("\0"):
        if f"{TIDY_TRAILER}:" in message:
            break
        tickets.update(re.findall(rf"^{TICKET_TRAILER}: (\S+)", message, re.M))
    return len(tickets)


def branch_name(ticket_id, title):
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower().translate(str.maketrans("æøå", "eoa"))).strip("-")[:40]
    return f"refiner/{slug or 'oppgave'}-{ticket_id.replace('-', '')[-6:]}"


def workdir(ticket_id):
    """Mappa for en oppgaves utvikling: repo/ (arbeidskopien på oppgavens branch) og rapport.md (agentens ferdigmelding)."""
    return WORK / f"{PREFIX}{ticket_id.replace('-', '')[-12:]}"


def shared_branch(url, ticket_id):
    """Oppgavens branch på GitHub, hvis en instans har pushet den (refiner/<slug>-<id6>), ellers None.
    Søker på ID-en, så det virker også om tittelen er endret."""
    suffix = ticket_id.replace("-", "")[-6:]
    for line in git("ls-remote", "--heads", url, f"refs/heads/refiner/*-{suffix}", check=False).splitlines():
        return line.split("refs/heads/", 1)[1]
    return None


def prepare_workdir(ticket_id, title, shared=True):
    """En egen klone på en egen branch for oppgaven. Har en annen instans begynt (branchen ligger på GitHub),
    fortsettes den. Ellers lages branchen fra siste main. Lages første gang, og gjenbrukes etterpå."""
    url, base = repo()
    folder = workdir(ticket_id)
    path = folder / "repo"
    branch = branch_name(ticket_id, title)
    if not (path / ".git").is_dir():
        folder.mkdir(parents=True, exist_ok=True)
        if remote_state(url, base) == "empty":
            # et helt nytt repo: lag base-branchen med en tom start-commit, så branchene har noe å bygge på
            path.mkdir(parents=True, exist_ok=True)
            git("-C", str(path), "init", "--quiet", "-b", base)
            git("-C", str(path), "remote", "add", "origin", url)
            git("-C", str(path), "commit", "--quiet", "--allow-empty", "-m", "Start prosjektet")
            git("-C", str(path), "push", "--quiet", "-u", "origin", base)
            git("-C", str(path), "checkout", "--quiet", "-b", branch)
            return path, branch, base
        git("clone", "--branch", base, "--single-branch", "--quiet", url, str(path))
        existing = shared_branch(url, ticket_id) if shared else None
        if existing:  # en annen instans har jobbet på oppgaven: fortsett fra commitene dens
            git("-C", str(path), "fetch", "--quiet", "origin", f"refs/heads/{existing}")
            git("-C", str(path), "checkout", "--quiet", "-b", existing, "FETCH_HEAD")
            return path, existing, base
        git("-C", str(path), "checkout", "--quiet", "-b", branch)
    else:
        git("-C", str(path), "fetch", "--quiet", "origin", base)
        branch = git("-C", str(path), "rev-parse", "--abbrev-ref", "HEAD")
        if not commits_ahead(path, base) and not uncommitted(path):
            git("-C", str(path), "reset", "--hard", "--quiet", f"origin/{base}")  # ny runde: start fra siste main
    return path, branch, base


def commits_ahead(path, base):
    return int(git("-C", str(path), "rev-list", "--count", f"origin/{base}..HEAD") or 0)


def uncommitted(path):
    return bool(git("-C", str(path), "status", "--porcelain"))


def checkpoint(path, branch, base):
    """Pusher oppgavens branch til GitHub, så en annen instans kan fortsette hvis denne stopper.
    Bare committet arbeid deles. Feiler stille: det er en sikkerhetskopi, ikke en del av flyten."""
    try:
        if (path / ".git").is_dir() and commits_ahead(path, base):
            git("-C", str(path), "push", "--quiet", "--force", "origin", f"HEAD:refs/heads/{branch}")
    except RuntimeError:
        pass


def delete_branch(path, branch):
    """Sletter oppgavens delte branch når den er ført inn i main."""
    git("-C", str(path), "push", "--quiet", "origin", "--delete", branch, check=False)


def rebase_on_main(path, base):
    """Legger branchens commits oppå siste main. Returnerer False ved konflikt (og avbryter rebasen)."""
    git("-C", str(path), "fetch", "--quiet", "origin", base)
    result = subprocess.run(["git", "-C", str(path), "rebase", "--quiet", f"origin/{base}"], capture_output=True, text=True,
                            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
    if result.returncode == 0:
        return True
    git("-C", str(path), "rebase", "--abort", check=False)
    return False


def push_to_main(path, base, trailer):
    """Fører branchen inn i main og pusher. Har en annen instans pushet til main i mellomtiden, avvises pushen
    (den er ikke fast-forward), så da legges branchen oppå på nytt og pushes igjen. Returnerer commit-ID-en,
    eller None ved konflikt. trailer (for eksempel «Refiner-oppgave: <url>») skrives i siste commit, så alle
    instansene kan lese fra main hva som er levert (tickets_since_tidy)."""
    for attempt in range(5):
        if attempt and not rebase_on_main(path, base):
            return None
        if trailer not in git("-C", str(path), "log", "-1", "--format=%B"):
            git("-C", str(path), "commit", "--quiet", "--amend", "--allow-empty", "--no-edit", "--trailer", trailer)
        result = subprocess.run(["git", "-C", str(path), "push", "--quiet", "origin", f"HEAD:{base}"],
                                capture_output=True, text=True, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
        if result.returncode == 0:
            return git("-C", str(path), "rev-parse", "HEAD")
        if "rejected" not in result.stderr and "fetch first" not in result.stderr and "non-fast-forward" not in result.stderr:
            raise RuntimeError(f"git push feilet: {result.stderr.strip()[:300]}")
        time.sleep(2 + attempt * 3)  # en annen instans pushet akkurat nå
    raise RuntimeError(f"fikk ikke pushet til {base} etter 5 forsøk, fordi andre pusher hele tiden")
