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
| [refine.py](refine.py) | Kjører `/refine-board` uten innblanding med `--permission-mode dontAsk`, skriver ut hvert steg mens det skjer, og venter med `--watch` til noe nytt skjer på boardet |
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
- **Utløser:** `python3 refine.py --watch` holder seg oppe og spinner. Mens den venter, sjekker den boardet hvert 20. sekund via Notion API, uten Claude og nesten gratis. Den starter en ny runde bare når noe nytt skjer: en ny oppgave i Innboks med Klar for pipeline huket av, en ny kommentar fra et menneske, eller når et menneske endrer en oppgave i Trenger avklaring, Raffinering eller Klar. Agentens egne endringer teller ikke. Hvert 30. minutt kjører den uansett en full runde, fordi kommentarer på enkeltlinjer og fastlåste oppgaver ikke synes i den billige sjekken. Juster med `--poll` og `--max-wait`.
- **Språk:** alt agenten skriver i Notion og i terminalen, er på norsk (bokmål). Navn på statuser, egenskaper og tagger er som på boardet.

## Oppsett

1. Installer [Claude Code](https://code.claude.com) og logg inn med claude.ai-kontoen som har Notion- og Figma-koblingene.
2. Fyll ut [refiner.toml](refiner.toml): boardsiden, GitHub-repoet og branchen (eller en lokal mappe), og hovedfila i Figma.
3. Kopier `.env.example` til `.env` og sett `NOTION_API_KEY`, en nøkkel til en Notion-integrasjon. Det er den eneste hemmeligheten. Integrasjonen må legges til under **Connections** på boardsiden, med tilgangene read content, update content, insert content, read comments og insert comments.

Det trengs ingen GitHub-nøkkel. Før hver runde synker `/sync-code` en skrivebeskyttet kopi av repoet til `.cache/code` med din egen git-innlogging, den samme som `git clone` bruker, og Claude søker i den kopien. Figma leses via claude.ai-koblingen til Figma, som du allerede er logget inn på.

## Bruk

```sh
python3 refine.py              # én runde
python3 refine.py --watch      # hold deg oppe: vent til noe nytt skjer på boardet, og kjør en runde da
python3 refine.py --verbose    # skriv også ut hele inndata og svar for hvert verktøykall

# eller interaktivt
claude
/refine-board              # én runde, med hvert steg synlig
/loop 2m /refine-board     # fortsett hvert andre minutt
```

For å prøve: lag en oppgave, sett **Eier** og huk av **Klar for pipeline**. Når en spesifikasjon går glipp av noe en erfaren utvikler ville fanget opp, strammer du inn [definition-of-ready.md](.claude/skills/refine-ticket/definition-of-ready.md).
