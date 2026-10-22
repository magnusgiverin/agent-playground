"""Starter en Claude-økt og viser hvert steg mens den jobber."""

import json
import subprocess
import time

from .config import AGENT, CONFIG, HERE, rules_prompt
from .display import HIDDEN_TOOLS, SOURCE_COLORS, USED_SERVERS, describe_call, describe_result, dim, paint, result_text, short


def claude_command(prompt, session_flags):
    # dontAsk: bare verktøyene som er tillatt i .claude/settings.json kjører, alt annet avvises
    cmd = ["claude", "-p", prompt, *session_flags, "--permission-mode", "dontAsk",
           "--model", AGENT.get("model", "claude-sonnet-5-5"), "--effort", AGENT.get("effort", "medium"),
           "--append-system-prompt", rules_prompt(),
           # underagenter dobler kostnaden, og kodesøk klarer økta selv med Grep og Glob
           "--disallowedTools", "Agent",
           "--mcp-config", str(HERE / ".mcp.json"), "--output-format", "stream-json", "--verbose"]
    local = CONFIG.get("code", {}).get("local")
    if local:  # en mappe utenfor prosjektet må legges til eksplisitt
        cmd += ["--add-dir", str((HERE / local).resolve())]
    return cmd

def run_claude(prompt, session_flags, verbose=False):
    """Kjører én Claude-tur og viser hvert steg. Returnerer (ferdig uten feil, kostnad i dollar)."""
    cmd = claude_command(prompt, session_flags)
    ok, cost = False, 0.0
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
            ok, cost = not event.get("is_error"), event.get("total_cost_usd", 0)
            outcome = paint("✓ ferdig", "32") if ok else paint("✗ feilet", "31")
            print(f"\n{outcome} · {event.get('num_turns')} steg · {event.get('duration_ms', 0) / 1000:.0f}s"
                  f" · ${event.get('total_cost_usd', 0):.2f}")
    return proc.wait() == 0 and ok, cost


COMPACT = ("/compact Behold dette ordrett: oppgavens URL og tittel; stiene og linjenumrene du fant i koden og hva de gjør; "
           "Figma-frames (URL med node-id) og hva de viser, og hvilke tilstander som mangler; Raffinering-seksjonen slik den "
           "står nå; åpne og besvarte spørsmål med ID; antakelser; siste Tilstand-linje. Dropp rå verktøysvar og hele filinnhold.")
