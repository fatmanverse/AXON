# 一脉 Axon 后端镜像。uv 装依赖 + uvicorn 起服务。
FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# uv:官方独立二进制,装依赖快且可复现(锁文件驱动)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# 先只拷依赖清单,利用镜像层缓存:代码变动不触发重装依赖
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# 再拷业务代码与迁移
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./

ENV PATH="/app/.venv/bin:${PATH}"

EXPOSE 8000

# 默认起 API;worker/beat/flower 由 compose 覆写 command
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
