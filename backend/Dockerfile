# 现实模拟后端 Docker 镜像
FROM node:24-alpine

WORKDIR /app

# 先装依赖（利用层缓存）
COPY package.json ./
RUN npm install --omit=dev

# 拷贝源码
COPY server.js ./
COPY src ./src

# 数据与上传目录（由 volume 挂载）
RUN mkdir -p /app/data /app/uploads

ENV PORT=3000
EXPOSE 3000

CMD ["node", "server.js"]
