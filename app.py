import json, os, re
from pathlib import Path
from flask import Flask, jsonify, request, render_template, session, redirect, url_for
from werkzeug.security import generate_password_hash, check_password_hash
from oj.database import connect, init_db
from oj.judge import Judge0, JudgeError
from oj.admin import register_admin_routes

def load_problems(path):
    data=json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(data,list): raise ValueError('Problems JSON phải là danh sách.')
    ids=set()
    for p in data:
        if p.get('id') in ids: raise ValueError('Trùng problem id: '+str(p.get('id')))
        ids.add(p.get('id'))
        if sum(int(s['score']) for s in p['subtasks']) != int(p['total_score']): raise ValueError('Điểm subtasks không khớp '+p['id'])
        if not p['subtasks'] or any(not s.get('tests') for s in p['subtasks']): raise ValueError('Subtask thiếu test '+p['id'])
    return data

def create_app(test_config=None):
    app=Flask(__name__,instance_relative_config=True)
    app.config.from_mapping(SECRET_KEY=os.getenv('SECRET_KEY','dev-change-me'),DATABASE_PATH=os.getenv('DATABASE_PATH',str(Path(app.instance_path)/'online_judge.sqlite3')),PROBLEMS_PATH=os.getenv('PROBLEMS_PATH',str(Path(__file__).parent/'data/problems.json')),JUDGE0_URL=os.getenv('JUDGE0_URL','https://ce.judge0.com'),JUDGE0_API_KEY=os.getenv('JUDGE0_API_KEY',''),ADMIN_USERNAME=os.getenv('ADMIN_USERNAME','admin'),ADMIN_PASSWORD=os.getenv('ADMIN_PASSWORD',''),TESTING=False)
    if test_config: app.config.update(test_config)
    Path(app.config['DATABASE_PATH']).parent.mkdir(parents=True,exist_ok=True)
    init_db(app.config['DATABASE_PATH'])
    # Bootstrap the administrator only from private server configuration.
    if app.config.get('ADMIN_PASSWORD'):
        with connect(app.config['DATABASE_PATH']) as db:
            row = db.execute('SELECT id FROM users WHERE username=?', (app.config['ADMIN_USERNAME'],)).fetchone()
            if row:
                db.execute('UPDATE users SET is_admin=1, approved=1 WHERE id=?', (row['id'],))
            else:
                db.execute('INSERT INTO users(username,password_hash,is_admin,approved) VALUES(?,?,1,1)',
                           (app.config['ADMIN_USERNAME'], generate_password_hash(app.config['ADMIN_PASSWORD'])))
    def problems(): return load_problems(app.config['PROBLEMS_PATH'])
    def current_user():
        uid=session.get('user_id')
        if not uid: return None
        with connect(app.config['DATABASE_PATH']) as db: row=db.execute('SELECT id,username,is_admin,approved FROM users WHERE id=?',(uid,)).fetchone()
        return dict(row) if row else None
    def admin_required():
        u=current_user()
        return u if u and u['is_admin'] else None
    @app.get('/health')
    def health():
        try: return jsonify(ok=True,problem_count=len(problems()))
        except Exception as e: return jsonify(ok=False,error=str(e)),500
    @app.get('/')
    def home(): return render_template('index.html')
    @app.get('/problems/<pid>')
    def problem_page(pid):
        p=next((x for x in problems() if x['id']==pid),None)
        return (render_template('problem.html',problem=p) if p else render_template('not_found.html')), (200 if p else 404)
    @app.get('/api/problems')
    def api_problems():
        return jsonify([{'id':p['id'],'title':p['title'],'category':p.get('category',''),'difficulty':p.get('difficulty',''),'total_score':p['total_score']} for p in problems()])
    @app.get('/api/problems/<pid>')
    def api_problem(pid):
        p=next((x for x in problems() if x['id']==pid),None)
        if not p: return jsonify(error='Không tìm thấy bài'),404
        public={k:v for k,v in p.items() if k!='subtasks'}
        public['subtasks']=[{'id':s['id'],'name':s['name'],'score':s['score']} for s in p['subtasks']]
        return jsonify(public)
    @app.post('/api/register')
    def register():
        d=request.get_json(silent=True) or {}; username=str(d.get('username','')).strip(); password=str(d.get('password',''))
        if not re.fullmatch(r'[A-Za-z0-9_]{3,24}',username): return jsonify(error='Tên đăng nhập 3-24 ký tự, chỉ gồm chữ, số, _.'),400
        if len(password)<8: return jsonify(error='Mật khẩu tối thiểu 8 ký tự.'),400
        if username == app.config['ADMIN_USERNAME']:
            return jsonify(error='Tên đăng nhập này được dành riêng cho quản trị viên.'),409
        try:
            with connect(app.config['DATABASE_PATH']) as db:
                db.execute('INSERT INTO users(username,password_hash,is_admin,approved) VALUES(?,?,0,0)',
                           (username,generate_password_hash(password)))
        except Exception:
            return jsonify(error='Tên đăng nhập đã tồn tại.'),409
        return jsonify(status='pending_approval', message='Đăng ký đã được ghi nhận. Hãy liên hệ Trần Tuấn Tú và chờ quản trị viên duyệt.'),202
    @app.post('/api/login')
    def login():
        d=request.get_json(silent=True) or {}
        with connect(app.config['DATABASE_PATH']) as db: row=db.execute('SELECT * FROM users WHERE username=?',(str(d.get('username','')).strip(),)).fetchone()
        if not row or not check_password_hash(row['password_hash'],str(d.get('password',''))): return jsonify(error='Sai tên đăng nhập hoặc mật khẩu.'),401
        if not row['approved']: return jsonify(error='Tài khoản đang chờ Trần Tuấn Tú/quản trị viên duyệt.'),403
        session.clear(); session['user_id']=row['id']
        return jsonify(id=row['id'],username=row['username'],is_admin=bool(row['is_admin']),approved=bool(row['approved']))
    @app.post('/api/logout')
    def logout(): session.clear(); return jsonify(ok=True)
    @app.get('/api/me')
    def me():
        u=current_user()
        if not u: return jsonify(user=None)
        with connect(app.config['DATABASE_PATH']) as db:
            row=db.execute('SELECT username,is_admin,approved,avatar_color,bio,created_at FROM users WHERE id=?',(u['id'],)).fetchone()
        return jsonify(user=dict(row) if row else None)
    @app.route('/profile', methods=['GET'])
    def profile_page():
        if not current_user(): return redirect(url_for('home'))
        return render_template('profile.html')
    @app.get('/about')
    def about_page(): return render_template('about.html')
    @app.get('/admin')
    def admin_page():
        u=current_user()
        if not u or not u['is_admin']:
            return render_template('not_found.html'),403
        return render_template('admin.html')
    @app.put('/api/profile')
    def update_profile():
        u=current_user()
        if not u: return jsonify(error='Cần đăng nhập.'),401
        d=request.get_json(silent=True) or {}
        bio=str(d.get('bio','')).strip()
        color=str(d.get('avatar_color','#7c3aed'))
        if len(bio)>240: return jsonify(error='Giới thiệu tối đa 240 ký tự.'),400
        if not re.fullmatch(r'#[0-9a-fA-F]{6}',color): return jsonify(error='Mã màu avatar không hợp lệ.'),400
        with connect(app.config['DATABASE_PATH']) as db:
            db.execute('UPDATE users SET bio=?,avatar_color=? WHERE id=?',(bio,color,u['id']))
        return jsonify(ok=True,bio=bio,avatar_color=color)
    @app.get('/api/submissions')
    def submission_history():
        u=current_user()
        if not u: return jsonify(error='Cần đăng nhập.'),401
        with connect(app.config['DATABASE_PATH']) as db: rows=db.execute('SELECT id,problem_id,verdict,score,max_score,details_json,created_at FROM submissions WHERE user_id=? ORDER BY id DESC LIMIT 200',(u['id'],)).fetchall()
        return jsonify([dict(r,details=json.loads(r['details_json'])) for r in rows])
    @app.get('/api/leaderboard')
    def leaderboard():
        with connect(app.config['DATABASE_PATH']) as db:
            rows=db.execute('SELECT u.username,COALESCE(SUM(s.best_score),0) score,COUNT(CASE WHEN s.best_score>0 THEN 1 END) solved FROM users u LEFT JOIN solves s ON s.user_id=u.id WHERE u.approved=1 GROUP BY u.id ORDER BY score DESC,solved DESC,u.username LIMIT 100').fetchall()
        return jsonify([dict(r) for r in rows])
    @app.post('/api/submit')
    def submit():
        u=current_user()
        if not u: return jsonify(error='Đăng nhập trước khi nộp bài.'),401
        d=request.get_json(silent=True) or {}; pid=str(d.get('problem_id','')); code=d.get('source_code',''); language=d.get('language_id',54)
        p=next((x for x in problems() if x['id']==pid),None)
        if not p: return jsonify(error='Không tìm thấy bài.'),404
        if not isinstance(code,str) or not code.strip() or len(code)>100000: return jsonify(error='Mã nguồn trống hoặc vượt 100 KB.'),400
        try: language=int(language)
        except (ValueError,TypeError): return jsonify(error='language_id không hợp lệ.'),400
        judge=Judge0(app.config['JUDGE0_URL'],app.config['JUDGE0_API_KEY']); score=0; details=[]; statuses=[]
        try:
            for st in p['subtasks']:
                sub_ok=True; sub_details=[]
                for i,t in enumerate(st['tests'],1):
                    res=judge.run(code,language,t['input'],t['output'],p.get('time_limit',1),p.get('memory_limit',256000)); status=res['status']; statuses.append(status)
                    ok=status=='Accepted'; sub_ok=sub_ok and ok
                    sub_details.append({'test':i,'status':status,'time':res.get('time'),'memory':res.get('memory'),'message':(res.get('compile_output') or res.get('stderr') or res.get('message') or '')[:1000]})
                    if status in ('Compilation Error','Compilation error'): break
                details.append({'subtask':st['id'],'name':st['name'],'score':st['score'] if sub_ok and len(sub_details)==len(st['tests']) else 0,'tests':sub_details})
                if sub_ok and len(sub_details)==len(st['tests']): score+=int(st['score'])
                if any(x['status'] in ('Compilation Error','Compilation error') for x in sub_details): break
            if score==p['total_score']: verdict='Accepted'
            elif score: verdict='Partially Accepted'
            elif 'Compilation Error' in statuses or 'Compilation error' in statuses: verdict='Compilation Error'
            elif 'Judge Timeout' in statuses: verdict='Judge Timeout'
            elif statuses and all(s=='Wrong Answer' for s in statuses): verdict='Wrong Answer'
            else: verdict=next((s for s in statuses if s!='Accepted'),'Wrong Answer')
        except JudgeError as e: return jsonify(error=str(e)),503
        with connect(app.config['DATABASE_PATH']) as db:
            cur=db.execute('INSERT INTO submissions(user_id,problem_id,language_id,source_code,verdict,score,max_score,details_json) VALUES(?,?,?,?,?,?,?,?)',(u['id'],pid,language,code,verdict,score,p['total_score'],json.dumps(details,ensure_ascii=False)))
            db.execute('INSERT INTO solves(user_id,problem_id,best_score) VALUES(?,?,?) ON CONFLICT(user_id,problem_id) DO UPDATE SET best_score=MAX(best_score,excluded.best_score)',(u['id'],pid,score))
        return jsonify(submission_id=cur.lastrowid,verdict=verdict,score=score,max_score=p['total_score'],details=details)
    @app.get('/api/admin/problems')
    def admin_list():
        if not admin_required(): return jsonify(error='Chỉ admin.'),403
        return jsonify(problems())
    @app.post('/api/admin/problems')
    def admin_create():
        if not admin_required(): return jsonify(error='Chỉ admin.'),403
        d=request.get_json(silent=True) or {}
        try:
            old=problems()
            if not d.get('id') or any(p['id']==d['id'] for p in old): return jsonify(error='ID thiếu hoặc đã tồn tại.'),400
            validate_problem(d); old.append(d); save_problems(old,app.config['PROBLEMS_PATH'])
            return jsonify(ok=True,id=d['id']),201
        except Exception as e: return jsonify(error=str(e)),400
    @app.put('/api/admin/problems/<pid>')
    def admin_update(pid):
        if not admin_required(): return jsonify(error='Chỉ admin.'),403
        d=request.get_json(silent=True) or {}
        try:
            old=problems(); idx=next((i for i,p in enumerate(old) if p['id']==pid),None)
            if idx is None: return jsonify(error='Không tìm thấy bài.'),404
            d['id']=pid; validate_problem(d); old[idx]=d; save_problems(old,app.config['PROBLEMS_PATH'])
            return jsonify(ok=True,id=pid)
        except Exception as e: return jsonify(error=str(e)),400
    @app.delete('/api/admin/problems/<pid>')
    def admin_delete(pid):
        if not admin_required(): return jsonify(error='Chỉ admin.'),403
        old=problems(); new=[p for p in old if p['id']!=pid]
        if len(new)==len(old): return jsonify(error='Không tìm thấy bài.'),404
        save_problems(new,app.config['PROBLEMS_PATH']); return jsonify(ok=True)
    register_admin_routes(app, connect, current_user, app.config['DATABASE_PATH'])
    return app

def validate_problem(p):
    if not p.get('title') or not p.get('statement') or not p.get('subtasks'): raise ValueError('Thiếu tiêu đề, đề bài hoặc subtasks.')
    if int(p.get('total_score',0))<=0: raise ValueError('total_score phải dương.')
    if sum(int(s['score']) for s in p['subtasks'])!=int(p['total_score']): raise ValueError('Tổng điểm subtasks phải bằng total_score.')
    if any(not s.get('tests') for s in p['subtasks']): raise ValueError('Mỗi subtask cần ít nhất một test.')

def save_problems(data,path):
    target=Path(path); target.parent.mkdir(parents=True,exist_ok=True); tmp=target.with_suffix(target.suffix+'.tmp'); tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8'); tmp.replace(target)

app=create_app()
