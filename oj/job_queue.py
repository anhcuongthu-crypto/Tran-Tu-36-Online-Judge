
import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

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
                user_id,
                problem['id'],
                language,
                code,
                'Queued',
                problem['total_score'],
                '[]'
            )
        )
        submission_id = cur.lastrowid

    with _lock:
        _jobs[job_id] = {
            'status': 'Queued',
            'submission_id': submission_id
        }

    _pool.submit(
        _run_job,
        app,
        job_id,
        submission_id,
        user_id,
        problem,
        code,
        language
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
               FROM submissions
               WHERE id=? AND user_id=?''',
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
    started = time.perf_counter()

    with _lock:
        if job_id in _jobs:
            _jobs[job_id]['status'] = 'Processing'

    with connect(app.config['DATABASE_PATH']) as db:
        db.execute(
            '''UPDATE submissions
               SET verdict=?
               WHERE id=? AND user_id=?''',
            ('Processing', submission_id, user_id)
        )

    score = 0
    details = []
    statuses = []

    try:
        judge = Judge0(
            app.config['JUDGE0_URL'],
            app.config.get('JUDGE0_API_KEY', '')
        )

        for st in problem['subtasks']:
            subtask_started = time.perf_counter()
            tests = st['tests']

            batch_tests = [
                {
                    'input': test['input'],
                    'output': test['output']
                }
                for test in tests
            ]

            results = judge.run_batch(
                code,
                language,
                batch_tests,
                problem.get('time_limit', 1),
                problem.get('memory_limit', 256000)
            )

            sub_details = []
            sub_ok = len(results) == len(tests)

            for i, test in enumerate(tests):
                if i < len(results):
                    result = results[i]
                else:
                    result = {
                        'status': 'Judge Unavailable',
                        'message': 'Judge0 không trả đủ kết quả.'
                    }

                status = result.get('status', 'Unknown')
                statuses.append(status)

                sub_details.append({
                    'test': i + 1,
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

                if status != 'Accepted':
                    sub_ok = False

            passed = (
                sub_ok
                and len(sub_details) == len(tests)
                and len(tests) > 0
            )

            if passed:
                score += int(st['score'])

            details.append({
                'subtask': st['id'],
                'name': st['name'],
                'score': int(st['score']) if passed else 0,
                'tests': sub_details
            })

            print(
                f"[OJ SPEED] problem={problem['id']} "
                f"subtask={st['id']} tests={len(tests)} "
                f"elapsed={time.perf_counter() - subtask_started:.2f}s",
                flush=True
            )

            if any(
                x['status'] in (
                    'Compilation Error',
                    'Compilation error'
                )
                for x in sub_details
            ):
                break

        if score == int(problem['total_score']):
            verdict = 'Accepted'
        elif score > 0:
            verdict = 'Partially Accepted'
        elif any(
            s in ('Compilation Error', 'Compilation error')
            for s in statuses
        ):
            verdict = 'Compilation Error'
        elif 'Judge Timeout' in statuses:
            verdict = 'Judge Timeout'
        elif statuses and all(s == 'Wrong Answer' for s in statuses):
            verdict = 'Wrong Answer'
        elif any(s == 'Time Limit Exceeded' for s in statuses):
            verdict = 'Time Limit Exceeded'
        elif any(s == 'Memory Limit Exceeded' for s in statuses):
            verdict = 'Memory Limit Exceeded'
        elif any(s.startswith('Runtime Error') for s in statuses):
            verdict = 'Runtime Error'
        elif any(s == 'Judge Unavailable' for s in statuses):
            verdict = 'Judge Unavailable'
        else:
            verdict = next(
                (s for s in statuses if s != 'Accepted'),
                'Wrong Answer'
            )

    except Exception as exc:
        verdict = 'Judge Unavailable'
        details = [{
            'status': verdict,
            'message': str(exc)[:1000]
        }]

        print(
            f"[OJ ERROR] submission={submission_id}: {exc}",
            flush=True
        )

    total_elapsed = time.perf_counter() - started

    print(
        f"[OJ SPEED] submission={submission_id} "
        f"verdict={verdict} score={score}/"
        f"{problem['total_score']} total={total_elapsed:.2f}s",
        flush=True
    )

    try:
        with connect(app.config['DATABASE_PATH']) as db:
            db.execute(
                '''UPDATE submissions
                   SET verdict=?, score=?, details_json=?
                   WHERE id=? AND user_id=?''',
                (
                    verdict,
                    score,
                    json.dumps(details, ensure_ascii=False),
                    submission_id,
                    user_id
                )
            )

            db.execute(
                '''INSERT INTO solves(user_id, problem_id, best_score)
                   VALUES(?,?,?)
                   ON CONFLICT(user_id,problem_id)
                   DO UPDATE SET best_score =
                       MAX(best_score, excluded.best_score)''',
                (user_id, problem['id'], score)
            )

    except Exception as exc:
        print(
            f"[OJ ERROR] Cannot save submission={submission_id}: {exc}",
            flush=True
        )

    with _lock:
        if job_id in _jobs:
            _jobs[job_id]['status'] = 'Finished'
