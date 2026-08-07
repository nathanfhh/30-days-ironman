"""E2E：被截斷的文字才出現 tooltip——設定欄的 chip 與名稱欄（真瀏覽器，不需 docker）。

    uv run --with flask --with docker --with sqlalchemy --with argon2-cffi \
        --with psutil --with playwright python tests/e2e_chips.py
（首次需 `uv run --with playwright playwright install chromium`）

為什麼一定要真瀏覽器：這件事整個是**排版量測**——`scrollWidth > clientWidth` 只有在真的
排版過之後才有值。純 DOM 斷言（有沒有這個 class）證不了「它真的被切到」，而那正是規則本身。

守的性質：
  🔴 長的要切、要有 tooltip，且 tooltip 內容是**完整**字串（切掉的那半才是它存在的理由）
  🔴 短的**不可以**有 tooltip。每顆都掛的話，滑過 `high` 會彈出一個只是重複顯示同樣文字的
     框——那是雜訊，也讓「有提示＝這裡有你看不到的東西」這個訊號失效
  🔴 tooltip 不可以被祖先裁掉。它是 `::after` 絕對定位，chip 只要有 overflow:hidden 就會
     被切一半——「掛上了」與「看得到」是兩件事，前者過了不代表後者
  🔴 **真實長度的值一律不可以被切**——不只模型，`claude`／`medium` 這些短值也算。
     只驗 model 一種 tone 的話，寫死的窄寬度會讓整欄變成 `COD…` `CLA…` `med…` 而全綠
  🔴 文字盒要容得下下伸部（g／p 的尾巴），否則 overflow:hidden 會把它切掉
  🔴 **名稱欄吃同一條規則**。它與 chip 共用 markClipped()，而且是同一個坑的第三次：
     名字被切成 `my-very-long-…` 卻沒有任何辦法看到全名（2026-07-31 使用者回報）。
     一起驗是因為修法也是共用的——截斷放內層、tooltip 掛外層。
"""
import datetime as _dt
import logging
import os
import socket
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from server import config  # noqa: E402

TMP = tempfile.mkdtemp(prefix="e2e-chips-")
config.DB_URL = f"sqlite:///{TMP}/t.db"
config.SECRET_KEY = "e2e-chips-secret"

from playwright.sync_api import sync_playwright  # noqa: E402

from server import auth  # noqa: E402
from server.app import app  # noqa: E402
from server.db import init_db, reset_engine, session_scope  # noqa: E402
from server.models import Session as SessionRow  # noqa: E402
from server.sessions import utcnow  # noqa: E402

_fails = 0
def check(label, ok):
    global _fails
    if not ok:
        _fails += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    return ok


reset_engine()
init_db()
admin = auth.create_user("e2e-admin", "e2e-password-1", is_admin=True)

# 兩個模型字串：一個**真實長度**（放得下，不該被切），一個刻意超長（一定被切）。
# 兩邊都要驗——只驗「長的會切」的話，把 chip 調到 5.6rem 那種連真實字串都塞不下的
# 寬度也會全綠，而那正是使用者回報的畫面（只看得到開頭幾個字）。
# （chip 畫的就是 profile.model 的字串，不查任何目錄。）
REAL = "claude-sonnet-5-preview"          # 實測自然寬度 133.8px；chip 是 auto + max-width 11rem（176px）
TOO_LONG = "claude-sonnet-5-preview-2026-07-31"

now = utcnow()
# 名稱欄同理：第一列取一個放不進欄寬的名字（一定被切），第二列不取名（沿用 12 碼 sid，
# 那是欄寬本來就照著抓的長度，不該被切）。
LONG_NAME = "refactor-the-login-flow-and-then-some"
with session_scope() as s:
    for i, (prof, name) in enumerate([
        ({"cli": "claude", "network": "restricted", "capture": False,
          "telemetry": False, "model": TOO_LONG, "effort": "high"}, LONG_NAME),
        ({"cli": "claude", "network": "restricted", "capture": False,
          "telemetry": False, "model": REAL, "effort": "high"}, None),
    ], start=1):
        s.add(SessionRow(id=f"e{i}", container_name=f"ec{i}", user_id=admin["id"],
                         workdir="/w", profile=prof, display_name=name,
                         created_at=now - _dt.timedelta(minutes=i), last_active_at=now))


class _FakeContainer:
    def __init__(self, name):
        self.name, self.status, self.id = name, "running", f"cid-{name}"

    def logs(self, **_kw):
        return b""


class _FakeDocker:
    class containers:
        @staticmethod
        def list(**_kw):
            return [_FakeContainer(f"ec{i}") for i in (1, 2)]


import server.app as app_mod  # noqa: E402
app_mod.manager._docker = _FakeDocker()


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


PORT = free_port()
BASE = f"http://127.0.0.1:{PORT}"
# ⚠ 把 werkzeug 的**請求** log 關掉（保留 WARNING 以上）。兩個理由，第二個才是重點：
#   · 每一發請求印一行 `127.0.0.1 - - [...] "GET /api/sessions"`，而列表每 15 秒輪詢一次
#     ——真正的失敗訊息會被埋在裡面。
#   · 🔴 **它是 `ValueError: I/O operation on closed file.` 的來源**：Flask 跑在 daemon thread，
#     腳本 `sys.exit()` 時 Python 關掉 stdout/stderr，而那條 thread 可能還在寫最後一筆請求
#     log（關瀏覽器時常有 in-flight 的輪詢）。daemon thread 的未捕捉例外不影響 exit code，
#     所以測試結果是可信的——但那串紅字每次都要重新判斷一遍「這是不是真的壞了」。
# ⚠ 只降到 WARNING 不是 ERROR：werkzeug 真的有話要說時（例如 port 被佔）還是要看得到。
logging.getLogger("werkzeug").setLevel(logging.WARNING)
threading.Thread(
    target=lambda: app.run(host="127.0.0.1", port=PORT, threaded=True, use_reloader=False),
    daemon=True,
).start()
for _ in range(50):
    with socket.socket() as s:
        if s.connect_ex(("127.0.0.1", PORT)) == 0:
            break
    time.sleep(0.1)

# 一顆 chip 的量測結果。tone 認得出是哪一顆：model / effort 各自有 data-tone。
PROBE = """
([tone, rowIdx]) => {
  const cell = document.querySelectorAll('.manifest__chips-cell')[rowIdx];
  const el = [...cell.querySelectorAll('.chip')].find(c => c.dataset.tone === tone);
  if (!el) return null;
  const text = el.querySelector('.chip__text');
  const cs = getComputedStyle(el);
  const ts = text ? getComputedStyle(text) : null;
  return {
    label: text ? text.textContent : null,
    // 下伸部（g/p 的尾巴）要放得進文字盒，否則 overflow:hidden 會把它切掉
    fontSize: ts ? parseFloat(ts.fontSize) : null,
    lineHeight: ts ? parseFloat(ts.lineHeight) : null,
    clipped: text ? text.scrollWidth > text.clientWidth + 1 : null,
    hasTip: el.classList.contains('tip'),
    tip: el.dataset.tip || null,
    // tooltip 是 ::after 絕對定位的子元素：祖先一裁就看不到
    chipOverflow: cs.overflowX,
  };
}
"""

with sync_playwright() as pw:
    browser = pw.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.goto(f"{BASE}/login", wait_until="domcontentloaded")
    page.fill("#username", "e2e-admin")
    page.fill("#password", "e2e-password-1")
    page.click("#login-btn")
    page.wait_for_function("() => !location.pathname.startsWith('/login')", timeout=8000)
    page.wait_for_selector(".manifest__chips-cell .chip", timeout=8000)

    print("== 超長的 slug：切掉 + 有 tooltip，且 tooltip 講的是完整字串 ==")
    m = page.evaluate(PROBE, ["model", 0])
    check("模型 chip 有畫出來", m is not None)
    check("🔴 真的被切到（scrollWidth > clientWidth）", bool(m) and m["clipped"] is True)
    check("畫面上顯示的是完整字串（切是視覺的，DOM 不動）", bool(m) and m["label"] == TOO_LONG)
    check("🔴 掛上了 tooltip", bool(m) and m["hasTip"] is True)
    check("🔴 tooltip 是完整的 slug（不是又一份被切過的）", bool(m) and m["tip"] == TOO_LONG)
    check("🔴 chip 本身不裁切，否則 ::after 的提示會被切掉一半",
          bool(m) and m["chipOverflow"] != "hidden")
    # ⚠ 下伸部：`.chip` 是 line-height:1，文字盒剛好等於字級，配上 overflow:hidden 會把
    #   g／p 的尾巴切掉（實測有下伸部的字會少半截）。這條守的是「文字盒比字級高」。
    check("🔴 文字盒容得下下伸部（line-height > font-size）",
          bool(m) and m["lineHeight"] > m["fontSize"] + 1)

    print("== 真實長度的 slug：**不該**被切（chip 寬度要夠）==")
    # 只驗「長的會切」的話，把寬度改回連真實長度的模型名都塞不下也會全綠，
    # 而那正是使用者看到的畫面（只剩開頭幾個字，等於什麼都沒說）。
    r = page.evaluate(PROBE, ["model", 1])
    check("🔴 真實長度的模型名完整顯示，沒有被切", bool(r) and r["clipped"] is False)
    check("🔴 沒被切就不掛 tooltip", bool(r) and r["hasTip"] is False and r["tip"] is None)

    print("== 整張表：**只有**刻意超長的那顆可以被切 ==")
    # 🔴 這條是這次漏掉的那個洞。原本只驗了 model 一種 tone，於是 `claude`／
    #    `medium` 被切成 `CLA…` `med…` 整欄送到使用者面前才被發現——寫死的
    #    5.6rem 本來就放不下它們，只是先前 ellipsis 沒生效、看起來剛好而已。
    clipped = page.evaluate("""() => [...document.querySelectorAll('.chip__text')]
      .filter(t => t.scrollWidth > t.clientWidth + 1).map(t => t.textContent)""")
    check("🔴 被切的只有那顆刻意超長的（claude／medium 這些真實長度值一律不准被切）",
          clipped == [TOO_LONG])

    print("== 短的：不切，也**不可以**有 tooltip ==")
    e = page.evaluate(PROBE, ["effort", 0])
    check("effort chip 有畫出來", e is not None)
    check("沒有被切到", bool(e) and e["clipped"] is False)
    check("🔴 沒有 tooltip（不然滑過去只會重複顯示同樣的字）",
          bool(e) and e["hasTip"] is False and e["tip"] is None)

    print("== hover 真的看得到（不只是掛了 class）==")
    page.hover('.manifest__chips-cell .chip[data-tone="model"]')
    page.wait_for_timeout(300)
    seen = page.evaluate("""() => {
      const el = document.querySelector('.manifest__chips-cell .chip[data-tone="model"]');
      const st = getComputedStyle(el, '::after');
      return { opacity: parseFloat(st.opacity), content: st.content };
    }""")
    check("🔴 hover 後 ::after 真的不透明了", seen["opacity"] > 0.9)
    check("提示內容取自 data-tip", TOO_LONG in (seen["content"] or ""))

    print("== 名稱欄：同一條規則（截斷內層、tooltip 外層）==")
    # 量的是內層 .manifest__id-text、掛 tooltip 的是外層 .manifest__id——**必須分兩層**，
    # 同一個元素既 overflow:hidden 又掛 .tip 的話，::after 會被自己的裁切吃掉。
    names = page.evaluate("""() => [...document.querySelectorAll(
        '.manifest__row:not(.manifest__row--head)')].map(row => {
      const host = row.querySelector('.manifest__id');
      const text = row.querySelector('.manifest__id-text');
      return {
        label: text ? text.textContent : null,
        clipped: text ? text.scrollWidth > text.clientWidth + 1 : null,
        hasTip: host ? host.classList.contains('tip') : null,
        tip: host ? (host.dataset.tip || null) : null,
        hostOverflow: host ? getComputedStyle(host).overflowX : null,
        // 名字的右緣超出所屬儲存格多少（>0 就是溢出到隔壁欄）
        overhang: text
          ? text.getBoundingClientRect().right - row.children[1].getBoundingClientRect().right
          : null,
      };
    })""")
    long_name, short_name = names[0], names[1]
    check("🔴 放不下的名字真的被切", long_name["clipped"] is True)
    check("🔴 被切的掛上 tooltip", long_name["hasTip"] is True)
    check("🔴 tooltip 是完整名稱（切掉的那半才是它存在的理由）", long_name["tip"] == LONG_NAME)
    check("🔴 外層不裁切，否則 ::after 的提示會被切掉", long_name["hostOverflow"] != "hidden")
    # ⚠ 外層改成不裁切之後，能不能縮就只剩 `min-width: 0` 撐著（flex 的 min-width:auto
    #   預設是內容寬）。漏掉那行的話名字既不會被切、還會整條**壓到隔壁的狀態欄上**
    #   ——而欄寬本身是寫死的 minmax 上限，量欄寬看不出任何異狀（實測仍是 184px）。
    #   所以要量的是「文字有沒有伸出儲存格」，不是「儲存格有沒有變寬」。
    check("🔴 名字沒有溢出到隔壁欄（min-width:0 生效）", long_name["overhang"] <= 1)
    check("🔴 沒被切的沒有被切", short_name["clipped"] is False)
    check("🔴 沒被切的不掛 tooltip", short_name["hasTip"] is False and short_name["tip"] is None)

    # ⚠ 選擇器**不要**寫成 `.manifest__id.tip`：tooltip 沒掛上時那個節點根本不存在，
    #   hover 會卡滿 30 秒逾時再丟 traceback——真正的失敗（沒掛 tooltip）會被埋在裡面。
    #   一律 hover「一定存在」的那個節點，讓斷言去說話。
    page.hover(".manifest__row:not(.manifest__row--head) .manifest__id")
    page.wait_for_timeout(300)
    seen = page.evaluate("""() => {
      const el = document.querySelector(
        '.manifest__row:not(.manifest__row--head) .manifest__id');
      const st = getComputedStyle(el, '::after');
      return { opacity: parseFloat(st.opacity), content: st.content };
    }""")
    # ⚠ 「不透明」與「內容對」要**併成一條**。沒有 .tip 時 ::after 根本沒有生成內容，而
    #   `getComputedStyle(el, '::after').opacity` 對一個不存在的 pseudo 仍然回 1
    #   ——拆成兩條的話，tooltip 完全沒掛的情況下前一條會綠燈，等於半個測試在說謊。
    check("🔴 hover 後名稱的提示真的顯示出完整名稱（不透明 **且** 內容對）",
          seen["opacity"] > 0.9 and LONG_NAME in (seen["content"] or ""))

    browser.close()

reset_engine()
__import__("shutil").rmtree(TMP, ignore_errors=True)
print(f"\n{'done' if _fails == 0 else f'{_fails} FAILED'}")
sys.exit(1 if _fails else 0)
