import os
from datetime import date, timedelta

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
