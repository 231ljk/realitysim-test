# -*- coding: utf-8 -*-
"""认证模块（大厂风格多品牌登录）
支持：用户名/手机号/邮箱 + 密码、手机号/邮箱 + 验证码、微信/QQ/抖音/微软 OAuth
管理员后台：用户管理（禁用/封禁/解封/白名单）、黑白名单
接口与原 Node 版完全兼容。
"""
import hashlib
import json
import re
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from functools import wraps
from urllib import request as urllib_request
from urllib import parse as urllib_parse

import jwt as pyjwt
from flask import Blueprint, g, jsonify, request

from . import db
from .config import (ADMIN_NICKNAME, ADMIN_PASSWORD, ADMIN_USERNAME, DEV_MODE,
                     JWT_EXPIRES, JWT_SECRET, MAIL_HOST, OAUTH, OAUTH_DEV_MOCK,
                     PUBLIC_BASE_URL, SMS_PROVIDER)

router = Blueprint('auth', __name__, url_prefix='/api/auth')

# ---------- 工具 ----------
PHONE_RE = re.compile(r'^1[3-9]\d{9}$')
EMAIL_RE = re.compile(r'^[^\s@]+@[^\s@]+\.[^\s@]+$')
USERNAME_RE = re.compile(r'^[a-zA-Z0-9_]{3,20}$')


def hash_password(password, salt):
    """与 Node crypto.scryptSync(password, salt, 64) 兼容：salt 按 UTF-8 字节处理"""
    return hashlib.scrypt(password.encode('utf-8'), salt=salt.encode('utf-8'),
                          n=16384, r=8, p=1, dklen=64).hex()


def public_user(u):
    return {
        'id': u['id'],
        'username': u['username'] or None,
        'phone': u['phone'] or None,
        'email': u['email'] or None,
        'nickname': u['nickname'],
        'avatar': u['avatar'],
        'role': u['role'] or 'user',
        'status': u['status'] or 'normal',
        'whitelisted': bool(u['whitelisted']),
        'bannedReason': u['banned_reason'] or '',
        'lastLoginAt': u['last_login_at'] or None,
        'createdAt': u['created_at'] or None,
    }


def validate_password(pw):
    if not pw or len(pw) < 6:
        return '密码至少 6 位'
    return None


def check_user_status(user):
    """登录状态检查：封禁不可登录（白名单无效）；禁用时白名单用户可登录"""
    if not user:
        return '账号或密码错误'
    if user['status'] == 'banned':
        return f"账号已被封禁：{user['banned_reason']}" if user['banned_reason'] else '账号已被封禁，请联系管理员'
    if user['status'] == 'disabled' and not user['whitelisted']:
        return '账号已被禁用，请联系管理员'
    return None


def _parse_expires(expires):
    """解析 JWT_EXPIRES：'7d'/'12h'/'30m'/'45s' 或纯数字（秒）"""
    try:
        return int(expires)
    except ValueError:
        pass
    m = re.match(r'^(\d+)([smhd])$', expires.strip())
    if not m:
        return 7 * 24 * 3600
    n = int(m.group(1))
    unit = m.group(2)
    return n * {'s': 1, 'm': 60, 'h': 3600, 'd': 86400}[unit]


def issue_token(user):
    db.execute("UPDATE users SET last_login_at = datetime('now') WHERE id = ?", (user['id'],))
    fresh = db.query_one('SELECT * FROM users WHERE id = ?', (user['id'],))
    now = datetime.now(timezone.utc)
    exp_seconds = _parse_expires(JWT_EXPIRES)
    token = pyjwt.encode(
        {'uid': user['id'], 'username': user['username'],
         'iat': now, 'exp': now + timedelta(seconds=exp_seconds)},
        JWT_SECRET, algorithm='HS256')
    return token, public_user(fresh)


# ---------- 验证码 ----------
_code_store = {}
_code_lock = threading.Lock()


def send_verification_code(channel, target):
    code = str(secrets.randbelow(900000) + 100000)
    key = f'{channel}:{target}'
    now = time.time()
    with _code_lock:
        prev = _code_store.get(key)
        if prev and now - prev['sentAt'] < 60:
            return {'error': '验证码发送过于频繁，请 60 秒后重试',
                    'wait': 60 - round(now - prev['sentAt'])}
        _code_store[key] = {'code': code, 'sentAt': now}
    # 5 分钟后过期
    def _expire():
        with _code_lock:
            rec = _code_store.get(key)
            if rec and rec['code'] == code:
                _code_store.pop(key, None)
    threading.Timer(300, _expire).start()

    dev_code = None
    if channel == 'phone' and SMS_PROVIDER:
        # 真实短信通道接入点：调用短信服务商 API（阿里云/腾讯云短信等）
        pass
    elif channel == 'email' and MAIL_HOST:
        # 真实邮件通道接入点：调用 SMTP 发送
        pass
    elif DEV_MODE:
        dev_code = code
    else:
        return {'error': ('短信通道未配置，请联系管理员' if channel == 'phone'
                          else '邮件通道未配置，请联系管理员')}
    return {'ok': True, 'devCode': dev_code}


def log_login(user_id=None, login_name='', channel='pwd', provider='', req=None, success=1, detail=''):
    try:
        ip = (req.headers.get('x-forwarded-for') or req.remote_addr or '')[:64]
        ua = (req.headers.get('user-agent') or '')[:255]
        db.execute(
            'INSERT INTO login_logs (user_id, login_name, channel, provider, ip, ua, success, detail) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
            (user_id, login_name, channel, provider, ip, ua, 1 if success else 0, detail or ''))
    except Exception:
        pass


def get_setting(key, default=None):
    try:
        row = db.query_one('SELECT value FROM settings WHERE key = ?', (key,))
        return row['value'] if row else default
    except Exception:
        return default


# ---------- 中间件 ----------
def auth_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        user = _try_auth()
        if user is None:
            return jsonify({'error': '未登录'}), 401
        g.user = user
        return fn(*args, **kwargs)
    return wrapper


def _try_auth():
    """尝试解析登录态；未登录返回 None（供可选登录接口使用）"""
    header = request.headers.get('Authorization', '')
    token = header[7:] if header.startswith('Bearer ') else None
    if not token:
        return None
    try:
        payload = pyjwt.decode(token, JWT_SECRET, algorithms=['HS256'])
        user = db.query_one('SELECT * FROM users WHERE id = ?', (payload['uid'],))
        return user
    except Exception:
        return None


def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        user = _try_auth()
        if user is None:
            return jsonify({'error': '未登录'}), 401
        if (user['role'] or 'user') != 'admin':
            return jsonify({'error': '仅管理员可操作'}), 403
        g.user = user
        return fn(*args, **kwargs)
    return wrapper


def _body():
    return request.get_json(silent=True) or {}


# ---------- 注册 ----------
@router.post('/register')
def register():
    if get_setting('register_enabled', '1') != '1':
        return jsonify({'error': '系统暂未开放注册，请联系管理员'}), 403
    data = _body()
    username = data.get('username')
    phone = data.get('phone')
    email = data.get('email')
    password = data.get('password')
    nickname = data.get('nickname')
    avatar = data.get('avatar')
    if not username and not phone and not email:
        return jsonify({'error': '用户名 / 手机号 / 邮箱至少填写一个'}), 400
    if username and not USERNAME_RE.match(username):
        return jsonify({'error': '用户名需为 3-20 位字母/数字/下划线'}), 400
    if phone and not PHONE_RE.match(phone):
        return jsonify({'error': '手机号格式不正确（需为 11 位大陆手机号）'}), 400
    if email and not EMAIL_RE.match(email):
        return jsonify({'error': '邮箱格式不正确'}), 400
    pw_err = validate_password(password)
    if pw_err:
        return jsonify({'error': pw_err}), 400
    if not nickname:
        return jsonify({'error': '昵称不能为空'}), 400

    salt = secrets.token_hex(16)
    password_hash = hash_password(password, salt)
    try:
        last_id, _ = db.execute(
            'INSERT INTO users (username, phone, email, password_hash, nickname, avatar) VALUES (?, ?, ?, ?, ?, ?)',
            (username or None, phone or None, email or None, f'{salt}:{password_hash}', nickname, avatar or ''))
        user = db.query_one('SELECT * FROM users WHERE id = ?', (last_id,))
        log_login(user_id=user['id'], login_name=username or phone or email, channel='register', req=request)
        token, pub = issue_token(user)
        return jsonify({'token': token, 'user': pub})
    except Exception as e:
        if 'UNIQUE' in str(e):
            return jsonify({'error': '该用户名 / 手机号 / 邮箱已被注册'}), 409
        return jsonify({'error': '注册失败: ' + str(e)}), 500


# ---------- 账号密码登录 ----------
@router.post('/login')
def login():
    if get_setting('login_enabled', '1') != '1':
        return jsonify({'error': '系统暂未开放登录，请联系管理员'}), 403
    data = _body()
    login_name = data.get('login')
    password = data.get('password')
    if not login_name or not password:
        return jsonify({'error': '登录账号与密码不能为空'}), 400
    user = db.query_one('SELECT * FROM users WHERE username = ? OR phone = ? OR email = ?',
                        (login_name, login_name, login_name))
    status_err = check_user_status(user)
    if status_err:
        log_login(user_id=user['id'] if user else None, login_name=login_name, channel='pwd',
                  req=request, success=0, detail=status_err)
        return jsonify({'error': status_err}), 403
    if not user:
        log_login(login_name=login_name, channel='pwd', req=request, success=0, detail='账号不存在')
        return jsonify({'error': '账号或密码错误'}), 401
    salt, stored = user['password_hash'].split(':')
    computed = hash_password(password, salt)
    if computed != stored:
        log_login(user_id=user['id'], login_name=login_name, channel='pwd',
                  req=request, success=0, detail='密码错误')
        return jsonify({'error': '账号或密码错误'}), 401
    log_login(user_id=user['id'], login_name=login_name, channel='pwd', req=request)
    token, pub = issue_token(user)
    return jsonify({'token': token, 'user': pub})


# ---------- 发送验证码 ----------
@router.post('/send-code')
def send_code():
    data = _body()
    channel = data.get('channel')
    target = data.get('target')
    if channel not in ('phone', 'email'):
        return jsonify({'error': 'channel 仅支持 phone / email'}), 400
    if channel == 'phone' and not PHONE_RE.match(target or ''):
        return jsonify({'error': '手机号格式不正确'}), 400
    if channel == 'email' and not EMAIL_RE.match(target or ''):
        return jsonify({'error': '邮箱格式不正确'}), 400
    result = send_verification_code(channel, target)
    if result.get('error'):
        return jsonify({'error': result['error'], 'wait': result.get('wait')}), 400
    resp = {'ok': True, 'message': '验证码已发送'}
    if result.get('devCode') is not None:
        resp['devCode'] = result['devCode']
    return jsonify(resp)


# ---------- 验证码登录（未注册自动注册） ----------
@router.post('/login-code')
def login_code():
    if get_setting('login_enabled', '1') != '1':
        return jsonify({'error': '系统暂未开放登录，请联系管理员'}), 403
    data = _body()
    channel = data.get('channel')
    target = data.get('target')
    code = data.get('code')
    if channel not in ('phone', 'email'):
        return jsonify({'error': 'channel 仅支持 phone / email'}), 400
    if not target or not code:
        return jsonify({'error': '账号与验证码不能为空'}), 400
    rec = _code_store.get(f'{channel}:{target}')
    if not rec or rec['code'] != str(code):
        return jsonify({'error': '验证码错误或已过期'}), 401
    _code_store.pop(f'{channel}:{target}', None)

    user = db.query_one(f'SELECT * FROM users WHERE {channel} = ?', (target,))
    is_new = False
    if user:
        status_err = check_user_status(user)
        if status_err:
            log_login(user_id=user['id'], login_name=target, channel='code',
                      req=request, success=0, detail=status_err)
            return jsonify({'error': status_err}), 403
    else:
        if get_setting('register_enabled', '1') != '1':
            return jsonify({'error': '该账号未注册，且系统暂未开放注册'}), 403
        salt = secrets.token_hex(16)
        random_pw = secrets.token_hex(24)
        nickname = f"用户{target[-4:]}" if channel == 'phone' else target.split('@')[0]
        last_id, _ = db.execute(
            f'INSERT INTO users ({channel}, password_hash, nickname) VALUES (?, ?, ?)',
            (target, f'{salt}:{hash_password(random_pw, salt)}', nickname))
        user = db.query_one('SELECT * FROM users WHERE id = ?', (last_id,))
        is_new = True
    log_login(user_id=user['id'], login_name=target, channel='code', req=request,
              detail='验证码登录（自动注册）' if is_new else '')
    token, pub = issue_token(user)
    return jsonify({'token': token, 'user': pub})


# ---------- OAuth 第三方登录 ----------
def _http_get_json(url):
    req = urllib_request.Request(url, headers={'User-Agent': 'realitysim-backend'})
    with urllib_request.urlopen(req, timeout=15) as resp:
        data = resp.read().decode('utf-8', errors='replace')
    try:
        return json.loads(data)
    except Exception:
        return data


def _http_post_form(url, body):
    payload = urllib_parse.urlencode(body).encode('utf-8')
    req = urllib_request.Request(url, data=payload, headers={
        'Content-Type': 'application/x-www-form-urlencoded',
        'User-Agent': 'realitysim-backend'})
    with urllib_request.urlopen(req, timeout=15) as resp:
        data = resp.read().decode('utf-8', errors='replace')
    try:
        return json.loads(data)
    except Exception:
        return data


def _strip_jsonp(text):
    s = str(text)
    if s.startswith('callback('):
        s = s[len('callback('):]
    if s.endswith(');'):
        s = s[:-2]
    return s


@router.get('/oauth/<provider>/url')
def oauth_url(provider):
    p = OAUTH.get(provider)
    if not p:
        return jsonify({'error': '不支持的第三方平台'}), 400
    base = PUBLIC_BASE_URL or 'http://localhost:3000'
    if OAUTH_DEV_MOCK:
        return jsonify({'url': f'{base}/api/auth/oauth/{provider}/mock', 'mock': True})
    if not p['clientId']:
        return jsonify({'error': f"{p['name']}登录暂未开放（应用未配置）"}), 503
    return jsonify({'url': p['authorizeUrl'](p), 'mock': False})


@router.get('/oauth/<provider>/mock')
def oauth_mock(provider):
    p = OAUTH.get(provider)
    if not p:
        return '不支持的第三方平台', 400
    if not OAUTH_DEV_MOCK:
        return 'Not Found', 404
    code = f"mock{secrets.randbelow(900000) + 100000}"
    brand = p['name'][0]
    bg = '#07c160' if provider == 'wechat' else '#12b7f5' if provider == 'qq' else '#000' if provider == 'douyin' else '#0f6cbd'
    html = f'''<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8"><title>{p['name']}授权</title>
<style>body{{font-family:system-ui;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;background:#f5f6f8}}
.card{{background:#fff;border-radius:16px;box-shadow:0 12px 40px rgba(0,0,0,.08);padding:40px 48px;text-align:center;max-width:380px}}
.logo{{width:56px;height:56px;border-radius:14px;background:{bg};color:#fff;font-size:28px;font-weight:700;display:flex;align-items:center;justify-content:center;margin:0 auto 16px}}
h2{{margin:0 0 8px;font-size:18px;color:#1a1a1a}}p{{color:#888;font-size:14px;margin:0 0 24px}}
button{{width:100%;padding:12px;border:0;border-radius:10px;background:#1a1a1a;color:#fff;font-size:15px;cursor:pointer}}
button:hover{{opacity:.9}}</style></head><body>
<div class="card"><div class="logo">{brand}</div><h2>{p['name']}授权（开发模拟）</h2>
<p>未配置真实开放平台应用，此页面模拟用户在 {p['name']} 点击「同意授权」。</p>
<button onclick="location.href='/api/auth/oauth/{provider}/callback?code={code}'">同意授权</button></div>
</body></html>'''
    return html


@router.get('/oauth/<provider>/callback')
def oauth_callback(provider):
    if get_setting('login_enabled', '1') != '1':
        return '系统暂未开放登录，请联系管理员', 403
    p = OAUTH.get(provider)
    if not p:
        return '不支持的第三方平台', 400
    code = request.args.get('code')
    if not code:
        return '缺少授权码 code', 400

    identity = None
    if OAUTH_DEV_MOCK:
        identity = {'uid': f'dev-{provider}-{code}', 'name': f"{p['name']}用户{code[-4:]}", 'avatar': ''}
    elif p['clientId'] and p['clientSecret']:
        try:
            if provider == 'microsoft':
                tok = _http_post_form(p['tokenUrl'], {
                    'client_id': p['clientId'], 'client_secret': p['clientSecret'], 'code': code,
                    'redirect_uri': p['redirectUri'], 'grant_type': 'authorization_code', 'scope': 'User.Read'})
                if not tok.get('access_token'):
                    raise Exception('微软授权失败')
                me = _http_get_json(p['userUrl'] + '?$select=id,displayName')
                identity = {'uid': me.get('id'), 'name': me.get('displayName') or '微软用户', 'avatar': ''}
            elif provider == 'qq':
                tok_raw = _http_post_form(p['tokenUrl'], {
                    'grant_type': 'authorization_code', 'client_id': p['clientId'],
                    'client_secret': p['clientSecret'], 'code': code, 'redirect_uri': p['redirectUri'], 'fmt': 'json'})
                tok = json.loads(_strip_jsonp(tok_raw)) if isinstance(tok_raw, str) else tok_raw
                me = _http_get_json(f"{p['userUrl']}?access_token={tok['access_token']}&fmt=json")
                openid = json.loads(_strip_jsonp(me)) if isinstance(me, str) else me
                identity = {'uid': openid.get('openid'), 'name': openid.get('nickname') or 'QQ用户',
                            'avatar': openid.get('figureurl_qq_2') or ''}
            elif provider == 'douyin':
                tok = _http_post_form(p['tokenUrl'], {
                    'client_key': p['clientId'], 'client_secret': p['clientSecret'],
                    'code': code, 'grant_type': 'authorization_code'})
                me = _http_get_json(f"{p['userUrl']}?access_token={tok['access_token']}&open_id={tok['open_id']}")
                u = me.get('data') or {}
                identity = {'uid': u.get('open_id'), 'name': u.get('nickname') or '抖音用户',
                            'avatar': u.get('avatar') or ''}
            else:  # wechat
                tok = _http_get_json(f"{p['tokenUrl']}?appid={p['clientId']}&secret={p['clientSecret']}&code={code}&grant_type=authorization_code")
                me = _http_get_json(f"{p['userUrl']}?access_token={tok['access_token']}&openid={tok['openid']}&lang=zh_CN")
                identity = {'uid': me.get('openid'), 'name': me.get('nickname') or '微信用户',
                            'avatar': me.get('headimgurl') or ''}
        except Exception as e:
            return f'第三方登录失败: {e}', 502
    else:
        return '该第三方平台暂未开放登录', 503

    # 查绑定关系，自动注册
    bind = db.query_one('SELECT * FROM oauth_bindings WHERE provider = ? AND provider_uid = ?',
                        (provider, identity['uid']))
    if bind:
        user = db.query_one('SELECT * FROM users WHERE id = ?', (bind['user_id'],))
    else:
        salt = secrets.token_hex(16)
        random_pw = secrets.token_hex(24)
        last_id, _ = db.execute(
            'INSERT INTO users (password_hash, nickname, avatar) VALUES (?, ?, ?)',
            (f'{salt}:{hash_password(random_pw, salt)}', identity['name'], identity['avatar']))
        db.execute('INSERT INTO oauth_bindings (user_id, provider, provider_uid, provider_name) VALUES (?, ?, ?, ?)',
                   (last_id, provider, identity['uid'], p['name']))
        user = db.query_one('SELECT * FROM users WHERE id = ?', (last_id,))
    status_err = check_user_status(user)
    if status_err:
        log_login(user_id=user['id'], login_name=provider, channel='oauth', provider=provider,
                  req=request, success=0, detail=status_err)
        return status_err, 403
    log_login(user_id=user['id'], login_name=f'{provider}:{identity["uid"]}',
              channel='oauth', provider=provider, req=request)
    token, _ = issue_token(user)
    redirect_base = PUBLIC_BASE_URL or 'http://localhost:3000'
    return '', 302, {'Location': f'{redirect_base}/#/oauth?token={token}'}


@router.post('/oauth/bind')
@auth_required
def oauth_bind():
    data = _body()
    provider = data.get('provider')
    p = OAUTH.get(provider)
    if not p:
        return jsonify({'error': '不支持的第三方平台'}), 400
    if not p['clientId']:
        return jsonify({'error': f"{p['name']}暂未开放绑定"}), 503
    return jsonify({'url': p['authorizeUrl'](p) + '&state=bind'})


# ---------- 我的信息 / 绑定 ----------
@router.get('/me')
@auth_required
def me():
    return jsonify({'user': public_user(g.user)})


@router.post('/bind')
@auth_required
def bind():
    data = _body()
    phone = data.get('phone')
    email = data.get('email')
    if not phone and not email:
        return jsonify({'error': 'phone / email 至少提供一个'}), 400
    if phone and not PHONE_RE.match(phone):
        return jsonify({'error': '手机号格式不正确'}), 400
    if email and not EMAIL_RE.match(email):
        return jsonify({'error': '邮箱格式不正确'}), 400
    try:
        if phone:
            db.execute('UPDATE users SET phone = ? WHERE id = ?', (phone, g.user['id']))
        if email:
            db.execute('UPDATE users SET email = ? WHERE id = ?', (email, g.user['id']))
        user = db.query_one('SELECT * FROM users WHERE id = ?', (g.user['id'],))
        return jsonify({'user': public_user(user)})
    except Exception as e:
        if 'UNIQUE' in str(e):
            return jsonify({'error': '该手机号 / 邮箱已被其他账号绑定'}), 409
        return jsonify({'error': '绑定失败: ' + str(e)}), 500


# ============ 管理员后台：用户管理 ============
@router.get('/admin/users')
@admin_required
def admin_users():
    keyword = request.args.get('keyword', '')
    status = request.args.get('status', '')
    page = max(1, int(request.args.get('page', '1') or 1))
    page_size = min(100, max(1, int(request.args.get('pageSize', '20') or 20)))
    conds = []
    args = []
    if keyword:
        k = f'%{keyword}%'
        conds.append('(username LIKE ? OR nickname LIKE ? OR phone LIKE ? OR email LIKE ?)')
        args.extend([k, k, k, k])
    if status == 'whitelist':
        conds.append('whitelisted = 1')
    elif status in ('normal', 'disabled', 'banned'):
        conds.append('status = ?')
        args.append(status)
    where = ('WHERE ' + ' AND '.join(conds)) if conds else ''
    total = db.query_one(f'SELECT COUNT(*) AS n FROM users {where}', args)['n']
    rows = db.query(f'SELECT * FROM users {where} ORDER BY id DESC LIMIT ? OFFSET ?',
                    args + [page_size, (page - 1) * page_size])
    stats = {
        'total': db.query_one('SELECT COUNT(*) AS n FROM users')['n'],
        'normal': db.query_one("SELECT COUNT(*) AS n FROM users WHERE status = 'normal'")['n'],
        'disabled': db.query_one("SELECT COUNT(*) AS n FROM users WHERE status = 'disabled'")['n'],
        'banned': db.query_one("SELECT COUNT(*) AS n FROM users WHERE status = 'banned'")['n'],
        'whitelist': db.query_one('SELECT COUNT(*) AS n FROM users WHERE whitelisted = 1')['n'],
    }
    return jsonify({'users': [public_user(u) for u in rows], 'total': total,
                    'page': page, 'pageSize': page_size, 'stats': stats})


@router.patch('/admin/users/<int:uid>')
@admin_required
def admin_patch_user(uid):
    data = _body()
    status = data.get('status')
    whitelisted = data.get('whitelisted')
    banned_reason = data.get('bannedReason')
    if uid == g.user['id'] and status == 'banned':
        return jsonify({'error': '不能封禁自己的账号'}), 400
    user = db.query_one('SELECT * FROM users WHERE id = ?', (uid,))
    if not user:
        return jsonify({'error': '用户不存在'}), 404
    sets = []
    args = []
    if status is not None:
        if status not in ('normal', 'disabled', 'banned'):
            return jsonify({'error': 'status 参数不合法'}), 400
        sets.append('status = ?')
        args.append(status)
    if whitelisted is not None:
        sets.append('whitelisted = ?')
        args.append(1 if whitelisted else 0)
    if banned_reason is not None:
        sets.append('banned_reason = ?')
        args.append(str(banned_reason))
    if not sets:
        return jsonify({'error': '没有需要修改的字段'}), 400
    args.append(uid)
    db.execute(f'UPDATE users SET {", ".join(sets)} WHERE id = ?', args)
    fresh = db.query_one('SELECT * FROM users WHERE id = ?', (uid,))
    return jsonify({'user': public_user(fresh)})


@router.post('/admin/users/<int:uid>/<action>')
@admin_required
def admin_user_action(uid, action):
    data = _body()
    reason = data.get('reason') or ''
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
    if uid == g.user['id'] and patch.get('status') == 'banned':
        return jsonify({'error': '不能封禁自己的账号'}), 400
    user = db.query_one('SELECT * FROM users WHERE id = ?', (uid,))
    if not user:
        return jsonify({'error': '用户不存在'}), 404
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
    fresh = db.query_one('SELECT * FROM users WHERE id = ?', (uid,))
    return jsonify({'user': public_user(fresh)})


@router.get('/admin/lists')
@admin_required
def admin_lists():
    blacklist = db.query("SELECT * FROM users WHERE status = 'banned' ORDER BY id DESC")
    whitelist = db.query('SELECT * FROM users WHERE whitelisted = 1 ORDER BY id DESC')
    return jsonify({
        'blacklist': [public_user(u) for u in blacklist],
        'whitelist': [public_user(u) for u in whitelist],
    })


# ---------- 内置管理员种子 ----------
def seed_admin():
    existing = db.query_one('SELECT id FROM users WHERE username = ?', (ADMIN_USERNAME,))
    if existing:
        return
    salt = secrets.token_hex(16)
    password_hash = hash_password(ADMIN_PASSWORD, salt)
    db.execute('INSERT INTO users (username, password_hash, nickname, role) VALUES (?, ?, ?, ?)',
               (ADMIN_USERNAME, f'{salt}:{password_hash}', ADMIN_NICKNAME, 'admin'))
    print(f'[realitysim-backend] 已创建内置管理员: {ADMIN_USERNAME}（请尽快修改密码）')
