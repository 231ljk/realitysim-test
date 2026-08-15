# -*- coding: utf-8 -*-
"""管理后台扩展接口：仪表盘 / 日志 / 批量操作 / 公告 / 敏感词 / 系统设置 / 导出"""
import secrets

from flask import Blueprint, Response, jsonify, request

from . import db
from .auth import admin_required, hash_password, public_user

router = Blueprint('admin', __name__, url_prefix='/api/admin')


def audit(admin, action, target, detail):
    db.execute(
        'INSERT INTO audit_logs (admin_id, admin_name, action, target, detail) VALUES (?, ?, ?, ?, ?)',
        (admin['id'], admin['username'] or admin['nickname'] or admin['id'], action, target or '', detail or ''))


def random_password(length=10):
    chars = 'abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789'
    return ''.join(chars[b % len(chars)] for b in secrets.token_bytes(length))


# ============ 仪表盘统计 ============
@router.get('/stats')
@admin_required
def stats():
    def cnt(sql, args=()):
        return db.query_one(sql, args)['n']

    today = cnt("SELECT COUNT(*) AS n FROM users WHERE date(created_at) = date('now')")
    week = cnt("SELECT COUNT(*) AS n FROM users WHERE created_at >= datetime('now', '-7 days')")
    month = cnt("SELECT COUNT(*) AS n FROM users WHERE created_at >= datetime('now', '-30 days')")
    login_today = cnt("SELECT COUNT(*) AS n FROM login_logs WHERE success = 1 AND date(created_at) = date('now')")
    login_total = cnt('SELECT COUNT(*) AS n FROM login_logs WHERE success = 1')
    oauth_total = cnt('SELECT COUNT(*) AS n FROM oauth_bindings')
    oauth_by_provider = [dict(r) for r in db.query('SELECT provider, COUNT(*) AS n FROM oauth_bindings GROUP BY provider')]

    trend = []
    for i in range(6, -1, -1):
        d = db.query_one("SELECT COUNT(*) AS n FROM users WHERE date(created_at) = date('now', ?)",
                         (f'-{i} days',))
        label = db.query_one("SELECT date('now', ?) AS d", (f'-{i} days',))['d']
        trend.append({'date': label[5:], 'count': d['n']})
    channel_dist = [dict(r) for r in db.query('SELECT channel, COUNT(*) AS n FROM login_logs WHERE success = 1 GROUP BY channel')]

    return jsonify({
        'users': {
            'total': cnt('SELECT COUNT(*) AS n FROM users'),
            'today': today, 'week': week, 'month': month,
            'normal': cnt("SELECT COUNT(*) AS n FROM users WHERE status = 'normal'"),
            'disabled': cnt("SELECT COUNT(*) AS n FROM users WHERE status = 'disabled'"),
            'banned': cnt("SELECT COUNT(*) AS n FROM users WHERE status = 'banned'"),
            'whitelist': cnt('SELECT COUNT(*) AS n FROM users WHERE whitelisted = 1'),
            'admins': cnt("SELECT COUNT(*) AS n FROM users WHERE role = 'admin'"),
        },
        'login': {'today': login_today, 'total': login_total},
        'oauth': {'total': oauth_total, 'byProvider': oauth_by_provider},
        'trend': trend,
        'channelDist': channel_dist,
        'audit': cnt('SELECT COUNT(*) AS n FROM audit_logs'),
        'announcements': cnt('SELECT COUNT(*) AS n FROM announcements WHERE published = 1'),
        'sensitiveWords': cnt('SELECT COUNT(*) AS n FROM sensitive_words'),
    })


# ============ 登录日志 ============
@router.get('/login-logs')
@admin_required
def login_logs():
    page = max(1, int(request.args.get('page', '1') or 1))
    page_size = min(100, max(1, int(request.args.get('pageSize', '20') or 20)))
    keyword = request.args.get('keyword', '')
    success = request.args.get('success', '')
    conds = []
    args = []
    if keyword:
        k = f'%{keyword}%'
        conds.append('(login_name LIKE ? OR detail LIKE ? OR ip LIKE ?)')
        args.extend([k, k, k])
    if success in ('1', '0'):
        conds.append('success = ?')
        args.append(int(success))
    where = ('WHERE ' + ' AND '.join(conds)) if conds else ''
    total = db.query_one(f'SELECT COUNT(*) AS n FROM login_logs {where}', args)['n']
    rows = db.query(f'SELECT * FROM login_logs {where} ORDER BY id DESC LIMIT ? OFFSET ?',
                    args + [page_size, (page - 1) * page_size])
    return jsonify({'logs': [dict(r) for r in rows], 'total': total, 'page': page, 'pageSize': page_size})


# ============ 操作审计日志 ============
@router.get('/audit-logs')
@admin_required
def audit_logs():
    page = max(1, int(request.args.get('page', '1') or 1))
    page_size = min(100, max(1, int(request.args.get('pageSize', '20') or 20)))
    keyword = request.args.get('keyword', '')
    conds = []
    args = []
    if keyword:
        k = f'%{keyword}%'
        conds.append('(admin_name LIKE ? OR action LIKE ? OR target LIKE ? OR detail LIKE ?)')
        args.extend([k, k, k, k])
    where = ('WHERE ' + ' AND '.join(conds)) if conds else ''
    total = db.query_one(f'SELECT COUNT(*) AS n FROM audit_logs {where}', args)['n']
    rows = db.query(f'SELECT * FROM audit_logs {where} ORDER BY id DESC LIMIT ? OFFSET ?',
                    args + [page_size, (page - 1) * page_size])
    return jsonify({'logs': [dict(r) for r in rows], 'total': total, 'page': page, 'pageSize': page_size})


# ============ 用户详情 ============
@router.get('/users/<int:uid>')
@admin_required
def user_detail(uid):
    user = db.query_one('SELECT * FROM users WHERE id = ?', (uid,))
    if not user:
        return jsonify({'error': '用户不存在'}), 404
    bindings = db.query('SELECT * FROM oauth_bindings WHERE user_id = ?', (uid,))
    logs = db.query('SELECT * FROM login_logs WHERE user_id = ? ORDER BY id DESC LIMIT 20', (uid,))
    msg_count = db.query_one('SELECT COUNT(*) AS n FROM messages WHERE sender_id = ?', (uid,))['n']
    return jsonify({'user': public_user(user), 'bindings': [dict(b) for b in bindings],
                    'logs': [dict(l) for l in logs], 'msgCount': msg_count})


# ============ 批量操作 ============
@router.post('/users/batch')
@admin_required
def batch_users():
    data = request.get_json(silent=True) or {}
    ids = [n for n in (data.get('ids') or []) if isinstance(n, int) and n > 0]
    action = data.get('action', '')
    reason = data.get('reason', '')
    if not ids:
        return jsonify({'error': '请至少选择一个用户'}), 400
    patch_map = {
        'disable': {'status': 'disabled'},
        'enable': {'status': 'normal'},
        'ban': {'status': 'banned', 'bannedReason': reason},
        'unban': {'status': 'normal', 'bannedReason': ''},
        'whitelist-on': {'whitelisted': True},
        'whitelist-off': {'whitelisted': False},
    }
    patch = patch_map.get(action)
    if not patch:
        return jsonify({'error': '不支持的操作'}), 400
    affected = 0
    from flask import g
    for uid in ids:
        if uid == g.user['id'] and patch.get('status') == 'banned':
            continue
        u = db.query_one('SELECT * FROM users WHERE id = ?', (uid,))
        if not u:
            continue
        sets = []
        args = []
        if patch.get('status') is not None:
            sets.append('status = ?')
            args.append(patch['status'])
        if patch.get('bannedReason') is not None:
            sets.append('banned_reason = ?')
            args.append(patch['bannedReason'])
        if patch.get('whitelisted') is not None:
            sets.append('whitelisted = ?')
            args.append(1 if patch['whitelisted'] else 0)
        args.append(uid)
        db.execute(f'UPDATE users SET {", ".join(sets)} WHERE id = ?', args)
        affected += 1
    audit(g.user, 'batch-' + action, f'用户ID: {",".join(str(i) for i in ids)}',
          reason or f'共影响 {affected} 个账号')
    return jsonify({'ok': True, 'affected': affected})


# ============ 重置密码 ============
@router.post('/users/<int:uid>/reset-password')
@admin_required
def reset_password(uid):
    user = db.query_one('SELECT * FROM users WHERE id = ?', (uid,))
    if not user:
        return jsonify({'error': '用户不存在'}), 404
    data = request.get_json(silent=True) or {}
    new_pw = data.get('password') or random_password()
    salt = secrets.token_hex(16)
    db.execute('UPDATE users SET password_hash = ? WHERE id = ?',
               (f'{salt}:{hash_password(new_pw, salt)}', user['id']))
    audit(request.user if hasattr(request, 'user') else _g_user(), 'reset-password',
          f'用户ID: {user["id"]}', f"重置 {user['username'] or user['nickname'] or ''} 的密码")
    return jsonify({'ok': True, 'password': new_pw})


def _g_user():
    from flask import g
    return g.user


# ============ 角色切换 ============
@router.post('/users/<int:uid>/role')
@admin_required
def set_role(uid):
    user = db.query_one('SELECT * FROM users WHERE id = ?', (uid,))
    if not user:
        return jsonify({'error': '用户不存在'}), 404
    data = request.get_json(silent=True) or {}
    role = data.get('role')
    if role not in ('admin', 'user'):
        return jsonify({'error': 'role 仅支持 admin / user'}), 400
    if user['id'] == _g_user()['id'] and role != 'admin':
        return jsonify({'error': '不能取消自己的管理员权限'}), 400
    db.execute('UPDATE users SET role = ? WHERE id = ?', (role, user['id']))
    audit(_g_user(), 'set-role', f'用户ID: {user["id"]}', f'角色变更为 {role}')
    fresh = db.query_one('SELECT * FROM users WHERE id = ?', (user['id'],))
    return jsonify({'user': public_user(fresh)})


# ============ 公告管理 ============
@router.get('/announcements')
@admin_required
def list_announcements():
    rows = db.query('SELECT * FROM announcements ORDER BY id DESC')
    return jsonify({'list': [dict(r) for r in rows]})


@router.post('/announcements')
@admin_required
def create_announcement():
    data = request.get_json(silent=True) or {}
    title = data.get('title')
    content = data.get('content')
    published = data.get('published', 0)
    if not title or not content:
        return jsonify({'error': '标题和内容不能为空'}), 400
    last_id, _ = db.execute(
        'INSERT INTO announcements (title, content, published) VALUES (?, ?, ?)',
        (str(title), str(content), 1 if published else 0))
    audit(_g_user(), 'announcement', f'公告ID: {last_id}', f'新增公告「{title}」')
    return jsonify({'ok': True, 'id': last_id})


@router.put('/announcements/<int:aid>')
@admin_required
def update_announcement(aid):
    data = request.get_json(silent=True) or {}
    title = data.get('title')
    content = data.get('content')
    published = data.get('published')
    item = db.query_one('SELECT * FROM announcements WHERE id = ?', (aid,))
    if not item:
        return jsonify({'error': '公告不存在'}), 404
    if title is not None:
        db.execute('UPDATE announcements SET title = ? WHERE id = ?', (str(title), item['id']))
    if content is not None:
        db.execute('UPDATE announcements SET content = ? WHERE id = ?', (str(content), item['id']))
    if published is not None:
        db.execute("UPDATE announcements SET published = ?, updated_at = datetime('now') WHERE id = ?",
                   (1 if published else 0, item['id']))
    audit(_g_user(), 'announcement', f'公告ID: {item["id"]}', f'更新公告「{title or item["title"]}」')
    return jsonify({'ok': True})


@router.delete('/announcements/<int:aid>')
@admin_required
def delete_announcement(aid):
    item = db.query_one('SELECT * FROM announcements WHERE id = ?', (aid,))
    if not item:
        return jsonify({'error': '公告不存在'}), 404
    db.execute('DELETE FROM announcements WHERE id = ?', (item['id'],))
    audit(_g_user(), 'announcement', f'公告ID: {item["id"]}', f'删除公告「{item["title"]}」')
    return jsonify({'ok': True})


# ============ 敏感词管理 ============
@router.get('/sensitive-words')
@admin_required
def list_words():
    rows = db.query('SELECT * FROM sensitive_words ORDER BY id DESC')
    return jsonify({'list': [dict(r) for r in rows], 'total': len(rows)})


@router.post('/sensitive-words')
@admin_required
def add_word():
    data = request.get_json(silent=True) or {}
    word = data.get('word')
    if not word or not str(word).strip():
        return jsonify({'error': '敏感词不能为空'}), 400
    try:
        last_id, _ = db.execute('INSERT INTO sensitive_words (word) VALUES (?)', (str(word).strip(),))
        audit(_g_user(), 'word', f'敏感词ID: {last_id}', f'新增敏感词「{word}」')
        return jsonify({'ok': True, 'id': last_id})
    except Exception as e:
        if 'UNIQUE' in str(e):
            return jsonify({'error': '该敏感词已存在'}), 409
        return jsonify({'error': '添加失败: ' + str(e)}), 500


@router.delete('/sensitive-words/<int:wid>')
@admin_required
def delete_word(wid):
    item = db.query_one('SELECT * FROM sensitive_words WHERE id = ?', (wid,))
    if not item:
        return jsonify({'error': '敏感词不存在'}), 404
    db.execute('DELETE FROM sensitive_words WHERE id = ?', (item['id'],))
    audit(_g_user(), 'word', f'敏感词ID: {item["id"]}', f'删除敏感词「{item["word"]}」')
    return jsonify({'ok': True})


# ============ 系统设置 ============
@router.get('/settings')
@admin_required
def get_settings():
    rows = db.query('SELECT key, value FROM settings')
    return jsonify({'settings': {r['key']: r['value'] for r in rows}})


@router.put('/settings')
@admin_required
def update_settings():
    data = request.get_json(silent=True) or {}
    settings = data.get('settings') or {}
    allowed = ['register_enabled', 'login_enabled', 'announcement_enabled',
               'verify_code_required', 'site_name']
    changed = []
    for key in allowed:
        if key not in settings:
            continue
        db.execute('INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value',
                   (key, str(settings[key])))
        changed.append(f'{key}={settings[key]}')
    if changed:
        audit(_g_user(), 'setting', '系统设置', ', '.join(changed))
    return jsonify({'ok': True, 'changed': changed})


# ============ 导出用户 CSV ============
@router.get('/export/users')
@admin_required
def export_users():
    rows = db.query('SELECT * FROM users ORDER BY id DESC')

    def esc(v):
        s = str('' if v is None else v)
        return '"' + s.replace('"', '""') + '"' if re_search(s) else s

    head = ['ID', '用户名', '手机号', '邮箱', '昵称', '头像', '角色', '状态', '白名单', '封禁原因', '最近登录', '注册时间']
    lines = []
    for u in rows:
        lines.append(','.join([
            esc(u['id']), esc(u['username']), esc(u['phone']), esc(u['email']),
            esc(u['nickname']), esc(u['avatar']), esc(u['role']), esc(u['status']),
            '是' if u['whitelisted'] else '否', esc(u['banned_reason'] or ''),
            esc(u['last_login_at'] or ''), esc(u['created_at'] or '')]))
    csv = '\ufeff' + ','.join(head) + '\n' + '\n'.join(lines)
    audit(_g_user(), 'export', '用户数据', f'导出 {len(rows)} 条用户记录')
    return Response(csv, mimetype='text/csv; charset=utf-8',
                    headers={'Content-Disposition': 'attachment; filename="realitysim-users.csv"'})


import re as _re
re_search = _re.search


# ============ 查询用户公开接口（供管理端联想） ============
@router.get('/users')
@admin_required
def admin_users_public():
    rows = db.query('SELECT id, username, nickname, phone, email, role, status FROM users ORDER BY id DESC LIMIT 200')
    return jsonify({'users': [dict(r) for r in rows]})
