# -*- coding: utf-8 -*-
"""好友与附近人模块
接口：
  好友  /api/friends
    GET    /api/friends                    好友列表
    POST   /api/friends/request            发送好友申请 {userId, message?}
    GET    /api/friends/requests           我收到的好友申请（含待处理）
    GET    /api/friends/requests/sent      我发出的好友申请
    POST   /api/friends/requests/<id>/accept   接受申请
    POST   /api/friends/requests/<id>/reject   拒绝申请
    DELETE /api/friends/<userId>           删除好友
  附近人 /api/users
    PUT    /api/users/location             上报位置 {lat, lng}
    GET    /api/users/nearby               附近的人（可传 lat/lng/radiusKm，缺省用本人上报位置）
"""
import math

from flask import Blueprint, g, jsonify, request

from . import db
from .auth import auth_required


def _fmt_user(u, extra=None):
    """用户公开信息（好友/附近人场景）"""
    d = {
        'id': u['id'],
        'username': u['username'] or None,
        'nickname': u['nickname'],
        'avatar': u['avatar'],
        'online': u['game_status'] == 'online',
        'gameStatus': u['game_status'],
    }
    if extra:
        d.update(extra)
    return d


def _haversine_km(lat1, lng1, lat2, lng2):
    """两点球面距离（公里）"""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _friend_ids(user_id):
    rows = db.query('SELECT friend_id AS fid FROM friendships WHERE user_id = ?', (user_id,))
    return {r['fid'] for r in rows}


def _relationship(user_id, other_id):
    """两个用户之间的关系：none / friend / outgoing(pending 已发) / incoming(待我处理)"""
    if other_id in _friend_ids(user_id):
        return 'friend'
    out = db.query_one("SELECT 1 AS x FROM friend_requests WHERE from_user_id=? AND to_user_id=? AND status='pending'",
                       (user_id, other_id))
    if out:
        return 'outgoing'
    inc = db.query_one("SELECT 1 AS x FROM friend_requests WHERE from_user_id=? AND to_user_id=? AND status='pending'",
                       (other_id, user_id))
    if inc:
        return 'incoming'
    return 'none'


def _ensure_target(user_id):
    u = db.query_one('SELECT * FROM users WHERE id = ?', (user_id,))
    if not u:
        return None, (jsonify({'error': '用户不存在'}), 404)
    if u['status'] == 'banned':
        return None, (jsonify({'error': '该用户已被封禁'}), 403)
    return u, None


router = Blueprint('friends', __name__, url_prefix='/api')


# ==================== 好友 ====================

@router.get('/friends')
@auth_required
def friends_list():
    me = g.user['id']
    rows = db.query('''
        SELECT u.*, f.created_at AS friend_since FROM friendships f
        JOIN users u ON u.id = f.friend_id
        WHERE f.user_id = ? ORDER BY f.created_at DESC
    ''', (me,))
    return jsonify({'friends': [_fmt_user(r, {'friendSince': r['friend_since']}) for r in rows]})


@router.post('/friends/request')
@auth_required
def friend_request():
    me = g.user['id']
    data = request.get_json(silent=True) or {}
    try:
        target_id = int(data.get('userId') or 0)
    except (TypeError, ValueError):
        return jsonify({'error': 'userId 无效'}), 400
    if target_id <= 0:
        return jsonify({'error': 'userId 无效'}), 400
    if target_id == me:
        return jsonify({'error': '不能添加自己为好友'}), 400

    target, err = _ensure_target(target_id)
    if err:
        return err

    if target_id in _friend_ids(me):
        return jsonify({'error': '对方已是你的好友'}), 409
    dup = db.query_one("SELECT id, status FROM friend_requests WHERE from_user_id=? AND to_user_id=? ORDER BY id DESC LIMIT 1",
                       (me, target_id))
    if dup and dup['status'] == 'pending':
        return jsonify({'error': '已发送过申请，等待对方处理'}), 409
    if dup and dup['status'] == 'accepted':
        return jsonify({'error': '对方已是你的好友'}), 409

    message = (data.get('message') or '').strip()[:200]
    req_id, _ = db.execute(
        "INSERT INTO friend_requests (from_user_id, to_user_id, message) VALUES (?, ?, ?)",
        (me, target_id, message))
    return jsonify({'ok': True, 'requestId': req_id, 'message': '申请已发送'}), 201


@router.get('/friends/requests')
@auth_required
def friend_requests_incoming():
    me = g.user['id']
    rows = db.query('''
        SELECT fr.*, u.username, u.nickname, u.avatar, u.game_status FROM friend_requests fr
        JOIN users u ON u.id = fr.from_user_id
        WHERE fr.to_user_id = ? ORDER BY fr.id DESC
    ''', (me,))
    out = []
    for r in rows:
        d = dict(r)
        d['from'] = _fmt_user(r, {'message': r['message'], 'requestId': r['id']})
        out.append(d)
    return jsonify({'requests': out})


@router.get('/friends/requests/sent')
@auth_required
def friend_requests_sent():
    me = g.user['id']
    rows = db.query('''
        SELECT fr.*, u.username, u.nickname, u.avatar, u.game_status FROM friend_requests fr
        JOIN users u ON u.id = fr.to_user_id
        WHERE fr.from_user_id = ? ORDER BY fr.id DESC
    ''', (me,))
    out = []
    for r in rows:
        d = dict(r)
        d['to'] = _fmt_user(r, {'message': r['message'], 'requestId': r['id']})
        out.append(d)
    return jsonify({'requests': out})


def _get_request_or_404(req_id, me):
    row = db.query_one('SELECT * FROM friend_requests WHERE id = ?', (req_id,))
    if not row or row['to_user_id'] != me:
        return None, (jsonify({'error': '申请不存在'}), 404)
    return row, None


@router.post('/friends/requests/<int:req_id>/accept')
@auth_required
def friend_accept(req_id):
    me = g.user['id']
    row, err = _get_request_or_404(req_id, me)
    if err:
        return err
    if row['status'] != 'pending':
        return jsonify({'error': '该申请已处理'}), 409

    from_id, to_id = row['from_user_id'], row['to_user_id']
    db.execute("UPDATE friend_requests SET status='accepted', handled_at=datetime('now') WHERE id=?", (req_id,))
    db.execute('INSERT OR IGNORE INTO friendships (user_id, friend_id) VALUES (?, ?)', (from_id, to_id))
    db.execute('INSERT OR IGNORE INTO friendships (user_id, friend_id) VALUES (?, ?)', (to_id, from_id))
    return jsonify({'ok': True, 'message': '已添加好友'})


@router.post('/friends/requests/<int:req_id>/reject')
@auth_required
def friend_reject(req_id):
    me = g.user['id']
    row, err = _get_request_or_404(req_id, me)
    if err:
        return err
    if row['status'] != 'pending':
        return jsonify({'error': '该申请已处理'}), 409
    db.execute("UPDATE friend_requests SET status='rejected', handled_at=datetime('now') WHERE id=?", (req_id,))
    return jsonify({'ok': True, 'message': '已拒绝'})


@router.delete('/friends/<int:user_id>')
@auth_required
def friend_delete(user_id):
    me = g.user['id']
    if user_id not in _friend_ids(me):
        return jsonify({'error': '对方不是你的好友'}), 404
    db.execute('DELETE FROM friendships WHERE (user_id=? AND friend_id=?) OR (user_id=? AND friend_id=?)',
               (me, user_id, user_id, me))
    return jsonify({'ok': True, 'message': '已删除好友'})


# ==================== 附近人 ====================

@router.put('/users/location')
@auth_required
def update_location():
    me = g.user['id']
    data = request.get_json(silent=True) or {}
    try:
        lat = float(data.get('lat'))
        lng = float(data.get('lng'))
    except (TypeError, ValueError):
        return jsonify({'error': 'lat/lng 必须是数字'}), 400
    if not (-90 <= lat <= 90) or not (-180 <= lng <= 180):
        return jsonify({'error': '经纬度超出合法范围'}), 400
    db.execute("UPDATE users SET lat=?, lng=?, last_location_at=datetime('now') WHERE id=?", (lat, lng, me))
    return jsonify({'ok': True, 'lat': lat, 'lng': lng})


@router.get('/users/nearby')
@auth_required
def nearby_users():
    me = g.user['id']
    me_row = db.query_one('SELECT * FROM users WHERE id = ?', (me,))

    try:
        radius = float(request.args.get('radiusKm', 10))
    except (TypeError, ValueError):
        return jsonify({'error': 'radiusKm 无效'}), 400
    radius = max(0.001, min(radius, 500))

    lat = request.args.get('lat')
    lng = request.args.get('lng')
    if lat is not None and lng is not None:
        try:
            lat, lng = float(lat), float(lng)
        except (TypeError, ValueError):
            return jsonify({'error': 'lat/lng 无效'}), 400
    elif me_row['lat'] is not None and me_row['lng'] is not None:
        lat, lng = me_row['lat'], me_row['lng']
    else:
        return jsonify({'error': '请先通过 PUT /api/users/location 上报位置'}), 400

    rows = db.query('''
        SELECT id, username, nickname, avatar, game_status, lat, lng, last_location_at
        FROM users WHERE lat IS NOT NULL AND lng IS NOT NULL AND id != ?
    ''', (me,))
    my_friends = _friend_ids(me)
    results = []
    for u in rows:
        if u['lat'] is None or u['lng'] is None:
            continue
        dist = _haversine_km(lat, lng, u['lat'], u['lng'])
        if dist > radius:
            continue
        results.append(_fmt_user(u, {
            'distanceKm': round(dist, 2),
            'lastLocationAt': u['last_location_at'],
            'relationship': 'friend' if u['id'] in my_friends else _relationship(me, u['id']),
        }))
    results.sort(key=lambda x: x['distanceKm'])
    return jsonify({'nearby': results, 'center': {'lat': lat, 'lng': lng}, 'radiusKm': radius})
