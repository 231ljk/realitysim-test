# 现实模拟 RealitySim 后端

官方后端服务：账号认证 + 聊天通信（REST + WebSocket）。代码托管于 GitHub，可一键 Docker 部署到任意服务器（含老电脑 Linux + 宝塔方案）。

## 功能

- 账号注册 / 登录（JWT，7 天有效）
- 会话列表、历史消息拉取（增量 `after` 游标）
- 文字 / 图片 / 语音消息（REST 发送 + WebSocket 实时推送）
- 图片 / 语音文件上传（20MB 上限，静态托管 `/uploads/`）
- SQLite 存储（Node 内置 `node:sqlite`，零编译依赖）
- 预留 Casdoor 账号系统对接位（`src/auth.js`）

## 本地运行

```bash
npm install
cp .env.example .env   # Windows 为: copy .env.example .env
npm start
```

服务默认监听 `http://localhost:3000`，健康检查 `GET /api/health`。

## API 一览

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/auth/register` | 注册 `{username, password, nickname, avatar?}` → `{token, user}` |
| POST | `/api/auth/login` | 登录 `{username, password}` → `{token, user}` |
| GET | `/api/auth/me` | 当前用户信息（Bearer Token） |
| POST | `/api/chat/conversations` | 创建会话 `{memberIds: [其他用户id数组]}` |
| GET | `/api/chat/conversations` | 我的会话列表（含成员与最后一条消息） |
| GET | `/api/chat/conversations/:id/messages?after=N` | 历史消息增量拉取 |
| POST | `/api/chat/conversations/:id/messages` | 发送消息 `{type: text\|image\|voice, content, duration?}` |
| POST | `/api/upload` | 上传文件 `multipart/form-data, 字段名 file` → `{url}` |
| WS | `/ws?token=xxx` | WebSocket 实时消息 |

### WebSocket 消息协议

客户端连接后先收到 `{type:"hello", user}`；发送消息：

```json
{"type":"message","conversationId":1,"msgType":"text","content":"你好"}
```

服务端回执 `{type:"ack", message}`，并广播 `{type:"message", message}` 给会话内其他在线成员。

## Docker 部署（宝塔 / 任意服务器）

```bash
git clone git@github.com:231ljk/realitysim-test.git
cd realitysim-test/backend
cp .env.example .env          # 务必修改 JWT_SECRET
docker compose up -d
```

- 服务端口：`3000`（宝塔面板放行后，可反代绑定域名 `api.xianshimoni.com`）
- 数据持久化：`./data/realitysim.db`、`./uploads/`（已挂载 volume）

## 前端对接

前端 `index.html` 中设置：

```js
const API_CONFIG = {
  BASE_URL: 'https://api.xianshimoni.com',      // 或 http://服务器IP:3000
  WS_URL: 'wss://api.xianshimoni.com/ws',       // 或 ws://服务器IP:3000/ws
};
// useMock 切为 false 即走真实后端
```

## 目录结构

```
backend/
├── server.js            # 入口：Express + WebSocket
├── src/
│   ├── config.js        # 环境变量配置
│   ├── db.js            # SQLite 建表与连接
│   ├── auth.js          # 注册/登录/JWT
│   ├── chat.js          # 会话与消息 REST
│   └── ws.js            # WebSocket 实时推送
├── Dockerfile
├── docker-compose.yml
└── .env.example
```

## Roadmap

- [ ] Casdoor 账号体系对接（登录互通）
- [ ] 好友 / 群组管理
- [ ] 未读数与已读回执
- [ ] 消息持久化迁移 PostgreSQL（可选）
