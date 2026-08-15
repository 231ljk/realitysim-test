// 现实模拟 RealitySim 后端入口
const fs = require('fs');
const path = require('path');
const http = require('http');
const express = require('express');
const multer = require('multer');
const config = require('./src/config');
const { router: authRouter } = require('./src/auth');
const chatRouter = require('./src/chat');
const { setupWebSocket } = require('./src/ws');

// 初始化目录
fs.mkdirSync(config.UPLOAD_DIR, { recursive: true });
fs.mkdirSync(path.join(config.ROOT, 'data'), { recursive: true });

const app = express();
app.use(express.json({ limit: '5mb' }));

// CORS
app.use((req, res, next) => {
  const origin = req.headers.origin;
  if (origin && (config.CORS_ORIGINS.includes('*') || config.CORS_ORIGINS.includes(origin))) {
    res.setHeader('Access-Control-Allow-Origin', origin);
    res.setHeader('Access-Control-Allow-Credentials', 'true');
  }
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type, Authorization');
  res.setHeader('Access-Control-Allow-Methods', 'GET, POST, PUT, DELETE, OPTIONS');
  if (req.method === 'OPTIONS') return res.sendStatus(204);
  next();
});

// 健康检查
app.get('/api/health', (req, res) => res.json({ ok: true, name: 'realitysim-backend', time: new Date().toISOString() }));

// 认证
app.use('/api/auth', authRouter);

// 聊天
app.use('/api/chat', chatRouter);

// 上传（图片 / 语音）
const upload = multer({
  storage: multer.diskStorage({
    destination: (req, file, cb) => cb(null, config.UPLOAD_DIR),
    filename: (req, file, cb) => {
      const ext = (path.extname(file.originalname) || '.bin').toLowerCase();
      cb(null, `${Date.now()}-${Math.round(Math.random() * 1e9)}${ext}`);
    },
  }),
  limits: { fileSize: 20 * 1024 * 1024 }, // 20MB
});
app.post('/api/upload', upload.single('file'), (req, res) => {
  if (!req.file) return res.status(400).json({ error: '未收到文件' });
  const base = config.PUBLIC_BASE_URL || `http://${req.headers.host}`;
  res.json({ url: `${base}/uploads/${req.file.filename}` });
});

// 静态上传文件
app.use('/uploads', express.static(config.UPLOAD_DIR));

// 404
app.use((req, res) => res.status(404).json({ error: 'not found' }));

const server = http.createServer(app);
setupWebSocket(server);

server.listen(config.PORT, () => {
  console.log(`[realitysim-backend] listening on http://0.0.0.0:${config.PORT}`);
});
