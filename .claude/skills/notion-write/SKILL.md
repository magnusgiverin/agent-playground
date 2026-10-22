---
name: notion-write
description: Hvordan raffineringsskillene endrer noe i Notion. De bruker Notion API-integrasjonen (notion-api-MCP-serveren, autentisert med NOTION_API_KEY) til å flytte oppgaver mellom kolonner, sette egenskaper, skrive Raffinering-seksjonen, poste kommentarer og lage oppgaver, og verifiserer resultatet etterpå. Brukes for alle skrivinger fra /refine-board, /refine-ticket og /split-ticket.
---

# Skriv til Notion via API-integrasjonen

Les med claude.ai-koblingen til Notion (`notion-fetch`, `notion-query-data-sources`, `notion-get-comments`). Skriv bare med `notion-api`-MCP-serveren, som kaller Notion API med integrasjonsnøkkelen. Serveren laster nøkkelen fra `.env` selv. Les aldri `.env`, og skriv aldri ut nøkkelen. Bruk aldri skriveverktøyene i koblingen, og bruk aldri `API-update-a-data-source`, `API-create-a-data-source`, `API-delete-a-block` eller `API-move-page`.

All tekst du skriver til Notion, er på norsk (bokmål). Navn på egenskaper, statuser og tagger skrives nøyaktig som på boardet.

**ID-er:** API-et vil ha UUID-er. Ta de 32 heksadesimale tegnene fra en side-URL (`https://app.notion.com/p/<32 hex>`) eller fra `collection://<uuid>`. Brukere er UUID-ene fra `user://<uuid>`.

## Flytt en oppgave og sett egenskaper

`API-patch-page` med `page_id`, og `properties` som egenskapsverdier i Notion API-formatet. Send alle egenskapsendringene i ett steg i ett kall:

```json
{
  "Status": {"status": {"name": "Trenger avklaring"}},
  "Etiketter": {"multi_select": [{"name": "backend"}, {"name": "more information needed"}]},
  "Størrelse": {"select": {"name": "S"}},
  "Figma-lenke": {"url": "https://www.figma.com/design/…?node-id=1-23"},
  "Blokkeringsnotat": {"rich_text": [{"text": {"content": "Venter på S4 (se kommentarene)"}}]},
  "Eier": {"people": [{"id": "<bruker-uuid>"}]},
  "Blokkert av": {"relation": [{"id": "<side-uuid>"}]},
  "Klar for pipeline": {"checkbox": false},
  "Oppgave": {"title": [{"text": {"content": "Ny tittel"}}]}
}
```

Lister (`multi_select`, `people`, `relation`) erstatter hele verdien. For å tømme et felt bruker du `{"select": null}`, `{"url": null}` eller `{"rich_text": []}`.

## Skriv Raffinering-seksjonen

Bruk `API-update-page-markdown`. Bruk aldri `replace_content` på en eksisterende oppgave, fordi det skriver om forfatterens tekst.

- **Første runde, med malskjelett**: `type: "update_content"` med `old_str` = skjelettet nøyaktig slik `API-retrieve-page-markdown` viser det, fra `## 🤖 Raffinering` til og med plassholderen under `### Logg`, og `new_str` = den nye seksjonen (som starter med skillelinjen).
- **Første runde, uten skjelett**: `type: "insert_content"` og `insert_content: {"content": "<seksjonen som markdown>", "position": {"type": "end"}}`.
- **Senere runder**: `type: "update_content"` og `update_content: {"content_updates": [{"old_str": "<den eksisterende seksjonen, nøyaktig slik du leste den>", "new_str": "<den nye seksjonen>"}]}`. Les `old_str` med `API-retrieve-page-markdown` rett før du skriver, fordi markdownen fra API-et kan avvike litt fra koblingens. Kopier fra skillelinjen over `## 🤖 Raffinering` til slutten av toggelen `Øyeblikksbilde av originalteksten`.

## Kommenter

`API-create-a-comment` med `parent: {"page_id": "<uuid>"}` og `rich_text`, for eksempel:

```json
[
  {"text": {"content": "🤖 "}},
  {"text": {"content": "S4", "link": null}, "annotations": {"bold": true}},
  {"text": {"content": ": Skal lista pagineres? Uten svar antar jeg 20 elementer per side. "}},
  {"type": "mention", "mention": {"user": {"id": "<eierens uuid>"}}}
]
```

**Ingen svar i tråd.** Serveren krever `parent`, og API-et avviser `parent` sammen med `discussion_id` (400 «mutually exclusive»). Ikke prøv `discussion_id`. Post alltid en sidekommentar med `parent`. Notion samler sidekommentarene i samme tråd uansett. API-et kan heller ikke løse diskusjoner, så hopp over det. Løkka trenger det ikke, fordi en tråd der siste kommentar starter med `🤖` regnes som ventende.

Flere linjer i én kommentar lager du med `\n` inne i `content`. Én kommentar per runde med alle nye spørsmål er bedre enn én per spørsmål.

## Markdown via API-et

`API-update-page-markdown` og `API-retrieve-page-markdown` bruker Notion API sin markdown, som ikke er helt lik claude.ai-koblingens (`notion://docs/notion-flavored-markdown-spec`). Dette vet vi virker:

| Hva | Skriv slik via API-et | Ikke slik |
|---|---|---|
| Toggle | `<details><summary>Tittel</summary>innhold</details>` | `<toggle>`, som lagres som bokstavelig tekst |
| Lenke til en Notion-side | `<mention-page url="https://app.notion.com/p/…"/>` | `<mention url="…">` |
| Nevne en person | `<mention-user url="user://<uuid>"/>` | `@navn` |
| Figma-frame | `<embed source="https://www.figma.com/…?node-id=…">bildetekst</embed>` | |
| Gjennomstreking | ikke bruk | `~~tekst~~`, som blir `\~\~` ved neste skriving |

Er du i tvil, leser du siden med `API-retrieve-page-markdown` og kopierer syntaksen derfra. Etter en skriving leser du den igjen og retter det med en gang hvis noe ble lagret som bokstavelig tekst.

## Lag en oppgave

1. `API-post-page` med `parent: {"type": "data_source_id", "data_source_id": "<boardets datakilde-uuid>"}` og `properties` i samme format som over. API-et bruker ikke boardets mal.
2. `API-update-page-markdown` på den nye siden, med `type: "replace_content"` og `replace_content: {"new_str": "<innholdet i oppgaven>"}`. Det er trygt her, fordi siden er ny og tom.
3. Bruk `id` og `url` fra steg 1 til relasjoner og mentions.

## Feil

- **404**: siden er ikke delt med integrasjonen. Stopp og si at integrasjonen må legges til under **Connections** på boardsiden.
- **403**: integrasjonen mangler en tilgang (update content, insert comments, read comments). Stopp og si hvilken.
- **400**: rett opp forespørselen én gang, for eksempel et feil egenskapsnavn eller valg, og prøv igjen. Feiler den igjen, rapporterer du det.
- **429**: vent litt og prøv igjen.

## Verifiser

Etter at du har skrevet, `notion-fetch`-er du oppgaven og sjekker:

- at egenskapene har verdiene du satte
- at Raffinering-seksjonen står slik du skrev den
- **at alt over Raffinering-skillelinjen er uendret.** Er det endret, poster du en kommentar om hva som endret seg, og stopper arbeidet med den oppgaven.
