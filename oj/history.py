
import json
import re
import sqlite3
import unicodedata
from datetime import datetime


def normalize_statement(value):
    value = unicodedata.normalize('NFKC', str(value or ''))
    value = value.casefold()
    return re.sub(r'\s+', ' ', value).strip()


def remove_duplicates(data):
    """
    Giữ bài xuất hiện đầu tiên khi statement trùng nhau.
    Trả về (danh sách sau lọc, danh sách bài bị loại).
    """
    seen = set()
    kept = []
    removed = []

    for problem in data:
        key = normalize_statement(problem.get('statement', ''))

        # Không tự động coi các bài không có đề là trùng.
        if not key:
            kept.append(problem)
        elif key not in seen:
            seen.add(key)
            kept.append(problem)
        else:
            removed.append(problem)

    return kept, removed


def initialize_history(db_path):
    """Tạo bảng lịch sử nếu chưa tồn tại."""
    with sqlite3.connect(db_path) as db:
        db.execute(
            '''
            CREATE TABLE IF NOT EXISTS problem_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                action TEXT NOT NULL,
                before_json TEXT NOT NULL,
                after_json TEXT NOT NULL,
                removed_json TEXT NOT NULL,
                before_count INTEGER NOT NULL,
                after_count INTEGER NOT NULL,
                removed_count INTEGER NOT NULL
            )
            '''
        )


def save_history(
    db_path,
    action,
    before,
    after,
    removed=None
):
    """Lưu trạng thái trước và sau một thao tác."""
    if removed is None:
        removed = []

    with sqlite3.connect(db_path) as db:
        cur = db.execute(
            '''
            INSERT INTO problem_history (
                created_at,
                action,
                before_json,
                after_json,
                removed_json,
                before_count,
                after_count,
                removed_count
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                datetime.now().isoformat(timespec='seconds'),
                action,
                json.dumps(before, ensure_ascii=False),
                json.dumps(after, ensure_ascii=False),
                json.dumps(removed, ensure_ascii=False),
                len(before),
                len(after),
                len(removed)
            )
        )

        return cur.lastrowid


def get_history(db_path, limit=100, offset=0):
    """Lấy danh sách lịch sử, không trả toàn bộ nội dung bài."""
    limit = max(1, min(int(limit), 500))
    offset = max(0, int(offset))

    with sqlite3.connect(db_path) as db:
        db.row_factory = sqlite3.Row

        rows = db.execute(
            '''
            SELECT
                id,
                created_at,
                action,
                before_count,
                after_count,
                removed_count
            FROM problem_history
            ORDER BY id DESC
            LIMIT ? OFFSET ?
            ''',
            (limit, offset)
        ).fetchall()

        total = db.execute(
            'SELECT COUNT(*) FROM problem_history'
        ).fetchone()[0]

    return {
        'total': total,
        'items': [dict(row) for row in rows]
    }


def get_history_detail(db_path, history_id):
    """Lấy đầy đủ dữ liệu trước/sau của một bản ghi."""
    with sqlite3.connect(db_path) as db:
        db.row_factory = sqlite3.Row

        row = db.execute(
            '''
            SELECT *
            FROM problem_history
            WHERE id = ?
            ''',
            (history_id,)
        ).fetchone()

    if row is None:
        return None

    result = dict(row)

    result['before'] = json.loads(result.pop('before_json'))
    result['after'] = json.loads(result.pop('after_json'))
    result['removed'] = json.loads(result.pop('removed_json'))

    return result


def restore_history_data(db_path, history_id):
    """
    Lấy trạng thái trước thao tác để phục vụ khôi phục.
    Hàm này KHÔNG tự ghi đè file JSON.
    """
    detail = get_history_detail(db_path, history_id)

    if detail is None:
        return None

    return detail['before']
