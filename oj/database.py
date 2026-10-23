import sqlite3
from pathlib import Path

def connect(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    db=sqlite3.connect(path, timeout=20)
    db.row_factory=sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    return db

def init_db(path):
    with connect(path) as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, is_admin INTEGER NOT NULL DEFAULT 0, approved INTEGER NOT NULL DEFAULT 0, avatar_color TEXT NOT NULL DEFAULT '#7c3aed', bio TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS submissions(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), problem_id TEXT NOT NULL, language_id INTEGER NOT NULL, source_code TEXT NOT NULL, verdict TEXT NOT NULL, score INTEGER NOT NULL DEFAULT 0, max_score INTEGER NOT NULL DEFAULT 100, details_json TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS solves(user_id INTEGER NOT NULL REFERENCES users(id), problem_id TEXT NOT NULL, best_score INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(user_id,problem_id));
        CREATE INDEX IF NOT EXISTS idx_submissions_user ON submissions(user_id, id DESC);
        CREATE INDEX IF NOT EXISTS idx_submissions_problem ON submissions(problem_id);
        """)
        columns = {row['name'] for row in db.execute('PRAGMA table_info(users)').fetchall()}
        if 'approved' not in columns:
            db.execute('ALTER TABLE users ADD COLUMN approved INTEGER NOT NULL DEFAULT 0')
        if 'avatar_color' not in columns:
            db.execute("ALTER TABLE users ADD COLUMN avatar_color TEXT NOT NULL DEFAULT '#7c3aed'")
        if 'bio' not in columns:
            db.execute("ALTER TABLE users ADD COLUMN bio TEXT NOT NULL DEFAULT ''")
        # Admin account is created only from server-side environment configuration,
        # never by a student choosing a reserved username during public registration.

