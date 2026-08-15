// 认证模块：注册 / 登录（用户名 / 手机号 / 邮箱）/ JWT 签发与校验 / 内置管理员
const crypto = require('crypto');
const jwt = require('jsonwebtoken');
const express = require('express');
const db = require('./db');
const config = require('./config');

const router = express.Router();

function hashPassword(password, salt) {
  return crypto.scryptSync(password, salt, 64).toString('hex');
}

function publicUser(u) {
  return {
    id: u.id,
    username: u.username || null,
    phone: u.phone || null,
    email: u.email || null,
    nickname: u.nickname,
    avatar: u.avatar,
    role: u.role || 'user',
  };
}

const PHONE_RE = /^1[3-9]\d{9}$/;
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const USERNAME_RE = /^[a-zA-Z0-9_]{3,20}$/;

function validatePassword(pw) {
  if (!pw || pw.length < 6) return '密码至少 6 位';
  return null;
}

// 注册：username / phone / email 至少提供一个作为登录标识
router.post('/register', (req, res) => {
  const { username, phone, email, password, nickname, avatar } = req.body || {};
  if (!username && !phone && !email) {
    return res.status(400).json({ error: '用户名 / 手机号 / 邮箱至少填写一个' });
  }
  if (username && !USERNAME_RE.test(username)) {
    return res.status(400).json({ error: '用户名需为 3-20 位字母/数字/下划线' });
  }
  if (phone && !PHONE_RE.test(phone)) {
    return res.status(400).json({ error: '手机号格式不正确（需为 11 位大陆手机号）' });
  }
  if (email && !EMAIL_RE.test(email)) {
    return res.status(400).json({ error: '邮箱格式不正确' });
  }
  const pwErr = validatePassword(password);
  if (pwErr) return res.status(400).json({ error: pwErr });
  if (!nickname) return res.status(400).json({ error: '昵称不能为空' });

  const salt = crypto.randomBytes(16).toString('hex');
  const passwordHash = hashPassword(password, salt);
  try {
    const info = db.prepare(
      'INSERT INTO users (username, phone, email, password_hash, nickname, avatar) VALUES (?, ?, ?, ?, ?, ?)'
    ).run(username || null, phone || null, email || null, `${salt}:${passwordHash}`, nickname, avatar || '');
    const user = db.prepare('SELECT * FROM users WHERE id = ?').get(info.lastInsertRowid);
    const token = jwt.sign({ uid: user.id, username: user.username }, config.JWT_SECRET, {
      expiresIn: config.JWT_EXPIRES,
    });
    res.json({ token, user: publicUser(user) });
  } catch (e) {
    if (String(e.message).includes('UNIQUE')) {
      return res.status(409).json({ error: '该用户名 / 手机号 / 邮箱已被注册' });
    }
    res.status(500).json({ error: '注册失败: ' + e.message });
  }
});

// 登录：login 字段统一匹配 username / phone / email 任意一种
router.post('/login', (req, res) => {
  const { login, password } = req.body || {};
  if (!login || !password) return res.status(400).json({ error: '登录账号与密码不能为空' });
  const user = db.prepare(
    'SELECT * FROM users WHERE username = ? OR phone = ? OR email = ?'
  ).get(login, login, login);
  if (!user) return res.status(401).json({ error: '账号或密码错误' });
  const [salt, stored] = user.password_hash.split(':');
  const computed = hashPassword(password, salt);
  if (computed !== stored) return res.status(401).json({ error: '账号或密码错误' });
  const token = jwt.sign({ uid: user.id, username: user.username }, config.JWT_SECRET, {
    expiresIn: config.JWT_EXPIRES,
  });
  res.json({ token, user: publicUser(user) });
});

// 中间件：校验 Bearer Token
function authMiddleware(req, res, next) {
  const header = req.headers.authorization || '';
  const token = header.startsWith('Bearer ') ? header.slice(7) : null;
  if (!token) return res.status(401).json({ error: '未登录' });
  try {
    const payload = jwt.verify(token, config.JWT_SECRET);
    const user = db.prepare('SELECT * FROM users WHERE id = ?').get(payload.uid);
    if (!user) return res.status(401).json({ error: '用户不存在' });
    req.user = user;
    next();
  } catch (e) {
    res.status(401).json({ error: '登录已过期，请重新登录' });
  }
}

// 管理员中间件
function adminMiddleware(req, res, next) {
  if ((req.user.role || 'user') !== 'admin') {
    return res.status(403).json({ error: '仅管理员可操作' });
  }
  next();
}

// 当前用户信息
router.get('/me', authMiddleware, (req, res) => {
  res.json({ user: publicUser(req.user) });
});

// 绑定手机号 / 邮箱（已登录用户，至少传一个）
router.post('/bind', authMiddleware, (req, res) => {
  const { phone, email } = req.body || {};
  if (!phone && !email) return res.status(400).json({ error: 'phone / email 至少提供一个' });
  if (phone && !PHONE_RE.test(phone)) return res.status(400).json({ error: '手机号格式不正确' });
  if (email && !EMAIL_RE.test(email)) return res.status(400).json({ error: '邮箱格式不正确' });
  try {
    if (phone) db.prepare('UPDATE users SET phone = ? WHERE id = ?').run(phone, req.user.id);
    if (email) db.prepare('UPDATE users SET email = ? WHERE id = ?').run(email, req.user.id);
    const user = db.prepare('SELECT * FROM users WHERE id = ?').get(req.user.id);
    res.json({ user: publicUser(user) });
  } catch (e) {
    if (String(e.message).includes('UNIQUE')) {
      return res.status(409).json({ error: '该手机号 / 邮箱已被其他账号绑定' });
    }
    res.status(500).json({ error: '绑定失败: ' + e.message });
  }
});

// 管理员：用户列表
router.get('/admin/users', authMiddleware, adminMiddleware, (req, res) => {
  const rows = db.prepare('SELECT * FROM users ORDER BY id ASC').all();
  res.json({ users: rows.map(publicUser) });
});

// 内置管理员种子：服务启动时调用，账号不存在则自动创建
function seedAdmin() {
  const existing = db.prepare('SELECT id FROM users WHERE username = ?').get(config.ADMIN_USERNAME);
  if (existing) return;
  const salt = crypto.randomBytes(16).toString('hex');
  const passwordHash = hashPassword(config.ADMIN_PASSWORD, salt);
  db.prepare(
    'INSERT INTO users (username, password_hash, nickname, role) VALUES (?, ?, ?, ?)'
  ).run(config.ADMIN_USERNAME, `${salt}:${passwordHash}`, config.ADMIN_NICKNAME, 'admin');
  console.log(`[realitysim-backend] 已创建内置管理员: ${config.ADMIN_USERNAME}（请尽快修改密码）`);
}

module.exports = { router, authMiddleware, adminMiddleware, publicUser, seedAdmin };
