"""项目入口：选后缀、建邮箱、自动打开登录链接（或接码）登录 Claude。

运行：
  uv run main.py                       选后缀 → 建邮箱 → 自动登录
  uv run main.py -d example.com        直接指定后缀
  uv run main.py -e you@example.com    复用指定邮箱
  uv run main.py --no-auto-login       只打印登录链接/验证码，不自动填
  uv run main.py --login-timeout 180   等待邮件超时秒数（0=跳过等待）

  旧参数名 --no-auto-code / --code-timeout 仍可用（别名，向后兼容）。
"""

from __future__ import annotations

import argparse

from claude_register.flow import run


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="选后缀、建 AnyMail 邮箱并自动打开登录链接（或接码）登录 Claude",
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
        "--no-auto-login",
        "--no-auto-code",
        dest="no_auto_login",
        action="store_true",
        help="收到登录链接/验证码只打印，不自动填入（--no-auto-code 为旧名别名）",
    )
    parser.add_argument(
        "--login-timeout",
        "--code-timeout",
        dest="login_timeout",
        type=float,
        default=120.0,
        metavar="SECONDS",
        help=(
            "等待登录链接/验证码的总预算秒数，默认 120；设 0 跳过等待"
            "（--code-timeout 为旧名别名）。"
            "验证码兜底轮询最多占这个预算的 25%%（且不超过 30s），"
            "剩余预算留给登录链接轮询，总等待时间不会超过这里设的值。"
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    run(
        email=args.email,
        domain=args.domain,
        auto_login=not args.no_auto_login,
        code_timeout=args.login_timeout,
    )


if __name__ == "__main__":
    main()

