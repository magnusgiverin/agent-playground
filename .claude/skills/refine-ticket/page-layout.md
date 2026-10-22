# Oppsett av Raffinering-seksjonen

Seksjonen står alltid helt nederst på siden, under forfatterens originaltekst, og starter med skillelinjen. På oppgaver fra malen erstatter den malskjelettet (se refine-ticket steg 4). Hold overskriftene i denne rekkefølgen, så senere runder finner og erstatter seksjonen. Skriv `(ingen)` under en overskrift i stedet for å utelate den. Alt skrives på norsk.

```markdown
---
## 🤖 Raffinering
> Vedlikeholdes av raffineringsagenten. Teksten din over endres aldri. Svar på spørsmålene i kommentarene, eller kommenter en linje under for å rette den.

**Eier (låst):** <mention-user url="user://…"/>

### Problem
En eller to setninger: hva trengs og hvorfor, sett fra brukeren.

### Akseptansekriterier
- [ ] Kort, testbart utsagn
- [ ] …

### Design
<embed source="https://www.figma.com/design/<fileKey>/<name>?node-id=1-23">Framenavn: hva den viser</embed>

- Ikke designet: lasting, tom tilstand, feil, …

(Én embed per frame du brukte, hver som egen blokk. Arbeid uten UI skriver `(ingen)`.)

### Omfang
- `sti/i/repo.ts`: hvorfor den berøres (og si fra hvis noe lignende finnes allerede). Kommer koden fra GitHub, lenk stien til fila der.

### Utenfor omfang
- …

### Testing
- Hva som bør testes automatisk, og de manuelle sjekkene (nettlesere, skjermstørrelser, feiltilfeller) som betyr noe for denne endringen

### Antakelser
- Små detaljer som er trygge å anta og lette å endre senere

### Avhengigheter
- Andre oppgaver (som mentions, og også i `Blokkert av`), beslutninger eller tilganger dette venter på

### Åpne spørsmål
- **S4**: … _Uten svar antar jeg: …_
- **S1** (besvart): … → svaret, og hva det endret

### Delt opp i
- <mention-page url="…"/>: oppsummering på én linje (bare for oppdelte oppgaver)

### Logg
- 2026-10-21, runde 1: første raffinering. Opprinnelig tittel: "…" → **Tilstand:** Status=Trenger avklaring · Oppgave="…" · Etiketter=design, frontend · Størrelse=M · Figma-lenke=https://…node-id=1-23 · Blokkert av=(ingen) · Klar for pipeline=ja
- 2026-10-22, runde 2: S1 og S2 besvart. AK 2 endret. S4 lagt til. → **Tilstand:** Status=Trenger avklaring · …

<details>
<summary>Forfatterens utfylling av malen (ordrett, bare hvis forfatteren skrev noe i malskjelettet)</summary>
Det forfatteren skrev under malens overskrifter, kopiert ordrett med overskriftene. Utelat hele toggelen hvis forfatteren ikke skrev noe der.
</details>

<details>
<summary>Øyeblikksbilde av originalteksten (brukes til å oppdage endringer, ikke rør)</summary>
Teksten over skillelinjen, kopiert ordrett slik den var da seksjonen sist ble skrevet.
</details>
```

Besvarte spørsmål blir stående i lista, merket «(besvart)» og med svaret, så forfatteren ser historikken. Ikke bruk gjennomstreking (`~~`), fordi API-et gjør den om til bokstavelige tegn når seksjonen skrives på nytt.

Toggles skrives som `<details><summary>…</summary>…</details>`, fordi det er formatet Notion API forstår. I koblingens visning heter det samme `<toggle>`, og begge betyr den samme toggelen.

Loggen får én linje per runde der noe endret seg. Antall linjer er antall runder. **Tilstand i siste logglinje er nøyaktig de verdiene agenten har satt.** `/refine-board` sammenligner oppgaven med den og ruller tilbake endringer folk gjør mens oppgaven er i Raffinering eller Klar. Skriv tomme verdier som `(ingen)`, og list flere verdier i fast rekkefølge (alfabetisk for tagger). Statuser, egenskaper og tagger skrives med navnene fra boardet.

Øyeblikksbildet fornyes ved hver runde. Det lar vakten oppdage endringer i brødteksten på skrivebeskyttede oppgaver.
