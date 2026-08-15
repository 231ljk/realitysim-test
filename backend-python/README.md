# 现实模拟 RealitySim 后端（Python 版）

原 Node.js (Express + ws + node:sqlite) 后端的 **Python 等价实现**，接口与数据结构完全兼容，前端 index.html 无需改动即可对接。

## 技术栈

- Flask（REST API）
- flask-sock（WebSocket，/ws/chat）
- sqlite3（标准库，兼容原 node:sqlite 建的表）
- PyJWT（登录态，与 Node 版 jsonwebtoken 格式一致）
- 密码哈希：crypto.scrypt，与 Node 版 crypto.scryptSync 结果完全一致（旧账号可直接登录）

## 目录结构

```
realitysim-backend-py/
├── server.py            # 入口：Flask app + 蓝图注册 + WebSocket + 上传
├── requirements.txt     # 依赖
├── install_deps.py      # 一键安装依赖（Windows）
├── .env                 # 配置（复用原 Node 版 .env，自动读取）
├── data/realitysim.db   # SQLite 数据库（复用原库，含老用户）
└── src/
    ├── __init__.py
    ├── config.py        # .env 加载与配置
    ├── db.py            # 连接管理 + 建表 + 旧库迁移
    ├── auth.py          # 注册/登录/验证码/OAuth/管理员用户管理
    ├── admin.py         # 仪表盘/日志/批量操作/公告/敏感词/设置/导出
    ├── chat.py          # 会话/消息 + WebSocket 实时聊天
    ├── posts.py         # 社区帖子/点赞/评论
    └── game.py          # 游戏客户端对接（playtime/heartbeat）
```

## 启动

```bash
cd realitysim-backend-py
python install_deps.py   # 首次：安装 flask flask-sock PyJWT flask-cors
python server.py         # 默认 http://localhost:3000（.env PORT 可覆盖）
```

启动后访问 http://localhost:3000/ 返回 `{name: "RealitySim Backend (Python)", status: "ok"}`。

## 接口清单（与原 Node 版一致）

| 模块 | 接口 |
| --- | --- |
| 认证 | POST /api/auth/register、/login、/send-code、/login-code、/bind；GET /api/auth/me、/oauth/\<provider\>/url、/callback、/mock |
| 聊天 | GET/POST /api/chat/conversations、GET/POST /api/chat/conversations/:id/messages；WS /ws/chat |
| 帖子 | GET/POST /api/posts、GET /api/posts/:id、POST /api/posts/:id/like、/comments、DELETE /api/posts/:id |
| 游戏 | GET /api/game/me、POST /api/game/playtime、/heartbeat、/logout |
| 公开 | GET /api/users、/api/health、/api/public/announcements、/api/public/settings |
| 管理 | GET /api/admin/stats、/users、/login-logs、/audit-logs、/settings、/export/users 等 |
| 上传 | POST /api/upload；GET /uploads/\<filename\> |

## 与 Node 版的差异说明

1. **数据库**：直接用原 `data/realitysim.db`，自动建缺失表与迁移列（解除 username NOT NULL 等），无需重建数据。
2. **密码**：scrypt 参数 n=16384, r=8, p=1, dklen=64 与 Node 默认一致，老密码可登录；新注册继续用相同格式 `salt:hash`。
3. **JWT**：PyJWT 生成，算法 HS256，secret 与过期时间取自 .env（JWT_SECRET / JWT_EXPIRES），前端无需改动。
4. **OAuth**：微信/QQ/抖音/微软回调逻辑已实现；未配置应用时提供开发模拟页（OAUTH_DEV_MOCK=true 时访问 /api/auth/oauth/\<provider\>/mock）。
5. **验证码**：开发模式（DEV_MODE=true）下验证码直接随 /send-code 返回 devCode；生产接入短信/邮件通道处留有接入点。
6. **WebSocket**：首帧必须发送 `token` 字符串完成鉴权，随后发送 `{"action":"message","conversationId":1,"content":"hi"}` 即可发消息。

## 前端切换

index.html 的 `API_CONFIG.BASE_URL` 已指向 `http://localhost:3000`，直接启动 Python 后端即可替代 Node 版，无需改动前端代码。
