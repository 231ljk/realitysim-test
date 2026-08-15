# -*- coding: utf-8 -*-
"""聊天模块：会话管理 + 消息历史 + WebSocket 实时聊天（flask-sock）
接口与原 Node 版兼容：/api/chat/conversations、/api/chat/conversations/:id/messages、/ws/chat
"""
import json
import os
import threading
import time
import uuid
from datetime import datetime

from flask import Blueprint, g, jsonify, request
from flask_sock import Sock

from . import db
from .auth import auth_required, _try_auth

router = Blueprint('chat', __name__, url_prefix='/api/chat')

# WebSocket 连接管理
ws_clients = {}  # conversation_id -> set(ws)
ws_lock = threading.Lock()


def _now():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def public_message(row):
    sender = db.query_one('SELECT id, username, nickname, avatar FROM users WHERE id = ?', (row['sender_id'],))
    return {
        'id': row['id'],
        'conversationId': row['conversation_id'],
        'senderId': row['sender_id'],
        'sender': {
            'id': row['sender_id'],
            'username': sender['username'] if sender else None,
            'nickname': sender['nickname'] if sender else '已注销用户',
            'avatar': sender['avatar'] if sender else '',
        },
        'type': row['type'],
        'content': row['content'],
        'duration': row['duration'] or 0,
        'createdAt': row['created_at'],
    }


# ============ 会话列表 ============
@router.get('/conversations')
@auth_required
def conversations():
    rows = db.query('''
        SELECT c.*, u.nickname AS other_nickname, u.avatar AS other_avatar, u.username AS other_username,
               (SELECT COUNT(*) FROM messages m WHERE m.conversation_id = c.id) AS msg_count
        FROM conversations c
        JOIN conversation_members cm ON cm.conversation_id = c.id AND cm.user_id != ?
        JOIN users u ON u.id = cm.user_id
        WHERE c.id IN (SELECT conversation_id FROM conversation_members WHERE user_id = ?)
        ORDER BY c.id DESC
    ''', (g.user['id'], g.user['id']))
    result = []
    for r in rows:
        result.append({
            'id': r['id'],
            'name': r['name'],
            'otherUser': {
                'id': r['other_username'] and db.query_one('SELECT id FROM users WHERE username = ?', (r['other_username'],))['id'],
                'username': r['other_username'],
                'nickname': r['other_nickname'],
                'avatar': r['other_avatar'],
            },
            'messageCount': r['msg_count'],
            'createdAt': r['created_at'],
        })
    return jsonify({'conversations': result})


# ============ 创建 / 获取私聊会话 ============
@router.post('/conversations')
@auth_required
def create_conversation():
    data = request.get_json(silent=True) or {}
    other_id = data.get('userId')
    if not other_id:
        return jsonify({'error': 'userId 不能为空'}), 400
    if other_id == g.user['id']:
        return jsonify({'error': '不能与自己创建会话'}), 400
    other = db.query_one('SELECT * FROM users WHERE id = ?', (other_id,))
    if not other:
        return jsonify({'error': '用户不存在'}), 404
    # 查已有私聊
    row = db.query_one('''
        SELECT c.id FROM conversations c
        WHERE c.id IN (SELECT conversation_id FROM conversation_members WHERE user_id = ?)
          AND c.id IN (SELECT conversation_id FROM conversation_members WHERE user_id = ?)
        ORDER BY c.id LIMIT 1
    ''', (g.user['id'], other_id))
    if row:
        conv_id = row['id']
    else:
        last_id, _ = db.execute('INSERT INTO conversations (name) VALUES (?)', ('',))
        db.execute('INSERT INTO conversation_members (conversation_id, user_id) VALUES (?, ?)', (last_id, g.user['id']))
        db.execute('INSERT INTO conversation_members (conversation_id, user_id) VALUES (?, ?)', (last_id, other_id))
        conv_id = last_id
    return jsonify({'conversation': {'id': conv_id, 'otherUser': {
        'id': other['id'], 'username': other['username'], 'nickname': other['nickname'], 'avatar': other['avatar']}}})


# ============ 消息历史 ============
@router.get('/conversations/<int:cid>/messages')
@auth_required
def messages(cid):
    member = db.query_one('SELECT * FROM conversation_members WHERE conversation_id = ? AND user_id = ?',
                          (cid, g.user['id']))
    if not member:
        return jsonify({'error': '无权限访问该会话'}), 403
    before = request.args.get('before')
    limit = min(100, max(1, int(request.args.get('limit', '50') or 50)))
    if before:
        rows = db.query('SELECT * FROM messages WHERE conversation_id = ? AND id < ? ORDER BY id DESC LIMIT ?',
                        (cid, int(before), limit))
    else:
        rows = db.query('SELECT * FROM messages WHERE conversation_id = ? ORDER BY id DESC LIMIT ?',
                        (cid, limit))
    rows.reverse()
    return jsonify({'messages': [public_message(r) for r in rows]})


# ============ 发送文本消息（REST 兜底） ============
@router.post('/conversations/<int:cid>/messages')
@auth_required
def send_message(cid):
    member = db.query_one('SELECT * FROM conversation_members WHERE conversation_id = ? AND user_id = ?',
                          (cid, g.user['id']))
    if not member:
        return jsonify({'error': '无权限访问该会话'}), 403
    data = request.get_json(silent=True) or {}
    content = (data.get('content') or '').strip()
    msg_type = data.get('type') or 'text'
    if not content:
        return jsonify({'error': '消息内容不能为空'}), 400
    last_id, _ = db.execute(
        'INSERT INTO messages (conversation_id, sender_id, type, content) VALUES (?, ?, ?, ?)',
        (cid, g.user['id'], msg_type, content[:5000]))
    row = db.query_one('SELECT * FROM messages WHERE id = ?', (last_id,))
    msg = public_message(row)
    _broadcast(cid, {'type': 'message', 'message': msg})
    return jsonify({'message': msg})


# ============ WebSocket ============
def _broadcast(cid, payload):
    text = json.dumps(payload, ensure_ascii=False)
    with ws_lock:
        for ws in list(ws_clients.get(cid, set())):
            try:
                ws.send(text)
            except Exception:
                pass


def register_ws(sock: Sock):
    @sock.route('/ws/chat')
    def ws_chat(ws):
        token = ws.receive(timeout=5)
        user = None
        if token:
            user = _resolve_token(token)
        if not user:
            ws.send(json.dumps({'type': 'error', 'error': '未登录'}, ensure_ascii=False))
            ws.close()
            return
        # 支持 query 里的 token 兜底
        if not user:
            return
        conv_ids = []
        try:
            # 直接监听用户所属全部会话
            rows = db.query('SELECT conversation_id FROM conversation_members WHERE user_id = ?', (user['id'],))
            conv_ids = [r['conversation_id'] for r in rows]
        except Exception:
            pass
        for cid in conv_ids:
            with ws_lock:
                ws_clients.setdefault(cid, set()).add(ws)
        try:
            while True:
                raw = ws.receive()
                if raw is None:
                    break
                try:
                    data = json.loads(raw)
                except Exception:
                    continue
                action = data.get('action')
                if action == 'ping':
                    ws.send(json.dumps({'type': 'pong'}, ensure_ascii=False))
                elif action == 'message':
                    cid = data.get('conversationId')
                    content = (data.get('content') or '').strip()
                    if not cid or not content:
                        continue
                    member = db.query_one('SELECT * FROM conversation_members WHERE conversation_id = ? AND user_id = ?',
                                          (cid, user['id']))
                    if not member:
                        ws.send(json.dumps({'type': 'error', 'error': '无权限'}, ensure_ascii=False))
                        continue
                    last_id, _ = db.execute(
                        'INSERT INTO messages (conversation_id, sender_id, type, content) VALUES (?, ?, ?, ?)',
                        (cid, user['id'], 'text', content[:5000]))
                    row = db.query_one('SELECT * FROM messages WHERE id = ?', (last_id,))
                    msg = public_message(row)
                    _broadcast(cid, {'type': 'message', 'message': msg})
        finally:
            for cid in conv_ids:
                with ws_lock:
                    ws_clients.get(cid, set()).discard(ws)


def _resolve_token(token):
    import jwt as pyjwt
    from .config import JWT_SECRET
    try:
        payload = pyjwt.decode(token, JWT_SECRET, algorithms=['HS256'])
        return db.query_one('SELECT * FROM users WHERE id = ?', (payload['uid'],))
    except Exception:
        return None
