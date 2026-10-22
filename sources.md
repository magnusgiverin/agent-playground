# Kode og design

Raffineringsagenten leser produktets kode og design, og endrer aldri noen av dem. Hvor de ligger, står i [refiner.toml](refiner.toml): `[code] github` (eller `local`) og `[design] figma`.

## Kode

Skillen [sync-code](.claude/skills/sync-code/SKILL.md) holder en lokal kopi oppdatert før hver runde: en grunn klone av GitHub-repoet i `.cache/code/<reponavn>`, laget med brukerens egen git-innlogging, eller den lokale mappa (`local`). Du får den som `code=<mappe>`, og i tillegg `github=<repo-url>@<branch>` når den kom fra GitHub.

- Søk **bare** med verktøyene Glob og Grep, og sett alltid `path` til kodemappa (for eksempel `path: ".cache/code/hellas-grill"`). `.cache/` er gitignored i dette prosjektet, så et søk uten `path` finner ingenting i koden. Bruk aldri Bash til å lete i koden (`ls`, `grep`, `cat`, `sed`). Bash er bare tillatt for git-kommandoene i sync-code, og alt annet blir avvist.
- List mapper med Glob (`pattern: "**/*"`, eller `src/*` for én mappe). Read virker bare på filer, ikke på mapper. Store filer leser du i biter med `offset` og `limit`, gjerne etter at Grep har funnet linjenumrene.
- Start med README, CLAUDE.md eller AGENTS.md og mappestrukturen for å forstå hvordan prosjektet er bygd opp.
- Finn filene oppgaven berører, sjekk om noe lignende finnes allerede, og bekreft de faktiske navnene (komponenter, ruter, endepunkter, felter, tekster).
- List bare stier du faktisk har åpnet. Skriv dem relativt til roten av repoet. Er `github=` oppgitt, lenker du hver av dem som `<repo-url>/blob/<branch>/<sti>`.
- Kopien er en grunn klone av én branch, så den har ingen historikk og ingen andre brancher. Ikke gjett om åpne pull requests. Spør hvis det betyr noe.

## Design (Figma)

Les Figma via claude.ai-koblingen til Figma. Følg alle Figma-lenker du finner: i oppgaveteksten, i `Figma-lenke` og `Referanselenke`, i kommentarer, i lenkede Notion-sider og i overordnede oppgaver.

- **Tolk URL-en** slik instruksjonene til Figma-serveren beskriver: `figma.com/design/<fileKey>/…?node-id=1-23` (bytt `-` med `:`), `/design/<fileKey>/branch/<branchKey>/…` (bruk branchKey), `/board/…` (FigJam: bruk `get_figjam`), `/proto/<fileKey>/…?node-id=…` (en prototype: behandle den som design med den noden) og `/make/…`.
- **En lenke til en frame**: `get_screenshot` for å se den, og `get_metadata` for struktur, navn og størrelse.
- **En lenke til en side, en seksjon eller en hel fil**: `get_metadata` for å liste framene, og åpne så de med navn som passer til oppgaven. Passer flere (for eksempel mobil og desktop, eller flere tilstander), åpner du alle. Ser du ikke hvilken oppgaven mener, spør du.
- **Ingen lenke, men UI-arbeid**: er `[design] figma` satt i refiner.toml, blar du i den fila på samme måte og ser etter frames som passer til oppgaven (skjermnavn, komponentnavn, tekstene oppgaven nevner). Finner du en tydelig match, bruker du den og skriver det under Antakelser. Finner du ingen, eller er `figma` tom, ber du om en lenke.
- **Varianter og tilstander**: se i metadataene etter frames eller varianter med navn som loading, empty, error, hover, disabled, mobile eller desktop. Det som finnes, står under Design. Det som mangler, er en tilstand som «ikke er designet».
- Kall aldri `get_design_context` eller verktøy som skriver til Figma. Du spesifiserer arbeidet, du bygger det ikke.

**Vis designet i oppgaven.** Når endringen er frontend (UI-tagger, UI-filer i omfanget eller en designlenke), legger du hver frame du brukte under Design i Raffinering-seksjonen som en levende embed, så utvikleren ser den uten å forlate Notion:

```
<embed source="https://www.figma.com/design/<fileKey>/<name>?node-id=1-23">Meny / Mobil: det nye prisoppsettet</embed>
```

Bruk URL-en med `node-id`, så embedden viser den framen og ikke hele fila. Sett oppgavens `Figma-lenke` til hovedframen.
