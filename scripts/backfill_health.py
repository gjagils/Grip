#!/usr/bin/env python3
"""
Backfill van Apple Health-data naar Grip.

Parseert de officiële Health-export van de iPhone en stuurt de laatste N dagen
als multi-day payload naar /api/health/sync.

Export maken op de iPhone:
  Gezondheid-app → profielfoto rechtsboven → "Exporteer alle gezondheidsgegevens"
  → deel de zip naar je Mac (AirDrop / Bestanden).

Gebruik:
  python3 scripts/backfill_health.py ~/Downloads/export.zip --days 15 \
      --token JOUW_SYNC_TOKEN [--dry-run]

De token kan ook via de env-var HEALTH_SYNC_TOKEN. Alleen Python-stdlib nodig.
"""
import argparse
import json
import os
import sys
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

# HealthKit-type → (Grip-veld, aggregatie)
# Aggregaties:
#   sum_by_source — per dag per bron optellen, dan max over bronnen
#                   (iPhone + Watch registreren dezelfde stappen dubbel)
#   sum           — alles optellen (voedings-apps overlappen niet)
#   last          — laatste meting van de dag (gewicht)
QUANTITY_TYPES = {
    "HKQuantityTypeIdentifierStepCount":            ("steps",            "sum_by_source"),
    "HKQuantityTypeIdentifierActiveEnergyBurned":   ("active_calories",  "sum_by_source"),
    "HKQuantityTypeIdentifierAppleExerciseTime":    ("exercise_minutes", "sum_by_source"),
    "HKQuantityTypeIdentifierDistanceWalkingRunning": ("distance_km",    "sum_by_source"),
    "HKQuantityTypeIdentifierDietaryEnergyConsumed": ("kcal",            "sum"),
    "HKQuantityTypeIdentifierBodyMass":             ("weight",           "last"),
}
CATEGORY_TYPES = {
    "HKCategoryTypeIdentifierSleepAnalysis": "sleep_hours",
    "HKCategoryTypeIdentifierAppleStandHour": "stand_hours",
}

INT_FIELDS = {"steps", "active_calories", "exercise_minutes", "kcal", "stand_hours"}


def convert_value(value: float, unit: str, field: str) -> float:
    unit = (unit or "").lower()
    if field == "distance_km":
        if unit == "mi":
            return value * 1.60934
        if unit == "m":
            return value / 1000
    if field == "weight" and unit in ("lb", "lbs"):
        return value * 0.453592
    if field in ("active_calories", "kcal") and unit == "kj":
        return value / 4.184
    return value


def parse_export(xml_file, cutoff: str):
    """
    Eén pass door export.xml. Geeft dicts terug met tussenstanden per
    (dag, veld) — de uiteindelijke aggregatie gebeurt in aggregate().
    """
    # (day, field, source) → som          voor sum_by_source
    by_source = defaultdict(float)
    # (day, field) → som                  voor sum
    plain_sum = defaultdict(float)
    # (day, field) → (startdatum-tijd, waarde)  voor last
    last_val = {}
    # (day, source) → slaapseconden       slaap per bron, dag = wakker-datum
    sleep_sec = defaultdict(float)
    # (day, source) → set van uren        stand hours per bron
    stand = defaultdict(set)

    n_records = 0
    for _, elem in ET.iterparse(xml_file, events=("end",)):
        if elem.tag != "Record":
            elem.clear()
            continue
        n_records += 1

        rtype = elem.get("type", "")
        start = elem.get("startDate", "")   # "2026-07-09 23:58:12 +0200"
        end = elem.get("endDate", "")
        source = elem.get("sourceName", "?")
        day = start[:10]

        if rtype in QUANTITY_TYPES:
            field, agg = QUANTITY_TYPES[rtype]
            if day >= cutoff:
                try:
                    value = convert_value(float(elem.get("value", "")), elem.get("unit"), field)
                except (TypeError, ValueError):
                    elem.clear()
                    continue
                if agg == "sum_by_source":
                    by_source[(day, field, source)] += value
                elif agg == "sum":
                    plain_sum[(day, field)] += value
                elif agg == "last":
                    key = (day, field)
                    if key not in last_val or start > last_val[key][0]:
                        last_val[key] = (start, value)

        elif rtype == "HKCategoryTypeIdentifierSleepAnalysis":
            # Slaap hoort bij de dag waarop je wakker wordt (einddatum)
            wake_day = end[:10]
            if wake_day >= cutoff and "Asleep" in elem.get("value", ""):
                try:
                    from datetime import datetime
                    t0 = datetime.strptime(start, "%Y-%m-%d %H:%M:%S %z")
                    t1 = datetime.strptime(end, "%Y-%m-%d %H:%M:%S %z")
                    sleep_sec[(wake_day, source)] += (t1 - t0).total_seconds()
                except ValueError:
                    pass

        elif rtype == "HKCategoryTypeIdentifierAppleStandHour":
            if day >= cutoff and elem.get("value", "").endswith("Stood"):
                stand[(day, source)].add(start[:13])  # uniek per uur

        elem.clear()

    return by_source, plain_sum, last_val, sleep_sec, stand, n_records


def aggregate(by_source, plain_sum, last_val, sleep_sec, stand) -> dict[str, dict]:
    """Combineer tussenstanden tot {dag: {veld: waarde}}."""
    days: dict[str, dict] = defaultdict(dict)

    # max over bronnen per (dag, veld)
    best = {}
    for (day, field, _source), total in by_source.items():
        key = (day, field)
        best[key] = max(best.get(key, 0.0), total)
    for (day, field), total in best.items():
        days[day][field] = total

    for (day, field), total in plain_sum.items():
        days[day][field] = total

    for (day, field), (_ts, value) in last_val.items():
        days[day][field] = value

    sleep_best = {}
    for (day, _source), seconds in sleep_sec.items():
        sleep_best[day] = max(sleep_best.get(day, 0.0), seconds)
    for day, seconds in sleep_best.items():
        days[day]["sleep_hours"] = seconds / 3600

    stand_best = {}
    for (day, _source), hours in stand.items():
        stand_best[day] = max(stand_best.get(day, 0), len(hours))
    for day, count in stand_best.items():
        days[day]["stand_hours"] = count

    # Afronden
    for day, fields in days.items():
        for field, value in fields.items():
            fields[field] = round(value) if field in INT_FIELDS else round(value, 2)
    return days


def open_export(path: Path):
    """Accepteert export.zip of een losse export.xml."""
    if path.suffix == ".zip":
        zf = zipfile.ZipFile(path)
        for name in zf.namelist():
            if name.endswith("export.xml") and "cda" not in name.lower():
                print(f"Gevonden in zip: {name}")
                return zf.open(name)
        sys.exit("Geen export.xml gevonden in de zip.")
    return open(path, "rb")


def main():
    parser = argparse.ArgumentParser(description="Backfill Apple Health-export naar Grip")
    parser.add_argument("export", type=Path, help="Pad naar export.zip of export.xml")
    parser.add_argument("--days", type=int, default=15, help="Aantal dagen terug (t/m gisteren, default 15)")
    parser.add_argument("--url", default="https://grip.gerdjan.nl/api/health/sync")
    parser.add_argument("--token", default=os.environ.get("HEALTH_SYNC_TOKEN", ""))
    parser.add_argument("--dry-run", action="store_true", help="Toon payload, verstuur niets")
    args = parser.parse_args()

    if not args.export.exists():
        sys.exit(f"Bestand niet gevonden: {args.export}")
    if not args.token and not args.dry_run:
        sys.exit("Geen token: geef --token mee of zet env-var HEALTH_SYNC_TOKEN.")

    yesterday = date.today() - timedelta(days=1)
    cutoff = (yesterday - timedelta(days=args.days - 1)).isoformat()
    print(f"Periode: {cutoff} t/m {yesterday.isoformat()}")

    print("Export parsen (kan even duren bij een grote export)...")
    with open_export(args.export) as f:
        by_source, plain_sum, last_val, sleep_sec, stand, n = parse_export(f, cutoff)
    print(f"{n:,} records gelezen.")

    days = aggregate(by_source, plain_sum, last_val, sleep_sec, stand)
    # Alleen de gevraagde periode (t/m gisteren — vandaag is nog niet compleet)
    entries = [{"date": d, **fields} for d, fields in sorted(days.items())
               if cutoff <= d <= yesterday.isoformat()]

    if not entries:
        sys.exit("Geen data gevonden in deze periode.")

    print(f"\n{len(entries)} dag(en) gevonden:")
    for e in entries:
        rest = {k: v for k, v in e.items() if k != "date"}
        print(f"  {e['date']}: {rest}")

    if args.dry_run:
        print("\n--dry-run: niets verstuurd.")
        return

    payload = json.dumps({"entries": entries}).encode()
    req = urllib.request.Request(
        args.url, data=payload, method="POST",
        headers={"Content-Type": "application/json", "X-Sync-Token": args.token},
    )
    print(f"\nVersturen naar {args.url} ...")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        sys.exit(f"Server weigerde ({e.code}): {e.read().decode(errors='replace')}")

    if not body.get("ok"):
        sys.exit(f"Fout van server: {body}")
    synced_days = sum(1 for r in body.get("results", []) if r.get("synced"))
    print(f"Klaar: {body.get('days')} dagen verwerkt, {synced_days} met gesyncte waarden.")


if __name__ == "__main__":
    main()
