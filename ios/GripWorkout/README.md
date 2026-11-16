# Workout Chat (GripWorkout)

iPhone-app waarin je een training beschrijft of een foto van je schema stuurt. Claude maakt er een
workout van, en met één tik staat die in de Apple Workout-app op je Watch.

```
iPhone-app ──(tekst/foto)──▶ Grip /api/workouts/parse ──▶ Claude
     ▲                                   │
     └────────── workout-JSON ◀──────────┘
     │
     └─▶ WorkoutKit ─▶ Workout-app / Apple Watch
```

Je kunt de workout bijsturen in de chat ("maak er 4 van", "iets rustiger", "voeg 10 min inlopen toe").

## Vereisten

- Mac met Xcode 15 of nieuwer
- iPhone met iOS 17+ en een Apple Watch met watchOS 10+
- Apple Developer-account
- Tailscale aan op je iPhone (Grip draait op je Synology)

## Installeren

1. Installeer XcodeGen en genereer het project:

   ```bash
   brew install xcodegen
   ```

   ```bash
   cd ios/GripWorkout && xcodegen generate
   ```

2. Open `GripWorkout.xcodeproj`. Kies onder *Signing & Capabilities* je team. Is de bundle-ID al bezet,
   pas dan `PRODUCT_BUNDLE_IDENTIFIER` aan in `project.yml` en voer `xcodegen generate` opnieuw uit.
3. Sluit je iPhone aan, kies hem als doel en druk op ▶︎ (Run).
4. Vul in de app bij Instellingen in:
   - **Server**: het adres van Grip, bijvoorbeeld `http://<tailscale-ip-synology>:8921`
   - **Token**: de waarde van `HEALTH_SYNC_TOKEN` (leeg laten als je die niet gebruikt)

## Gebruik

- Typ bijvoorbeeld *"3 x 400 m rennen op 4:30/km, 2 min wandelen ertussen"*, of kies of maak een foto.
- **Naar Workout-app** opent Apple's eigen voorbeeldscherm. Van daaruit voeg je de workout toe aan de
  Workout-app op je Watch.
- **Inplannen** zet de workout op een datum en tijd. Hij staat dan klaar in de Workout-app op je Watch.

Een tempo wordt een tempo-alert met een bereik (één tempo krijgt ±5 s/km). Zorg dat je Watch tempo toont
in plaats van snelheid (Watch-app > Workout > Tempo/snelheid).

## Model en kosten

Standaard gebruikt het backend `claude-opus-5-5`, ongeveer 1 à 4 cent per workout. Goedkoper kan door in
Portainer de stack-variabele `WORKOUT_MODEL` op `claude-sonnet-5-5` of `claude-haiku-5-5` te zetten.

## Bekende aandachtspunten

- De code is niet gecompileerd in de omgeving waarin hij is geschreven. Geeft Xcode een fout in
  `WorkoutBuilder.swift` of `WorkoutCard.swift` (WorkoutKit-API), dan is dat meestal een kleine
  aanpassing aan een initializer, of een ontbrekende `try` bij `WorkoutScheduler.schedule`.
- De token staat in `@AppStorage` (UserDefaults). Prima voor een persoonlijke app; anders verplaatsen naar
  de Keychain.
- Ideeën voor later: een share-extensie (foto vanuit Foto's of WhatsApp direct "delen naar Workout Chat")
  en zwemworkouts (die vragen in WorkoutKit een andere locatie: zwembad of open water).
