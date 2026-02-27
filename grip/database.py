import aiosqlite
import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
DB_PATH = DATA_DIR / "grip.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    type TEXT NOT NULL CHECK (type IN ('score', 'open')),
    category TEXT NOT NULL CHECK (category IN ('daily', 'weekly')),
    is_core INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS check_ins (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    completed INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS check_in_answers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    check_in_id INTEGER NOT NULL REFERENCES check_ins(id),
    question_id INTEGER NOT NULL REFERENCES questions(id),
    answer_text TEXT,
    answer_score INTEGER
);

CREATE TABLE IF NOT EXISTS week_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    year INTEGER NOT NULL,
    week_number INTEGER NOT NULL,
    score INTEGER,
    went_well TEXT,
    improve TEXT,
    on_track_goals INTEGER,
    priorities_next_week TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(year, week_number)
);

CREATE TABLE IF NOT EXISTS goals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    description TEXT,
    type TEXT NOT NULL CHECK (type IN ('yearly', 'quarterly')),
    quarter TEXT CHECK (quarter IN ('Q1', 'Q2', 'Q3', 'Q4')),
    year INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'completed', 'abandoned')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS goal_updates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    goal_id INTEGER NOT NULL REFERENCES goals(id),
    check_in_id INTEGER REFERENCES check_ins(id),
    week_review_id INTEGER REFERENCES week_reviews(id),
    note TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS goal_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    goal_id INTEGER NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    completed INTEGER NOT NULL DEFAULT 0,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS daily_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    date TEXT NOT NULL,
    completed INTEGER NOT NULL DEFAULT 0,
    check_in_id INTEGER REFERENCES check_ins(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS trackers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    unit TEXT NOT NULL DEFAULT '',
    type TEXT NOT NULL DEFAULT 'number' CHECK (type IN ('number', 'boolean')),
    active INTEGER NOT NULL DEFAULT 1,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS tracker_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tracker_id INTEGER NOT NULL REFERENCES trackers(id) ON DELETE CASCADE,
    date TEXT NOT NULL,
    value REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(tracker_id, date)
);

CREATE TABLE IF NOT EXISTS insights (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prompt TEXT NOT NULL,
    response TEXT NOT NULL,
    context_type TEXT NOT NULL DEFAULT 'general' CHECK (context_type IN ('daily', 'weekly', 'quarterly', 'general')),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


async def get_db() -> aiosqlite.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")
    return db


async def init_db():
    db = await get_db()
    try:
        await db.executescript(SCHEMA)
        await _seed_questions(db)
        await db.commit()
    finally:
        await db.close()


async def _seed_questions(db: aiosqlite.Connection):
    cursor = await db.execute("SELECT COUNT(*) FROM questions")
    row = await cursor.fetchone()
    if row[0] > 0:
        return

    questions = [
        # Dagelijkse kernvragen (altijd)
        ("Energieniveau", "score", "daily", 1),
        ("Wat is vandaag je #1 prioriteit?", "open", "daily", 1),
        # Dagelijkse wisselende score-vragen
        ("Hoe voel je je vandaag?", "score", "daily", 0),
        ("Hoe productief was je vandaag?", "score", "daily", 0),
        ("Hoe goed heb je geslapen?", "score", "daily", 0),
        ("Hoeveel stress ervaar je?", "score", "daily", 0),
        ("Hoe tevreden ben je over vandaag?", "score", "daily", 0),
        # Dagelijkse wisselende open vragen
        ("Waar ben je dankbaar voor vandaag?", "open", "daily", 0),
        ("Wat heb je vandaag geleerd?", "open", "daily", 0),
        ("Wat zou je morgen anders doen?", "open", "daily", 0),
        ("Wat was het hoogtepunt van je dag?", "open", "daily", 0),
        ("Welk doel heb je vandaag dichterbij gebracht?", "open", "daily", 0),
        ("Wat staat er in de weg van je doelen?", "open", "daily", 0),
        ("Wat heb je voor iemand anders gedaan vandaag?", "open", "daily", 0),
        ("Waar wil je morgen mee beginnen?", "open", "daily", 0),
        # Weekreview vragen
        ("Hoe was je week overall?", "score", "weekly", 1),
        ("Wat ging er goed deze week?", "open", "weekly", 1),
        ("Wat kan er beter volgende week?", "open", "weekly", 1),
        ("Ben je op koers met je kwartaaldoelen?", "score", "weekly", 1),
        ("Wat zijn je top 3 prioriteiten voor volgende week?", "open", "weekly", 1),
    ]

    await db.executemany(
        "INSERT INTO questions (text, type, category, is_core) VALUES (?, ?, ?, ?)",
        questions,
    )
