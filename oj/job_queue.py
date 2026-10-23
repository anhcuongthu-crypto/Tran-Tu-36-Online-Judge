
import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

from flask import current_app
from oj.database import connect
from oj.judge import Judge0, JudgeError


_pool = ThreadPoolExecutor(max_workers=2)
_lock = threading.Lock()
_jobs = {}


def enqueue(app, user_id, problem, code, language):
    job_id = uuid.uuid4().hex

    with connect(app.config['DATABASE_PATH']) as db:
        cur = db.execute(
            '''INSERT INTO submissions
            (user_id, problem_id, language_id, source_code,
             verdict, score, max_score, details_json)
            VALUES (?, ?, ?, ?, ?, 0, ?, ?)''',
            (
                user_id, problem['id'], language, code,
                'Queued', problem['total_score'], '[]'
            )
        )
        submission_id = cur.lastrowid

    with _lock:
        _jobs[job_id] = {
            'status': 'Queued',
            'submission_id': submission_id
        }

    _pool.submit(
        _run_job, app, job_id, submission_id,
        user_id, problem, code, language
    )
    return job_id, submission_id


def get_job(app, job_id, user_id):
    with _lock:
        job = _jobs.get(job_id)

    if not job:
        return None

    with connect(app.config['DATABASE_PATH']) as db:
        row = db.execute(
            '''SELECT id, problem_id, verdict, score, max_score,
                      details_json, created_at
               FROM submissions WHERE id=? AND user_id=?''',
            (job['submission_id'], user_id)
        ).fetchone()

    if not row:
        return None

    result = dict(row)
    result['details'] = json.loads(result.pop('details_json'))
    result['status'] = job['status']
    return result


def _run_job(app, job_id, submission_id,
             user_id, problem, code, language):
    with _lock:
        _jobs[job_id]['status'] = 'Processing'

    score = 0
    details = []
    statuses = []

    try:
        judge = Judge0(
            app.config['JUDGE0_URL'],
            app.config['JUDGE0_API_KEY']
        )

        for st in problem['subtasks']:
            sub_ok = True
            sub_details = []

            for i, test in enumerate(st['tests'], 1):
                result = judge.run(
                    code, language,
                    test['input'], test['output'],
                    problem.get('time_limit', 1),
                    problem.get('memory_limit', 256000)
                )
                status = result['status']
                statuses.append(status)
                sub_ok = sub_ok and status == 'Accepted'

                sub_details.append({
                    'test': i,
                    'status': status,
                    'time': result.get('time'),
                    'memory': result.get('memory'),
                    'message': (
                        result.get('compile_output')
                        or result.get('stderr')
                        or result.get('message')
                        or ''
                    )[:1000]
                })

                if status in ('Compilation Error', 'Compilation error'):
                    break

            passed = (
                sub_ok and len(sub_details) == len(st['tests'])
            )
            if passed:
                score += int(st['score'])

            details.append({
                'subtask': st['id'],
                'name': st['name'],
                'score': int(st['score']) if passed else 0,
                'tests': sub_details
            })

            if any(
                x['status'] in ('Compilation Error', 'Compilation error')
                for x in sub_details
            ):
                break

        if score == int(problem['total_score']):
            verdict = 'Accepted'
        elif score > 0:
            verdict = 'Partially Accepted'
        elif any(s in ('Compilation Error', 'Compilation error') for s in statuses):
            verdict = 'Compilation Error'
        elif 'Judge Timeout' in statuses:
            verdict = 'Judge Timeout'
        elif statuses and all(s == 'Wrong Answer' for s in statuses):
            verdict = 'Wrong Answer'
        else:
            verdict = next(
                (s for s in statuses if s != 'Accepted'),
                'Wrong Answer'
            )

    except (JudgeError, Exception) as exc:
        verdict = 'Judge Unavailable'
        details = [{
            'status': verdict,
            'message': str(exc)[:1000]
        }]

    with connect(app.config['DATABASE_PATH']) as db:
        db.execute(
            '''UPDATE submissions
               SET verdict=?, score=?, details_json=?
               WHERE id=? AND user_id=?''',
            (
                verdict, score,
                json.dumps(details, ensure_ascii=False),
                submission_id, user_id
            )
        )
        db.execute(
            '''INSERT INTO solves(user_id, problem_id, best_score)
               VALUES(?,?,?)
               ON CONFLICT(user_id,problem_id)
               DO UPDATE SET best_score=MAX(best_score,excluded.best_score)''',
            (user_id, problem['id'], score)
        )

    with _lock:
        _jobs[job_id]['status'] = 'Finished'
