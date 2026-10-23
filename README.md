# Raffineringsagent

Gjør grove Notion-oppgaver om til spesifikasjoner en utvikler kan bygge uten å gjette. Den jobber i en løkke med oppgavens eier til ingenting er uklart. Den er bygd av Claude Code-skills som leser Notion og Figma via claude.ai-koblingene, og skriver til Notion via Notion API. Den leser produktkoden, men endrer den aldri. Alt den skriver, er på norsk.

```
Innboks ──(Eier + «Klar for pipeline»)──▶ Raffinering ─▶ Trenger avklaring ⟲ (eieren svarer i Notion eller Slack)
                                                 │      visuell endring → skisse i Figma («🤖 Skisser») → må godkjennes
                                                 ▼
                                               Klar ──(automatisk)──▶ Under utvikling (egen branch) ──(alt grønt)──▶ PR åpnes ──▶ Til gjennomgang ──(godkjent)──▶ merges i main ──▶ Fullført
                                                          egen branch, bygg og tester grønne     review-kommentarer → agenten fikser
```

## Slik fungerer det

| Fil | Jobb |
|---|---|
| [refine.py](refine.py) | Inngangen: løkka som venter på boardet og starter, fortsetter eller forker én Claude-økt per oppgave |
| [refiner/](refiner/) | Koden bak løkka: `board.py` (Notion API), `jobs.py` (hva som skal skje neste, og meldingen til agenten), `claude.py` (starte en økt og vise den), `sessions.py` (`.cache/sessions.json`), `code.py` (git-synk), `github.py` (gh: åpne, følge og merge PR-er), `review.py` (følger PR-ene til de er merget), `config.py` (refiner.toml og regelfilene i systemprompten), `display.py` (terminalvisningen) |
| [refiner.toml](refiner.toml) | Innstillinger: boardsiden, GitHub-repo og branch, Figma-fila, og hva som skjer når utviklingen er ferdig (`[merge]`) |
| [board.md](board.md) | Navn på egenskaper og statuser, hvem som kan redigere hva, og grenser |
| [/sync-code](.claude/skills/sync-code/SKILL.md) | Kloner eller oppdaterer en skrivebeskyttet kopi av GitHub-repoet i `.cache/code` med din git-innlogging (eller bruker en lokal mappe) |
| [/refine-board](.claude/skills/refine-board/SKILL.md) | Én runde: vokter skrivebeskyttede oppgaver, slipper inn nye, raffinerer nye og besvarte oppgaver, og lukker paraplyer |
| [/refine-ticket](.claude/skills/refine-ticket/SKILL.md) | Leser oppgaven, kommentarene, Figma-framene og koden. Skriver `🤖 Raffinering`-seksjonen, setter tagger, størrelse, Figma-lenke og status, og stiller spørsmål som kommentarer |
| [/split-ticket](.claude/skills/split-ticket/SKILL.md) | Deler en L- eller XL-oppgave opp i 2 til 6 oppgaver med én endring hver |
| [notion-write](.claude/skills/notion-write/SKILL.md) | Hvordan hver endring i Notion gjøres via API-et: flytte oppgaver mellom kolonner, egenskaper, Raffinering-seksjonen, kommentarer og nye oppgaver |
| [sources.md](sources.md) | Hvordan agenten leser koden, følger Figma-lenker og bygger inn frames i oppgaver |
| [.claude/settings.json](.claude/settings.json) | Alle verktøyene agenten får bruke, også de fire nøyaktige git-kommandoene. Alt annet avvises når den kjører uten innblanding |
| [.mcp.json](.mcp.json) | Kjører Notions offisielle MCP-server (`@notionhq/notion-mcp-server`) med `NOTION_API_KEY` fra `.env` |

- **Port:** en oppgave kommer inn bare når `Klar for pipeline` er huket av og `Eier` er nøyaktig én person. Er boksen huket av uten eier, fjerner agenten haken og sier hvorfor.
- **Eierlås:** når pipelinen har startet, kan eieren ikke endres. Agenten setter den tilbake hvis noen endrer den.
- **Forfatterens tekst røres aldri.** Agenten skriver bare sin egen `🤖 Raffinering`-seksjon nederst på siden, og erstatter den seksjonen i senere runder.
- **Spørsmål er kommentarer.** Alle sidekommentarer havner i samme tråd i Notion (API-et kan ikke svare i en egen tråd), så agenten samler nye spørsmål i én kommentar per runde (`🤖 S3: … Uten svar antar jeg …`), og svarer i én kommentar (`🤖 Svar på S1–S3: …`). Svar nederst på siden eller i Slack-tråden. Det plukkes opp innen 20 sekunder. Kommentarer på enkeltlinjer i teksten virker også, men sjekkes bare hvert 10. minutt, fordi API-et må spørres blokk for blokk. Alt agenten skriver, starter med 🤖.
- **Skrivebeskyttede kolonner:** folk redigerer i Innboks og Trenger avklaring. Raffinering og Klar er bare for kommentarer. Notion kan ikke styre tilgang etter status, så agenten ruller tilbake endringer i status og egenskaper der ved hver runde. Endringer i brødteksten kan den bare flagge, ikke angre.
- **Én økt per oppgave:** `python3 refine.py` holder seg oppe og spinner. Den sjekker boardet hvert 20. sekund via Notion API, uten Claude og nesten gratis. Når en oppgave i Innboks har Klar for pipeline og én Eier, starter den en Claude-økt for den oppgaven. Økta leser Notion, Figma og koden, og stopper av seg selv når oppgaven er **Klar** (da er økta ferdig) eller **Trenger avklaring**. Mens den venter på svar, kjører ingenting, så det koster ingen tokens. Når eieren kommenterer eller endrer oppgaven, fortsetter løkka **den samme økta** (`--resume`), med alt den har lest fra før. Den trenger altså ikke å lese Figma og koden på nytt. Oppgaver som deles opp, får hver sin økt, som starter med morens kontekst (`--fork-session`). Øktene ligger i `.cache/sessions.json`, så de overlever at du stopper og starter refine.py.
- **Flere agenter samtidig:** hver oppgave får sin egen agent, og opptil `parallel` (standard 3, under `[agent]` i refiner.toml) jobber samtidig. Løkka fortsetter å sjekke boardet og speile Slack mens de jobber, og starter neste oppgave så snart en agent er ferdig. Hver linje i terminalen er merket med oppgaven, for eksempel `[Del 2] …`. Samme oppgave får aldri to agenter samtidig, og en del fra en oppdeling venter til morens økt er ferdig, så den får med seg hele konteksten.
- **Billig i drift:** økta kjører på Sonnet 5.5 med `effort = "medium"` (endres i `[agent]` i refiner.toml). Regelfilene sendes i den bufrede systemprompten. Kodesynken, sjekken av boardet, egenskapene og kommentarene gjør Python, så agenten ikke bruker steg på det. En økt som skal hvile, komprimeres med `/compact` bare når den er stor (`compact_at`, standard 60 000 tokens per steg). Ellers koster komprimeringen mer enn den sparer.
- **Kostnaden måles per kall:** Claude Code oppgir hva *hele økta* har kostet, også ved fortsettelser og forker. refine.py trekker derfor fra det økta hadde kostet før, så hvert kall bare telles én gang. Hvert kall skrives til `.cache/runs.jsonl` (oppgave, slags kall, økt, kostnad, øktas total, steg, sekunder og tokens per steg), og summen per oppgave står i `.cache/sessions.json`. Tall fra før denne rettelsen var for høye, fordi totalene ble lagt sammen. De ligger nå i `cost_before_fix`.
- **Feil og omforsøk:** feiler en økt, venter løkka 1, 5 og så 15 minutter før neste forsøk. Etter tre feil på rad lar den oppgaven være til et menneske kommenterer eller endrer den. Bare én refine.py kan kjøre om gangen (`.cache/refine.lock`), så to prosesser kan ikke ta samme oppgave.
- **Språk:** alt agenten skriver i Notion og i terminalen, er på norsk (bokmål). Navn på statuser, egenskaper og tagger er som på boardet.

## Fra idé til PR

- **Lese design og lage design er to ulike ting:** raffineringen *leser* Figma (`get_metadata`, `get_screenshot`) for å forstå skjermene, og *tegner* bare skisser på siden «🤖 Skisser» via /sketch-figma. Utviklingen *leser* Figma i detalj (`get_design_context`) for å bygge etter den godkjente skissen. Den tegner aldri i Figma.
- **Skisser:** når en oppgave endrer noe visuelt og det ikke finnes et godkjent design, tegner raffineringsagenten en skisse i Figma-fila fra refiner.toml, på siden «🤖 Skisser» ([/sketch-figma](.claude/skills/sketch-figma/SKILL.md)). Skissen legges inn i oppgaven og i Slack-tråden, og oppgaven blir ikke Klar før eieren har godkjent den. Ber eieren om endringer, kommer en ny versjon (v2, v3 …) ved siden av den forrige. Skru av med `sketches = false` under `[design]`.
- **Utvikling:** så snart en oppgave som løkka har raffinert, blir Klar, starter en utviklingsøkt ([/develop-ticket](.claude/skills/develop-ticket/SKILL.md)). Den er forket fra raffineringsøkta, så den husker koden og skissen. Den jobber i en egen klone i `.cache/work/<oppgave>/repo` på en egen branch, kjører prosjektets sjekker og committer. Når alt er grønt, skriver den en kort rapport. Da legger Python branchen oppå siste main, pusher den med git-innloggingen din og åpner en PR med `gh`. Oppgaven blir Til gjennomgang, `Pull request` får PR-lenken, og rapporten blir en kommentar i Notion og Slack. PR-en merges (squash) av refine.py så snart den er godkjent: enten av en annen person i GitHub (forfatteren kan ikke godkjenne sin egen PR), eller ved at du setter etiketten `godkjent` på den. Da blir oppgaven Fullført. Kommenterer noen oppgaven i Notion, eller skriver på PR-en (vanlige kommentarer, linjekommentarer eller et review med «endringer ønsket»), mens den er Til gjennomgang, fikser agenten PR-en og svarer på PR-en. Det som er utenfor oppgaven, blir en oppfølgingsoppgave i Innboks (uten «Klar for pipeline»). Et review med «endringer ønsket» hindrer merge, også med etiketten, til den som ba om endringene godkjenner eller trekker det tilbake. Lukkes PR-en uten merge, blir oppgaven stående i Klar. Agenten kan verken pushe, åpne PR eller merge selv. Med `mode = "main"` under `[merge]` føres branchen rett inn i main uten PR, som før. Har en annen oppgave endret de samme linjene i mellomtiden, vekkes agenten for å løse konflikten på branchen sin. Feiler bygg eller tester, pushes ingenting, og agenten spør i stedet. Oppgaver som er Blokkert av andre, venter til de er Fullført.
- **Hele boardet ved oppstart:** `python3 refine.py` går gjennom alle kolonnene, ikke bare det løkka selv har startet. Oppgaver med «Klar for pipeline» som er laget, flyttet eller raffinert uten løkka, tas over: Raffinering og ubesvarte kommentarer i Trenger avklaring raffineres videre, og **Klar** og Under utvikling bygges. Oppgaver som står i Til gjennomgang fra den gamle PR-flyten, følges av PR-vokteren i `mode = "pr"`, og føres inn i main uten Claude, eldste PR først, i `mode = "main"`. Det løkka tar over, listes når den starter.
- **Tomt repo:** har repoet ingen brancher ennå, lager første utvikling `main` med en tom start-commit, så branchene har noe å bygge på.

## Oppsett

1. Installer [Claude Code](https://code.claude.com) og logg inn med claude.ai-kontoen som har Notion- og Figma-koblingene.
2. Fyll ut [refiner.toml](refiner.toml): boardsiden, GitHub-repoet og branchen (eller en lokal mappe), og hovedfila i Figma.
3. Kopier `.env.example` til `.env` og sett `NOTION_API_KEY`, en nøkkel til en Notion-integrasjon. Det er den eneste hemmeligheten. Integrasjonen må legges til under **Connections** på boardsiden, med tilgangene read content, update content, insert content, read comments og insert comments.

Det trengs ingen GitHub-nøkkel i `.env`. For PR-modus må [gh](https://cli.github.com) være installert og logget inn (`gh auth login`) med skrivetilgang til repoet. refine.py sjekker det ved oppstart og oppretter etiketten `godkjent`. Før hver runde synker `/sync-code` en skrivebeskyttet kopi av repoet til `.cache/code` med din egen git-innlogging, den samme som `git clone` bruker, og Claude søker i den kopien. Figma leses via claude.ai-koblingen til Figma, som du allerede er logget inn på.

## Slack (valgfritt)

Med Slack kan du lage oppgaver med `/new <beskrivelse>` og snakke med oppgaven i en tråd. Agentens spørsmål, statusendringer og Figma-frames kommer i tråden, og det du svarer i tråden, blir en kommentar på oppgaven i Notion. Boardet er fortsatt kilden til sannhet, og agenten jobber bare mot Notion. Broen er [refiner/slack.py](refiner/slack.py), og den bruker ingen tokens.

1. **Lag appen.** Gå til [api.slack.com/apps](https://api.slack.com/apps) → *Create New App* → *From an app manifest*, velg arbeidsområdet og lim inn [slack-manifest.yml](slack-manifest.yml). Den setter opp `/new`, Socket Mode (ingen offentlig URL nødvendig), hendelsene for svar i tråder og tilgangene. I arbeidsområder som krever godkjenning, må en Slack-admin godkjenne appen.
2. **Installer den.** *Install App* → *Install to Workspace*. Kopier *Bot User OAuth Token* (`xoxb-…`) til `.env` som `SLACK_BOT_TOKEN`.
3. **Lag en app-nøkkel for Socket Mode.** *Basic Information* → *App-Level Tokens* → *Generate Token and Scopes*, med scopen `connections:write`. Kopier nøkkelen (`xapp-…`) til `.env` som `SLACK_APP_TOKEN`.
4. **La Notion-integrasjonen lese e-post.** På integrasjonen (notion.so/profile/integrations) skrur du på *Read user information including email addresses*. Slik finner `/new` riktig Eier: den som skriver kommandoen, må ha samme e-post i Slack og Notion.
5. **Installer Slack-biblioteket** i et virtuelt miljø:
   ```sh
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   ```
   Etterpå starter du som vanlig med `python3 refine.py`. Finnes `.venv`, starter refine.py seg selv på nytt i det miljøet.
6. **Velg kanalen.** Broen bruker én kanal. Inviter boten dit med `/invite @Raffineringsagent`, og sett kanal-ID-en som `channel` under `[slack]` i refiner.toml. ID-en står nederst i kanalens detaljer.
7. **Bruk den.** Skriv `/new Vis åpningstidene i footeren på mobil` i den kanalen. `/new` i andre kanaler gir bare et svar om hvor den skal brukes, og meldinger i andre kanaler ignoreres. Alle oppgaver får sin tråd i kanalen, også de som lages direkte i Notion.

Valgfritt:
- **Figma som bilder:** med `FIGMA_TOKEN` (en personlig nøkkel fra Figma) i `.env` lastes framene opp som bilder i tråden. Uten den postes de som lenker, og Slack viser forhåndsvisning hvis Figma-appen er installert i Slack.

## Bruk

```sh
python3 refine.py              # start løkka, og la den stå (Ctrl-C stopper, øktene er lagret)
python3 refine.py --verbose    # skriv også ut hele inndata og svar for hvert verktøykall

# eller interaktivt
claude
/refine-board              # én runde, med hvert steg synlig
/loop 2m /refine-board     # fortsett hvert andre minutt
```

For å prøve: lag en oppgave, sett **Eier** og huk av **Klar for pipeline**. Når en spesifikasjon går glipp av noe en erfaren utvikler ville fanget opp, strammer du inn [definition-of-ready.md](.claude/skills/refine-ticket/definition-of-ready.md).
