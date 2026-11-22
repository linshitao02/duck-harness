# 在 Ubuntu 上构建并运行网页服务。数据库默认是容器里的 SQLite。
FROM node:22-bookworm-slim AS frontend
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HARNESS_SQLITE_PATH=/data/harness.sqlite \
    HARNESS_UPLOAD_DIR=/data/uploads \
    HARNESS_WORKSPACE=/workspace \
    DATABASE_URL=

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl gnupg git \
    && curl -fsSL https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor -o /usr/share/keyrings/microsoft.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/microsoft.gpg] https://packages.microsoft.com/debian/12/prod bookworm main" > /etc/apt/sources.list.d/microsoft.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends powershell \
    && ln -sf "$(command -v pwsh)" /usr/local/bin/powershell \
    && ln -sf "$(command -v pwsh)" /usr/local/bin/powershell.exe \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY backend/requirements.txt /tmp/requirements.txt
RUN grep -Ev '^(pywebview|pytest)' /tmp/requirements.txt > /tmp/runtime-requirements.txt \
    && pip install --no-cache-dir -r /tmp/runtime-requirements.txt \
    && rm /tmp/requirements.txt /tmp/runtime-requirements.txt

COPY backend /app/backend
COPY --from=frontend /src/frontend/dist /app/frontend/dist
COPY docker/entrypoint.sh /entrypoint.sh
RUN sed -i 's/\r$//' /entrypoint.sh \
    && chmod +x /entrypoint.sh \
    && useradd --create-home --uid 1000 harness \
    && mkdir -p /data/uploads /workspace

WORKDIR /app/backend
EXPOSE 8787
ENTRYPOINT ["/entrypoint.sh"]
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8787"]
