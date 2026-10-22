#!/usr/bin/env python3
"""Kjører /refine-board uten innblanding og viser hva den gjør, steg for steg.

Selve raffineringen ligger i skillene i .claude/skills (start med refine-board),
innstillingene i refiner.toml og verktøytilgangene i .claude/settings.json.
Dette skriptet starter bare Claude uten spørsmål, skriver ut fremdriften og venter.

    python3 refine.py              # én runde
    python3 refine.py --watch      # kjør en runde, vent til noe nytt skjer på boardet, gjenta
    python3 refine.py --verbose    # skriv også ut hele inndata og svar for hvert verktøykall

Mens den venter i --watch, sjekker skriptet boardet selv via Notion API (billig, ingen
Claude) og starter en ny runde bare når det kommer en ny oppgave i Innboks, en ny
kommentar fra et menneske, eller når et menneske endrer en oppgave i pipelinen.
"""

import argparse
import json
import re
import subprocess
import sys
import time
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent


def claude_command():
    # dontAsk: bare verktøyene som er tillatt i .claude/settings.json kjører, alt annet avvises
    cmd = ["claude", "-p", "/refine-board", "--permission-mode", "dontAsk",
           "--mcp-config", str(HERE / ".mcp.json"), "--output-format", "stream-json", "--verbose"]
    local = tomllib.loads((HERE / "refiner.toml").read_text()).get("code", {}).get("local")
    if local:  # en mappe utenfor prosjektet må legges til eksplisitt
        cmd += ["--add-dir", str((HERE / local).resolve())]
    return cmd


# --- Vis hva som skjer -----------------------------------------------------------

COLOR = sys.stdout.isatty()


def paint(text, code):
    return f"\033[{code}m{text}\033[0m" if COLOR else text


def dim(text):
    return paint(text, "2")


def short(value, limit=90):
    text = " ".join(str(value).split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def page_ref(value):
    match = re.search(r"[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}", str(value))
    # slutten av ID-en, fordi alle ID-er i et arbeidsområde starter likt
    return "…" + match.group(0).replace("-", "")[-6:] if match else short(value, 40)


def describe_call(name, args):
    """Gjør et verktøykall om til (kilde, hva det gjør)."""
    if name.startswith("mcp__claude_ai_Notion__notion-"):
        action = name.removeprefix("mcp__claude_ai_Notion__notion-")
        target = args.get("id") or args.get("page_id") or args.get("query") or ""
        if action == "query-data-sources":
            data = args.get("data", {})
            target = short(data.get("query") or json.dumps(data.get("filter", "")), 70)
        return "Notion", f"{action} {page_ref(target) if action in ('fetch', 'get-comments') else short(target, 70)}"
    if name.startswith("mcp__notion-api__API-"):
        action = name.removeprefix("mcp__notion-api__API-")
        page = page_ref(args.get("page_id") or args.get("parent") or "")
        if action == "patch-page":
            props = args.get("properties") or {}
            if isinstance(props, str):
                props = json.loads(props) if props.strip().startswith("{") else {}
            status = props.get("Status", {}).get("status", {}).get("name")
            fields = ", ".join(k for k in props if k != "Status")
            moved = f"→ {status} " if status else ""
            return "Notion API", f"oppdaterer {page} {moved}{fields}".rstrip()
        if action == "create-a-comment":
            text = "".join(part.get("text", {}).get("content", "") if "text" in part else "@person" if part.get("type") == "mention" else ""
                           for part in args.get("rich_text", []) if isinstance(part, dict))
            return "Notion API", f"kommenterer {page}: {short(text, 60)}"
        if action == "update-page-markdown":
            return "Notion API", f"redigerer innholdet i {page} ({args.get('type')})"
        if action == "post-page":
            return "Notion API", "lager oppgave"
        return "Notion API", f"{action} {page}"
    if name.startswith("mcp__claude_ai_Figma__"):
        action = name.removeprefix("mcp__claude_ai_Figma__")
        return "Figma", f"{action} {args.get('fileKey', '')} {args.get('nodeId', '')}".rstrip()
    if name == "Bash":
        return "Git", short(args.get("command", ""), 80)
    if name == "Read":
        return "Repo", f"leser {args.get('file_path', '')}"
    if name == "Grep":
        return "Repo", f"søker {short(args.get('pattern', ''), 50)} {args.get('path', '')}".rstrip()
    if name == "Glob":
        return "Repo", f"finner {args.get('pattern', '')}"
    if name == "Agent":
        return "Agent", f"{args.get('subagent_type', '')}: {short(args.get('description', ''), 70)}"
    if name == "Skill":
        return "Skill", f"/{args.get('skill', '')} {short(args.get('args', ''), 60)}".rstrip()
    return name, short(json.dumps(args), 70)


def result_text(content):
    if isinstance(content, str):
        return content
    parts = []
    for part in content or []:
        if part.get("type") == "text":
            parts.append(part.get("text", ""))
        elif part.get("type") == "image":
            parts.append("[bilde]")
    return "\n".join(parts)


def describe_result(source, text, is_error):
    """Et sammendrag på én linje av et verktøysvar."""
    if is_error:
        return paint("✗ " + short(text, 110), "31")
    size = f"{len(text) / 1024:.1f} KB" if len(text) > 1024 else f"{len(text)} tegn"
    if text == "[bilde]":
        return paint("✓ skjermbilde", "32")
    try:
        data = json.loads(text)
    except ValueError:
        data = None
    if isinstance(data, dict):
        if isinstance(data.get("results"), list):
            return paint(f"✓ {len(data['results'])} treff", "32")
        if data.get("object") == "error":
            return paint(f"✗ {data.get('status')} {short(data.get('message', ''), 90)}", "31")
        if data.get("object"):
            return paint(f"✓ {data['object']} lagret", "32")
    title = re.search(r'"title"\s*:\s*"([^"]+)"', text)
    if title:
        return paint(f"✓ \"{short(title.group(1), 50)}\"", "32") + dim(f" ({size})")
    return paint("✓ ", "32") + dim(f"{short(text.strip().splitlines()[0] if text.strip() else '(tom)', 70)} ({size})")


USED_SERVERS = {"notion-api", "claude.ai Notion", "claude.ai Figma"}
HIDDEN_TOOLS = {"ToolSearch"}  # Claude Code som laster verktøyskjemaer, ikke agentens arbeid
SOURCE_COLORS = {"Agent": "1", "Git": "32", "Notion": "36", "Notion API": "35", "Figma": "33", "Repo": "34", "Skill": "1"}


def run_pass(verbose=False):
    cmd = claude_command()
    calls = {}  # tool_use_id -> (kilde, startet)
    proc = subprocess.Popen(cmd, cwd=HERE, stdout=subprocess.PIPE, text=True, bufsize=1)
    for line in proc.stdout:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        kind = event.get("type")

        if kind == "system" and event.get("subtype") == "init":
            servers = ", ".join(f"{s['name']} {'✓' if s.get('status') == 'connected' else '✗ ' + s.get('status', '')}"
                                for s in event.get("mcp_servers", []) if s["name"] in USED_SERVERS)
            print(dim(f"Startet {event.get('model', '')} · {servers}"))

        elif kind == "assistant":
            for block in event["message"].get("content", []):
                if block.get("type") == "text" and block["text"].strip():
                    print("\n" + paint(block["text"].strip(), "1"))
                elif block.get("type") == "tool_use" and block["name"] not in HIDDEN_TOOLS:
                    source, what = describe_call(block["name"], block.get("input") or {})
                    calls[block["id"]] = (source, time.monotonic())
                    print(f"  {paint(f'{source:<10}', SOURCE_COLORS.get(source, '0'))} {what}")
                    if verbose:
                        print(dim("             " + short(json.dumps(block.get("input")), 400)))

        elif kind == "user":
            content = event.get("message", {}).get("content", [])
            for block in content if isinstance(content, list) else []:
                if block.get("type") != "tool_result" or block.get("tool_use_id") not in calls:
                    continue
                source, started = calls.pop(block["tool_use_id"])
                text = result_text(block.get("content"))
                took = dim(f" {time.monotonic() - started:.1f}s")
                print(f"  {'':<10} {describe_result(source, text, block.get('is_error'))}{took}")
                if verbose:
                    print(dim("             " + short(text, 600)))

        elif kind == "result":
            outcome = paint("✗ feilet", "31") if event.get("is_error") else paint("✓ ferdig", "32")
            print(f"\n{outcome} · {event.get('num_turns')} steg · {event.get('duration_ms', 0) / 1000:.0f}s"
                  f" · ${event.get('total_cost_usd', 0):.2f}")
    return proc.wait()


# --- Vent på noe nytt (--watch) ----------------------------------------------------
# Navnene må stemme med board.md.

WATCHED = {"Trenger avklaring", "Raffinering", "Klar"}  # statuser der kommentarer og endringer betyr noe
INBOX = {"Innboks", None}  # tom status regnes som Innboks
SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


class Board:
    """Leser boardet direkte fra Notion API, uten Claude, for å se om noe nytt har skjedd."""

    def __init__(self):
        env = (HERE / ".env").read_text() if (HERE / ".env").exists() else ""
        key = re.search(r"^NOTION_API_KEY=(.+)$", env, re.M)
        if not key:
            sys.exit("Sett NOTION_API_KEY i .env (se .env.example)")
        self.key = key.group(1).strip()
        board = tomllib.loads((HERE / "refiner.toml").read_text())["notion"]["board"]
        page = re.search(r"[0-9a-f]{32}", board.replace("-", "")).group(0)
        database = next(b["id"] for b in self.api("GET", f"blocks/{page}/children")["results"]
                        if b["type"] == "child_database")
        self.data_source = self.api("GET", f"databases/{database}")["data_sources"][0]["id"]
        # agentens egne endringer og kommentarer gjøres av integrasjonen, så de har denne bot-ID-en
        self.bot = self.api("GET", "users/me")["id"]

    def api(self, method, path, body=None):
        request = urllib.request.Request(
            f"https://api.notion.com/v1/{path}", method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Authorization": f"Bearer {self.key}", "Notion-Version": "2025-09-03",
                     "Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)

    def pages(self, path, body=None):
        cursor = None
        while True:
            if body is not None:
                result = self.api("POST", path, {**body, **({"start_cursor": cursor} if cursor else {})})
            else:
                result = self.api("GET", path + (f"&start_cursor={cursor}" if cursor else ""))
            yield from result["results"]
            if not result.get("has_more"):
                return
            cursor = result["next_cursor"]

    def snapshot(self):
        """Det som betyr noe for agenten, per oppgave. Agentens egne endringer er utelatt."""
        tickets = {}
        query = {"filter": {"or": [{"property": "Klar for pipeline", "checkbox": {"equals": True}},
                                   {"property": "Status", "status": {"equals": "Raffinering"}},
                                   {"property": "Status", "status": {"equals": "Klar"}}]}}
        for page in self.pages(f"data_sources/{self.data_source}/query", query):
            props = page["properties"]
            status = (props["Status"]["status"] or {}).get("name")
            ticket = {
                "title": "".join(t["plain_text"] for t in props["Oppgave"]["title"]) or "(uten tittel)",
                "status": status,
                "ready": props["Klar for pipeline"]["checkbox"],
                # bare endringer gjort av et menneske, ikke av agenten
                "edited": page["last_edited_time"] if page["last_edited_by"]["id"] != self.bot else None,
                "comment": None,
            }
            if status in WATCHED:
                for comment in self.pages(f"comments?block_id={page['id']}&page_size=100"):
                    text = "".join(t.get("plain_text", "") for t in comment["rich_text"])
                    if comment["created_by"]["id"] != self.bot and not text.startswith("🤖"):
                        ticket["comment"] = comment["id"]
            tickets[page["id"]] = ticket
        return tickets


def what_changed(before, after):
    """Lister det som er nytt siden forrige runde, som tekst. Tom liste betyr ingenting nytt."""
    news = []
    for id, now in after.items():
        old = before.get(id, {})
        title = f"«{now['title']}»"
        if now["ready"] and now["status"] in INBOX and not (old.get("ready") and old.get("status") in INBOX):
            news.append(f"ny oppgave i Innboks: {title}")
        if now["comment"] and now["comment"] != old.get("comment"):
            news.append(f"ny kommentar på {title}")
        if now["status"] in WATCHED and now["edited"] and now["edited"] != old.get("edited"):
            news.append(f"{title} er endret av et menneske")
    return news


def wait_for_news(board, before, poll, max_wait):
    """Spinner til noe nytt skjer på boardet, eller til max_wait minutter har gått."""
    deadline = time.monotonic() + max_wait * 60
    latest = time.strftime("%H:%M", time.localtime(time.time() + max_wait * 60))
    if not COLOR:
        print(f"Venter på nye oppgaver eller kommentarer (sjekker hvert {poll:g}. sekund, full runde senest {latest})")
    frame, checked = 0, "–"
    while time.monotonic() < deadline:
        try:
            news = what_changed(before, board.snapshot())
            checked = time.strftime("%H:%M:%S")
        except (urllib.error.URLError, TimeoutError, KeyError) as error:
            news, checked = [], paint(f"feil: {short(error, 60)}", "31")
        if news:
            if COLOR:
                print("\r\033[K", end="")
            for item in news:
                print(paint("● " + item, "33"))
            return
        next_check = time.monotonic() + poll
        while time.monotonic() < min(next_check, deadline):
            if COLOR:
                print(f"\r\033[K{paint(SPINNER[frame % len(SPINNER)], '36')} Venter på nye oppgaver eller kommentarer"
                      + dim(f" · sist sjekket {checked} · full runde senest {latest}"), end="", flush=True)
            frame += 1
            time.sleep(0.1)
    if COLOR:
        print("\r\033[K", end="")
    print(dim(f"Ingenting nytt på {max_wait:g} min, kjører en full runde for sikkerhets skyld."))


def watch(verbose, poll, max_wait):
    board = Board()
    while True:
        print(paint(f"\n=== {time.strftime('%H:%M:%S')} raffineringsrunde ===", "1"))
        run_pass(verbose)
        time.sleep(5)  # gi Notion et øyeblikk på å vise agentens egne endringer
        try:
            before = board.snapshot()
        except (urllib.error.URLError, TimeoutError, KeyError) as error:
            print(paint(f"Klarte ikke å lese boardet: {short(error, 80)}", "31"))
            before = {}
        print()
        wait_for_news(board, before, poll, max_wait)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--watch", action="store_true", help="kjør en runde, vent til noe nytt skjer på boardet, og gjenta")
    parser.add_argument("--poll", type=float, default=20, metavar="SEKUNDER", help="hvor ofte boardet sjekkes mens den venter (standard 20)")
    parser.add_argument("--max-wait", type=float, default=30, metavar="MINUTTER",
                        help="kjør en full runde etter så lang tid selv om ingenting er nytt, for å fange opp kommentarer på enkeltlinjer og fastlåste oppgaver (standard 30)")
    parser.add_argument("--verbose", action="store_true", help="skriv også ut hele inndata og svar for hvert verktøykall")
    args = parser.parse_args()

    if not args.watch:
        sys.exit(run_pass(args.verbose))
    try:
        watch(args.verbose, args.poll, args.max_wait)
    except KeyboardInterrupt:
        print(dim("\nStoppet."))


if __name__ == "__main__":
    main()
