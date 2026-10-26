"""Leser og skriver boardet direkte via Notion API, uten Claude. Navnene må stemme med board.md."""

import json
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime

from .config import INSTANCE, env

# Kolonnene på boardet, i rekkefølge. Navnene må stemme med board.md.
INNBOKS, RAFFINERING, AVKLARING, KLAR = "Innboks", "Raffinering", "Trenger avklaring", "Klar"
UTVIKLING, GJENNOMGANG, FULLFORT = "Under utvikling", "Til gjennomgang", "Fullført"
INBOX = {INNBOKS, None}  # tom status regnes som Innboks
DONE = {KLAR, UTVIKLING, GJENNOMGANG, FULLFORT}  # Klar eller lenger
NETWORK_ERRORS = (urllib.error.URLError, TimeoutError, KeyError, ValueError)
REQUIRED_PROPERTIES = ["Oppgave", "Status", "Etiketter", "Størrelse", "Figma-lenke", "Blokkeringsnotat",
                       "Blokkert av", "Blokkerer", "Klar for pipeline", "Eier", "Pull request"]
REQUIRED_STATUSES = [INNBOKS, RAFFINERING, AVKLARING, KLAR, UTVIKLING, GJENNOMGANG, FULLFORT]
# Egenskaper som bare refine.py skriver, og som lages av seg selv hvis de mangler. Se lease.py og slack.py.
MANAGED_PROPERTIES = {"Agent": {"rich_text": {}}, "Slack-tråd": {"url": {}}}


def epoch(iso):
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() if iso else 0.0


class Board:
    """Leser boardet direkte fra Notion API, uten Claude."""

    def __init__(self):
        self.key = env("NOTION_API_KEY")
        if not self.key:
            sys.exit("Sett NOTION_API_KEY (i miljøet eller i env-fila, se .env.example)")
        match = re.search(r"[0-9a-f]{32}", INSTANCE.board.replace("-", ""))
        if not match:
            sys.exit("Sett NOTION_BOARD til URL-en til Notion-siden med boardet (se .env.example)")
        try:
            database = next(b["id"] for b in self.api("GET", f"blocks/{match.group(0)}/children")["results"]
                            if b["type"] == "child_database")
        except urllib.error.HTTPError as error:
            sys.exit(f"Fikk ikke lest boardsiden ({error.code}). Legg integrasjonen til under ••• → Connections "
                     "på boardsiden, og sjekk at NOTION_BOARD peker på den.")
        self.data_source = self.api("GET", f"databases/{database}")["data_sources"][0]["id"]
        # agentens egne endringer og kommentarer gjøres av integrasjonen, så de har denne bot-ID-en
        self.bot = self.api("GET", "users/me")["id"]
        self.people = {}  # Notion-bruker-ID → {"name", "email"}, hentet ved behov

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
        """Oppgavene agenten bryr seg om, med det som betyr noe for dem. Agentens egne endringer er utelatt."""
        from .lease import parse  # lease.py bruker Board, så den importeres her
        tickets = {}
        query = {"filter": {"or": [{"property": "Klar for pipeline", "checkbox": {"equals": True}},
                                   {"property": "Status", "status": {"equals": RAFFINERING}},
                                   {"property": "Status", "status": {"equals": KLAR}},
                                   {"property": "Status", "status": {"equals": UTVIKLING}},
                                   {"property": "Status", "status": {"equals": GJENNOMGANG}}]}}
        for page in self.pages(f"data_sources/{self.data_source}/query", query):
            props = page["properties"]
            status = (props["Status"]["status"] or {}).get("name")
            ticket = {
                "title": "".join(t["plain_text"] for t in props["Oppgave"]["title"]) or "(uten tittel)",
                "url": page["url"],
                "status": status,
                "ready": props["Klar for pipeline"]["checkbox"],
                "owners": [p["id"] for p in props["Eier"]["people"]],
                "creator": page["created_by"]["id"],
                "blocked_by": [r["id"] for r in props["Blokkert av"]["relation"]],
                "tags": sorted(o["name"] for o in props["Etiketter"]["multi_select"]),
                "size": (props["Størrelse"]["select"] or {}).get("name"),
                "figma": props["Figma-lenke"]["url"],
                "blocker": "".join(t["plain_text"] for t in props["Blokkeringsnotat"]["rich_text"]),
                "pr": props["Pull request"]["url"],
                # hvilken instans som har oppgaven (lease.py), og Slack-tråden (slack.py); begge delt mellom instansene
                "lease": parse("".join(t["plain_text"] for t in props["Agent"]["rich_text"])),
                "slack_thread": props["Slack-tråd"]["url"],
                # bare endringer gjort av et menneske, ikke av agenten
                "edited": page["last_edited_time"] if page["last_edited_by"]["id"] != self.bot else None,
                "comment": None,   # ID-en til siste kommentar fra et menneske
                "comments": [],    # (ID, tekst) for alle kommentarer fra mennesker
                "all_comments": [],  # alle kommentarene, også agentens, til speilingen i Slack
            }
            if status not in INBOX:
                ticket["all_comments"] = self.comments(page["id"])
                ticket["comments"] = [(c["id"], c["text"]) for c in ticket["all_comments"] if c["human"]]
                ticket["comment"] = ticket["comments"][-1][0] if ticket["comments"] else None
            # siste gang et menneske gjorde noe (kommentar eller endring), til overtakelse mellom instanser
            ticket["human_at"] = max([epoch(ticket["edited"])]
                                     + [c["at"] for c in ticket["all_comments"] if c["human"]])
            tickets[page["id"]] = ticket
        return tickets

    def comments(self, page_id):
        """Alle sidekommentarene. «human» er sant for mennesker, også de som svarte fra Slack («💬 …»)."""
        comments = []
        for comment in self.pages(f"comments?block_id={page_id}&page_size=100"):
            text = "".join(t.get("plain_text", "") for t in comment["rich_text"])
            author = comment["created_by"]["id"]
            from_agent = author == self.bot and not text.startswith("💬")
            comments.append({"id": comment["id"], "author": author, "text": text, "at": epoch(comment["created_time"]),
                             "human": not from_agent and not text.startswith("🤖")})
        return comments

    def inline_comments(self, page_id):
        """Menneskers kommentarer på enkeltlinjer i siden. API-et gir dem bare blokk for blokk, så dette er
        mange kall og brukes sjelden (se InlineWatcher)."""
        found = []
        for block in self.pages(f"blocks/{page_id}/children?page_size=100"):
            for comment in self.pages(f"comments?block_id={block['id']}&page_size=100"):
                text = "".join(t.get("plain_text", "") for t in comment["rich_text"])
                if (comment["created_by"]["id"] != self.bot or text.startswith("💬")) and not text.startswith("🤖"):
                    found.append((comment["id"], text))
        return found

    def human_comments(self, page_id):
        return [(c["id"], c["text"]) for c in self.comments(page_id) if c["human"]]

    def person(self, user_id):
        """Navn og e-post for en Notion-bruker (e-post krever at integrasjonen kan lese brukerinfo)."""
        if user_id not in self.people:
            try:
                user = self.api("GET", f"users/{user_id}")
                self.people[user_id] = {"name": user.get("name") or "noen",
                                        "email": (user.get("person") or {}).get("email")}
            except NETWORK_ERRORS:
                self.people[user_id] = {"name": "noen", "email": None}
        return self.people[user_id]

    def user_by_email(self, email):
        """Notion-brukeren med denne e-postadressen, eller None."""
        for user in self.pages("users?page_size=100"):
            if (user.get("person") or {}).get("email", "").lower() == (email or "").lower():
                return user["id"]
        return None

    def create_ticket(self, title, description, owner, source):
        """Lager en oppgave i Innboks med Klar for pipeline og eier, slik malen ser ut."""
        paragraph = lambda text: {"type": "paragraph", "paragraph": {"rich_text": [{"text": {"content": text[:2000]}}]}}
        page = self.api("POST", "pages", {
            "parent": {"type": "data_source_id", "data_source_id": self.data_source},
            "properties": {
                "Oppgave": {"title": [{"text": {"content": title}}]},
                "Status": {"status": {"name": "Innboks"}},
                "Klar for pipeline": {"checkbox": True},
                "Eier": {"people": [{"id": owner}]},
            },
            "children": [
                {"type": "heading_2", "heading_2": {"rich_text": [{"text": {"content": "Beskriv kort hva du vil endre"}}]}},
                *[paragraph(part) for part in description.split("\n\n") if part.strip()],
                paragraph(source),
            ],
        })
        return page["id"], page["url"]

    def update(self, page_id, properties):
        self.api("PATCH", f"pages/{page_id}", {"properties": properties})

    def move(self, page_id, status, **more):
        """Flytter oppgaven til en kolonne, og setter eventuelt flere egenskaper samtidig."""
        self.update(page_id, {"Status": {"status": {"name": status}}, **more})

    def set_blocker(self, page_id, text):
        self.update(page_id, {"Blokkeringsnotat": {"rich_text": [{"text": {"content": text}}] if text else []}})

    def comment(self, page_id, text):
        self.api("POST", "comments", {"parent": {"page_id": page_id}, "rich_text": [{"text": {"content": text[:2000]}}]})

    def markdown(self, page_id):
        return self.api("GET", f"pages/{page_id}/markdown").get("markdown", "")

    def check_schema(self):
        """Stopper hvis boardet mangler noe fra board.md, så ingen økt bruker tokens på det.
        Egenskapene bare refine.py bruker (MANAGED_PROPERTIES), lages hvis de mangler."""
        schema = self.api("GET", f"data_sources/{self.data_source}")["properties"]
        new = {name: kind for name, kind in MANAGED_PROPERTIES.items() if name not in schema}
        if new:
            try:
                self.api("PATCH", f"data_sources/{self.data_source}", {"properties": new})
                print(f"La til {', '.join(new)} på boardet. De brukes av refine.py til å koordinere instansene.")
                schema.update(new)
            except urllib.error.HTTPError as error:
                sys.exit(f"Boardet mangler {', '.join(new)}, og integrasjonen fikk ikke lagt dem til ({error.code}). "
                         "Legg dem til selv: Agent (tekst) og Slack-tråd (URL).")
        missing = [name for name in REQUIRED_PROPERTIES if name not in schema]
        statuses = {o["name"] for o in schema.get("Status", {}).get("status", {}).get("options", [])}
        missing += [f"status «{name}»" for name in REQUIRED_STATUSES if name not in statuses]
        if missing:
            sys.exit(f"Boardet mangler: {', '.join(missing)}. Legg dem til, eller oppdater board.md og refine.py.")

    def split_from(self, page_id):
        """Morens side-ID hvis oppgaven er delt ut fra en annen («Delt ut fra …» øverst)."""
        text = self.api("GET", f"pages/{page_id}/markdown").get("markdown", "")[:500]
        match = re.search(r'Delt ut fra <mention-page url="[^"]*?/([0-9a-f-]{32,36})"', text)
        return str(uuid.UUID(match.group(1))) if match else None

    def reject(self, page_id, ticket):
        """Fjerner haken og forklarer, når en oppgave er sendt inn uten nøyaktig én Eier."""
        self.api("PATCH", f"pages/{page_id}", {"properties": {"Klar for pipeline": {"checkbox": False}}})
        self.api("POST", "comments", {"parent": {"page_id": page_id}, "rich_text": [
            {"text": {"content": "🤖 Oppgaven trenger nøyaktig én Eier før den kan gå inn i pipelinen. "
                                 "Sett Eier, og huk av Klar for pipeline igjen. "}},
            {"type": "mention", "mention": {"user": {"id": ticket["creator"]}}}]})


class InlineWatcher:
    """Ser etter linjekommentarer på oppgaver som venter på svar, hvert 10. minutt, i en egen tråd.
    Den billige sjekken i hovedløkka ser bare sidekommentarer."""

    def __init__(self, board, every=600):
        import threading
        self.board, self.every = board, every
        self.waiting = set()    # oppgaver som venter på svar; settes av hovedløkka
        self.found = {}         # oppgave-ID → [(kommentar-ID, tekst)] for linjekommentarer
        self.wake = threading.Event()
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while True:
            time.sleep(self.every)
            for page_id in list(self.waiting):
                try:
                    comments = self.board.inline_comments(page_id)
                except NETWORK_ERRORS:
                    continue
                if comments and comments != self.found.get(page_id):
                    self.found[page_id] = comments
                    self.wake.set()
