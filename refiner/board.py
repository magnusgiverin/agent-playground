"""Leser og skriver boardet direkte via Notion API, uten Claude. Navnene må stemme med board.md."""

import json
import re
import sys
import urllib.error
import urllib.request
import uuid

from .config import CONFIG, env

INBOX = {"Innboks", None}  # tom status regnes som Innboks
DONE = {"Klar", "Under utvikling", "Til gjennomgang", "Fullført"}  # Klar eller lenger
NETWORK_ERRORS = (urllib.error.URLError, TimeoutError, KeyError, ValueError)
REQUIRED_PROPERTIES = ["Oppgave", "Status", "Etiketter", "Størrelse", "Figma-lenke", "Blokkeringsnotat",
                       "Blokkert av", "Blokkerer", "Klar for pipeline", "Eier"]
REQUIRED_STATUSES = ["Innboks", "Raffinering", "Trenger avklaring", "Klar"]


class Board:
    """Leser boardet direkte fra Notion API, uten Claude."""

    def __init__(self):
        self.key = env("NOTION_API_KEY")
        if not self.key:
            sys.exit("Sett NOTION_API_KEY i .env (se .env.example)")
        board = CONFIG["notion"]["board"]
        page = re.search(r"[0-9a-f]{32}", board.replace("-", "")).group(0)
        database = next(b["id"] for b in self.api("GET", f"blocks/{page}/children")["results"]
                        if b["type"] == "child_database")
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
        tickets = {}
        query = {"filter": {"or": [{"property": "Klar for pipeline", "checkbox": {"equals": True}},
                                   {"property": "Status", "status": {"equals": "Raffinering"}},
                                   {"property": "Status", "status": {"equals": "Klar"}}]}}
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
            tickets[page["id"]] = ticket
        return tickets

    def comments(self, page_id):
        """Alle sidekommentarene. «human» er sant for mennesker, også de som svarte fra Slack («💬 …»)."""
        comments = []
        for comment in self.pages(f"comments?block_id={page_id}&page_size=100"):
            text = "".join(t.get("plain_text", "") for t in comment["rich_text"])
            author = comment["created_by"]["id"]
            from_agent = author == self.bot and not text.startswith("💬")
            comments.append({"id": comment["id"], "author": author, "text": text,
                             "human": not from_agent and not text.startswith("🤖")})
        return comments

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

    def comment(self, page_id, text):
        self.api("POST", "comments", {"parent": {"page_id": page_id}, "rich_text": [{"text": {"content": text[:2000]}}]})

    def markdown(self, page_id):
        return self.api("GET", f"pages/{page_id}/markdown").get("markdown", "")

    def check_schema(self):
        """Stopper hvis boardet mangler noe fra board.md, så ingen økt bruker tokens på det."""
        schema = self.api("GET", f"data_sources/{self.data_source}")["properties"]
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
