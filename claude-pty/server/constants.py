"""sessions 層共用常數（從 sessions.py 拆出，讓 run_kwargs / reconciler 不必 import sessions）。"""

from __future__ import annotations


# entrypoint.sh 在 exec driver 前印出的標記（⚠ SYNC：dev-container/entrypoint.sh）。
# 有它就代表前置（選單/firewall/mitm）全數完成、driver 正要啟動——比辨識 CLI banner
# 可靠得多（banner 會隨版本改）。
DRIVER_MARKER = "__NCR_DRIVER_STARTING__"

# container 視為「session 仍在」的狀態；exited/dead/removing 視為結束。
ALIVE_STATES = frozenset({"running", "restarting", "paused", "created"})
