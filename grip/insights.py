import os
from datetime import date, datetime, timedelta

import aiosqlite
from anthropic import AsyncAnthropic

client = AsyncAnthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))

SYSTEM_PROMPT = """Je bent een persoonlijke coach en accountability partner. Je analyseert check-in data, weekreviews en doelen van de gebruiker.

Je stijl:
- Direct en eerlijk, maar bemoedigend
- Je wijst op patronen en trends
- Je stelt vervolgvragen om dieper te graven
- Je herinnert de gebruiker aan zijn eigen doelen en uitspraken
- Je geeft concrete, actionable suggesties
- Je schrijft in het Nederlands

Je hebt toegang tot de check-in geschiedenis en doelen van de gebruiker. Gebruik deze data om je antwoorden te onderbouwen."""

CHAT_SYSTEM_PROMPT = """Je bent de vaste accountability partner van de gebruiker in de Grip app. Dit is een doorlopend gesprek — je bouwt voort op alles wat eerder besproken is.

Je karakter:
- Je onthoudt wat je eerder hebt besproken en verwijst er actief naar terug
- Je signaleert patronen: energiedips op bepaalde dagen, terugkerende blokkades, trends in scores
- Je verbindt dagelijkse ervaringen aan de langetermijndoelen die je kent
- Je daagt uit zonder te pushen — je stelt de ongemakkelijke vraag als dat nodig is
- Je viert vooruitgang, ook de kleine
- Je schrijft in het Nederlands, informeel maar scherp
- Geen opsommingslijsten tenzij het echt helpt — schrijf als een mens, niet als een rapport

Je hebt live toegang tot de Grip data van de gebruiker. Gebruik die data actief in je antwoorden."""


async def _build_context(db: aiosqlite.Connection, days: int = 30) -> str:
    """Bouw context op van recente check-ins en doelen voor de LLM."""
    parts = []

    # Recente check-ins
    since = (date.today() - timedelta(days=days)).isoformat()
    cursor = await db.execute(
        """
        SELECT ci.date, q.text, q.type, ca.answer_text, ca.answer_score
        FROM check_in_answers ca
        JOIN check_ins ci ON ca.check_in_id = ci.id
        JOIN questions q ON ca.question_id = q.id
        WHERE ci.date >= ?
        ORDER BY ci.date DESC, q.is_core DESC
        """,
        (since,),
    )
    rows = await cursor.fetchall()

    if rows:
        parts.append("## Recente check-ins")
        current_date = None
        for r in rows:
            if r["date"] != current_date:
                current_date = r["date"]
                parts.append(f"\n### {current_date}")
            answer = r["answer_score"] if r["type"] == "score" else r["answer_text"]
            parts.append(f"- {r['text']}: {answer}")

    # Recente weekreviews
    cursor = await db.execute(
        """
        SELECT year, week_number, score, went_well, improve, on_track_goals, priorities_next_week
        FROM week_reviews
        ORDER BY year DESC, week_number DESC
        LIMIT 4
        """,
    )
    reviews = await cursor.fetchall()

    if reviews:
        parts.append("\n## Recente weekreviews")
        for r in reviews:
            parts.append(f"\n### Week {r['week_number']} ({r['year']})")
            if r["score"]:
                parts.append(f"- Score: {r['score']}/10")
            if r["went_well"]:
                parts.append(f"- Ging goed: {r['went_well']}")
            if r["improve"]:
                parts.append(f"- Verbeteren: {r['improve']}")
            if r["priorities_next_week"]:
                parts.append(f"- Prioriteiten: {r['priorities_next_week']}")

    # Actieve doelen met taken
    cursor = await db.execute(
        "SELECT id, title, description, type, quarter, year FROM goals WHERE status = 'active' ORDER BY type, year, quarter"
    )
    goals = await cursor.fetchall()

    if goals:
        parts.append("\n## Actieve doelen")
        for g in goals:
            label = f"{g['type']} {g['year']}"
            if g["quarter"]:
                label += f" {g['quarter']}"
            parts.append(f"- [{label}] {g['title']}")
            if g["description"]:
                parts.append(f"  {g['description']}")
            # Taken bij doel
            cursor = await db.execute(
                "SELECT title, completed FROM goal_tasks WHERE goal_id = ? ORDER BY sort_order",
                (g["id"],),
            )
            tasks = await cursor.fetchall()
            for t in tasks:
                status = "x" if t["completed"] else " "
                parts.append(f"  [{status}] {t['title']}")

    # Tracker data
    cursor = await db.execute(
        """
        SELECT t.name, t.unit, te.date, te.value
        FROM tracker_entries te
        JOIN trackers t ON te.tracker_id = t.id
        WHERE te.date >= ?
        ORDER BY t.name, te.date DESC
        """,
        (since,),
    )
    tracker_rows = await cursor.fetchall()

    if tracker_rows:
        parts.append("\n## Tracker data")
        current_tracker = None
        for r in tracker_rows:
            name = f"{r['name']}" + (f" ({r['unit']})" if r["unit"] else "")
            if name != current_tracker:
                current_tracker = name
                parts.append(f"\n### {name}")
            parts.append(f"- {r['date']}: {r['value']}")

    return "\n".join(parts) if parts else "Nog geen data beschikbaar."


async def ask(db: aiosqlite.Connection, question: str) -> str:
    """Stel een vraag aan Claude met de check-in context."""
    context = await _build_context(db)

    response = await client.messages.create(
        model="claude-sonnet-4-5-20250929",
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": f"Hier is mijn recente data:\n\n{context}\n\n---\n\nMijn vraag: {question}",
            }
        ],
    )

    return response.content[0].text


async def reflect_checkin(db: aiosqlite.Connection) -> str:
    """Haiku reflectie op de check-in van vandaag."""
    today = date.today().isoformat()
    now = datetime.now()
    year, week, _ = now.isocalendar()

    cursor = await db.execute(
        """
        SELECT q.text, q.type, ca.answer_text, ca.answer_score
        FROM check_in_answers ca
        JOIN check_ins ci ON ca.check_in_id = ci.id
        JOIN questions q ON ca.question_id = q.id
        WHERE ci.date = ?
        ORDER BY q.is_core DESC
        """,
        (today,),
    )
    rows = await cursor.fetchall()

    parts = [f"## Check-in {today}"]
    for r in rows:
        answer = r["answer_score"] if r["type"] == "score" else r["answer_text"]
        if answer is not None:
            parts.append(f"- {r['text']}: {answer}")

    cursor = await db.execute(
        "SELECT title FROM goals WHERE type = 'weekly' AND status = 'active' AND year = ? AND week_number = ?",
        (year, week),
    )
    goals = await cursor.fetchall()
    if goals:
        parts.append("\n## Weekdoelen")
        for g in goals:
            parts.append(f"- {g['title']}")

    context = "\n".join(parts)

    response = await client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=512,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": f"{context}\n\n---\nReflecteer op deze check-in. Geef 2-3 korte, scherpe observaties en één concrete vraag die me aan het denken zet.",
            }
        ],
    )
    return response.content[0].text


async def reflect_weekreview(db: aiosqlite.Connection) -> str:
    """Haiku reflectie op de weekreview van deze week."""
    now = datetime.now()
    year, week, _ = now.isocalendar()

    cursor = await db.execute(
        "SELECT * FROM week_reviews WHERE year = ? AND week_number = ?", (year, week)
    )
    current = await cursor.fetchone()
    if not current:
        return "Geen weekreview gevonden voor deze week."

    cursor = await db.execute(
        "SELECT * FROM week_reviews ORDER BY year DESC, week_number DESC LIMIT 3"
    )
    all_reviews = await cursor.fetchall()

    cursor = await db.execute(
        "SELECT title, type FROM goals WHERE status = 'active' ORDER BY type"
    )
    goals = await cursor.fetchall()

    parts = [f"## Weekreview week {week} ({year})"]
    if current["score"]:
        parts.append(f"- Score: {current['score']}/10")
    if current["went_well"]:
        parts.append(f"- Ging goed: {current['went_well']}")
    if current["improve"]:
        parts.append(f"- Verbeteren: {current['improve']}")
    if current["priorities_next_week"]:
        parts.append(f"- Prioriteiten: {current['priorities_next_week']}")

    prev = [r for r in all_reviews if not (r["year"] == year and r["week_number"] == week)][:2]
    if prev:
        parts.append("\n## Vorige weken (context)")
        for r in prev:
            parts.append(f"\n### Week {r['week_number']} ({r['year']})")
            if r["score"]:
                parts.append(f"- Score: {r['score']}/10")
            if r["went_well"]:
                parts.append(f"- Ging goed: {r['went_well']}")
            if r["improve"]:
                parts.append(f"- Verbeteren: {r['improve']}")

    if goals:
        parts.append("\n## Actieve doelen")
        for g in goals:
            parts.append(f"- [{g['type']}] {g['title']}")

    context = "\n".join(parts)

    response = await client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=512,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": f"{context}\n\n---\nReflecteer op deze weekreview. Geef 2-3 scherpe observaties over patronen of trends en stel één gerichte vraag voor de komende week.",
            }
        ],
    )
    return response.content[0].text


async def build_chat_system(db: aiosqlite.Connection) -> str:
    """Bouw het systeem-prompt voor de chat met verse Grip data erin."""
    context = await _build_context(db, days=30)
    today = date.today().isoformat()
    return f"{CHAT_SYSTEM_PROMPT}\n\n## Grip data van vandaag ({today})\n\n{context}"


async def load_chat_history(db: aiosqlite.Connection, limit: int = 40) -> list[dict]:
    """Laad de laatste N berichten als messages-array voor de API."""
    cursor = await db.execute(
        "SELECT role, content FROM chat_messages ORDER BY id DESC LIMIT ?", (limit,)
    )
    rows = await cursor.fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


async def reflect_quarterly(db: aiosqlite.Connection, review: dict) -> str:
    """Sonnet reflectie op de kwartaalreview."""
    now = datetime.now()
    year, _, _ = now.isocalendar()
    quarter = (now.month - 1) // 3 + 1

    parts = [f"## Kwartaalreview Q{quarter} {year}"]

    if review.get("highlights_proud"):
        parts.append(f"\n### Trots op / blij mee\n{review['highlights_proud']}")
    if review.get("highlights_bad"):
        parts.append(f"\n### Geen goed gevoel over\n{review['highlights_bad']}")
    if review.get("goals_review"):
        parts.append(f"\n### Doelen terugkijken\n{review['goals_review']}")

    cats = [
        ("Werk", "cat_werk"), ("Relatie & gezin", "cat_relatie"),
        ("Familie", "cat_familie"), ("Vrienden", "cat_vrienden"),
        ("Gezondheid", "cat_gezondheid"), ("Vaardigheden", "cat_vaardigheden"),
        ("Sideprojects", "cat_sideprojects"), ("Plezier", "cat_plezier"),
        ("Geld – inkomen", "cat_geld_inkomen"), ("Geld – sparen", "cat_geld_sparen"),
        ("Geld – geven", "cat_geld_geven"),
    ]
    filled = [(label, review[key]) for label, key in cats if review.get(key)]
    if filled:
        parts.append("\n### Review per categorie")
        for label, text in filled:
            parts.append(f"\n**{label}:** {text}")

    if review.get("quarter_reflection"):
        parts.append(f"\n### Terugkijken op kwartaal\n{review['quarter_reflection']}")
    if review.get("new_goals"):
        parts.append(f"\n### Nieuwe doelen\n{review['new_goals']}")
    if review.get("outlook"):
        parts.append(f"\n### Vooruitblik\n{review['outlook']}")

    # Actieve doelen als context
    cursor = await db.execute(
        "SELECT title, type FROM goals WHERE status = 'active' ORDER BY type"
    )
    goals = await cursor.fetchall()
    if goals:
        parts.append("\n### Actieve doelen (context)")
        for g in goals:
            parts.append(f"- [{g['type']}] {g['title']}")

    context = "\n".join(parts)

    response = await client.messages.create(
        model="claude-sonnet-4-5-20250929",
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": f"{context}\n\n---\nReflecteer op deze kwartaalreview. Geef scherpe observaties over patronen en blinde vlekken. Stel 2-3 krachtige vragen die helpen bij het formuleren van doelen voor het nieuwe kwartaal.",
            }
        ],
    )
    return response.content[0].text


async def export_week_markdown(db: aiosqlite.Connection) -> str:
    """Exporteer de laatste 2 weken als markdown voor Claude.ai Projects."""
    now = datetime.now()
    year, week, _ = now.isocalendar()
    context = await _build_context(db, days=14)
    header = f"# Grip Export — Week {week} ({year})\nGegenereerd: {date.today().isoformat()}\n\n"
    return header + context
