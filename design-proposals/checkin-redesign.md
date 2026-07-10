# Voorstel: Check-in redesign — wisselende reflectievragen + AI-accountability

**Datum:** 2026-07-10
**Doel:** Sneller en simpeler dagelijks inchecken. Geen vaste vragen per type meer,
maar wisselende reflectievragen (à la 5 Minute Journal / Daily Stoic), met behoud
van de AI-accountabilitypartner die prikkelt en motiveert.

---

## Wat er nu is (en wat eraan schort)

De huidige check-in bestaat uit ~8 kaarten: tracker-invulgrid, twee vaste
reflectievragen, doel-van-gisteren, taken-van-gisteren, doel-vandaag, 3 taken,
"waar heb je zin in", en Claude's vraag. Problemen:

1. **Te lang.** Acht kaarten met zes tekstvelden voelt als een formulier, niet als journalen.
2. **De vaste vragen slijten.** Elke dag "wat was het leukste van gisteren?" nodigt na
   week twee uit tot automatische antwoorden.
3. **Het tracker-grid is achterhaald** — de health-sync vult stappen/slaap/gewicht nu
   automatisch. Handmatig cijfers overtypen is dubbel werk.
4. De `questions`-tabel (core/pool per categorie) wordt door de check-in **niet gebruikt**
   — dode infrastructuur die we juist kunnen hergebruiken voor de rotatie.

## Concept: "2 minuten, 3 vragen"

Vier compacte blokken, in deze volgorde:

### Blok 1 — Gisteren afronden (accountability, 10 seconden)
- **"Doel van gisteren: 〈doel〉"** met drie tap-chips: **Gehaald / Deels / Niet**
  (nu: checkbox + los tekstveld). Optioneel één regel toelichting, ingeklapt.
- Taken van gisteren afvinken (alleen zichtbaar als die er zijn).
- ~~Tracker-grid~~ → **vervalt**. Health-data komt binnen via de sync; alleen
  trackers zonder health-bron (bijv. booleans als "gelezen") blijven, als één
  compacte regel met tap-chips.

### Blok 2 — Twee wisselende reflectievragen (het journal-hart)
Elke dag **één terugblik-vraag + één vooruitblik-vraag** uit een pool, geïnspireerd
op 5 Minute Journal, Daily Stoic en One Line a Day. Voorbeelden:

**Terugblik (pool A, ~12 vragen):**
- Waar ben je dankbaar voor als je aan gisteren terugdenkt? *(5MJ)*
- Wat gaf je gisteren energie — en wat vrat energie? *(energie-audit)*
- Wat heb je gisteren geleerd, hoe klein ook? *(Daily Stoic avondvraag)*
- Welk moment van gisteren verdient een compliment aan jezelf?
- Wie maakte gisteren het verschil voor je — en weet diegene dat?
- Wat stelde je gisteren uit, en wat zat daarachter?

**Vooruitblik (pool B, ~12 vragen):**
- Wat zou vandaag geweldig maken? *(5MJ)*
- Welk obstakel kun je vandaag zien aankomen — en hoe reageer je dan? *(premeditatio malorum)*
- Waar kijk je vandaag naar uit?
- Waar zeg je vandaag bewust "nee" tegen?
- Hoe wil je vanavond op vandaag terugkijken?
- Welke kleine stap richting je kwartaaldoel past in vandaag?

**Rotatielogica:** deterministisch op datum (zelfde dag = zelfde vragen, zoals de
bestaande seed-aanpak in `questions.py`), maar met een geshuffelde cyclus zodat een
vraag pas terugkomt als de hele pool geweest is. Pools komen in de bestaande
`questions`-tabel (categorieën `reflect_back` / `reflect_forward`), dus later
vragen toevoegen/uitzetten kan zonder code.

### Blok 3 — Claude's vraag (de accountabilitypartner, aangescherpt)
Blijft AI-gegenereerd (Haiku, bestaande route), maar de prompt wordt scherper:

- Krijgt naast de 7-dagen context ook **gisterens antwoorden** en **de twee
  rotatievragen van vandaag** mee (zodat hij niet dubbelt).
- Instructie nieuwe stijl: *"Je bent een accountabilitypartner. Verwijs naar iets
  concreets uit de afgelopen dagen (een uitspraak, een patroon, een niet-gehaald
  doel). Wees prikkelend of motiverend, nooit vaag. Eén vraag, max 25 woorden."*
- Variatie in invalshoek per dag (doorvragen / confronteren / vieren / vooruitduwen),
  gestuurd met een datum-geroteerde toonhint in de prompt.

Voorbeeld van het verschil: nu *"Wat wil je vandaag bereiken?"* → straks *"Je doel
'presentatie afmaken' schoof deze week twee keer door — wat maakt vandaag anders?"*

### Blok 4 — Vandaag (het contract)
- **Eén belangrijkste doel** (blijft — dit is het anker waar de AI op doorvraagt).
- **Max 3 taken** (blijft, dashboard en streak gebruiken dit).
- ~~"Waar heb je zin in vandaag?"~~ → vervalt als vast veld, zit in pool B.

Na opslaan blijft **Claude's reflectie** beschikbaar zoals nu.

## Datamodel

- Nieuw tabelletje **`reflection_answers`** `(id, date, question_id, question_text,
  answer)` — antwoorden op rotatievragen. `question_text` wordt meegeslagen zodat
  historie leesbaar blijft als een vraag later wijzigt.
- `check_ins` behoudt: `today_main_goal`, `yesterday_goal_done` (wordt
  `0/0.5/1` voor niet/deels/gehaald), `yesterday_goal_note`, `claude_question`,
  `claude_question_answer`. De kolommen `yesterday_highlight/yesterday_different/
  today_joy` blijven bestaan (oude data), maar worden niet meer gevuld.
- Seed-migratie vult de twee vraagpools; bestaande `daily`-poolvragen die passen
  verhuizen mee.
- Historie-pagina toont voortaan vraag+antwoord-paren uit `reflection_answers`
  naast de vaste velden.

## Wat het oplevert

| | Nu | Straks |
|---|---|---|
| Kaarten | ~8 | 4 |
| Tekstvelden | 6+ | 3 (2 reflecties + AI-antwoord) + doel |
| Vragen | elke dag dezelfde | roteren, pas herhaling na volledige pool |
| AI-vraag | generiek | verwijst naar concrete patronen/uitspraken |
| Trackers | handmatig overtypen | automatisch via health-sync |

## Bouwvolgorde

1. Migratie: `reflection_answers` + vraagpools seeden (`database.py`)
2. Rotatielogica in `questions.py` (vervangt `get_daily_questions`)
3. Route `/checkin` + `POST /api/checkin` aanpassen (chips, generieke antwoorden)
4. Template opnieuw opbouwen (4 blokken, Grip-stijl, tap-chips)
5. AI-prompt aanscherpen in `insights.py` (`generate_checkin_question`)
6. Historie-pagina bijwerken
7. Testen (lokaal met testdata + visueel) → deploy

## Open keuzes

1. **Trackers volledig uit de check-in?** Voorstel: alleen handmatige/boolean
   trackers blijven, als compacte chip-regel. Alternatief: alles eruit.
2. **Aantal rotatievragen per dag:** 2 (voorstel) of liever 1 of 3?
3. **Toon van de AI-vraag:** mag hij confronterend zijn ("je stelde dit 2× uit")
   of vooral positief-motiverend?
