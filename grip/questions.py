import random
from datetime import date

import aiosqlite


async def get_daily_questions(db: aiosqlite.Connection, for_date: date | None = None) -> list[dict]:
    """Selecteer de vragen voor vandaag: alle kernvragen + wisselende vragen uit de pool."""
    if for_date is None:
        for_date = date.today()

    # Gebruik de datum als seed zodat dezelfde dag dezelfde vragen geeft
    seed = int(for_date.strftime("%Y%m%d"))

    # Haal alle actieve dagelijkse vragen op
    cursor = await db.execute(
        "SELECT id, text, type, is_core FROM questions WHERE category = 'daily' AND active = 1"
    )
    rows = await cursor.fetchall()

    core = [dict(r) for r in rows if r["is_core"]]
    pool = [dict(r) for r in rows if not r["is_core"]]

    # Kies 3-5 wisselende vragen uit de pool
    rng = random.Random(seed)
    n_extra = min(rng.randint(3, 5), len(pool))
    extra = rng.sample(pool, n_extra)

    return core + extra


async def get_weekly_questions(db: aiosqlite.Connection) -> list[dict]:
    """Haal alle weekreview vragen op."""
    cursor = await db.execute(
        "SELECT id, text, type, is_core FROM questions WHERE category = 'weekly' AND active = 1"
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]
