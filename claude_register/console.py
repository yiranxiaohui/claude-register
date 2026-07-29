"""终端 I/O：日志、输入、醒目横幅。Web 模式经 contextvar sink 捕获。"""
from __future__ import annotations

import contextvars
from collections.abc import Callable

_sink: contextvars.ContextVar[Callable[[str], None] | None] = contextvars.ContextVar(
    "console_sink", default=None
)


def set_sink(fn: Callable[[str], None] | None) -> contextvars.Token:
    return _sink.set(fn)


def reset_sink(token: contextvars.Token) -> None:
    _sink.reset(token)


def log(msg: str) -> None:
    sink = _sink.get()
    if sink is not None:
        sink(msg)
    else:
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
    text = f"\n{line}\n  {msg}\n{line}\n"
    sink = _sink.get()
    if sink is not None:
        sink(text)
    else:
        print(text, flush=True)
