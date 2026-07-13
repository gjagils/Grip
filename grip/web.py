import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from pathlib import Path

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("grip.health")

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from grip.database import get_app_state, get_db, init_db, set_app_state
from grip.questions import get_daily_questions, get_reflection_questions, get_weekly_questions
from grip import insights, notify

BASE_DIR = Path(__file__).parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    watchdog = asyncio.create_task(notify.sync_watchdog_loop())
    try:
        yield
    finally:
        watchdog.cancel()


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

        # Gisteren in cijfers — alleen-lezen samenvatting van gevulde trackers
        # (gevuld via health-sync of de Trackers-pagina; de check-in vraagt er niet meer om)
        cursor = await db.execute(
            "SELECT t.name, t.unit, t.type, t.threshold_green, t.threshold_red, t.threshold_direction, te.value "
            "FROM trackers t JOIN tracker_entries te ON te.tracker_id = t.id AND te.date = ? "
            "WHERE t.active = 1 ORDER BY t.sort_order, t.id",
            (yesterday_str,),
        )
        yesterday_stats = [dict(r) for r in await cursor.fetchall()]

        # Stemming van vandaag (tracker "Stemming", 1-5)
        cursor = await db.execute(
            """SELECT te.value FROM tracker_entries te
               JOIN trackers t ON te.tracker_id = t.id
               WHERE t.name = 'Stemming' AND te.date = ?""",
            (today_str,),
        )
        mood_row = await cursor.fetchone()
        mood = int(mood_row["value"]) if mood_row else None

        # Reflectievragen van vandaag + eventuele antwoorden (voor bijwerken)
        reflections = await get_reflection_questions(db, today)
        for q in reflections:
            cursor = await db.execute(
                "SELECT answer FROM reflection_answers WHERE date = ? AND question_id = ?",
                (today_str, q["id"]),
            )
            row = await cursor.fetchone()
            q["answer"] = row["answer"] if row else ""

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
                "yesterday_stats": yesterday_stats,
                "reflections": reflections,
                "mood": mood,
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

        # Doel van gisteren: chips gehaald/deels/niet → 1 / 0.5 / 0
        goal_raw = form.get("yesterday_goal_done", "")
        try:
            goal_done = float(goal_raw) if goal_raw != "" else None
        except ValueError:
            goal_done = None

        await db.execute(
            """UPDATE check_ins SET
                yesterday_goal_done = ?, yesterday_goal_note = ?,
                today_main_goal = ?,
                claude_question = ?, claude_question_answer = ?,
                claude_followup = ?, claude_followup_answer = ?,
                completed = 1
               WHERE id = ?""",
            (
                goal_done,
                form.get("yesterday_goal_note"),
                form.get("today_main_goal"),
                form.get("claude_question"),
                form.get("claude_question_answer"),
                form.get("claude_followup"),
                form.get("claude_followup_answer"),
                checkin_id,
            ),
        )

        # Stemming (1-5) → tracker "Stemming"
        mood_raw = form.get("mood", "")
        if mood_raw in ("1", "2", "3", "4", "5"):
            cursor = await db.execute("SELECT id FROM trackers WHERE name = 'Stemming'")
            row = await cursor.fetchone()
            if row:
                mood_tracker_id = row["id"]
            else:
                cursor = await db.execute(
                    "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM trackers"
                )
                next_order = (await cursor.fetchone())[0]
                cursor = await db.execute(
                    """INSERT INTO trackers (name, unit, type, sort_order,
                                             threshold_green, threshold_red, threshold_direction)
                       VALUES ('Stemming', '', 'number', ?, 4, 2.5, 'higher')""",
                    (next_order,),
                )
                mood_tracker_id = cursor.lastrowid
            await db.execute(
                """INSERT INTO tracker_entries (tracker_id, date, value)
                   VALUES (?, ?, ?)
                   ON CONFLICT(tracker_id, date) DO UPDATE SET value = excluded.value""",
                (mood_tracker_id, today, float(mood_raw)),
            )

        # Antwoorden op de reflectievragen van vandaag
        for key, value in form.multi_items():
            if not key.startswith("reflection_"):
                continue
            try:
                qid = int(key.removeprefix("reflection_"))
            except ValueError:
                continue
            cursor = await db.execute(
                "SELECT text FROM reflection_questions WHERE id = ?", (qid,)
            )
            qrow = await cursor.fetchone()
            if qrow is None:
                continue
            await db.execute(
                """INSERT INTO reflection_answers (date, question_id, question_text, answer)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(date, question_id) DO UPDATE SET answer = excluded.answer""",
                (today, qid, qrow["text"], value.strip()),
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

        # Vandaag's taken: sync op titel (behoudt completed-status, voorkomt duplicaten)
        cursor = await db.execute(
            "SELECT id, title FROM daily_tasks WHERE date = ?", (today,)
        )
        existing_tasks = {r["title"]: r["id"] for r in await cursor.fetchall()}
        new_titles = [t for t in
                      (form.get(f"today_task_{i}", "").strip() for i in range(1, 4)) if t]
        for title, tid in existing_tasks.items():
            if title not in new_titles:
                await db.execute("DELETE FROM daily_tasks WHERE id = ?", (tid,))
        for title in new_titles:
            if title not in existing_tasks:
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
            """SELECT date, today_main_goal, yesterday_goal_done, yesterday_goal_note,
                      claude_question, claude_question_answer,
                      claude_followup, claude_followup_answer,
                      yesterday_highlight, yesterday_different, today_joy
               FROM check_ins ORDER BY date DESC LIMIT 60"""
        )
        checkins = [dict(r) for r in await cursor.fetchall()]

        cursor = await db.execute(
            "SELECT date, question_text, answer FROM reflection_answers WHERE answer != '' ORDER BY date DESC"
        )
        reflections: dict[str, list[dict]] = {}
        for r in await cursor.fetchall():
            reflections.setdefault(r["date"], []).append(dict(r))

        # yesterday_goal_done van dag X gaat over het doel van dag X-1 —
        # koppel de status dus aan de kaart van de dag waarop het doel gesteld werd
        by_date = {ci["date"]: ci for ci in checkins}

        history = []
        for ci in checkins:
            entries = []
            for q in reflections.get(ci["date"], []):
                entries.append({"q": q["question_text"], "a": q["answer"]})
            # Oude vaste velden (historische check-ins) blijven leesbaar
            for field, label in [("yesterday_highlight", "Leukste van gisteren"),
                                 ("yesterday_different", "Anders doen"),
                                 ("today_joy", "Zin in")]:
                if ci.get(field):
                    entries.append({"q": label, "a": ci[field]})
            if ci.get("claude_question") and ci.get("claude_question_answer"):
                entries.append({"q": ci["claude_question"], "a": ci["claude_question_answer"], "claude": True})
            if ci.get("claude_followup") and ci.get("claude_followup_answer"):
                entries.append({"q": ci["claude_followup"], "a": ci["claude_followup_answer"], "claude": True})

            next_day = (date.fromisoformat(ci["date"]) + timedelta(days=1)).isoformat()
            next_ci = by_date.get(next_day)
            history.append({
                "date": ci["date"],
                "goal": ci.get("today_main_goal"),
                "goal_done": next_ci.get("yesterday_goal_done") if next_ci else None,
                "goal_note": next_ci.get("yesterday_goal_note") if next_ci else None,
                "entries": entries,
            })

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

        # Vorige week (voor trend-vergelijking)
        prev_start = week_start - timedelta(days=7)
        prev_end = week_start - timedelta(days=1)

        cursor = await db.execute(
            "SELECT id, name, unit, type, threshold_green, threshold_red, threshold_direction FROM trackers WHERE active = 1 ORDER BY sort_order, id"
        )
        tracker_rows = await cursor.fetchall()

        tracker_week = []
        for t in tracker_rows:
            # Huidige week
            cursor = await db.execute(
                "SELECT date, value FROM tracker_entries WHERE tracker_id = ? AND date >= ? AND date <= ?",
                (t["id"], week_dates[0], week_dates[-1]),
            )
            entries = {r["date"]: r["value"] for r in await cursor.fetchall()}
            week_values = [entries.get(d) for d in week_dates]

            # Vorige week — alleen voor gemiddelde t.b.v. trend
            cursor = await db.execute(
                "SELECT value FROM tracker_entries WHERE tracker_id = ? AND date >= ? AND date <= ?",
                (t["id"], prev_start.isoformat(), prev_end.isoformat()),
            )
            prev_values = [r["value"] for r in await cursor.fetchall()]
            prev_avg = (sum(prev_values) / len(prev_values)) if prev_values else None

            tracker_week.append({
                "id": t["id"],
                "name": t["name"],
                "unit": t["unit"],
                "type": t["type"],
                "threshold_green": t["threshold_green"],
                "threshold_red": t["threshold_red"],
                "threshold_direction": t["threshold_direction"],
                "week_values": week_values,
                "prev_avg": prev_avg,
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
    """Claude's vraag van vandaag. Eén keer per dag gegenereerd en gecachet —
    zo tonen de check-in én de ochtend-nudge dezelfde vraag."""
    db = await get_db()
    try:
        today = date.today().isoformat()
        cursor = await db.execute(
            "SELECT question FROM daily_questions WHERE date = ?", (today,)
        )
        row = await cursor.fetchone()
        if row:
            return JSONResponse({"question": row["question"], "cached": True})

        todays = await get_reflection_questions(db)
        question = await insights.generate_checkin_question(
            db, avoid=[q["text"] for q in todays]
        )
        await db.execute(
            "INSERT OR REPLACE INTO daily_questions (date, question) VALUES (?, ?)",
            (today, question),
        )
        await db.commit()
        return JSONResponse({"question": question, "cached": False})
    finally:
        await db.close()


@app.post("/api/checkin/followup")
async def checkin_followup(request: Request):
    """Genereert één doorvraag op het antwoord op Claude's dagvraag."""
    try:
        body = await request.json()
        question = (body.get("question") or "").strip()
        answer = (body.get("answer") or "").strip()
    except Exception:
        return JSONResponse({"error": "Ongeldige JSON"}, status_code=400)
    if not question or not answer:
        return JSONResponse({"error": "Vraag en antwoord zijn verplicht"}, status_code=400)

    db = await get_db()
    try:
        followup = await insights.generate_followup(db, question, answer)
        return JSONResponse({"followup": followup})
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
    "weight":             ("Gewicht",                "kg",      "number"),
    "weight_kg":          ("Gewicht",                "kg",      "number"),  # alias
    "body_weight":        ("Gewicht",                "kg",      "number"),  # alias
}


# Velden die we NIET willen overslaan bij waarde 0 (een gewicht van 0 is fout, maar
# voor andere metrics interpreteren we 0 als "geen data"). Voor gewicht behandelen
# we 0 nog steeds als ontbrekend.
_ZERO_AS_MISSING = True  # alle metrics: 0 = geen data


def _sync_auth_error(request: Request) -> JSONResponse | None:
    """
    Optionele beveiliging van de health-endpoints. Als de env-var HEALTH_SYNC_TOKEN
    gezet is, moet de client die meesturen in de X-Sync-Token header. Zonder
    env-var blijven de endpoints open (Tailscale-only opstelling).
    """
    expected = os.environ.get("HEALTH_SYNC_TOKEN")
    if not expected:
        return None
    if request.headers.get("X-Sync-Token") == expected:
        return None
    logger.warning("health — geweigerd: ontbrekende of foute X-Sync-Token")
    return JSONResponse({"ok": False, "error": "Ongeldige of ontbrekende X-Sync-Token"}, status_code=401)


async def _upsert_health_entry(db, sync_date: str, data: dict) -> tuple[list[str], list[str]]:
    """
    Verwerk één dag aan health data. Retourneert (synced_names, skipped_names).
    `data` bevat de metric-velden (steps, weight, etc.) — eventuele 'date' wordt genegeerd.
    """
    synced: list[str] = []
    skipped: list[str] = []

    for field, (name, unit, ttype) in _HEALTH_FIELDS.items():
        value = data.get(field)
        if value is None:
            continue

        # Slaap auto-conversie: als > 24 → waarschijnlijk minuten
        if field == "sleep_hours" and float(value) > 24:
            logger.info("health/sync — slaap %s → %.1f uur (was minuten)", value, float(value) / 60)
            value = round(float(value) / 60, 1)

        # Negeer nullen (Shortcut stuurt 0 als data ontbreekt)
        if float(value) == 0:
            skipped.append(name)
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

    return synced, skipped


@app.post("/api/health/sync")
async def health_sync(request: Request):
    """
    Ontvangt gezondheidsdata van Apple Shortcuts.

    Single-day payload (backward compatible):
    {
      "date": "2026-04-30",          ← optioneel, default = gisteren
      "steps": 9823,
      "weight": 78.4,
      "sleep_hours": 7.2,
      ...
    }

    Multi-day payload (backfill, bv. laatste 5 dagen):
    {
      "entries": [
        {"date": "2026-04-26", "steps": 7000, "weight": 78.6},
        {"date": "2026-04-27", "steps": 8500, "weight": 78.5},
        {"date": "2026-04-28", "steps": 9100, "weight": 78.4}
      ]
    }

    Alle metric-velden zijn optioneel. Bestaande waarden voor dezelfde dag worden overschreven.
    """
    if (auth_error := _sync_auth_error(request)) is not None:
        return auth_error

    try:
        data = await request.json()
    except Exception as e:
        logger.error("health/sync — ongeldige JSON: %s", e)
        return JSONResponse({"ok": False, "error": f"Ongeldige JSON: {e}"}, status_code=400)

    logger.info("health/sync — ontvangen: %s", json.dumps(data, default=str))

    db = await get_db()
    try:
        yesterday = (date.today() - timedelta(days=1)).isoformat()

        # Multi-day: array van entries
        if isinstance(data.get("entries"), list):
            results = []
            for entry in data["entries"]:
                if not isinstance(entry, dict):
                    continue
                entry_date = entry.get("date") or yesterday
                synced, skipped = await _upsert_health_entry(db, entry_date, entry)
                results.append({"date": entry_date, "synced": synced, "skipped": skipped})

            await set_app_state(db, notify.LAST_SYNC_KEY, notify._now().isoformat(timespec="seconds"))
            await db.commit()
            result = {"ok": True, "mode": "multi", "results": results, "days": len(results)}
            logger.info("health/sync — resultaat: %s", json.dumps(result))
            return JSONResponse(result)

        # Single-day: bestaande gedrag
        sync_date = data.get("date", yesterday)
        synced, skipped = await _upsert_health_entry(db, sync_date, data)

        await set_app_state(db, notify.LAST_SYNC_KEY, notify._now().isoformat(timespec="seconds"))
        await db.commit()
        result = {"ok": True, "mode": "single", "synced": synced, "skipped": skipped, "date": sync_date}
        logger.info("health/sync — resultaat: %s", json.dumps(result))
        return JSONResponse(result)
    except Exception as e:
        logger.error("health/sync — fout bij verwerken: %s", e, exc_info=True)
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
    finally:
        await db.close()


# Health Auto Export metric-naam → veldnaam in _HEALTH_FIELDS
_HAE_METRICS = {
    "step_count":               "steps",
    "active_energy":            "active_calories",
    "dietary_energy":           "kcal",
    "apple_exercise_time":      "exercise_minutes",
    "apple_stand_hour":         "stand_hours",
    "apple_stand_time":         "stand_hours",
    "sleep_analysis":           "sleep_hours",
    "walking_running_distance": "distance_km",
    "weight_body_mass":         "weight",
}

# Metrics waarvoor datapunten binnen één dag opgeteld worden; voor de rest
# (gewicht) nemen we het gemiddelde van de metingen.
_HAE_CUMULATIVE = {"steps", "active_calories", "kcal", "exercise_minutes",
                   "stand_hours", "sleep_hours", "distance_km"}


@app.post("/api/health/import/hae")
async def health_import_hae(request: Request):
    """
    Ontvangt exports van de Health Auto Export app (REST API automation).

    Verwacht formaat:
    {
      "data": {
        "metrics": [
          {"name": "step_count", "units": "count",
           "data": [{"date": "2026-07-08 00:00:00 +0200", "qty": 9823}, ...]},
          ...
        ]
      }
    }

    Datapunten worden per dag geaggregeerd (som, gewicht: gemiddelde) en via
    dezelfde logica als /api/health/sync opgeslagen.
    """
    if (auth_error := _sync_auth_error(request)) is not None:
        return auth_error

    try:
        payload = await request.json()
    except Exception as e:
        logger.error("health/import/hae — ongeldige JSON: %s", e)
        return JSONResponse({"ok": False, "error": f"Ongeldige JSON: {e}"}, status_code=400)

    metrics = (payload.get("data") or {}).get("metrics")
    if not isinstance(metrics, list):
        return JSONResponse(
            {"ok": False, "error": "Geen 'data.metrics' gevonden — is dit een Health Auto Export payload?"},
            status_code=400,
        )

    days: dict[str, dict[str, float]] = {}
    weight_counts: dict[str, int] = {}
    unknown_metrics: list[str] = []

    for metric in metrics:
        if not isinstance(metric, dict):
            continue
        name = metric.get("name", "")
        field = _HAE_METRICS.get(name)
        if field is None:
            unknown_metrics.append(name)
            continue
        units = str(metric.get("units") or "").lower()

        for point in metric.get("data", []):
            if not isinstance(point, dict):
                continue
            day = str(point.get("date", ""))[:10]  # "2026-07-08 00:00:00 +0200" → "2026-07-08"
            if len(day) != 10:
                continue

            qty = point.get("qty")
            if field == "sleep_hours" and qty is None:
                # sleep_analysis heeft geen qty maar asleep/totalSleep (in uren)
                qty = point.get("totalSleep") or point.get("asleep")
            if qty is None:
                continue

            try:
                value = float(qty)
            except (TypeError, ValueError):
                continue

            # Eenheden normaliseren naar wat _HEALTH_FIELDS verwacht
            if field == "distance_km" and units == "mi":
                value *= 1.60934
            elif field == "distance_km" and units == "m":
                value /= 1000
            elif field == "weight" and units in ("lb", "lbs"):
                value *= 0.453592
            elif field in ("active_calories", "kcal") and units == "kj":
                value /= 4.184
            elif field == "sleep_hours" and units == "min":
                value /= 60

            bucket = days.setdefault(day, {})
            if field in _HAE_CUMULATIVE:
                bucket[field] = bucket.get(field, 0.0) + value
            else:
                # gewicht: lopend gemiddelde over metingen op dezelfde dag
                count = weight_counts.get(day, 0)
                bucket[field] = (bucket.get(field, 0.0) * count + value) / (count + 1)
                weight_counts[day] = count + 1

    logger.info("health/import/hae — %d dag(en), overgeslagen metrics: %s",
                len(days), unknown_metrics or "geen")

    db = await get_db()
    try:
        results = []
        for day in sorted(days):
            entry = {k: round(v, 2) for k, v in days[day].items()}
            synced, skipped = await _upsert_health_entry(db, day, entry)
            results.append({"date": day, "synced": synced, "skipped": skipped})

        await db.commit()
        result = {"ok": True, "mode": "hae", "results": results, "days": len(results),
                  "unknown_metrics": unknown_metrics}
        logger.info("health/import/hae — resultaat: %s", json.dumps(result))
        return JSONResponse(result)
    except Exception as e:
        logger.error("health/import/hae — fout bij verwerken: %s", e, exc_info=True)
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
    finally:
        await db.close()


@app.post("/api/notify/test")
async def notify_test(request: Request):
    """Stuurt een test-push — handig om de ntfy-config te verifiëren."""
    if (auth_error := _sync_auth_error(request)) is not None:
        return auth_error
    ok = await notify.send_push_async(
        title="Grip test",
        message="Testmelding vanuit Grip — als je dit ziet werkt ntfy. ✅",
        priority="default",
        tags="white_check_mark",
    )
    if ok:
        return JSONResponse({"ok": True})
    return JSONResponse(
        {"ok": False, "error": "Push niet verstuurd — controleer NTFY_URL in de env."},
        status_code=503,
    )


@app.get("/api/health/status")
async def health_status(request: Request):
    """Geeft de meest recente health-sync terug — handig voor testen vanuit Shortcuts."""
    if (auth_error := _sync_auth_error(request)) is not None:
        return auth_error

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


# --- Health Dashboard ---

# Volgorde op de pagina; alles wat niet genoemd is komt erachter
_HEALTH_METRIC_ORDER = ["Stappen", "Slaap", "Gewicht", "Actieve calorieën",
                        "Gegeten calorieën", "Beweegminuten", "Staande uren", "Afstand"]
_HEALTH_LINE_METRICS = {"Gewicht"}  # lijn i.p.v. staafjes


@app.get("/health", response_class=HTMLResponse)
async def health_page(request: Request):
    """Dashboard met Apple Health-data + sync-status per metric."""
    db = await get_db()
    try:
        today = date.today()
        window = 30
        day_list = [(today - timedelta(days=i)).isoformat() for i in range(window - 1, -1, -1)]

        health_names = list(dict.fromkeys(v[0] for v in _HEALTH_FIELDS.values()))
        placeholders = ",".join("?" * len(health_names))
        cursor = await db.execute(
            f"""SELECT id, name, unit, threshold_green, threshold_red, threshold_direction
                FROM trackers WHERE name IN ({placeholders})""",
            health_names,
        )
        tracker_by_name = {r["name"]: dict(r) for r in await cursor.fetchall()}

        def _avg(vals):
            vals = [v for v in vals if v is not None]
            return round(sum(vals) / len(vals), 1) if vals else None

        metrics = []
        missing = []      # metrics waarvoor nog nooit iets is binnengekomen
        days_with_data: set[str] = set()
        overall_last = None

        ordered = [n for n in _HEALTH_METRIC_ORDER if n in health_names] + \
                  [n for n in health_names if n not in _HEALTH_METRIC_ORDER]
        for name in ordered:
            t = tracker_by_name.get(name)
            if t is None:
                missing.append(name)
                continue

            cursor = await db.execute(
                "SELECT date, value FROM tracker_entries WHERE tracker_id = ? AND date >= ?",
                (t["id"], day_list[0]),
            )
            by_date = {r["date"]: r["value"] for r in await cursor.fetchall()}

            cursor = await db.execute(
                "SELECT date, value FROM tracker_entries WHERE tracker_id = ? ORDER BY date DESC LIMIT 1",
                (t["id"],),
            )
            last_row = await cursor.fetchone()
            if last_row is None:
                missing.append(name)
                continue

            days_with_data.update(by_date)
            if overall_last is None or last_row["date"] > overall_last:
                overall_last = last_row["date"]

            values = [by_date.get(d) for d in day_list]
            metrics.append({
                "name": name,
                "unit": t["unit"],
                "kind": "line" if name in _HEALTH_LINE_METRICS else "bar",
                "values": values,
                "last_value": last_row["value"],
                "last_date": last_row["date"],
                "days_ago": (today - date.fromisoformat(last_row["date"])).days,
                "avg7": _avg(values[-7:]),
                "prev7": _avg(values[-14:-7]),
                "threshold_green": t["threshold_green"],
                "threshold_direction": t["threshold_direction"],
            })

        # Sync-status: dekking van de laatste 14 dagen + hoe lang geleden de
        # laatste sync écht binnenkwam (ontvangsttijd, niet de datadatum — die
        # loopt altijd een dag achter en zegt dus niets over of de Shortcut liep)
        coverage = [{"date": d, "has": d in days_with_data} for d in day_list[-14:]]
        received_raw = await get_app_state(db, notify.LAST_SYNC_KEY)
        received_dt = None
        if received_raw:
            try:
                received_dt = datetime.fromisoformat(received_raw)
            except ValueError:
                received_dt = None

        if received_dt is not None:
            now = notify._now()
            if received_dt.tzinfo is None:
                received_dt = received_dt.replace(tzinfo=notify.TZ)
            mins = (now - received_dt).total_seconds() / 60
            if mins < 2:
                ago = "zojuist"
            elif mins < 90:
                ago = f"{int(round(mins))} min geleden"
            elif mins < 36 * 60:
                ago = f"{int(round(mins / 60))} uur geleden"
            else:
                ago = f"{int(mins // (24 * 60))} dagen geleden"
            recv_date = received_dt.astimezone(notify.TZ).date()
            gap_days = (now.date() - recv_date).days
            if gap_days == 0:
                sync_state = "ok"
            elif gap_days == 1:
                sync_state = "warn"
            else:
                sync_state = "bad"
            sync_label = f"laatste sync: {ago}"
        elif overall_last is None:
            sync_state, sync_label = "none", "nog geen data ontvangen"
        else:
            # Fallback voor databases van vóór de ontvangsttijd-tracking
            gap = (today - date.fromisoformat(overall_last)).days
            sync_state = "ok" if gap <= 1 else ("warn" if gap <= 3 else "bad")
            sync_label = f"laatste data: {overall_last}"

        # Metrics die wel bestaan maar al even niets ontvingen (sync-aandachtspunten)
        stale = [{"name": m["name"], "days_ago": m["days_ago"]}
                 for m in metrics if m["days_ago"] > 3]

        return templates.TemplateResponse(
            request,
            "health.html",
            {
                "days": day_list,
                "metrics": metrics,
                "coverage": coverage,
                "received_14": sum(1 for c in coverage if c["has"]),
                "sync_state": sync_state,
                "sync_label": sync_label,
                "stale": stale,
                "missing": missing,
            },
        )
    finally:
        await db.close()
