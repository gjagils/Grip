# Opzet: gezondheids-app (iOS + web) als opvolger van Grip

**Datum:** 2026-08-25
**Status:** voorstel — nog niet gebouwd
**Doel:** één systeem dat fysieke gezondheid, mentale gezondheid, werkdruk en de doel-/reviewcyclus samenbrengt, met een native iOS-app voor de data-aanvoer en een web-interface voor het denkwerk.

---

## 1. Uitgangspunten

Vastgesteld in de vragenronde:

| Besluit | Keuze | Gevolg |
|---|---|---|
| Verhouding tot Grip | Nieuwe app **vervangt** Grip | Nieuw datamodel en nieuwe UI; het chassis (FastAPI, SQLite, Actions→Portainer→Synology) blijft |
| Apple Developer | Al aanwezig | Native SwiftUI + HealthKit + TestFlight beschikbaar |
| Werkdata | Handmatig in v1, Microsoft 365 als step-up | Zie §8 — afspraakvrije uren kunnen wél meteen automatisch |
| Hosting | Synology + Tailscale | Gezondheidsdata verlaat het huis niet; VPN-afhankelijkheid moet expliciet opgevangen |
| Interfaces | iOS-app **en** web | Backend wordt een echte JSON-API met twee clients |
| Wearable | Nog open: band zonder abonnement óf Oura/Whoop | **Geen Apple Watch.** Zonder abonnement loopt alles via HealthKit; met abonnement via de cloud-API van de fabrikant — twee verschillende datapaden, zie §3 |

**Waarom het chassis blijft.** "Grip vervangen" gaat over het product, niet over de deploy-pipeline. De GitHub Actions-workflow, het ghcr.io-image, de Tailscale-hop naar Portainer en de stack op de Synology werken al. Die opnieuw opbouwen kost dagen en levert niets op. Wat wél weggaat: de Jinja-PWA, het trackers-als-health-opslag-hack, en de huidige check-in-flow.

---

## 2. Wat het wordt

Drie componenten, één waarheid.

```mermaid
graph TB
    subgraph iPhone
        A[iOS-app · SwiftUI]
        HK[(HealthKit)]
        EK[(EventKit · agenda)]
        OB[Outbox · lokale queue]
        HK --> A
        EK --> A
        A --> OB
    end

    subgraph Synology · achter Tailscale
        API[FastAPI · JSON-API]
        DB[(SQLite)]
        ANA[Analyse-laag · deterministisch]
        LLM[Claude · prefill en gesprek]
        API --> DB
        DB --> ANA
        ANA --> LLM
        LLM --> DB
    end

    B[Web-interface · review-werkbank]

    OB -->|deltas, met retry| API
    B <--> API
    API -->|ntfy push| A
```

**iOS-app — de zintuigen.** Leest HealthKit en de agenda, doet de dagelijkse check-in, toont een compact dagbeeld en een widget. Kort in gebruik: seconden, niet minuten.

**Web-interface — de werkbank.** Waar je écht nadenkt: jaardoelen, kwartaaldoelen, weekreviews, grafieken over lange periodes, het gesprek met Claude over je eigen data. Dat doe je op een toetsenbord, niet op een telefoon.

**Backend — het geheugen en het verstand.** Eén database, één set analyses, twee clients die dezelfde API praten.

---

## 3. Waarom native de sync inderdaad simpel maakt

Je intuïtie klopt, en het verschil is groter dan je denkt. Nu loopt Health-data via Shortcuts of Health Auto Export: dagtotalen, best effort, stilletjes falend, en alleen wat een Shortcut kan uitlezen. Native verandert dat fundamenteel.

**HealthKit met anchors en background delivery**

- `HKAnchoredObjectQuery` geeft je per meettype een *anchor*: een bookmark in HealthKit. Je vraagt "alles sinds deze anchor" en krijgt precies de nieuwe en gewijzigde samples terug. Sla de anchor op → nooit dubbel, nooit een gat.
- `HKObserverQuery` + `enableBackgroundDelivery(for:frequency:)` wekt de app zodra er nieuwe data is. Geen automation, geen goedkeuring, geen "vraag voor uitvoeren".
- Eerste run: backfill van 2 jaar in chunks van een maand. Klaar in een paar minuten.

**Wat je erbij krijgt dat nu ontbreekt**

| Categorie | Nu (Shortcuts/HAE) | Native |
|---|---|---|
| Slaap | totaal aantal uren | fases: diep, REM, kern, wakker + bedtijd/opstaan → *slaapregelmaat* |
| Hart | — | HRV (SDNN), rust-hartslag, hartslagherstel, VO2max |
| Workouts | — | type, duur, energie, afstand, gemiddelde/max hartslag, HR-zones per training |
| Ademhaling | — | ademfrequentie tijdens slaap, zuurstofsaturatie |
| Mentaal | — | State of Mind (iOS 17+), mindful minutes |
| Verversing | 1× per dag | continu, binnen minuten na de meting |

**Workouts specifiek.** `HKWorkout` levert het type (`HKWorkoutActivityType` — hardlopen, kracht, fietsen, …), start/eind, energie en afstand. Daarnaast kun je per workout de hartslagsamples ophalen en daaruit zones en een belastingsscore berekenen. Dat is het verschil tussen "je hebt 45 minuten bewogen" en "je deed een zware duurloop terwijl je HRV al twee dagen onder je baseline zat".

### De band bepaalt wat er binnenkomt

Zonder Apple Watch is de app afhankelijk van wat de fabrikant van de band naar HealthKit schrijft — en dat is bijna altijd minder dan de doos belooft. "Werkt met Apple Health" zegt niets; wat telt is *welke HealthKit-types* er landen.

Voor dit ontwerp zijn er drie die ertoe doen, in deze volgorde:

| HealthKit-type | Waarvoor | Zonder dit |
|---|---|---|
| `sleepAnalysis` **met fases** | slaapkwaliteit en slaapregelmaat | alleen slaapduur — de zwakste van alle slaapmaten |
| `heartRateVariabilitySDNN` | belangrijkste hersteldriver in de Lichaam-score | de score leunt volledig op slaap en rust-HR |
| `restingHeartRate` | tweede hersteldriver, trage trend | geen tegenwicht tegen een ruisige HRV |

Steps, calorieën en beweegminuten zijn nadrukkelijk de *minst* waardevolle metrics in dit hele plan. Een band die stappen perfect telt maar slaap als één ongedifferentieerd blok doorgeeft, is voor deze app een slechte band.

**Acceptatietest bij aanschaf, één nacht:** draag hem, en kijk de volgende ochtend in Apple Gezondheid onder *Slaap* of je een fase-verdeling ziet (diep / REM / kern / wakker) en onder *Hart → HRV* of er een nachtwaarde staat. Staan die er niet, dan is de band ongeschikt — hoe goed zijn eigen app ook is.

### De harde bevinding: HRV landt nooit in HealthKit

Uitgezocht per fabrikant, en de uitkomst is opvallend consistent: **geen enkele derde partij schrijft HRV naar Apple Health.**

| Apparaat | Slaapfases → HealthKit | HRV → HealthKit | Officiële API | Kosten |
|---|---|---|---|---|
| **Garmin CIRQA** | ja | **nee** | nee — alleen onofficieel | **€199 eenmalig** |
| Oura Ring 4 | ja | **nee** | ja, OAuth v2 | ~$349 + $70/jr |
| Whoop 5.0 | sessie, geen fases | **nee** (rMSSD vs SDNN) | ja, v2 | $199/jr, hardware inbegrepen |
| Amazfit Helio Strap | nee — één blok | gedeeltelijk | nee | ~$99 |
| Apple Watch SE | ja, native | **ja, native** | n.v.t. | ~$249 |

Apple's eigen Watch is het enige apparaat dat `heartRateVariabilitySDNN` zelf wegschrijft. Voor al het andere geldt: HealthKit is de bodem, niet het plafond.

**Ontwerpgevolg:** de architectuur mag niet aannemen dat HealthKit het universele pad is. Er zijn er twee, en ze vullen elkaar aan:

- **HealthKit als duurzame bodem.** Slaapfases, rust-hartslag, stappen, workouts. Werkt altijd, breekt nooit, vereist niets van een fabrikant.
- **Een tweede pad voor de rest.** HRV, readiness, huidtemperatuur. Hoe dat pad eruitziet verschilt per merk — zie hieronder.

Valt het tweede pad weg, dan degradeert de Lichaam-score naar slaap plus rust-hartslag. Minder scherp, maar niet stuk. Dat is bewust: geen enkele metric mag een single point of failure zijn.

### Tweede route: rechtstreeks uit de cloud van de fabrikant

Bij abonnementsapparaten (Oura, Whoop) is HealthKit juist de *slechtste* route. Beide houden hun beste data achter: Oura schrijft wel slaapfases naar Apple Health maar **geen HRV en geen rust-hartslag**; Whoop schrijft **helemaal geen HRV** omdat Apple SDNN opslaat en Whoop rMSSD meet.

Beide hebben wél een gratis ontwikkelaars-API. Dat geeft een vierde datapad, en voor die data een beter:

```
Synology  ──nachtelijke pull met refresh token──►  Oura / Whoop cloud
```

| | Via HealthKit | Via de cloud-API |
|---|---|---|
| Telefoon nodig | ja | **nee** |
| Tailnet nodig | ja | nee — server belt uit |
| Outbox nodig | ja | nee |
| Volledigheid | wat de fabrikant deelt | **alles wat ze meten**, incl. readiness en huidtemperatuur |
| Afhankelijkheid | HealthKit-bridge | OAuth refresh token; API kan veranderen |

Voor die apparaten verdwijnt de hele sync-problematiek uit §3 dus: de Synology haalt 's nachts zelf op. De iOS-app blijft nodig voor de agenda (EventKit), de check-in en het dagbeeld, maar niet meer als koerier.

**Garmin heeft geen persoonlijke API.** Het Connect Developer Program eist een rechtspersoon en wijst privéaanvragen af. Wat wél werkt is `python-garminconnect`: een onderhouden Python-bibliotheek die met je eigen inloggegevens hartslag, slaap, stress, SpO2, HRV en Body Battery ophaalt. Onofficieel, en het is een kat-en-muisspel — Garmin schroefde in maart 2026 de botdetectie aan, waarna de bibliotheek zijn login op `curl_cffi` herbouwde en het weer deed.

Voor een privéproject op je eigen NAS is dat een aanvaardbaar risico, mits je het goed inricht: de bibliotheek levert de *verrijking* (HRV, Body Battery), HealthKit levert de *bodem* (slaapfases, rust-HR). Breekt de bibliotheek, dan mis je scherpte, geen data.

**De valkuil: Tailscale.** De app kan de NAS alleen bereiken als het tailnet up is. Achtergrondsync terwijl de VPN uit staat mislukt — en dat mag niet stil gebeuren. Oplossing:

1. **Outbox.** Elke delta gaat eerst in een lokale SQLite-queue. Verzenden is een aparte stap; mislukt hij, dan blijft het item staan.
2. **Retry.** Bij elke app-start, elke background-wake en elke succesvolle netwerkwijziging wordt de queue leeggewerkt.
3. **Watchdog.** De server kijkt dagelijks of er data binnenkwam en stuurt anders een ntfy-push. Die logica staat er al (`grip/notify.py`) en verhuist mee.
4. **Zichtbaarheid.** De app toont "laatste sync: 14 min geleden" en hoeveel items in de wachtrij staan. Nooit meer raden.

Een tweede reden dat HealthKit soms niets teruggeeft: **health-data is versleuteld zolang de telefoon vergrendeld is na een herstart**. Background delivery levert pas na de eerste ontgrendeling. Dat is geen bug om weg te programmeren maar een eigenschap om omheen te ontwerpen — vandaar de outbox in plaats van "verzenden of verloren".

---

## 4. Datamodel

Weg met health-data-in-trackers. Aparte, getypeerde tabellen:

```
health_daily(date PK, steps, active_kcal, exercise_min, stand_hours,
             distance_km, weight_kg, resting_hr, hrv_sdnn, vo2max,
             respiratory_rate, mindful_min, updated_at)

sleep_nights(date PK, bedtime, waketime, total_min, deep_min, rem_min,
             core_min, awake_min, efficiency, source)

workouts(id PK, hk_uuid UNIQUE, start_at, end_at, activity_type,
         duration_min, energy_kcal, distance_km, avg_hr, max_hr,
         zone1_min … zone5_min, perceived_effort, note, source)

work_daily(date PK, mail_open, mail_unread, tasks_open, tasks_done,
           meeting_hours, meeting_count, focus_hours, longest_focus_min,
           source CHECK(source IN ('manual','eventkit','graph','todoist')))

baselines(metric, date, mean_7d, mean_28d, sd_28d, z_score)
daily_scores(date PK, body, mind, work, composite, drivers_json, computed_at)

check_ins(date PK, mood, energy, stress, focus, …, notes, …)
goals(id, title, type CHECK(type IN ('yearly','quarterly','weekly')), …)
week_reviews / quarter_reviews / year_reviews
review_drafts(review_type, review_key, field, draft_text, final_text,
              accepted, generated_at)

sync_state(device_id, sample_type, anchor_blob, last_seen_at)
```

Drie dingen die de moeite waard zijn:

- **`hk_uuid UNIQUE` op workouts.** HealthKit levert stabiele UUID's; een `INSERT OR REPLACE` daarop maakt de hele sync idempotent. Je kunt dezelfde delta drie keer sturen zonder schade.
- **`baselines` als eigen tabel.** Nachtelijk berekend, niet on-the-fly. Dat maakt de web-UI snel en de analyses reproduceerbaar.
- **`review_drafts` bewaart zowel `draft_text` als `final_text`.** Zie §7 — dit is niet alleen boekhouding.

**Migratie.** De bestaande historie gaat mee: check-ins, doelen, weekreviews, kwartaalreviews, en tracker-entries met health-namen (Stappen, Slaap, Gewicht, …) worden omgezet naar `health_daily`. Handgemaakte trackers blijven bestaan als vrije trackers. Eenmalig script, draaien vóór de cutover.

---

## 5. De analyse-laag: twee lagen, streng gescheiden

Dit is de belangrijkste ontwerpbeslissing in het hele plan.

**Laag 1 — deterministisch (Python, geen LLM).** Berekent baselines en afwijkingen: 7-daags versus 28-daags voortschrijdend gemiddelde, z-scores per metric, streaks, trendrichting, correlaties over een langere periode. Output is een compacte *feitenkaart*:

```
Week 34 · feiten
  Slaap        6u12 gem (28d: 7u04)   z = −1.8   ▼ vierde week op rij dalend
  HRV          38 ms  (28d: 47 ms)    z = −1.4   ▼
  Rust-HR      58 bpm (28d: 54)       z = +1.1   ▲
  Workouts     4 (2 zwaar) · 312 min · belasting 118% van 4-weeksgemiddelde
  Stemming     3.4 / 5 (28d: 3.9)     z = −1.2
  Afspraakvrij 9,5 u over 5 dagen (28d-gem: 14,2 u)  ▼
  Mail open    47 (maandag 31)        ▲
  Doelen       2/3 kwartaaldoelen on track; "3× sporten" 4 weken gehaald
```

**Laag 2 — Claude.** Krijgt de feitenkaart plus jouw eigen woorden (check-in-notities, vorige reviews, doelen) en schrijft de conceptreview. **Claude krijgt nooit de ruwe database** — alleen de feitenkaart. Daardoor kan hij geen getallen verzinnen: elk cijfer in zijn tekst is er één dat de deterministische laag heeft berekend.

Dat is het verschil tussen een app die je vertrouwt en een app die af en toe overtuigend onzin schrijft over je eigen gezondheid.

De feitenkaart is bovendien op zichzelf al waardevol: hij is leesbaar, testbaar, en werkt ook als de Anthropic-API onbereikbaar is.

---

## 6. Scores: drie, niet één

Eén samengesteld getal is verleidelijk en vervlakt precies waar het interessant wordt — een goede week met slechte slaap en een slechte week met goede slaap kunnen dezelfde 72 opleveren.

Daarom drie subscores, elk 0–100 **relatief aan jouw eigen baseline**, niet aan populatienormen:

- **Lichaam** — slaapduur + slaapregelmaat, HRV t.o.v. baseline, rust-hartslag, trainingsbelasting versus herstel.
- **Hoofd** — je eigen check-in-scores (stemming, energie, stress, focus) plus proxies: slaapregelmaat, HRV-dip, hoe laat je laatste activiteit van de dag was, aantal aaneengesloten dagen zonder afspraakvrije tijd.
- **Werk** — mail-backlog, open versus afgeronde taken, afspraakvrije uren, vergaderdichtheid.

Een composiet mag als klein kopgetal, maar altijd met de drie eronder zichtbaar.

**Harde regel voor de UI:** nooit een score tonen zonder de twee drivers die hem bewogen. "Lichaam 61 (−12) — slaap 4e week dalend, HRV onder baseline" is bruikbaar. "61" is een horoscoop.

**Mentale gezondheid, eerlijk gezegd.** Proxies meten *belasting*, niet gemoedstoestand. Slaap, HRV en agenda-dichtheid voorspellen redelijk wanneer het mís gaat, maar ze weten niet hoe je je voelt. De check-in blijft dus de primaire bron; de proxies zijn een tweede mening die mag tegenspreken. Als de app zegt "je hoofd staat op 78" terwijl jij je beroerd voelt, heeft de app ongelijk — en dat moet je met één tik kunnen vastleggen, want juist die momenten zijn de interessante datapunten.

---

## 7. Reviewcyclus en prefill

De cascade loopt twee kanten op:

```
Jaardoelen  ──►  Kwartaaldoelen  ──►  Weekintenties  ──►  Dagfocus
     ▲                 ▲                    ▲                │
     └── bewijs ───────┴──── bewijs ────────┴─── bewijs ─────┘
```

**Dagelijks (iOS, < 60 sec).** Stemming/energie/stress/focus, één regel over gisteren, focus voor vandaag, en — als je met handmatige werkdata werkt — drie getallen. Health-data staat er al in; daar hoef je niets voor te doen.

**Wekelijks (web, zondagochtend).** Een geplande job draait zaterdagnacht: feitenkaart + doelvoortgang + de intenties die je vorige week uitsprak → Claude schrijft een concept.

**Kwartaal.** Aggregeert 13 weekreviews, doeluitkomsten en gezondheidstrends per levenscategorie (werk, relatie, familie, vrienden, gezondheid, vaardigheden, side projects, plezier, geld) — die indeling staat al in Grip en verdient het om mee te gaan.

**Jaarlijks.** Aggregeert vier kwartalen, met de gezondheidstrend over 12 maanden ernaast.

### Hoe ver de prefill mag gaan

Voorstel: **de app vult feiten en patronen in, jij vult betekenis in.**

| Veld | Wie |
|---|---|
| "Wat gebeurde er deze week" (cijfers, workouts, doelvoortgang) | app, kant en klaar |
| "Wat viel op" (patronen, afwijkingen, correlaties) | Claude, als concept |
| "Wat ging goed / wat wil ik anders" | **leeg** — jij |
| "Prioriteiten volgende week" | Claude stelt voor, jij kiest |

Concepten verschijnen visueel onderscheiden (grijs, cursief) met per veld accepteren / bewerken / verwerpen.

**Waarom `review_drafts` beide versies bewaart.** Als je na een half jaar terugkijkt en ziet dat je 90% van Claude's concepten ongewijzigd accepteert, is dat geen succes — dan ben je aan het afvinken in plaats van reflecteren. Die acceptatiegraad is zelf een signaal dat de app je mag laten zien. Het grootste risico van dit hele plan is niet dat de AI het fout doet; het is dat hij het zó plausibel doet dat je stopt met nadenken.

---

## 8. Werkdata

**Afspraakvrije uren — automatisch vanaf dag één.** Dit hoeft níet te wachten op de Graph-API. Als je werkagenda op je iPhone staat (Outlook-account in iOS Agenda, of de Outlook-app met agenda-toegang), leest de native app hem via **EventKit** — precies zoals hij HealthKit leest. Eén permissiedialoog, geen OAuth, geen app-registratie, geen toestemming van je werkgever.

Daaruit rolt automatisch: vergaderuren per dag, aantal afspraken, afspraakvrije uren binnen werktijd, langste aaneengesloten focusblok, en het aandeel dagen zonder enig blok van 90+ minuten. Dat laatste is waarschijnlijk het meest voorspellende werkcijfer dat je kunt hebben.

Nodig: welke agenda's meetellen, wat je werktijden zijn, of hele-dag-afspraken meetellen, en de minimumlengte van een "focusblok".

**Mail en taken — handmatig in v1.** Hier is geen native truc voor. De ontwerpregel: als het meer dan tien seconden kost, hou je het geen drie weken vol. Dus geen formulier maar drie steppers in de dagelijkse check-in, plus een Home Screen-widget waarop je de getallen rechtstreeks kunt tikken.

Wat "open mails" precies is, moet je zelf definiëren — ongelezen, alles in de inbox, of mails ouder dan drie dagen zonder antwoord. Alleen de derde meet echt iets; de eerste twee meten vooral je opruimgedrag.

**Step-up naar Microsoft 365.** Delegated permissions `Mail.Read` + `Calendars.Read` via een eigen app-registratie in Entra ID, auth code flow, refresh token server-side op de Synology zodat de tellingen ook doorlopen als je telefoon uit staat. Het risico is niet technisch maar organisatorisch: veel werk-tenants blokkeren app-registraties door gebruikers. Dat is één minuut uitzoeken en bepaalt of deze fase überhaupt kan.

> Je noemde "een versie die op mijn telefoon draait die je zou kunnen gebruiken" — ik weet niet zeker wat je bedoelt (de Outlook-app? een eigen tool?). Dat is een van de open vragen onderaan.

**Todoist** is een makkelijke extra: schone API, token, open/afgeronde taken per dag in een paar regels. Je hebt de koppeling al draaien. Zeg het maar als je die in v1 wilt.

---

## 9. Web-interface

Bewust saai gehouden: **server-rendered Jinja2 + vanilla JS**, net als nu, met een lokaal meegeleverde grafiekbibliotheek (uPlot of Chart.js — vendored, geen CDN). Geen npm-buildstap, één Docker-image, dezelfde deploy als vandaag.

Reden: het enige dat een SPA hier toevoegt is soepelere interactie, en daar staat een buildpipeline, een tweede dependency-boom en een tweede deploy-artefact tegenover. Voor een privé-app op een NAS is dat een slechte ruil. De iOS-app haalt zijn data uit dezelfde JSON-API, dus de logica ligt sowieso in de backend.

Pagina's: **Vandaag** (dagbeeld + scores + drivers), **Trends** (elke metric over week/maand/jaar, met baselines), **Reviews** (week/kwartaal/jaar-werkbank), **Doelen** (jaar → kwartaal → week, met voortgang uit de data), **Gesprek** (Claude met toegang tot je feitenkaarten).

---

## 10. Beveiliging

Tailscale doet het zware werk, maar health- en mentale data verdienen een tweede slot:

- **iOS-app:** per-device token, aangemaakt bij het koppelen, opgeslagen in de Keychain. Vervangt de huidige gedeelde `X-Sync-Token`, zodat je één toestel kunt intrekken.
- **Web:** sessie-cookie achter een wachtwoord. Iemand anders op je tailnet is niet automatisch jou.
- **API:** alle schrijf-endpoints achter auth, geen uitzonderingen "voor het gemak".
- **Backups:** SQLite met WAL op een Synology-volume met snapshots; wekelijkse versleutelde dump naar een tweede locatie. Twee jaar gezondheidsdata is niet reproduceerbaar.

---

## 11. Deploy en distributie

**Backend:** ongewijzigd. Push naar main → GitHub Actions bouwt het image → ghcr.io → runner joint het tailnet → Portainer API → stack update op de Synology.

**iOS:** Archive in Xcode → TestFlight (interne tester = jijzelf, geen App Store-review). Over-the-air updates, versiehistorie, crashlogs.

Let op één ding: **TestFlight-builds verlopen na 90 dagen.** Een development-signed build via Xcode houdt het een jaar vol maar moet je met een kabel installeren. Praktisch compromis: TestFlight voor het gemak, en de rebuild plannen op je kwartaalreview — dan valt het onderhoud samen met een moment dat je toch in de app zit.

---

## 12. Fasering

| Fase | Wat | Resultaat dat je kunt zien | Ruwe omvang |
|---|---|---|---|
| **0** | Besluiten, repo-indeling, schema, API-contract, migratiescript | Bestaande historie draait op het nieuwe model | klein |
| **1** | Backend v2 + kale iOS-app: permissies, anchored queries, background delivery, outbox | 2 jaar Health-historie inclusief slaapfases, HRV en workouts stroomt binnen | groot |
| **2** | Deterministische analyse (baselines, z-scores, subscores) + web-UI Vandaag/Trends | Je ziet je eigen patronen, zonder AI | middel |
| **3** | Reviewcyclus + doelen + AI-prefill (week → kwartaal → jaar) | Zondagochtend ligt er een concept klaar | groot |
| **4** | EventKit-agenda + handmatige mail/taken + widget | Werkdruk zit in het beeld | middel |
| **5** | Microsoft Graph, Apple Watch-app, Live Activity, complicaties | Volledig automatisch | open |

Fase 1 en 2 zijn samen al een bruikbaar product: je krijgt data die je nu niet hebt, en analyses die je nu niet hebt. Fase 3 is waar het echt jouw app wordt.

**Cutover.** Grip blijft draaien tot fase 3 af is. Daarna één keer migreren en de oude stack uitzetten. Niet eerder — je wilt niet halverwege zonder check-in-plek zitten.

---

## 13. Risico's, eerlijk

| Risico | Ernst | Aanpak |
|---|---|---|
| Sync mislukt stil door VPN | hoog | Outbox + retry + watchdog-push + zichtbare sync-status |
| Handmatige werkdata verwatert binnen weken | hoog | Widget met steppers; als het na een maand niet beklijft, schrappen we het cijfer in plaats van onszelf voor de gek te houden |
| Prefill maakt reviews passief | hoog | Reflectievelden blijven leeg; acceptatiegraad wordt zelf gemeten en getoond |
| Background delivery is best-effort | middel | Nooit op timing rekenen; alles is eventually consistent |
| Werk-tenant blokkeert app-registratie | middel | Eerst uitzoeken (5 min) vóór fase 5 wordt ingepland |
| Scope-explosie: dit is drie apps in één | middel | Fasering hierboven; fase 1+2 moeten zelfstandig waarde hebben |
| Twee jaar data op één NAS | middel | Snapshots + versleutelde offsite dump |

---

## 14. Open vragen

1. **Welk apparaat wordt het?** Vastgesteld: geen Apple Watch. Aanbeveling: **Garmin CIRQA** (€199, geen abonnement) — meet alles wat dit ontwerp nodig heeft, is de goedkoopste van het veld, en kan op de bovenarm gedragen worden waar de hartslagmeting volgens onafhankelijke tests een borstband benadert. Zie de tabel in §3 voor het alternatief als je toch een abonnement wilt.
2. **Welke metrics zeggen jóu iets?** Ik kan alles binnenhalen, maar het dashboard moet klein blijven. Noem de vijf die je écht wilt zien.
3. Wat bedoelde je met **"een versie die op mijn telefoon draait"** bij de werkdata?
4. **Werktijden en agenda's:** welke uren tellen als werkdag, welke agenda's meenemen, tellen hele-dag-afspraken mee, en vanaf hoeveel minuten is iets een focusblok?
5. **Definitie "open mails"** — ongelezen, inbox-totaal, of onbeantwoord ouder dan X dagen?
6. **Check-in-moment:** ochtend (vooruitkijken) of avond (terugkijken)? Dat verandert de vragen.
7. **Naam.** "Grip" hergebruiken of een nieuwe naam? Repo en stack kunnen hoe dan ook blijven heten zoals ze heten.
8. **Todoist meenemen in v1?** Het is goedkoop en je hebt de koppeling al.
9. **Andere bronnen** naast Apple Health — Strava, Whoop, Oura, Garmin?
