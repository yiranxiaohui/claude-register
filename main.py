"""项目入口：选择 AnyMail 邮箱并自动填入 Claude 登录页。

运行：
  uv run main.py
  uv run main.py --email you@example.com
  uv run main.py --new
  uv run main.py --domain example.com --new
"""

from __future__ import annotations

import argparse

from claude_register.flow import run


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="选择 AnyMail 邮箱并自动填入 Claude 登录页",
    )
    parser.add_argument(
        "--email",
        "-e",
        help="直接使用该邮箱（已存在则复用，不存在则创建）",
    )
    parser.add_argument(
        "--domain",
        "-d",
        help="新建邮箱时使用的域名（也可设 ANYMAIL_DOMAIN）",
    )
    parser.add_argument(
        "--new",
        action="store_true",
        help="跳过列表，直接新建自定义邮箱",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    run(email=args.email, domain=args.domain, create_new=args.new)


if __name__ == "__main__":
    main()
