
import os
import sqlite3
from pathlib import Path
from contextlib import contextmanager


def _is_postgres(path):
    return str(path).startswith(("postgres://", "postgresql://"))


class PostgresCursor:
    def __init__(self, cursor):
        self._cursor = cursor

    def execute(self, sql, params=()):
        sql = sql.replace("?", "%s")
        self._cursor.execute(sql, params)
        return self

    def executemany(self, sql, seq):
        sql = sql.replace("?", "%s")
        self._cursor.executemany(sql, seq)
        return self

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()

    @property
    def rowcount(self):
        return self._cursor.rowcount

    @property
    def lastrowid(self):
        return None


class PostgresConnection:
    def __init__(self, conn):
        self._conn = conn

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type:
            self._conn.rollback()
        else:
            self._conn.commit()
        self._conn.close()
        return False

    def execute(self, sql, params=()):
        cur = self._conn.cursor()
        cur.execute(sql.replace("?", "%s"), params)
        return PostgresCursor(cur)

    def executemany(self, sql, seq):
        cur = self._conn.cursor()
        cur.executemany(sql.replace("?", "%s"), seq)
        return PostgresCursor(cur)

    def executescript(self, sql):
        for statement in sql.split(";"):
            statement = statement.strip()
            if statement:
                self._conn.execute(statement)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()


def connect(path):
    if _is_postgres(path):
        import psycopg
        from psycopg.rows import dict_row

        conn = psycopg.connect(path, row_factory=dict_row)
        conn.execute("SET search_path TO public")
        return PostgresConnection(conn)

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=20)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    return db


def init_db(path):
    if _is_postgres(path):
        raise RuntimeError(
            "PostgreSQL chưa được khởi tạo an toàn. "
            "Không deploy trước khi chuyển schema và migration."
        )

    with connect(path) as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            is_admin INTEGER NOT NULL DEFAULT 0,
            approved INTEGER NOT NULL DEFAULT 0,
            avatar_color TEXT NOT NULL DEFAULT '#7c3aed',
            bio TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS submissions(
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            problem_id TEXT NOT NULL,
            language_id INTEGER NOT NULL,
            source_code TEXT NOT NULL,
            verdict TEXT NOT NULL,
            score INTEGER NOT NULL DEFAULT 0,
            max_score INTEGER NOT NULL DEFAULT 100,
            details_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            contest_id INTEGER REFERENCES contests(id)
        );

        CREATE TABLE IF NOT EXISTS solves(
            user_id INTEGER NOT NULL REFERENCES users(id),
            problem_id TEXT NOT NULL,
            best_score INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(user_id, problem_id)
        );

        CREATE TABLE IF NOT EXISTS contests(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            start_time TEXT NOT NULL,
            end_time TEXT NOT NULL,
            is_visible INTEGER NOT NULL DEFAULT 1,
            created_by INTEGER REFERENCES users(id),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS contest_problems(
            contest_id INTEGER NOT NULL
                REFERENCES contests(id) ON DELETE CASCADE,
            problem_id TEXT NOT NULL,
            position INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(contest_id, problem_id)
        );

        CREATE INDEX IF NOT EXISTS idx_submissions_user
        ON submissions(user_id, id DESC);

        CREATE INDEX IF NOT EXISTS idx_submissions_problem
        ON submissions(problem_id);

        CREATE INDEX IF NOT EXISTS idx_submissions_contest
        ON submissions(contest_id, user_id, problem_id);

        CREATE TABLE IF NOT EXISTS contest_custom_problems(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            contest_id INTEGER NOT NULL
                REFERENCES contests(id) ON DELETE CASCADE,
            problem_id TEXT NOT NULL,
            title TEXT NOT NULL,
            statement TEXT NOT NULL DEFAULT '',
            input_desc TEXT NOT NULL DEFAULT '',
            output_desc TEXT NOT NULL DEFAULT '',
            subtasks_json TEXT NOT NULL DEFAULT '[]',
            total_score REAL NOT NULL DEFAULT 10,
            time_limit REAL NOT NULL DEFAULT 1,
            memory_limit INTEGER NOT NULL DEFAULT 256000,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(contest_id, problem_id)
        );
        """)
