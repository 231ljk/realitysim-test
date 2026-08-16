# -*- coding: utf-8 -*-
"""现实模拟 RealitySim 后端（Python 版）
兼容原 Node.js 版全部接口：/api/auth、/api/chat、/api/posts、/api/game、/api/users
WebSocket: /ws/chat
启动: python server.py  （默认端口 3000，可被 .env 的 PORT 覆盖）
"""
import os
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from flask_sock import Sock

from src import admin, auth, chat, db, friends, game, posts
from src.config import CORS_ORIGINS, PORT, ROOT, UPLOAD_DIR

app = Flask(__name__)
app.json.ensure_ascii = False

# 上传目录
os.makedirs(UPLOAD_DIR, exist_ok=True)

# CORS
if CORS_ORIGINS == ['*']:
    CORS(app, resources={r'/*': {'origins': '*'}})
else:
    CORS(app, resources={r'/*': {'origins': CORS_ORIGINS}})

# 数据库连接按请求管理
app.teardown_appcontext(db.close_db)

# 注册蓝图
app.register_blueprint(auth.router)
app.register_blueprint(admin.router)
app.register_blueprint(chat.router)
app.register_blueprint(posts.router)
app.register_blueprint(game.router)
app.register_blueprint(friends.router)


@app.get('/')
def index():
    return jsonify({'name': 'RealitySim Backend (Python)', 'status': 'ok', 'time': __import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S')})


@app.get('/api/health')
def health():
    return jsonify({'status': 'ok'})


# ============ 公开信息 ============
@app.get('/api/users')
def public_users():
    rows = db.query('SELECT id, username, nickname, avatar, role FROM users ORDER BY id DESC LIMIT 100')
    return jsonify({'users': [dict(r) for r in rows]})


@app.get('/api/public/announcements')
def public_announcements():
    rows = db.query('SELECT id, title, content, created_at FROM announcements WHERE published = 1 ORDER BY id DESC')
    return jsonify({'announcements': [dict(r) for r in rows]})


@app.get('/api/public/settings')
def public_settings():
    rows = db.query("SELECT key, value FROM settings WHERE key IN ('site_name', 'announcement_enabled', 'register_enabled', 'login_enabled', 'verify_code_required')")
    return jsonify({'settings': {r['key']: r['value'] for r in rows}})


# ============ 敏感词过滤辅助（供聊天/帖子使用可扩展） ============
@app.get('/api/sensitive-words')
def sensitive_words_public():
    rows = db.query('SELECT word FROM sensitive_words')
    return jsonify({'words': [r['word'] for r in rows]})


# ============ 文件上传 ============
@app.post('/api/upload')
def upload():
    from .auth import auth_required  # noqa: F401（未使用，保持简单）
    # 简单鉴权：接受 Bearer token（可选）
    from src.auth import _try_auth
    user = _try_auth()
    if not user:
        return jsonify({'error': '未登录'}), 401
    f = request.files.get('file')
    if not f:
        return jsonify({'error': '缺少 file 字段'}), 400
    ext = os.path.splitext(f.filename or '')[1].lower()
    allowed = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.mp3', '.mp4', '.wav', '.ogg', '.txt'}
    if ext not in allowed:
        return jsonify({'error': '不支持的文件类型'}), 400
    import uuid
    filename = str(uuid.uuid4().hex) + ext
    f.save(os.path.join(UPLOAD_DIR, filename))
    from src.config import PUBLIC_BASE_URL
    base = PUBLIC_BASE_URL or f'http://localhost:{PORT}'
    return jsonify({'url': f'{base}/uploads/{filename}'})


@app.get('/uploads/<path:filename>')
def uploaded_file(filename):
    return send_from_directory(UPLOAD_DIR, filename)


# ============ WebSocket ============
sock = Sock(app)
chat.register_ws(sock)


if __name__ == '__main__':
    with app.app_context():
        auth.seed_admin()
    print(f'[realitysim-backend-py] 启动成功: http://localhost:{PORT}')
    app.run(host='0.0.0.0', port=PORT, debug=False, threaded=True)
