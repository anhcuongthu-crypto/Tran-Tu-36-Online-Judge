
import time
import requests


class JudgeError(RuntimeError):
    pass


class Judge0:
    def __init__(self, base_url, api_key='', session=None):
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.session = session or requests.Session()

    def run(self, source, language_id, stdin, expected,
            time_limit=1.0, memory_limit=256000):
        headers = {'Content-Type': 'application/json'}

        if self.api_key:
            headers['X-Auth-Token'] = self.api_key

        payload = {
            'source_code': source,
            'language_id': int(language_id),
            'stdin': stdin,
            'expected_output': expected,
            'cpu_time_limit': float(time_limit),
            'wall_time_limit': min(
                30.0, max(2.0, float(time_limit) * 3)
            ),
            'memory_limit': int(memory_limit)
        }

        url = (
            self.base_url
            + '/submissions?base64_encoded=false&wait=false'
        )

        token = None
        last_error = None

        for attempt in range(3):
            try:
                r = self.session.post(
                    url,
                    json=payload,
                    headers=headers,
                    timeout=(5, 15)
                )

                print('Judge0 POST status:', r.status_code, flush=True)

                if r.status_code in (502, 503, 504, 429):
                    last_error = (
                        f'Judge0 HTTP {r.status_code}: {r.text[:500]}'
                    )
                    if attempt < 2:
                        time.sleep(2 ** attempt)
                        continue
                    raise JudgeError(last_error)

                r.raise_for_status()
                token = r.json().get('token')

                if not token:
                    raise JudgeError('Judge0 không trả token.')

                print('Judge0 token received: True', flush=True)
                break

            except requests.RequestException as e:
                last_error = str(e)
                if attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                raise JudgeError(
                    'Không kết nối được Judge0: ' + last_error
                ) from e

        if not token:
            raise JudgeError(last_error or 'Không tạo được submission.')

        deadline = time.monotonic() + 60
        delay = 0.5
        last_status = 'Unknown'
        last_poll_error = None

        while time.monotonic() < deadline:
            time.sleep(delay)

            try:
                q = self.session.get(
                    self.base_url + '/submissions/' + token
                    + '?base64_encoded=false',
                    headers=headers,
                    timeout=(5, 10)
                )

                if q.status_code != 200:
                    print('Judge0 GET status:', q.status_code, flush=True)
                    print('Judge0 GET body:', q.text[:1500], flush=True)

                q.raise_for_status()
                data = q.json()
                last_poll_error = None

                last_status = (
                    data.get('status') or {}
                ).get('description', 'Unknown')

                print('Judge0 poll status:', last_status, flush=True)

                if last_status not in ('In Queue', 'Processing'):
                    return {
                        'status': last_status,
                        'time': data.get('time'),
                        'memory': data.get('memory'),
                        'stdout': data.get('stdout'),
                        'stderr': data.get('stderr'),
                        'compile_output': data.get('compile_output'),
                        'message': data.get('message')
                    }

            except requests.RequestException as e:
                last_poll_error = str(e)
                print('Judge0 poll error:', str(e), flush=True)

            delay = min(delay * 1.5, 3.0)

        message = (
            'Hết thời gian chờ kết quả từ Judge0. '
            f'Trạng thái cuối: {last_status}.'
        )

        if last_poll_error:
            message += ' Lỗi polling cuối: ' + last_poll_error[:300]

        return {
            'status': 'Judge Timeout',
            'message': message
        }
