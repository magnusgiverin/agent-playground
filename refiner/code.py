"""Git uten Claude: en skrivebeskyttet kopi til raffinering, og arbeidskopier til utvikling."""

import os
import re
import subprocess
import threading
import time

from .config import CONFIG, HERE

WORK = HERE / ".cache" / "work"


def repo():
    """(URL, branch) fra [code] i refiner.toml."""
    code = CONFIG.get("code", {})
    if not code.get("github"):
        raise RuntimeError("Sett [code] github i refiner.toml til repoet agenten skal jobbe i.")
    return code["github"].rstrip("/").removesuffix(".git"), code.get("branch", "main")


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
        raise RuntimeError(f"Repoet har ingen branch som heter «{branch}». Sett riktig branch under [code] i refiner.toml.")
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


def _sync_code():
    """Returnerer kodeargumentene og commiten."""
    code = CONFIG.get("code", {})
    if code.get("local"):
        path = (HERE / code["local"]).resolve()
        return f"code={path}", "lokal mappe"
    url, branch = repo()
    path = HERE / ".cache" / "code" / url.rsplit("/", 1)[-1]
    if remote_state(url, branch) == "empty":
        return f"code=(tomt repo, ingen kode ennå) github={url}@{branch}", "tomt repo"
    try:
        if (path / ".git").is_dir():
            git("-C", str(path), "fetch", "--depth", "1", "origin", branch)
            git("-C", str(path), "reset", "--hard", "--quiet", "FETCH_HEAD")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            git("clone", "--depth", "1", "--branch", branch, "--single-branch", "--quiet", url, str(path))
    except RuntimeError as error:
        raise RuntimeError(f"{error}. Sjekk at `git clone {url}` virker.") from error
    commit = git("-C", str(path), "log", "-1", "--format=%h %s")
    return f"code={path.relative_to(HERE)} github={url}@{branch}", commit


def branch_name(ticket_id, title):
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower().translate(str.maketrans("æøå", "eoa"))).strip("-")[:40]
    return f"refiner/{slug or 'oppgave'}-{ticket_id.replace('-', '')[-6:]}"


def workdir(ticket_id):
    """Mappa for en oppgaves utvikling: repo/ (arbeidskopien på oppgavens branch) og rapport.md (agentens ferdigmelding)."""
    return WORK / ticket_id.replace("-", "")[-12:]


def prepare_workdir(ticket_id, title):
    """En egen klone på en egen branch for oppgaven, laget fra siste main. Lages første gang, og gjenbrukes etterpå."""
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
        else:
            git("clone", "--branch", base, "--single-branch", "--quiet", url, str(path))
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


def rebase_on_main(path, base):
    """Legger branchens commits oppå siste main. Returnerer False ved konflikt (og avbryter rebasen)."""
    git("-C", str(path), "fetch", "--quiet", "origin", base)
    result = subprocess.run(["git", "-C", str(path), "rebase", "--quiet", f"origin/{base}"], capture_output=True, text=True,
                            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
    if result.returncode == 0:
        return True
    git("-C", str(path), "rebase", "--abort", check=False)
    return False


def push_branch(path, branch):
    """Pusher oppgavens branch (ikke main). Den rebases på main mellom rundene, derfor force-with-lease.
    Klonen er --single-branch, så git finner ingen sporingsref for branchen selv. Vi lagrer det vi sist pushet,
    og bruker det som forventet verdi: leasen betyr «bare hvis ingen andre har pushet siden sist».
    Tom forventet verdi (første push) betyr at branchen ikke skal finnes fra før."""
    last = git("-C", str(path), "rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{branch}", check=False)
    git("-C", str(path), "push", "--quiet", f"--force-with-lease=refs/heads/{branch}:{last}", "origin", f"HEAD:refs/heads/{branch}")
    git("-C", str(path), "update-ref", f"refs/remotes/origin/{branch}", "HEAD")


def push_to_main(path, base):
    """Fører branchen inn i main (fast-forward, siden den nettopp er rebaset) og pusher. Returnerer commit-ID-en."""
    git("-C", str(path), "push", "--quiet", "origin", f"HEAD:{base}")
    return git("-C", str(path), "rev-parse", "HEAD")
