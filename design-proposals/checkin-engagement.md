# Plan: van check-in naar gewoonte — drie scenario's

**Datum:** 2026-07-10
**Doel-keten (Gerd-Jan):** vaker gebruiken → betere health-data → beter voelen
→ gelukkiger, beter voor omgeving en resultaten.

**Aanleiding:** de AI-vraag ziet nu wel ruwe data (7 dagen trackers, antwoorden,
doelen), maar berekent geen trends of gaten — "je logt al 5 dagen geen calorieën"
gebeurt alleen per toeval. En er is géén gevoel/stemming-datapunt, dus een diepere
vraag naar hoe je je voelt kan hij niet onderbouwen.

---

## Fundament (nodig voor alle scenario's)

Twee kleine bouwstenen die elk scenario voeden:

1. **Stemming vastleggen** — één tap in de check-in: "Hoe voel je je nu?" op een
   5-puntsschaal (😞 😕 😐 🙂 😄), opgeslagen als tracker "Stemming". Kost de
   gebruiker 1 seconde, en is het ontbrekende datapunt dat de hele keten meetbaar
   maakt (health-data ↔ gevoel).
2. **Signalen-engine** — een functie die vóór elke AI-aanroep berekent:
   - *Gaten:* welke metric ontbreekt hoeveel dagen (calorieën niet gelogd sinds X)
   - *Trends:* 7-daags gemiddelde vs. de 7 dagen ervoor per metric (slaap ↓, stappen ↑)
   - *Reeksen:* check-in streak, doel-haalpercentage deze week
   - *Correlatie-hints:* stemming vs. slaap/beweging (zodra er stemmingsdata is)
   Deze signalen gaan als expliciet blok de prompt in — de AI hoeft ze niet meer
   toevallig te ontdekken, hij kríjgt ze.

---

## Scenario 1 — "De partner die oplet" (signalerend, kleinste stap)

**Idee:** de AI-vraag wordt data-gedreven. De signalen-engine voedt de prompt, en
de toon-rotatie krijgt een vijfde invalshoek: *signalerend* — benoem een gat of
trend en vraag ernaar.

> *"Ik zie sinds dinsdag geen calorieën meer — bewuste pauze of erbij ingeschoten?"*
> *"Je sliep drie nachten onder de 6,5 uur en je stemming zakte mee. Wat speelt er?"*

**Wat er gebeurt:**
- Signalen-engine + stemming-tap (fundament)
- Prompt-uitbreiding met signalenblok + signalerende toon
- Claude's reflectie ná het opslaan gebruikt dezelfde signalen

**Waarom dit werkt voor de keten:** de check-in voelt direct persoonlijk — hij
*merkt* dingen op. Dat is de reden om terug te komen ("wat heeft hij vandaag
gezien?"). Gaten in health-data worden vanzelf onderwerp van gesprek → jij fixt
je logging → data verbetert.

**Effort:** ~een dagdeel. Geen nieuwe UI behalve de stemming-tap.
**Risico:** laag. **Effect op gebruik:** direct maar bescheiden — de pull zit in
betere vragen, niet in een nieuw moment.

## Scenario 2 — "Het gesprek" (de check-in komt naar jou toe)

**Idee:** draai de richting om. Niet jij opent de app, de app opent jou — en de
check-in wordt een kort gesprek in plaats van een formulier.

**Wat er gebeurt:**
- **Ochtend-nudge:** dagelijks (instelbaar tijdstip) genereert de server Claude's
  vraag van de dag en stuurt hem als notificatie. Opties: PWA-push (werkt op iOS
  16.4+ als Grip op het beginscherm staat), of pragmatischer: een e-mail/Shortcut-
  notificatie via een simpel endpoint. De vraag zelf is de teaser — nieuwsgierigheid
  als haakje.
- **Micro-dialoog:** na jouw antwoord op Claude's vraag volgt één korte doorvraag
  (streaming, zoals de chat), daarna afronden. Maximaal één ronde — het moet 2
  minuten blijven.
- **Avond-variant (optioneel):** aparte mini-check-in 's avonds: alleen stemming +
  "hoe kijk je terug?" — 15 seconden. Geeft de ochtend-AI vers materiaal én een
  tweede contactmoment.

**Waarom dit werkt voor de keten:** gewoontes ontstaan door een trigger op een
vast moment. De notificatie ís die trigger; het gesprek maakt afmaken belonend.
Twee lichte contactmomenten per dag verdubbelen de data zonder meer invultijd.

**Effort:** 2-3 dagdelen (push-infrastructuur is het meeste werk; e-mailvariant
halveert dat). **Risico:** notificatie-moeheid — daarom max 1-2 per dag en een
snooze. **Effect op gebruik:** waarschijnlijk het grootst — dit pakt het
*vergeten* aan, de meest voorkomende reden dat journals sneuvelen.

## Scenario 3 — "De wekelijkse spiegel" (het waarom zichtbaar maken)

**Idee:** maak de keten die jij beschrijft zichtbaar in het product. Als je *ziet*
dat betere data → beter voelen, wordt bijhouden intrinsiek de moeite waard.

**Wat er gebeurt:**
- **Correlatie-inzichten:** wekelijks (bij de weekreview, automatisch) legt de
  server stemming naast slaap, beweging, calorieën en doel-haalratio:
  > *"In de 9 weken met gemiddeld >7u slaap gaf je je stemming een 4,1;
  > in de andere weken een 3,2."*
- **Spiegel-kaart op het dashboard:** streak, stemming-sparkline naast
  slaap/stappen, en het inzicht van de week. Eén blik = het verband.
- **Maand-terugblik:** eerste check-in van de maand opent met een mini-review
  van Claude: wat veranderde er, wat leverde het op, wat verdient focus.
- Milestone-momenten (streak 7/30/100, beste slaapweek) als kleine viering in
  de check-in — geen badges-circus, wel erkenning.

**Waarom dit werkt voor de keten:** scenario 1 en 2 duwen (extrinsiek); dit trekt
(intrinsiek). Het beantwoordt "waarom doe ik dit?" met jouw eigen data. Dat is
wat een gewoonte ná week zes overeind houdt.

**Effort:** 2-3 dagdelen, deels afhankelijk van genoeg stemmingsdata (heeft dus
weken aanloop nodig — juist een reden om de stemming-tap nú te bouwen).
**Risico:** correlaties op weinig data kunnen onzin zijn → minimum-drempel
(bijv. pas tonen na 3 weken data) en altijd als observatie formuleren, nooit
als conclusie.

---

## Vergelijking en aanbeveling

| | S1 Signalerend | S2 Gesprek | S3 Spiegel |
|---|---|---|---|
| Pakt aan | vragen te generiek | vergeten / geen trigger | motivatie op lange termijn |
| Effect op dagelijks gebruik | + | +++ | ++ (na weken) |
| Effort | ~1 dagdeel | 2-3 dagdelen | 2-3 dagdelen |
| Afhankelijkheden | geen | push/e-mail kanaal | stemmingsdata uit S1 |

**Aanbeveling: gefaseerd, in deze volgorde — ze stapelen.**
1. **Nu:** fundament + Scenario 1 (stemming-tap + signalen-engine + signalerende AI).
   Klein, en S2/S3 hebben het toch nodig.
2. **Volgende:** Scenario 2, te beginnen met de eenvoudigste nudge-variant
   (vraag-van-de-dag als notificatie), micro-dialoog daarna.
3. **Over ~3 weken** (als er stemmingsdata ligt): Scenario 3.

Zo test je bovendien per stap of het gebruik echt toeneemt voordat de volgende
investering gedaan wordt — de streak-teller is de meetlat.
