"""錄 golden master：把現在這個（legacy）介面的樣子存成檔案。

    uv run --with flask --with docker --with sqlalchemy --with argon2-cffi \
        --with psutil --with cryptography --with playwright --with pillow \
        python tests/golden_record.py

**不需要 docker、也不需要 ttyd。**

## 這支不是測試

它一條斷言都沒有，跑完只會把檔案寫進 `tests/golden/`。驗的那一支是 `golden_check.py`，
它才會進 run-all.sh。兩支共用 `golden_scenes.py` 的場景定義，所以「錄的」與「比的」
永遠是同一個狀態。

## 什麼時候該重錄

**只有在你確定介面真的該變的時候。** 重錄等於改規格，diff 要進 code review，
與改一份 API 契約同一個份量。看到 `golden_check` 紅了就順手重錄一次，等於把
「我改壞了」寫成「這就是新的對的樣子」，那條防線當場消失。

## 一致性

`--verify` 會錄到暫存目錄再與現有的逐位比對，兩次一致才算釘死了。CI 或改完錄製邏輯
之後都該跑一次：不穩定源沒釘乾淨的話，這支自己就會告訴你是哪一個檔案在飄。
"""

from __future__ import annotations

import filecmp
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import golden_scenes as G  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402


def record_into(root: str) -> list[str]:
    """把每一場錄進 root/<scene>/。回傳寫出去的相對路徑（排序過）。"""
    written: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        # 錄製環境的指紋。golden_check 拿它決定「這台機器能不能比截圖」（見
        # golden_scenes.screenshot_comparable 的說明）。
        os.makedirs(root, exist_ok=True)
        _write(os.path.join(root, G.META_NAME), G.meta_text(browser), written, root)
        for name, _desc, run in G.SCENES:
            out = os.path.join(root, name)
            os.makedirs(out, exist_ok=True)
            print(f"  {name}")
            for vp in G.VIEWPORTS:
                ctx = G.new_context(browser, vp)
                page = ctx.new_page()
                reqs = G.prepare_page(page)
                run(page, BASE)

                aria = page.locator("body").aria_snapshot()
                _write(os.path.join(out, f"aria.{vp}.txt"), aria.rstrip("\n") + "\n", written, root)
                # aria 記不到的那一整類合約屬性（見 golden_scenes.DOM_ATTRS 的說明）
                _write(os.path.join(out, f"dom.{vp}.txt"), G.dom_text(page), written, root)

                if vp == G.SHOT_VIEWPORT:
                    _write(os.path.join(out, "network.txt"), G.network_text(page, reqs, BASE), written, root)
                    # ⚠ animations="disabled"：把還在跑的 CSS 動畫定住。context 已經開了
                    #   reduced-motion，這是第二道（有幾個動畫刻意不受 reduced-motion 影響，
                    #   例如 .lamp 的呼吸燈）。
                    shot = os.path.join(out, f"screen.{vp}.png")
                    page.screenshot(path=shot, full_page=True, animations="disabled")
                    written.append(os.path.relpath(shot, root))
                ctx.close()
        browser.close()
    return sorted(written)


def _write(path: str, text: str, written: list[str], root: str) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    written.append(os.path.relpath(path, root))


G.pin_all()
G.seed()
BASE = G.start_server()

try:
    if "--verify" in sys.argv:
        # 連錄兩次比對。不穩定源沒釘乾淨的話，這裡會直接指出是哪一個檔案。
        a, b = tempfile.mkdtemp(prefix="golden-a-"), tempfile.mkdtemp(prefix="golden-b-")
        try:
            print("第一次：")
            names = record_into(a)
            # ⚠ 兩次之間重新 seed。不重 seed 的話「在 seed 當下用真實時間填的欄位」兩次
            #   會一模一樣，這支就看不到它在飄——`users.created_at` 正是這樣溜過去，
            #   最後被跨行程跑的 golden_check 抓到的（2026-08-25）。
            time.sleep(1.1)
            G.seed()
            print("第二次：")
            record_into(b)
            diff = [n for n in names if not filecmp.cmp(os.path.join(a, n), os.path.join(b, n), shallow=False)]
            print()
            if diff:
                print(f"❌ 兩次錄製有 {len(diff)} 個檔案不一致：")
                for n in diff:
                    print(f"  · {n}")
                print("   不准用放寬閾值蓋過去：去 golden_scenes.pin_all() 把來源釘死。")
                sys.exit(1)
            print(f"✅ 兩次錄製逐位一致（{len(names)} 個檔案）")
        finally:
            shutil.rmtree(a, ignore_errors=True)
            shutil.rmtree(b, ignore_errors=True)
    else:
        shutil.rmtree(G.GOLDEN_DIR, ignore_errors=True)
        os.makedirs(G.GOLDEN_DIR, exist_ok=True)
        print(f"錄進 {G.GOLDEN_DIR}：")
        names = record_into(G.GOLDEN_DIR)
        total = sum(os.path.getsize(os.path.join(G.GOLDEN_DIR, n)) for n in names)
        print(f"\n寫出 {len(names)} 個檔案，共 {total / 1024:.0f} KB")
finally:
    G.cleanup()
