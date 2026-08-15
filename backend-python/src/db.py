# -*- coding: utf-8 -*-
"""SQLite 数据库（标准库 sqlite3，兼容 Node 版 node:sqlite 建的表结构）"""
import os
import sqlite3
from pathlib import Path

from flask import g

from .config import DB_PATH, ROOT

os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)


def get_db():
    """每个请求一个连接（Flask 多线程安全）"""
    if 'db' not in g:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys = ON')
        g.db = conn
    return g.db


def close_db(_exc=None):
    db = g.pop('db', None)
    if db is not None:
        db.close()


def query(sql, args=()):
    cur = get_db().execute(sql, args)
    rows = cur.fetchall()
    cur.close()
    return rows


def query_one(sql, args=()):
    cur = get_db().execute(sql, args)
    row = cur.fetchone()
    cur.close()
    return row


def execute(sql, args=()):
    db = get_db()
    cur = db.execute(sql, args)
    db.commit()
    last_id = cur.lastrowid
    rowcount = cur.rowcount
    cur.close()
    return last_id, rowcount


def init_schema():
    """建表 + 旧库迁移（模块加载时执行一次）"""
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

    conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_users_phone ON users(phone)')
    conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email ON users(email)')
    conn.commit()
    conn.close()


init_schema()
