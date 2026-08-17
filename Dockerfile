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
    nginx \
    supervisor \
    ca-certificates && \
    rm -rf /var/lib/apt/lists/*
# Xpra LTS：虚拟 X 桌面 + WebSocket/HTML5 客户端。固定 HTML5 5.6，避免较新
# 16.x 客户端在慢网络下的重连回归，并保留双向 Clipboard API 同步。
# 官方仓库签名 Key 指纹：B499 3B57 3231 48E3 7977 E5D8 7325 4CAD 1797 8FAF。
ARG XPRA_VERSION=5.1.6-r0-1
ARG XPRA_HTML5_VERSION=5.6-r14-1
ADD https://xpra.org/xpra.asc /usr/share/keyrings/xpra.asc
RUN chmod 0644 /usr/share/keyrings/xpra.asc
COPY deploy/xpra-lts.sources /etc/apt/sources.list.d/xpra-lts.sources
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      xpra-server=${XPRA_VERSION} \
      xpra-x11=${XPRA_VERSION} \
      xpra-codecs=${XPRA_VERSION} \
      xpra-html5=${XPRA_HTML5_VERSION} && \
    rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
# 此时源码还没拷入，只装第三方依赖；项目本体（hatchling 构建需要 README.md/源码）留到下面装
RUN uv sync --frozen --no-dev --no-install-project
COPY claude_register/ ./claude_register/
COPY server/ ./server/
COPY serve.py main.py README.md ./
COPY deploy/nginx.conf /etc/nginx/nginx.conf
COPY deploy/supervisord.conf /etc/supervisor/conf.d/claude-register.conf
RUN nginx -t
RUN uv sync --frozen --no-dev
COPY --from=web /web/dist ./web/dist
RUN uv run camoufox fetch
ENV CLAUDE_REGISTER_INTERNAL_PORT=8791
EXPOSE 8790
CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/conf.d/claude-register.conf"]
