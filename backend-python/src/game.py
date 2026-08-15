# -*- coding: utf-8 -*-
"""游戏对接模块：供团结引擎客户端同步官网账号
接口与原 Node 版兼容：/api/game/me、/api/game/playtime、/api/game/heartbeat
"""
from flask import Blueprint, g, jsonify, request

from . import db
from .auth import auth_required

router = Blueprint('game', __name__, url_prefix='/api/game')


@router.get('/me')
@auth_required
def me():
    u = g.user
    return jsonify({'user': {
        'id': u['id'],
        'username': u['username'],
        'nickname': u['nickname'],
        'avatar': u['avatar'],
        'playHours': u['play_hours'] or 0,
        'gameStatus': u['game_status'] or 'offline',
    }})


@router.post('/playtime')
@auth_required
def playtime():
    data = request.get_json(silent=True) or {}
    seconds = max(0, float(data.get('seconds', 0) or 0))
    # 单次上报不超过 24 小时，防止异常数据
    seconds = min(seconds, 86400)
    db.execute('UPDATE users SET play_hours = play_hours + ? WHERE id = ?',
               (round(seconds / 3600.0, 3), g.user['id']))
    u = db.query_one('SELECT * FROM users WHERE id = ?', (g.user['id'],))
    return jsonify({'ok': True, 'playHours': u['play_hours'] or 0})


@router.post('/heartbeat')
@auth_required
def heartbeat():
    status = request.get_json(silent=True) or {}
    game_status = (status.get('status') or 'online')[:32]
    db.execute("UPDATE users SET game_status = ?, last_heartbeat_at = datetime('now') WHERE id = ?",
               (game_status, g.user['id']))
    return jsonify({'ok': True})


@router.post('/logout')
@auth_required
def logout():
    db.execute("UPDATE users SET game_status = 'offline', last_heartbeat_at = datetime('now') WHERE id = ?",
               (g.user['id'],))
    return jsonify({'ok': True})
