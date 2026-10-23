import time, requests

class JudgeError(RuntimeError): pass

class Judge0:
    def __init__(self, base_url, api_key='', session=None):
        self.base_url=base_url.rstrip('/')
        self.api_key=api_key
        self.session=session or requests.Session()
    def run(self, source, language_id, stdin, expected, time_limit=1.0, memory_limit=256000):
        headers={'Content-Type':'application/json'}
        if self.api_key: headers['X-Auth-Token']=self.api_key
        payload={'source_code':source,'language_id':int(language_id),'stdin':stdin,'expected_output':expected,'cpu_time_limit':float(time_limit),'wall_time_limit':max(2.0,float(time_limit)*3),'memory_limit':int(memory_limit)}
        try:
            r=self.session.post(self.base_url+'/submissions?base64_encoded=false&wait=false',json=payload,headers=headers,timeout=15)
            r.raise_for_status(); token=r.json().get('token')
            if not token: raise JudgeError('Judge0 không trả token.')
            for _ in range(60):
                time.sleep(.5)
                q=self.session.get(self.base_url+'/submissions/'+token+'?base64_encoded=false',headers=headers,timeout=15)
                q.raise_for_status(); data=q.json(); status=(data.get('status') or {}).get('description','Unknown')
                if status not in ('In Queue','Processing'):
                    return {'status':status,'time':data.get('time'),'memory':data.get('memory'),'stdout':data.get('stdout'),'stderr':data.get('stderr'),'compile_output':data.get('compile_output'),'message':data.get('message')}
            return {'status':'Judge Timeout','message':'Quá thời gian chờ kết quả Judge0.'}
        except requests.RequestException as e:
            raise JudgeError('Không kết nối được Judge0: '+str(e)) from e
