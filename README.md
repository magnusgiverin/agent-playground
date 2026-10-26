# Raffinerings- og utviklingsagent

Gjør grove Notion-oppgaver om til spesifikasjoner en utvikler kan bygge uten å gjette, og bygger dem. Den jobber i en løkke med oppgavens eier til ingenting er uklart, tegner skisser i Figma, bygger på en egen branch, lar en egen agent gjennomgå endringen og fører den inn i main. Den er bygd av Claude Code-skills og en liten Python-løkke. Alt den skriver, er på norsk.

```
Innboks ──(Eier + «Klar for pipeline»)──▶ Raffinering ─▶ Trenger avklaring ⟲ (eieren svarer i Notion eller Slack)
                                              │     visuell endring → skisse i Figma («🤖 Skisser») → må godkjennes
                                              ▼
                                            Klar ──▶ Under utvikling ──(alt grønt)──▶ Til gjennomgang ──(GODKJENT)──▶ main ──▶ Fullført
                                                     (egen branch)   ◀──(ENDRINGER: agenten fikser)──┘
```

Samme kode kan kobles til hvilket som helst board, repo, Figma-fil og Slack-kanal, og flere PC-er kan kjøre den mot de samme systemene samtidig. Se [Koble til dine systemer](#koble-til-dine-systemer) og [Flere instanser](#flere-instanser).

## Koble til dine systemer

Alt som er unikt for et oppsett, er **miljøvariabler**. De leses fra miljøet først, og så fra `.env` (eller fila du velger med `--env`). [refiner.toml](refiner.toml) har bare oppførselen (modeller, parallelitet, gjennomgang …), som er lik for alle og kan committes. `.env` committes aldri.

| Variabel | Påkrevd | Hva |
|---|---|---|
| `NOTION_API_KEY` | ja | Nøkkel til en Notion-integrasjon (hemmelig) |
| `NOTION_BOARD` | ja | URL til Notion-siden med oppgaveboardet |
| `GITHUB_REPO` | ja* | URL til produktets repo. Git bruker din egen innlogging, ingen nøkkel |
| `GITHUB_BRANCH` | | Branchen det bygges mot (standard `main`) |
| `CODE_LOCAL` | ja* | Les en lokal mappe i stedet for GitHub (bare raffinering) |
| `FIGMA_FILE` | | Produktets Figma-fil. Agenten blar i den og tegner skisser der |
| `FIGMA_TOKEN` | | Personlig Figma-nøkkel, så frames postes som bilder i Slack (hemmelig) |
| `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN` | | Slack-appen (hemmelige). Uten dem kjører den uten Slack |
| `SLACK_CHANNEL` | med Slack | Kanal-ID-en broen bruker |
| `SLACK_TEST_OWNER` | | Bare for testing: Notion-bruker-ID som blir Eier på alt fra `/new` |
| `INSTANCE_NAME` | | Navnet andre instanser ser. Standard er PC-navnet |

\* Én av `GITHUB_REPO` og `CODE_LOCAL`.

### Steg for steg

1. **Claude Code.** Installer [Claude Code](https://code.claude.com) og logg inn med claude.ai-kontoen som har Figma-koblingen (og Notion-koblingen, som brukes til lesing).
2. **Env-fila.** `cp .env.example .env` og fyll inn verdiene under. Sjekk når som helst med `python3 refine.py --oppsett`, som viser hva instansen er koblet til, uten å vise hemmelighetene.
3. **Notion.**
   - Lag en integrasjon på [notion.so/profile/integrations](https://www.notion.so/profile/integrations) med *read content*, *update content*, *insert content*, *read comments*, *insert comments* og *read user information including email addresses*. Nøkkelen er `NOTION_API_KEY`.
   - Legg integrasjonen til på boardsiden: **••• → Connections**. Flyttes siden senere, må den kanskje legges til på nytt (ellers 404).
   - `NOTION_BOARD` er URL-en til siden med boardet. Boardet må ha egenskapene og statusene i [board.md](board.md). refine.py sjekker det ved oppstart og sier nøyaktig hva som mangler. Egenskapene `Agent` og `Slack-tråd`, som refine.py bruker til å koordinere, lages av seg selv.
4. **GitHub.** Sett `GITHUB_REPO` og eventuelt `GITHUB_BRANCH`. Sjekk at `git clone <repo>` virker i terminalen, for refine.py bruker den samme innloggingen. Den må kunne pushe til branchen, fordi godkjente endringer føres rett inn i den.
5. **Figma (valgfritt).** Sett `FIGMA_FILE` til produktets fil. Claude Code-kontoen din må kunne redigere den, for skisser tegnes på siden «🤖 Skisser». Skru av skisser med `sketches = false` under `[design]` i refiner.toml.
6. **Slack (valgfritt).** Se [Slack](#slack) under.
7. **Start:** `python3 refine.py`.

## Flere instanser

Flere PC-er kan kjøre refine.py mot det samme boardet, repoet og Figma-fila samtidig. De ser hverandre og fordeler oppgavene mellom seg. Stopper én, tar de andre over der den slapp.

- **Låser i Notion.** Før en agent starter, skriver instansen seg inn i egenskapen `Agent`, for eksempel `magnus-mbp · jobber · 2026-10-26T12:10:00Z · a1b2c3`. Den venter så noen sekunder og leser igjen, og bare den som står der da, får oppgaven. Mens agenten jobber, fornyes låsen hvert 3. minutt. Andre instanser rører ikke en oppgave med en levende lås.
- **Krasj og lukket lokk.** Fornyes ikke låsen, utløper den etter `lease_minutes` (standard 10), og en annen instans tar over. Stopper du med Ctrl-C (eller lukker terminalen), stopper agentene, og oppgavene merkes `ledig`, så andre tar over med en gang.
- **Hjemmet får første sjanse.** Når en oppgave hviler (venter på svar eller utvikling), står den fortsatt som instansens (`hviler`). Den instansen har økta med alt som er lest, så den svarer billigst. Har den ikke tatt svaret innen `takeover_minutes` (standard 5), tar en annen instans over og leser alt fra Notion.
- **Agendaen leses fra boardet.** Hver kolonne har én ting som skal skje når ingen agent jobber på oppgaven, og den leses fra Notion, ikke fra minnet til én PC ([refiner/scheduler.py](refiner/scheduler.py)). Ingen oppgave blir derfor liggende fordi en instans stoppet midt i:

  | Kolonne | Når ingen agent jobber på den |
  |---|---|
  | Innboks (Klar for pipeline) | raffiner, eller fjern haken hvis den ikke har nøyaktig én Eier |
  | Raffinering | fortsett raffineringen |
  | Trenger avklaring | vent; fortsett når et menneske svarer |
  | Klar | bygg, når alt den er Blokkert av, er Fullført |
  | Under utvikling | fortsett utviklingen fra branchen på GitHub (eller vent hvis den har et Blokkeringsnotat) |
  | Til gjennomgang | gjennomgå og før inn i main |
  | paraplyer | Klar når alle delene er Klar, Fullført når alle er Fullført |

- **Branchen deles.** Etter hver utviklingsrunde, og mens agenten jobber, pusher refine.py oppgavens branch (`refiner/<tittel>-<id>`) til GitHub. En annen instans som tar over, fortsetter fra de commitene. Branchen slettes når oppgaven er i main.
- **Main tåler at flere pusher.** Har en annen instans pushet til main i mellomtiden, avvises pushen. Da legges branchen oppå på nytt og pushes igjen. Gir det konflikt, går oppgaven tilbake til Under utvikling, og agenten løser konflikten.
- **Felles teller for rydding.** Hver commit som går inn i main, får en trailer (`Refiner-oppgave: <Notion-URL>`), så alle instansene teller oppgavene siden siste rydding fra main. Bare én instans rydder om gangen (en lås-ref på GitHub, `refiner/laas-rydding`).
- **Slack.** Tråden står i `Slack-tråd` i Notion, og bare instansen som har oppgaven, speiler til den. Hver melding broen poster, har metadata om hva den speiler, så en instans som tar over, ser hva som allerede står i tråden. Svar i tråden kan tas imot av hvilken som helst instans, fordi de bare blir kommentarer i Notion.
- **Agentene vet om hverandre.** Hver økt får vite hvilke andre oppgaver som er i arbeid og hvor, og reglene i [sources.md](sources.md) sier at de ikke skal røre andres oppgaver eller brancher.
- **Flere instanser på én PC.** Bruk én env-fil per instans: `python3 refine.py --env .env.kunde-a`. Hver får sin egen tilstand (`.cache/kunde-a/`) og sine egne arbeidskopier.

Det som bare finnes lokalt, er Claude-øktene (`~/.claude/projects`) og `.cache/sessions.json` (hvilken økt hører til hvilken oppgave, og kostnaden). Mistes de, starter oppgaven på nytt fra det som står i Notion og på GitHub.

## Slik fungerer det

- **Port:** en oppgave kommer inn bare når `Klar for pipeline` er huket av og `Eier` er nøyaktig én person. Er boksen huket av uten eier, fjerner agenten haken og sier hvorfor.
- **Eierlås:** når pipelinen har startet, kan eieren ikke endres. Agenten setter den tilbake hvis noen endrer den.
- **Forfatterens tekst røres aldri.** Agenten skriver bare sin egen `🤖 Raffinering`-seksjon nederst på siden, og erstatter den seksjonen i senere runder.
- **Spørsmål er kommentarer.** Agenten samler nye spørsmål i én kommentar per runde (`🤖 S3: … Uten svar antar jeg …`). Svar nederst på siden eller i Slack-tråden. Det plukkes opp innen 20 sekunder. Kommentarer på enkeltlinjer sjekkes hvert 10. minutt. Alt agenten skriver, starter med 🤖.
- **Skrivebeskyttede kolonner:** folk redigerer i Innboks og Trenger avklaring. Resten er bare for kommentarer. Agenten ruller tilbake endringer i status og egenskaper der.
- **Én økt per oppgave:** når eieren svarer, fortsetter løkka **den samme økta** (`--resume`), med alt den har lest fra før. Oppgaver som deles opp, får hver sin økt med morens kontekst (`--fork-session`). Mens den venter, kjører ingenting, så det koster ingen tokens.
- **Flere agenter samtidig:** opptil `parallel` agenter (under `[agent]` i refiner.toml) per instans. Hver linje i terminalen er merket med oppgaven.
- **Skisser:** ved visuelle endringer uten et godkjent design tegner raffineringsagenten en skisse på siden «🤖 Skisser» ([/sketch-figma](.claude/skills/sketch-figma/SKILL.md)). Oppgaven blir ikke Klar før eieren har godkjent den.
- **Utvikling:** Klar-oppgaver bygges av [/develop-ticket](.claude/skills/develop-ticket/SKILL.md) i en egen klone i `.cache/work/`, på en egen branch. Agenten kan committe, slette og flytte filer (`git -C … rm|mv`), men aldri pushe. Når alt er grønt, skriver den en rapport.
- **Gjennomgang før main:** oppgaven flyttes til **Til gjennomgang**, og en egen agent ([/review-change](.claude/skills/review-change/SKILL.md)) gjennomgår endringen i en ny økt. Bare `GODKJENT` slipper inn i main. Ved `ENDRINGER` går oppgaven tilbake til Under utvikling mens utviklingsøkta fikser funnene. Etter `review_rounds` runder uten godkjenning går den til eieren.
- **Inn i main:** Python legger branchen oppå siste main og pusher. Oppgaven blir Fullført, `Pull request` får lenken til commiten, og rapporten blir en kommentar.
- **Agentnotater:** designvalg og advarsler skrives i `docs/agents/` i produktrepoet, og ryddes av [/tidy-notes](.claude/skills/tidy-notes/SKILL.md) etter hver `tidy_every`-te oppgave i main.
- **Feil og omforsøk:** feiler en økt, venter instansen 1, 5 og så 15 minutter. Etter tre feil på rad merkes oppgaven `feilet` i `Agent`, og eieren får en kommentar. Ingen instans prøver igjen før et menneske kommenterer eller endrer oppgaven.
- **Kostnad:** Sonnet 5.5 med `effort = "medium"` som standard. Hvert kall skrives til `.cache/runs.jsonl`, og summen per oppgave står i `.cache/sessions.json`.
- **Flere antakelser:** med `--antakelser` spør agentene bare om det som er absolutt kritisk, og antar resten.

## Slack

Med Slack kan du lage oppgaver med `/new <beskrivelse>` og snakke med oppgaven i en tråd. Det du svarer i tråden, blir en kommentar på oppgaven i Notion. Broen er [refiner/slack.py](refiner/slack.py), og den bruker ingen tokens. Flere instanser kan dele den samme Slack-appen.

1. **Lag appen.** [api.slack.com/apps](https://api.slack.com/apps) → *Create New App* → *From an app manifest*, og lim inn [slack-manifest.yml](slack-manifest.yml).
2. **Installer den.** *Install App* → *Install to Workspace*. *Bot User OAuth Token* (`xoxb-…`) er `SLACK_BOT_TOKEN`.
3. **App-nøkkel for Socket Mode.** *Basic Information* → *App-Level Tokens*, med scopen `connections:write`. Nøkkelen (`xapp-…`) er `SLACK_APP_TOKEN`.
4. **Kanalen.** Inviter boten med `/invite @Raffineringsagent`, og sett kanal-ID-en (nederst i kanalens detaljer) som `SLACK_CHANNEL`.
5. **Eier fra e-post.** `/new` finner Eier ut fra e-posten i Slack, så den må være lik i Notion (integrasjonen må kunne lese e-post).
6. **Slack-biblioteket:** `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`. refine.py starter seg selv i `.venv` når den finnes.

## Bruk

```sh
python3 refine.py                     # start løkka, og la den stå (Ctrl-C stopper og slipper oppgavene)
python3 refine.py --oppsett           # vis hva instansen er koblet til
python3 refine.py --env .env.kunde-a  # en instans til, med et annet oppsett
python3 refine.py --verbose           # skriv også ut hele inndata og svar for hvert verktøykall
python3 refine.py --antakelser        # agentene antar mer og spør bare om det som er absolutt kritisk

# eller interaktivt
claude
/refine-board              # én runde, med hvert steg synlig
```

For å prøve: lag en oppgave, sett **Eier** og huk av **Klar for pipeline**.

## Videreutvikling

Python-koden gjør alt som kan gjøres uten en språkmodell (lese boardet, låser, git, Slack). Claude gjør bare det som krever vurdering, styrt av skillene. Hold den delingen.

| Fil | Ansvar |
|---|---|
| [refine.py](refine.py) | Inngangen: argumenter, hovedløkka, oppstart og ryddig stopp |
| [refiner/config.py](refiner/config.py) | Oppførsel fra refiner.toml, oppsett for instansen fra miljøvariablene (`INSTANCE`, `ENV_VARS`), og regelfilene i systemprompten |
| [refiner/board.py](refiner/board.py) | Notion API: snapshot av boardet, kommentarer, egenskaper. Kolonnenavnene står øverst |
| [refiner/scheduler.py](refiner/scheduler.py) | Hva som skal skje neste, per kolonne. Ingen Claude, bare regler |
| [refiner/lease.py](refiner/lease.py) | Låsene i `Agent`, som lar flere instanser dele boardet |
| [refiner/runner.py](refiner/runner.py) | Kjører én jobb: tar låsen, bygger meldingen til agenten, starter eller fortsetter riktig økt |
| [refiner/shipping.py](refiner/shipping.py) | Gjennomgang, rettelser og push til main, og ryddingen i agentnotatene |
| [refiner/code.py](refiner/code.py) | Git: lesekopien, arbeidskopier, delte brancher, push med nytt forsøk |
| [refiner/claude.py](refiner/claude.py) | Starter `claude -p`, verktøytilgangene per økttype, og visningen av hvert steg |
| [refiner/slack.py](refiner/slack.py) | Slack-broen |
| [refiner/sessions.py](refiner/sessions.py) | Lokal tilstand: økter og kostnad |
| [.claude/skills/](.claude/skills/) | Hva agentene gjør: refine-ticket, split-ticket, sketch-figma, develop-ticket, review-change, tidy-notes, notion-write, sync-code |
| [board.md](board.md), [sources.md](sources.md) | Regler som lastes inn i hver økt: boardets navn og regler, og hvordan kode, Figma, nettet og andre agenter håndteres |
| [.claude/settings.json](.claude/settings.json) | Verktøy alle økter får (og aldri får). Ekstra verktøy per økttype står i `MODES` i claude.py |

Vanlige endringer:

- **Ny innstilling for alle:** legg den i refiner.toml og les den med `AGENT.get(...)` der den brukes.
- **Nytt oppsett per instans** (et nytt system å koble til): legg den til i `ENV_VARS` og `Instance` i config.py, og i .env.example og tabellen over. Skal agenten se den, kommer den med i `Instance.describe()`.
- **Ny kolonne eller ny jobb:** legg kolonnenavnet øverst i board.py og i board.md, og en regel i `next_jobs` i scheduler.py. Jobber uten Claude legges i `PYTHON_KINDS` og `python_job` i runner.py. Jobber med Claude får en melding i `claude_job`. Sjekk at kolonnen har en regel for når ingen agent jobber på den, så ingenting blir liggende.
- **Nytt verktøy for agenten:** i `MODES` i claude.py (bare én økttype) eller i .claude/settings.json (alle). Agentene kjører uten noen som kan godkjenne, så alt som ikke er tillatt, avvises.
- **Endre hva agenten gjør:** endre skillen, ikke Python-koden.
