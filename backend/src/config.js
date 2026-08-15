// 配置加载：读取 .env（不存在则用默认值）
const path = require('path');
require('dotenv').config({ path: path.join(__dirname, '..', '.env') });

const ROOT = path.join(__dirname, '..');

module.exports = {
  ROOT,
  PORT: parseInt(process.env.PORT || '3000', 10),
  JWT_SECRET: process.env.JWT_SECRET || 'realitysim-dev-secret-change-me',
  JWT_EXPIRES: process.env.JWT_EXPIRES || '7d',
  DB_PATH: path.join(ROOT, process.env.DB_PATH || 'data/realitysim.db'),
  UPLOAD_DIR: path.join(ROOT, process.env.UPLOAD_DIR || 'uploads'),
  CORS_ORIGINS: (process.env.CORS_ORIGIN || '*')
    .split(',').map(s => s.trim()).filter(Boolean),
  PUBLIC_BASE_URL: (process.env.PUBLIC_BASE_URL || '').replace(/\/$/, ''),
  // 内置管理员
  ADMIN_USERNAME: process.env.ADMIN_USERNAME || 'admin',
  ADMIN_PASSWORD: process.env.ADMIN_PASSWORD || 'admin123456',
  ADMIN_NICKNAME: process.env.ADMIN_NICKNAME || '管理员',
};
