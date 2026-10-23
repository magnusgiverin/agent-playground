"""GitHub via gh-CLI, uten Claude. Agentene har ikke gh: bare Python åpner, følger og merger PR-er."""

import json
import os
import re
import subprocess

from .code import repo

FLAGS = {"squash": "--squash", "rebase": "--rebase", "merge": "--merge"}


def gh(*args):
    result = subprocess.run(["gh", *args], capture_output=True, text=True, env={**os.environ, "GH_PROMPT_DISABLED": "1"})
    if result.returncode:
        raise RuntimeError(f"gh {args[0]} {args[1]} feilet: {(result.stderr or result.stdout).strip()[:300]}")
    return result.stdout.strip()


def slug():
    """«eier/navn» fra [code] github i refiner.toml."""
    match = re.search(r"github\.com[/:]([^/]+/[^/]+)$", repo()[0])
    if not match:
        raise RuntimeError("PR-modus krever at [code] github peker på et repo på github.com.")
    return match.group(1)


def check_auth():
    gh("auth", "status")


def ensure_label(name):
    gh("label", "create", name, "--repo", slug(), "--color", "0E8A16", "--force",
       "--description", "Godkjent for merge av refine.py")


def open_pr(branch, base, title, body):
    """Åpner PR-en og returnerer URL-en."""
    return gh("pr", "create", "--repo", slug(), "--head", branch, "--base", base, "--title", title, "--body", body)


def pr_state(url):
    """state (OPEN, MERGED, CLOSED), reviewDecision, labels (navn), mergeable og mergeCommit."""
    info = json.loads(gh("pr", "view", url, "--json", "state,reviewDecision,labels,mergeable,mergeCommit"))
    info["labels"] = {label["name"] for label in info["labels"]}
    return info


def merge_pr(url, method):
    gh("pr", "merge", url, FLAGS[method], "--delete-branch")


def reply_pr(url, text):
    """Svarer på PR-en. Teksten starter med 🤖, så pr_feedback ikke leser den som en ny kommentar."""
    gh("pr", "comment", url, "--body", text)


def pr_feedback(url):
    """Det mennesker har skrevet på PR-en, som [(id, tekst)]: vanlige kommentarer, reviews og linjekommentarer.
    Det agenten selv har skrevet (starter med 🤖) og rene godkjenninger hoppes over."""
    found = []
    view = json.loads(gh("pr", "view", url, "--json", "comments,reviews"))
    for comment in view["comments"]:
        found.append((comment["id"], comment["body"]))
    for review in view["reviews"]:
        if review["state"] == "APPROVED" or (review["state"] != "CHANGES_REQUESTED" and not review["body"].strip()):
            continue  # en godkjenning er ingen oppgave, og linjekommentarer lager et tomt review rundt seg
        label = "Review: endringer ønsket" if review["state"] == "CHANGES_REQUESTED" else "Review"
        found.append((review["id"], f"{label}. {review['body']}".strip()))
    number = url.rstrip("/").rsplit("/", 1)[-1]
    lines = gh("api", f"repos/{slug()}/pulls/{number}/comments", "--paginate",
               "--jq", ".[] | {id, body, path, line} | tojson")
    for line in lines.splitlines():
        comment = json.loads(line)
        found.append((f"linje-{comment['id']}", f"({comment['path']}:{comment['line']}) {comment['body']}"))
    return [(id, text) for id, text in found if text.strip() and not text.lstrip().startswith("🤖")]
