"""Hvordan det agenten gjør, vises i terminalen."""

import json
import re
import sys

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
SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
