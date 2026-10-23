"""Administrator-only account approval routes, kept separate from the public app routes."""
from flask import jsonify


def register_admin_routes(app, connect, current_user, database_path):
    @app.get('/api/admin/users')
    def admin_users():
        user = current_user()
        if not user or not user['is_admin']:
            return jsonify(error='Chỉ admin.'), 403
        with connect(database_path) as db:
            rows = db.execute('SELECT id,username,approved,created_at FROM users WHERE is_admin=0 ORDER BY approved, id').fetchall()
        return jsonify([dict(row) for row in rows])

    @app.post('/api/admin/users/<int:user_id>/approve')
    def admin_approve_user(user_id):
        user = current_user()
        if not user or not user['is_admin']:
            return jsonify(error='Chỉ admin.'), 403
        with connect(database_path) as db:
            cur = db.execute('UPDATE users SET approved=1 WHERE id=? AND is_admin=0', (user_id,))
            if cur.rowcount != 1:
                return jsonify(error='Không tìm thấy học sinh.'), 404
        return jsonify(ok=True, user_id=user_id, approved=True)
