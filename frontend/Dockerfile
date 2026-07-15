# 一脉 Axon 前端镜像:多阶段构建,产物由 nginx 托管。
FROM node:22-slim AS build

WORKDIR /app

# 先装依赖利用层缓存。构建期出网依赖 compose 透传的 HTTP(S)_PROXY(内网服务器容器
# 无直连外网能力,DNS 亦不通);走国内镜像源(npmmirror)并放宽超时/重试兜底网络抖动。
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund \
      --registry=https://registry.npmmirror.com \
      --fetch-timeout=600000 --fetch-retries=5

COPY . .
RUN npm run build

FROM nginx:1.27-alpine AS runtime

# SPA 路由回退 + /api、/ws 反代到后端
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
