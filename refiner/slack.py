"""Slack-broen. /new lager oppgaver, og hver oppgave får sin egen tråd.

Boardet er fortsatt kilden til sannhet. Alt som skrives i Slack, blir en kommentar på
oppgaven i Notion («💬 Navn (Slack): …»), og agenten jobber bare mot Notion. Løkka speiler
så agentens kommentarer, statusendringer og Figma-frames tilbake til tråden. Ingenting her
bruker Claude, så det koster ingen tokens.

Kjører med Socket Mode: appen kobler seg til Slack over en websocket, så den trenger ingen
offentlig URL. Broen bruker bare én kanal: `channel` under [slack] i refiner.toml. Krever
SLACK_BOT_TOKEN og SLACK_APP_TOKEN i .env (se README).
"""

import json
import queue
import re
import threading
import urllib.parse
import urllib.request

from .board import DONE, NETWORK_ERRORS
from .config import SLACK, env

STATUS_MESSAGES = {
    "Raffinering": "🔍 Agenten raffinerer oppgaven nå.",
    "Trenger avklaring": "❓ Agenten trenger svar på spørsmålene over. Svar her i tråden, så går svaret rett til oppgaven.",
    "Klar": "✅ Da er oppgaven klar til utvikling. En annen agent plukker den opp snarest.",
    "Under utvikling": "🚧 Oppgaven er under utvikling.",
    "Til gjennomgang": "👀 Oppgaven er til gjennomgang.",
    "Fullført": "🎉 Oppgaven er fullført.",
}
QUESTION = re.compile(r"\bS\d+:")  # agentens spørsmål («🤖 S4: …»), så eieren nevnes
FIGMA_EMBED = re.compile(r'<embed source="(https://www\.figma\.com/[^"]+)">([^<]*)</embed>')


CHANNEL = SLACK.get("channel", "")


def enabled():
    return bool(env("SLACK_BOT_TOKEN") and env("SLACK_APP_TOKEN"))


class Slack:
    def __init__(self, board, state):
        from slack_bolt import App
        from slack_bolt.adapter.socket_mode import SocketModeHandler

        if not CHANNEL:
            raise ValueError("Sett channel under [slack] i refiner.toml til kanal-ID-en Slack-broen skal bruke.")
        self.board = board
        self.app = App(token=env("SLACK_BOT_TOKEN"))
        self.client = self.app.client
        self.links = queue.Queue()      # nye tråder fra /new, som hovedløkka lagrer i sessions.json
        self.wake = threading.Event()   # settes når noen svarer, så løkka sjekker boardet med en gang
        # tråd → oppgave, slik at svar i tråden finner riktig oppgave
        self.threads = {e["slack"]["ts"]: id for id, e in state.items() if e.get("slack")}
        self.app.command("/new")(self.on_new)
        self.app.event("message")(self.on_message)
        SocketModeHandler(self.app, env("SLACK_APP_TOKEN")).connect()  # kjører i en egen tråd

    # --- Slack → Notion ---------------------------------------------------------------

    def on_new(self, ack, command, respond):
        ack()
        text = command.get("text", "").strip()
        if not text:
            respond("Skriv hva du vil endre etter kommandoen, for eksempel `/new Vis åpningstidene i footeren`.")
            return
        user = command["user_id"]
        if command["channel_id"] != CHANNEL:
            respond(f"Bruk /new i <#{CHANNEL}>, så havner oppgaven og tråden der.")
            return
        email = self.client.users_info(user=user)["user"]["profile"].get("email")
        # under testing får alle oppgaver samme eier (test_owner i refiner.toml)
        owner = SLACK.get("test_owner") or (self.board.user_by_email(email) if email else None)
        if not owner:
            respond(f"Fant ingen Notion-bruker med e-posten din ({email or 'ukjent'}). Oppgaven trenger en Eier i Notion, "
                    "så be om tilgang til arbeidsområdet, eller lag oppgaven i Notion i stedet.")
            return
        title = text.splitlines()[0][:80]
        page_id, url = self.board.create_ticket(title, text, owner, f"Opprettet fra Slack av {self.name(user)}.")
        root = self.post_to(CHANNEL, None, f"🆕 *{title}*\n<{url}|Åpne i Notion> · opprettet av <@{user}>\n"
                                           "Jeg legger spørsmål og status her i tråden. Svar her, så går svaret rett til oppgaven.")
        self.threads[root["ts"]] = page_id
        self.links.put((page_id, {"channel": CHANNEL, "ts": root["ts"], "user": user}))
        self.wake.set()

    def on_message(self, event):
        if event.get("channel") != CHANNEL or event.get("bot_id") or event.get("subtype") or not event.get("thread_ts"):
            return  # bare menneskers svar i tråder i kanalen vår
        page_id = self.threads.get(event["thread_ts"])
        if not page_id:
            return
        try:
            self.board.comment(page_id, f"💬 {self.name(event['user'])} (Slack): {event.get('text', '')}")
            self.client.reactions_add(channel=event["channel"], timestamp=event["ts"], name="white_check_mark")
        except NETWORK_ERRORS as error:
            self.post_to(event["channel"], event["thread_ts"], f"⚠️ Fikk ikke lagret svaret i Notion: {error}")
        self.wake.set()

    # --- Notion → Slack ---------------------------------------------------------------

    def sync(self, snap, state):
        """Lagrer nye tråder, lager tråder for oppgaver fra Notion, og speiler nytt til trådene."""
        changed = False
        while not self.links.empty():
            page_id, link = self.links.get()
            state.setdefault(page_id, {"title": snap.get(page_id, {}).get("title", "")})["slack"] = {**link, "posted": [], "status": None}
            changed = True
        for id, entry in state.items():
            ticket = snap.get(id)
            if not ticket:
                continue
            if not entry.get("slack") and entry.get("session") and ticket["status"] not in DONE:
                self.open_thread(id, entry, ticket)
                changed = True
            if entry.get("slack"):
                changed |= self.mirror(id, entry["slack"], ticket)
        return changed

    def open_thread(self, id, entry, ticket):
        """Tråd for en oppgave som ble laget direkte i Notion."""
        owner = self.slack_user(ticket["owners"][0]) if ticket["owners"] else None
        who = f"<@{owner}>" if owner else self.board.person(ticket["owners"][0])["name"] if ticket["owners"] else "ingen"
        root = self.post_to(CHANNEL, None, f"🆕 *{ticket['title']}*\n<{ticket['url']}|Åpne i Notion> · eier {who}\n"
                                                    "Jeg legger spørsmål og status her i tråden. Svar her, så går svaret rett til oppgaven.")
        # det som allerede står på oppgaven, er ikke nytt for tråden
        entry["slack"] = {"channel": CHANNEL, "ts": root["ts"], "user": owner,
                          "posted": [c["id"] for c in ticket["all_comments"]], "status": None}
        self.threads[root["ts"]] = id

    def mirror(self, id, link, ticket):
        changed = False
        for comment in ticket["all_comments"]:
            if comment["id"] in link["posted"]:
                continue
            link["posted"].append(comment["id"])
            changed = True
            if comment["text"].startswith("💬"):
                continue  # kom fra Slack, står allerede i tråden
            if comment["human"]:
                self.post(link, f"📝 {self.board.person(comment['author'])['name']} i Notion: {comment['text']}")
            else:
                mention = f" <@{link['user']}>" if link.get("user") and QUESTION.search(comment["text"]) else ""
                self.post(link, comment["text"] + mention)
        if ticket["status"] != link.get("status"):
            if ticket["status"] in ("Trenger avklaring", "Klar"):
                self.post_figma(id, link, ticket)
            if ticket["status"] in STATUS_MESSAGES:
                self.post(link, STATUS_MESSAGES[ticket["status"]])
            link["status"] = ticket["status"]
            changed = True
        return changed

    def post_figma(self, id, link, ticket):
        """Figma-framene agenten brukte. Med FIGMA_TOKEN lastes de opp som bilder, ellers som lenker."""
        try:
            frames = FIGMA_EMBED.findall(self.board.markdown(id))
        except NETWORK_ERRORS:
            frames = []
        if ticket["figma"] and ticket["figma"] not in {url for url, _ in frames}:
            frames.insert(0, (ticket["figma"], "Hovedframe"))
        posted = link.setdefault("figma", [])
        for url, caption in frames:
            if url in posted:
                continue
            posted.append(url)
            image = figma_png(url)
            if image:
                self.client.files_upload_v2(channel=link["channel"], thread_ts=link["ts"], content=image,
                                            filename="figma.png", title=caption or "Figma",
                                            initial_comment=f"🎨 {caption or 'Figma'}: <{url}|åpne i Figma>")
            else:
                self.post(link, f"🎨 <{url}|{caption or 'Figma-frame'}>")

    # --- Hjelpere ---------------------------------------------------------------------

    def post(self, link, text):
        return self.post_to(link["channel"], link["ts"], text)

    def post_to(self, channel, thread_ts, text):
        try:
            return self.client.chat_postMessage(channel=channel, thread_ts=thread_ts, text=text, unfurl_links=True)
        except Exception as error:
            if "not_in_channel" in str(error):
                raise RuntimeError(f"Boten er ikke med i kanalen {channel}. Inviter den: /invite @Raffineringsagent") from error
            raise

    def name(self, slack_user):
        profile = self.client.users_info(user=slack_user)["user"]["profile"]
        return profile.get("real_name") or profile.get("display_name") or "noen"

    def slack_user(self, notion_user):
        email = self.board.person(notion_user)["email"]
        if not email:
            return None
        try:
            return self.client.users_lookupByEmail(email=email)["user"]["id"]
        except Exception:
            return None


def figma_png(url):
    """PNG av en Figma-frame via Figma REST API, hvis FIGMA_TOKEN er satt. Ellers None."""
    token = env("FIGMA_TOKEN")
    match = re.search(r"figma\.com/(?:design|file|proto)/([A-Za-z0-9]+)(?:/branch/([A-Za-z0-9]+))?.*?node-id=([0-9]+[-:][0-9]+)", url)
    if not token or not match:
        return None
    key, node = match.group(2) or match.group(1), match.group(3).replace("-", ":")
    try:
        request = urllib.request.Request(
            f"https://api.figma.com/v1/images/{key}?ids={urllib.parse.quote(node)}&format=png&scale=2",
            headers={"X-Figma-Token": token})
        with urllib.request.urlopen(request, timeout=60) as response:
            image_url = json.load(response)["images"].get(node)
        if not image_url:
            return None
        with urllib.request.urlopen(image_url, timeout=60) as response:
            return response.read()
    except Exception:
        return None  # da blir det en lenke i stedet
