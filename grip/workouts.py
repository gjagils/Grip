"""
Workout-parser: zet een chatbericht en/of foto van een trainingsschema om naar een
gestructureerde workout die de iOS-app (GripWorkout) met WorkoutKit in de
Apple Workout-app zet.

Endpoint: POST /api/workouts/parse
Auth:     zelfde X-Sync-Token header als de health-endpoints (HEALTH_SYNC_TOKEN).

Request body:
    {
      "messages": [
        {"role": "user", "text": "3 x 400 m op 4:30/km, 2 min wandelen", "images": ["<base64 jpeg>"]},
        {"role": "assistant", "text": "<raw JSON van een eerder antwoord>"},
        ...
      ]
    }
Alleen de laatste user-turn hoeft afbeeldingen te bevatten; de app stuurt eerdere
foto's niet opnieuw mee (scheelt kosten). De uitgelezen workout staat dan al in
de assistant-turns.

Response:
    {"ok": true, "reply": "...", "workout": {...} | null, "raw": "<JSON string>"}
"""

import json
import logging
import os
from datetime import date

from anthropic import AsyncAnthropic
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter()

client = AsyncAnthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))

DEFAULT_MODEL = "claude-opus-5-5"

MAX_IMAGES = 5
MAX_IMAGE_B64_CHARS = 7_000_000  # ~5 MB na decoderen (API-limiet per afbeelding)


def _model() -> str:
    # Lege string (onbekende Portainer-variabele) telt als niet gezet.
    return os.environ.get("WORKOUT_MODEL") or DEFAULT_MODEL


# --- Schema ---------------------------------------------------------------

def _nullable(schema: dict) -> dict:
    return {"anyOf": [schema, {"type": "null"}]}


STEP_SCHEMA = {
    "type": "object",
    "properties": {
        "purpose": {
            "type": "string",
            "enum": ["work", "recovery"],
            "description": "work = inspanning, recovery = herstel (wandelen, dribbelen, rust). "
                           "Voor warming-up en cooling-down maakt dit niet uit; gebruik 'work'.",
        },
        "goal_type": {"type": "string", "enum": ["distance", "time", "open"]},
        "distance_m": _nullable({"type": "number"}),
        "duration_s": _nullable({"type": "number"}),
        "pace_fast_s_per_km": _nullable({
            "type": "number",
            "description": "Snelste kant van het tempobereik in seconden per km (4:20/km = 260).",
        }),
        "pace_slow_s_per_km": _nullable({
            "type": "number",
            "description": "Langzaamste kant van het tempobereik in seconden per km.",
        }),
        "heart_rate_zone": _nullable({"type": "integer", "description": "Hartslagzone 1-5."}),
        "label": {"type": "string", "description": "Korte omschrijving, bv. '400 m snel'."},
    },
    "required": [
        "purpose", "goal_type", "distance_m", "duration_s",
        "pace_fast_s_per_km", "pace_slow_s_per_km", "heart_rate_zone", "label",
    ],
    "additionalProperties": False,
}

WORKOUT_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "Korte naam, zichtbaar in de Workout-app."},
        "activity": {"type": "string", "enum": ["running", "walking", "cycling", "hiking"]},
        "location": {"type": "string", "enum": ["outdoor", "indoor"]},
        "warmup": _nullable(STEP_SCHEMA),
        "blocks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "iterations": {"type": "integer"},
                    "steps": {"type": "array", "items": STEP_SCHEMA},
                },
                "required": ["iterations", "steps"],
                "additionalProperties": False,
            },
        },
        "cooldown": _nullable(STEP_SCHEMA),
    },
    "required": ["name", "activity", "location", "warmup", "blocks", "cooldown"],
    "additionalProperties": False,
}

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "reply": {
            "type": "string",
            "description": "Kort antwoord in het Nederlands voor de gebruiker.",
        },
        "workout": _nullable(WORKOUT_SCHEMA),
    },
    "required": ["reply", "workout"],
    "additionalProperties": False,
}


SYSTEM_PROMPT = """Je zet trainingsbeschrijvingen om naar een gestructureerde workout voor de Apple Workout-app (WorkoutKit) op de Apple Watch. De gebruiker is Nederlandstalig.

De invoer is een chatbericht, een foto (bijvoorbeeld een trainingsschema van een loopgroep of coach, een screenshot of een whiteboard), of allebei. Over meerdere berichten kan de gebruiker een eerdere workout bijsturen ("maak er 4 van", "iets rustiger"). Geef dan steeds de volledige, bijgewerkte workout terug.

Zo bouw je een workout op:
- warmup: optioneel, één stap. Alleen opnemen als de gebruiker of de foto erom vraagt, of als het schema er duidelijk een bevat.
- blocks: één of meer herhaalblokken. Elk blok heeft iterations (het aantal herhalingen) en steps (work- en recovery-stappen, in volgorde).
- cooldown: optioneel, één stap. Zelfde regel als bij warmup.
- Een stap heeft een doel: distance (distance_m in meters), time (duration_s in seconden) of open (geen doel; de gebruiker tikt zelf door). Vul alleen het veld in dat bij goal_type hoort en zet het andere op null.
- Tempo: pace_fast_s_per_km en pace_slow_s_per_km in seconden per km. Bij één tempo ("op 4:30") neem je een bereik van ongeveer ±5 seconden (265 tot 275). Rekent de gebruiker in min/mijl, reken dan om naar per km. Een snelheid in km/u reken je ook om naar tempo per km.
- heart_rate_zone: alleen invullen als er een zone wordt genoemd. Gebruik niet tegelijk een tempo- én een hartslagalert in één stap; kies dan tempo.
- Wandel- of rustherstel is een recovery-stap zonder tempo, tenzij er expliciet een tempo bij staat.
- Activiteit: hardlopen = running, wandelen = walking, fietsen = cycling, wandeltocht/trail = hiking. Op een loopband of binnen is location indoor, anders outdoor.

Voorbeeld: "3 x 400 m rennen op 4:30/km met 2 min wandelen ertussen" wordt één blok met iterations 3 en twee stappen: work 400 m met tempo 265–275, en recovery 120 s.

reply: kort (1-3 zinnen), in het Nederlands. Vat de workout samen zoals jij hem hebt begrepen, en noem aannames die je zelf hebt gemaakt.
Als de invoer echt te onduidelijk is om er een workout van te maken, of geen training beschrijft, zet je workout op null en stel je in reply een gerichte vraag. Ontbreekt er alleen een detail, maak dan een redelijke aanname en noem die."""


# --- Helpers --------------------------------------------------------------

def _sniff_media_type(b64: str) -> str:
    head = b64[:16]
    if head.startswith("iVBOR"):
        return "image/png"
    if head.startswith("R0lGOD"):
        return "image/gif"
    if head.startswith("UklGR"):
        return "image/webp"
    return "image/jpeg"


def _build_messages(raw_messages: list) -> list[dict]:
    messages: list[dict] = []
    for m in raw_messages:
        role = m.get("role")
        text = (m.get("text") or "").strip()
        if role == "assistant":
            if text:
                messages.append({"role": "assistant", "content": text})
            continue
        if role != "user":
            continue

        content: list[dict] = []
        for img in (m.get("images") or [])[:MAX_IMAGES]:
            if not isinstance(img, str) or not img or len(img) > MAX_IMAGE_B64_CHARS:
                continue
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": _sniff_media_type(img), "data": img},
            })
        if text:
            content.append({"type": "text", "text": text})
        elif content:
            content.append({"type": "text", "text": "Maak hier een workout van."})
        if content:
            messages.append({"role": "user", "content": content})
    return messages


def _normalize_step(step: dict | None) -> dict | None:
    if not step:
        return None
    s = dict(step)
    fast, slow = s.get("pace_fast_s_per_km"), s.get("pace_slow_s_per_km")
    if fast and not slow:
        s["pace_fast_s_per_km"], s["pace_slow_s_per_km"] = fast - 5, fast + 5
    elif slow and not fast:
        s["pace_fast_s_per_km"], s["pace_slow_s_per_km"] = slow - 5, slow + 5
    elif fast and slow and fast > slow:
        s["pace_fast_s_per_km"], s["pace_slow_s_per_km"] = slow, fast
    if s.get("goal_type") == "distance" and not (s.get("distance_m") or 0) > 0:
        s["goal_type"] = "open"
    if s.get("goal_type") == "time" and not (s.get("duration_s") or 0) > 0:
        s["goal_type"] = "open"
    if s.get("heart_rate_zone") is not None and not 1 <= s["heart_rate_zone"] <= 5:
        s["heart_rate_zone"] = None
    return s


def _normalize_workout(w: dict | None) -> dict | None:
    if not w:
        return None
    w = dict(w)
    w["warmup"] = _normalize_step(w.get("warmup"))
    w["cooldown"] = _normalize_step(w.get("cooldown"))
    blocks = []
    for b in w.get("blocks") or []:
        steps = [_normalize_step(s) for s in b.get("steps") or []]
        steps = [s for s in steps if s]
        if steps:
            blocks.append({"iterations": max(1, int(b.get("iterations") or 1)), "steps": steps})
    w["blocks"] = blocks
    if not blocks:
        return None
    return w


# --- Endpoint -------------------------------------------------------------

@router.post("/api/workouts/parse")
async def parse_workout(request: Request):
    # Lazy import om een circulaire import met grip.web te voorkomen.
    from grip.web import _sync_auth_error

    if (err := _sync_auth_error(request)) is not None:
        return err

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "Ongeldige JSON"}, status_code=400)

    messages = _build_messages(body.get("messages") or [])
    if not messages or messages[-1]["role"] != "user":
        return JSONResponse({"ok": False, "error": "Laatste bericht moet van de gebruiker zijn"}, status_code=400)

    system = SYSTEM_PROMPT + f"\n\nVandaag is het {date.today().isoformat()}."

    try:
        response = await client.beta.messages.create(
            model=_model(),
            max_tokens=16000,
            system=system,
            messages=messages,
            output_config={
                "effort": "medium",
                "format": {"type": "json_schema", "schema": RESPONSE_SCHEMA},
            },
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    except Exception as e:  # noqa: BLE001 — fout netjes terug naar de app
        logger.exception("workouts/parse — Claude-aanroep mislukt")
        return JSONResponse({"ok": False, "error": f"Claude-aanroep mislukt: {e}"}, status_code=502)

    if response.stop_reason == "refusal":
        return JSONResponse({"ok": False, "error": "Claude weigerde dit verzoek."}, status_code=422)
    if response.stop_reason == "max_tokens":
        return JSONResponse({"ok": False, "error": "Antwoord werd afgekapt."}, status_code=502)

    raw = next((b.text for b in response.content if b.type == "text"), "")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.error("workouts/parse — geen geldige JSON terug: %r", raw[:500])
        return JSONResponse({"ok": False, "error": "Onverwacht antwoord van Claude"}, status_code=502)

    workout = _normalize_workout(data.get("workout"))
    logger.info(
        "workouts/parse — model=%s in=%s out=%s workout=%s",
        response.model, response.usage.input_tokens, response.usage.output_tokens, bool(workout),
    )
    return {"ok": True, "reply": data.get("reply", ""), "workout": workout, "raw": raw}
