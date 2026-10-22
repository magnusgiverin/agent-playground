---
name: refine-board
description: Én runde over hele Notion-boardet, for bruk interaktivt (refine.py følger hver oppgave i sin egen økt i stedet). Slipper inn oppgaver med «Klar for pipeline» huket av og nøyaktig én Eier, låser eieren, holder Raffinering og Klar skrivebeskyttet for folk, raffinerer nye og besvarte oppgaver med /refine-ticket, og flytter oppdelte paraplyoppgaver til Klar når delene er det. Kjør den når du vil ta en manuell runde, eller med /loop 2m /refine-board. Vanligvis kjører du python3 refine.py i stedet.
---

# Raffiner boardet

Hver runde er kort og trygg å gjenta. Les [board.md](../../../board.md) først. **Alt du skriver, er på norsk (bokmål)**: kommentarer, Blokkeringsnotat, logglinjer og fortellingen i terminalen.

**Fortell underveis.** Noen følger med i terminalen. Før hvert steg skriver du én kort linje om hva du gjør, for eksempel `Sjekker boardet i Notion…`, `Vakt: 2 oppgaver i Raffinering eller Klar, ser etter endringer…` eller `Slipper inn «Tekstendring»…`. Etter hvert steg skriver du én linje om hva du fant, for eksempel `Boardet er OK: alle 12 egenskaper og 4 statuser finnes.` eller `1 ny oppgave, 0 besvarte, 0 fastlåste.` Bare fakta, ingen resonnering høyt.

**Alle endringer i denne skillen går via [notion-write](../notion-write/SKILL.md).** Les med claude.ai-koblingen til Notion. Når stegene sier «sett», bruker du `API-patch-page` (å flytte en oppgave mellom kolonner er å sette `Status`). Når de sier «post» eller «kommenter», bruker du `API-create-a-comment`. Send alle egenskapsendringene for én oppgave i ett kall.

Folk kan bare redigere oppgaver i **Innboks** og **Trenger avklaring**. **Raffinering** og **Klar** tilhører agenten. Der kommenterer folk, og du ruller tilbake alle andre endringer de gjør. Notion kan ikke håndheve dette selv, så denne runden gjør det.

## 1. Synk koden og sjekk boardet

Kjør `/sync-code`. Feiler den, stopper du runden og rapporterer feilen. Ta vare på kodemappa og GitHub-URL-en den gir deg, som kodeargumentene (se board.md).

Les refiner.toml. `notion-fetch` boardsiden fra `[notion] board`, og hent så datakilden til oppgavedatabasen på den. Bekreft at alle egenskaper og statuser fra board.md finnes. Mangler noe, stopper du og rapporterer nøyaktig hva som må legges til. Endre aldri boardets skjema selv.

## 2. Vakt: rull tilbake endringer folk ikke har lov til å gjøre

Gjør dette før alt annet, så du aldri raffinerer en oppgave noen har tuklet med.

**Forventet tilstand** for en pipeline-oppgave er siste linje i Logg under `## 🤖 Raffinering` (se [page-layout.md](../refine-ticket/page-layout.md)). Den har verdiene du sist satte for status, tittel, tagger, størrelse, Figma-lenke, Blokkert av og Klar for pipeline. **Den låste eieren** står i den første kommentaren som starter med `🤖 Pipeline startet. Eier:`.

Hent alle oppgaver med `Status` = Raffinering eller Klar, og alle oppgaver med en låsekommentar som ikke er Fullført. En oppgave med låsekommentar, men uten Tilstand-linje ennå, er fortsatt under arbeid. Der sjekker du bare eieren. For hver oppgave:

| Det du ser | Det du gjør |
|---|---|
| Raffinering eller Klar, men ingen låsekommentar (flyttet dit for hånd) | Sett `Status` til Innboks. Kommenter `🤖 Bare raffineringsagenten flytter oppgaver til <status>. Sett en Eier og huk av Klar for pipeline i stedet.` |
| Raffinering eller Klar med låsekommentar, men status, tittel, tagger, størrelse, Figma-lenke, Blokkert av eller Klar for pipeline avviker fra forventet tilstand | Sett dem tilbake. Kommenter `🤖 Denne oppgaven er skrivebeskyttet mens den er i <status>. Jeg har rullet tilbake endringen i <felter>. Skriv en kommentar her i stedet, så oppdaterer jeg oppgaven.` Nevn eieren. |
| Flyttet fra Raffinering eller Klar til Innboks eller Trenger avklaring for hånd | Sett den tilbake til forventet status, og post den samme kommentaren. |
| Flyttet fra Klar til Under utvikling | Lov. Utviklingen har startet. |
| `Eier` er en annen enn den låste eieren (alle statuser unntatt Fullført) | Sett `Eier` tilbake. Kommenter `🤖 Eieren er låst til <mention> fordi oppgaven er i pipelinen. Den kan ikke endres.` |
| Brødteksten over Raffinering-overskriften er endret på en oppgave i Raffinering eller Klar | Dette kan du ikke angre trygt, så la den være. Kommenter `🤖 Denne oppgaven er skrivebeskyttet mens den er i <status>. Legg endringer i en kommentar i stedet.` |

For å oppdage endringer i brødteksten sammenligner du teksten over skillelinjen med `Øyeblikksbilde av originalteksten` i Raffinering-seksjonen. Post ikke samme vaktkommentar på nytt hvis din forrige kommentar på oppgaven allerede sier det samme.

## 3. Finn arbeidet

Spør i rows-modus, eldste først, med `Klar for pipeline` huket av. I Innboks og Trenger avklaring betyr en tom boks at oppgaven er satt på pause.

1. **Nye**: `Status` = Innboks, eller tom status (en oppgave uten status regnes som Innboks).
2. **Besvarte**: `Status` = Trenger avklaring. Kall `notion-get-comments` med `include_all_blocks: true` for hver av dem. Plukk den opp hvis siste kommentar i en diskusjon ikke starter med `🤖`, altså at eieren har svart, eller hvis teksten over skillelinjen avviker fra øyeblikksbildet, altså at eieren har redigert oppgaven. Begge deler er lov i Trenger avklaring. Ellers venter den fortsatt, så hopp over den.
3. **Fastlåste**: `Status` = Raffinering, uten `Blokkeringsnotat` som starter med «Delt opp i», og med `Sist oppdatert` for over en time siden. En tidligere runde døde mens den jobbet med den.
4. **Paraplyer**: `Status` = Raffinering, med `Blokkeringsnotat` som starter med «Delt opp i». Er alle oppgavene i `Blokkert av` Klar eller lenger (Under utvikling, Til gjennomgang eller Fullført), setter du paraplyen til Klar, tømmer `Blokkeringsnotat`, legger til en logglinje og poster `🤖 Alle delene er klare.` Ikke raffiner den på nytt.

## 4. Slipp inn nye oppgaver

For hver ny oppgave sjekker du `Eier` først:

- **Tom, eller mer enn én person**: fjern haken i `Klar for pipeline`. Post `🤖 Oppgaven trenger nøyaktig én Eier før den kan gå inn i pipelinen. Sett Eier, og huk av Klar for pipeline igjen.` og nevn den som opprettet siden. Hopp over oppgaven. Siden haken er borte, poster ikke neste runde dette på nytt.
- **Nøyaktig én person**: kjør `/refine-ticket` med oppgavens URL i steg 5. Den tar oppgaven selv (setter Raffinering og poster låsekommentaren, se refine-ticket steg 0).

## 5. Raffiner

Ta høyst så mange som «Oppgaver per runde» i board.md: først de besvarte, så de nye du slapp inn, så de fastlåste, eldste først i hver gruppe. For hver oppgave:

1. For besvarte oppgaver setter du `Status` til Raffinering. Nye oppgaver tar refine-ticket selv.
2. Kjør `/refine-ticket` med oppgavens URL og kodeargumentene. Hopp over oppgaver du ikke fikk tatt.
3. Feiler noe, poster du `🤖 Raffineringen feilet: <kort grunn>. Jeg prøver igjen i neste runde.` og lar statusen stå på Raffinering, så neste runde plukker den opp som fastlåst.

## 6. Rapport

Skriv én linje per oppgave du rørte (tittel, gammel status → ny status, lenke), én linje per endring vakten rullet tilbake, og hvor mange oppgaver som venter på svar. Var det ingenting å gjøre, skriver du bare `Ingenting å raffinere`.
