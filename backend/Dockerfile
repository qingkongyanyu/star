# 11-Agent 中台 · 后端镜像（云部署/本地 docker 均适用；本地裸机跑不受影响）
# 构建上下文 = 项目根（含 pyproject.toml、backend/alembic）
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv
COPY pyproject.toml ./
COPY backend backend

# 中文字体：报告图表（matplotlib）需要 CJK 字体，否则中文标签会变成方块
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

# 安装应用（pip install . → site-packages 里出现 app 包；backend/ 内仍保留 alembic 与源码）
RUN pip install --upgrade pip && pip install .

WORKDIR /srv/backend

EXPOSE 8070
HEALTHCHECK --interval=30s --timeout=5s --start-period=25s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8070/healthz', timeout=4)" || exit 1

# 启动前自动执行 DB 迁移（task_repo + artifact_repo），然后跑 uvicorn
CMD ["sh", "-c", "alembic upgrade head && exec python -m uvicorn app.main:app --host 0.0.0.0 --port 8070"]
