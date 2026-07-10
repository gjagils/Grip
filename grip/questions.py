import random
from datetime import date

import aiosqlite


async def get_reflection_questions(db: aiosqlite.Connection, for_date: date | None = None) -> list[dict]:
    """
    Kies de twee reflectievragen van vandaag: één terugblik + één vooruitblik.

    Rotatie: per pool wordt de volgorde geshuffeld met een seed per cyclus, en
    lopen we die volgorde dag voor dag af. Een vraag komt dus pas terug als de
    hele pool geweest is, en dezelfde dag geeft altijd dezelfde vragen.
    """
    if for_date is None:
        for_date = date.today()
    day = for_date.toordinal()

    result = []
    for kind in ("back", "forward"):
        cursor = await db.execute(
            "SELECT id, text, kind FROM reflection_questions WHERE kind = ? AND active = 1 ORDER BY id",
            (kind,),
        )
        pool = [dict(r) for r in await cursor.fetchall()]
        if not pool:
            continue
        n = len(pool)
        cycle = day // n
        order = random.Random(f"{kind}-{cycle}").sample(range(n), n)
        if n > 1:
            # Geen directe herhaling op een cyclusgrens: als de eerste vraag van
            # deze cyclus gelijk is aan de laatste van de vorige, wissel hem om.
            prev_order = random.Random(f"{kind}-{cycle - 1}").sample(range(n), n)
            if order[0] == prev_order[-1]:
                order[0], order[1] = order[1], order[0]
        result.append(pool[order[day % n]])
    return result


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
