"""session 層的例外（從 sessions.py 拆出，讓 credentials / provision / run_kwargs 不必回頭 import sessions）。"""

from __future__ import annotations


class SessionError(RuntimeError):
    pass


class SessionNotFound(SessionError):
    """找不到 session，或請求者無權存取（兩者刻意回同一種錯，不洩漏存在性）。"""
