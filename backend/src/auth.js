// 认证模块：注册 / 登录 / JWT 签发与校验
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
  return { id: u.id, username: u.username, nickname: u.nickname, avatar: u.avatar };
}

// 注册
router.post('/register', (req, res) => {
  const { username, password, nickname, avatar } = req.body || {};
  if (!username || !password || !nickname) {
    return res.status(400).json({ error: 'username / password / nickname 均不能为空' });
  }
  if (!/^[a-zA-Z0-9_]{3,20}$/.test(username)) {
    return res.status(400).json({ error: '账号需为 3-20 位字母/数字/下划线' });
  }
  if (password.length < 6) {
    return res.status(400).json({ error: '密码至少 6 位' });
  }
  const salt = crypto.randomBytes(16).toString('hex');
  const passwordHash = hashPassword(password, salt);
  try {
    const info = db.prepare(
      'INSERT INTO users (username, password_hash, nickname, avatar) VALUES (?, ?, ?, ?)'
    ).run(username, `${salt}:${passwordHash}`, nickname, avatar || '');
    const user = db.prepare('SELECT * FROM users WHERE id = ?').get(info.lastInsertRowid);
    const token = jwt.sign({ uid: user.id, username: user.username }, config.JWT_SECRET, {
      expiresIn: config.JWT_EXPIRES,
    });
    res.json({ token, user: publicUser(user) });
  } catch (e) {
    if (String(e.message).includes('UNIQUE')) {
      return res.status(409).json({ error: '该账号已注册' });
    }
    res.status(500).json({ error: '注册失败: ' + e.message });
  }
});

// 登录
router.post('/login', (req, res) => {
  const { username, password } = req.body || {};
  if (!username || !password) return res.status(400).json({ error: '账号与密码不能为空' });
  const user = db.prepare('SELECT * FROM users WHERE username = ?').get(username);
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

// 当前用户信息
router.get('/me', authMiddleware, (req, res) => {
  res.json({ user: publicUser(req.user) });
});

module.exports = { router, authMiddleware, publicUser };
