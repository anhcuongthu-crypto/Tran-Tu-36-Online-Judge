
import json
import os
import re
import shutil
import unicodedata
from datetime import datetime
from pathlib import Path

from flask import (
    Flask, jsonify, request, render_template,
    session, redirect, url_for
)
from werkzeug.security import generate_password_hash, check_password_hash

from oj.database import connect, init_db
from oj.job_queue import enqueue
from oj.admin import register_admin_routes


def load_problems(path):
    data = json.loads(Path(path).read_text(encoding='utf-8'))

    if not isinstance(data, list):
        raise ValueError('Problems JSON phải là danh sách.')

    ids = set()

    for p in data:
        if not isinstance(p, dict):
            raise ValueError('Mỗi bài phải là một object.')

        if not p.get('id'):
            raise ValueError('Bài thiếu problem id.')

        if p['id'] in ids:
            raise ValueError('Trùng problem id: ' + str(p['id']))

        ids.add(p['id'])

        if not p.get('subtasks'):
            raise ValueError('Bài thiếu subtasks: ' + str(p['id']))

        if sum(int(s['score']) for s in p['subtasks']) != int(p['total_score']):
            raise ValueError('Điểm subtasks không khớp ' + str(p['id']))

        if any(not s.get('tests') for s in p['subtasks']):
            raise ValueError('Subtask thiếu test ' + str(p['id']))

    return data


def save_problems(data, path):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    tmp = target.with_suffix(target.suffix + '.tmp')
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding='utf-8'
    )
    tmp.replace(target)


def validate_problem(p):
    if not p.get('title') or not p.get('statement') or not p.get('subtasks'):
        raise ValueError('Thiếu tiêu đề, đề bài hoặc subtasks.')

    if int(p.get('total_score', 0)) <= 0:
        raise ValueError('total_score phải dương.')

    if sum(int(s['score']) for s in p['subtasks']) != int(p['total_score']):
        raise ValueError('Tổng điểm subtasks phải bằng total_score.')

    if any(not s.get('tests') for s in p['subtasks']):
        raise ValueError('Mỗi subtask cần ít nhất một test.')


def initialize_problem_file(app):
    target = Path(app.config['PROBLEMS_PATH'])
    target.parent.mkdir(parents=True, exist_ok=True)

    if target.exists():
        return

    source = Path(__file__).parent / 'data' / 'problems.json'

    if source.exists():
        if source.resolve() != target.resolve():
            shutil.copy2(source, target)
    else:
        target.write_text('[]', encoding='utf-8')


def init_problem_history(db_path):
    with connect(db_path) as db:
        db.execute('''
            CREATE TABLE IF NOT EXISTS problem_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                action TEXT NOT NULL,
                created_at TEXT NOT NULL,
                before_json TEXT NOT NULL,
                after_json TEXT NOT NULL,
                removed_json TEXT NOT NULL DEFAULT '[]'
            )
        ''')


def save_problem_history(db_path, action, before, after, removed=None):
    with connect(db_path) as db:
        db.execute('''
            INSERT INTO problem_history
                (action, created_at, before_json, after_json, removed_json)
            VALUES (?, ?, ?, ?, ?)
        ''', (
            action,
            datetime.now().isoformat(timespec='seconds'),
            json.dumps(before, ensure_ascii=False),
            json.dumps(after, ensure_ascii=False),
            json.dumps(removed or [], ensure_ascii=False)
        ))


def normalize_statement(statement):
    text = unicodedata.normalize('NFKC', str(statement or ''))
    text = text.casefold()
    return re.sub(r'\s+', ' ', text).strip()


def remove_duplicate_problems(bank):
    seen = set()
    result = []
    removed = []

    for problem in bank:
        statement = normalize_statement(problem.get('statement', ''))

        # Không tự động xóa bài thiếu đề bài.
        if not statement:
            result.append(problem)
        elif statement in seen:
            removed.append(problem)
        else:
            seen.add(statement)
            result.append(problem)

    return result, removed


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)

    app.config.from_mapping(
        SECRET_KEY=os.getenv('SECRET_KEY', 'dev-change-me'),
        DATABASE_PATH=os.getenv(
            'DATABASE_PATH',
            str(Path(app.instance_path) / 'online_judge.sqlite3')
        ),
        PROBLEMS_PATH=os.getenv(
            'PROBLEMS_PATH',
            str(Path(__file__).parent / 'data' / 'problems.json')
        ),
        JUDGE0_URL=os.getenv('JUDGE0_URL', 'https://ce.judge0.com'),
        JUDGE0_API_KEY=os.getenv('JUDGE0_API_KEY', ''),
        ADMIN_USERNAME=os.getenv('ADMIN_USERNAME', 'admin'),
        ADMIN_PASSWORD=os.getenv('ADMIN_PASSWORD', ''),
        TESTING=False
    )

    if test_config:
        app.config.update(test_config)

    Path(app.config['DATABASE_PATH']).parent.mkdir(
        parents=True,
        exist_ok=True
    )

    initialize_problem_file(app)
    init_db(app.config['DATABASE_PATH'])
    init_problem_history(app.config['DATABASE_PATH'])

    if app.config.get('ADMIN_PASSWORD'):
        with connect(app.config['DATABASE_PATH']) as db:
            row = db.execute(
                'SELECT id FROM users WHERE username=?',
                (app.config['ADMIN_USERNAME'],)
            ).fetchone()

            if row:
                db.execute(
                    'UPDATE users SET is_admin=1, approved=1 WHERE id=?',
                    (row['id'],)
                )
            else:
                db.execute(
                    '''INSERT INTO users
                       (username, password_hash, is_admin, approved)
                       VALUES (?, ?, 1, 1)''',
                    (
                        app.config['ADMIN_USERNAME'],
                        generate_password_hash(app.config['ADMIN_PASSWORD'])
                    )
                )

    def problems():
        return load_problems(app.config['PROBLEMS_PATH'])

    def current_user():
        uid = session.get('user_id')

        if not uid:
            return None

        with connect(app.config['DATABASE_PATH']) as db:
            row = db.execute(
                'SELECT id, username, is_admin, approved FROM users WHERE id=?',
                (uid,)
            ).fetchone()

        return dict(row) if row else None

    def admin_required():
        user = current_user()
        return user if user and user['is_admin'] else None

    def parse_contest_time(value):
        if not isinstance(value, str) or not value.strip():
            raise ValueError('Thiếu thời gian bắt đầu hoặc kết thúc.')

        try:
            result = datetime.fromisoformat(value.strip())
        except ValueError:
            raise ValueError('Thời gian không hợp lệ.')

        if result.tzinfo is not None:
            raise ValueError('Hãy nhập thời gian địa phương.')

        return result

    def contest_state(contest):
        now = datetime.now()
        start = parse_contest_time(contest['start_time'])
        end = parse_contest_time(contest['end_time'])

        if now < start:
            return 'upcoming'

        if now >= end:
            return 'ended'

        return 'running'

    def get_contest_data(contest_id, allow_hidden=False):
        with connect(app.config['DATABASE_PATH']) as db:
            contest = db.execute(
                'SELECT * FROM contests WHERE id=?',
                (contest_id,)
            ).fetchone()

            if not contest:
                return None

            if not contest['is_visible'] and not allow_hidden:
                return None

            ids = db.execute(
                '''SELECT problem_id FROM contest_problems
                   WHERE contest_id=?
                   ORDER BY position, problem_id''',
                (contest_id,)
            ).fetchall()

        item = dict(contest)
        item['is_visible'] = bool(item['is_visible'])
        item['problem_ids'] = [r['problem_id'] for r in ids]

        bank = {p['id']: p for p in problems()}

        item['problems'] = [
            {
                'id': pid,
                'title': bank[pid].get('title', pid),
                'total_score': bank[pid]['total_score'],
                'url': url_for('problem_page', pid=pid)
            }
            for pid in item['problem_ids']
            if pid in bank
        ]

        item['state'] = contest_state(item)
        return item

    @app.get('/health')
    def health():
        try:
            return jsonify(ok=True, problem_count=len(problems()))
        except Exception as e:
            return jsonify(ok=False, error=str(e)), 500

    @app.get('/')
    def home():
        return render_template('index.html')

    @app.get('/problems/<pid>')
    def problem_page(pid):
        p = next((x for x in problems() if x['id'] == pid), None)

        if p:
            return render_template('problem.html', problem=p)

        return render_template('not_found.html'), 404

    @app.get('/api/problems')
    def api_problems():
        return jsonify([
            {
                'id': p['id'],
                'title': p['title'],
                'category': p.get('category', ''),
                'difficulty': p.get('difficulty', ''),
                'total_score': p['total_score']
            }
            for p in problems()
        ])

    @app.get('/api/problems/<pid>')
    def api_problem(pid):
        p = next((x for x in problems() if x['id'] == pid), None)

        if not p:
            return jsonify(error='Không tìm thấy bài'), 404

        public = {k: v for k, v in p.items() if k != 'subtasks'}
        public['subtasks'] = [
            {
                'id': s['id'],
                'name': s['name'],
                'score': s['score']
            }
            for s in p['subtasks']
        ]

        return jsonify(public)

    @app.post('/api/register')
    def register():
        d = request.get_json(silent=True) or {}
        username = str(d.get('username', '')).strip()
        password = str(d.get('password', ''))

        if not re.fullmatch(r'[A-Za-z0-9_]{3,24}', username):
            return jsonify(
                error='Tên đăng nhập 3-24 ký tự, chỉ gồm chữ, số, _.'
            ), 400

        if len(password) < 8:
            return jsonify(error='Mật khẩu tối thiểu 8 ký tự.'), 400

        if username == app.config['ADMIN_USERNAME']:
            return jsonify(
                error='Tên đăng nhập này được dành riêng cho quản trị viên.'
            ), 409

        try:
            with connect(app.config['DATABASE_PATH']) as db:
                db.execute(
                    '''INSERT INTO users
                       (username, password_hash, is_admin, approved)
                       VALUES (?, ?, 0, 0)''',
                    (username, generate_password_hash(password))
                )
        except Exception:
            return jsonify(error='Tên đăng nhập đã tồn tại.'), 409

        return jsonify(
            status='pending_approval',
            message='Đăng ký đã được ghi nhận. Hãy chờ quản trị viên duyệt.'
        ), 202

    @app.post('/api/login')
    def login():
        d = request.get_json(silent=True) or {}

        with connect(app.config['DATABASE_PATH']) as db:
            row = db.execute(
                'SELECT * FROM users WHERE username=?',
                (str(d.get('username', '')).strip(),)
            ).fetchone()

        if not row or not check_password_hash(
            row['password_hash'],
            str(d.get('password', ''))
        ):
            return jsonify(error='Sai tên đăng nhập hoặc mật khẩu.'), 401

        if not row['approved']:
            return jsonify(
                error='Tài khoản đang chờ quản trị viên duyệt.'
            ), 403

        session.clear()
        session['user_id'] = row['id']

        return jsonify(
            id=row['id'],
            username=row['username'],
            is_admin=bool(row['is_admin']),
            approved=bool(row['approved'])
        )

    @app.post('/api/logout')
    def logout():
        session.clear()
        return jsonify(ok=True)

    @app.get('/api/me')
    def me():
        user = current_user()

        if not user:
            return jsonify(user=None)

        with connect(app.config['DATABASE_PATH']) as db:
            row = db.execute(
                '''SELECT username, is_admin, approved,
                          avatar_color, bio, created_at
                   FROM users WHERE id=?''',
                (user['id'],)
            ).fetchone()

        return jsonify(user=dict(row) if row else None)

    @app.get('/profile')
    def profile_page():
        if not current_user():
            return redirect(url_for('home'))

        return render_template('profile.html')

    @app.get('/about')
    def about_page():
        return render_template('about.html')

    @app.get('/admin')
    def admin_page():
        user = current_user()

        if not user or not user['is_admin']:
            return render_template('not_found.html'), 403

        return render_template('admin.html')

    @app.put('/api/profile')
    def update_profile():
        user = current_user()

        if not user:
            return jsonify(error='Cần đăng nhập.'), 401

        d = request.get_json(silent=True) or {}
        bio = str(d.get('bio', '')).strip()
        color = str(d.get('avatar_color', '#7c3aed'))

        if len(bio) > 240:
            return jsonify(error='Giới thiệu tối đa 240 ký tự.'), 400

        if not re.fullmatch(r'#[0-9a-fA-F]{6}', color):
            return jsonify(error='Mã màu avatar không hợp lệ.'), 400

        with connect(app.config['DATABASE_PATH']) as db:
            db.execute(
                'UPDATE users SET bio=?, avatar_color=? WHERE id=?',
                (bio, color, user['id'])
            )

        return jsonify(ok=True, bio=bio, avatar_color=color)

    @app.get('/api/submissions')
    def submission_history():
        user = current_user()

        if not user:
            return jsonify(error='Cần đăng nhập.'), 401

        with connect(app.config['DATABASE_PATH']) as db:
            rows = db.execute(
                '''SELECT id, problem_id, language_id, verdict,
                          score, max_score, details_json, created_at, contest_id
                   FROM submissions WHERE user_id=?
                   ORDER BY id DESC LIMIT 200''',
                (user['id'],)
            ).fetchall()

        result = []

        for row in rows:
            item = dict(row)
            item['details'] = json.loads(item.pop('details_json') or '[]')
            result.append(item)

        return jsonify(result)

    @app.get('/api/submissions/<int:submission_id>/status')
    def submission_status(submission_id):
        user = current_user()

        if not user:
            return jsonify(error='Cần đăng nhập.'), 401

        with connect(app.config['DATABASE_PATH']) as db:
            row = db.execute(
                '''SELECT id, problem_id, language_id, verdict,
                          score, max_score, details_json, created_at, contest_id
                   FROM submissions WHERE id=? AND user_id=?''',
                (submission_id, user['id'])
            ).fetchone()

        if not row:
            return jsonify(error='Không tìm thấy bài nộp.'), 404

        result = dict(row)
        result['details'] = json.loads(result.pop('details_json') or '[]')
        return jsonify(result)

    @app.get('/api/leaderboard')
    def leaderboard():
        with connect(app.config['DATABASE_PATH']) as db:
            rows = db.execute(
                '''SELECT u.username,
                          COALESCE(SUM(s.best_score), 0) score,
                          COUNT(CASE WHEN s.best_score>0 THEN 1 END) solved
                   FROM users u
                   LEFT JOIN solves s ON s.user_id=u.id
                   WHERE u.approved=1
                   GROUP BY u.id
                   ORDER BY score DESC, solved DESC, u.username
                   LIMIT 100'''
            ).fetchall()

        return jsonify([dict(row) for row in rows])

    @app.post('/api/submit')
    def submit():
        user = current_user()

        if not user:
            return jsonify(error='Đăng nhập trước khi nộp bài.'), 401

        d = request.get_json(silent=True) or {}
        pid = str(d.get('problem_id', ''))
        code = d.get('source_code', '')
        language = d.get('language_id', 54)

        p = next((x for x in problems() if x['id'] == pid), None)

        if not p:
            return jsonify(error='Không tìm thấy bài.'), 404

        if not isinstance(code, str) or not code.strip() or len(code) > 100000:
            return jsonify(error='Mã nguồn trống hoặc vượt 100 KB.'), 400

        try:
            language = int(language)
        except (ValueError, TypeError):
            return jsonify(error='language_id không hợp lệ.'), 400

        job_id, submission_id = enqueue(
            app, user['id'], p, code, language
        )

        return jsonify(
            submission_id=submission_id,
            job_id=job_id,
            status='Queued',
            message='Đã nhận bài. Đang chấm...'
        ), 202

    # ==================== CONTEST PAGES ====================

    @app.get('/contests')
    def contests_page():
        return render_template('contests.html')

    @app.get('/contests/<int:contest_id>')
    def contest_page(contest_id):
        return render_template(
            'contest_detail.html',
            contest_id=contest_id
        )

    # ==================== CONTEST PUBLIC API ====================

    @app.get('/api/contests')
    def api_contests():
        user = current_user()

        with connect(app.config['DATABASE_PATH']) as db:
            if user and user['is_admin']:
                rows = db.execute(
                    'SELECT * FROM contests ORDER BY start_time DESC, id DESC'
                ).fetchall()
            else:
                rows = db.execute(
                    '''SELECT * FROM contests WHERE is_visible=1
                       ORDER BY start_time DESC, id DESC'''
                ).fetchall()

        result = []

        for row in rows:
            item = dict(row)
            item['is_visible'] = bool(item['is_visible'])
            item['state'] = contest_state(item)
            result.append(item)

        return jsonify(result)

    @app.get('/api/contests/<int:contest_id>')
    def api_contest_detail(contest_id):
        user = current_user()
        item = get_contest_data(
            contest_id,
            bool(user and user['is_admin'])
        )

        if not item:
            return jsonify(error='Không tìm thấy cuộc thi.'), 404

        return jsonify(item)

    @app.get('/api/contests/<int:contest_id>/leaderboard')
    def contest_leaderboard(contest_id):
        user = current_user()

        with connect(app.config['DATABASE_PATH']) as db:
            contest = db.execute(
                'SELECT is_visible FROM contests WHERE id=?',
                (contest_id,)
            ).fetchone()

            if not contest:
                return jsonify(error='Không tìm thấy cuộc thi.'), 404

            if not contest['is_visible'] and not (user and user['is_admin']):
                return jsonify(error='Không tìm thấy cuộc thi.'), 404

            rows = db.execute(
                '''SELECT u.username,
                          COALESCE(SUM(best.score), 0) AS score,
                          COUNT(CASE WHEN best.solved=1 THEN 1 END) AS solved
                   FROM users u
                   LEFT JOIN (
                       SELECT user_id, problem_id, MAX(score) AS score,
                              MAX(CASE WHEN verdict='Accepted' THEN 1 ELSE 0 END) AS solved
                       FROM submissions
                       WHERE contest_id=?
                         AND verdict NOT IN ('Queued', 'Processing', 'Judge Unavailable')
                       GROUP BY user_id, problem_id
                   ) best ON best.user_id=u.id
                   WHERE u.approved=1
                   GROUP BY u.id
                   ORDER BY score DESC, solved DESC, u.username ASC''',
                (contest_id,)
            ).fetchall()

        return jsonify([dict(row) for row in rows])

    # ==================== CONTEST SUBMISSION ====================

    @app.post('/api/contests/<int:contest_id>/submit')
    def contest_submit(contest_id):
        user = current_user()

        if not user:
            return jsonify(error='Đăng nhập trước khi nộp bài.'), 401

        d = request.get_json(silent=True) or {}
        pid = str(d.get('problem_id', ''))
        code = d.get('source_code', '')
        language = d.get('language_id', 54)

        if not isinstance(code, str) or not code.strip() or len(code) > 100000:
            return jsonify(error='Mã nguồn trống hoặc vượt 100 KB.'), 400

        try:
            language = int(language)
        except (ValueError, TypeError):
            return jsonify(error='language_id không hợp lệ.'), 400

        with connect(app.config['DATABASE_PATH']) as db:
            contest = db.execute(
                'SELECT * FROM contests WHERE id=? AND is_visible=1',
                (contest_id,)
            ).fetchone()

            if not contest:
                return jsonify(error='Không tìm thấy cuộc thi.'), 404

            member = db.execute(
                '''SELECT 1 FROM contest_problems
                   WHERE contest_id=? AND problem_id=?''',
                (contest_id, pid)
            ).fetchone()

        now = datetime.now()
        start = parse_contest_time(contest['start_time'])
        end = parse_contest_time(contest['end_time'])

        if now < start:
            return jsonify(error='Cuộc thi chưa bắt đầu.'), 403

        if now >= end:
            return jsonify(error='Cuộc thi đã kết thúc.'), 403

        if not member:
            return jsonify(error='Bài này không thuộc cuộc thi.'), 403

        p = next((x for x in problems() if x['id'] == pid), None)

        if not p:
            return jsonify(error='Không tìm thấy bài.'), 404

        job_id, submission_id = enqueue(
            app, user['id'], p, code, language,
            contest_id=contest_id
        )

        return jsonify(
            submission_id=submission_id,
            job_id=job_id,
            status='Queued',
            message='Đã nhận bài thi. Đang chấm...'
        ), 202

    # ==================== ADMIN CONTEST API ====================

    @app.get('/api/admin/contests')
    def admin_list_contests():
        if not admin_required():
            return jsonify(error='Chỉ admin.'), 403

        with connect(app.config['DATABASE_PATH']) as db:
            rows = db.execute(
                'SELECT * FROM contests ORDER BY id DESC'
            ).fetchall()

        result = []

        for row in rows:
            item = dict(row)
            item['is_visible'] = bool(item['is_visible'])
            item['state'] = contest_state(item)
            result.append(item)

        return jsonify(result)

    @app.post('/api/admin/contests')
    def admin_create_contest():
        admin = admin_required()

        if not admin:
            return jsonify(error='Chỉ admin.'), 403

        d = request.get_json(silent=True) or {}
        title = str(d.get('title', '')).strip()
        description = str(d.get('description', '')).strip()
        problem_ids = d.get('problem_ids', [])
        visible = bool(d.get('is_visible', True))

        if not title or len(title) > 160:
            return jsonify(error='Tên cuộc thi không hợp lệ.'), 400

        if len(description) > 5000:
            return jsonify(error='Mô tả tối đa 5000 ký tự.'), 400

        if not isinstance(problem_ids, list) or not problem_ids:
            return jsonify(error='Chọn ít nhất một bài thi.'), 400

        try:
            start = parse_contest_time(d.get('start_time'))
            end = parse_contest_time(d.get('end_time'))

            if end <= start:
                return jsonify(
                    error='Thời gian kết thúc phải sau thời gian bắt đầu.'
                ), 400

            bank = {p['id'] for p in problems()}
            ids = []

            for value in problem_ids:
                pid = str(value)

                if pid not in bank:
                    return jsonify(error='Không tìm thấy bài: ' + pid), 400

                if pid not in ids:
                    ids.append(pid)

        except (ValueError, TypeError) as exc:
            return jsonify(error=str(exc)), 400

        with connect(app.config['DATABASE_PATH']) as db:
            cur = db.execute(
                '''INSERT INTO contests
                   (title, description, start_time, end_time, is_visible, created_by)
                   VALUES (?, ?, ?, ?, ?, ?)''',
                (
                    title,
                    description,
                    start.isoformat(timespec='minutes'),
                    end.isoformat(timespec='minutes'),
                    int(visible),
                    admin['id']
                )
            )

            contest_id = cur.lastrowid

            db.executemany(
                '''INSERT INTO contest_problems
                   (contest_id, problem_id, position)
                   VALUES (?, ?, ?)''',
                [(contest_id, pid, i) for i, pid in enumerate(ids)]
            )

        return jsonify(ok=True, id=contest_id), 201

    @app.put('/api/admin/contests/<int:contest_id>')
    def admin_update_contest(contest_id):
        if not admin_required():
            return jsonify(error='Chỉ admin.'), 403

        d = request.get_json(silent=True) or {}
        title = str(d.get('title', '')).strip()
        description = str(d.get('description', '')).strip()
        problem_ids = d.get('problem_ids', [])
        visible = bool(d.get('is_visible', True))

        if not title or len(title) > 160:
            return jsonify(error='Tên cuộc thi không hợp lệ.'), 400

        if len(description) > 5000:
            return jsonify(error='Mô tả tối đa 5000 ký tự.'), 400

        if not isinstance(problem_ids, list) or not problem_ids:
            return jsonify(error='Chọn ít nhất một bài thi.'), 400

        try:
            start = parse_contest_time(d.get('start_time'))
            end = parse_contest_time(d.get('end_time'))

            if end <= start:
                return jsonify(
                    error='Thời gian kết thúc phải sau thời gian bắt đầu.'
                ), 400

            bank = {p['id'] for p in problems()}
            ids = []

            for value in problem_ids:
                pid = str(value)

                if pid not in bank:
                    return jsonify(error='Không tìm thấy bài: ' + pid), 400

                if pid not in ids:
                    ids.append(pid)

        except (ValueError, TypeError) as exc:
            return jsonify(error=str(exc)), 400

        with connect(app.config['DATABASE_PATH']) as db:
            exists = db.execute(
                'SELECT id FROM contests WHERE id=?',
                (contest_id,)
            ).fetchone()

            if not exists:
                return jsonify(error='Không tìm thấy cuộc thi.'), 404

            db.execute(
                '''UPDATE contests SET title=?, description=?, start_time=?,
                   end_time=?, is_visible=? WHERE id=?''',
                (
                    title,
                    description,
                    start.isoformat(timespec='minutes'),
                    end.isoformat(timespec='minutes'),
                    int(visible),
                    contest_id
                )
            )

            db.execute(
                'DELETE FROM contest_problems WHERE contest_id=?',
                (contest_id,)
            )

            db.executemany(
                '''INSERT INTO contest_problems
                   (contest_id, problem_id, position)
                   VALUES (?, ?, ?)''',
                [(contest_id, pid, i) for i, pid in enumerate(ids)]
            )

        return jsonify(ok=True, id=contest_id)

    @app.delete('/api/admin/contests/<int:contest_id>')
    def admin_delete_contest(contest_id):
        if not admin_required():
            return jsonify(error='Chỉ admin.'), 403

        with connect(app.config['DATABASE_PATH']) as db:
            exists = db.execute(
                'SELECT id FROM contests WHERE id=?',
                (contest_id,)
            ).fetchone()

            if not exists:
                return jsonify(error='Không tìm thấy cuộc thi.'), 404

            submissions = db.execute(
                'SELECT COUNT(*) AS n FROM submissions WHERE contest_id=?',
                (contest_id,)
            ).fetchone()['n']

            if submissions:
                return jsonify(
                    error='Cuộc thi đã có bài nộp, không thể xóa.'
                ), 409

            db.execute('DELETE FROM contests WHERE id=?', (contest_id,))

        return jsonify(ok=True)

    # ==================== ADMIN PROBLEM API ====================

    @app.get('/api/admin/problems')
    def admin_list():
        if not admin_required():
            return jsonify(error='Chỉ admin.'), 403

        return jsonify(problems())

    @app.post('/api/admin/problems')
    def admin_create():
        if not admin_required():
            return jsonify(error='Chỉ admin.'), 403

        d = request.get_json(silent=True) or {}

        try:
            old = problems()

            if not d.get('id') or any(p['id'] == d['id'] for p in old):
                return jsonify(error='ID thiếu hoặc đã tồn tại.'), 400

            validate_problem(d)
            old.append(d)
            save_problems(old, app.config['PROBLEMS_PATH'])

            return jsonify(ok=True, id=d['id']), 201

        except Exception as e:
            return jsonify(error=str(e)), 400

    @app.put('/api/admin/problems/<pid>')
    def admin_update(pid):
        if not admin_required():
            return jsonify(error='Chỉ admin.'), 403

        d = request.get_json(silent=True) or {}

        try:
            old = problems()
            idx = next(
                (i for i, p in enumerate(old) if p['id'] == pid),
                None
            )

            if idx is None:
                return jsonify(error='Không tìm thấy bài.'), 404

            d['id'] = pid
            validate_problem(d)
            old[idx] = d
            save_problems(old, app.config['PROBLEMS_PATH'])

            return jsonify(ok=True, id=pid)

        except Exception as e:
            return jsonify(error=str(e)), 400

    @app.delete('/api/admin/problems/<pid>')
    def admin_delete(pid):
        if not admin_required():
            return jsonify(error='Chỉ admin.'), 403

        old = problems()
        new = [p for p in old if p['id'] != pid]

        if len(new) == len(old):
            return jsonify(error='Không tìm thấy bài.'), 404

        save_problems(new, app.config['PROBLEMS_PATH'])
        return jsonify(ok=True)

    # ==================== BULK RENAME ====================

    @app.put('/api/admin/problems/bulk-rename')
    def admin_bulk_rename():
        if not admin_required():
            return jsonify(error='Chỉ admin.'), 403

        data = request.get_json(silent=True) or {}
        changes = data.get('changes')

        if not isinstance(changes, list) or not changes:
            return jsonify(error='Danh sách đổi tên không hợp lệ.'), 400

        try:
            before = problems()
            by_id = {p['id']: p for p in before}
            seen = set()
            validated = []

            for item in changes:
                if not isinstance(item, dict):
                    return jsonify(error='Dữ liệu đổi tên không hợp lệ.'), 400

                pid = str(item.get('id', '')).strip()
                title = str(item.get('title', '')).strip()

                if pid not in by_id:
                    return jsonify(error=f'Không tìm thấy bài {pid}.'), 404

                if pid in seen:
                    return jsonify(error=f'Bài {pid} bị lặp.'), 400

                if not title or len(title) > 200:
                    return jsonify(
                        error=f'Tên bài {pid} phải từ 1 đến 200 ký tự.'
                    ), 400

                seen.add(pid)
                validated.append((pid, title))

            after = json.loads(json.dumps(before, ensure_ascii=False))
            after_by_id = {p['id']: p for p in after}

            for pid, title in validated:
                after_by_id[pid]['title'] = title

            save_problem_history(
                app.config['DATABASE_PATH'],
                'bulk_rename',
                before,
                after
            )
            save_problems(after, app.config['PROBLEMS_PATH'])

            return jsonify(
                ok=True,
                updated_count=len(validated),
                message=f'Đã cập nhật tên {len(validated)} bài.'
            )

        except Exception as e:
            app.logger.exception('Lỗi đổi tên hàng loạt')
            return jsonify(error=str(e)), 500

    # ==================== REMOVE DUPLICATES ====================

    @app.post('/api/admin/problems/remove-duplicates')
    def admin_remove_duplicates():
        if not admin_required():
            return jsonify(error='Chỉ admin.'), 403

        try:
            before = problems()
            after, removed = remove_duplicate_problems(before)

            if not removed:
                return jsonify(
                    ok=True,
                    removed_count=0,
                    remaining_count=len(after),
                    message='Không tìm thấy bài trùng.'
                )

            save_problem_history(
                app.config['DATABASE_PATH'],
                'remove_duplicates',
                before,
                after,
                removed
            )
            save_problems(after, app.config['PROBLEMS_PATH'])

            return jsonify(
                ok=True,
                removed_count=len(removed),
                remaining_count=len(after),
                removed=[
                    {
                        'id': p.get('id'),
                        'title': p.get('title', '')
                    }
                    for p in removed
                ],
                message=f'Đã loại {len(removed)} bài trùng.'
            )

        except Exception as e:
            app.logger.exception('Lỗi lọc bài trùng')
            return jsonify(error=str(e)), 500

    # ==================== PROBLEM HISTORY ====================

    @app.get('/api/admin/problem-history')
    def admin_problem_history():
        if not admin_required():
            return jsonify(error='Chỉ admin.'), 403

        with connect(app.config['DATABASE_PATH']) as db:
            rows = db.execute('''
                SELECT id, action, created_at
                FROM problem_history
                ORDER BY id DESC
                LIMIT 200
            ''').fetchall()

        return jsonify([dict(row) for row in rows])

    @app.get('/api/admin/problem-history/<int:history_id>')
    def admin_problem_history_detail(history_id):
        if not admin_required():
            return jsonify(error='Chỉ admin.'), 403

        with connect(app.config['DATABASE_PATH']) as db:
            row = db.execute(
                'SELECT * FROM problem_history WHERE id=?',
                (history_id,)
            ).fetchone()

        if not row:
            return jsonify(error='Không tìm thấy lịch sử.'), 404

        item = dict(row)
        item['before'] = json.loads(item.pop('before_json'))
        item['after'] = json.loads(item.pop('after_json'))
        item['removed'] = json.loads(item.pop('removed_json'))

        return jsonify(item)

    @app.post('/api/admin/problem-history/<int:history_id>/restore')
    def admin_restore_problem_history(history_id):
        if not admin_required():
            return jsonify(error='Chỉ admin.'), 403

        try:
            with connect(app.config['DATABASE_PATH']) as db:
                row = db.execute(
                    'SELECT * FROM problem_history WHERE id=?',
                    (history_id,)
                ).fetchone()

            if not row:
                return jsonify(error='Không tìm thấy lịch sử.'), 404

            target = json.loads(row['before_json'])

            if not isinstance(target, list):
                return jsonify(error='Dữ liệu lịch sử không hợp lệ.'), 400

            # Kiểm tra dữ liệu khôi phục trước khi ghi file.
            temp_path = Path(app.config['PROBLEMS_PATH']).with_suffix('.restore.tmp')
            temp_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path.write_text(
                json.dumps(target, ensure_ascii=False, indent=2),
                encoding='utf-8'
            )

            try:
                load_problems(temp_path)
            finally:
                if temp_path.exists():
                    temp_path.unlink()

            current = problems()

            save_problem_history(
                app.config['DATABASE_PATH'],
                'before_restore',
                current,
                target
            )
            save_problems(target, app.config['PROBLEMS_PATH'])

            return jsonify(
                ok=True,
                restored_count=len(target),
                message='Đã khôi phục danh sách bài.'
            )

        except Exception as e:
            app.logger.exception('Lỗi khôi phục lịch sử')
            return jsonify(error=str(e)), 500

    # ==================== ADMIN PAGES ====================

    @app.get('/admin/problem-history')
    def admin_problem_history_page():
        if not admin_required():
            return render_template('not_found.html'), 403

        return render_template('problem_history.html')

    @app.get('/admin/bulk-rename')
    def admin_bulk_rename_page():
        if not admin_required():
            return render_template('not_found.html'), 403

        return render_template('bulk_rename.html')

    register_admin_routes(
        app,
        connect,
        current_user,
        app.config['DATABASE_PATH']
    )

    return app


app = create_app()


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
