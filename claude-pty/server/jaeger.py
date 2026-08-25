"""Jaeger 可達性探測（從 sessions.py 拆出；preflight 與 SessionManager.create 都用）。"""

from __future__ import annotations

import socket

from . import config


def _jaeger_reachable() -> bool:
    """OTEL_ENDPOINT 的 host:port 此刻連得上嗎（TCP connect）。

    只回答「連得上」——連得上不保證 collector 健康，但**連不上**幾乎一定代表 trace
    送出去會石沉大海（OTLP 是 fail-open，claude 不會報錯）。控制平面據此決定：
      · 連得上  → 照設 OTEL env，session 真的送 trace
      · 連不上  → **不設** OTEL env（不送去一個沒人接的地方），但 session 照開
    這不是 fail-closed：telemetry 是觀察不是控制，不能為了它讓人開不了場。

    ⚠ 回傳只影響「送不送 + 座標記什麼」，不影響 session 起不起得來。任何例外都當
      「連不上」——探測本身壞掉不該比 jaeger 不在更嚴重。
    """
    from urllib.parse import urlparse

    try:
        u = urlparse(config.OTEL_ENDPOINT)
        host, port = u.hostname, u.port or 4317
        if not host:
            return False
        with socket.create_connection((host, port), timeout=config.JAEGER_PROBE_TIMEOUT):
            return True
    except Exception:  # noqa: BLE001 — 探測壞掉＝當成連不上，見 docstring
        return False
