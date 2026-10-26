"""Starter en Claude-økt og viser hvert steg mens den jobber."""

import json
import subprocess
import time

from .config import AGENT, CONFIG, HERE, rules_prompt
from .display import say, HIDDEN_TOOLS, SOURCE_COLORS, USED_SERVERS, describe_call, describe_result, dim, paint, result_text, short


# Ekstra verktøy for utviklingsøkter. Redigering og kommandoer er begrenset til arbeidskopiene i
# .cache/work. Push og PR gjør Python etterpå, så agenten kan aldri pushe selv.
DEV_TOOLS = [
    "Edit(.cache/work/**)", "Write(.cache/work/**)",
    "Bash(npm *)", "Bash(corepack pnpm *)", "Bash(npx tsc*)", "Bash(npx eslint*)",
    "Bash(npx prettier*)", "Bash(npx vitest*)", "Bash(npx jest*)", "Bash(npx playwright*)",
    "Bash(git -C .cache/work/* status*)", "Bash(git -C .cache/work/* diff*)", "Bash(git -C .cache/work/* add *)",
    "Bash(git -C .cache/work/* commit *)", "Bash(git -C .cache/work/* log*)",
    # konflikter med main løses på branchen; --continue uten editor, siden ingen kan skrive i den
    "Bash(git -C .cache/work/* fetch origin *)", "Bash(git -C .cache/work/* rebase origin/*)",
    "Bash(git -C .cache/work/* rebase --abort)", "Bash(git -C .cache/work/* -c core.editor=true rebase --continue)",
    # ufarlige hjelpekommandoer. I dontAsk-modus må hver del av en sammensatt kommando være tillatt,
    # og Claude Code stopper alltid «cd <mappe> && git …», så agenten bruker git -C og npm --prefix
    "Bash(ls .cache/work/*)", "Bash(wc *)", "Bash(which *)", "Bash(node -v)", "Bash(node --version)",
    "Bash(mkdir -p .cache/work/*)",
    "mcp__claude_ai_Figma__get_design_context", "mcp__claude_ai_Figma__get_variable_defs",
]
# Ekstra verktøy for raffineringsøkter når skisser er på: skrive skisser i Figma via /sketch-figma
SKETCH_TOOLS = ["ReadMcpResourceTool", "mcp__claude_ai_Figma__create_session", "mcp__claude_ai_Figma__use_figma",
                "mcp__claude_ai_Figma__search_design_system", "mcp__claude_ai_Figma__get_screenshot"]
# Gjennomgang før main: kan lese, kjøre sjekkene og skrive dommen (review.md), men ikke endre koden
REVIEW_TOOLS = [
    "Write(.cache/work/*/review.md)",
    "Bash(npm *)", "Bash(corepack pnpm *)", "Bash(npx tsc*)", "Bash(npx eslint*)", "Bash(npx vitest*)", "Bash(npx jest*)",
    "Bash(git -C .cache/work/* diff*)", "Bash(git -C .cache/work/* log*)", "Bash(git -C .cache/work/* show*)",
    "Bash(git -C .cache/work/* status*)", "Bash(ls .cache/work/*)", "Bash(wc *)",
]
# Nettet: søk og lesing av sider, for dokumentasjon, API-er og pakker. Alle økter får det.
WEB_TOOLS = ["WebSearch", "WebFetch"]
NEVER = ["Agent", "Bash(*.env*)", "Bash(git push*)", "Bash(git * push*)", "Bash(npm publish*)", "Bash(git remote*)", "Bash(git config*)"]


MODES = {  # økttype → (modellinnstilling, innsats, ekstra verktøy)
    "refine": ("model", "effort", []),
    "develop": ("dev_model", "effort", DEV_TOOLS),
    "tidy": ("dev_model", "effort", DEV_TOOLS),        # rydder notater på en egen branch, som en utviklingsoppgave
    "review": ("review_model", "review_effort", REVIEW_TOOLS),
}


def claude_command(prompt, session_flags, mode="refine"):
    # dontAsk: bare verktøyene som er tillatt i .claude/settings.json (og verktøyene for modusen) kjører, alt annet avvises
    model_key, effort_key, extra = MODES[mode]
    model = AGENT.get(model_key) or AGENT.get("model", "claude-sonnet-5-5")
    cmd = ["claude", "-p", prompt, *session_flags, "--permission-mode", "dontAsk",
           "--model", model, "--effort", AGENT.get(effort_key) or AGENT.get("effort", "medium"),
           "--append-system-prompt", rules_prompt(),
           # underagenter dobler kostnaden, og kodesøk klarer økta selv med Grep og Glob
           "--disallowedTools", *NEVER,
           "--mcp-config", str(HERE / ".mcp.json"), "--output-format", "stream-json", "--verbose"]
    allowed = (list(WEB_TOOLS) if AGENT.get("web", True) else []) + extra
    if mode == "refine" and CONFIG.get("design", {}).get("sketches", True) and CONFIG.get("design", {}).get("figma"):
        allowed += SKETCH_TOOLS
    if allowed:
        cmd += ["--allowedTools", *allowed]
    local = CONFIG.get("code", {}).get("local")
    if local:  # en mappe utenfor prosjektet må legges til eksplisitt
        cmd += ["--add-dir", str((HERE / local).resolve())]
    return cmd


def run_claude(prompt, session_flags, verbose=False, mode="refine", baseline=0.0):
    """Kjører én Claude-tur og viser hvert steg.

    Claude Code oppgir kostnaden for hele økta (fortsettelser og forker arver tidligere kostnad), så
    kostnaden for akkurat dette kallet er totalen minus det økta hadde kostet før (baseline).
    Returnerer en dict: ok, cost (dette kallet), total (økta så langt), turns, seconds, context (tokens per steg)."""
    cmd = claude_command(prompt, session_flags, mode)
    run = {"ok": False, "cost": 0.0, "total": baseline, "turns": 0, "seconds": 0.0, "context": 0}
    calls = {}  # tool_use_id -> (kilde, startet)
    proc = subprocess.Popen(cmd, cwd=HERE, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, text=True, bufsize=1)
    for line in proc.stdout:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        kind = event.get("type")

        if kind == "system" and event.get("subtype") == "init":
            servers = ", ".join(f"{s['name']} {'✓' if s.get('status') == 'connected' else '✗ ' + s.get('status', '')}"
                                for s in event.get("mcp_servers", []) if s["name"] in USED_SERVERS)
            say(dim(f"Startet {event.get('model', '')} · {servers}"))

        elif kind == "assistant":
            for block in event["message"].get("content", []):
                if block.get("type") == "text" and block["text"].strip():
                    say(paint(block["text"].strip(), "1"))
                elif block.get("type") == "tool_use" and block["name"] not in HIDDEN_TOOLS:
                    source, what = describe_call(block["name"], block.get("input") or {})
                    calls[block["id"]] = (source, time.monotonic())
                    say(f"  {paint(f'{source:<10}', SOURCE_COLORS.get(source, '0'))} {what}")
                    if verbose:
                        say(dim("             " + short(json.dumps(block.get("input")), 400)))

        elif kind == "user":
            content = event.get("message", {}).get("content", [])
            for block in content if isinstance(content, list) else []:
                if block.get("type") != "tool_result" or block.get("tool_use_id") not in calls:
                    continue
                source, started = calls.pop(block["tool_use_id"])
                text = result_text(block.get("content"))
                took = dim(f" {time.monotonic() - started:.1f}s")
                say(f"  {'':<10} {describe_result(source, text, block.get('is_error'))}{took}")
                if verbose:
                    say(dim("             " + short(text, 600)))

        elif kind == "result":
            usage = event.get("usage", {})
            total = event.get("total_cost_usd", 0) or 0
            turns = event.get("num_turns") or 1
            run.update(ok=not event.get("is_error"), total=total, cost=max(0.0, total - baseline), turns=turns,
                       seconds=event.get("duration_ms", 0) / 1000,
                       context=(usage.get("input_tokens", 0) + usage.get("cache_read_input_tokens", 0)
                                + usage.get("cache_creation_input_tokens", 0)) // turns)
            outcome = paint("✓ ferdig", "32") if run["ok"] else paint("✗ feilet", "31")
            say(f"{outcome} · {turns} steg · {run['seconds']:.0f}s · ${run['cost']:.2f}"
                + dim(f" (økta totalt ${total:.2f}, ~{run['context'] // 1000}k tokens per steg)"))
    run["ok"] = proc.wait() == 0 and run["ok"]
    return run


COMPACT = ("/compact Behold dette ordrett: oppgavens URL og tittel; stiene og linjenumrene du fant i koden og hva de gjør; "
           "Figma-frames (URL med node-id) og hva de viser, og hvilke tilstander som mangler; Raffinering-seksjonen slik den "
           "står nå; åpne og besvarte spørsmål med ID; antakelser; siste Tilstand-linje; skisser i Figma (URL med node-id, "
           "versjon, og om de er godkjent). Dropp rå verktøysvar og hele filinnhold.")

DEV_COMPACT = ("/compact Behold dette ordrett: oppgavens URL og tittel; arbeidskopien og branchen; akseptansekriteriene og "
               "hvilke som er gjort; filene du har endret og hvorfor; hvilke sjekker (lint, typer, tester, bygg) som er kjørt "
               "og resultatet; Figma-frames og den godkjente skissen du bygger etter; spørsmålet du venter svar på. "
               "Dropp rå verktøysvar og hele filinnhold.")
