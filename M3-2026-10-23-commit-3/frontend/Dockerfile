# =============================================================================
# 拾光 · 前端镜像（构建 Vue 产物 + Nginx 托管与反向代理）
# 构建上下文为仓库根目录：docker build -f frontend/Dockerfile .
# =============================================================================

# ---------- 阶段 1：构建前端静态资源 ----------
FROM node:20-alpine AS build

WORKDIR /app

# 先复制依赖清单，利用 Docker 层缓存加速重复构建
COPY frontend/package*.json ./
RUN npm ci --no-audit --no-fund

COPY frontend/ ./

# 构建期注入 API 前缀（Vite 要求 VITE_ 前缀变量在构建期确定）
ARG VITE_API_BASE_URL=/api/v1
ARG VITE_APP_TITLE=拾光
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL \
    VITE_APP_TITLE=$VITE_APP_TITLE

RUN npm run build


# ---------- 阶段 2：Nginx 托管 ----------
FROM nginx:1.27-alpine AS runtime

LABEL org.opencontainers.image.title="shiguang-frontend" \
      org.opencontainers.image.description="拾光 · 前端 Web 应用" \
      org.opencontainers.image.version="1.0.0"

RUN rm -rf /usr/share/nginx/html/*

COPY --from=build /app/dist /usr/share/nginx/html
COPY deploy/nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD wget -qO- http://localhost/ >/dev/null 2>&1 || exit 1

CMD ["nginx", "-g", "daemon off;"]
