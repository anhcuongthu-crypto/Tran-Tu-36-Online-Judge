"""Tests for SQLite schema/migrations that do not require Flask."""
from oj.database import connect, init_db


def test_new_database_has_approval_and_unique_username(tmp_path):
    path = tmp_path / 'db.sqlite3'
    init_db(path)
    with connect(path) as db:
        cols = {r['name'] for r in db.execute('PRAGMA table_info(users)')}
        assert {'username', 'password_hash', 'is_admin', 'approved'} <= cols
        db.execute("INSERT INTO users(username,password_hash,approved) VALUES('student','hash',0)")
        try:
            db.execute("INSERT INTO users(username,password_hash,approved) VALUES('student','hash',0)")
        except Exception:
            pass
        else:
            raise AssertionError('Duplicate username was accepted')


def test_existing_database_migrates_approval_column(tmp_path):
    path = tmp_path / 'old.sqlite3'
    with connect(path) as db:
        db.execute('CREATE TABLE users(id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, is_admin INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)')
    init_db(path)
    with connect(path) as db:
        cols = {r['name'] for r in db.execute('PRAGMA table_info(users)')}
        assert 'approved' in cols
        assert db.execute('SELECT approved FROM users').fetchall() == []
