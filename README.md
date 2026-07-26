# claude-register

打开 Claude 登录页，通过 AnyMail 选择/创建邮箱并自动填入。

## 准备

1. 复制 `.env.example` 为 `.env`，填写 AnyMail 配置
2. 安装依赖：

```text
uv sync
uv run playwright install chromium
```

## 启动

交互选择已有邮箱，或新建：

```text
uv run main.py
```

指定邮箱：

```text
uv run main.py --email you@example.com
```

新建自定义邮箱：

```text
uv run main.py --new
uv run main.py --new --domain example.com
```
