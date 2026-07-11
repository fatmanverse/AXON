# 一脉 Axon 前端镜像:多阶段构建,产物由 nginx 托管。
FROM node:22-slim AS build

WORKDIR /app

# 先装依赖利用层缓存
COPY package.json package-lock.json ./
RUN npm ci

COPY . .
RUN npm run build

FROM nginx:1.27-alpine AS runtime

# SPA 路由回退 + /api、/ws 反代到后端
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
