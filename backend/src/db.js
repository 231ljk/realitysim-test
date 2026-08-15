// SQLite 数据库（使用 Node 内置 node:sqlite，无需原生编译）
const fs = require('fs');
const path = require('path');
const { DatabaseSync } = require('node:sqlite');
const config = require('./config');

fs.mkdirSync(path.dirname(config.DB_PATH), { recursive: true });
const db = new DatabaseSync(config.DB_PATH);

db.exec(`
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT UNIQUE,
  phone TEXT UNIQUE,
  email TEXT UNIQUE,
  password_hash TEXT NOT NULL,
  nickname TEXT NOT NULL,
  avatar TEXT DEFAULT '',
  role TEXT NOT NULL DEFAULT 'user',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

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
  type TEXT NOT NULL DEFAULT 'text',   -- text | image | voice
  content TEXT NOT NULL,
  duration INTEGER DEFAULT 0,          -- 语音时长（秒）
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id, id);
`);

module.exports = db;

// 旧库迁移：为已存在的 users 表补充新列（SQLite 不支持 ADD COLUMN UNIQUE，用普通列 + 唯一索引）
function ensureColumn(table, column, ddl) {
  const cols = db.prepare(`PRAGMA table_info(${table})`).all().map(c => c.name);
  if (!cols.includes(column)) {
    db.exec(`ALTER TABLE ${table} ADD COLUMN ${column} ${ddl}`);
  }
}
ensureColumn('users', 'phone', 'TEXT');
ensureColumn('users', 'email', 'TEXT');
ensureColumn('users', 'role', "TEXT NOT NULL DEFAULT 'user'");

// 旧库迁移：早期 username 为 NOT NULL，无法支持纯手机号/邮箱注册（username 可为 NULL）
// SQLite 不支持修改列约束，需重建表
const userCols = db.prepare('PRAGMA table_info(users)').all();
const usernameCol = userCols.find(c => c.name === 'username');
if (usernameCol && usernameCol.notnull === 1) {
  console.log('[realitysim-backend] 迁移 users 表：解除 username NOT NULL 约束');
  db.exec(`
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
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
INSERT INTO users_new (id, username, phone, email, password_hash, nickname, avatar, role, created_at)
  SELECT id, username, phone, email, password_hash, nickname, avatar, role, created_at FROM users;
DROP TABLE users;
ALTER TABLE users_new RENAME TO users;
COMMIT;
`);
}

db.exec(`
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_phone ON users(phone);
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email ON users(email);
`);
