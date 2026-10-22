"""Holder kodekopien oppdatert med git, uten Claude."""

import os
import subprocess

from .config import CONFIG, HERE


def sync_code():
    """Synker kodekopien med git, uten Claude. Returnerer kodeargumentene og commiten."""
    code = CONFIG.get("code", {})
    if code.get("local"):
        path = (HERE / code["local"]).resolve()
        return f"code={path}", "lokal mappe"
    url, branch = code["github"].rstrip("/").removesuffix(".git"), code.get("branch", "main")
    path = HERE / ".cache" / "code" / url.rsplit("/", 1)[-1]
    git = ["git", "-c", "advice.detachedHead=false"]
    if (path / ".git").is_dir():
        steps = [git + ["-C", str(path), "fetch", "--depth", "1", "origin", branch],
                 git + ["-C", str(path), "reset", "--hard", "--quiet", "FETCH_HEAD"]]
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        steps = [git + ["clone", "--depth", "1", "--branch", branch, "--single-branch", "--quiet", url, str(path)]]
    for step in steps:
        result = subprocess.run(step, capture_output=True, text=True, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
        if result.returncode:
            raise RuntimeError(f"git feilet: {result.stderr.strip()[:200]}. Sjekk at `git clone {url}` virker.")
    commit = subprocess.run(["git", "-C", str(path), "log", "-1", "--format=%h %s"],
                            capture_output=True, text=True).stdout.strip()
    return f"code={path.relative_to(HERE)} github={url}@{branch}", commit
