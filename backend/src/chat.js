// 聊天模块：会话 / 消息 REST 接口
const express = require('express');
const db = require('./db');
const { authMiddleware, publicUser } = require('./auth');

const router = express.Router();
router.use(authMiddleware);

// 创建会话（单聊 / 群聊），memberIds 为其他成员用户ID数组
router.post('/conversations', (req, res) => {
  const memberIds = Array.isArray(req.body && req.body.memberIds)
    ? [...new Set((req.body.memberIds).map(Number).filter(Number.isFinite))]
    : [];
  if (memberIds.length === 0) return res.status(400).json({ error: 'memberIds 不能为空' });
  const info = db.prepare('INSERT INTO conversations (name) VALUES (?)').run('');
  const convId = info.lastInsertRowid;
  const insert = db.prepare('INSERT OR IGNORE INTO conversation_members (conversation_id, user_id) VALUES (?, ?)');
  insert.run(convId, req.user.id);
  for (const uid of memberIds) insert.run(convId, uid);
  res.json({ conversation: { id: convId, memberIds: [req.user.id, ...memberIds] } });
});

// 获取我的会话列表（含最后一条消息与未读数简化版）
router.get('/conversations', (req, res) => {
  const rows = db.prepare(`
    SELECT c.id, c.name, c.created_at,
           (SELECT COUNT(*) FROM messages m WHERE m.conversation_id = c.id) AS message_count
    FROM conversations c
    JOIN conversation_members cm ON cm.conversation_id = c.id
    WHERE cm.user_id = ?
    ORDER BY c.id DESC
  `).all(req.user.id);
  // 附加会话成员（用于展示对方昵称）
  for (const c of rows) {
    const members = db.prepare(`
      SELECT u.id, u.username, u.nickname, u.avatar FROM conversation_members cm
      JOIN users u ON u.id = cm.user_id WHERE cm.conversation_id = ?
    `).all(c.id);
    c.members = members.map(publicUser);
    const last = db.prepare('SELECT * FROM messages WHERE conversation_id = ? ORDER BY id DESC LIMIT 1').get(c.id);
    c.lastMessage = last || null;
  }
  res.json({ conversations: rows });
});

// 获取会话历史消息（可选 after 做增量拉取）
router.get('/conversations/:id/messages', (req, res) => {
  const convId = Number(req.params.id);
  const member = db.prepare('SELECT 1 FROM conversation_members WHERE conversation_id = ? AND user_id = ?')
    .get(convId, req.user.id);
  if (!member) return res.status(403).json({ error: '无权访问该会话' });
  const after = parseInt(req.query.after || '0', 10);
  const rows = db.prepare(`
    SELECT m.*, u.nickname AS sender_nickname, u.avatar AS sender_avatar
    FROM messages m JOIN users u ON u.id = m.sender_id
    WHERE m.conversation_id = ? AND m.id > ?
    ORDER BY m.id ASC LIMIT 200
  `).all(convId, after);
  res.json({ messages: rows });
});

// 发送消息
router.post('/conversations/:id/messages', (req, res) => {
  const convId = Number(req.params.id);
  const { type = 'text', content, duration = 0 } = req.body || {};
  if (!content) return res.status(400).json({ error: '消息内容不能为空' });
  if (!['text', 'image', 'voice'].includes(type)) {
    return res.status(400).json({ error: '消息类型仅支持 text / image / voice' });
  }
  const member = db.prepare('SELECT 1 FROM conversation_members WHERE conversation_id = ? AND user_id = ?')
    .get(convId, req.user.id);
  if (!member) return res.status(403).json({ error: '无权在该会话发言' });
  const info = db.prepare(`
    INSERT INTO messages (conversation_id, sender_id, type, content, duration) VALUES (?, ?, ?, ?, ?)
  `).run(convId, req.user.id, type, content, duration);
  const msg = db.prepare('SELECT * FROM messages WHERE id = ?').get(info.lastInsertRowid);
  res.json({ message: msg });
});

module.exports = router;
