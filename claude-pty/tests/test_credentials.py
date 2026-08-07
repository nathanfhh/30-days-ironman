"""憑證狀態的 regression：credentials_state() / _guard_credentials()。

    uv run --with flask --with docker python tests/test_credentials.py

為什麼要有這支：2026-07-26 的事故是「session 全部靜靜改用 API 按量計價」，沒有任何錯誤、
只有帳單。這裡守的是那條防線本身——判定要對，而且到期是**預告得到的**（days_left）。

⚠ 全程只碰 tmpdir：這支測試絕不可讀到真的 ~/.claude/.credentials.json，那是使用者的憑證。
"""
import json
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from server import config
from server.sessions import (
    Profile,
    SessionError,
    _guard_credentials,
    build_run_kwargs,
    claude_credentials_state as credentials_state,
    credentials_state as all_credentials_state,
)

_pass = _fail = 0
def check(label, ok):
    global _pass, _fail
    _pass += ok
    _fail += (not ok)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")


TMP = tempfile.mkdtemp(prefix="cred-test-")
# 隔離：兩個來源都指進 tmpdir。HOST_HOME 決定第二來源（~/.claude/.credentials.json），
# 沒改的話這支會去讀使用者真正的憑證檔——測試不可以碰那個檔。
config.CREDENTIALS_HOST = os.path.join(TMP, "extracted.json")
config.HOST_HOME = os.path.join(TMP, "home")
LIVE = os.path.join(config.HOST_HOME, ".claude", ".credentials.json")
os.makedirs(os.path.dirname(LIVE))


def write(path, *, days=30, plan="max", refresh_exp=True, access_hours=1):
    """寫一份憑證檔。days 是 refreshToken 距今的天數（可為負＝已過期）。

    多加 3600 秒是為了讓 days_left 的無條件捨去落在整數上：credentials_state() 算的是
    `(rexp - now) // 86400`，剛好整數天的話會因為執行期間過了幾毫秒而掉一天。

    access_hours 是 accessToken 距今的小時數（可為負＝已過期）。對**快照**來說那不是
    無害的：見 claude_credentials_state 裡 stale 的說明。
    """
    oauth = {"accessToken": "x", "refreshToken": "x",
             "expiresAt": int((time.time() + access_hours * 3600) * 1000)}
    if plan is not None:
        oauth["subscriptionType"] = plan
    if refresh_exp:
        oauth["refreshTokenExpiresAt"] = int((time.time() + days * 86400 + 3600) * 1000)
    with open(path, "w") as f:
        json.dump({"claudeAiOauth": oauth}, f)


def clear():
    for p in (config.CREDENTIALS_HOST, LIVE):
        if os.path.exists(p):
            os.remove(p)


print("== 沒有憑證：擋下來，而且說得夠白 ==")
clear()
st = credentials_state()
check("ok=False", st["ok"] is False)
check("state=bad", st["state"] == "bad")
# label 講的是**後果**（按量計價），不是原因——原因在 detail。畫面上人只會掃過 label。
check("label 提到按量計價", "按量計價" in st["label"])
check("label 講明是哪個 agent", st["label"].startswith("Claude"))
check("days_left=None", st["days_left"] is None)
try:
    _guard_credentials()
    check("_guard_credentials 沒憑證要 raise", False)
except SessionError as e:
    check("_guard_credentials 沒憑證 raise SessionError", True)
    check("錯誤訊息指向 refresh-credentials.sh", "refresh-credentials.sh" in str(e))

print("== 正常的憑證：安靜地報訂閱計價 ==")
clear()
write(config.CREDENTIALS_HOST, days=30)
st = credentials_state()
check("ok=True", st["ok"] is True)
check("state=ok", st["state"] == "ok")
check("days_left=30", st["days_left"] == 30)
check("plan=max", st["plan"] == "max")
check("label 帶 agent 名與方案", st["label"] == "Claude 訂閱 · max")
# 兩個到期時刻原樣往前送（epoch 毫秒），格式化是瀏覽器的事——控制平面在容器裡是 UTC，
# 它排出來的時間不屬於任何人。這裡只確認「有送、而且沒被換算過」。
with open(config.CREDENTIALS_HOST) as f:
    raw = json.load(f)["claudeAiOauth"]
stamps = {s["label"]: s["at"] for s in st["stamps"]}
check("存取權杖時刻原樣送出（epoch 毫秒）", stamps["存取權杖"] == raw["expiresAt"])
check("續期權杖時刻原樣送出", stamps["續期權杖"] == raw["refreshTokenExpiresAt"])
_guard_credentials()          # 不該 raise；raise 的話這支測試直接掛掉
check("_guard_credentials 放行", True)

print("== 到期前示警：門檻是 CREDENTIALS_WARN_DAYS（含）==")
# 這條是這次功能的重點。到期**算得出來**，所以畫面該在到期前就開始講，而不是等某天
# 開的 session 全部靜靜轉成按量計價才發現。門檻兩側各驗一次，免得日後改成 `<` 沒人知道。
for days, want in ((15, "ok"), (14, "warn"), (1, "warn")):
    clear()
    write(config.CREDENTIALS_HOST, days=days)
    st = credentials_state()
    check(f"剩 {days} 天 → state={want}", st["state"] == want)
    if want == "warn":
        check(f"剩 {days} 天 → label 講剩幾天", st["label"] == f"Claude 憑證剩 {days} 天")
        check(f"剩 {days} 天 → ok 仍為 True（還能用，只是要換了）", st["ok"] is True)

print("== refreshToken 已過期：等同沒有憑證 ==")
clear()
write(config.CREDENTIALS_HOST, days=-3)
st = credentials_state()
check("ok=False", st["ok"] is False)
check("state=bad", st["state"] == "bad")
check("days_left 為負（不是 None）", st["days_left"] is not None and st["days_left"] < 0)
check("detail 說是過期", "過期" in st["detail"])

print("== 快照的存取權杖過期 → warn，不可以繼續顯示綠燈 ==")
# 探索性測試 2026-07-26 打出來的：徽章 ok／「還有 26 天」，而容器裡的 agent 是
# `401 OAuth access token has been revoked`。徽章原本只看 refreshToken 的到期日，
# 但 CREDENTIALS_HOST 是唯讀掛進容器的**凍結快照**——host 換發一次它就作廢，實際壽命
# 與 refreshToken 的 26 天無關。存取權杖已過期至少證明「這份落後於 host 了」。
clear()
write(config.CREDENTIALS_HOST, days=26, access_hours=-3)
st = credentials_state()
check("state=warn（不是 ok）", st["state"] == "warn")
check("ok 仍為 True（續期權杖沒過期，不是 bad）", st["ok"] is True)
check("label 講的是快照過期", "快照" in st["label"])
check("detail 指向重跑 refresh-credentials.sh",
      "refresh-credentials.sh" in st["detail"])
check("days_left 仍照實回報，沒有為了改狀態而扭曲數字", st["days_left"] == 26)

print("== 但可寫的真檔（~/.claude 內的）存取權杖過期是常態，不可以誤報 ==")
# Linux 上那個檔是 claude 自己就地換發的，過期本來就會自己補回來。
clear()
write(LIVE, days=26, access_hours=-3)
st = credentials_state()
check("state=ok", st["state"] == "ok")
check("沒有被標成 stale", not st.get("stale"))

print("== 快照的存取權杖還沒過期就是正常的 ok ==")
clear()
write(config.CREDENTIALS_HOST, days=26, access_hours=+3)
st = credentials_state()
check("state=ok", st["state"] == "ok")
check("沒有被標成 stale", not st.get("stale"))

print("== 缺 refreshTokenExpiresAt：不可以因為讀不到就亂報警 ==")
# 沒有到期欄位＝不知道何時到期，不等於快到期。報 warn 會讓人白跑一趟 Keychain。
clear()
write(config.CREDENTIALS_HOST, refresh_exp=False)
st = credentials_state()
check("state=ok", st["state"] == "ok")
check("days_left=None", st["days_left"] is None)
check("沒有續期權杖那一行（tooltip 就不會出現）",
      "續期權杖" not in {s["label"] for s in st["stamps"]})

print("== 空檔要當成「沒有」，不是「壞掉的憑證」 ==")
# ⚠ ~/.claude/.credentials.json 常常是空檔——它同時是巢狀 mount 的落點，由
#   refresh-credentials.sh 以 `: >` 建出來。當成壞檔的話，畫面會說「讀不到或格式不對」
#   而不是「未登入」，把人指向去修一個根本沒壞的檔案。
#   腳本的 pick() 用 `[ -s ]`（非空才算），伺服端必須同一個語意。
clear()
open(LIVE, "w").close()                     # 空的落點
st = credentials_state()
check("只有空落點 → 當成找不到憑證", st["detail"] == "找不到任何憑證")
check("不會說成「格式不對」", "格式不對" not in st["detail"])

print("== 壞掉的檔：當成沒有，並且指出是哪個檔 ==")
clear()
with open(config.CREDENTIALS_HOST, "w") as f:
    f.write("{ not json")
st = credentials_state()
check("ok=False", st["ok"] is False)
check("detail 帶上路徑（不然不知道要去修哪一個）",
      config.CREDENTIALS_HOST in st["detail"])

print("== 兩個來源的優先序：與 refresh-credentials.sh 的 pick() 一致 ==")
# ⚠ 順序必須跟腳本一樣（抽出來的優先），否則 `--check` 報的會是一份跟 session 實際
#   吃到的不同的憑證——那種不一致查起來要人命。
clear()
write(LIVE, days=30, plan="pro")
st = credentials_state()
check("只有 ~/.claude 那份時用它（Linux 的正常情況）", st["plan"] == "pro")
check("detail 指明來源是 ~/.claude", "~/.claude" in st["detail"])
write(config.CREDENTIALS_HOST, days=30, plan="max")
st = credentials_state()
check("兩份都在時用抽出來的那份（與 build_run_kwargs 疊加的方向一致）",
      st["plan"] == "max")

print("== 憑證掛進容器：掛哪一份、用什麼模式（ADR 0016）==")
# ⚠ 這裡曾經是 `_require_credentials_mountpoint()` 的測試。憑證以前是**巢狀** bind mount
#   （疊在 ~/.claude 之上），runc 不會替你在 host 上建 mountpoint，缺席就 exit 125，所以
#   要先擋下來。ADR 0016 之後憑證掛進 `/home/nathan/.claude-creds/`——那個目錄在 image 裡
#   不存在，docker 是在**容器 rootfs 內**建目錄鏈，不是巢狀掛載，整個問題連同那個函式一起
#   消失（refresh-credentials.sh 的 ensure_mountpoint() 同步退場）。
#
# 接手的是這一條：**掛哪一份、什麼模式**。規則是「這個檔是不是真相來源」而不是看平台：
#   快照 → ro（容器換發的結果寫回一份 host 根本不讀的副本，沒有意義）
#   真檔 → rw（Linux 上 session 是**唯一**的換發者，掛 ro 就是數小時後全部登出）
_saved_mounts = config.MOUNTS
try:
    config.MOUNTS = {"/shared": {"bind": "/shared", "mode": "rw"}}   # 非空才會掛憑證
    clear()
    write(config.CREDENTIALS_HOST)                    # 只有快照（macOS 的形狀）
    vols = build_run_kwargs("c", "s", Profile(), 1)["volumes"]
    check("快照掛 **ro**", vols.get(config.CREDENTIALS_HOST)
          == {"bind": config.CREDENTIALS_BIND, "mode": "ro"})

    clear()
    write(LIVE)                                       # 只有真檔（Linux 原生的形狀）
    vols = build_run_kwargs("c", "s", Profile(), 1)["volumes"]
    check("真檔掛 **rw**（session 是唯一的換發者）", vols.get(LIVE)
          == {"bind": config.CREDENTIALS_BIND, "mode": "rw"})

    # 落點必須是「專屬目錄底下的單一檔案」。⚠ 絕不可以改成掛整個 ~/.claude-pty 目錄：
    # 非容器化執行時那裡面還有 secret.key（cookie 的簽章金鑰）與預設的
    # SQLite registry，掛進 session 等於把簽章金鑰和整個資料庫發給每一個使用者。
    check("落點在專屬目錄底下，掛的是單一檔案",
          config.CREDENTIALS_BIND == config.CREDENTIALS_DIR_BIND + "/.credentials.json")
    check("掛的來源是檔案不是目錄", os.path.isfile(LIVE))

    write(config.CREDENTIALS_HOST, plan="max")        # 兩份都在
    vols = build_run_kwargs("c", "s", Profile(), 1)["volumes"]
    check("兩份都在時掛快照那份——與招牌徽章讀的是同一份（分岔就會「畫面說有、容器沒吃到」）",
          config.CREDENTIALS_HOST in vols and LIVE not in vols)

    clear()
    vols = build_run_kwargs("c", "s", Profile(), 1)["volumes"]
    check("一份都沒有就不掛（擋下建立是 _guard_credentials 的職責，不是這裡)",
          not any(v["bind"] == config.CREDENTIALS_BIND for v in vols.values()))
finally:
    config.MOUNTS = _saved_mounts

print("== credentials_state()：形狀是 {cli: state} ==")
both = all_credentials_state()
check("只有 claude 一把鑰匙", set(both) == {"claude"})
check("brand 正確（畫面的品牌標誌靠它）", both["claude"]["brand"] == "anthropic")

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{_pass} passed, {_fail} failed")
sys.exit(1 if _fail else 0)
