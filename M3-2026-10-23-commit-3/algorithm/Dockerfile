# =============================================================================
# 拾光 · 算法服务镜像
# 构建：docker build -t shiguang-algorithm:1.0.0 -f algorithm/Dockerfile .
# =============================================================================

FROM python:3.11-slim

LABEL org.opencontainers.image.title="shiguang-algorithm" \
      org.opencontainers.image.description="拾光 · 多模态匹配与可信度评估算法服务" \
      org.opencontainers.image.version="1.0.0"

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app

COPY algorithm/requirements.txt /app/requirements.txt
# requirements.txt 只列可选依赖；本镜像需要真正的 ASGI 服务，故优先安装服务化依赖清单。
# 使用通配 COPY，使 requirements-service.txt 缺失时构建也不会失败。
COPY algorithm/requirements-service.tx[t] /app/
RUN if [ -f /app/requirements-service.txt ]; then \
        pip install --no-cache-dir -r /app/requirements-service.txt; \
    else \
        pip install --no-cache-dir -r /app/requirements.txt; \
    fi

COPY algorithm/ /app/

RUN useradd --create-home --uid 10002 algouser \
    && chown -R algouser:algouser /app
USER algouser

EXPOSE 9000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:9000/algorithm/v1/health', timeout=4).status==200 else 1)"

# 算法服务只在内网暴露，不映射到宿主机公网端口
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "9000", "--workers", "2"]
