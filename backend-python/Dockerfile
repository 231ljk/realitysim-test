# RealitySim 后端 - Zeabur / 通用容器部署镜像
# 基础镜像：Python 3.11 slim（体积小，turso 走 HTTP API 无需额外系统库）
FROM python:3.11-slim

WORKDIR /app

# 先装依赖，利用 Docker 层缓存
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 拷贝后端代码（.dockerignore 已排除 .env / data / uploads / 缓存）
COPY . .

# 容器内默认端口（Zeabur 会注入 PORT 环境变量并覆盖）
EXPOSE 3000

# gthread worker 支持 flask-sock WebSocket 长连接；
# 端口取 $PORT（Zeabur 注入），未注入时回退 3000
CMD ["sh", "-c", "gunicorn server:app --worker-class gthread --bind 0.0.0.0:${PORT:-3000} --workers 2 --threads 8 --timeout 120"]
