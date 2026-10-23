
import time
import requests


class JudgeError(RuntimeError):
    pass


class Judge0:
    def __init__(self, base_url, api_key='', session=None):
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.session = session or requests.Session()

    def _headers(self):
        headers = {'Content-Type': 'application/json'}

        if self.api_key:
            headers['X-Auth-Token'] = self.api_key

        return headers

    def _payload(self, source, language_id, stdin, expected,
                 time_limit, memory_limit):
        return {
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

    def _format_result(self, data):
        status_data = data.get('status') or {}

        status = status_data.get('description')

        if not status:
            status_names = {
                1: 'In Queue',
                2: 'Processing',
                3: 'Accepted',
                4: 'Wrong Answer',
                5: 'Time Limit Exceeded',
                6: 'Compilation Error',
                7: 'Runtime Error (SIGSEGV)',
                8: 'Runtime Error (SIGXFSZ)',
                9: 'Runtime Error (SIGFPE)',
                10: 'Runtime Error (SIGABRT)',
                11: 'Runtime Error (NZEC)',
                12: 'Runtime Error (Other)',
                13: 'Internal Error',
                14: 'Exec Format Error'
            }

            status = status_names.get(
                data.get('status_id'), 'Unknown'
            )

        return {
            'status': status,
            'time': data.get('time'),
            'memory': data.get('memory'),
            'stdout': data.get('stdout'),
            'stderr': data.get('stderr'),
            'compile_output': data.get('compile_output'),
            'message': data.get('message')
        }

    def run(self, source, language_id, stdin, expected,
            time_limit=1.0, memory_limit=256000):
        headers = self._headers()

        payload = self._payload(
            source,
            language_id,
            stdin,
            expected,
            time_limit,
            memory_limit
        )

        url = self.base_url + '/submissions'

        try:
            r = self.session.post(
                url,
                params={
                    'base64_encoded': 'false',
                    'wait': 'false'
                },
                json=payload,
                headers=headers,
                timeout=(5, 15)
            )

            print(
                'Judge0 POST status:', r.status_code,
                flush=True
            )

            if r.status_code in (429, 502, 503, 504):
                raise JudgeError(
                    f'Judge0 HTTP {r.status_code}: {r.text[:500]}'
                )

            r.raise_for_status()
            token = r.json().get('token')

            if not token:
                raise JudgeError('Judge0 không trả token.')

        except requests.RequestException as e:
            raise JudgeError(
                'Không kết nối được Judge0: ' + str(e)
            ) from e

        deadline = time.monotonic() + 60
        delay = 0.5
        last_status = 'Unknown'

        while time.monotonic() < deadline:
            time.sleep(delay)

            try:
                q = self.session.get(
                    self.base_url + '/submissions/' + token,
                    params={'base64_encoded': 'false'},
                    headers=headers,
                    timeout=(5, 10)
                )
                q.raise_for_status()

                data = q.json()
                result = self._format_result(data)
                last_status = result['status']

                print(
                    'Judge0 poll status:', last_status,
                    flush=True
                )

                if last_status not in ('In Queue', 'Processing'):
                    return result

            except requests.RequestException as e:
                print('Judge0 poll error:', str(e), flush=True)

            delay = min(delay * 1.5, 3.0)

        return {
            'status': 'Judge Timeout',
            'message': (
                'Hết thời gian chờ kết quả từ Judge0. '
                f'Trạng thái cuối: {last_status}.'
            )
        }

    def run_batch(self, source, language_id, tests,
                  time_limit=1.0, memory_limit=256000):
        if not tests:
            return []

        headers = self._headers()

        submissions = []

        for test in tests:
            submissions.append(
                self._payload(
                    source,
                    language_id,
                    test['input'],
                    test['output'],
                    time_limit,
                    memory_limit
                )
            )

        try:
            r = self.session.post(
                self.base_url + '/submissions/batch',
                params={'base64_encoded': 'false'},
                json={'submissions': submissions},
                headers=headers,
                timeout=(5, 30)
            )

            r.raise_for_status()
            data = r.json()

        except requests.RequestException as e:
            raise JudgeError(
                'Không gửi được batch tới Judge0: ' + str(e)
            ) from e

        if not isinstance(data, list):
            raise JudgeError(
                'Judge0 batch không trả về danh sách token.'
            )

        tokens = [item.get('token') for item in data]

        if len(tokens) != len(tests) or any(not t for t in tokens):
            raise JudgeError(
                'Judge0 batch trả về số token không hợp lệ.'
            )

        results = [None] * len(tokens)
        pending = set(range(len(tokens)))
        deadline = time.monotonic() + 60
        delay = 0.5

        while pending and time.monotonic() < deadline:
            time.sleep(delay)

            try:
                q = self.session.get(
                    self.base_url + '/submissions/batch',
                    params={
                        'tokens': ','.join(
                            tokens[i] for i in sorted(pending)
                        ),
                        'base64_encoded': 'false'
                    },
                    headers=headers,
                    timeout=(5, 15)
                )

                q.raise_for_status()
                response = q.json()
                items = response.get('submissions', [])

                by_token = {
                    item.get('token'): item
                    for item in items
                }

                for i in list(pending):
                    item = by_token.get(tokens[i])

                    if item is None:
                        continue

                    result = self._format_result(item)
                    status = result['status']

                    if status not in ('In Queue', 'Processing'):
                        results[i] = result
                        pending.remove(i)

            except requests.RequestException as e:
                print(
                    'Judge0 batch poll error:',
                    str(e),
                    flush=True
                )

            delay = min(delay * 1.5, 2.0)

        for i in pending:
            results[i] = {
                'status': 'Judge Timeout',
                'message': 'Hết thời gian chờ kết quả từ Judge0.'
            }

        return results
