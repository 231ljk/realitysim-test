// WebSocket 实时消息模块
const { WebSocketServer } = require('ws');
const jwt = require('jsonwebtoken');
const db = require('./db');
const config = require('./config');
const { publicUser } = require('./auth');

// uid -> Set<ws>
const online = new Map();

function markOnline(uid, ws) {
  if (!online.has(uid)) online.set(uid, new Set());
  online.get(uid).add(ws);
}

function markOffline(uid, ws) {
  const set = online.get(uid);
  if (!set) return;
  set.delete(ws);
  if (set.size === 0) online.delete(uid);
}

function broadcastToMembers(conversationId, payload, exceptUid) {
  const memberIds = db.prepare('SELECT user_id FROM conversation_members WHERE conversation_id = ?')
    .all(conversationId).map(r => r.user_id);
  for (const uid of memberIds) {
    if (uid === exceptUid) continue;
    const set = online.get(uid);
    if (!set) continue;
    for (const ws of set) {
      if (ws.readyState === 1) ws.send(JSON.stringify(payload));
    }
  }
}

function setupWebSocket(server) {
  const wss = new WebSocketServer({ server, path: '/ws' });

  wss.on('connection', (ws, req) => {
    // 通过 URL query token 校验登录态
    const url = new URL(req.url, 'http://localhost');
    const token = url.searchParams.get('token') || '';
    let user = null;
    try {
      const payload = jwt.verify(token, config.JWT_SECRET);
      user = db.prepare('SELECT * FROM users WHERE id = ?').get(payload.uid);
    } catch (e) {
      ws.close(4001, 'unauthorized');
      return;
    }
    if (!user) { ws.close(4001, 'unauthorized'); return; }

    markOnline(user.id, ws);
    ws.send(JSON.stringify({ type: 'hello', user: publicUser(user) }));

    ws.on('message', (raw) => {
      let data;
      try { data = JSON.parse(raw.toString()); } catch { return; }
      if (data.type !== 'message') return;

      const convId = Number(data.conversationId);
      const msgType = ['text', 'image', 'voice'].includes(data.msgType) ? data.msgType : 'text';
      const content = String(data.content || '').trim();
      if (!content) return;

      const member = db.prepare('SELECT 1 FROM conversation_members WHERE conversation_id = ? AND user_id = ?')
        .get(convId, user.id);
      if (!member) return;

      const info = db.prepare(`
        INSERT INTO messages (conversation_id, sender_id, type, content, duration) VALUES (?, ?, ?, ?, ?)
      `).run(convId, user.id, msgType, content, Number(data.duration || 0));

      const msg = db.prepare(`
        SELECT m.*, u.nickname AS sender_nickname, u.avatar AS sender_avatar
        FROM messages m JOIN users u ON u.id = m.sender_id WHERE m.id = ?
      `).get(info.lastInsertRowid);

      // 回执给发送者
      ws.send(JSON.stringify({ type: 'ack', message: msg }));
      // 广播给会话其他在线成员
      broadcastToMembers(convId, { type: 'message', message: msg }, user.id);
    });

    ws.on('close', () => markOffline(user.id, ws));
    ws.on('error', () => markOffline(user.id, ws));
  });

  return wss;
}

module.exports = { setupWebSocket, broadcastToMembers };
