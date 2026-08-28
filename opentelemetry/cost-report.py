# /// script
# requires-python = ">=3.11"
# ///
"""每角色的精確成本：從 Claude Code 的 session transcript（JSONL）算錢，按 subagent 角色分組。

telemetry-report.py（同資料夾）管時間與 token（來源是 Jaeger 的 trace，成本只能估）；
這支管錢（來源是 transcript 的 usage，逐請求精確）。兩支合起來才是完整的分析層。

用法：
    uv run opentelemetry/cost-report.py                          # 最新改動的 session（掃所有 transcript 根，見 transcript_roots）
    uv run opentelemetry/cost-report.py <session.jsonl 或其目錄>  # 指定 session（目錄也可給含多場的專案目錄，取最新）

角色的認法：session 目錄下 subagents/<agent-id>.meta.json 的 agentType 欄位，
對上同名 .jsonl 的 usage。主對話檔算主 agent。

去重規則（重要，算錯就是這裡）：streaming 期間同一則訊息會寫進多行、output_tokens
遞增，同一個 (requestId, message.id) 要取「最終值」——取第一行會少算好幾倍。
ccusage 也是這樣做的。

計算方式已與 ccusage 對帳驗證：同一個 session（含 subagents/）由本腳本與
`CLAUDE_CONFIG_DIR=<dir> npx ccusage session --json` 各算一次，
input / output / cache-read / cache-write 四項 token 逐位一致，
總金額一致到小數第 8 位（驗證於 2026-08-06，Claude Code 2.1.222）。

費率來源：**LiteLLM 的 `model_prices_and_context_window.json`**（ccusage 也是抓這一份，
所以兩邊對帳才對得起來）。抓得到就用線上的，抓不到退回檔案裡的快照，報表會標明用了哪個。
模型兩邊都查不到就只報 token、金額標「無牌價」，不要猜。

⚠ **不要把快照當唯一真相。** 這張表原本是寫死的，2026-08-10 Anthropic 宣布 Sonnet 5 推廣價
永久維持（原訂 9/1 調成 3/15 的那次取消），寫死的那份當場高估 Sonnet 1.5 倍，而報表照樣
印得理直氣壯。價目表本身就是會動的東西，跟著上游走才不會沉默地算錯。

cache 的費率仍由 input 派生（read = 0.1×、寫入 5 分鐘 TTL = 1.25×、1 小時 TTL = 2×）：
那組比例是官方的定價規則、不是每個模型各自的數字，而且與 ccusage 對帳一致的算法是這一版。
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time
import unicodedata
import urllib.request
from collections import defaultdict
from contextlib import suppress

# 上游費率表：ccusage 也是抓這一份，兩邊對帳才有意義。
LITELLM_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main/"
    "model_prices_and_context_window.json"
)
LITELLM_CACHE = os.path.expanduser("~/.cache/ncr-litellm-prices.json")
LITELLM_TTL = 24 * 3600  # 快取多久算新鮮；過期就重抓，抓不到照樣用舊的

# 主要 AI agent 在報表裡的顯示名稱，同時**也是聚合用的 key**。
# ⚠ 這是一個常數而不是散落的字面值，因為它同時是顯示名稱與 join key：
#   `session-report.py` 拿 trace 算出來的角色（`role_of` 的 fallback）去對 transcript
#   算出來的角色（`role_files` 的 key），兩邊只要有一邊沒改到，同一個角色就會裂成兩列，
#   而且不會有任何錯誤訊息。`session-report.py` 直接引用這一份（它本來就載入本模組）。
# ⚠ 2026-08-24 改名（沿革見該次 commit）。舊名借用了作業系統的 thread 說法，但這裡指的
#   是主要的那個 AI agent，台灣讀者在這個語境也不會用那個詞。
MAIN_ROLE = "主 agent"

# {模型字串前綴: (input, output) per MTok}。**這是 fallback，不是真相**——連不到上游
# （離線、GitHub 掛掉、公司網路擋住）時才會用到，報表會標明。cache 費率由 input 派生。
# 比對取「最長命中前綴」，不吃清單順序——靠排序的話，日後加一個互為前綴的
# 項目（如 claude-opus-4 vs claude-opus-4-5）就是等著被踩的陷阱。
# ⚠ 快照日期：2026-08-11。Sonnet 5 是 2/10（推廣價於 2026-08-10 宣布永久維持）。
SNAPSHOT: list[tuple[str, tuple[float, float]]] = [
    ("claude-haiku-4-5", (1.0, 5.0)),
    ("claude-sonnet-4", (3.0, 15.0)),
    ("claude-sonnet-5", (2.0, 10.0)),
    ("claude-opus-4", (5.0, 25.0)),
    ("claude-opus-5", (5.0, 25.0)),
    ("claude-fable-5", (10.0, 50.0)),
]
RATES: list[tuple[str, tuple[float, float]]] = list(SNAPSHOT)
RATES_SOURCE = "快照"


def _load_litellm(
    url: str = LITELLM_URL, cache: str = LITELLM_CACHE, ttl: int = LITELLM_TTL
) -> dict | None:
    """把上游費率表讀進來（有快取）。任何失敗都回 None，讓呼叫端退回快照。

    ⚠ **不可以讓這一步弄死報表。** 它是價格來源的升級，不是必要條件；離線的人照樣要
      算得出東西，只是標成用了快照。
    ⚠ 抓不到時**先用過期的快取**再退快照：上游的舊資料仍然比檔案裡手寫的那份新。
    """

    def _read(path: str) -> dict | None:
        with suppress(Exception), open(path, encoding="utf-8") as f:
            return json.load(f)
        return None

    with suppress(Exception):
        if os.path.isfile(cache) and (time.time() - os.path.getmtime(cache)) < ttl:
            fresh = _read(cache)
            if fresh is not None:
                return fresh
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            raw = r.read().decode("utf-8")
        data = json.loads(raw)
    except Exception:  # noqa: BLE001
        return _read(cache)
    with suppress(Exception):
        os.makedirs(os.path.dirname(cache), exist_ok=True)
        with open(cache, "w", encoding="utf-8") as f:
            f.write(raw)
    return data


def _rates_from_litellm(data: dict) -> list[tuple[str, tuple[float, float]]]:
    """挑出第一方 Claude 的條目，轉成本腳本的 (前綴, (in, out)) 形狀。

    ⚠ 只收**不帶 provider 前綴**的鍵（`claude-sonnet-5`），不收 `vertex_ai/…`、
      `us.anthropic.…`、`azure_ai/…`：那些是各雲的區域價（實測會貴 10%），而 transcript
      裡的 model 字串是第一方的名字。混進去會讓最長前綴比對挑到錯的價。
    """
    out: list[tuple[str, tuple[float, float]]] = []
    for key, v in data.items():
        if not isinstance(v, dict) or not key.startswith("claude-"):
            continue
        i, o = v.get("input_cost_per_token"), v.get("output_cost_per_token")
        if not isinstance(i, (int, float)) or not isinstance(o, (int, float)):
            continue
        out.append((key, (i * 1e6, o * 1e6)))
    return out


def refresh_rates(data: dict | None = None, offline: bool = False) -> str:
    """把 RATES 換成上游的（拿不到就維持快照）。回傳這次用的來源，供報表標示。

    ⚠ **「使用者要離線」與「連不到上游」要分開講。** 兩者都用快照，但一個是選擇、一個是
      故障；印同一句話會讓真的連不到的那次看起來像是自己選的（第一版就是這樣寫的）。
    """
    global RATES, RATES_SOURCE
    if not offline and data is None:
        data = _load_litellm()
    upstream = [] if offline else (_rates_from_litellm(data) if data else [])
    if upstream:
        # 上游沒收錄的型號仍由快照兜底（例如剛發、還沒進 LiteLLM 的）
        have = {k for k, _ in upstream}
        RATES = upstream + [(k, r) for k, r in SNAPSHOT if k not in have]
        RATES_SOURCE = "LiteLLM 上游"
    else:
        RATES = list(SNAPSHOT)
        RATES_SOURCE = "快照（指定離線）" if offline else "快照（連不到上游）"
    return RATES_SOURCE


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
                seen[(e.get("requestId"), m.get("id"))] = (
                    m["model"],
                    normalize_usage(u),
                )

    agg: dict[str, dict] = defaultdict(
        lambda: {"in": 0, "out": 0, "cr": 0, "cw5m": 0, "cw1h": 0, "n": 0}
    )
    for model, usage in seen.values():
        a = agg[model]
        a["n"] += 1
        for k in ("in", "out", "cr", "cw5m", "cw1h"):
            a[k] += usage[k]
    return agg


# --------------------------------------------------------------------------
# session 的落點有兩條路徑，而且**不是同一個 $HOME**
#
#   · run script 起的容器（TUI 那條）：host 的 $HOME 原樣掛進去，所以 transcript 在
#     `~/.claude/projects`、審查報告的 archive 在 `~/ncr`。
#   · 網頁（claude-pty）起的：ADR 0014 之後 session 的 `~/.claude` 與 `~/ncr` 都來自
#     **per-user 空間** `${CLAUDE_PTY_SPACE}/user-{id}/`，host 這一側長的是
#     `user-{id}/claude/`（沒有那個點）與 `user-{id}/ncr/`。
#
# 只認前者的話，網頁開的場次在這裡是**完全看不見**的：不會報錯，只是 token 與成本
# 那幾欄整片空白、report.json 永遠顯示「未封存」，而報表本身看起來一切正常。
#
# space 的位置可以在 deploy/.env 改，而那份 .env 不會出現在跑報表的這個 shell 的環境裡，
# 所以：先看 NCR_SPACE / CLAUDE_PTY_SPACE，沒有才退回 `~/claude-pty-space*` 的猜測。
# 猜不到就把 space 用環境變數指給它——不猜第二層。
# --------------------------------------------------------------------------


# 派遣時沒有具名 agent 就會落到這個值：skill 的 agent 定義要在 `~/.claude/agents/`
# 才叫得動，不在的話 SKILL.md 明寫「改用 general-purpose subagent 並帶對應的 prompt」。
# 於是五個角色會**全部**掛成同一個字串，報表就拆不開了。
GENERIC_AGENT_TYPE = "general-purpose"


def role_label(meta: dict) -> str:
    """一個 subagent 在報表裡叫什麼。

    `agentType` 具名（`ncr-scan-trivy`…）就用它。掉進 general-purpose 那條 fallback 時
    它對每個角色都是同一個字，唯一分得開的是 `description`（"Trivy scan"、"Lint scan"…）
    ——那是派遣當下寫進 meta 的，不是猜的。

    兩個都印，因為兩件事都要看得見：**是誰**（description），以及**它是 fallback**
    （agentType）。只印後者就是現在這樣五個塌成一列；只印前者則會讓「agent 沒安裝」
    這個真正的病灶從報表上消失，而那正是要修的東西。
    """
    at = (meta.get("agentType") or "").strip()
    desc = (meta.get("description") or "").strip()
    if at and at != GENERIC_AGENT_TYPE:
        return at
    if desc:
        return f"{at or '?'}：{desc}"
    return "（未標名的 subagent）"


def user_spaces() -> list[str]:
    """claude-pty 的 per-user 空間（`.../user-{id}` 那一層）。"""
    space = os.environ.get("NCR_SPACE") or os.environ.get("CLAUDE_PTY_SPACE")
    roots = (
        [os.path.expanduser(space)]
        if space
        else sorted(glob.glob(os.path.expanduser("~/claude-pty-space*")))
    )
    out: list[str] = []
    for r in roots:
        out.extend(sorted(glob.glob(os.path.join(r, "user-*"))))
    return [d for d in out if os.path.isdir(d)]


def transcript_roots() -> list[str]:
    """所有存放 session transcript 的 `projects` 目錄（host 的，加上每個 per-user 的）。"""
    roots = [os.path.expanduser("~/.claude/projects")]
    roots += [os.path.join(u, "claude", "projects") for u in user_spaces()]
    return [r for r in roots if os.path.isdir(r)]


def archive_roots() -> list[str]:
    """所有審查報告 archive 的根（skill 的 workspace-paths 說的那個 `$HOME/ncr`）。"""
    roots = [os.path.expanduser("~/ncr")]
    roots += [os.path.join(u, "ncr") for u in user_spaces()]
    return [r for r in roots if os.path.isdir(r)]


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
                sys.exit(
                    f"{arg} 裡沒有 .jsonl（給 session 目錄或含 session 的專案目錄）"
                )
            main = max(jsonls, key=os.path.getmtime)
            if len(jsonls) > 1:
                print(
                    f"（目錄含 {len(jsonls)} 場，取 mtime 最新：{os.path.basename(main)}）"
                )
            base = os.path.splitext(main)[0]
            return main, base if os.path.isdir(base) else ""
        base = os.path.splitext(arg)[0]
        return arg, base if os.path.isdir(base) else ""
    # 沒指定：掃所有 transcript 根（host + 每個 per-user 空間），拿最新改動的 session
    roots = transcript_roots()
    cands = [g for r in roots for g in glob.glob(os.path.join(r, "*", "*.jsonl"))]
    if not cands:
        sys.exit(
            "找不到任何 session transcript。已找過："
            + "、".join(roots or ["~/.claude/projects"])
            + "（claude-pty 的 per-user 空間不在預設位置時，"
            "用 NCR_SPACE 或 CLAUDE_PTY_SPACE 指給它）"
        )
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
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "session", nargs="?", help="session 的 .jsonl 或其目錄；不給就拿最新的"
    )
    ap.add_argument(
        "--offline", action="store_true", help="不連上游費率表，直接用檔案裡的快照"
    )
    args = ap.parse_args()

    # 費率先就位再算錢。連不到上游不會中斷，只是報表標成用了快照。
    source = refresh_rates(offline=args.offline)

    main_jsonl, session_dir = find_session(args.session)
    print(f"session：{os.path.basename(main_jsonl)}")

    # 角色 → 該角色的 jsonl 清單。新格式 subagents/ 每個 agent 一份 + meta 標身分；
    # 沒有 subagents/ 的舊格式只有主檔，只能算 session 總額（如實印出，不硬拆）。
    roles: dict[str, list[str]] = {MAIN_ROLE: [main_jsonl]}
    sub_dir = os.path.join(session_dir, "subagents") if session_dir else ""
    if sub_dir and os.path.isdir(sub_dir):
        for meta_path in sorted(glob.glob(os.path.join(sub_dir, "*.meta.json"))):
            with open(meta_path) as f:
                meta = json.load(f)
            jsonl = meta_path.replace(".meta.json", ".jsonl")
            if os.path.exists(jsonl):
                role = role_label(meta)
                roles.setdefault(role, []).append(jsonl)
    else:
        print("（沒有 subagents/ 目錄：舊格式或無派遣，只有 session 總額可算）")

    # 角色欄寬跟著實際內容走。寫死 30 的時候，agent 沒安裝而落到
    # `general-purpose：Report quality check` 那種長名字會直接把 model 欄擠掉，
    # 而 lpad 只補不截，所以症狀是整列欄位錯開、不是被截斷。
    role_w = max(30, max(_w(r) for r in roles) + 2)
    widths = (role_w, 24, 10, 10, 12, 12, 11)
    hdr = (
        lpad("角色", widths[0])
        + lpad("model", widths[1])
        + "".join(
            rpad(h, w)
            for h, w in zip(
                ("輸入", "輸出", "cache 讀", "cache 寫", "成本"), widths[2:]
            )
        )
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
    # ⚠ 來源要印出來。價目表會變，而「這份報表用的是哪一版費率」是事後對帳唯一的線索
    #   ——不印的話，離線那次算出來的舊價數字看起來跟線上那次一模一樣。
    print(
        f"（金額 = token × 費率，來源：{source}；與 ccusage 對帳一致。"
        "企業合約或特殊費率另計。）"
    )


if __name__ == "__main__":
    main()
