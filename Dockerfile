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
# KasmVNC：接管会话的 X 服务器 + Web 客户端 + websocket 推流（替代 x11vnc+noVNC）。
# deb 按基础镜像的 Debian 版本选（python:3.13-slim 现为 trixie）；升级基础镜像时同步换。
ARG KASMVNC_VERSION=1.5.0
ADD https://github.com/kasmtech/KasmVNC/releases/download/v${KASMVNC_VERSION}/kasmvncserver_trixie_${KASMVNC_VERSION}_amd64.deb /tmp/kasmvncserver.deb
RUN apt-get update && \
    apt-get install -y --no-install-recommends /tmp/kasmvncserver.deb && \
    rm -rf /var/lib/apt/lists/* /tmp/kasmvncserver.deb
RUN pip install --no-cache-dir uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
# 此时源码还没拷入，只装第三方依赖；项目本体（hatchling 构建需要 README.md/源码）留到下面装
RUN uv sync --frozen --no-dev --no-install-project
COPY claude_register/ ./claude_register/
COPY server/ ./server/
COPY serve.py main.py README.md ./
RUN uv sync --frozen --no-dev
COPY --from=web /web/dist ./web/dist
RUN uv run camoufox fetch
EXPOSE 8790
CMD ["uv", "run", "python", "serve.py"]
