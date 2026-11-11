# 1) 前端
FROM oven/bun:1 AS web
WORKDIR /web
COPY web/package.json ./
RUN bun install
COPY web/ ./
RUN bun run build

# 2) 运行镜像（含 Python + Xvfb + Playwright Chromium）
FROM python:3.13-slim
# Chromium 自身的系统依赖由下面的 `playwright install --with-deps` 按官方清单安装；
# 这里只装 Xvfb（注册/接管的虚拟显示）、字体和面板运行所需的服务。
RUN apt-get update && apt-get install -y --no-install-recommends \
    xvfb \
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
# Playwright 自带的 Chromium（完整版，不装 headless shell：注册与接管都以有头模式
# 挂在 Xvfb 上）。版本由 uv.lock 锁定的 playwright 决定，放在拷源码之前以复用缓存。
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright
RUN uv run --no-sync playwright install --with-deps --no-shell chromium && \
    rm -rf /var/lib/apt/lists/*
COPY claude_register/ ./claude_register/
COPY server/ ./server/
COPY serve.py main.py README.md ./
COPY deploy/nginx.conf /etc/nginx/nginx.conf
COPY deploy/supervisord.conf /etc/supervisor/conf.d/claude-register.conf
RUN nginx -t
RUN uv sync --frozen --no-dev
COPY --from=web /web/dist ./web/dist
ENV CLAUDE_REGISTER_INTERNAL_PORT=8791
EXPOSE 8790
CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/conf.d/claude-register.conf"]
