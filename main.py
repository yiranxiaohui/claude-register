"""项目入口：选后缀、建邮箱、自动接码并填入 Claude 登录页。

运行：
  uv run main.py                       选后缀 → 建邮箱 → 自动接码登录
  uv run main.py -d example.com        直接指定后缀
  uv run main.py -e you@example.com    复用指定邮箱
  uv run main.py --no-auto-code        只打印验证码，不自动填
  uv run main.py --code-timeout 180    接码超时秒数（0=跳过接码）
"""

from __future__ import annotations

import argparse

from claude_register.flow import run


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="选后缀、建 AnyMail 邮箱并自动接码填入 Claude 登录页",
    )
    parser.add_argument(
        "--email",
        "-e",
        help="复用该邮箱（已存在则复用，不存在则创建）；与 --domain 同给时本项优先",
    )
    parser.add_argument(
        "--domain",
        "-d",
        help="新建邮箱用的后缀域名（也可设 ANYMAIL_DOMAIN）",
    )
    parser.add_argument(
        "--no-auto-code",
        action="store_true",
        help="接到验证码只打印，不自动填入",
    )
    parser.add_argument(
        "--code-timeout",
        type=float,
        default=120.0,
        metavar="SECONDS",
        help="接码超时秒数，默认 120；设 0 跳过接码",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    run(
        email=args.email,
        domain=args.domain,
        auto_code=not args.no_auto_code,
        code_timeout=args.code_timeout,
    )


if __name__ == "__main__":
    main()
