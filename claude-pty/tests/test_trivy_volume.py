"""trivy cache named volume 的擁有權（ADR 0018）——**需要 docker 與 build 好的 image**。

    uv run --with docker python tests/test_trivy_volume.py

這支守的是整個 ADR 0018 賴以成立的那一件事：**volume 的擁有者是從 image 複製過來的**。
它同時把反面釘住，而反面的**精確條件**是實測出來的：初始化的觸發是「掛載時仍為空」，
不是「第幾次掛載」。所以被錯的 image 掛過而沒寫東西還救得回來；一旦在 root 擁有的狀態下
**被寫進任何東西**，volume 就永久卡在 root，而那是無聲的。
"""
import os
import subprocess
import sys
import uuid

IMAGE = os.environ.get("CLAUDE_PTY_IMAGE", "ncr-dev-container")
BIND = "/home/nathan/.cache/trivy"

_pass = _fail = 0


def check(label, ok):
    global _pass, _fail
    _pass += ok
    _fail += (not ok)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")


def run(*args, **kw):
    return subprocess.run(args, capture_output=True, text=True, **kw)


def owner_of(vol, image=IMAGE, entry="sh"):
    """掛上去問那個目錄的數字擁有者。回 (uid, gid) 或 None。"""
    r = run("docker", "run", "--rm", "--user", "root", "--entrypoint", entry, "-v",
            f"{vol}:{BIND}", image, "-c", f"stat -c '%u %g' {BIND}")
    out = r.stdout.strip().split()
    return tuple(out) if len(out) == 2 else None


def rm_vol(vol):
    run("docker", "volume", "rm", "-f", vol)


if run("docker", "version").returncode != 0:
    print("SKIP：docker 不可用")
    sys.exit(0)
if run("docker", "image", "inspect", IMAGE).returncode != 0:
    print(f"SKIP：找不到 image {IMAGE}（先 build）")
    sys.exit(0)

# image 裡宣告的 uid。ADR 0017 把它 stamp 成 LABEL/ENV，這裡就用它當期望值——
# 寫死 1001 的話，哪天有人用 --build-arg NCR_UID 換掉，這支會變成假紅燈。
_lbl = run("docker", "image", "inspect", "-f",
           "{{index .Config.Labels \"ncr.uid\"}}", IMAGE).stdout.strip()
EXPECT_UID = _lbl if _lbl and _lbl != "<no value>" else None

print(f"== image {IMAGE} 宣告的 uid：{EXPECT_UID or '（沒有 stamp，這支只能驗相對關係）'} ==")

print("\n== 全新的空 volume，由 session image 先掛 → 擁有者要是 nathan ==")
vol = f"trivytest-{uuid.uuid4().hex[:8]}"
try:
    got = owner_of(vol)
    check("問得到擁有者", got is not None)
    if EXPECT_UID:
        check(f"🔴 volume 的擁有者 == image 的 NCR_UID（{EXPECT_UID}）",
              got is not None and got[0] == EXPECT_UID)
    check("🔴 而且不是 root（是 root 就代表初始化沒有從 image 複製到）",
          got is not None and got[0] != "0")
    # 真的寫得進去才算數：擁有者對但 mode 不對一樣是壞的。
    w = run("docker", "run", "--rm", "--entrypoint", "sh", "-v", f"{vol}:{BIND}",
            IMAGE, "-c", f"touch {BIND}/.probe && echo OK")
    check("🔴 image 的預設使用者真的寫得進去", "OK" in w.stdout)
finally:
    rm_vol(vol)

print("\n== 反面：root 擁有 + 被寫過 → 永久卡住 ==")
# ⚠ 精確的規則是「**掛載時仍為空**就會被該 image 初始化」，不是「只有第一次掛載」。
#   所以「被錯的 image 掛過」本身不致命——只要沒人往裡面寫，下一次由正確的 image 掛
#   就會被修好（下面 case A）。致命的是 **在 root 擁有的狀態下被寫入**（case B）：
#   volume 從此非空，再也不會被初始化，nathan 永久寫不進去。
vol = f"trivytest-{uuid.uuid4().hex[:8]}"
try:
    run("docker", "run", "--rm", "--user", "root", "-v", f"{vol}:{BIND}",
        "alpine", "true")                      # 掛了但沒寫東西
    after = owner_of(vol)
    check("case A：被 root 掛過但仍為空 → 下次由正確 image 掛會被修好",
          after is not None and after[0] != "0")
finally:
    rm_vol(vol)

vol = f"trivytest-{uuid.uuid4().hex[:8]}"
try:
    run("docker", "run", "--rm", "--user", "root", "-v", f"{vol}:{BIND}",
        "alpine", "sh", "-c", f"touch {BIND}/x")   # 以 root 寫了東西
    after = owner_of(vol)
    check("🔴 case B：root 寫過之後 → 擁有者永久是 root", after is not None and after[0] == "0")
    w = run("docker", "run", "--rm", "--entrypoint", "sh", "-v", f"{vol}:{BIND}",
            IMAGE, "-c", f"touch {BIND}/.probe && echo OK")
    check("🔴 於是 image 的預設使用者寫不進去（restricted 會卡滿逾時）",
          "OK" not in w.stdout)
finally:
    rm_vol(vol)

print("\n== 回歸守衛：控制平面不可以掛這顆 volume ==")
_here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(_here, "deploy", "docker-compose.yml"), encoding="utf-8") as f:
    compose = f.read()
_svc = compose.split("volumes:\n  # trivy")[0]      # 頂層 volumes 宣告之前的部分＝各服務
check("🔴 沒有任何服務把 trivy cache 掛進去（理由見上面那組反面測試）",
      f":{BIND}" not in _svc)

print(f"\n{_pass} passed, {_fail} failed")
sys.exit(1 if _fail else 0)
