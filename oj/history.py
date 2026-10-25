
import json
import re
import unicodedata
from datetime import datetime

from oj.database import connect


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

        # Không tự động coi bài không có đề là trùng.
        if not key:
            kept.append(problem)
        elif key not in seen:
            seen.add(key)
            kept.append(problem)
        else:
            removed.append(problem)

    return kept, removed


def initialize_history(db_path):
    """
    Bảng problem_history được tạo bởi init_db()
    trong oj/database.py.
    """
    # Đảm bảo hàm cũ trong app.py vẫn gọi được.
    # Không tạo bảng bằng cú pháp riêng của SQLite.
    return None


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

    with connect(db_path) as db:
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

    with connect(db_path) as db:
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

        count_row = db.execute(
            'SELECT COUNT(*) AS total FROM problem_history'
        ).fetchone()

    return {
        'total': count_row['total'],
        'items': [dict(row) for row in rows]
    }


def get_history_detail(db_path, history_id):
    """Lấy đầy đủ dữ liệu trước/sau của một bản ghi."""
    with connect(db_path) as db:
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
    Không tự ghi đè file JSON.
    """
    detail = get_history_detail(db_path, history_id)

    if detail is None:
        return None

    return detail['before']
