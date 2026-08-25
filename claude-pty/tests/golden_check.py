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
  · **DOM 合約屬性**：`data-testid`／`data-act`／`data-tone`／`data-kind` 那一整類。
    aria 一個字都不記它們（實測），而它們正是 e2e 的抓手、事件委派的分派鍵、狀態的
    真相來源。白名單見 `golden_scenes.DOM_ATTRS`，**不記 class、不記完整 HTML**。
  · **網路序列**：文件與 API 的呼叫順序。多打一發、少打一發、換了端點都看得到。
  · **截圖**：看起來還不還是同一個東西。

## 截圖的兩道閘：比例，加上「有沒有一整塊變了」

比例這一道單獨用是抓不到東西的。實測：把抽屜面板的底色**整個換掉**，全頁只差
**0.04%**（那塊底色幾乎被 iframe 與標題列蓋滿）。1% 的全頁比例等於允許一塊 158x158
的區域整個換掉還是綠的。

後來改成數「強差異像素」的絕對數量，仍然不夠：一顆 chip 的顏色只挪 20 階、一顆 7px
的狀態燈換色，數量都太小。**問題不在數量，在形狀。** 反鋸齒的差異是沿著字緣的
一兩像素細線，真的改動則是一整塊。所以第二道改成問形狀：

  · **比例** <= `PIXEL_TOLERANCE`（1%）。守的是整頁大面積改變。
  · **有沒有任何一塊實心 `BLOCK`x`BLOCK` 的強差異**。作法是把強差異畫成遮罩，再做一次
    `BLOCK`x`BLOCK` 的侵蝕（`MinFilter`）：侵蝕之後還剩下任何一點，就代表原圖存在一塊
    完全由強差異構成的 5x5。細線侵蝕完什麼都不剩，實心塊剩得下來。
  · **強差異像素總數** <= `STRONG_PIXEL_LIMIT`。這一道是給**細但廣**的改動用的。

第三道不是湊數，是實測補上的：把 chip 的 1px 邊框顏色挪 20 階，前兩道**都放過**
（1px 的線永遠湊不出實心 5x5，總量也遠低於 1%），而它其實改到了畫面上每一顆 chip。
三道各接一種形狀：大面積、局部一整塊、細而廣。

因為形狀那一道擋得住反鋸齒，`STRONG_DELTA` 就可以壓得很低（8 階，而不是原本的 32），
小幅度的真差異才抓得到。實測乾淨的一輪是 0 塊、0 個強差異像素。

⚠ 用侵蝕而不是連通元件標記：一來它是 C 實作的一次卷積，2.5M 像素跑得動；二來連通元件
  的**外接矩形**會被細線騙過去——整行文字位移一像素會產生一條 500x8 的細長元件，
  外接矩形遠大於 5x5，但它並不是「一塊」。

⚠ 尺寸不同一律是失敗，不做縮放後再比：版面高度變了正是要抓的事，縮放會把它抹平成
  一片模糊的小差異，然後落在閾值以內。

⚠ aria、DOM 與網路都是**逐字**比對，沒有閾值。它們是文字，沒有反鋸齒問題，給了容忍額度就等於
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
# 單一通道差超過這個才算「真的不一樣」。壓得低是因為擋反鋸齒的工作交給下面那道
# 形狀規則了，這裡不必再靠高門檻去擋它。
STRONG_DELTA = 8
# 「一塊」的邊長。實心 5x5 比任何一個看得出來的介面改動都小（7px 的狀態燈就有 5x5），
# 又比反鋸齒的一兩像素細線大得多。
BLOCK = 5
# 強差異像素的總數上限。給「細但廣」的改動用（1px 邊框、分隔線），那一類湊不出實心塊。
# 實測：乾淨的一輪是 0 個，把 chip 的 1px 邊框挪 20 階是四位數。
STRONG_PIXEL_LIMIT = 400

_fails = 0
skipped_shots: list[str] = []
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


def _only(a: str, b: str) -> list[str]:
    """a 有而 b 沒有的行（保持 a 的順序，註解與空行不算）。"""
    bl = [x for x in b.splitlines() if x.strip() and not x.startswith("#")]
    seen: dict[str, int] = {}
    for x in bl:
        seen[x] = seen.get(x, 0) + 1
    out = []
    for x in a.splitlines():
        if not x.strip() or x.startswith("#"):
            continue
        if seen.get(x):
            seen[x] -= 1
        else:
            out.append(x)
    return out


def compare_png(golden_path: str, now_bytes: bytes, scene: str) -> tuple[bool, str]:
    """回 (過不過, 說明)。差異圖寫進 DIFF_DIR。"""
    from PIL import Image, ImageChops, ImageFilter

    a = Image.open(golden_path).convert("RGB")
    b = Image.open(_bytes_io(now_bytes)).convert("RGB")
    if a.size != b.size:
        # ⚠ 尺寸不同**不縮放後再比**。版面高度變了正是要抓的東西，縮放會把它抹平成
        #   一片模糊的小差異，然後落在閾值以內。
        return False, f"尺寸就不一樣了：golden {a.size} vs 現在 {b.size}"
    diff = ImageChops.difference(a, b)
    total = a.size[0] * a.size[1]
    # 每像素取三通道最大差（不是轉灰階：轉灰階是加權平均，純藍換成純紅會被平掉）
    r, g, bl = diff.split()
    worst = ImageChops.lighter(ImageChops.lighter(r, g), bl)
    changed = total - worst.histogram()[0]
    ratio = changed / total
    mask = worst.point(lambda v: 255 if v > STRONG_DELTA else 0)
    strong = mask.histogram()[255]
    # 侵蝕：只有「實心 BLOCKxBLOCK 的強差異」活得下來，細線一律歸零。
    blocks = mask.filter(ImageFilter.MinFilter(BLOCK))
    box = blocks.getbbox()  # 全黑時回 None
    why = f"差 {ratio:.2%}、實心 {BLOCK}x{BLOCK} 塊 {'有' if box else '無'}、強差異 {strong} 個"
    if ratio > PIXEL_TOLERANCE or box is not None or strong > STRONG_PIXEL_LIMIT:
        out = os.path.join(DIFF_DIR, f"{scene}.diff.png")
        # 差異放大成可見的：原圖的差通常只有幾階灰，直接存會看起來全黑。
        diff.point(lambda v: min(255, v * 8)).save(out)
        Image.open(_bytes_io(now_bytes)).save(os.path.join(DIFF_DIR, f"{scene}.now.png"))
        where = f"，第一塊在 {box[:2]}" if box else ""
        return False, (
            f"{why}{where}（上限 {PIXEL_TOLERANCE:.0%}／不得有實心塊／強差異 {STRONG_PIXEL_LIMIT} 個）；差異圖 {out}"
        )
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
        shots_ok, why_not = G.screenshot_comparable(browser)
        print("== 環境 ==")
        if shots_ok:
            print("  截圖：與錄製環境相同，照比")
        else:
            # ⚠ 明說。靜靜少比十二條就是假綠燈，而那正是這支存在的理由的反面。
            print(f"  ⚠ 截圖跳過：平台不同（{why_not}）")
            print("    aria／DOM／網路照比，它們是文字，與字體算繪無關，跨平台完全可比。")
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
                # ⚠ 一場開不起來**不可以把整支打斷**。原本沒有這層 try，於是 vue 模式下
                #   抽屜那場一逾時，後面三場（drawer-open／account-user／account-admin）
                #   就完全沒有比到，而畫面上只看得到一段 traceback ——「那三場過了嗎」
                #   與「那三場根本沒跑」在輸出裡長得一模一樣。
                try:
                    run(page, BASE)
                except Exception as ex:  # noqa: BLE001 —— 任何一種開不起來都算這一場紅
                    check(f"{name} 這一場開得起來（{vp}）", False)
                    print(f"        {type(ex).__name__}: {str(ex).splitlines()[0][:160]}")
                    ctx.close()
                    continue

                want = open(os.path.join(d, f"aria.{vp}.txt"), encoding="utf-8").read()
                got = page.locator("body").aria_snapshot().rstrip("\n") + "\n"
                if not check(f"aria 樹一致（{vp}）", want == got):
                    print(f"        {text_diff_hint(want, got)}")

                want_dom = open(os.path.join(d, f"dom.{vp}.txt"), encoding="utf-8").read()
                got_dom = G.dom_text(page)
                if not check(f"DOM 合約屬性一致（{vp}）", want_dom == got_dom):
                    print(f"        {text_diff_hint(want_dom, got_dom)}")

                if vp == G.SHOT_VIEWPORT:
                    want_net = open(os.path.join(d, "network.txt"), encoding="utf-8").read()
                    got_net = G.network_text(page, reqs, BASE)
                    if not check("網路呼叫一致", want_net == got_net):
                        # ⚠ 網路這一段印的是**集合差**，不是第一行差異。少打一發會讓後面
                        #   每一行都對不齊，「第一行差異」那種提示只會指到位移，看不出
                        #   真正多了什麼、少了什麼。
                        for tag, only in (
                            ("golden 有、現在沒有", _only(want_net, got_net)),
                            ("現在有、golden 沒有", _only(got_net, want_net)),
                        ):
                            for ln in only:
                                print(f"        {tag}：{ln}")
                if vp == G.SHOT_VIEWPORT or name in G.MOBILE_SHOT:
                    if shots_ok:
                        shot = page.screenshot(full_page=True, animations="disabled")
                        ok, why = compare_png(os.path.join(d, f"screen.{vp}.png"), shot, f"{name}.{vp}")
                        check(f"截圖一致（{vp}，{why}）", ok)
                    else:
                        skipped_shots.append(f"{name} {vp}")
                ctx.close()
        browser.close()
finally:
    G.cleanup()

if skipped_shots:
    print(f"\n⚠ 這一輪沒有比截圖（平台與錄製時不同）：{len(skipped_shots)} 場")
    print("   要在這台機器上也守住視覺，就在這台機器重錄一份 golden。")
if _fails:
    print(f"\n差異圖與當下的截圖都在 {DIFF_DIR}")
    print("⚠ 確認那是你要的改動再重錄（tests/golden_record.py）。看到紅燈就順手重錄，")
    print("  等於把「我改壞了」寫成「這就是新的對的樣子」，這道防線當場消失。")
print(f"\n{'done' if not _fails else f'{_fails} FAILED'}")
sys.exit(1 if _fails else 0)
