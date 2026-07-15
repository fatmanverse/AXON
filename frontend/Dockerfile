# 一脉 Axon 前端镜像:多阶段构建,产物由 nginx 托管。
FROM node:22-slim AS build

WORKDIR /app

# 先装依赖利用层缓存。构建容器内无宿主机代理,直连 npm 官方源会超时,
# 故走国内镜像源(npmmirror)并放宽超时/重试,避免偶发网络抖动导致 npm 崩溃。
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
