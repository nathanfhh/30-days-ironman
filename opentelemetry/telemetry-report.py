# /// script
# requires-python = ">=3.11"
# ///
"""每角色的時間與 token 歸因報表：從 Jaeger 讀 Claude Code 的 traces，按 subagent 角色分組。

用法：
    uv run opentelemetry/telemetry-report.py                       # 撈最近 24 小時，逐 session 列出
    uv run opentelemetry/telemetry-report.py --session 45f130ea    # 只看 session id 以此開頭的那場
    uv run opentelemetry/telemetry-report.py --lookback 72         # 撈最近 72 小時
    uv run opentelemetry/telemetry-report.py --file a.json b.json  # 離線：直接餵 query API 的 JSON

角色的認法：派遣 span（tool_name = Task/Agent）帶著 subagent_type 標籤，其餘 span 沿
CHILD_OF 父子鏈往上走、撞到哪個派遣 span 就屬於哪個角色；走不到派遣 span 的屬於主線程。
不能只看 span 上的 agent_id——實測派遣 span 自帶的 agent_id 會指錯人，父子鏈才可靠。

錢不在這裡：trace 記的是 token 不是帳單。精確的每角色成本改從 transcript 算
（session 目錄的 subagents/*.meta.json 有 agentType，把該檔 usage 乘上牌價即可，
ccusage 也吃得到）。這支只負責時間與 token。
"""

from __future__ import annotations

import argparse
import json
import sys
import unicodedata
import urllib.parse
import urllib.request
from collections import defaultdict

DISPATCH_TOOLS = {"Task", "Agent"}


def fetch_traces(base: str, service: str, lookback_h: int, limit: int) -> list[dict]:
    q = urllib.parse.urlencode(
        {"service": service, "lookback": f"{lookback_h}h", "limit": str(limit)}
    )
    url = f"{base.rstrip('/')}/api/traces?{q}"
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            return json.load(r).get("data", [])
    except OSError as e:
        sys.exit(
            f"連不上 Jaeger（{url}）：{e}\n它在跑嗎？docker compose -f opentelemetry/jaeger-compose.yaml up -d"
        )


def load_files(paths: list[str]) -> list[dict]:
    out = []
    for p in paths:
        try:
            with open(p) as f:
                d = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            sys.exit(f"讀不了 {p}：{e}")
        # query API 的完整回應（{"data": [...]}）與單一 trace 物件都收
        out.extend(d["data"] if isinstance(d, dict) and "data" in d else [d])
    return out


def flatten(traces: list[dict]) -> tuple[dict, dict]:
    """回 (all_spans by spanID, resource attrs by session id)。"""
    spans: dict[str, dict] = {}
    resources: dict[str, dict] = {}
    for tr in traces:
        # 一個 trace 可以有多個 process；resource attrs 要用「該 span 自己的」process 查，
        # 不能拿整個 trace 最後一個 process 充數
        procs = {
            pid: {t["key"]: t["value"] for t in proc.get("tags", [])}
            for pid, proc in tr.get("processes", {}).items()
        }
        for s in tr.get("spans", []):
            tags = {t["key"]: t["value"] for t in s.get("tags", [])}
            tags["_start"] = s["startTime"]  # µs
            tags["_dur"] = s["duration"]  # µs
            tags["_parent"] = next(
                (
                    r["spanID"]
                    for r in s.get("references", [])
                    if r["refType"] == "CHILD_OF"
                ),
                None,
            )
            spans[s["spanID"]] = tags
            sid = tags.get("session.id")
            if sid and sid not in resources:
                resources[sid] = procs.get(s.get("processID"), {})
    return spans, resources


def _w(s: str) -> int:
    """終端顯示寬度：全形字算 2，不然 CJK 表頭一定歪。"""
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def lpad(s: str, width: int) -> str:
    return s + " " * max(0, width - _w(s))


def rpad(s: str, width: int) -> str:
    return " " * max(0, width - _w(s)) + s


def role_of(span: dict, spans: dict, dispatch: dict) -> str:
    """沿父子鏈往上找派遣 span；找不到就是主線程。"""
    p, seen = span.get("_parent"), set()
    while p and p in spans and p not in seen:
        seen.add(p)
        if p in dispatch:
            return dispatch[p].get("subagent_type") or "（未標名的 subagent）"
        p = spans[p]["_parent"]
    return "主線程"


def report(spans: dict, resources: dict, session_prefix: str | None) -> None:
    by_session: dict[str, list[dict]] = defaultdict(list)
    for s in spans.values():
        sid = s.get("session.id")
        if sid:
            by_session[sid].append(s)

    wanted = {
        sid: ss
        for sid, ss in by_session.items()
        if not session_prefix or sid.startswith(session_prefix)
    }
    if not wanted:
        have = "、".join(s[:8] for s in by_session) or "（一場都沒有）"
        sys.exit(f"沒有符合的 session。這批資料裡有：{have}")

    for sid, ss in sorted(
        wanted.items(), key=lambda kv: min(x["_start"] for x in kv[1])
    ):
        res = resources.get(sid, {})
        session_spans = {k: v for k, v in spans.items() if v.get("session.id") == sid}
        dispatch = {
            k: v
            for k, v in session_spans.items()
            if v.get("span.type") == "tool" and v.get("tool_name") in DISPATCH_TOOLS
        }

        agg: dict[str, dict] = defaultdict(
            lambda: {
                "first": None,
                "last": 0,
                "llm_us": 0,
                "out": 0,
                "cr": 0,
                "cw": 0,
                "n_llm": 0,
            }
        )
        for s in session_spans.values():
            r = role_of(s, session_spans, dispatch)
            a = agg[r]
            end = s["_start"] + s["_dur"]
            a["first"] = (
                s["_start"] if a["first"] is None else min(a["first"], s["_start"])
            )
            a["last"] = max(a["last"], end)
            if s.get("span.type") == "llm_request":
                a["n_llm"] += 1
                a["llm_us"] += s["_dur"]
                a["out"] += int(s.get("output_tokens", 0))
                a["cr"] += int(s.get("cache_read_tokens", 0))
                a["cw"] += int(s.get("cache_creation_tokens", 0))

        t0 = min(a["first"] for a in agg.values())
        t1 = max(a["last"] for a in agg.values())
        total_s = (t1 - t0) / 1e6
        labels = [
            f"{k}={res[k]}" for k in ("experiment", "skill.version") if res.get(k)
        ]
        print(f"\nsession {sid[:8]}…" + (f" · {' · '.join(labels)}" if labels else ""))
        print(f"整場 wall-clock：{int(total_s // 60)} 分 {int(total_s % 60):02d} 秒\n")

        widths = (28, 8, 10, 10, 12, 10)
        hdr = lpad("角色", widths[0]) + "".join(
            rpad(h, w)
            for h, w in zip(
                ("Wall Time", "LLM 耗時", "輸出 tok", "cache 讀", "cache 寫"),
                widths[1:],
            )
        )
        print(hdr)
        print("-" * sum(widths))
        ordered = sorted(
            agg.items(), key=lambda kv: kv[1]["last"] - kv[1]["first"], reverse=True
        )
        for role, a in ordered:
            wall = (a["last"] - a["first"]) / 1e6
            cells = (
                f"{wall:.0f}s",
                f"{a['llm_us'] / 1e6:.0f}s",
                f"{a['out']:,}",
                f"{a['cr']:,}",
                f"{a['cw']:,}",
            )
            print(
                lpad(role, widths[0])
                + "".join(rpad(c, w) for c, w in zip(cells, widths[1:]))
            )
        print(
            "\n（Wall Time = 該角色首末 span 的時距；LLM 耗時 = llm_request span 加總，"
            "有平行請求時會大於 wall。）"
        )


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--jaeger", default="http://localhost:16686", help="Jaeger query 的位址"
    )
    ap.add_argument("--service", default="claude-code")
    ap.add_argument("--lookback", type=int, default=24, help="往回撈幾小時（預設 24）")
    ap.add_argument("--limit", type=int, default=200, help="最多撈幾個 trace")
    ap.add_argument("--session", help="只看 session id 以此開頭的場次")
    ap.add_argument(
        "--file", nargs="+", help="不連 Jaeger，直接讀 query API 匯出的 JSON 檔"
    )
    args = ap.parse_args()

    traces = (
        load_files(args.file)
        if args.file
        else fetch_traces(args.jaeger, args.service, args.lookback, args.limit)
    )
    if not traces:
        sys.exit(
            "撈不到任何 trace。確認 telemetry 有開（run wrapper 啟動時會印 📊 那行）。"
        )
    spans, resources = flatten(traces)
    report(spans, resources, args.session)


if __name__ == "__main__":
    main()
