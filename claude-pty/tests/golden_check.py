"""比對現在的畫面與 `tests/golden/` 裡錄下來的規格。

    uv run --with flask --with docker --with sqlalchemy --with argon2-cffi \
        --with psutil --with cryptography --with playwright --with pillow \
        python tests/golden_check.py

**不需要 docker、也不需要 ttyd。**

## 這支在守什麼

舊實作就是規格（Day 26 特徵測試同一招）。Vue 版重寫 DOM 與狀態時，這裡是唯一一個會
說「你把某個東西改掉了」的地方，而且它說得出改在哪：

  · **aria 快照**：結構與可及名稱。按鈕變成 div、標籤掉了、順序換了都會現形。
    兩個視口各一份，media query 之後的結構也守得住。
  · **網路序列**：文件與 API 的呼叫順序。多打一發、少打一發、換了端點都看得到。
  · **截圖**：看起來還不還是同一個東西。

## 截圖的兩道閘（單靠比例會漏掉真的改動）

一開始只有「像素差比例 <= 1%」。實測發現它抓不到東西：把抽屜面板的底色整個換掉，
全頁只差 **0.04%**，因為那塊底色幾乎被 iframe 與標題列蓋滿。1% 的全頁比例等於允許
一塊 158x158 的區域整個換掉還是綠的，那條閘形同虛設。

所以改成兩道，兩道都要過：

  · **比例** <= `PIXEL_TOLERANCE`（1%，Nathan 給的數字）。守的是「整頁大面積改變」。
  · **強差異像素數** <= `STRONG_PIXEL_LIMIT`。只數「單一通道差超過 `STRONG_DELTA`」的
    像素。反鋸齒在字緣造成的是幾階灰，過不了這道濾網；換顏色、位移、少一個元件則
    一定過得了。這一道才是真正在抓改動的那一道。

⚠ 尺寸不同一律是失敗，不做縮放後再比：版面高度變了正是要抓的事，縮放會把它抹平成
  一片模糊的小差異，然後落在閾值以內。

⚠ aria 與網路是**逐字**比對，沒有閾值。它們是文字，沒有反鋸齒問題，給了容忍額度就等於
  給了「悄悄改掉一個標籤」的空間。

## 紅了怎麼辦

先看差異圖（會印路徑出來），確認那是不是你要的改動。**是**的話重錄
（`python tests/golden_record.py`）並把 diff 一起送審；**不是**的話那就是回歸。
順手重錄是這道防線唯一的死法。
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import golden_scenes as G  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

# 允許的像素差比例（Nathan 給的數字）。同一台機器上實測是 0.00%。
PIXEL_TOLERANCE = 0.01
# 單一通道差超過這個才算「真的不一樣」。反鋸齒在字緣是幾階灰，過不了。
STRONG_DELTA = 32
# 強差異像素的絕對上限。400 個大約是 20x20，比任何一個看得出來的介面改動都小，
# 又比零星的算繪飄動大。實測：乾淨的一輪是 0 個，換一個底色是四位數。
STRONG_PIXEL_LIMIT = 400

_fails = 0
DIFF_DIR = tempfile.mkdtemp(prefix="golden-diff-")


def check(label, ok):
    global _fails
    if not ok:
        _fails += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    return ok


def text_diff_hint(want: str, got: str) -> str:
    """指出第一行不一樣的地方。整份 diff 印出來會把畫面洗掉，第一個分歧點才是線索。"""
    w, g = want.split("\n"), got.split("\n")
    for i in range(max(len(w), len(g))):
        a = w[i] if i < len(w) else "<沒有這一行>"
        b = g[i] if i < len(g) else "<沒有這一行>"
        if a != b:
            return f"第 {i + 1} 行： golden={a.strip()!r} 現在={b.strip()!r}"
    return "長度不同但每一行都相同（結尾的空白行）"


def compare_png(golden_path: str, now_bytes: bytes, scene: str) -> tuple[bool, str]:
    """回 (過不過, 說明)。差異圖寫進 DIFF_DIR。"""
    from PIL import Image, ImageChops

    a = Image.open(golden_path).convert("RGB")
    b = Image.open(_bytes_io(now_bytes)).convert("RGB")
    if a.size != b.size:
        # ⚠ 尺寸不同**不縮放後再比**。版面高度變了正是要抓的東西，縮放會把它抹平成
        #   一片模糊的小差異，然後落在閾值以內。
        return False, f"尺寸就不一樣了：golden {a.size} vs 現在 {b.size}"
    diff = ImageChops.difference(a, b)
    total = a.size[0] * a.size[1]
    changed = strong = 0
    for px in diff.getdata():
        m = max(px)
        if m:
            changed += 1
            if m > STRONG_DELTA:
                strong += 1
    ratio = changed / total
    why = f"差 {ratio:.2%}、強差異 {strong} 個"
    if ratio > PIXEL_TOLERANCE or strong > STRONG_PIXEL_LIMIT:
        out = os.path.join(DIFF_DIR, f"{scene}.diff.png")
        # 差異放大成可見的：原圖的差通常只有幾階灰，直接存會看起來全黑。
        diff.point(lambda v: min(255, v * 8)).save(out)
        Image.open(_bytes_io(now_bytes)).save(os.path.join(DIFF_DIR, f"{scene}.now.png"))
        return False, f"{why}（上限 {PIXEL_TOLERANCE:.0%} / {STRONG_PIXEL_LIMIT} 個）；差異圖 {out}"
    return True, why


def _bytes_io(b: bytes):
    import io

    return io.BytesIO(b)


G.pin_all()
G.seed()
BASE = G.start_server()

try:
    if not os.path.isdir(G.GOLDEN_DIR) or not os.listdir(G.GOLDEN_DIR):
        print(f"  FAIL  找不到 golden（{G.GOLDEN_DIR}）。先跑 tests/golden_record.py 錄一份。")
        sys.exit(1)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        for name, desc, run in G.SCENES:
            print(f"== {name}：{desc} ==")
            d = G.scene_dir(name)
            if not os.path.isdir(d):
                check(f"{name} 有錄過（golden 裡沒有這一場，先重錄）", False)
                continue
            for vp in G.VIEWPORTS:
                ctx = G.new_context(browser, vp)
                page = ctx.new_page()
                reqs = G.prepare_page(page)
                run(page, BASE)

                want = open(os.path.join(d, f"aria.{vp}.txt"), encoding="utf-8").read()
                got = page.locator("body").aria_snapshot().rstrip("\n") + "\n"
                if not check(f"aria 樹一致（{vp}）", want == got):
                    print(f"        {text_diff_hint(want, got)}")

                if vp == G.SHOT_VIEWPORT:
                    want_net = open(os.path.join(d, "network.txt"), encoding="utf-8").read()
                    got_net = G.network_text(reqs, BASE)
                    if not check("網路呼叫一致", want_net == got_net):
                        print(f"        {text_diff_hint(want_net, got_net)}")
                    shot = page.screenshot(full_page=True, animations="disabled")
                    ok, why = compare_png(os.path.join(d, f"screen.{vp}.png"), shot, name)
                    check(f"截圖一致（{vp}，{why}）", ok)
                ctx.close()
        browser.close()
finally:
    G.cleanup()

if _fails:
    print(f"\n差異圖與當下的截圖都在 {DIFF_DIR}")
    print("⚠ 確認那是你要的改動再重錄（tests/golden_record.py）。看到紅燈就順手重錄，")
    print("  等於把「我改壞了」寫成「這就是新的對的樣子」，這道防線當場消失。")
print(f"\n{'done' if not _fails else f'{_fails} FAILED'}")
sys.exit(1 if _fails else 0)
