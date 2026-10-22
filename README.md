# Raffineringsagent

Gjør grove Notion-oppgaver om til spesifikasjoner en utvikler kan bygge uten å gjette. Den jobber i en løkke med oppgavens eier til ingenting er uklart. Den er bygd av Claude Code-skills som leser Notion og Figma via claude.ai-koblingene, og skriver til Notion via Notion API. Den leser produktkoden, men endrer den aldri. Alt den skriver, er på norsk.

```
Innboks ──(Eier satt + «Klar for pipeline» huket av)──▶ Raffinering ─▶ Trenger avklaring ⟲ (eieren svarer i kommentarer → neste runde raffinerer igjen)
                                                                ─▶ Klar
                                                                ─▶ delt opp i små oppgaver (Blokkert av i byggerekkefølge), originalen blir en paraply → Klar når alle delene er det
```

## Slik fungerer det

| Fil | Jobb |
|---|---|
| [refine.py](refine.py) | Inngangen: løkka som venter på boardet og starter, fortsetter eller forker én Claude-økt per oppgave |
| [refiner/](refiner/) | Koden bak løkka: `board.py` (Notion API), `jobs.py` (hva som skal skje neste, og meldingen til agenten), `claude.py` (starte en økt og vise den), `sessions.py` (`.cache/sessions.json`), `code.py` (git-synk), `config.py` (refiner.toml og regelfilene i systemprompten), `display.py` (terminalvisningen) |
| [refiner.toml](refiner.toml) | Innstillinger: boardsiden, GitHub-repo og branch, og Figma-fila |
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
- **Spørsmål er kommentarer.** Hvert spørsmål er sin egen tråd (`🤖 S3: … Uten svar antar jeg …`). Svar i tråden, eller kommenter en linje i Raffinering-seksjonen. Neste runde oppdaterer spesifikasjonen og svarer med hva som ble endret. Alt agenten skriver, starter med 🤖.
- **Skrivebeskyttede kolonner:** folk redigerer i Innboks og Trenger avklaring. Raffinering og Klar er bare for kommentarer. Notion kan ikke styre tilgang etter status, så agenten ruller tilbake endringer i status og egenskaper der ved hver runde. Endringer i brødteksten kan den bare flagge, ikke angre.
- **Én økt per oppgave:** `python3 refine.py` holder seg oppe og spinner. Den sjekker boardet hvert 20. sekund via Notion API, uten Claude og nesten gratis. Når en oppgave i Innboks har Klar for pipeline og én Eier, starter den en Claude-økt for den oppgaven. Økta leser Notion, Figma og koden, og stopper av seg selv når oppgaven er **Klar** (da er økta ferdig) eller **Trenger avklaring**. Mens den venter på svar, kjører ingenting, så det koster ingen tokens. Når eieren kommenterer eller endrer oppgaven, fortsetter løkka **den samme økta** (`--resume`), med alt den har lest fra før. Den trenger altså ikke å lese Figma og koden på nytt. Oppgaver som deles opp, får hver sin økt, som starter med morens kontekst (`--fork-session`). Øktene ligger i `.cache/sessions.json`, så de overlever at du stopper og starter refine.py.
- **Billig i drift:** økta kjører på Sonnet 5.5 med `effort = "medium"` (endres i `[agent]` i refiner.toml). Regelfilene sendes i den bufrede systemprompten. Kodesynken, sjekken av boardet, egenskapene og kommentarene gjør Python, så agenten ikke bruker steg på det. Før en økt hviler, komprimeres den med `/compact`, så en fortsettelse sender et sammendrag i stedet for hele historikken. Kostnaden per oppgave summeres i `.cache/sessions.json` og vises når oppgaven er Klar.
- **Kommenter nederst på siden:** den billige sjekken ser kommentarer på siden, ikke kommentarer på enkeltlinjer inne i teksten.
- **Språk:** alt agenten skriver i Notion og i terminalen, er på norsk (bokmål). Navn på statuser, egenskaper og tagger er som på boardet.

## Oppsett

1. Installer [Claude Code](https://code.claude.com) og logg inn med claude.ai-kontoen som har Notion- og Figma-koblingene.
2. Fyll ut [refiner.toml](refiner.toml): boardsiden, GitHub-repoet og branchen (eller en lokal mappe), og hovedfila i Figma.
3. Kopier `.env.example` til `.env` og sett `NOTION_API_KEY`, en nøkkel til en Notion-integrasjon. Det er den eneste hemmeligheten. Integrasjonen må legges til under **Connections** på boardsiden, med tilgangene read content, update content, insert content, read comments og insert comments.

Det trengs ingen GitHub-nøkkel. Før hver runde synker `/sync-code` en skrivebeskyttet kopi av repoet til `.cache/code` med din egen git-innlogging, den samme som `git clone` bruker, og Claude søker i den kopien. Figma leses via claude.ai-koblingen til Figma, som du allerede er logget inn på.

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
