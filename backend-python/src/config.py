# -*- coding: utf-8 -*-
"""配置加载：读取 .env（不存在则用默认值）"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv():
    env_path = ROOT / '.env'
    if not env_path.exists():
        return
    try:
        raw = env_path.read_bytes()
        try:
            text = raw.decode('utf-8')
        except UnicodeDecodeError:
            text = raw.decode('gbk', errors='ignore')
    except Exception:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, _, value = line.partition('=')
        key = key.strip()
        value = value.strip()
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv()


def _env(key, default=''):
    return os.environ.get(key, default)


def _env_int(key, default):
    try:
        return int(_env(key, str(default)))
    except ValueError:
        return default


PORT = _env_int('PORT', 3000)
JWT_SECRET = _env('JWT_SECRET', 'realitysim-dev-secret-change-me')
JWT_EXPIRES = _env('JWT_EXPIRES', '7d')
DB_PATH = str(ROOT / _env('DB_PATH', 'data/realitysim.db'))
UPLOAD_DIR = str(ROOT / _env('UPLOAD_DIR', 'uploads'))
CORS_ORIGINS = [s.strip() for s in _env('CORS_ORIGIN', '*').split(',') if s.strip()]
PUBLIC_BASE_URL = _env('PUBLIC_BASE_URL', '').rstrip('/')

# 数据库模式：sqlite（本地文件，默认）/ turso（云端 SQLite 托管，免费 PaaS 部署用）
DB_TYPE = _env('DB_TYPE', 'sqlite').strip().lower()
# Turso 连接信息（DB_TYPE=turso 时必填）：libsql://xxx.turso.io 与 Auth Token
TURSO_DATABASE_URL = _env('TURSO_DATABASE_URL', '').strip()
TURSO_AUTH_TOKEN = _env('TURSO_AUTH_TOKEN', '').strip()

# 内置管理员
ADMIN_USERNAME = _env('ADMIN_USERNAME', 'admin')
ADMIN_PASSWORD = _env('ADMIN_PASSWORD', 'admin123456')
ADMIN_NICKNAME = _env('ADMIN_NICKNAME', '管理员')

# 开发模式：验证码直接随接口返回（生产环境务必关闭）
DEV_MODE = _env('DEV_MODE', 'true').lower() != 'false'

# 短信/邮件验证码发送通道（未配置时仅开发模式可返回验证码）
SMS_PROVIDER = _env('SMS_PROVIDER', '')
SMS_ACCESS_KEY_ID = _env('SMS_ACCESS_KEY_ID', '')
SMS_ACCESS_KEY_SECRET = _env('SMS_ACCESS_KEY_SECRET', '')
SMS_SIGN_NAME = _env('SMS_SIGN_NAME', '')
SMS_TEMPLATE_CODE = _env('SMS_TEMPLATE_CODE', '')
MAIL_HOST = _env('MAIL_HOST', '')
MAIL_PORT = _env_int('MAIL_PORT', 465)
MAIL_USER = _env('MAIL_USER', '')
MAIL_PASS = _env('MAIL_PASS', '')

# 第三方 OAuth（在对应开放平台申请后填入 .env，本地联调可用 OAUTH_DEV_MOCK）
OAUTH_DEV_MOCK = _env('OAUTH_DEV_MOCK', 'false').lower() == 'true'

_BASE = lambda: PUBLIC_BASE_URL or 'http://localhost:3000'  # noqa: E731

OAUTH = {
    'wechat': {
        'name': '微信',
        'clientId': _env('WECHAT_CLIENT_ID', ''),
        'clientSecret': _env('WECHAT_CLIENT_SECRET', ''),
        'redirectUri': _env('WECHAT_REDIRECT_URI', '') or _BASE() + '/api/auth/oauth/wechat/callback',
        'authorizeUrl': lambda c: f"https://open.weixin.qq.com/connect/qrconnect?appid={c['clientId']}&redirect_uri={_urlencode(c['redirectUri'])}&response_type=code&scope=snsapi_login&state=realitysim#wechat_redirect",
        'tokenUrl': 'https://api.weixin.qq.com/sns/oauth2/access_token',
        'userUrl': 'https://api.weixin.qq.com/sns/userinfo',
    },
    'qq': {
        'name': 'QQ',
        'clientId': _env('QQ_CLIENT_ID', ''),
        'clientSecret': _env('QQ_CLIENT_SECRET', ''),
        'redirectUri': _env('QQ_REDIRECT_URI', '') or _BASE() + '/api/auth/oauth/qq/callback',
        'authorizeUrl': lambda c: f"https://graph.qq.com/oauth2.0/authorize?response_type=code&client_id={c['clientId']}&redirect_uri={_urlencode(c['redirectUri'])}&scope=get_user_info&state=realitysim",
        'tokenUrl': 'https://graph.qq.com/oauth2.0/token',
        'userUrl': 'https://graph.qq.com/oauth2.0/me',
    },
    'douyin': {
        'name': '抖音',
        'clientId': _env('DOUYIN_CLIENT_KEY', ''),
        'clientSecret': _env('DOUYIN_CLIENT_SECRET', ''),
        'redirectUri': _env('DOUYIN_REDIRECT_URI', '') or _BASE() + '/api/auth/oauth/douyin/callback',
        'authorizeUrl': lambda c: f"https://open.douyin.com/platform/oauth/connect?client_key={c['clientId']}&response_type=code&scope=user_info&redirect_uri={_urlencode(c['redirectUri'])}&state=realitysim",
        'tokenUrl': 'https://open.douyin.com/oauth/access_token/',
        'userUrl': 'https://open.douyin.com/oauth/userinfo/',
    },
    'microsoft': {
        'name': '微软',
        'clientId': _env('MICROSOFT_CLIENT_ID', ''),
        'clientSecret': _env('MICROSOFT_CLIENT_SECRET', ''),
        'redirectUri': _env('MICROSOFT_REDIRECT_URI', '') or _BASE() + '/api/auth/oauth/microsoft/callback',
        'authorizeUrl': lambda c: f"https://login.microsoftonline.com/common/oauth2/v2.0/authorize?client_id={c['clientId']}&response_type=code&redirect_uri={_urlencode(c['redirectUri'])}&response_mode=query&scope=User.Read&state=realitysim",
        'tokenUrl': 'https://login.microsoftonline.com/common/oauth2/v2.0/token',
        'userUrl': 'https://graph.microsoft.com/v1.0/me',
    },
}


def _urlencode(s):
    from urllib.parse import quote
    return quote(s, safe='')
