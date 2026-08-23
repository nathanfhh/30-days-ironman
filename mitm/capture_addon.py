"""mitmproxy addon：把每條 flow 的**脫敏副本**寫成一顆 .mitm。

姿態只有一句：**不確定就不落地。**

- 寫的是 `flow.copy()` 的脫敏版，活的 flow 一個位元組都不動——動了的話，
  Claude 收到的回應就會變成 `<redacted>`。
- 脫敏或寫檔過程中出任何例外，那條 flow 直接丟掉，**絕不改寫成未脫敏版落地**。
- host 不在清單裡的不收。**這支 addon 自己的預設清單是 `api.anthropic.com`**，
  但 dev-container 的 entrypoint 在使用者選了要錄之後傳的是**空字串（全錄）**——
  兩個「預設」講的是不同層，別把它們當同一件事。

只處理 HTTP（含 SSE）。Claude Code 跟模型之間走的是 SSE，這裡就只做 SSE：
WebSocket 的紀錄要等連線關閉才寫得出完整的一筆，那條路徑上「即時」跟「脫敏」
不能兩全，而這個工具用不到它——所以不做，遇到就跳過並留一行紀錄。

載入方式（**不要**跟 `-w` 併用：內建的 save addon 排在前面，會先把未脫敏的
原始 flow 寫出去，那就沒有意義了）：

    mitmweb -q --listen-host 127.0.0.1 --listen-port 8880 \\
        --set store_streamed_bodies=true \\
        -s mitm/capture_addon.py \\
        --set capture_out=/path/flows-<時間>.mitm \\
        --set capture_hosts=api.anthropic.com

`store_streamed_bodies=true` 是必要的：沒有它，SSE 的 body 在 response hook
的當下還沒就位，錄出來的回應會是空的。

產出就是一顆正常的 .mitm，mitmweb -r 跟 mitm/wire_report.py 都讀得動。
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mitmproxy import ctx, io
from redact import redact_flow

DEFAULT_HOSTS = "api.anthropic.com"


class RedactedCapture:
    def __init__(self) -> None:
        self._fh = None
        self._writer = None
        self.written = 0
        self.dropped = 0
        self.skipped_ws = 0
        self.errored = 0

    def load(self, loader) -> None:
        loader.add_option(
            "capture_out",
            str,
            "",
            "寫出脫敏 .mitm 的路徑（必填；空字串＝不錄）。",
        )
        loader.add_option(
            "capture_hosts",
            str,
            DEFAULT_HOSTS,
            "只錄 request host 落在這份逗號分隔清單裡的 flow（空字串＝全部）。",
        )

    def running(self) -> None:
        path = ctx.options.capture_out
        if not path:
            ctx.log.error("[capture] 沒有給 capture_out——本場不會錄到任何東西。")
            return
        # 'wb'：entrypoint 每一場給一個帶時間戳的新路徑，不跨場 append。
        # 不能用 with：這個 handle 要活過整場錄製，由 done() 關閉。
        self._fh = open(path, "wb")  # noqa: SIM115 - 生命週期跨越 running()→done()
        self._writer = io.FlowWriter(self._fh)
        ctx.log.info(f"[capture] 脫敏後寫入 {path}")

    def response(self, flow) -> None:
        if getattr(flow, "websocket", None) is not None or (
            flow.response is not None and flow.response.status_code == 101
        ):
            # WebSocket 升級。不收，理由見檔頭。
            self.skipped_ws += 1
            return
        self._save(flow)

    def error(self, flow) -> None:
        """連線中途壞掉的 flow。

        不收進 capture（沒有完整的 response 可寫），但要記一筆數字——不然報表的
        請求數只算「有完整回應的」，失敗的連線在畫面上完全不存在。
        """
        self.errored += 1

    def _save(self, flow) -> None:
        if self._writer is None:
            return
        try:
            host = flow.request.pretty_host if flow.request is not None else ""
            want = ctx.options.capture_hosts
            if want:
                allowed = {h.strip() for h in want.split(",") if h.strip()}
                if host not in allowed:
                    return
            scrubbed = flow.copy()  # 絕不動活的 flow
            scrubbed.id = flow.id  # copy() 會發新 id，紀錄要沿用原本那個
            redact_flow(scrubbed)
            self._writer.add(scrubbed)
            self._fh.flush()
            self.written += 1
        except Exception as exc:  # noqa: BLE001 - fail-closed：任何錯都寧可丟，不寫未脫敏版
            self.dropped += 1
            try:
                ctx.log.warn(f"[capture] 丟棄一條 flow（fail-closed）：{exc}")
            except Exception:  # noqa: BLE001, S110 - 連記錄都失敗時無處可報，不能因此中斷錄製
                pass

    def done(self) -> None:
        if self._fh is not None:
            try:
                self._fh.flush()
            finally:
                self._fh.close()
                self._fh = None
        if self.written or self.dropped or self.skipped_ws or self.errored:
            try:
                ctx.log.info(
                    f"[capture] 寫入 {self.written} 條，丟棄 {self.dropped} 條，"
                    f"跳過 WebSocket {self.skipped_ws} 條，中途出錯 {self.errored} 條。"
                )
            except Exception:  # noqa: BLE001, S110 - 收尾階段 log 可能已關閉，統計印不出來不算故障
                pass


addons = [RedactedCapture()]
