FROM node:20-alpine AS ui-build
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends docker.io docker-cli openssl ca-certificates ffmpeg curl \
    && mkdir -p /usr/libexec/docker/cli-plugins \
    && curl -SL https://github.com/docker/compose/releases/download/v2.24.6/docker-compose-linux-x86_64 -o /usr/libexec/docker/cli-plugins/docker-compose \
    && chmod +x /usr/libexec/docker/cli-plugins/docker-compose \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock README.md ./
COPY orchestrator/ ./orchestrator/
COPY templates/ ./templates/
COPY ui/ ./ui/
COPY stack.yaml state.json ./
COPY frontend/dist ./frontend/dist

RUN pip install --no-cache-dir .

EXPOSE 8443
CMD ["uvicorn", "orchestrator.app:app", "--host", "0.0.0.0", "--port", "8443"]

