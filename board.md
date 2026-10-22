# Oppsett av boardet

Raffineringsskillene leser denne fila først. Endrer boardet seg, endrer du verdiene her. Skillene sjekker boardets skjema ved hver kjøring og stopper hvis noe herfra mangler.

**Språk:** alt agenten skriver er på norsk (bokmål). Det gjelder Raffinering-seksjonen, kommentarer, Blokkeringsnotat, loggen, nye oppgaver og fortellingen i terminalen. Boardet bruker norske navn på egenskaper og statuser, og agenten bruker nøyaktig de navnene (`Trenger avklaring`, `Blokkert av`, `Klar for pipeline` og så videre), fordi de må matche. Etikettvalgene på boardet er fortsatt engelske (`frontend`, `more information needed` …), så de skrives som de står.

## Boardet

Innstillingene ligger i [refiner.toml](refiner.toml): boardsiden (`[notion] board`), koden (`[code]`) og designfila (`[design] figma`). Les den i starten av hver runde. Hent boardsiden og bruk datakilden til oppgavedatabasen på den.

**Les aldri `.env`.** Den inneholder bare Notion API-nøkkelen, som notion-api-MCP-serveren laster selv (se [notion-write](.claude/skills/notion-write/SKILL.md)).

`/refine-board` starter med `/sync-code`, som gir deg **kodemappa** og, for GitHub, repo-URL og branch (se [sources.md](sources.md)). Send dem videre som `code=<mappe> github=<url>@<branch>` til alle skills du kaller. De kalles **kodeargumentene**.

## Egenskaper

| Rolle | Egenskap | Type | Agenten |
|---|---|---|---|
| Tittel | Oppgave | title | kan gjøre den tydeligere |
| Status | Status | status | setter den |
| Tagger | Etiketter | multi_select | setter den fra de eksisterende valgene |
| Størrelse | Størrelse | select: XS, S, M, L, XL | setter den |
| Figma | Figma-lenke | url | setter hovedframen |
| Venter på | Blokkeringsnotat | text | oppsummering av åpne spørsmål, tømmes ved Klar |
| Avhenger av | Blokkert av / Blokkerer | relation | kobler oppdelte oppgaver og avhengigheter den finner |
| Port | Klar for pipeline | checkbox | bare avhukede oppgaver går inn i pipelinen, uten hake er oppgaven satt på pause |
| Eier | Eier | person | nøyaktig én person, påkrevd for å komme inn, låst når pipelinen har startet, får spørsmålene |
| Ansvarlig | Utfører | person | setter den aldri |
| Prioritet | Prioritet | select | endrer den aldri |

## Statuser

| Rolle | Status | Folk kan redigere |
|---|---|---|
| Ny, ikke rørt av agenten | Innboks (eller tom status) | ja |
| Agenten jobber med den | Raffinering | nei, bare kommentarer |
| Venter på at eieren svarer på spørsmål | Trenger avklaring | ja |
| Entydig og klar til å bygges | Klar | nei, bare kommentarer (å flytte den til Under utvikling er lov) |

Statusene etter Klar (Under utvikling, Til gjennomgang, Fullført) er utenfor agentens ansvar. Der gjelder bare eierlåsen.

Tagg for oppgaver som venter på svar: `more information needed`.

## Malen

Nye oppgaver lages fra malen «Utviklingsoppgave». Den har overskriften `## Beskriv kort hva du vil endre`, der forfatteren skriver, og under den et tomt **malskjelett** av `## 🤖 Raffinering` med plassholdere (Problem, Akseptansekriterier, Omfang, Antakelser, Åpne spørsmål, Logg). Skjelettet kjennes igjen ved at det mangler toggelen `Øyeblikksbilde av originalteksten` og Tilstand-linjen i Logg. Se refine-ticket for hvordan det erstattes.

## Regler

- En oppgave går inn i pipelinen når `Klar for pipeline` er huket av **og** `Eier` er nøyaktig én person. Er boksen huket av uten en gyldig eier, fjerner agenten haken og sier hvorfor.
- Eierlåsen er agentens kommentar `🤖 Pipeline startet. Eier: …`. Hvis `Eier` senere er en annen, setter agenten den tilbake og kommenterer.
- Skrivebeskyttelsen håndheves av agenten, ikke av Notion. Ved hver runde ruller den tilbake endringer folk gjør i status og egenskaper på oppgaver i Raffinering og Klar, ut fra Tilstand i siste logglinje. Endringer i brødteksten kan den ikke angre, så dem kommenterer den bare.
- Størrelser som aldri er klare, så oppgaven deles opp: L, XL
- Spørsmålsrunder før oppgaven gis til et menneske: 5
- Oppgaver per runde: 5
