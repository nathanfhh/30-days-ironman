# /// script
# requires-python = ">=3.11"
# ///
"""單一審查場次的 HTML 報表：結論、時間軸甘特、每角色時間×token×成本×快取命中。

用法：
    uv run opentelemetry/session-report.py <session-id 前綴>
    uv run opentelemetry/session-report.py <session-id 前綴> --out 報表.html --open

資料源紀律（三源各取所長，不互相冒充）：
- 結論、MR 標題、掃描狀態 ← report.json（archive 裡 session 對得上的那份；
  拿不到就如實顯示「未封存」，不隱藏）
- 時間與甘特 ← Jaeger trace（角色歸因走 span 父子鏈，同 telemetry-report.py）
- token、成本、快取命中率 ← session transcript（重用 cost-report.py 的
  tally 與牌價；命中率 = cache 讀 / (輸入 + cache 讀 + cache 寫)）

甘特圖是離線互動頁：左欄角色名固定、右側橫向捲動；＋／－（或 ⌘/Ctrl＋滾輪）縮放；
span hover 顯示詳情。連續 GAP_MIN 秒以上全域無 span 的空窗壓縮成斷軸（⫽ n 分），
不然 resume 過的 session 會把時間軸拉爆。
"""

from __future__ import annotations

import argparse
import glob
import html
import importlib.util
import json
import os
import subprocess
import sys
import urllib.request
from collections import defaultdict
from pathlib import Path

GAP_MIN = 120  # 全域空窗超過這秒數 → 斷軸

# 重用 cost-report 的 tally / 牌價（同資料夾的兄弟腳本；檔名帶連字號，走路徑載入）
_spec = importlib.util.spec_from_file_location(
    "_ncr_costmod", Path(__file__).resolve().parent / "cost-report.py")
costmod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(costmod)

DISPATCH_TOOLS = {"Task", "Agent"}


# --------------------------------------------------------------------------
# 資料收集
# --------------------------------------------------------------------------


def split_by_session(data: list[dict], sid_prefix: str) -> tuple[dict[str, dict], set[str]]:
    """純函式：query API 的 data → {命中前綴的 sid: 該場 spans}，外加這批資料的全部 sid。

    一個 sid 一組——命中多場時讓呼叫端明確處理，不靜默合併
    （合併會產出時間軸一場、metadata 另一場的自相矛盾報表）。
    """
    by_sid: dict[str, dict] = defaultdict(dict)
    all_sids: set[str] = set()
    for tr in data:
        for s in tr.get("spans", []):
            t = {x["key"]: x["value"] for x in s.get("tags", [])}
            sid = t.get("session.id", "")
            if sid:
                all_sids.add(sid)
            if not sid.startswith(sid_prefix):
                continue
            t["_start"], t["_dur"] = s["startTime"], s["duration"]
            t["_parent"] = next(
                (ref["spanID"] for ref in s.get("references", []) if ref["refType"] == "CHILD_OF"),
                None,
            )
            by_sid[sid][s["spanID"]] = t
    return dict(by_sid), all_sids


def fetch_spans(
    jaeger: str, sid_prefix: str, lookback: str = "168h", limit: int = 500
) -> tuple[dict[str, dict], set[str], bool]:
    """回 (sid → spans, 這批資料全部 sid, 是否撞到 limit 可能截斷)。"""
    url = f"{jaeger.rstrip('/')}/api/traces?service=claude-code&lookback={lookback}&limit={limit}"
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            data = json.load(r).get("data", [])
    except OSError as e:
        sys.exit(f"連不上 Jaeger（{url}）：{e}")
    by_sid, all_sids = split_by_session(data, sid_prefix)
    return by_sid, all_sids, len(data) >= limit


def role_of(span: dict, spans: dict, dispatch: dict) -> str:
    p, seen = span.get("_parent"), set()
    while p and p in spans and p not in seen:
        seen.add(p)
        if p in dispatch:
            return dispatch[p].get("subagent_type") or "（未標名的 subagent）"
        p = spans[p]["_parent"]
    return "主線程"


def collect_roles(spans: dict) -> tuple[dict, list[dict]]:
    """回 (每角色聚合, 甘特用的 span 清單)。純函式，測試從這裡進。"""
    dispatch = {
        k: v
        for k, v in spans.items()
        if v.get("span.type") == "tool" and v.get("tool_name") in DISPATCH_TOOLS
    }
    agg: dict[str, dict] = defaultdict(lambda: {"first": None, "last": 0, "llm_us": 0})
    chart: list[dict] = []
    for s in spans.values():
        r = role_of(s, spans, dispatch)
        a = agg[r]
        end = s["_start"] + s["_dur"]
        a["first"] = s["_start"] if a["first"] is None else min(a["first"], s["_start"])
        a["last"] = max(a["last"], end)
        kind = s.get("span.type")
        if kind == "llm_request":
            a["llm_us"] += s["_dur"]
        if kind in ("llm_request", "tool"):
            chart.append(
                {
                    "role": r,
                    "s": s["_start"],
                    "e": end,
                    "kind": kind,
                    "label": s.get("model") or s.get("tool_name") or kind,
                    "in": int(s.get("input_tokens", 0)),
                    "out": int(s.get("output_tokens", 0)),
                    "cr": int(s.get("cache_read_tokens", 0)),
                    "cw": int(s.get("cache_creation_tokens", 0)),
                }
            )
    return agg, chart


def find_gaps(chart: list[dict], gap_min_s: int = GAP_MIN) -> list[tuple[int, int]]:
    """全域（跨角色）無任何 span 的空窗，微秒座標。純函式。"""
    merged: list[list[int]] = []
    for iv in sorted((c["s"], c["e"]) for c in chart):
        if merged and iv[0] <= merged[-1][1] + 1_000_000:
            merged[-1][1] = max(merged[-1][1], iv[1])
        else:
            merged.append([iv[0], iv[1]])
    return [
        (merged[i][1], merged[i + 1][0])
        for i in range(len(merged) - 1)
        if (merged[i + 1][0] - merged[i][1]) / 1e6 >= gap_min_s
    ]


def hit_rate(tk: dict) -> float | None:
    """快取命中率 = cache 讀 / (輸入 + cache 讀 + cache 寫)。分母 0 回 None。"""
    denom = tk.get("in", 0) + tk.get("cr", 0) + tk.get("cw", 0)
    return tk.get("cr", 0) / denom if denom else None


def collect_transcript(sid: str) -> dict:
    """每角色 token / 成本 / model，來自 transcript。找不到就回空 dict。"""
    hits = glob.glob(os.path.expanduser(f"~/.claude/projects/*/{sid}.jsonl"))
    if not hits:
        return {}
    main_jsonl = hits[0]
    base = os.path.splitext(main_jsonl)[0]
    role_files = {"主線程": [main_jsonl]}
    for mp in glob.glob(os.path.join(base, "subagents", "*.meta.json")):
        with open(mp) as f:
            meta = json.load(f)
        jl = mp.replace(".meta.json", ".jsonl")
        if os.path.exists(jl):
            role_files.setdefault(meta.get("agentType") or "（未標名的 subagent）", []).append(jl)
    out: dict[str, dict] = {}
    for role, files in role_files.items():
        per_model = costmod.tally(files)
        tk = {"in": 0, "out": 0, "cr": 0, "cw": 0, "cost": 0.0, "model": "—"}
        for model, a in per_model.items():
            for k in ("in", "out", "cr"):
                tk[k] += a[k]
            tk["cw"] += a["cw5m"] + a["cw1h"]
            c = costmod.cost_usd(model, a)
            if c is not None:
                tk["cost"] += c
            tk["model"] = model.removeprefix("claude-")
        out[role] = tk
    return out


def find_report_json(t0_us: int, t1_us: int, root: str = "~/ncr") -> dict | None:
    """在 archive 找屬於這個 session 的 report.json。

    report.json 沒有記 session id，只能用時間比對：報告寫檔（含發佈後回寫）發生在
    session 活動範圍內或其後不久，所以判準是「mtime 落在 [t0−1h, t1＋6h] 視窗內、
    且離 session 結束最近的一份」。視窗內沒有 → 回 None——寧可顯示未封存，不亂配。
    """
    lo = t0_us / 1e6 - 3600
    hi = t1_us / 1e6 + 6 * 3600
    best: tuple[float, dict] | None = None
    for p in glob.glob(os.path.expanduser(f"{root.rstrip('/')}/**/*.json"), recursive=True):
        mt = os.path.getmtime(p)
        if not (lo <= mt <= hi):
            continue
        try:
            with open(p) as f:
                d = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        if "conclusion" not in d or "mr" not in d:
            continue
        dist = abs(mt - t1_us / 1e6)
        if best is None or dist < best[0]:
            d["_path"] = p
            best = (dist, d)
    return best[1] if best else None


# --------------------------------------------------------------------------
# 頁面
# --------------------------------------------------------------------------

PAGE = """<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<title>ncr 場次報表｜__SID8__</title>
<style>
:root { --ink:#1a1a1a; --sub:#666; --line:#e4e1da; --accent:#0f766e; --bg:#faf9f6; --card:#fff; }
* { box-sizing:border-box; margin:0; }
body { font-family:-apple-system,"PingFang TC",sans-serif; background:var(--bg); color:var(--ink);
       max-width:1240px; margin:0 auto; padding:44px 32px 80px; line-height:1.7; font-size:16px; }
h1 { font-size:26px; } h2 { font-size:18px; margin:44px 0 14px; padding-top:18px; border-top:1px solid var(--line); }
.meta { color:var(--sub); font-size:14.5px; margin:8px 0 32px; }
.meta code { font-family:ui-monospace,Menlo,monospace; font-size:13.5px; }
.facts { display:grid; grid-template-columns:repeat(3,1fr); gap:14px; }
.fact { background:var(--card); border:1px solid var(--line); border-radius:8px; padding:16px 20px; }
.fact b { display:block; font-size:26px; margin-top:2px; font-variant-numeric:tabular-nums; }
.fact span { color:var(--sub); font-size:13.5px; }
table { width:100%; border-collapse:collapse; font-size:15px; background:var(--card); }
th { text-align:left; color:var(--sub); font-size:13px; padding:9px 12px; border-bottom:1px solid var(--line); }
td { padding:8px 12px; border-bottom:1px solid var(--line); font-variant-numeric:tabular-nums; }
th.n,td.n { text-align:right; font-family:ui-monospace,Menlo,monospace; font-size:14px; }
td.role { font-family:ui-monospace,Menlo,monospace; font-size:14px; }
p.note { color:var(--sub); font-size:14px; margin-top:10px; }
.gantt { display:flex; background:var(--card); border:1px solid var(--line); border-radius:8px; overflow:hidden; }
.gantt .labels { flex:0 0 200px; border-right:1px solid var(--line); padding-top:6px; }
.gantt .labels div { height:34px; line-height:34px; text-align:right; padding-right:12px;
                     font:13px ui-monospace,Menlo,monospace; white-space:nowrap; }
.gantt .scroll { overflow-x:auto; flex:1; }
.zoom { margin:0 0 8px; display:flex; gap:8px; align-items:center; color:var(--sub); font-size:13.5px; }
.zoom button { font-size:15px; width:30px; height:26px; border:1px solid var(--line); background:var(--card);
               border-radius:5px; cursor:pointer; }
#tip { position:fixed; display:none; background:#1f2937; color:#f9fafb; font:12.5px/1.6 ui-monospace,Menlo,monospace;
       padding:8px 11px; border-radius:6px; pointer-events:none; z-index:9; max-width:340px; }
</style></head><body>
<h1>審查場次報表</h1>
<p class="meta">__META__</p>
<div class="facts">__FACTS__</div>
<h2>時間軸</h2>
<div class="zoom"><button id="zi">＋</button><button id="zo">－</button><button id="zr" style="width:auto;padding:0 10px">重置</button><span>⌘/Ctrl＋滾輪縮放 · hover 看詳情</span></div>
<div class="gantt"><div class="labels" id="labels"></div><div class="scroll" id="scroll"><svg id="chart"></svg></div></div>
<h2>每角色：時間 × token × 成本 × 快取</h2>
<table>
<tr><th>角色</th><th>模型</th><th class=n>Wall Time</th><th class=n>LLM 耗時</th><th class=n>輸入</th><th class=n>輸出</th><th class=n>cache 讀</th><th class=n>cache 寫</th><th class=n>快取命中</th><th class=n>成本</th></tr>
__ROWS__
</table>
<p class="note">資料源：結論與 MR 標題 ← report.json（未封存時如實顯示、不隱藏）；時間與甘特 ← Jaeger trace；
token、成本與快取命中率 ← transcript usage（命中率 = cache 讀 ÷（輸入＋cache 讀＋cache 寫），成本 = usage × 產表當下牌價）。
時間看 trace、錢看 transcript。</p>
<div id="tip"></div>
<script>
const DATA = __DATA__;
const ROW_H = 34, GAP_W = 30;
const PX0 = __PX0__;
let pxPerSec = PX0;
const svg = document.getElementById('chart'), tip = document.getElementById('tip');
const labels = document.getElementById('labels');
DATA.roles.forEach(r => { const d = document.createElement('div'); d.textContent = r; labels.appendChild(d); });

function xOf(us) {                      // 斷軸座標：空窗壓成固定寬
  let cut = 0, gapsPassed = 0;
  for (const [g0, g1] of DATA.gaps) {
    if (us >= g1) { cut += (g1 - g0); gapsPassed++; }
    else if (us > g0) { cut += (us - g0); }
  }
  return ((us - DATA.t0 - cut) / 1e6) * pxPerSec + gapsPassed * GAP_W;
}
function render() {
  const active = (DATA.t1 - DATA.t0 - DATA.gaps.reduce((a,[g0,g1]) => a + (g1-g0), 0)) / 1e6;
  const W = Math.ceil(active * pxPerSec + DATA.gaps.length * GAP_W) + 20;
  const H = DATA.roles.length * ROW_H + 26;
  svg.setAttribute('width', W); svg.setAttribute('height', H);
  let out = '';
  DATA.spans.forEach((s, i) => {
    const y = DATA.roles.indexOf(s.role) * ROW_H + 12;   // 條的中心對齊左欄文字（labels padding-top 6px）
    const x = xOf(s.s), w = Math.max(xOf(s.e) - x, 1.2);
    out += `<rect data-i="${i}" x="${x.toFixed(1)}" y="${y+3}" width="${w.toFixed(1)}" height="16" rx="2"
             fill="${DATA.colors[s.role]}" opacity="${s.kind === 'llm_request' ? 0.92 : 0.45}"/>`;
  });
  DATA.gaps.forEach(([g0, g1]) => {
    const x = xOf(g1) - GAP_W / 2;
    out += `<rect x="${x-4}" y="0" width="8" height="${DATA.roles.length*ROW_H}" fill="#fff"/>
            <text x="${x}" y="${DATA.roles.length*ROW_H+16}" text-anchor="middle"
                  style="font:12px sans-serif;fill:#666">⫽ ${Math.round((g1-g0)/60e6)} 分</text>`;
  });
  svg.innerHTML = out;
}
svg.addEventListener('mousemove', ev => {
  const t = ev.target.closest('rect[data-i]');
  if (!t) { tip.style.display = 'none'; return; }
  const s = DATA.spans[+t.dataset.i];
  const dur = ((s.e - s.s) / 1e6).toFixed(1);
  let txt = `<b>${s.role}</b><br>${s.kind === 'llm_request' ? 'LLM' : 'tool'} · ${s.label} · ${dur}s`;
  if (s.kind === 'llm_request') {
    const ti = s["in"] || 0;
    const denom = ti + s.cr + s.cw;   // 與頁腳、表格同一條公式：cache 讀 ÷（輸入＋cache 讀＋cache 寫）
    txt += `<br>輸入 ${ti.toLocaleString()} · 輸出 ${s.out.toLocaleString()} tok · cache 讀 ${s.cr.toLocaleString()} / 寫 ${s.cw.toLocaleString()}`;
    if (denom) txt += `<br>此請求快取命中 ${(100 * s.cr / denom).toFixed(1)}%`;
  }
  tip.innerHTML = txt;
  tip.style.display = 'block';
  tip.style.left = Math.min(ev.clientX + 14, innerWidth - 360) + 'px';
  tip.style.top = (ev.clientY + 14) + 'px';
});
svg.addEventListener('mouseleave', () => tip.style.display = 'none');
const clampPx = v => Math.min(240, Math.max(0.02, v));   // 縮放夾制：畫布不炸寬、也不縮到看不見
document.getElementById('zi').onclick = () => { pxPerSec = clampPx(pxPerSec * 1.5); render(); };
document.getElementById('zo').onclick = () => { pxPerSec = clampPx(pxPerSec / 1.5); render(); };
document.getElementById('zr').onclick = () => {
  pxPerSec = PX0; render();
  document.getElementById('scroll').scrollLeft = 0;
};
document.getElementById('scroll').addEventListener('wheel', ev => {
  if (!ev.ctrlKey && !ev.metaKey) return;
  ev.preventDefault();
  pxPerSec = clampPx(pxPerSec * (ev.deltaY < 0 ? 1.2 : 1 / 1.2));
  render();
}, { passive: false });
render();
</script>
</body></html>"""


def build_page(sid: str, agg: dict, chart: list[dict], gaps: list, tokens: dict, report: dict | None) -> str:
    t0 = min(a["first"] for a in agg.values())
    t1 = max(a["last"] for a in agg.values())
    active_s = (t1 - t0 - sum(g1 - g0 for g0, g1 in gaps)) / 1e6
    gap_total_min = sum(g1 - g0 for g0, g1 in gaps) / 60e6

    order = sorted(agg, key=lambda r: (r != "主線程", -(agg[r]["last"] - agg[r]["first"])))
    palette_pool = ["#0f766e", "#b45309", "#1d4ed8", "#9333ea", "#be123c", "#4d7c0f", "#0369a1"]
    colors = {r: palette_pool[i % len(palette_pool)] for i, r in enumerate(order)}

    rows = []
    total_cost, cost_known = 0.0, False
    for r in order:
        a, tk = agg[r], tokens.get(r, {})
        hr = hit_rate(tk) if tk else None
        cost_s = f"${tk['cost']:.3f}" if tk else "—"
        if tk:
            total_cost += tk["cost"]
            cost_known = True
        rows.append(
            f"<tr><td class=role>{html.escape(r)}</td><td class=role>{html.escape(tk.get('model','—'))}</td>"
            f"<td class=n>{(a['last']-a['first'])/1e6:,.0f}s</td><td class=n>{a['llm_us']/1e6:,.0f}s</td>"
            f"<td class=n>{tk.get('in',0):,}</td><td class=n>{tk.get('out',0):,}</td>"
            f"<td class=n>{tk.get('cr',0):,}</td><td class=n>{tk.get('cw',0):,}</td>"
            f"<td class=n>{f'{hr*100:.1f}%' if hr is not None else '—'}</td>"
            f"<td class=n>{cost_s}</td></tr>"
        )

    # transcript 有、trace 沒拍到的角色（telemetry 中途才開、或撈取撞到 limit）：
    # 照樣列出、成本照樣進總額——靜默漏掉會讓總成本卡片少算而沒有人知道。
    extra_roles = sorted(r for r in tokens if r not in agg)
    for r in extra_roles:
        tk = tokens[r]
        hr = hit_rate(tk)
        total_cost += tk["cost"]
        cost_known = True
        rows.append(
            f"<tr><td class=role>{html.escape(r)}（trace 未拍到）</td>"
            f"<td class=role>{html.escape(tk.get('model','—'))}</td>"
            f"<td class=n>—</td><td class=n>—</td>"
            f"<td class=n>{tk.get('in',0):,}</td><td class=n>{tk.get('out',0):,}</td>"
            f"<td class=n>{tk.get('cr',0):,}</td><td class=n>{tk.get('cw',0):,}</td>"
            f"<td class=n>{f'{hr*100:.1f}%' if hr is not None else '—'}</td>"
            f"<td class=n>${tk['cost']:.3f}</td></tr>"
        )

    if report:
        cnt: dict[str, int] = defaultdict(int)
        for f_ in report.get("findings", []):
            cnt[(f_.get("severity") or "?")[:1].upper()] += 1
        v_card = (
            f'<div class="fact"><span>審查結論</span><b>{html.escape(report.get("conclusion") or "—")}</b>'
            f'<span>Critical {cnt.get("C",0)} · Suggestion {cnt.get("S",0)} · Nit {cnt.get("N",0)}'
            f' · 提問 {len(report.get("open_questions", []))}</span></div>'
        )
        # local_branch 模式的報告 "mr" 是 null——合法形狀，不是缺欄
        mr = report.get("mr") or {}
        title_bits = (
            f'MR !{html.escape(str(mr.get("iid")))}「{html.escape(mr.get("title") or "")}」 · '
            if mr.get("title") else ""
        )
        skill_ver = (report.get("meta") or {}).get("skill_version", "—")
    else:
        v_card = ('<div class="fact"><span>審查結論</span><b>—</b>'
                  '<span>report.json 未封存（審查未完成或 archive 未掛載）</span></div>')
        title_bits, skill_ver = "", "—"

    extra_note = f'（另 {len(extra_roles)} 個角色僅見於 transcript）' if extra_roles else ''
    facts = v_card + (
        f'<div class="fact"><span>活動時間（trace，扣除 {gap_total_min:.0f} 分空窗）</span>'
        f'<b>{int(active_s//60)} 分 {int(active_s%60):02d} 秒</b>'
        f'<span>主線程與 {len(order)-1} 個 subagent{extra_note}</span></div>'
        f'<div class="fact"><span>總成本（transcript）</span>'
        f'<b>{f"${total_cost:.2f}" if cost_known else "—"}</b><span>usage × 產表當下牌價</span></div>'
    )
    meta = (f'{title_bits}session <code>{sid[:8]}</code> · skill <code>{html.escape(str(skill_ver))}</code>'
            f' · 限制模式（iptables 白名單）')

    data = {
        "t0": t0, "t1": t1, "roles": order, "colors": colors,
        "gaps": [list(g) for g in gaps],
        "spans": sorted(chart, key=lambda c: c["s"]),
    }
    px0 = max(0.4, min(2.0, 1000 / max(active_s, 1)))
    return (PAGE
            .replace("__SID8__", sid[:8])
            .replace("__META__", meta)
            .replace("__FACTS__", facts)
            .replace("__ROWS__", "".join(rows))
            # </ 逸出：role 名或 label 含 "</script>" 時不得讓 JSON 提前關閉 script 標籤
            .replace("__DATA__", json.dumps(data, ensure_ascii=False).replace("</", "<\\/"))
            .replace("__PX0__", f"{px0:.3f}"))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("session", help="session id 前綴（如 17c7d838）")
    ap.add_argument("--out", help="輸出檔路徑，預設 ./session-<id>.html")
    ap.add_argument("--jaeger", default="http://localhost:16686")
    ap.add_argument("--lookback", default="168h", help="往回撈多久（Jaeger 語法，預設 168h；badger 保存 720h）")
    ap.add_argument("--limit", type=int, default=500, help="最多撈幾筆 trace（預設 500）")
    ap.add_argument("--open", action="store_true", help="產完直接用瀏覽器開啟（macOS open）")
    args = ap.parse_args()

    by_sid, all_sids, truncated = fetch_spans(args.jaeger, args.session, args.lookback, args.limit)
    if truncated:
        print(f"⚠️  撈到 --limit={args.limit} 上限，較舊的 trace 可能被截掉（必要時提高 --limit）")
    if not by_sid:
        have = "、".join(sorted(s[:8] for s in all_sids)) or "（一場都沒有）"
        sys.exit(
            f"Jaeger 裡找不到 session {args.session}*。這批資料裡有：{have}\n"
            f"（更早的場次用 --lookback 調大，預設 168h）"
        )
    if len(by_sid) > 1:
        hits = "、".join(sorted(by_sid))
        sys.exit(f"前綴 {args.session} 命中 {len(by_sid)} 場：{hits}\n請給更長的前綴，一場一報，不合併。")
    sid, spans = next(iter(by_sid.items()))
    agg, chart = collect_roles(spans)
    gaps = find_gaps(chart)
    tokens = collect_transcript(sid)
    t0 = min(a["first"] for a in agg.values())
    t1 = max(a["last"] for a in agg.values())
    report = find_report_json(t0, t1)

    out = args.out or f"session-{sid[:8]}.html"
    Path(out).write_text(build_page(sid, agg, chart, gaps, tokens, report), encoding="utf-8")
    print(f"已輸出 {out}（{len(chart)} spans，壓縮空窗 {len(gaps)} 段）")
    if args.open:
        subprocess.run(["open", out], check=False)


if __name__ == "__main__":
    main()
