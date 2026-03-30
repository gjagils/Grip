import json
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
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
            request,
            "dashboard.html",
            {
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
        yesterday_str = (today - timedelta(days=1)).isoformat()

        # Vandaag's check-in
        cursor = await db.execute("SELECT * FROM check_ins WHERE date = ?", (today_str,))
        existing_row = await cursor.fetchone()
        existing = dict(existing_row) if existing_row else None

        # Vandaag's taken
        cursor = await db.execute(
            "SELECT id, title, completed FROM daily_tasks WHERE date = ? ORDER BY created_at",
            (today_str,),
        )
        today_tasks = [dict(r) for r in await cursor.fetchall()]

        # Gisteren's taken
        cursor = await db.execute(
            "SELECT id, title, completed FROM daily_tasks WHERE date = ? ORDER BY created_at",
            (yesterday_str,),
        )
        yesterday_tasks = [dict(r) for r in await cursor.fetchall()]

        # Gisteren's doel (today_main_goal van gisterens check-in)
        cursor = await db.execute(
            "SELECT today_main_goal FROM check_ins WHERE date = ?", (yesterday_str,)
        )
        yesterday_checkin = await cursor.fetchone()
        yesterday_goal = yesterday_checkin["today_main_goal"] if yesterday_checkin else None

        # Trackers — gisteren's waarden
        cursor = await db.execute(
            "SELECT t.id, t.name, t.unit, t.type, t.threshold_green, t.threshold_red, t.threshold_direction, te.value, te.note FROM trackers t "
            "LEFT JOIN tracker_entries te ON te.tracker_id = t.id AND te.date = ? "
            "WHERE t.active = 1 ORDER BY t.sort_order, t.id",
            (yesterday_str,),
        )
        trackers = [dict(r) for r in await cursor.fetchall()]

        # Bestaand dagelijks inzicht
        cursor = await db.execute(
            "SELECT response FROM insights WHERE context_type = 'daily' AND date(created_at) = ? ORDER BY created_at DESC LIMIT 1",
            (today_str,),
        )
        existing_insight = await cursor.fetchone()

        return templates.TemplateResponse(
            request,
            "checkin.html",
            {
                "today": today_str,
                "already_done": existing is not None,
                "existing": existing,
                "today_tasks": today_tasks,
                "yesterday_tasks": yesterday_tasks,
                "yesterday_goal": yesterday_goal,
                "trackers": trackers,
                "today_insight": existing_insight["response"] if existing_insight else None,
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

        # Maak check-in aan of update bestaande
        cursor = await db.execute("SELECT id FROM check_ins WHERE date = ?", (today,))
        row = await cursor.fetchone()
        if row:
            checkin_id = row["id"]
        else:
            cursor = await db.execute(
                "INSERT INTO check_ins (date, completed) VALUES (?, 1)", (today,)
            )
            checkin_id = cursor.lastrowid

        # Sla gestructureerde velden op
        await db.execute(
            """UPDATE check_ins SET
                yesterday_highlight = ?, yesterday_different = ?,
                yesterday_goal_done = ?, yesterday_goal_note = ?,
                today_main_goal = ?, today_joy = ?,
                claude_question = ?, claude_question_answer = ?,
                completed = 1
               WHERE id = ?""",
            (
                form.get("yesterday_highlight"),
                form.get("yesterday_different"),
                1 if form.get("yesterday_goal_done") else 0,
                form.get("yesterday_goal_note"),
                form.get("today_main_goal"),
                form.get("today_joy"),
                form.get("claude_question"),
                form.get("claude_question_answer"),
                checkin_id,
            ),
        )

        # Gisteren's taken markeren als voltooid/niet voltooid
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        cursor = await db.execute(
            "SELECT id FROM daily_tasks WHERE date = ?", (yesterday,)
        )
        yesterday_task_rows = await cursor.fetchall()
        for t in yesterday_task_rows:
            done = form.get(f"ytask_{t['id']}")
            await db.execute(
                "UPDATE daily_tasks SET completed = ? WHERE id = ?",
                (1 if done else 0, t["id"]),
            )

        # Gisteren's tracker waarden en notities opslaan
        cursor = await db.execute("SELECT id, type FROM trackers WHERE active = 1")
        active_trackers = await cursor.fetchall()
        for t in active_trackers:
            tid = t["id"]
            note = form.get(f"tracker_note_{tid}", "").strip() or None
            if t["type"] == "boolean":
                raw = form.get(f"tracker_{tid}")
                val = 1.0 if raw else 0.0
                await db.execute(
                    """INSERT INTO tracker_entries (tracker_id, date, value, note)
                       VALUES (?, ?, ?, ?)
                       ON CONFLICT(tracker_id, date) DO UPDATE SET value = excluded.value, note = excluded.note""",
                    (tid, yesterday, val, note),
                )
            else:
                raw = form.get(f"tracker_{tid}", "").strip()
                if raw != "":
                    try:
                        val = float(raw)
                    except ValueError:
                        continue
                    await db.execute(
                        """INSERT INTO tracker_entries (tracker_id, date, value, note)
                           VALUES (?, ?, ?, ?)
                           ON CONFLICT(tracker_id, date) DO UPDATE SET value = excluded.value, note = excluded.note""",
                        (tid, yesterday, val, note),
                    )
                elif note:
                    await db.execute(
                        "UPDATE tracker_entries SET note = ? WHERE tracker_id = ? AND date = ?",
                        (note, tid, yesterday),
                    )

        # Vandaag's taken aanmaken (max 3)
        for i in range(1, 4):
            title = form.get(f"today_task_{i}", "").strip()
            if title:
                # Upsert: als er al een taak met deze positie is, update; anders insert
                await db.execute(
                    "INSERT INTO daily_tasks (title, date, check_in_id) VALUES (?, ?, ?)",
                    (title, today, checkin_id),
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

        return templates.TemplateResponse(request, "history.html", {"history": history})
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

        # Meest recente weekreview-inzicht
        cursor = await db.execute(
            "SELECT response FROM insights WHERE context_type = 'weekly' ORDER BY created_at DESC LIMIT 1"
        )
        weekly_insight = await cursor.fetchone()

        # Tracker weekdata (ma t/m zo van huidige week)
        week_start = (now - timedelta(days=now.weekday())).date()
        week_dates = [(week_start + timedelta(days=i)).isoformat() for i in range(7)]
        day_labels = ["Ma", "Di", "Wo", "Do", "Vr", "Za", "Zo"]

        cursor = await db.execute(
            "SELECT id, name, unit, type, threshold_green, threshold_red, threshold_direction FROM trackers WHERE active = 1 ORDER BY sort_order, id"
        )
        tracker_rows = await cursor.fetchall()

        tracker_week = []
        for t in tracker_rows:
            cursor = await db.execute(
                "SELECT date, value FROM tracker_entries WHERE tracker_id = ? AND date >= ? AND date <= ?",
                (t["id"], week_dates[0], week_dates[-1]),
            )
            entries = {r["date"]: r["value"] for r in await cursor.fetchall()}
            tracker_week.append({
                "id": t["id"],
                "name": t["name"],
                "unit": t["unit"],
                "type": t["type"],
                "threshold_green": t["threshold_green"],
                "threshold_red": t["threshold_red"],
                "threshold_direction": t["threshold_direction"],
                "values": [entries.get(d) for d in week_dates],
            })

        return templates.TemplateResponse(
            request,
            "weekreview.html",
            {
                "questions": questions,
                "year": year,
                "week": week,
                "existing": dict(existing) if existing else None,
                "weekly_insight": weekly_insight["response"] if weekly_insight else None,
                "tracker_week": tracker_week,
                "week_dates": week_dates,
                "day_labels": day_labels,
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
            request,
            "focus.html",
            {
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
        _, week, _ = now.isocalendar()

        cursor = await db.execute(
            "SELECT * FROM goals ORDER BY status, type, year DESC, quarter"
        )
        all_goals = [dict(r) for r in await cursor.fetchall()]

        weekly = [
            g for g in all_goals
            if g["type"] == "weekly" and g["status"] == "active"
            and g["year"] == year and g["week_number"] == week
        ]
        quarterly = [
            g for g in all_goals
            if g["type"] == "quarterly" and g["status"] == "active"
            and g["year"] == year and g["quarter"] == quarter
        ]
        yearly = [g for g in all_goals if g["type"] == "yearly" and g["status"] == "active" and g["year"] == year]
        archived = [g for g in all_goals if g["status"] != "active"]

        # Taken per doel laden
        active_ids = [g["id"] for g in weekly + quarterly + yearly]
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
            request,
            "goals.html",
            {
                "weekly_goals": weekly,
                "quarterly_goals": quarterly,
                "yearly_goals": yearly,
                "archived_goals": archived,
                "goal_tasks": goal_tasks,
                "current_year": year,
                "current_quarter": quarter,
                "current_week": week,
            },
        )
    finally:
        await db.close()


@app.post("/api/goals")
async def create_goal(request: Request):
    db = await get_db()
    try:
        form = await request.form()
        goal_type = form["type"]
        now = datetime.now()
        _, current_week, _ = now.isocalendar()
        week_number = current_week if goal_type == "weekly" else None
        quarter = form.get("quarter") or None if goal_type == "quarterly" else None
        await db.execute(
            "INSERT INTO goals (title, description, type, quarter, year, week_number) VALUES (?, ?, ?, ?, ?, ?)",
            (
                form["title"],
                form.get("description", ""),
                goal_type,
                quarter,
                int(form["year"]),
                week_number,
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
        cursor = await db.execute("SELECT id, name, unit, type, active, sort_order, threshold_green, threshold_red, threshold_direction FROM trackers ORDER BY sort_order, id")
        all_trackers = [dict(r) for r in await cursor.fetchall()]

        return templates.TemplateResponse(request, "trackers.html", {"trackers": all_trackers})
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


@app.post("/api/trackers/{tracker_id}/thresholds")
async def update_thresholds(tracker_id: int, request: Request):
    form = await request.form()
    green = form.get("threshold_green")
    red = form.get("threshold_red")
    db = await get_db()
    try:
        direction = form.get("threshold_direction", "higher")
        await db.execute(
            "UPDATE trackers SET threshold_green = ?, threshold_red = ?, threshold_direction = ? WHERE id = ?",
            (float(green) if green else None, float(red) if red else None, direction, tracker_id),
        )
        await db.commit()
        return JSONResponse({"ok": True})
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

        return templates.TemplateResponse(request, "insights.html", {"history": history})
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


@app.get("/api/checkin/question")
async def checkin_question(request: Request):
    """Genereert een persoonlijke vraag van Claude voor de check-in."""
    db = await get_db()
    try:
        question = await insights.generate_checkin_question(db)
        return JSONResponse({"question": question})
    finally:
        await db.close()


@app.post("/api/reflect/checkin")
async def reflect_checkin(request: Request):
    db = await get_db()
    try:
        response = await insights.reflect_checkin(db)
        await db.execute(
            "INSERT INTO insights (prompt, response, context_type) VALUES (?, ?, 'daily')",
            ("Dagelijkse check-in reflectie", response),
        )
        await db.commit()
        return JSONResponse({"response": response})
    finally:
        await db.close()


@app.post("/api/reflect/weekreview")
async def reflect_weekreview(request: Request):
    db = await get_db()
    try:
        response = await insights.reflect_weekreview(db)
        await db.execute(
            "INSERT INTO insights (prompt, response, context_type) VALUES (?, ?, 'weekly')",
            ("Weekreview reflectie", response),
        )
        await db.commit()
        return JSONResponse({"response": response})
    finally:
        await db.close()


@app.get("/api/export/week")
async def export_week(request: Request):
    db = await get_db()
    try:
        markdown = await insights.export_week_markdown(db)
        return JSONResponse({"markdown": markdown})
    finally:
        await db.close()


# --- Coach Chat ---


@app.get("/chat", response_class=HTMLResponse)
async def chat_page(request: Request):
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT id, role, content, created_at FROM chat_messages ORDER BY id ASC"
        )
        history = [dict(r) for r in await cursor.fetchall()]
        return templates.TemplateResponse(request, "chat.html", {"history": history})
    finally:
        await db.close()


@app.post("/api/chat")
async def chat_send(request: Request):
    data = await request.json()
    user_message = data.get("message", "").strip()
    if not user_message:
        return JSONResponse({"error": "Geen bericht"}, status_code=400)

    db = await get_db()

    # Sla gebruikersbericht op
    await db.execute(
        "INSERT INTO chat_messages (role, content) VALUES ('user', ?)", (user_message,)
    )
    await db.commit()

    # Bouw context en geschiedenis
    system = await insights.build_chat_system(db)
    messages = await insights.load_chat_history(db, limit=40)

    async def generate():
        full_response = ""
        try:
            async with insights.client.messages.stream(
                model="claude-sonnet-4-5-20250929",
                max_tokens=1024,
                system=system,
                messages=messages,
            ) as stream:
                async for text in stream.text_stream:
                    full_response += text
                    yield f"data: {json.dumps({'delta': text})}\n\n"

            # Sla het complete antwoord op
            await db.execute(
                "INSERT INTO chat_messages (role, content) VALUES ('assistant', ?)",
                (full_response,),
            )
            await db.commit()
            yield f"data: {json.dumps({'done': True})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
        finally:
            await db.close()

    return StreamingResponse(generate(), media_type="text/event-stream")


@app.post("/api/chat/clear")
async def chat_clear(request: Request):
    db = await get_db()
    try:
        await db.execute("DELETE FROM chat_messages")
        await db.commit()
        return JSONResponse({"ok": True})
    finally:
        await db.close()


# --- Kwartaalreview ---


@app.get("/quarterly", response_class=HTMLResponse)
async def quarterly_page(request: Request):
    now = datetime.now()
    year, _, _ = now.isocalendar()
    quarter = (now.month - 1) // 3 + 1

    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM quarterly_reviews WHERE year = ? AND quarter = ?",
            (year, quarter),
        )
        existing = await cursor.fetchone()
        existing = dict(existing) if existing else None

        # Kwartaaldoelen van dit kwartaal (voor stap 2 review)
        quarter_label = f"Q{quarter}"
        cursor = await db.execute(
            "SELECT id, title, description FROM goals WHERE status IN ('active','completed') AND type = 'quarterly' AND year = ? AND quarter = ? ORDER BY id",
            (year, quarter_label),
        )
        quarterly_goals = [dict(g) for g in await cursor.fetchall()]

        # Alle actieve doelen (voor stap 2 algemeen)
        cursor = await db.execute(
            "SELECT id, title, type, status FROM goals WHERE status = 'active' ORDER BY type, year, quarter"
        )
        all_goals = [dict(g) for g in await cursor.fetchall()]

        # Bestaande per-doel reviews laden
        goal_reviews = {}
        if existing:
            cursor = await db.execute(
                "SELECT goal_id, achieved, note FROM quarterly_goal_reviews WHERE quarterly_review_id = ?",
                (existing["id"],),
            )
            for r in await cursor.fetchall():
                goal_reviews[r["goal_id"]] = {"achieved": r["achieved"], "note": r["note"]}

        return templates.TemplateResponse(
            request,
            "quarterly.html",
            {
                "active_nav": "quarterly",
                "year": year,
                "quarter": quarter,
                "quarter_label": quarter_label,
                "existing": existing,
                "quarterly_goals": quarterly_goals,
                "all_goals": all_goals,
                "goal_reviews": goal_reviews,
            },
        )
    finally:
        await db.close()


@app.post("/api/quarterly")
async def save_quarterly(request: Request):
    form = await request.form()
    now = datetime.now()
    year, _, _ = now.isocalendar()
    quarter = (now.month - 1) // 3 + 1

    db = await get_db()
    try:
        cursor = await db.execute(
            """INSERT INTO quarterly_reviews (
                year, quarter,
                highlights_proud, highlights_bad, goals_review,
                cat_werk, cat_relatie, cat_familie, cat_vrienden, cat_gezondheid,
                cat_vaardigheden, cat_sideprojects, cat_plezier,
                cat_geld_inkomen, cat_geld_sparen, cat_geld_geven,
                quarter_reflection, new_goals, outlook
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(year, quarter) DO UPDATE SET
                highlights_proud = excluded.highlights_proud,
                highlights_bad = excluded.highlights_bad,
                goals_review = excluded.goals_review,
                cat_werk = excluded.cat_werk,
                cat_relatie = excluded.cat_relatie,
                cat_familie = excluded.cat_familie,
                cat_vrienden = excluded.cat_vrienden,
                cat_gezondheid = excluded.cat_gezondheid,
                cat_vaardigheden = excluded.cat_vaardigheden,
                cat_sideprojects = excluded.cat_sideprojects,
                cat_plezier = excluded.cat_plezier,
                cat_geld_inkomen = excluded.cat_geld_inkomen,
                cat_geld_sparen = excluded.cat_geld_sparen,
                cat_geld_geven = excluded.cat_geld_geven,
                quarter_reflection = excluded.quarter_reflection,
                new_goals = excluded.new_goals,
                outlook = excluded.outlook""",
            (
                year, quarter,
                form.get("highlights_proud"), form.get("highlights_bad"),
                form.get("goals_review"),
                form.get("cat_werk"), form.get("cat_relatie"), form.get("cat_familie"),
                form.get("cat_vrienden"), form.get("cat_gezondheid"),
                form.get("cat_vaardigheden"), form.get("cat_sideprojects"),
                form.get("cat_plezier"), form.get("cat_geld_inkomen"),
                form.get("cat_geld_sparen"), form.get("cat_geld_geven"),
                form.get("quarter_reflection"), form.get("new_goals"),
                form.get("outlook"),
            ),
        )

        # Haal review ID op
        cursor = await db.execute(
            "SELECT id FROM quarterly_reviews WHERE year = ? AND quarter = ?", (year, quarter)
        )
        review_row = await cursor.fetchone()
        review_id = review_row["id"]

        # Sla per-doel reviews op
        for key, value in form.multi_items():
            if key.startswith("goal_done_"):
                goal_id = int(key.split("_")[2])
                note = form.get(f"goal_note_{goal_id}", "")
                await db.execute(
                    """INSERT INTO quarterly_goal_reviews (quarterly_review_id, goal_id, achieved, note)
                       VALUES (?, ?, 1, ?)
                       ON CONFLICT(quarterly_review_id, goal_id) DO UPDATE SET achieved = 1, note = excluded.note""",
                    (review_id, goal_id, note),
                )
        # Niet-aangevinkte doelen opslaan als niet gehaald
        for key in form.keys():
            if key.startswith("goal_present_"):
                goal_id = int(key.split("_")[2])
                if not form.get(f"goal_done_{goal_id}"):
                    note = form.get(f"goal_note_{goal_id}", "")
                    await db.execute(
                        """INSERT INTO quarterly_goal_reviews (quarterly_review_id, goal_id, achieved, note)
                           VALUES (?, ?, 0, ?)
                           ON CONFLICT(quarterly_review_id, goal_id) DO UPDATE SET achieved = 0, note = excluded.note""",
                        (review_id, goal_id, note),
                    )

        # Nieuwe doelen aanmaken voor volgend kwartaal
        next_quarter = quarter + 1 if quarter < 4 else 1
        next_year = year if quarter < 4 else year + 1
        next_quarter_label = f"Q{next_quarter}"
        for key in form.keys():
            if key.startswith("new_goal_"):
                title = form.get(key, "").strip()
                if title:
                    goal_id_str = key.split("_")[2]
                    # Bestaand doel updaten
                    if goal_id_str.isdigit():
                        await db.execute(
                            "UPDATE goals SET title = ? WHERE id = ?",
                            (title, int(goal_id_str)),
                        )
                    else:
                        # Nieuw doel aanmaken
                        await db.execute(
                            """INSERT INTO goals (title, type, quarter, year, status)
                               VALUES (?, 'quarterly', ?, ?, 'active')""",
                            (title, next_quarter_label, next_year),
                        )
        # Verwijderde bestaande doelen markeren als abandoned
        for key in form.keys():
            if key.startswith("goal_keep_"):
                goal_id = int(key.split("_")[2])
                kept_title = form.get(f"new_goal_{goal_id}", "").strip()
                if not kept_title:
                    await db.execute(
                        "UPDATE goals SET status = 'abandoned' WHERE id = ?", (goal_id,)
                    )

        await db.commit()
        return RedirectResponse("/quarterly", status_code=303)
    finally:
        await db.close()


@app.post("/api/reflect/quarterly")
async def reflect_quarterly_api(request: Request):
    now = datetime.now()
    year, _, _ = now.isocalendar()
    quarter = (now.month - 1) // 3 + 1

    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM quarterly_reviews WHERE year = ? AND quarter = ?",
            (year, quarter),
        )
        row = await cursor.fetchone()
        if not row:
            return JSONResponse({"error": "Geen kwartaalreview gevonden"}, status_code=404)

        response = await insights.reflect_quarterly(db, dict(row))

        await db.execute(
            "UPDATE quarterly_reviews SET claude_reflection = ? WHERE year = ? AND quarter = ?",
            (response, year, quarter),
        )
        await db.commit()
        return JSONResponse({"response": response})
    finally:
        await db.close()


# --- Health Sync (Apple Shortcuts → Grip) ---

# Veldnaam → (tracker naam, eenheid, type)
_HEALTH_FIELDS = {
    "steps":              ("Stappen",               "stappen", "number"),
    "active_calories":    ("Actieve calorieën",     "kcal",    "number"),
    "energy":             ("Actieve calorieën",     "kcal",    "number"),  # alias
    "kcal":               ("Gegeten calorieën",     "kcal",    "number"),
    "calories":           ("Gegeten calorieën",     "kcal",    "number"),  # alias
    "dietary_calories":   ("Gegeten calorieën",     "kcal",    "number"),  # alias
    "food_calories":      ("Gegeten calorieën",     "kcal",    "number"),  # alias
    "exercise_minutes":   ("Beweegminuten",         "min",     "number"),
    "stand_hours":        ("Staande uren",           "uur",     "number"),
    "sleep_hours":        ("Slaap",                  "uur",     "number"),
    "distance_km":        ("Afstand",                "km",      "number"),
}


@app.post("/api/health/sync")
async def health_sync(request: Request):
    """
    Ontvangt gezondheidsdata van Apple Shortcuts.

    Verwacht JSON:
    {
      "date": "2026-03-24",          ← gisteren, optioneel (default: gisteren)
      "steps": 9823,
      "active_calories": 412,
      "exercise_minutes": 38,
      "stand_hours": 11,
      "sleep_hours": 7.2,
      "distance_km": 7.4
    }
    Alle velden zijn optioneel. Alleen meegestuurde velden worden opgeslagen.
    """
    data = await request.json()
    db = await get_db()
    try:
        # Default: vandaag (shortcut stuurt vandaag's data, check-in toont die morgen als "gisteren")
        today_str = date.today().isoformat()
        sync_date = data.get("date", today_str)

        synced: list[str] = []

        for field, (name, unit, ttype) in _HEALTH_FIELDS.items():
            value = data.get(field)
            if value is None:
                continue

            # Zoek of maak tracker aan
            cursor = await db.execute(
                "SELECT id FROM trackers WHERE name = ?", (name,)
            )
            row = await cursor.fetchone()
            if row:
                tracker_id = row["id"]
            else:
                cursor = await db.execute(
                    "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM trackers"
                )
                next_order = (await cursor.fetchone())[0]
                cursor = await db.execute(
                    "INSERT INTO trackers (name, unit, type, sort_order) VALUES (?, ?, ?, ?)",
                    (name, unit, ttype, next_order),
                )
                tracker_id = cursor.lastrowid

            # Upsert waarde
            await db.execute(
                """INSERT INTO tracker_entries (tracker_id, date, value)
                   VALUES (?, ?, ?)
                   ON CONFLICT(tracker_id, date) DO UPDATE SET value = excluded.value""",
                (tracker_id, sync_date, float(value)),
            )
            synced.append(name)

        await db.commit()
        return JSONResponse({"ok": True, "synced": synced, "date": sync_date})
    finally:
        await db.close()


@app.get("/api/health/status")
async def health_status(request: Request):
    """Geeft de meest recente health-sync terug — handig voor testen vanuit Shortcuts."""
    db = await get_db()
    try:
        health_names = [v[0] for v in _HEALTH_FIELDS.values()]
        placeholders = ",".join("?" * len(health_names))
        cursor = await db.execute(
            f"""SELECT t.name, te.date, te.value
                FROM tracker_entries te
                JOIN trackers t ON te.tracker_id = t.id
                WHERE t.name IN ({placeholders})
                ORDER BY te.date DESC, t.name
                LIMIT 20""",
            health_names,
        )
        rows = [dict(r) for r in await cursor.fetchall()]
        return JSONResponse({"entries": rows})
    finally:
        await db.close()
