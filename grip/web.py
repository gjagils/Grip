from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from grip.database import get_db, init_db
from grip.questions import get_daily_questions, get_weekly_questions
from grip import insights

BASE_DIR = Path(__file__).parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(title="Grip", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


# --- Dashboard ---


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    db = await get_db()
    try:
        today = date.today().isoformat()

        # Check of er vandaag al een check-in is
        cursor = await db.execute("SELECT id FROM check_ins WHERE date = ?", (today,))
        todays_checkin = await cursor.fetchone()

        # Huidige week check
        now = datetime.now()
        year, week, _ = now.isocalendar()
        cursor = await db.execute(
            "SELECT id FROM week_reviews WHERE year = ? AND week_number = ?",
            (year, week),
        )
        weeks_review = await cursor.fetchone()

        # Actieve doelen
        cursor = await db.execute(
            "SELECT id, title, type, quarter, year FROM goals WHERE status = 'active' ORDER BY type, year, quarter"
        )
        goals = [dict(r) for r in await cursor.fetchall()]

        # Streak berekenen
        cursor = await db.execute(
            "SELECT date FROM check_ins WHERE completed = 1 ORDER BY date DESC"
        )
        checkin_dates = [r["date"] for r in await cursor.fetchall()]
        streak = _calculate_streak(checkin_dates)

        return templates.TemplateResponse(
            "dashboard.html",
            {
                "request": request,
                "today": today,
                "has_checkin": todays_checkin is not None,
                "has_week_review": weeks_review is not None,
                "goals": goals,
                "streak": streak,
                "year": year,
                "week": week,
            },
        )
    finally:
        await db.close()


def _calculate_streak(dates: list[str]) -> int:
    if not dates:
        return 0
    streak = 0
    current = date.today()
    for d in dates:
        check_date = date.fromisoformat(d)
        if check_date == current:
            streak += 1
            current -= timedelta(days=1)
        elif check_date == current - timedelta(days=1):
            streak += 1
            current = check_date - timedelta(days=1)
        else:
            break
    return streak


# --- Dagelijkse Check-in ---


@app.get("/checkin", response_class=HTMLResponse)
async def checkin_page(request: Request):
    db = await get_db()
    try:
        today = date.today()
        today_str = today.isoformat()
        questions = await get_daily_questions(db, today)

        # Check of er al een check-in is voor vandaag
        cursor = await db.execute(
            "SELECT id FROM check_ins WHERE date = ?", (today_str,)
        )
        existing = await cursor.fetchone()

        # Dagelijkse taken voor vandaag
        cursor = await db.execute(
            "SELECT id, title, completed FROM daily_tasks WHERE date = ? ORDER BY created_at",
            (today_str,),
        )
        daily_tasks = [dict(r) for r in await cursor.fetchall()]

        # Actieve trackers
        cursor = await db.execute(
            "SELECT t.id, t.name, t.unit, t.type, te.value FROM trackers t "
            "LEFT JOIN tracker_entries te ON te.tracker_id = t.id AND te.date = ? "
            "WHERE t.active = 1 ORDER BY t.sort_order, t.id",
            (today_str,),
        )
        trackers = [dict(r) for r in await cursor.fetchall()]

        return templates.TemplateResponse(
            "checkin.html",
            {
                "request": request,
                "questions": questions,
                "today": today_str,
                "already_done": existing is not None,
                "daily_tasks": daily_tasks,
                "trackers": trackers,
            },
        )
    finally:
        await db.close()


@app.post("/api/checkin")
async def save_checkin(request: Request):
    db = await get_db()
    try:
        form = await request.form()
        today = date.today().isoformat()

        # Maak check-in aan (of gebruik bestaande)
        cursor = await db.execute("SELECT id FROM check_ins WHERE date = ?", (today,))
        row = await cursor.fetchone()
        if row:
            checkin_id = row["id"]
            # Verwijder oude antwoorden
            await db.execute(
                "DELETE FROM check_in_answers WHERE check_in_id = ?", (checkin_id,)
            )
        else:
            cursor = await db.execute(
                "INSERT INTO check_ins (date, completed) VALUES (?, 1)", (today,)
            )
            checkin_id = cursor.lastrowid

        # Sla antwoorden op
        for key, value in form.items():
            if not key.startswith("q_"):
                continue
            question_id = int(key.split("_")[1])
            qtype = form.get(f"type_{question_id}", "open")

            if qtype == "score" and value:
                await db.execute(
                    "INSERT INTO check_in_answers (check_in_id, question_id, answer_score) VALUES (?, ?, ?)",
                    (checkin_id, question_id, int(value)),
                )
            elif value:
                await db.execute(
                    "INSERT INTO check_in_answers (check_in_id, question_id, answer_text) VALUES (?, ?, ?)",
                    (checkin_id, question_id, value),
                )

        # Dagelijkse taken bijwerken
        for key, value in form.items():
            if key.startswith("task_"):
                task_id = int(key.split("_")[1])
                await db.execute(
                    "UPDATE daily_tasks SET completed = 1, check_in_id = ? WHERE id = ?",
                    (checkin_id, task_id),
                )

        # Tracker entries opslaan
        for key, value in form.items():
            if not key.startswith("tracker_") or not value:
                continue
            tracker_id = int(key.split("_")[1])
            await db.execute(
                """INSERT INTO tracker_entries (tracker_id, date, value)
                   VALUES (?, ?, ?)
                   ON CONFLICT(tracker_id, date) DO UPDATE SET value = ?""",
                (tracker_id, today, float(value), float(value)),
            )

        # Markeer als compleet
        await db.execute(
            "UPDATE check_ins SET completed = 1 WHERE id = ?", (checkin_id,)
        )
        await db.commit()

        return RedirectResponse("/", status_code=303)
    finally:
        await db.close()


@app.get("/checkin/history", response_class=HTMLResponse)
async def checkin_history(request: Request):
    db = await get_db()
    try:
        cursor = await db.execute(
            """
            SELECT ci.id, ci.date, ci.completed,
                   q.text, q.type, ca.answer_text, ca.answer_score
            FROM check_ins ci
            LEFT JOIN check_in_answers ca ON ca.check_in_id = ci.id
            LEFT JOIN questions q ON ca.question_id = q.id
            ORDER BY ci.date DESC, q.is_core DESC
            """
        )
        rows = await cursor.fetchall()

        # Groepeer per datum
        history: dict[str, list[dict]] = {}
        for r in rows:
            d = r["date"]
            if d not in history:
                history[d] = []
            if r["text"]:
                history[d].append(dict(r))

        return templates.TemplateResponse(
            "history.html",
            {"request": request, "history": history},
        )
    finally:
        await db.close()


# --- Weekreview ---


@app.get("/weekreview", response_class=HTMLResponse)
async def weekreview_page(request: Request):
    db = await get_db()
    try:
        questions = await get_weekly_questions(db)
        now = datetime.now()
        year, week, _ = now.isocalendar()

        cursor = await db.execute(
            "SELECT * FROM week_reviews WHERE year = ? AND week_number = ?",
            (year, week),
        )
        existing = await cursor.fetchone()

        return templates.TemplateResponse(
            "weekreview.html",
            {
                "request": request,
                "questions": questions,
                "year": year,
                "week": week,
                "existing": dict(existing) if existing else None,
            },
        )
    finally:
        await db.close()


@app.post("/api/weekreview")
async def save_weekreview(request: Request):
    db = await get_db()
    try:
        form = await request.form()
        now = datetime.now()
        year, week, _ = now.isocalendar()

        score = form.get("score")
        went_well = form.get("went_well", "")
        improve = form.get("improve", "")
        on_track = form.get("on_track_goals")
        priorities = form.get("priorities_next_week", "")

        # Upsert
        cursor = await db.execute(
            "SELECT id FROM week_reviews WHERE year = ? AND week_number = ?",
            (year, week),
        )
        existing = await cursor.fetchone()

        if existing:
            await db.execute(
                """UPDATE week_reviews
                   SET score = ?, went_well = ?, improve = ?, on_track_goals = ?, priorities_next_week = ?
                   WHERE id = ?""",
                (score, went_well, improve, on_track, priorities, existing["id"]),
            )
        else:
            await db.execute(
                """INSERT INTO week_reviews (year, week_number, score, went_well, improve, on_track_goals, priorities_next_week)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (year, week, score, went_well, improve, on_track, priorities),
            )

        await db.commit()
        return RedirectResponse("/", status_code=303)
    finally:
        await db.close()


# --- Focus sidebar ---


@app.get("/focus", response_class=HTMLResponse)
async def focus_sidebar(request: Request):
    """Smalle sidebar-view met weekdoelen en prioriteiten."""
    db = await get_db()
    try:
        now = datetime.now()
        year, week, _ = now.isocalendar()
        quarter = f"Q{(now.month - 1) // 3 + 1}"

        # Weekreview prioriteiten
        cursor = await db.execute(
            "SELECT priorities_next_week FROM week_reviews WHERE year = ? AND week_number = ? ",
            (year, week),
        )
        current_review = await cursor.fetchone()

        # Vorige week als fallback
        if not current_review:
            cursor = await db.execute(
                "SELECT priorities_next_week FROM week_reviews ORDER BY year DESC, week_number DESC LIMIT 1"
            )
            current_review = await cursor.fetchone()

        priorities = current_review["priorities_next_week"] if current_review else ""

        # Actieve kwartaaldoelen
        cursor = await db.execute(
            "SELECT id, title FROM goals WHERE status = 'active' AND type = 'quarterly' AND year = ? AND quarter = ? ORDER BY id",
            (year, quarter),
        )
        quarterly_goals = [dict(r) for r in await cursor.fetchall()]

        # Actieve jaardoelen
        cursor = await db.execute(
            "SELECT id, title FROM goals WHERE status = 'active' AND type = 'yearly' AND year = ? ORDER BY id",
            (year,),
        )
        yearly_goals = [dict(r) for r in await cursor.fetchall()]

        return templates.TemplateResponse(
            "focus.html",
            {
                "request": request,
                "priorities": priorities,
                "quarterly_goals": quarterly_goals,
                "yearly_goals": yearly_goals,
                "week": week,
                "year": year,
                "quarter": quarter,
            },
        )
    finally:
        await db.close()


# --- Doelen ---


@app.get("/goals", response_class=HTMLResponse)
async def goals_page(request: Request):
    db = await get_db()
    try:
        now = datetime.now()
        year = now.year
        quarter = f"Q{(now.month - 1) // 3 + 1}"

        cursor = await db.execute(
            "SELECT * FROM goals ORDER BY status, type, year DESC, quarter"
        )
        all_goals = [dict(r) for r in await cursor.fetchall()]

        yearly = [g for g in all_goals if g["type"] == "yearly" and g["status"] == "active"]
        quarterly = [g for g in all_goals if g["type"] == "quarterly" and g["status"] == "active"]
        archived = [g for g in all_goals if g["status"] != "active"]

        # Taken per doel laden
        active_ids = [g["id"] for g in yearly + quarterly]
        goal_tasks: dict[int, list[dict]] = {gid: [] for gid in active_ids}
        if active_ids:
            placeholders = ",".join("?" * len(active_ids))
            cursor = await db.execute(
                f"SELECT * FROM goal_tasks WHERE goal_id IN ({placeholders}) ORDER BY sort_order, id",
                active_ids,
            )
            for t in await cursor.fetchall():
                goal_tasks[t["goal_id"]].append(dict(t))

        return templates.TemplateResponse(
            "goals.html",
            {
                "request": request,
                "yearly_goals": yearly,
                "quarterly_goals": quarterly,
                "archived_goals": archived,
                "goal_tasks": goal_tasks,
                "current_year": year,
                "current_quarter": quarter,
            },
        )
    finally:
        await db.close()


@app.post("/api/goals")
async def create_goal(request: Request):
    db = await get_db()
    try:
        form = await request.form()
        await db.execute(
            "INSERT INTO goals (title, description, type, quarter, year) VALUES (?, ?, ?, ?, ?)",
            (
                form["title"],
                form.get("description", ""),
                form["type"],
                form.get("quarter") or None,
                int(form["year"]),
            ),
        )
        await db.commit()
        return RedirectResponse("/goals", status_code=303)
    finally:
        await db.close()


@app.put("/api/goals/{goal_id}")
async def update_goal(goal_id: int, request: Request):
    db = await get_db()
    try:
        data = await request.json()
        fields = []
        values = []
        for key in ("title", "description", "status"):
            if key in data:
                fields.append(f"{key} = ?")
                values.append(data[key])
        if fields:
            fields.append("updated_at = datetime('now')")
            values.append(goal_id)
            await db.execute(
                f"UPDATE goals SET {', '.join(fields)} WHERE id = ?", values
            )
            await db.commit()
        return JSONResponse({"ok": True})
    finally:
        await db.close()


@app.post("/api/goals/{goal_id}/update")
async def add_goal_update(goal_id: int, request: Request):
    db = await get_db()
    try:
        form = await request.form()
        note = form.get("note", "")
        await db.execute(
            "INSERT INTO goal_updates (goal_id, note) VALUES (?, ?)",
            (goal_id, note),
        )
        await db.commit()
        return RedirectResponse("/goals", status_code=303)
    finally:
        await db.close()


# --- Goal Tasks (taken bij doelen) ---


@app.post("/api/goals/{goal_id}/tasks")
async def add_goal_task(goal_id: int, request: Request):
    db = await get_db()
    try:
        form = await request.form()
        title = form.get("title", "").strip()
        if not title:
            return RedirectResponse("/goals", status_code=303)
        # Bepaal sort_order
        cursor = await db.execute(
            "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM goal_tasks WHERE goal_id = ?",
            (goal_id,),
        )
        next_order = (await cursor.fetchone())[0]
        await db.execute(
            "INSERT INTO goal_tasks (goal_id, title, sort_order) VALUES (?, ?, ?)",
            (goal_id, title, next_order),
        )
        await db.commit()
        return RedirectResponse("/goals", status_code=303)
    finally:
        await db.close()


@app.post("/api/goal-tasks/{task_id}/toggle")
async def toggle_goal_task(task_id: int):
    db = await get_db()
    try:
        await db.execute(
            "UPDATE goal_tasks SET completed = CASE WHEN completed = 0 THEN 1 ELSE 0 END WHERE id = ?",
            (task_id,),
        )
        await db.commit()
        return JSONResponse({"ok": True})
    finally:
        await db.close()


@app.delete("/api/goal-tasks/{task_id}")
async def delete_goal_task(task_id: int):
    db = await get_db()
    try:
        await db.execute("DELETE FROM goal_tasks WHERE id = ?", (task_id,))
        await db.commit()
        return JSONResponse({"ok": True})
    finally:
        await db.close()


# --- Daily Tasks (taken bij check-in) ---


@app.post("/api/daily-tasks")
async def add_daily_task(request: Request):
    db = await get_db()
    try:
        form = await request.form()
        title = form.get("title", "").strip()
        task_date = form.get("date", date.today().isoformat())
        if not title:
            return RedirectResponse("/checkin", status_code=303)
        await db.execute(
            "INSERT INTO daily_tasks (title, date) VALUES (?, ?)",
            (title, task_date),
        )
        await db.commit()
        return RedirectResponse("/checkin", status_code=303)
    finally:
        await db.close()


@app.post("/api/daily-tasks/{task_id}/toggle")
async def toggle_daily_task(task_id: int):
    db = await get_db()
    try:
        await db.execute(
            "UPDATE daily_tasks SET completed = CASE WHEN completed = 0 THEN 1 ELSE 0 END WHERE id = ?",
            (task_id,),
        )
        await db.commit()
        return JSONResponse({"ok": True})
    finally:
        await db.close()


@app.delete("/api/daily-tasks/{task_id}")
async def delete_daily_task(task_id: int):
    db = await get_db()
    try:
        await db.execute("DELETE FROM daily_tasks WHERE id = ?", (task_id,))
        await db.commit()
        return JSONResponse({"ok": True})
    finally:
        await db.close()


# --- Trackers (configureerbare dagelijkse metrieken) ---


@app.get("/trackers", response_class=HTMLResponse)
async def trackers_page(request: Request):
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM trackers ORDER BY sort_order, id")
        all_trackers = [dict(r) for r in await cursor.fetchall()]

        return templates.TemplateResponse(
            "trackers.html",
            {"request": request, "trackers": all_trackers},
        )
    finally:
        await db.close()


@app.post("/api/trackers")
async def create_tracker(request: Request):
    db = await get_db()
    try:
        form = await request.form()
        name = form.get("name", "").strip()
        unit = form.get("unit", "").strip()
        tracker_type = form.get("type", "number")
        if not name:
            return RedirectResponse("/trackers", status_code=303)

        cursor = await db.execute(
            "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM trackers"
        )
        next_order = (await cursor.fetchone())[0]

        await db.execute(
            "INSERT INTO trackers (name, unit, type, sort_order) VALUES (?, ?, ?, ?)",
            (name, unit, tracker_type, next_order),
        )
        await db.commit()
        return RedirectResponse("/trackers", status_code=303)
    finally:
        await db.close()


@app.post("/api/trackers/{tracker_id}/toggle")
async def toggle_tracker(tracker_id: int):
    db = await get_db()
    try:
        await db.execute(
            "UPDATE trackers SET active = CASE WHEN active = 0 THEN 1 ELSE 0 END WHERE id = ?",
            (tracker_id,),
        )
        await db.commit()
        return JSONResponse({"ok": True})
    finally:
        await db.close()


@app.delete("/api/trackers/{tracker_id}")
async def delete_tracker(tracker_id: int):
    db = await get_db()
    try:
        await db.execute("DELETE FROM trackers WHERE id = ?", (tracker_id,))
        await db.commit()
        return JSONResponse({"ok": True})
    finally:
        await db.close()


# --- Insights ---


@app.get("/insights", response_class=HTMLResponse)
async def insights_page(request: Request):
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM insights ORDER BY created_at DESC LIMIT 20"
        )
        history = [dict(r) for r in await cursor.fetchall()]

        return templates.TemplateResponse(
            "insights.html",
            {"request": request, "history": history},
        )
    finally:
        await db.close()


@app.post("/api/insights/ask")
async def ask_insight(request: Request):
    db = await get_db()
    try:
        data = await request.json()
        question = data.get("question", "")
        if not question:
            return JSONResponse({"error": "Geen vraag opgegeven"}, status_code=400)

        response = await insights.ask(db, question)

        # Sla op in de database
        await db.execute(
            "INSERT INTO insights (prompt, response, context_type) VALUES (?, ?, 'general')",
            (question, response),
        )
        await db.commit()

        return JSONResponse({"response": response})
    finally:
        await db.close()
