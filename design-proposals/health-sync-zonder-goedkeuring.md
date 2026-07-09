# Voorstel: Health-sync zonder dagelijkse goedkeuring

**Datum:** 2026-07-09
**Probleem:** De Shortcuts-automation die Apple Health-data naar `/api/health/sync` stuurt vraagt dagelijks om goedkeuring op de iPhone (of faalt stilletjes). Doel: volledig automatische sync.

---

## Waarom de automation nu hapert

Twee bekende iOS-oorzaken, los van elkaar:

1. **"Vraag voor uitvoeren" staat aan.** Bij een tijd-gebaseerde automation kan dit sinds iOS 17 uit: open de automation in de Shortcuts-app → kies **"Direct uitvoeren"** en zet **"Melden bij uitvoeren"** uit.
2. **Health-data is versleuteld zolang de telefoon vergrendeld is.** Een automation om 07:00 terwijl de telefoon op het nachtkastje ligt, leest dan lege waarden (vandaar ook de nullen die de server al moet wegfilteren). Workaround: trigger op een moment dat je de telefoon gebruikt, of gebruik de trigger **"Wekker gestopt"**.

→ **Stap 0 (gratis, 5 minuten):** deze twee instellingen proberen vóór er iets gebouwd wordt. Grote kans dat dit het al oplost.

## Optie 1 — Health Auto Export app (aanbevolen als stap 0 niet genoeg is)

De App Store-app **"Health Auto Export — JSON+CSV"** doet precies wat je zelf wilt bouwen: op de achtergrond HealthKit-data verzamelen en periodiek als JSON naar een REST-endpoint POSTen. Geen goedkeuring, geen Shortcuts.

- **Kosten:** eenmalige aankoop / premium-tier voor REST-automations (~€25), geen Apple Developer-account nodig.
- **Bereikbaarheid:** Tailscale-app op de iPhone installeren zodat de app `http://100.65.249.84:...` (Synology) kan bereiken — geen poort naar internet open.
- **Werk in Grip:** één nieuw endpoint `/api/health/import/hae` dat het Health Auto Export-formaat (`{"data": {"metrics": [{"name", "units", "data": [{"date", "qty"}]}]}}`) vertaalt naar de bestaande `_upsert_health_entry`-logica. Geschat: ~60 regels Python, hergebruikt alles wat er al staat (multi-day, nullen negeren, slaapconversie).

**Voordeel:** dagen werk bespaard, betrouwbaarder dan Shortcuts, en de app regelt zelf backfill van gemiste dagen.

## Optie 2 — Eigen mini-companion-app (SwiftUI)

Een kale iOS-app die alleen HealthKit uitleest en naar het bestaande `/api/health/sync`-endpoint POST (het multi-day formaat bestaat al, dus server-side is er *niets* nodig).

- **Techniek:** SwiftUI + HealthKit, `HKObserverQuery` met background delivery voor automatische sync, plus een "Sync nu"-knop als vangnet. ~300 regels Swift.
- **Maar:** een blijvende installatie op je eigen telefoon vereist een **Apple Developer-account (€99/jaar)** — met een gratis account verloopt de app elke 7 dagen en moet je opnieuw installeren via Xcode. Dat is in de praktijk een dealbreaker, tenzij je dat account toch al wilt.
- Background delivery is bij iOS "best effort": iOS bepaalt wanneer de app wakker wordt. Voor dagelijkse data prima, maar niet stipter dan de Health Auto Export-app.

**Conclusie:** leuk project, maar je betaalt €99/jaar om iets na te bouwen wat optie 1 voor ~€25 eenmalig doet.

## Optie 3 — Heel Grip als native app: afgeraden

Het enige dat een native app toevoegt boven de huidige PWA is HealthKit-toegang — en dat lossen optie 1 of 2 gerichter op. Heel Grip native maken betekent:

- De complete Jinja2/vanilla-JS UI opnieuw bouwen in Swift (of wrappen in Capacitor).
- App Store review of TestFlight-gedoe bij elke wijziging, in plaats van de huidige push-naar-main-en-klaar pipeline.
- €99/jaar developer-account sowieso.

De PWA blijft de juiste vorm voor Grip; alleen de *data-aanvoer* heeft een native oplossing nodig.

## Los hiervan: beveiliging van het endpoint

`/api/health/sync` heeft nu **geen enkele authenticatie**. Binnen Tailscale is dat acceptabel, maar zodra een app (of Grip zelf) van buitenaf bereikbaar wordt: een shared secret toevoegen.

- Server: check op header `X-Sync-Token` tegen env-var `HEALTH_SYNC_TOKEN`.
- Client (Shortcut of app): header meesturen.
- Zonder env-var blijven de endpoints open (backward compatible, Tailscale-modus).

## Implementatiestatus (2026-07-09)

Gebouwd en getest:

- **`POST /api/health/import/hae`** — adapter voor het Health Auto Export-formaat. Aggregeert datapunten per dag (som; gewicht: gemiddelde), converteert eenheden (mi→km, lb→kg, kJ→kcal, min→uur voor slaap) en slaat op via dezelfde logica als `/api/health/sync`. Onbekende metrics worden gerapporteerd in de response (`unknown_metrics`).
- **`X-Sync-Token`-check** op `/api/health/sync`, `/api/health/status` en het nieuwe endpoint. Actief zodra de env-var `HEALTH_SYNC_TOKEN` gezet is.
- `docker-compose.yml` geeft `HEALTH_SYNC_TOKEN` door aan de container.

Nog te doen (handmatig, eenmalig):

1. **Portainer:** in de Grip-stack de environment-variabele `HEALTH_SYNC_TOKEN` toevoegen met een zelfgekozen lange random string. (De deploy-workflow hergebruikt de bestaande stack-env, dus dit hoeft níet in GitHub Secrets.)
2. **iPhone — Shortcuts (stap 0):** automation op "Direct uitvoeren" zetten, en de `X-Sync-Token` header toevoegen aan de "Get contents of URL"-actie.
3. **iPhone — als je voor Health Auto Export kiest:** Tailscale-app installeren, in Health Auto Export een REST API-automation aanmaken naar `http://100.65.249.84:8921/api/health/import/hae` met header `X-Sync-Token`, export-formaat JSON, aggregatie "Days", en de metrics stappen/energie/beweegminuten/staande uren/slaap/afstand/gewicht aanvinken.

## Aanbevolen route

| Stap | Actie | Kosten | Effort |
|------|-------|--------|--------|
| 0 | Automation op "Direct uitvoeren" + trigger "Wekker gestopt" | €0 | 5 min |
| 1 | Werkt stap 0 niet betrouwbaar → Health Auto Export + adapter-endpoint + Tailscale op iPhone | ~€25 eenmalig | ~1 uur bouwen |
| 2 | `X-Sync-Token`-check toevoegen (kan altijd, ook nu al) | €0 | ~15 min |

Eigen app (optie 2) alleen overwegen als je toch een Apple Developer-account neemt; heel Grip native (optie 3) niet doen.
