# =============================================================================
# 拾光 · 后端镜像（多阶段构建）
# 构建：docker build -t shiguang-backend:1.0.0 -f Dockerfile .
# 说明：构建上下文为仓库根目录（需要同时访问 backend/ 与 scripts/）
# =============================================================================

# ---------- 阶段 1：构建依赖 ----------
FROM python:3.11-slim AS builder

WORKDIR /build

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

# 编译 psycopg/mysqlclient 等可能需要编译器；PyMySQL 为纯 Python，保留最小依赖
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       gcc \
       default-libmysqlclient-dev \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt .
RUN pip install --prefix=/install -r requirements.txt


# ---------- 阶段 2：运行镜像 ----------
FROM python:3.11-slim AS runtime

LABEL org.opencontainers.image.title="shiguang-backend" \
      org.opencontainers.image.description="拾光 · 校园多模态失物检索与可信认领平台 - 后端 API" \
      org.opencontainers.image.version="1.0.0" \
      org.opencontainers.image.licenses="Proprietary"

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 appuser

COPY --from=builder /install /usr/local

WORKDIR /app

# 应用代码（构建上下文为仓库根目录）
COPY --chown=appuser:appuser backend/ /app/backend/
COPY --chown=appuser:appuser algorithm/config/ /app/algorithm/config/
COPY --chown=appuser:appuser scripts/ /app/scripts/

WORKDIR /app/backend

# 非 root 运行
USER appuser

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/backend

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/health/live || exit 1

# 生产：gunicorn + uvicorn worker
# 本地/演示可用：uvicorn app.main:app --host 0.0.0.0 --port 8000
CMD ["gunicorn", "app.main:app", \
     "--worker-class", "uvicorn.workers.UvicornWorker", \
     "--workers", "2", \
     "--bind", "0.0.0.0:8000", \
     "--timeout", "60", \
     "--graceful-timeout", "30", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]
