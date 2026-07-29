# 1) 前端
FROM oven/bun:1 AS web
WORKDIR /web
COPY web/package.json ./
RUN bun install
COPY web/ ./
RUN bun run build

# 2) 运行镜像（含 Python + Xvfb + Camoufox）
FROM python:3.13-slim
# Camoufox 自带 Firefox 二进制，但仍动态链接标准 Firefox 系统库；slim 镜像里没有这些库，
# 缺一个都要到容器运行时才暴露。下面是 Firefox 运行时依赖的规范集合（含 Xvfb 虚拟显示）。
# 注意：较新的 Debian 基础镜像（trixie/13 起）ALSA 包可能改名为 libasound2t64，若构建报
# 找不到 libasound2，把它换成 libasound2t64 即可。
RUN apt-get update && apt-get install -y --no-install-recommends \
    xvfb \
    libgtk-3-0 \
    libx11-xcb1 \
    libasound2 \
    libdbus-glib-1-2 \
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libpango-1.0-0 \
    libcairo2 \
    libxshmfence1 \
    libxext6 \
    libxrender1 \
    libxtst6 \
    libxi6 \
    fonts-liberation \
    fonts-unifont \
    ca-certificates && \
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
