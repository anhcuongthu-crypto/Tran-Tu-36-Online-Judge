
import os
import sqlite3
from pathlib import Path
from contextlib import contextmanager


def is_postgres(path):
    return str(path).startswith((
        'postgres://',
        'postgresql://',
        'postgresql+psycopg://'
    ))


def connect(path):
    path = str(path)

    if is_postgres(path):
        import psycopg
        from psycopg.rows import dict_row

        url = path.replace(
            'postgresql+psycopg://', 'postgresql://', 1
        )
        url = url.replace('postgres://', 'postgresql://', 1)

        return psycopg.connect(
            url,
            row_factory=dict_row,
            connect_timeout=20
        )

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=20)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    return db


def init_db(path):
    if is_postgres(path):
        init_postgres(path)
    else:
        init_sqlite(path)


def init_sqlite(path):
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
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
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

        CREATE INDEX IF NOT EXISTS idx_submissions_user
        ON submissions(user_id, id DESC);

        CREATE INDEX IF NOT EXISTS idx_submissions_problem
        ON submissions(problem_id);
        """)

        user_columns = {
            row['name'] for row in db.execute(
                'PRAGMA table_info(users)'
            ).fetchall()
        }

        if 'approved' not in user_columns:
            db.execute(
                "ALTER TABLE users ADD COLUMN approved INTEGER NOT NULL DEFAULT 0"
            )
        if 'avatar_color' not in user_columns:
            db.execute(
                "ALTER TABLE users ADD COLUMN avatar_color TEXT NOT NULL DEFAULT '#7c3aed'"
            )
        if 'bio' not in user_columns:
            db.execute(
                "ALTER TABLE users ADD COLUMN bio TEXT NOT NULL DEFAULT ''"
            )

        submission_columns = {
            row['name'] for row in db.execute(
                'PRAGMA table_info(submissions)'
            ).fetchall()
        }

        if 'contest_id' not in submission_columns:
            db.execute("""
                ALTER TABLE submissions ADD COLUMN contest_id INTEGER
                REFERENCES contests(id) ON DELETE SET NULL
            """)

        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_submissions_contest
            ON submissions(contest_id, user_id, problem_id)
        """)


def init_postgres(path):
    statements = [
        """CREATE TABLE IF NOT EXISTS users(
            id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            is_admin INTEGER NOT NULL DEFAULT 0,
            approved INTEGER NOT NULL DEFAULT 0,
            avatar_color TEXT NOT NULL DEFAULT '#7c3aed',
            bio TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS submissions(
            id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            user_id BIGINT NOT NULL REFERENCES users(id),
            problem_id TEXT NOT NULL,
            language_id INTEGER NOT NULL,
            source_code TEXT NOT NULL,
            verdict TEXT NOT NULL,
            score INTEGER NOT NULL DEFAULT 0,
            max_score INTEGER NOT NULL DEFAULT 100,
            details_json TEXT NOT NULL DEFAULT '[]',
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            contest_id BIGINT REFERENCES contests(id) ON DELETE SET NULL
        )""",
        """CREATE TABLE IF NOT EXISTS solves(
            user_id BIGINT NOT NULL REFERENCES users(id),
            problem_id TEXT NOT NULL,
            best_score INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(user_id, problem_id)
        )""",
        """CREATE TABLE IF NOT EXISTS contests(
            id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            title TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            start_time TEXT NOT NULL,
            end_time TEXT NOT NULL,
            is_visible INTEGER NOT NULL DEFAULT 1,
            created_by BIGINT REFERENCES users(id),
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""",
        """CREATE TABLE IF NOT EXISTS contest_problems(
            contest_id BIGINT NOT NULL REFERENCES contests(id) ON DELETE CASCADE,
            problem_id TEXT NOT NULL,
            position INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(contest_id, problem_id)
        )""",
        """CREATE TABLE IF NOT EXISTS contest_custom_problems(
            id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            contest_id BIGINT NOT NULL REFERENCES contests(id) ON DELETE CASCADE,
            problem_id TEXT NOT NULL,
            title TEXT NOT NULL,
            statement TEXT NOT NULL DEFAULT '',
            input_desc TEXT NOT NULL DEFAULT '',
            output_desc TEXT NOT NULL DEFAULT '',
            subtasks_json TEXT NOT NULL DEFAULT '[]',
            total_score DOUBLE PRECISION NOT NULL DEFAULT 10,
            time_limit DOUBLE PRECISION NOT NULL DEFAULT 1,
            memory_limit INTEGER NOT NULL DEFAULT 256000,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(contest_id, problem_id)
        )""",
        "CREATE INDEX IF NOT EXISTS idx_submissions_user ON submissions(user_id, id DESC)",
        "CREATE INDEX IF NOT EXISTS idx_submissions_problem ON submissions(problem_id)",
        "CREATE INDEX IF NOT EXISTS idx_submissions_contest ON submissions(contest_id, user_id, problem_id)",
        """ALTER TABLE users ADD COLUMN IF NOT EXISTS approved INTEGER NOT NULL DEFAULT 0""",
        """ALTER TABLE users ADD COLUMN IF NOT EXISTS avatar_color TEXT NOT NULL DEFAULT '#7c3aed'""",
        """ALTER TABLE users ADD COLUMN IF NOT EXISTS bio TEXT NOT NULL DEFAULT ''""",
        """ALTER TABLE submissions ADD COLUMN IF NOT EXISTS contest_id BIGINT REFERENCES contests(id) ON DELETE SET NULL"""
    ]

    with connect(path) as db:
        # Tạo contests trước submissions để khóa ngoại hợp lệ.
        ordered = [statements[i] for i in (0, 3, 1, 2, 4, 5)]
        ordered.extend(statements[6:])
        for sql in ordered:
            db.execute(sql)
