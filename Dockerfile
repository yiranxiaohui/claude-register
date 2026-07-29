# 1) 前端
FROM oven/bun:1 AS web
WORKDIR /web
COPY web/package.json ./
RUN bun install
COPY web/ ./
RUN bun run build

# 2) 运行镜像（含 Python + Xvfb + Camoufox）
FROM python:3.13-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    xvfb libgtk-3-0 libx11-xcb1 libasound2 libdbus-glib-1-2 && \
    rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY claude_register/ ./claude_register/
COPY server/ ./server/
COPY serve.py main.py ./
COPY --from=web /web/dist ./web/dist
RUN uv run camoufox fetch
EXPOSE 8790
CMD ["uv", "run", "python", "serve.py"]
