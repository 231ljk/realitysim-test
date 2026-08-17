# -*- coding: utf-8 -*-
"""数据库访问层：支持两种模式（环境变量 DB_TYPE 切换）

- DB_TYPE=sqlite（默认）：本地 SQLite 文件（标准库 sqlite3，兼容 Node 版 node:sqlite 建的表结构）
- DB_TYPE=turso：云端 Turso（SQLite 兼容，libsql 客户端），需 TURSO_DATABASE_URL / TURSO_AUTH_TOKEN

对外 API 不变：get_db / close_db / query / query_one / execute / init_schema
"""
import json
import os
import sqlite3
import urllib.request
from pathlib import Path

from flask import g

from .config import DB_PATH, DB_TYPE, ROOT, TURSO_AUTH_TOKEN, TURSO_DATABASE_URL

os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

_TURSO = DB_TYPE == 'turso'


def _turso_arg(v):
    if v is None:
        return {'type': 'null', 'value': None}
    if isinstance(v, bool):
        return {'type': 'integer', 'value': '1' if v else '0'}
    if isinstance(v, int):
        return {'type': 'integer', 'value': str(v)}
    if isinstance(v, float):
        return {'type': 'real', 'value': str(v)}
    if isinstance(v, bytes):
        return {'type': 'blob', 'value': v.hex()}
    return {'type': 'text', 'value': str(v)}


def _turso_val(v):
    t = v.get('type')
    if t == 'null':
        return None
    if t == 'integer':
        return int(v['value'])
    if t == 'real':
        return float(v['value'])
    if t == 'blob':
        return bytes.fromhex(v.get('value') or '')
    return v.get('value')  # text


class _TursoResult:
    """模拟 libsql ResultSet：columns / rows / last_insert_rowid / rows_affected"""
    def __init__(self, columns, rows, last_insert_rowid, rows_affected):
        self.columns = columns
        self.rows = rows
        self.last_insert_rowid = int(last_insert_rowid) if last_insert_rowid is not None else None
        self.rows_affected = rows_affected or 0


_turso_http = None


def _turso_execute(sql, args=()):
    """通过 Turso 官方 HTTP API（v2/pipeline）执行 SQL，绕开 WebSocket 握手问题"""
    global _turso_http
    if _turso_http is None:
        if not TURSO_DATABASE_URL:
            raise RuntimeError('DB_TYPE=turso 但未配置 TURSO_DATABASE_URL')
        if not TURSO_AUTH_TOKEN:
            raise RuntimeError('DB_TYPE=turso 但未配置 TURSO_AUTH_TOKEN')
        _turso_http = TURSO_DATABASE_URL.replace('libsql://', 'https://').rstrip('/') + '/v2/pipeline'

    payload = json.dumps({
        'requests': [{
            'type': 'execute',
            'stmt': {'sql': sql, 'args': [_turso_arg(a) for a in args]},
        }]
    }).encode()
    req = urllib.request.Request(_turso_http, data=payload, method='POST')
    req.add_header('Authorization', 'Bearer ' + TURSO_AUTH_TOKEN)
    req.add_header('Content-Type', 'application/json')
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = json.loads(resp.read().decode())

    result = body['results'][0]
    if result.get('type') != 'ok':
        raise RuntimeError(f"Turso 执行失败: {result.get('error') or result}")
    inner = result['response']['result']
    columns = [c['name'] for c in inner.get('cols', [])]
    rows = [[_turso_val(v) for v in row] for row in inner.get('rows', [])]
    return _TursoResult(columns, rows, inner.get('last_insert_rowid'), inner.get('affected_row_count'))


def _row_dicts(columns, rows):
    return [dict(zip(columns, row)) for row in rows]


def get_db():
    """每个请求一个连接（Flask 多线程安全）；turso 模式请使用 query/execute（返回 None）"""
    if _TURSO:
        return None
    if 'db' not in g:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys = ON')
        g.db = conn
    return g.db


def close_db(_exc=None):
    if _TURSO:
        return
    db = g.pop('db', None)
    if db is not None:
        db.close()


def query(sql, args=()):
    if _TURSO:
        res = _turso_execute(sql, args)
        return _row_dicts(res.columns, res.rows)
    cur = get_db().execute(sql, args)
    rows = cur.fetchall()
    cur.close()
    return rows


def query_one(sql, args=()):
    if _TURSO:
        res = _turso_execute(sql, args)
        if not res.rows:
            return None
        return dict(zip(res.columns, res.rows[0]))
    cur = get_db().execute(sql, args)
    row = cur.fetchone()
    cur.close()
    return row


def execute(sql, args=()):
    if _TURSO:
        res = _turso_execute(sql, args)
        return res.last_insert_rowid, res.rows_affected
    db = get_db()
    cur = db.execute(sql, args)
    db.commit()
    last_id = cur.lastrowid
    rowcount = cur.rowcount
    cur.close()
    return last_id, rowcount


# 建表语句（turso 模式下逐条执行；sqlite 模式仍走 executescript + 本地迁移）
_SCHEMA_STATEMENTS = [
    "CREATE TABLE IF NOT EXISTS users (\n"
    "  id INTEGER PRIMARY KEY AUTOINCREMENT,\n"
    "  username TEXT UNIQUE,\n"
    "  phone TEXT UNIQUE,\n"
    "  email TEXT UNIQUE,\n"
    "  password_hash TEXT NOT NULL,\n"
    "  nickname TEXT NOT NULL,\n"
    "  avatar TEXT DEFAULT '',\n"
    "  role TEXT NOT NULL DEFAULT 'user',\n"
    "  status TEXT NOT NULL DEFAULT 'normal',\n"
    "  whitelisted INTEGER NOT NULL DEFAULT 0,\n"
    "  banned_reason TEXT DEFAULT '',\n"
    "  last_login_at TEXT,\n"
    "  play_hours REAL NOT NULL DEFAULT 0,\n"
    "  game_status TEXT NOT NULL DEFAULT 'offline',\n"
    "  last_heartbeat_at TEXT,\n"
    "  lat REAL,\n"
    "  lng REAL,\n"
    "  last_location_at TEXT,\n"
    "  created_at TEXT NOT NULL DEFAULT (datetime('now'))\n"
    ");",

    "CREATE TABLE IF NOT EXISTS oauth_bindings (\n"
    "  id INTEGER PRIMARY KEY AUTOINCREMENT,\n"
    "  user_id INTEGER NOT NULL,\n"
    "  provider TEXT NOT NULL,\n"
    "  provider_uid TEXT NOT NULL,\n"
    "  provider_name TEXT DEFAULT '',\n"
    "  created_at TEXT NOT NULL DEFAULT (datetime('now')),\n"
    "  UNIQUE (provider, provider_uid)\n"
    ");",
    "CREATE INDEX IF NOT EXISTS idx_oauth_user ON oauth_bindings(user_id);",

    "CREATE TABLE IF NOT EXISTS conversations (\n"
    "  id INTEGER PRIMARY KEY AUTOINCREMENT,\n"
    "  name TEXT DEFAULT '',\n"
    "  created_at TEXT NOT NULL DEFAULT (datetime('now'))\n"
    ");",

    "CREATE TABLE IF NOT EXISTS conversation_members (\n"
    "  conversation_id INTEGER NOT NULL,\n"
    "  user_id INTEGER NOT NULL,\n"
    "  PRIMARY KEY (conversation_id, user_id)\n"
    ");",

    "CREATE TABLE IF NOT EXISTS messages (\n"
    "  id INTEGER PRIMARY KEY AUTOINCREMENT,\n"
    "  conversation_id INTEGER NOT NULL,\n"
    "  sender_id INTEGER NOT NULL,\n"
    "  type TEXT NOT NULL DEFAULT 'text',\n"
    "  content TEXT NOT NULL,\n"
    "  duration INTEGER DEFAULT 0,\n"
    "  created_at TEXT NOT NULL DEFAULT (datetime('now'))\n"
    ");",
    "CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id, id);",

    "CREATE TABLE IF NOT EXISTS login_logs (\n"
    "  id INTEGER PRIMARY KEY AUTOINCREMENT,\n"
    "  user_id INTEGER,\n"
    "  login_name TEXT DEFAULT '',\n"
    "  channel TEXT DEFAULT 'pwd',\n"
    "  provider TEXT DEFAULT '',\n"
    "  ip TEXT DEFAULT '',\n"
    "  ua TEXT DEFAULT '',\n"
    "  success INTEGER NOT NULL DEFAULT 1,\n"
    "  detail TEXT DEFAULT '',\n"
    "  created_at TEXT NOT NULL DEFAULT (datetime('now'))\n"
    ");",
    "CREATE INDEX IF NOT EXISTS idx_login_logs_user ON login_logs(user_id);",
    "CREATE INDEX IF NOT EXISTS idx_login_logs_time ON login_logs(created_at);",

    "CREATE TABLE IF NOT EXISTS audit_logs (\n"
    "  id INTEGER PRIMARY KEY AUTOINCREMENT,\n"
    "  admin_id INTEGER,\n"
    "  admin_name TEXT DEFAULT '',\n"
    "  action TEXT NOT NULL,\n"
    "  target TEXT DEFAULT '',\n"
    "  detail TEXT DEFAULT '',\n"
    "  created_at TEXT NOT NULL DEFAULT (datetime('now'))\n"
    ");",
    "CREATE INDEX IF NOT EXISTS idx_audit_logs_time ON audit_logs(created_at);",

    "CREATE TABLE IF NOT EXISTS announcements (\n"
    "  id INTEGER PRIMARY KEY AUTOINCREMENT,\n"
    "  title TEXT NOT NULL,\n"
    "  content TEXT NOT NULL,\n"
    "  published INTEGER NOT NULL DEFAULT 0,\n"
    "  created_at TEXT NOT NULL DEFAULT (datetime('now')),\n"
    "  updated_at TEXT NOT NULL DEFAULT (datetime('now'))\n"
    ");",

    "CREATE TABLE IF NOT EXISTS sensitive_words (\n"
    "  id INTEGER PRIMARY KEY AUTOINCREMENT,\n"
    "  word TEXT NOT NULL UNIQUE,\n"
    "  created_at TEXT NOT NULL DEFAULT (datetime('now'))\n"
    ");",

    "CREATE TABLE IF NOT EXISTS settings (\n"
    "  key TEXT PRIMARY KEY,\n"
    "  value TEXT NOT NULL DEFAULT ''\n"
    ");",

    "CREATE TABLE IF NOT EXISTS posts (\n"
    "  id INTEGER PRIMARY KEY AUTOINCREMENT,\n"
    "  user_id INTEGER NOT NULL,\n"
    "  title TEXT NOT NULL,\n"
    "  content TEXT NOT NULL,\n"
    "  images TEXT DEFAULT '',\n"
    "  likes INTEGER NOT NULL DEFAULT 0,\n"
    "  deleted INTEGER NOT NULL DEFAULT 0,\n"
    "  created_at TEXT NOT NULL DEFAULT (datetime('now')),\n"
    "  updated_at TEXT NOT NULL DEFAULT (datetime('now'))\n"
    ");",
    "CREATE INDEX IF NOT EXISTS idx_posts_user ON posts(user_id);",
    "CREATE INDEX IF NOT EXISTS idx_posts_time ON posts(created_at);",

    "CREATE TABLE IF NOT EXISTS post_likes (\n"
    "  post_id INTEGER NOT NULL,\n"
    "  user_id INTEGER NOT NULL,\n"
    "  created_at TEXT NOT NULL DEFAULT (datetime('now')),\n"
    "  PRIMARY KEY (post_id, user_id)\n"
    ");",

    "CREATE TABLE IF NOT EXISTS post_comments (\n"
    "  id INTEGER PRIMARY KEY AUTOINCREMENT,\n"
    "  post_id INTEGER NOT NULL,\n"
    "  user_id INTEGER NOT NULL,\n"
    "  content TEXT NOT NULL,\n"
    "  created_at TEXT NOT NULL DEFAULT (datetime('now'))\n"
    ");",
    "CREATE INDEX IF NOT EXISTS idx_post_comments_post ON post_comments(post_id, id);",

    "CREATE TABLE IF NOT EXISTS friendships (\n"
    "  user_id INTEGER NOT NULL,\n"
    "  friend_id INTEGER NOT NULL,\n"
    "  created_at TEXT NOT NULL DEFAULT (datetime('now')),\n"
    "  PRIMARY KEY (user_id, friend_id)\n"
    ");",
    "CREATE INDEX IF NOT EXISTS idx_friendships_friend ON friendships(friend_id, user_id);",

    "CREATE TABLE IF NOT EXISTS friend_requests (\n"
    "  id INTEGER PRIMARY KEY AUTOINCREMENT,\n"
    "  from_user_id INTEGER NOT NULL,\n"
    "  to_user_id INTEGER NOT NULL,\n"
    "  message TEXT DEFAULT '',\n"
    "  status TEXT NOT NULL DEFAULT 'pending',\n"
    "  created_at TEXT NOT NULL DEFAULT (datetime('now')),\n"
    "  handled_at TEXT\n"
    ");",
    "CREATE INDEX IF NOT EXISTS idx_fr_to ON friend_requests(to_user_id, status, id);",
    "CREATE INDEX IF NOT EXISTS idx_fr_from ON friend_requests(from_user_id, status, id);",
]

_DEFAULT_SETTINGS = [
    ('register_enabled', '1'),
    ('login_enabled', '1'),
    ('announcement_enabled', '1'),
    ('verify_code_required', '0'),
    ('site_name', '现实模拟 RealitySim'),
]


def _ensure_setting(conn, key, value):
    row = conn.execute('SELECT key FROM settings WHERE key = ?', (key,)).fetchone()
    if not row:
        conn.execute('INSERT INTO settings (key, value) VALUES (?, ?)', (key, value))


def _init_schema_turso():
    """云端 Turso：幂等建表 + 默认设置（表结构由导入保证完整，无需本地迁移逻辑）"""
    for stmt in _SCHEMA_STATEMENTS:
        _turso_execute(stmt)
    for key, value in _DEFAULT_SETTINGS:
        row = _turso_execute('SELECT key FROM settings WHERE key = ?', [key]).rows
        if not row:
            _turso_execute('INSERT INTO settings (key, value) VALUES (?, ?)', [key, value])


def init_schema():
    """建表 + 旧库迁移（模块加载时执行一次）"""
    if _TURSO:
        _init_schema_turso()
        return

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript('''
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT UNIQUE,
  phone TEXT UNIQUE,
  email TEXT UNIQUE,
  password_hash TEXT NOT NULL,
  nickname TEXT NOT NULL,
  avatar TEXT DEFAULT '',
  role TEXT NOT NULL DEFAULT 'user',
  status TEXT NOT NULL DEFAULT 'normal',
  whitelisted INTEGER NOT NULL DEFAULT 0,
  banned_reason TEXT DEFAULT '',
  last_login_at TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS oauth_bindings (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  provider TEXT NOT NULL,
  provider_uid TEXT NOT NULL,
  provider_name TEXT DEFAULT '',
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (provider, provider_uid)
);
CREATE INDEX IF NOT EXISTS idx_oauth_user ON oauth_bindings(user_id);

CREATE TABLE IF NOT EXISTS conversations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT DEFAULT '',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS conversation_members (
  conversation_id INTEGER NOT NULL,
  user_id INTEGER NOT NULL,
  PRIMARY KEY (conversation_id, user_id)
);

CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  conversation_id INTEGER NOT NULL,
  sender_id INTEGER NOT NULL,
  type TEXT NOT NULL DEFAULT 'text',
  content TEXT NOT NULL,
  duration INTEGER DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id, id);

CREATE TABLE IF NOT EXISTS login_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER,
  login_name TEXT DEFAULT '',
  channel TEXT DEFAULT 'pwd',
  provider TEXT DEFAULT '',
  ip TEXT DEFAULT '',
  ua TEXT DEFAULT '',
  success INTEGER NOT NULL DEFAULT 1,
  detail TEXT DEFAULT '',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_login_logs_user ON login_logs(user_id);
CREATE INDEX IF NOT EXISTS idx_login_logs_time ON login_logs(created_at);

CREATE TABLE IF NOT EXISTS audit_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  admin_id INTEGER,
  admin_name TEXT DEFAULT '',
  action TEXT NOT NULL,
  target TEXT DEFAULT '',
  detail TEXT DEFAULT '',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_audit_logs_time ON audit_logs(created_at);

CREATE TABLE IF NOT EXISTS announcements (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  content TEXT NOT NULL,
  published INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS sensitive_words (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  word TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS posts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  title TEXT NOT NULL,
  content TEXT NOT NULL,
  images TEXT DEFAULT '',
  likes INTEGER NOT NULL DEFAULT 0,
  deleted INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_posts_user ON posts(user_id);
CREATE INDEX IF NOT EXISTS idx_posts_time ON posts(created_at);

CREATE TABLE IF NOT EXISTS post_likes (
  post_id INTEGER NOT NULL,
  user_id INTEGER NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (post_id, user_id)
);

CREATE TABLE IF NOT EXISTS post_comments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  post_id INTEGER NOT NULL,
  user_id INTEGER NOT NULL,
  content TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_post_comments_post ON post_comments(post_id, id);

CREATE TABLE IF NOT EXISTS friendships (
  user_id INTEGER NOT NULL,
  friend_id INTEGER NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (user_id, friend_id)
);
CREATE INDEX IF NOT EXISTS idx_friendships_friend ON friendships(friend_id, user_id);

CREATE TABLE IF NOT EXISTS friend_requests (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  from_user_id INTEGER NOT NULL,
  to_user_id INTEGER NOT NULL,
  message TEXT DEFAULT '',
  status TEXT NOT NULL DEFAULT 'pending',
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  handled_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_fr_to ON friend_requests(to_user_id, status, id);
CREATE INDEX IF NOT EXISTS idx_fr_from ON friend_requests(from_user_id, status, id);
''')
    conn.commit()

    # 初始化默认设置
    def ensure_setting(key, value):
        row = conn.execute('SELECT key FROM settings WHERE key = ?', (key,)).fetchone()
        if not row:
            conn.execute('INSERT INTO settings (key, value) VALUES (?, ?)', (key, value))
    ensure_setting('register_enabled', '1')
    ensure_setting('login_enabled', '1')
    ensure_setting('announcement_enabled', '1')
    ensure_setting('verify_code_required', '0')
    ensure_setting('site_name', '现实模拟 RealitySim')
    conn.commit()

    # 旧库迁移：为已存在的 users 表补充新列
    def ensure_column(table, column, ddl):
        cols = [r[1] for r in conn.execute(f'PRAGMA table_info({table})').fetchall()]
        if column not in cols:
            conn.execute(f'ALTER TABLE {table} ADD COLUMN {column} {ddl}')
    ensure_column('users', 'phone', 'TEXT')
    ensure_column('users', 'email', 'TEXT')
    ensure_column('users', 'role', "TEXT NOT NULL DEFAULT 'user'")
    ensure_column('users', 'status', "TEXT NOT NULL DEFAULT 'normal'")
    ensure_column('users', 'whitelisted', 'INTEGER NOT NULL DEFAULT 0')
    ensure_column('users', 'banned_reason', "TEXT DEFAULT ''")
    ensure_column('users', 'last_login_at', 'TEXT')
    ensure_column('users', 'play_hours', 'REAL NOT NULL DEFAULT 0')
    ensure_column('users', 'game_status', "TEXT NOT NULL DEFAULT 'offline'")
    ensure_column('users', 'last_heartbeat_at', 'TEXT')
    ensure_column('users', 'lat', 'REAL')
    ensure_column('users', 'lng', 'REAL')
    ensure_column('users', 'last_location_at', 'TEXT')
    conn.commit()

    # 旧库迁移：早期 username 为 NOT NULL，无法支持纯手机号/邮箱注册（username 可为 NULL）
    user_cols = conn.execute('PRAGMA table_info(users)').fetchall()
    username_col = next((c for c in user_cols if c[1] == 'username'), None)
    if username_col and username_col[3] == 1:  # notnull == 1
        print('[realitysim-backend] 迁移 users 表：解除 username NOT NULL 约束并补齐新列')
        conn.executescript('''
BEGIN;
CREATE TABLE users_new (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT UNIQUE,
  phone TEXT UNIQUE,
  email TEXT UNIQUE,
  password_hash TEXT NOT NULL,
  nickname TEXT NOT NULL,
  avatar TEXT DEFAULT '',
  role TEXT NOT NULL DEFAULT 'user',
  status TEXT NOT NULL DEFAULT 'normal',
  whitelisted INTEGER NOT NULL DEFAULT 0,
  banned_reason TEXT DEFAULT '',
  last_login_at TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
INSERT INTO users_new (id, username, phone, email, password_hash, nickname, avatar, role, created_at)
  SELECT id, username, phone, email, password_hash, nickname, avatar, role, created_at FROM users;
DROP TABLE users;
ALTER TABLE users_new RENAME TO users;
COMMIT;
''')
        conn.commit()

    # 迁移后再次补齐可能因重建丢失的新列
    ensure_column('users', 'phone', 'TEXT')
    ensure_column('users', 'email', 'TEXT')
    ensure_column('users', 'role', "TEXT NOT NULL DEFAULT 'user'")
    ensure_column('users', 'status', "TEXT NOT NULL DEFAULT 'normal'")
    ensure_column('users', 'whitelisted', 'INTEGER NOT NULL DEFAULT 0')
    ensure_column('users', 'banned_reason', "TEXT DEFAULT ''")
    ensure_column('users', 'last_login_at', 'TEXT')
    ensure_column('users', 'play_hours', 'REAL NOT NULL DEFAULT 0')
    ensure_column('users', 'game_status', "TEXT NOT NULL DEFAULT 'offline'")
    ensure_column('users', 'last_heartbeat_at', 'TEXT')
    ensure_column('users', 'lat', 'REAL')
    ensure_column('users', 'lng', 'REAL')
    ensure_column('users', 'last_location_at', 'TEXT')
    conn.commit()

    conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_users_phone ON users(phone)')
    conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email ON users(email)')
    conn.commit()
    conn.close()


init_schema()
