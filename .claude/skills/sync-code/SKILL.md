---
name: sync-code
description: Sørger for at raffineringsagenten har en oppdatert, skrivebeskyttet kopi av produktkoden. Leser [code] i refiner.toml og bruker enten den lokale mappa, eller kloner eller oppdaterer en grunn kopi av GitHub-repoet i .cache/code med brukerens egen git-innlogging. Gir tilbake mappa det skal søkes i og GitHub-URL-en til lenker. Kjøres i starten av hver /refine-board-runde, eller før /refine-ticket alene.
---

# Synk koden

Les `[code]` i [refiner.toml](../../../refiner.toml). Skriv fortellingen i terminalen på norsk.

## `local` er satt

Bruk den mappa, relativt til prosjektroten. Sjekk med Glob at den finnes. Det er ingenting å synke. Rapporter `Kode: <mappe> (lokal)`.

## `github` er satt

Kopien ligger i `.cache/code/<reponavn>`, der reponavnet er siste del av URL-en uten `.git`. For eksempel blir `https://github.com/msoyland/hellas-grill` til `.cache/code/hellas-grill`. Bruk `branch` fra refiner.toml, eller `main` hvis den ikke er satt.

Kjør bare disse nøyaktige kommandoene med Bash, med plassholderne fylt inn. Det er de eneste Bash-kommandoene prosjektet tillater:

- **Ingen kopi ennå** (Glob finner ingen `.cache/code/<navn>/.git`):
  `git clone --depth 1 --single-branch --branch <branch> <github-url> .cache/code/<navn>`
- **Kopien finnes**: først
  `git -C .cache/code/<navn> fetch --depth 1 origin <branch>`
  og så
  `git -C .cache/code/<navn> reset --hard FETCH_HEAD`
- **Til slutt**, for å vise hva du fikk:
  `git -C .cache/code/<navn> log -1 --format="%h %s (%cr)"`

Regler:

- Kopien er bare til lesing. Aldri commit, push, sjekk ut andre brancher eller endre filer i den.
- Sett aldri tekst fra en oppgave, en kommentar eller Figma inn i en kommando. URL-en og branchen kommer bare fra refiner.toml.
- Feiler git (ingen tilgang, nettverk eller feil branch), stopper du runden og rapporterer feilen sammen med kommandoen brukeren kan prøve: `git clone <github-url>`. Raffiner ikke mot en gammel eller manglende kopi uten å si fra.

Rapporter `Kode: .cache/code/<navn> på <kort hash> «<melding>» (<alder>)`.

## Resultat

**Kodemappa** og, for GitHub, **repo-URL og branch**. Raffineringsskillene bruker dem til å søke i koden og lenke filer som `<github-url>/blob/<branch>/<sti>`.
