"""Push-notificaties (ntfy) + dagelijkse sync-watchdog.

De server stuurt niet rechtstreeks naar de telefoon: hij POST't een bericht naar
een ntfy-topic. ntfy bewaart het en Apple's push (APNs) levert het zodra de
telefoon weer online is — dus 'telefoon uit/offline' is geen probleem.

Config via env-vars (Portainer-secrets):
  NTFY_URL   volledige topic-URL, bv. https://ntfy.sh/grip-a1b2c3   (verplicht)
  NTFY_TOKEN optioneel bearer-token voor beveiligde/self-hosted ntfy
  WATCHDOG_HOUR   uur (0-23) waarop de dagcheck draait, default 21
"""

import asyncio
import logging
import os
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from grip.database import get_app_state, get_db

logger = logging.getLogger("grip.notify")

TZ = ZoneInfo("Europe/Amsterdam")
LAST_SYNC_KEY = "last_sync_received_at"


def _now() -> datetime:
    return datetime.now(TZ)


def send_push(title: str, message: str, priority: str = "default",
              tags: str | None = None) -> bool:
    """Stuur een push via ntfy. Blocking (urllib) — roep aan via asyncio.to_thread.
    Retourneert True bij succes, False als niet-geconfigureerd of bij fout."""
    url = os.environ.get("NTFY_URL")
    if not url:
        logger.warning("push overgeslagen — NTFY_URL niet gezet: %s", title)
        return False

    headers = {
        "Title": title.encode("utf-8"),
        "Priority": priority,
    }
    if tags:
        headers["Tags"] = tags
    token = os.environ.get("NTFY_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(
        url, data=message.encode("utf-8"), headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            ok = 200 <= resp.status < 300
            if ok:
                logger.info("push verstuurd: %s", title)
            else:
                logger.error("push mislukt (HTTP %s): %s", resp.status, title)
            return ok
    except urllib.error.URLError as e:
        logger.error("push mislukt: %s", e)
        return False


async def send_push_async(title: str, message: str, priority: str = "default",
                          tags: str | None = None) -> bool:
    return await asyncio.to_thread(send_push, title, message, priority, tags)


async def _received_sync_today() -> bool:
    """True als er vandaag (Amsterdam-tijd) een geslaagde health-sync binnenkwam."""
    db = await get_db()
    try:
        raw = await get_app_state(db, LAST_SYNC_KEY)
    finally:
        await db.close()
    if not raw:
        return False
    try:
        received = datetime.fromisoformat(raw)
    except ValueError:
        return False
    if received.tzinfo is None:
        received = received.replace(tzinfo=TZ)
    return received.astimezone(TZ).date() == _now().date()


def _seconds_until(hour: int, minute: int = 0) -> float:
    now = _now()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


async def sync_watchdog_loop() -> None:
    """Draait elke dag rond WATCHDOG_HOUR:00 en pusht als er die dag geen
    health-sync binnenkwam — dan draaide de iPhone-Shortcut waarschijnlijk niet."""
    try:
        hour = int(os.environ.get("WATCHDOG_HOUR", "21"))
    except ValueError:
        hour = 21

    logger.info("sync-watchdog gestart — dagelijkse check om %02d:00 (Europe/Amsterdam)", hour)
    while True:
        await asyncio.sleep(_seconds_until(hour))
        try:
            if await _received_sync_today():
                logger.info("watchdog: sync vandaag ontvangen — geen melding")
            else:
                logger.warning("watchdog: GEEN sync vandaag — push versturen")
                await send_push_async(
                    title="Grip: geen health-sync vandaag",
                    message=(
                        "Er kwam vandaag geen health-data binnen. "
                        "De Shortcut 'Grip update' draaide waarschijnlijk niet — "
                        "draai 'm even handmatig."
                    ),
                    priority="high",
                    tags="warning",
                )
        except Exception:
            logger.exception("watchdog: fout tijdens dagcheck")
        # kleine marge zodat we niet twee keer binnen hetzelfde minuutvenster vuren
        await asyncio.sleep(60)
