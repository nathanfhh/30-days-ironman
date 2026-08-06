# /// script
# requires-python = ">=3.11"
# dependencies = ["mitmproxy>=12.2.3"]   # 下限＝容器裡寫檔的那個版本，見下方說明
# ///
"""從一顆 .mitm 算出「線上到底流過什麼」，輸出單頁 dashboard。

這支**刻意不做**帳單、不做每角色的時間歸因、不畫甘特——那些 opentelemetry/ 底下
已經有了，而且來源比這裡準（transcript 的 usage 是逐請求精確的，trace 的父子鏈
才認得出誰是誰）。重複做只會讓人問「我該信哪一份」。

這裡只回答四個問題，都是只有 L7 答得出來的：

1. **線上實際傳了多少 byte**——trace 記 token，不記 byte。帳單便宜不等於頻寬省。
2. **其中有多少是重送的**——prompt cache 命中的那一段，就是這次又整個送了一遍的
   那一段。cache 用 token 計價，這裡用 byte 計流量。
3. **除了模型 API，還有誰在講話**——遙測、用量查詢、更新檢查。trace 只記錄程式
   願意送出來的東西，capture 記錄的是事實。
4. **cache 的斷點下在哪、命中了沒**——命中率 trace 看得到，斷點策略只有 body 有。

計價的部分 import opentelemetry/cost-report.py，不自己抄一份牌價。

**讀檔的 mitmproxy 不能比寫檔的舊。** `.mitm` 的內容帶著 flow format 版本號，舊版
遇到新格式是直接拋例外，不是盡力而為地讀。容器裡寫檔的是 12.2.3（格式 v21），所以
這裡的相依下限就釘在同一個版本。用 `uv run` 執行這支腳本會拿到它自己的環境，
不會誤用到 host 上那支可能很舊的 mitmproxy。

用法：

    uv run mitm/wire_report.py ~/ncr/mitm/<session-id>/
    uv run mitm/wire_report.py <一場的資料夾或 .mitm> --out report.html --open
    uv run mitm/wire_report.py <一場的資料夾或 .mitm> --json > wire.json
"""

from __future__ import annotations

import argparse
import hashlib
import html
import importlib.util
import json
import math
import subprocess
import sys
from collections import defaultdict
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path

# mitmproxy 只在真的要讀 capture 檔時才 import（見 load_flows）。上面那些算指標的
# 函式是純運算，讓它們在沒裝 mitmproxy 的環境也 import 得進來，測試才跑得成離線。

# 牌價與成本算法沿用 OTel 那支（同 repo 的兄弟腳本；檔名帶連字號，走路徑載入）。
# 兩份報表對同一個模型不該報出不同的錢。
_COST_PATH = Path(__file__).resolve().parent.parent / "opentelemetry" / "cost-report.py"
_spec = importlib.util.spec_from_file_location("_ncr_costmod", _COST_PATH)
if _spec is None or _spec.loader is None:  # pragma: no cover - import plumbing
    raise SystemExit(f"載入不了 {_COST_PATH}")
_cost = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_cost)

# 「靜止多久算空窗」沿用場次報表的定義（同一個常數，兩份報表不該各有一套）。
_SESSION_PATH = Path(__file__).resolve().parent.parent / "opentelemetry" / "session-report.py"
_sr_spec = importlib.util.spec_from_file_location("_ncr_sessionmod", _SESSION_PATH)
if _sr_spec is None or _sr_spec.loader is None:  # pragma: no cover - import plumbing
    raise SystemExit(f"載入不了 {_SESSION_PATH}")
_session = importlib.util.module_from_spec(_sr_spec)
_sr_spec.loader.exec_module(_session)
GAP_MIN = _session.GAP_MIN

# 斷軸之後，那段空窗在圖上仍佔一點寬度，才看得出「這裡斷過」。
BREAK_EQUIV_S = 25

MESSAGES_PATH = "/v1/messages"


# --------------------------------------------------------------------------------------
# 讀檔與逐 flow 抽取
# --------------------------------------------------------------------------------------

def _body_bytes(msg) -> bytes:
    """落在線上的那份 body。

    刻意取 `raw_content`（沒有解壓的原樣），因為這支要回答的是「傳了多少」，
    不是「內容有多長」。兩者在有壓縮時會差好幾倍。
    """
    if msg is None:
        return b""
    return msg.raw_content or b""


def _sse_usage(text: str) -> dict:
    """從 SSE 串流裡把 usage 撿出來。

    message_start 帶輸入側（含 cache 讀寫），message_delta 帶最終的 output_tokens。
    兩個都要，只取前者會少算輸出。
    """
    usage: dict = {}
    for line in text.splitlines():
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            event = json.loads(payload)
        except ValueError:
            continue
        if event.get("type") == "message_start":
            usage.update(event.get("message", {}).get("usage") or {})
        elif event.get("type") == "message_delta":
            usage.update(event.get("usage") or {})
    return usage


def _breakpoint_sites(req: dict) -> list[str]:
    """找出這一次請求把 cache_control 斷點下在哪裡。

    只數「幾個」用處不大——斷點的**位置**才是策略：放在 system 或 tools 表示
    「這段我認定不會變」，放在 messages 的第 n 則表示「到這裡為止都算穩定」。
    位置變了，前綴的雜湊就變了，後面整段快取跟著作廢。

    回傳的是人看得懂的位置標籤（`system[0]`、`tools[-1]`、`messages[12]`），
    在 messages 上一律以「第幾則」表示，不細分到 content block——真正決定
    快取邊界的是訊息序位，content 內部第幾塊在報表上沒有解讀價值。
    """
    sites: list[str] = []

    def has_bp(node: object) -> bool:
        """這個節點自己或它底下任何一層有沒有 cache_control。"""
        if isinstance(node, dict):
            return "cache_control" in node or any(has_bp(v) for v in node.values())
        if isinstance(node, list):
            return any(has_bp(item) for item in node)
        return False

    for key in ("system", "tools", "messages"):
        section = req.get(key)
        if isinstance(section, dict):
            if has_bp(section):
                sites.append(key)
            continue
        if not isinstance(section, list):
            continue
        for idx, item in enumerate(section):
            if not has_bp(item):
                continue
            # 最後一項用 [-1] 表示：tools 幾乎總是掛在最後一個工具上，
            # 寫成 tools[41] 只會讓讀的人去數工具有幾個。
            label = "-1" if idx == len(section) - 1 else str(idx)
            sites.append(f"{key}[{label}]")

    # 上面三個區段之外的位置（未來 API 新增的欄位）也要看得到，不能靜靜漏掉。
    counted = sum(1 for _ in sites)
    total = _count_all_breakpoints(req)
    if total > counted:
        sites.append(f"其他×{total - counted}")
    return sites


def _count_all_breakpoints(node: object) -> int:
    """整份 body 裡 cache_control 出現的總次數，用來對帳位置有沒有漏掉。"""
    found = 0
    stack: list = [node]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            if "cache_control" in current:
                found += 1
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)
    return found


def _lane_key(req: dict) -> str | None:
    """把請求歸到某一條對話。

    同一條對話的每一次請求，messages[0] 都是同一則——主線程是使用者的第一句，
    subagent 是它的派遣 prompt。拿它當 key 就能把交錯進來的 subagent 流量分開，
    不必依賴任何客戶端配合送出來的識別。
    """
    messages = req.get("messages") or []
    if not messages:
        # 回 None 而不是某個字串：字串是 truthy，會讓所有沒有 messages 的請求
        # 被歸進同一條 lane 互相比對，重送量就變成一個沒有意義的數字。
        return None
    # 取全長的雜湊，不截前綴：兩個 subagent 的派遣 prompt 常常共用一大段前言，
    # 截斷會把它們判成同一條對話，重送量就歸錯人。
    first = json.dumps(messages[0], ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(first.encode()).hexdigest()


def _common_prefix_len(a: bytes, b: bytes) -> int:
    limit = min(len(a), len(b))
    lo, hi = 0, limit
    # 二分找共同前綴長度：body 動輒幾百 KB，逐 byte 比對在幾百次請求下會很慢。
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if a[:mid] == b[:mid]:
            lo = mid
        else:
            hi = mid - 1
    return lo


def resolve_capture(target: Path) -> Path:
    """接受一場的資料夾，也接受直接指到 .mitm。

    現在的版面是一場一個資料夾（`~/ncr/mitm/<session-id>/flows.mitm`），指資料夾
    比指檔案自然。舊的版面是同一層裡的 `flows-<時間>.mitm`，那些檔案還在，直接指
    檔案的路徑也要能走。
    """
    if target.is_dir():
        found = sorted(target.glob("*.mitm"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not found:
            raise SystemExit(f"{target} 裡沒有 .mitm")
        if len(found) > 1:
            # 挑了哪一顆要說出來，不要靜靜選一個。
            print(f"（{target} 裡有 {len(found)} 顆 .mitm，用最新的 {found[0].name}）")
        return found[0]
    return target


def load_sidecars(capture: Path) -> dict:
    """撿這一場的兩個附檔：環境，以及防火牆的封包計數。

    兩種版面都找：同資料夾裡的 `meta.json` / `firewall.txt`（一場一個資料夾），
    以及舊版同層的 `<檔名>.meta.json` / `<檔名>.firewall.txt`。

    沒有就沒有（手動錄的、或舊版 image 錄的），報表照樣出，只是少那兩塊。
    **不要靜靜當成「這一場什麼都沒擋」**，那是完全不同的意思。
    """
    base = capture.with_suffix("")
    out: dict = {"meta": None, "firewall": None}

    for meta_path in (capture.parent / "meta.json", base.with_suffix(".meta.json")):
        if meta_path.is_file():
            try:
                out["meta"] = json.loads(meta_path.read_text())
            except ValueError:
                out["meta"] = None
            break

    for fw_path in (capture.parent / "firewall.txt", base.with_suffix(".firewall.txt")):
        if fw_path.is_file():
            out["firewall"] = parse_firewall(fw_path.read_text())
            break
    return out


def _counter(token: str) -> int | None:
    """iptables 的封包計數。純數字，或帶 K/M/G 縮寫的舊資料。

    我們自己的 dump 加了 `-x` 所以是純數字，但手上仍有那之前錄的檔案；
    解析器直接 `int()` 會在那些檔案上拋例外，整份報表跟著產不出來。
    """
    if token.isdigit():
        return int(token)
    scale = {"K": 1_000, "M": 1_000_000, "G": 1_000_000_000}.get(token[-1:].upper())
    head = token[:-1]
    if scale and head.replace(".", "", 1).isdigit():
        return int(float(head) * scale)
    return None


def parse_firewall(text: str) -> dict:
    """把 `iptables -L -v -n` 的輸出拆成「放行了什麼、擋了什麼」。

    每條規則的前兩欄是封包數與 byte 數，倒數第二欄是 target。擋下的那幾條
    （REJECT / DROP）才是這份資料的重點：沒送出去的東西不會出現在任何 L7 紀錄裡。
    """
    chains: list[dict] = []
    chain = None
    for line in text.splitlines():
        if line.startswith("Chain "):
            chain = {"name": line.split()[1], "policy": "", "rules": []}
            if "policy" in line:
                chain["policy"] = line.split("policy")[1].split()[0].rstrip(")")
            chains.append(chain)
            continue
        parts = line.split()
        if chain is None or len(parts) < 4 or not _counter(parts[0]):
            continue
        chain["rules"].append({
            "packets": _counter(parts[0]) or 0,
            "bytes": parts[1],
            "target": parts[2],
            "detail": " ".join(parts[3:]),
        })
    blocked = sum(r["packets"] for c in chains for r in c["rules"]
                  if r["target"] in ("REJECT", "DROP"))
    allowed = sum(r["packets"] for c in chains for r in c["rules"]
                  if r["target"] == "ACCEPT")
    return {"chains": chains, "blocked": blocked, "allowed": allowed}


def load_flows(path: str) -> list[dict]:
    from mitmproxy import io as mitm_io

    rows: list[dict] = []
    with open(path, "rb") as fh:
        for flow in mitm_io.FlowReader(fh).stream():
            request = getattr(flow, "request", None)
            if request is None:
                continue
            response = getattr(flow, "response", None)
            up = _body_bytes(request)
            down = _body_bytes(response)
            # 取 request 的開始時間，不是 client_conn 的。連線是 keep-alive 的，
            # 幾十次請求共用一條——用連線時間會讓它們全部塌在同一個時間點上，
            # 時間軸與空窗偵測跟著一起錯。
            started = getattr(request, "timestamp_start", None) or getattr(
                getattr(flow, "client_conn", None), "timestamp_start", None)
            row = {
                "ts": started or 0.0,
                "host": request.pretty_host,
                "path": request.path.split("?")[0],
                "method": request.method,
                "status": getattr(response, "status_code", None),
                "up": len(up),
                "down": len(down),
                # 上行有沒有壓縮，決定「網卡上的量」跟這裡的數字能不能直接對帳。
                "req_encoding": request.headers.get("content-encoding", ""),
                "resp_encoding": getattr(response, "headers", {}).get("content-encoding", "")
                if response is not None else "",
                "model": None,
                "lane": None,
                "breakpoints": 0,
                "breakpoint_sites": [],
                "usage": {},
                "_raw_up": up,
            }
            if row["path"] == MESSAGES_PATH and request.method == "POST":
                try:
                    payload = json.loads(request.get_text(strict=False) or "{}")
                except (ValueError, TypeError):
                    payload = {}
                if isinstance(payload, dict):
                    row["model"] = payload.get("model")
                    row["lane"] = _lane_key(payload)
                    row["breakpoints"] = _count_all_breakpoints(payload)
                    row["breakpoint_sites"] = _breakpoint_sites(payload)
                if response is not None:
                    try:
                        row["usage"] = _sse_usage(response.get_text(strict=False) or "")
                    except Exception:  # noqa: BLE001 - 撿不到 usage 就當沒有，不該讓整份報表掛掉
                        row["usage"] = {}
            rows.append(row)
    rows.sort(key=lambda r: r["ts"])
    return rows


# --------------------------------------------------------------------------------------
# 指標
# --------------------------------------------------------------------------------------

def annotate_repeat(rows: list[dict]) -> None:
    """算每一次請求裡「上一次就送過」的 byte 數。

    刻意不叫它「重送」。重送聽起來像連線失敗後的 retry，那是另一回事——這裡沒有任何
    東西失敗，是協定本來就要求你每一次都把整段對話從頭附上。所以報表上分成
    **既有內容**（上一次已經送過的那段開頭）與**新內容**（這一次真正新增的）。

    比對對象是**同一條對話的前一次**，不是時間上的前一次——subagent 會交錯進來，
    拿時間相鄰的兩次去比會得到一個沒有意義的小數字。
    """
    previous: dict[str, bytes] = {}
    for row in rows:
        lane = row.get("lane")
        if lane is None:
            row["repeat"] = 0
            continue
        prior = previous.get(lane)
        row["repeat"] = _common_prefix_len(prior, row["_raw_up"]) if prior else 0
        previous[lane] = row["_raw_up"]


def find_gaps(rows: list[dict], gap_min_s: int = GAP_MIN) -> list[tuple[float, float]]:
    """找出全場沒有任何請求的空窗。

    一場審查裡有大量時間是沒有動靜的——人在看報告、在回訊息、或者只是離開。
    不把這些壓掉，曲線會被死時間拉平，看起來像整場都很閒。
    """
    stamps = sorted(r["ts"] for r in rows if r["ts"])
    gaps = []
    for earlier, later in pairwise(stamps):
        if later - earlier >= gap_min_s:
            gaps.append((earlier, later))
    return gaps


def summarize(rows: list[dict]) -> dict:
    total_up = sum(r["up"] for r in rows)
    total_down = sum(r["down"] for r in rows)
    repeated = sum(r.get("repeat", 0) for r in rows)
    stamps = [r["ts"] for r in rows if r["ts"]]

    endpoints: dict[tuple[str, str], dict] = defaultdict(
        lambda: {"count": 0, "up": 0, "down": 0})
    for row in rows:
        slot = endpoints[(row["host"], row["path"])]
        slot["count"] += 1
        slot["up"] += row["up"]
        slot["down"] += row["down"]

    models: dict[str, dict] = defaultdict(
        lambda: {"calls": 0, "input": 0, "cache_read": 0, "cache_write": 0,
                 "output": 0, "cost": 0.0, "priced": True})
    for row in rows:
        model = row.get("model")
        if not model:
            continue
        usage = row.get("usage") or {}
        slot = models[model]
        slot["calls"] += 1
        slot["input"] += usage.get("input_tokens", 0) or 0
        slot["cache_read"] += usage.get("cache_read_input_tokens", 0) or 0
        slot["cache_write"] += usage.get("cache_creation_input_tokens", 0) or 0
        slot["output"] += usage.get("output_tokens", 0) or 0
        # 正規化與牌價都走 cost-report.py，兩份報表不該對同一個模型報出不同的錢。
        # usage 的欄位可能存在但值是 null。summarize 自己的加總都有 `or 0`，
        # 丟給 normalize_usage 的那份也要，不然 cost_usd 會 TypeError、整份報表掛掉。
        clean = {k: (v if v is not None else 0) for k, v in usage.items()}
        priced = _cost.cost_usd(model, _cost.normalize_usage(clean))
        if priced is None:
            slot["priced"] = False      # 牌價表上沒有就不猜，報表標「無牌價」
        else:
            slot["cost"] += priced

    gaps = find_gaps(rows)
    idle = sum(end - start for start, end in gaps)
    compressed_up = sum(1 for r in rows if r["req_encoding"])
    return {
        "capture": {
            "requests": len(rows),
            "up": total_up,
            "down": total_down,
            "repeated": repeated,
            "repeated_ratio": (repeated / total_up) if total_up else 0.0,
            "started": min(stamps) if stamps else None,
            "ended": max(stamps) if stamps else None,
            "wall": (max(stamps) - min(stamps)) if len(stamps) > 1 else 0.0,
            "requests_compressed": compressed_up,
            "idle": idle,
            "gaps": [{"start": s, "end": e, "seconds": e - s} for s, e in gaps],
        },
        "endpoints": [
            {"host": host, "path": path, **slot}
            for (host, path), slot in sorted(
                endpoints.items(), key=lambda kv: -kv[1]["up"])
        ],
        "models": [{"model": name, **slot} for name, slot in models.items()],
        "calls": [
            {k: v for k, v in row.items() if not k.startswith("_")}
            for row in rows
        ],
    }


# --------------------------------------------------------------------------------------
# 呈現
# --------------------------------------------------------------------------------------

def fmt_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def fmt_secs(s: float) -> str:
    if s < 60:
        return f"{s:.0f} 秒"
    if s < 3600:
        return f"{s / 60:.0f} 分"
    return f"{int(s // 3600)} 時 {int((s % 3600) // 60)} 分"


def fmt_clock(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, UTC).astimezone().strftime("%H:%M")


def _nice_ticks(hi: float, count: int = 4) -> list[float]:
    """挑好看的刻度值。1 / 2 / 5 × 10^n，不要出現 3.7 MB 這種刻度。"""
    if hi <= 0:
        return [0]
    raw = hi / count
    mag = 10 ** math.floor(math.log10(raw))
    for mult in (1, 2, 2.5, 5, 10):
        if mag * mult >= raw:
            step = mag * mult
            break
    else:  # pragma: no cover - 迴圈必定命中 10
        step = mag * 10
    ticks, value = [], 0.0
    while value <= hi * 1.0001:
        ticks.append(value)
        value += step
    return ticks


def json_for_script(value: object) -> str:
    """把資料序列化成可以安全嵌進 <script> 的 JSON。

    `json.dumps` 不會逸出 `<`，所以資料裡只要有 `</script>` 就能提前關掉整個 script
    區塊、後面的內容變成 HTML。而這裡的資料**來自 capture**——request path 是容器
    裡的東西可以影響的，等於讓沙箱裡的資料在 host 的瀏覽器拿到執行權。
    """
    return (json.dumps(value, ensure_ascii=False)
            .replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026"))


def _chart(rows: list[dict]) -> tuple[str, str]:
    """累計上傳曲線。回傳 (SVG, 給 JS 用的資料 JSON)。

    兩層堆疊：整條是累計上傳，底下那層是其中的既有內容。手寫 SVG、互動用內嵌的
    JS，沒有任何外部資源——這頁要能離線開、能寄給別人。
    """
    stamps = [r["ts"] for r in rows if r["ts"]]
    if not stamps:
        return '<p class="empty">沒有帶時間戳的 flow，畫不出曲線。</p>', "[]"

    t0, t1 = min(stamps), max(stamps)
    total = sum(r["up"] for r in rows) or 1
    gaps = find_gaps(rows)

    W, H = 1000, 330          # viewBox 座標，實際大小由 CSS 撐滿
    PAD_L, PAD_B, PAD_T = 92, 56, 16
    plot_w, plot_h = W - PAD_L - 20, H - PAD_B - PAD_T
    BREAK_PX = 16             # 斷口在圖上固定佔這麼寬，不隨空窗長度變

    # 把時間切成「有動靜的區間」，每段照它的實際長度分配寬度，段與段之間插一個固定
    # 寬度的斷口。先前是在時間域裡壓縮，結果是空窗愈長、斷口在圖上反而愈窄到看不見。
    bounds = [t0] + [b for g in gaps for b in g] + [t1]
    segments = [(bounds[i], bounds[i + 1]) for i in range(0, len(bounds) - 1, 2)]
    active = sum(end - start for start, end in segments) or 1.0
    usable = plot_w - BREAK_PX * len(gaps)

    placed, cursor = [], float(PAD_L)
    for start, end in segments:
        width = (end - start) / active * usable
        placed.append((start, end, cursor, width))
        cursor += width + BREAK_PX

    def x_of(ts: float) -> float:
        for start, end, x0, width in placed:
            if ts <= end:
                span_s = (end - start) or 1.0
                return x0 + (max(ts, start) - start) / span_s * width
        return placed[-1][2] + placed[-1][3]

    def y_of(value: float) -> float:
        return PAD_T + plot_h - value / total * plot_h

    cum_up = cum_rep = 0
    pts_up, pts_rep, points = [], [], []
    for row in rows:
        cum_up += row["up"]
        cum_rep += row.get("repeat", 0)
        x, ts = x_of(row["ts"] or t0), row["ts"] or t0
        pts_up.append(f"{x:.1f},{y_of(cum_up):.1f}")
        pts_rep.append(f"{x:.1f},{y_of(cum_rep):.1f}")
        points.append({
            "x": round(x, 1),
            "yUp": round(y_of(cum_up), 1),
            "yRep": round(y_of(cum_rep), 1),
            "rel": fmt_secs(ts - t0),
            "clock": fmt_clock(ts),
            "path": row["path"],
            "model": (row.get("model") or "").replace("claude-", "") or "—",
            "up": fmt_bytes(row["up"]),
            "repeat": fmt_bytes(row.get("repeat", 0)),
            "cumUp": fmt_bytes(cum_up),
            "cumRep": fmt_bytes(cum_rep),
        })

    # Y 軸：累計上傳量
    y_axis = []
    for tick in _nice_ticks(total):
        y = y_of(tick)
        y_axis.append(
            f'<line class="grid" x1="{PAD_L}" y1="{y:.1f}" x2="{W - 16}" y2="{y:.1f}"/>'
            f'<text class="tick" x="{PAD_L - 10}" y="{y + 5:.1f}" text-anchor="end">'
            f'{html.escape(fmt_bytes(tick))}</text>')

    # X 軸：每一段有動靜的區間標它的起訖時間，斷口另外標它壓掉了多久。
    # 兩層都要防重疊——斷軸會把相鄰的邊界擠到同一個位置，直接全部畫上去就是糊成一團。
    def place_labels(items: list[tuple[float, str]], min_px: float,
                     row_y: float, cls: str) -> list[str]:
        out, last_x = [], -1e9
        for x, text in sorted(items):
            if x - last_x < min_px:
                continue
            out.append(
                f'<text class="{cls}" x="{x:.1f}" y="{row_y:.1f}" text-anchor="middle">'
                f'{html.escape(text)}</text>')
            last_x = x
        return out

    x_axis = []
    for _, _, x0, width in placed:                      # 區間邊界的垂直格線
        for x in (x0, x0 + width):
            x_axis.append(f'<line class="grid" x1="{x:.1f}" y1="{PAD_T}" '
                          f'x2="{x:.1f}" y2="{PAD_T + plot_h}"/>')
    for (start_ts, end_ts, x0, width), gap in zip(placed, gaps):   # 斷口的灰帶
        x_axis.append(f'<rect class="break" x="{x0 + width:.1f}" y="{PAD_T}" '
                      f'width="{BREAK_PX}" height="{plot_h}"/>')

    clocks = [(x_of(t0), fmt_clock(t0)), (x_of(t1), fmt_clock(t1))]
    for start_ts, end_ts, x0, width in placed:
        clocks.append((x0, fmt_clock(start_ts)))
        clocks.append((x0 + width, fmt_clock(end_ts)))
    x_axis += place_labels(clocks, 46, PAD_T + plot_h + 20, "tick")

    breaks = []
    for (_, _, x0, width), (gap_start, gap_end) in zip(placed, gaps):
        breaks.append((x0 + width + BREAK_PX / 2,
                       f"⫽ 靜止 {fmt_secs(gap_end - gap_start)}"))
    x_axis += place_labels(breaks, 96, PAD_T + plot_h + 40, "tick brk")

    base = f"{x_of(t1):.1f},{y_of(0):.1f} {PAD_L},{y_of(0):.1f}"
    svg = f"""<svg viewBox="0 0 {W} {H}" class="chart" id="chart">
  {''.join(y_axis)}{''.join(x_axis)}
  <polygon class="area-up" points="{' '.join(pts_up)} {base}"/>
  <polygon class="area-rep" points="{' '.join(pts_rep)} {base}"/>
  <polyline class="line-up" points="{' '.join(pts_up)}"/>
  <line id="cursor" class="cursor" x1="0" y1="{PAD_T}" x2="0" y2="{PAD_T + plot_h}" style="display:none"/>
  <circle id="dotUp" class="dot up" r="4" style="display:none"/>
  <circle id="dotRep" class="dot rep" r="4" style="display:none"/>
  <rect id="hit" x="{PAD_L}" y="{PAD_T}" width="{plot_w}" height="{plot_h}" fill="transparent"/>
</svg>"""
    return svg, json_for_script(points)


PAGE = """<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>線上流量報表｜__NAME__</title>
<style>
:root {
  --bg:#fbfbfa; --fg:#1a1d21; --dim:#5f6772; --line:#e1e4e8; --card:#fff;
  --up:#2f6feb; --rep:#e8912d; --shadow:0 1px 2px rgba(0,0,0,.05);
}
@media (prefers-color-scheme: dark) {
  :root { --bg:#15171c; --fg:#e8eaed; --dim:#9aa2ad; --line:#2c3138; --card:#1c1f25;
          --up:#5b93f5; --rep:#f0a54a; --shadow:none; }
}
* { box-sizing:border-box; }
body { margin:0; padding:28px 32px 48px; background:var(--bg); color:var(--fg);
  font:16px/1.6 -apple-system,"Noto Sans TC","PingFang TC",sans-serif; }
h1 { font-size:22px; margin:0 0 6px; font-weight:650; letter-spacing:-.01em; }
.meta { color:var(--dim); font-size:14px; margin-bottom:22px; }
.kpis { display:grid; grid-template-columns:repeat(4,1fr); gap:14px; margin-bottom:18px; }
.kpi { background:var(--card); border:1px solid var(--line); border-radius:10px;
  padding:16px 18px; box-shadow:var(--shadow); }
.kpi .n { font-size:30px; font-weight:650; letter-spacing:-.02em; font-variant-numeric:tabular-nums; }
.kpi .k { color:var(--dim); font-size:14px; margin-top:2px; }
.card { background:var(--card); border:1px solid var(--line); border-radius:10px;
  padding:18px 20px; box-shadow:var(--shadow); margin-bottom:14px; }
.card h2 { font-size:16px; margin:0 0 4px; font-weight:650; }
.card .sub { color:var(--dim); font-size:13px; margin:0 0 14px; }
.chartwrap { position:relative; }
.chart { width:100%; height:auto; display:block; overflow:visible; }
.area-up { fill:var(--up); opacity:.14; }
.area-rep { fill:var(--rep); opacity:.34; }
.line-up { fill:none; stroke:var(--up); stroke-width:2; }
.grid-line, .grid { stroke:var(--line); stroke-width:1; }
.tick { fill:var(--dim); font-size:13px; }
.tick.dim { opacity:.62; font-size:12px; }
.axis-label { fill:var(--dim); font-size:13px; }
.cursor { stroke:var(--dim); stroke-width:1; stroke-dasharray:3 3; }
.break { fill:var(--dim); opacity:.09; }
.tick.brk { font-size:12px; opacity:.75; }
.dot { stroke:var(--card); stroke-width:2; }
.dot.up { fill:var(--up); } .dot.rep { fill:var(--rep); }
#tip { position:absolute; pointer-events:none; display:none; z-index:5;
  background:var(--card); border:1px solid var(--line); border-radius:8px;
  padding:10px 12px; font-size:13px; line-height:1.65; min-width:210px;
  box-shadow:0 4px 14px rgba(0,0,0,.13); }
#tip b { font-weight:650; }
#tip .row { display:flex; justify-content:space-between; gap:18px; }
#tip .row span:last-child { font-variant-numeric:tabular-nums; }
#tip .ep { color:var(--dim); font-size:12px; word-break:break-all;
  border-top:1px solid var(--line); margin-top:7px; padding-top:7px; }
.legend { display:flex; gap:20px; font-size:14px; color:var(--dim); margin-top:12px; }
.swatch { display:inline-block; width:11px; height:11px; border-radius:3px; margin-right:7px; }
table { width:100%; border-collapse:collapse; font-size:14px; }
th,td { text-align:left; padding:7px 8px; border-bottom:1px solid var(--line); }
th { color:var(--dim); font-weight:500; font-size:13px; }
td.n, th.n { text-align:right; font-variant-numeric:tabular-nums; }
td.ep { word-break:break-all; }
.wrap { overflow-x:auto; }
.tag { display:inline-block; padding:2px 9px; border-radius:999px; font-size:12px;
  font-weight:650; letter-spacing:.03em; white-space:nowrap; }
.tag.accept { color:#0f7a3d; background:rgba(24,150,80,.13); }
.tag.reject { color:#b03a28; background:rgba(200,66,44,.14); }
.tag.other  { color:var(--dim);  background:rgba(128,128,128,.14); }
@media (prefers-color-scheme: dark) {
  .tag.accept { color:#5fd18b; background:rgba(50,190,110,.15); }
  .tag.reject { color:#ff8a72; background:rgba(255,110,80,.16); }
}
.note { color:var(--dim); font-size:13px; margin-top:14px; line-height:1.7; }
.empty { color:var(--dim); font-size:14px; }
</style>
<h1>線上流量報表</h1>
<div class="meta">__META__</div>
<div class="kpis">__KPIS__</div>
<div class="card">
    <h2>累計上傳量</h2>
    <p class="sub">橘色是其中「上一次就送過」的。滑過曲線看每一個。</p>
    <div class="chartwrap">__CHART__<div id="tip"></div></div>
    <div class="legend">
      <span><i class="swatch" style="background:var(--up)"></i>新內容</span>
      <span><i class="swatch" style="background:var(--rep)"></i>既有內容（上一次送過的）</span>
    </div>
    <div class="note">__REPEAT_NOTE__</div>
</div>
<div class="card">
  <h2>連線對象</h2>
  <p class="sub">依上行量排序。__HOSTS_NOTE__</p>
  <div class="wrap">__ENDPOINTS__</div>
</div>
__FIREWALL__
<div class="card">
  <h2>每一個模型呼叫</h2>
  <p class="sub"><b>「快取存到哪」</b>：請求可以標記「從開頭到這裡請存起來」，
  伺服器把那段算成一個雜湊值。下一次開頭一樣就命中，<b>差一個字元就整段作廢</b>。</p>
  <div class="wrap">__CALLS__</div>
  <div class="note">__COMPRESSION_NOTE__</div>
</div>
<script>
const PTS = __POINTS__;
const svg = document.getElementById('chart');
if (svg && PTS.length) {
  const tip = document.getElementById('tip');
  const cur = document.getElementById('cursor');
  const dU = document.getElementById('dotUp'), dR = document.getElementById('dotRep');
  const hit = document.getElementById('hit');
  const vb = svg.viewBox.baseVal;
  hit.addEventListener('mousemove', e => {
    const box = svg.getBoundingClientRect();
    const vx = (e.clientX - box.left) / box.width * vb.width;
    let best = PTS[0];
    for (const p of PTS) if (Math.abs(p.x - vx) < Math.abs(best.x - vx)) best = p;
    cur.setAttribute('x1', best.x); cur.setAttribute('x2', best.x);
    cur.style.display = ''; 
    dU.setAttribute('cx', best.x); dU.setAttribute('cy', best.yUp); dU.style.display = '';
    dR.setAttribute('cx', best.x); dR.setAttribute('cy', best.yRep); dR.style.display = '';
    // 不用 innerHTML 拼字串：best.path / best.model 來自 capture，容器裡的東西
    // 影響得到。一律走 textContent，資料永遠是文字、不會變成標記。
    const row = (label, value) => {
      const d = document.createElement('div'); d.className = 'row';
      const a = document.createElement('span'); a.textContent = label;
      const b = document.createElement('span'); b.textContent = value;
      d.append(a, b); return d;
    };
    tip.replaceChildren();
    const head = document.createElement('div');
    const strong = document.createElement('b'); strong.textContent = '+' + best.rel;
    const clock = document.createElement('span');
    clock.style.color = 'var(--dim)'; clock.textContent = ' ' + best.clock;
    head.append(strong, clock);
    const ep = document.createElement('div');
    ep.className = 'ep'; ep.textContent = best.model + ' · ' + best.path;
    tip.append(head,
      row('這一次上傳', best.up), row('其中既有內容', best.repeat),
      row('累計上傳', best.cumUp), row('累計既有', best.cumRep), ep);
    tip.style.display = 'block';
    // 靠右時把提示框翻到游標左邊，不要被視窗切掉
    const px = best.x / vb.width * box.width;
    tip.style.left = (px > box.width * 0.62 ? px - tip.offsetWidth - 14 : px + 14) + 'px';
    tip.style.top = (best.yUp / vb.height * box.height - 10) + 'px';
  });
  hit.addEventListener('mouseleave', () => {
    tip.style.display = 'none'; cur.style.display = 'none';
    dU.style.display = 'none'; dR.style.display = 'none';
  });
}
</script>
"""


SITE_NAMES = {"system": "系統提示", "tools": "工具定義", "messages": "對話訊息"}


def site_label(site: str) -> str:
    """把 `system[-1]` 這種機器可讀的位置換成人看得懂的字。

    JSON 裡留原形（後續分析要拿它比對），只有畫面上換。
    """
    if "[" not in site:
        return SITE_NAMES.get(site, site)
    name, _, rest = site.partition("[")
    idx = rest.rstrip("]")
    label = SITE_NAMES.get(name, name)
    if idx == "-1":
        return f"{label}末段" if name != "messages" else "最後一則訊息"
    # 陣列從 0 起算，講給人聽要 +1
    ordinal = int(idx) + 1 if idx.lstrip("-").isdigit() else idx
    return f"{label}第 {ordinal} 段" if name != "messages" else f"第 {ordinal} 則訊息"


def cache_boundary(sites: list[str]) -> str:
    """這一次請求要求伺服器快取到哪裡為止。

    一次請求可以下好幾個標記，但真正決定「省下多少」的是**最深的那一個**——
    它之前的內容全部進快取。所以報表只講那一個，其餘的用「另有 n 個標記」帶過，
    不要把三個位置排在一起讓人自己判斷哪個才算數。
    """
    if not sites:
        return "沒有標記（整段重算）"
    # 位置在請求裡的先後：system → tools → messages，最深的就是最後出現的那個
    order = {"system": 0, "tools": 1, "messages": 2}
    # 用 reversed：max 平手時回傳先遇到的那個，而 sites 在同一段裡是升冪排的，
    # 直接 max 會在「最後兩則訊息各下一個標記」這種常見情況挑到比較淺的那個。
    deepest = max(reversed(sites), key=lambda x: order.get(x.split("[")[0], 3))
    extra = len(sites) - 1
    label = site_label(deepest)
    return f"{label}（另有 {extra} 個標記）" if extra else label


def _kpi(value: str, label: str) -> str:
    return f'<div class="kpi"><div class="n">{html.escape(value)}</div>' \
           f'<div class="k">{html.escape(label)}</div></div>'


def _table(headers: list[tuple[str, bool]], rows: list[list[str]],
           first_col_class: str = "", raw_cols: set[int] | None = None) -> str:
    """raw_cols 裡的欄位不做逸出。只給本檔自己組出來的標記用（例如 ACCEPT/REJECT 的
    色塊），任何來自 capture 的字串一律走逸出那條路。"""
    head = "".join(
        f'<th class="n">{html.escape(h)}</th>' if numeric else f"<th>{html.escape(h)}</th>"
        for h, numeric in headers)
    body = []
    for row in rows:
        cells = []
        for idx, (cell, (_, numeric)) in enumerate(zip(row, headers)):
            cls = "n" if numeric else (first_col_class if idx == 0 else "")
            attr = f' class="{cls}"' if cls else ""
            body_cell = cell if (raw_cols and idx in raw_cols) else html.escape(cell)
            cells.append(f"<td{attr}>{body_cell}</td>")
        body.append("<tr>" + "".join(cells) + "</tr>")
    return (f"<table><thead><tr>{head}</tr></thead>"
            f"<tbody>{''.join(body)}</tbody></table>")


PROTO_NAMES = {"0": "全部", "all": "全部", "1": "ICMP", "6": "TCP", "17": "UDP"}
ANY = "任何"


def split_rule(detail: str) -> list[str]:
    """把 iptables 的規則欄拆開並翻成看得懂的值。

    原文是 `0 -- * * 0.0.0.0/0 0.0.0.0/0 reject-with icmp-admin-prohibited`，
    順序是 協定／opt／進介面／出介面／來源／目的地／target 選項。難讀的原因有兩個：
    沒有表頭，而且「不限」是用 `*` 和 `0.0.0.0/0` 表示的。這裡兩個都處理：欄位拆開
    各給表頭，值翻成人話。opt 欄丟掉，它幾乎永遠是 `--`。

    原文不會消失，`.firewall.txt` 裡留的是 iptables 的原樣輸出。
    """
    parts = detail.split()
    if len(parts) < 6:
        return [detail, "", "", "", "", ""]
    proto, _opt, iface_in, iface_out, src, dst = parts[:6]
    extra = " ".join(parts[6:])
    if extra == "reject-with icmp-admin-prohibited":
        # 這個選項是有意義的：REJECT 會回一個 ICMP 通知，對方兩毫秒就知道被擋；
        # DROP 是沉默，對方要等到逾時。
        extra = "拒絕並回 ICMP 通知"
    return [
        PROTO_NAMES.get(proto, proto),
        ANY if iface_in == "*" else iface_in,
        ANY if iface_out == "*" else iface_out,
        ANY if src == "0.0.0.0/0" else src,
        ANY if dst == "0.0.0.0/0" else dst,
        extra,
    ]


def build_firewall_card(sidecars: dict) -> str:
    """防火牆這一場放行與擋下的封包。

    這塊回答的是「連線對象」那張表回答不了的問題：沒連成功的呢？L7 只錄得到有出去
    而且有回來的東西，被牆擋掉的連線在那張表上完全不存在。
    """
    fw = sidecars.get("firewall")
    meta = sidecars.get("meta")
    if not fw:
        if meta and meta.get("network") == "unrestricted":
            note = "這一場是完全開放，沒有防火牆規則可計數。"
        else:
            note = "這一場沒有防火牆計數。<b>是沒有量，不是沒有擋。</b>"
        return ('<div class="card"><h2>這一場的邊界</h2>'
                f'<p class="sub">{note}</p></div>')

    table_rows = []
    for chain in fw["chains"]:
        for rule in chain["rules"]:
            if not rule["packets"]:
                continue
            tone = {"ACCEPT": "accept", "REJECT": "reject", "DROP": "reject"}.get(
                rule["target"], "other")
            tag = f'<span class="tag {tone}">{html.escape(rule["target"])}</span>'
            table_rows.append([chain["name"], tag, f'{rule["packets"]:,}',
                               rule["bytes"], *split_rule(rule["detail"])])
    table = _table([("鏈", False), ("處置", False), ("封包", True), ("bytes", True),
                    ("協定", False), ("進", False), ("出", False),
                    ("來源", False), ("目的地", False), ("條件／選項", False)],
                   table_rows, raw_cols={1})
    sub = (f'放行 {fw["allowed"]:,} 個封包，擋下 <b>{fw["blocked"]:,}</b> 個。'
           f'被擋掉的連線不會出現在上面那張端點表裡。')
    return ('<div class="card">\n  <h2>這一場的邊界</h2>\n'
            f'  <p class="sub">{sub}</p>\n'
            f'  <div class="wrap">{table}</div>\n</div>')


def build_page(name: str, summary: dict, rows: list[dict],
               sidecars: dict | None = None) -> str:
    cap = summary["capture"]
    started = fmt_clock(cap["started"]) if cap["started"] else "—"

    kpis = "".join([
        _kpi(fmt_bytes(cap["up"]), "上傳"),
        _kpi(fmt_bytes(cap["down"]), "下載"),
        _kpi(str(cap["requests"]), "請求數"),
        _kpi(fmt_secs(cap["wall"] - cap["idle"]), "有動靜"),
    ])

    endpoints = _table(
        [("端點", False), ("次", True), ("上行", True), ("下行", True)],
        [[f'{e["host"]}{e["path"]}', str(e["count"]), fmt_bytes(e["up"]), fmt_bytes(e["down"])]
         for e in summary["endpoints"]],
        first_col_class="ep")

    call_rows = []
    for row in rows:
        if not row.get("model"):
            continue
        usage = row.get("usage") or {}
        call_rows.append([
            fmt_clock(row["ts"]) if row["ts"] else "—",
            (row.get("model") or "").replace("claude-", ""),
            fmt_bytes(row["up"]),
            fmt_bytes(row.get("repeat", 0)),
            cache_boundary(row.get("breakpoint_sites") or []),
            f'{usage.get("cache_read_input_tokens", 0):,}',
            f'{usage.get("cache_creation_input_tokens", 0):,}',
            f'{usage.get("input_tokens", 0):,}',
            f'{usage.get("output_tokens", 0):,}',
        ])
    calls = _table(
        [("時間", False), ("模型", False), ("上行", True), ("其中既有", True),
         ("快取存到哪", False),
         ("cache 讀", True), ("cache 寫", True), ("新輸入", True), ("輸出", True)],
        call_rows) if call_rows else '<p class="empty">這顆 capture 裡沒有模型呼叫。</p>'

    info = (sidecars or {}).get("meta") or {}

    idle_note = ""
    if cap["gaps"]:
        idle_note = (f'　時間軸斷了 {len(cap["gaps"])} 處：全場 {fmt_secs(cap["wall"])} 裡有 '
                     f'{fmt_secs(cap["idle"])} 沒有任何請求。')
    # 只錄了部分 host 的話一定要講。不講的話這張表看起來就像「這一場連過的全部」，
    # 而它其實是「過濾器讓我看到的那些」。
    hosts = info.get("capture_hosts")
    hosts_note = (f'本場只錄 {hosts}，其餘 host 不在此表。' if hosts
                  else '這一場的錄製範圍沒有記錄，此表未必是全部連線。')

    compress_caveat = ('　請求有壓縮，這個比例是壓縮後 byte 的前綴比對，不等於內容重複率。'
                       if cap["requests_compressed"] else '')
    ratio = cap["repeated_ratio"] * 100
    repeat_note = (
        f'{fmt_bytes(cap["repeated"])}（{ratio:.1f}%）是同一條對話前一次就送過的。'
        f'不是失敗重試，是協定要求每一次都把整段對話從頭附上。'
        + idle_note + compress_caveat
    )

    if cap["requests_compressed"]:
        compression_note = (
            f'{cap["requests_compressed"]} 次請求有壓縮，數字是壓縮後的量。'
        )
    else:
        compression_note = (
            '沒有任何一次請求壓縮過，所以這些數字可以直接跟網卡上的量對帳。'
        )

    meta_line = f"{name} · 起始 {started} · 共 {cap['requests']} 次請求"
    if info.get("network"):
        meta_line += f" · 網路 {info['network']}"
    if info.get("session_id"):
        meta_line += f" · session {info['session_id'][:8]}"

    svg, points = _chart(rows)
    return (PAGE
            .replace("__NAME__", html.escape(name))
            .replace("__META__", html.escape(meta_line))
            .replace("__KPIS__", kpis)
            .replace("__CHART__", svg)
            .replace("__POINTS__", points)
            .replace("__HOSTS_NOTE__", html.escape(hosts_note))
            .replace("__ENDPOINTS__", endpoints)
            .replace("__CALLS__", calls)
            .replace("__FIREWALL__", build_firewall_card(sidecars or {}))
            .replace("__REPEAT_NOTE__", html.escape(repeat_note))
            .replace("__COMPRESSION_NOTE__", html.escape(compression_note)))


def print_terminal(summary: dict) -> None:
    cap = summary["capture"]
    print(f"請求 {cap['requests']} 次 · 上傳 {fmt_bytes(cap['up'])} · "
          f"下載 {fmt_bytes(cap['down'])} · 時長 {fmt_secs(cap['wall'])}")
    print(f"其中既有內容（上一次就送過的）{fmt_bytes(cap['repeated'])}"
          f"（{cap['repeated_ratio'] * 100:.1f}%）")
    print()
    for e in summary["endpoints"]:
        print(f"  {e['count']:>4}  {fmt_bytes(e['up']):>9} ↑  "
              f"{fmt_bytes(e['down']):>9} ↓  {e['host']}{e['path']}")
    for m in summary["models"]:
        cost = "無牌價" if not m["priced"] else f"${m['cost']:.2f}"
        print(f"\n  {m['model']}：{m['calls']} 次 · cache 讀 {m['cache_read']:,} · "
              f"寫 {m['cache_write']:,} · 輸出 {m['output']:,} · {cost}")


def main() -> None:
    parser = argparse.ArgumentParser(description="從 .mitm capture 算線上流量報表")
    parser.add_argument("capture",
                        help="一場的資料夾（~/ncr/mitm/<session-id>/），或直接指 .mitm")
    parser.add_argument("--json", action="store_true", help="輸出 JSON 而非 HTML")
    parser.add_argument("--out", help="HTML 輸出路徑（預設與 capture 同名 .html）")
    parser.add_argument("--open", action="store_true", help="產生後開啟")
    args = parser.parse_args()

    target = Path(args.capture).expanduser()
    if not target.exists():
        raise SystemExit(f"找不到 {target}")
    path = resolve_capture(target)

    rows = load_flows(str(path))
    if not rows:
        raise SystemExit(f"{path} 裡沒有任何 flow。錄製那一場是不是被 fail-closed 跳過了？")
    annotate_repeat(rows)
    summary = summarize(rows)

    if args.json:
        json.dump(summary, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return

    out = Path(args.out).expanduser() if args.out else path.with_suffix(".html")
    sidecars = load_sidecars(path)
    out.write_text(build_page(path.name, summary, rows, sidecars), encoding="utf-8")
    print_terminal(summary)
    print(f"\n報表：{out}")
    if args.open:
        opener = "open" if sys.platform == "darwin" else "xdg-open"
        subprocess.run([opener, str(out)], check=False)


if __name__ == "__main__":
    main()
