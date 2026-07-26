"""终端 I/O：日志、输入、醒目横幅。"""

from __future__ import annotations


def log(msg: str) -> None:
    print(msg, flush=True)


def prompt(msg: str) -> str:
    """读取一行输入；EOF 时返回空串（便于非交互环境）。"""
    try:
        return input(msg).strip()
    except EOFError:
        return ""


def banner(msg: str) -> None:
    """把关键信息（验证码、邮箱）打成醒目横幅，避免刷屏时被淹没。"""
    line = "=" * max(40, len(msg) + 4)
    print(f"\n{line}\n  {msg}\n{line}\n", flush=True)
