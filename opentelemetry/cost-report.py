# /// script
# requires-python = ">=3.11"
# ///
"""每角色的精確成本：從 Claude Code 的 session transcript（JSONL）算錢，按 subagent 角色分組。

telemetry-report.py（同資料夾）管時間與 token（來源是 Jaeger 的 trace，成本只能估）；
這支管錢（來源是 transcript 的 usage，逐請求精確）。兩支合起來才是完整的分析層。

用法：
    uv run opentelemetry/cost-report.py                          # 最新改動的 session（掃 ~/.claude/projects 全部專案）
    uv run opentelemetry/cost-report.py <session.jsonl 或其目錄>  # 指定 session（目錄也可給含多場的專案目錄，取最新）

角色的認法：session 目錄下 subagents/<agent-id>.meta.json 的 agentType 欄位，
對上同名 .jsonl 的 usage。主對話檔算主線程。

去重規則（重要，算錯就是這裡）：streaming 期間同一則訊息會寫進多行、output_tokens
遞增，同一個 (requestId, message.id) 要取「最終值」——取第一行會少算好幾倍。
ccusage 也是這樣做的。

計算方式已與 ccusage 對帳驗證：同一個 session（含 subagents/）由本腳本與
`CLAUDE_CONFIG_DIR=<dir> npx ccusage session --json` 各算一次，
input / output / cache-read / cache-write 四項 token 逐位一致，
總金額一致到小數第 8 位（驗證於 2026-08-06，Claude Code 2.1.222）。

牌價快照（2026-08，USD per MTok；cache read = 0.1×input、寫入 5 分鐘 TTL = 1.25×、
1 小時 TTL = 2×——這組比例是官方定價規則，跟著 input 價走）。模型不在表上就只報
token、金額標「無牌價」，不要猜。
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import unicodedata
from collections import defaultdict

# {模型字串前綴: (input, output) per MTok}。cache 費率由 input 派生。
# 比對取「最長命中前綴」，不吃清單順序——靠排序的話，日後加一個互為前綴的
# 項目（如 claude-opus-4 vs claude-opus-4-5）就是等著被踩的陷阱。
RATES: list[tuple[str, tuple[float, float]]] = [
    ("claude-haiku-4-5", (1.0, 5.0)),
    ("claude-sonnet-4", (3.0, 15.0)),
    ("claude-sonnet-5", (3.0, 15.0)),   # 正式牌價；促銷期實際帳單可能更低
    ("claude-opus-4", (5.0, 25.0)),
    ("claude-opus-5", (5.0, 25.0)),
    ("claude-fable-5", (10.0, 50.0)),
]


def rate_for(model: str) -> tuple[float, float] | None:
    best: tuple[float, float] | None = None
    best_len = -1
    for prefix, r in RATES:
        if model.startswith(prefix) and len(prefix) > best_len:
            best, best_len = r, len(prefix)
    return best


def cost_usd(model: str, u: dict) -> float | None:
    r = rate_for(model)
    if r is None:
        return None
    inp, out = r
    return (
        u["in"] * inp
        + u["out"] * out
        + u["cr"] * inp * 0.1
        + u["cw5m"] * inp * 1.25
        + u["cw1h"] * inp * 2.0
    ) / 1e6


def normalize_usage(u: dict) -> dict:
    """把 API 回的 usage 攤成 cost_usd 吃的五個欄位。

    抽成函式是因為不只這支在用：mitm/wire_report.py 從線上流量裡撿到的 usage 也走
    這裡。cache 寫入的 5m/1h 拆分規則只該有一份，兩邊各寫一份遲早會分岔。
    """
    cw = u.get("cache_creation") or {}
    return {
        "in": u.get("input_tokens", 0),
        "out": u.get("output_tokens", 0),
        "cr": u.get("cache_read_input_tokens", 0),
        # 有 ephemeral 細分就用；沒有（舊格式）就整包當 5m 算（最低價，
        # 可能低估——舊格式想保守請自行視為區間）
        "cw5m": cw.get("ephemeral_5m_input_tokens", 0)
        or (0 if cw else u.get("cache_creation_input_tokens", 0)),
        "cw1h": cw.get("ephemeral_1h_input_tokens", 0),
    }


def tally(paths: list[str]) -> dict:
    """讀多個 JSONL，去重後按 model 加總 usage。"""
    # (requestId, message.id) → 最後一次出現的 (model, usage)；後寫的蓋前面的
    seen: dict[tuple, tuple[str, dict]] = {}
    for p in paths:
        with open(p) as f:
            for line in f:
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                m = e.get("message") or {}
                u = m.get("usage")
                if not u or not m.get("model"):
                    continue
                seen[(e.get("requestId"), m.get("id"))] = (m["model"], normalize_usage(u))

    agg: dict[str, dict] = defaultdict(
        lambda: {"in": 0, "out": 0, "cr": 0, "cw5m": 0, "cw1h": 0, "n": 0}
    )
    for model, usage in seen.values():
        a = agg[model]
        a["n"] += 1
        for k in ("in", "out", "cr", "cw5m", "cw1h"):
            a[k] += usage[k]
    return agg


def find_session(arg: str | None) -> tuple[str, str]:
    """回 (主 jsonl 路徑, session 目錄或空字串)。

    目錄參數吃兩種：session 目錄本身（旁邊有同名 .jsonl）、含多場的專案目錄
    （取 mtime 最新的一場並印出選了誰——字母序第一場既不是「最新」也沒人知道選了誰）。
    """
    if arg:
        if os.path.isdir(arg):
            sib = arg.rstrip("/") + ".jsonl"
            if os.path.isfile(sib):
                return sib, arg.rstrip("/")
            jsonls = glob.glob(os.path.join(arg, "*.jsonl"))
            if not jsonls:
                sys.exit(f"{arg} 裡沒有 .jsonl（給 session 目錄或含 session 的專案目錄）")
            main = max(jsonls, key=os.path.getmtime)
            if len(jsonls) > 1:
                print(f"（目錄含 {len(jsonls)} 場，取 mtime 最新：{os.path.basename(main)}）")
            base = os.path.splitext(main)[0]
            return main, base if os.path.isdir(base) else ""
        base = os.path.splitext(arg)[0]
        return arg, base if os.path.isdir(base) else ""
    # 沒指定：掃 ~/.claude/projects 全部專案，拿最新改動的 session
    cands = glob.glob(os.path.expanduser("~/.claude/projects/*/*.jsonl"))
    if not cands:
        sys.exit("找不到任何 session transcript（~/.claude/projects 是空的）")
    main = max(cands, key=os.path.getmtime)
    base = os.path.splitext(main)[0]
    return main, base if os.path.isdir(base) else ""


def _w(s: str) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def lpad(s: str, width: int) -> str:
    return s + " " * max(0, width - _w(s))


def rpad(s: str, width: int) -> str:
    return " " * max(0, width - _w(s)) + s


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("session", nargs="?", help="session 的 .jsonl 或其目錄；不給就拿最新的")
    args = ap.parse_args()

    main_jsonl, session_dir = find_session(args.session)
    print(f"session：{os.path.basename(main_jsonl)}")

    # 角色 → 該角色的 jsonl 清單。新格式 subagents/ 每個 agent 一份 + meta 標身分；
    # 沒有 subagents/ 的舊格式只有主檔，只能算 session 總額（如實印出，不硬拆）。
    roles: dict[str, list[str]] = {"主線程": [main_jsonl]}
    sub_dir = os.path.join(session_dir, "subagents") if session_dir else ""
    if sub_dir and os.path.isdir(sub_dir):
        for meta_path in sorted(glob.glob(os.path.join(sub_dir, "*.meta.json"))):
            with open(meta_path) as f:
                meta = json.load(f)
            jsonl = meta_path.replace(".meta.json", ".jsonl")
            if os.path.exists(jsonl):
                role = meta.get("agentType") or "（未標名的 subagent）"
                roles.setdefault(role, []).append(jsonl)
    else:
        print("（沒有 subagents/ 目錄：舊格式或無派遣，只有 session 總額可算）")

    widths = (30, 24, 10, 10, 12, 12, 11)
    hdr = lpad("角色", widths[0]) + lpad("model", widths[1]) + "".join(
        rpad(h, w) for h, w in zip(("輸入", "輸出", "cache 讀", "cache 寫", "成本"), widths[2:])
    )
    print("\n" + hdr)
    print("-" * sum(widths))

    total_cost, total_known = 0.0, True
    for role, paths in roles.items():
        for model, a in sorted(tally(paths).items()):
            # Claude Code 的 <synthetic> 訊息 usage 全 0——不進表、也不觸發
            # 「不含無牌價的模型」免責（一毛錢都沒漏，免責掛了反而失真；
            # ccusage 也是跳過它，對帳宣稱才對得上）
            if model == "<synthetic>" and not any(
                a[k] for k in ("in", "out", "cr", "cw5m", "cw1h")
            ):
                continue
            c = cost_usd(model, a)
            if c is None:
                total_known = False
                cost_s = "無牌價"
            else:
                total_cost += c
                cost_s = f"${c:.4f}"
            cells = (
                f"{a['in']:,}",
                f"{a['out']:,}",
                f"{a['cr']:,}",
                f"{a['cw5m'] + a['cw1h']:,}",
                cost_s,
            )
            print(
                lpad(role, widths[0])
                + lpad(model.removeprefix("claude-"), widths[1])
                + "".join(rpad(x, w) for x, w in zip(cells, widths[2:]))
            )

    print(
        f"\n總成本：${total_cost:.4f}" + ("" if total_known else "（不含無牌價的模型）")
    )
    print("（金額 = token × 牌價快照，見腳本開頭；與 ccusage 對帳一致。促銷價或企業合約另計。）")


if __name__ == "__main__":
    main()
