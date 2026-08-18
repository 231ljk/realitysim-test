# RealitySim 后端 - Zeabur 免费版部署指南

> 适用：后端 `backend-python`（Python Flask + flask-sock + gunicorn），数据库用 Turso 云端托管。
> 目标：后端不依赖本机、面向全国用户常驻运行，零成本。

## 架构

```
GitHub 仓库 realitysim-test
        │  (Zeabur 关联仓库，检测到 backend-python/Dockerfile)
        ▼
Zeabur 免费项目（Docker 容器，gunicorn 监听 $PORT）
        │  DB_TYPE=turso ──► Turso 云端数据库（libsql，免费 9GB）
        ▼
https://<项目名>.zeabur.app  （后端公网域名）
```

## 一、前置准备

1. **GitHub 仓库**：`realitysim-test` 已推送（含 `backend-python/Dockerfile`、`.dockerignore`）。
2. **Turso 数据库**（已建）：
   - URL：`libsql://1-231ljk.aws-ap-northeast-1.turso.io`
   - Token：在 Turso 控制台 `Tokens` 页面获取
3. **Zeabur 账号**：https://zeabur.com 注册（支持 GitHub 登录）。

## 二、部署步骤（浏览器操作）

1. 打开 https://zeabur.com ，登录后进入 Dashboard。
2. 点击 **新建项目**（免费版默认可用）。
3. 选择 **关联 GitHub 仓库** → 授权 Zeabur 访问 GitHub → 选择 `realitysim-test`。
4. 仓库检测到 `backend-python/Dockerfile`，选择 **backend-python 目录** 部署。
5. 等待首次构建（自动 `pip install` + 启动 gunicorn），点服务卡片查看日志，出现
   `Booting worker with pid ...` 即启动成功。
6. **设置环境变量**（服务 → Variables）：
   - `PORT`：可留空（Zeabur 自动注入，Dockerfile 已兼容）
   - `DB_TYPE=turso`
   - `TURSO_DATABASE_URL=libsql://1-231ljk.aws-ap-northeast-1.turso.io`
   - `TURSO_AUTH_TOKEN=<你的 Turso Token>`
   - `JWT_SECRET=<随机长字符串>`
   - `DEV_MODE=false`（正式模式：图形验证码必过，短信/邮件需另行配置）
   - `ADMIN_USERNAME=admin`
   - `ADMIN_PASSWORD=<强密码>`
   - `CORS_ORIGIN=*`（前端 GitHub Pages 访问时允许跨域）
   - `PUBLIC_BASE_URL=https://<你的服务域名>`（部署后拿到域名再填，用于 OAuth 回调）
7. **绑定公网域名**：服务 → Networking → 绑定域名，Zeabur 会分配
   `https://<服务名>.zeabur.app`；填回 `PUBLIC_BASE_URL` 后重启服务。
8. **验证**：
   - 浏览器访问 `https://<服务名>.zeabur.app/api/health` → `{"status":"ok"}`
   - 浏览器访问 `https://<服务名>.zeabur.app/` → 后端欢迎 JSON

## 三、前端切换

修改仓库根 `index.html` 的 `API_CONFIG`：

```js
const API_CONFIG = {
  BASE_URL: 'https://<服务名>.zeabur.app',
  useMock: false,
  WS_URL: 'wss://<服务名>.zeabur.app/ws/chat'
};
```

提交推送后，GitHub Pages 的在线页面即接入云端后端。

## 四、已知边界

| 项目 | 说明 |
|------|------|
| WebSocket | gunicorn 使用 gthread worker 支持 flask-sock 长连接；Zeabur 开放 80/443，WS 升级链路正常 |
| 文件上传 `uploads/` | 容器磁盘重启会丢，正式运营需接对象存储（腾讯云 COS / R2 等） |
| 免费额度 | Zeabur 免费版有每月运行时长限制，超时服务会休眠，重新访问可唤醒 |
| 国内访问 | Zeabur 大陆直连一般可用；若个别网络环境不稳定，可再绑定自定义域名 |

## 五、环境变量总表

| 变量 | 必填 | 说明 |
|------|------|------|
| `DB_TYPE` | 是 | `turso` |
| `TURSO_DATABASE_URL` | 是 | Turso 数据库 URL |
| `TURSO_AUTH_TOKEN` | 是 | Turso 认证 Token |
| `JWT_SECRET` | 是 | 登录令牌签名密钥，随机长串 |
| `DEV_MODE` | 是 | `false` |
| `ADMIN_PASSWORD` | 是 | 内置管理员密码，强密码 |
| `ADMIN_USERNAME` | 否 | 默认 `admin` |
| `PUBLIC_BASE_URL` | 是 | 公网域名，OAuth/验证码回调用 |
| `CORS_ORIGIN` | 否 | 默认 `*` |
| `PORT` | 否 | Zeabur 自动注入 |
