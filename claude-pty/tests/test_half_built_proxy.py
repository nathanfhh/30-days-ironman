"""停在 `created` 的代理：夠舊的要收掉重建，還新的一定不能碰。

    uv run --with flask --with docker --with sqlalchemy --with argon2-cffi \
        --with psutil --with cryptography python tests/test_half_built_proxy.py

`is_stale_half_built` 是這個判準的**唯一一份**，而在這支測試之前**沒有任何測試碰過它**
（審查 F-006：全樹 grep 只出現在 server/，兩個呼叫端各一次）。它守的事故寫在
`sessions._ensure_user_proxy` 的註解裡：

  停在 `created` 的代理有兩種來源，外觀完全一樣——
    · `create_container` 完成但 `put_archive` 還沒跑 → `/etc/nginx` 還是 **image 的預設**
    · `put_archive` 完成但 `start` 還沒跑 → 設定是對的
  start 第一種會得到一顆**永久的殭屍**：nginx 用預設設定開在 80，容器狀態變成 `running`
  看起來很健康，但 `gitlab-proxy:5678` 連不上；此後 reconciler 只走 running 分支、
  `/_state` 問不到，依「問不到就別亂動」**永遠不修**。

分不出來，所以一律當半成品收掉重建——**但只有夠舊的才收**。還新的話那是別的 worker
正在建（同一時間兩場 session 是常態），碰它就是把人家建到一半的容器刪掉，而那正是
`create_or_adopt` 吸收 409 要防的事。

⚠ 兩個方向缺一不可，而且兩邊的代價**不對稱**：
  · 該收沒收 → 那個人的 GitLab 永久失效，要等他下次再開一場才被救回來
  · 不該收卻收了 → 刪掉別的 worker 正在建的容器，兩場都拿不到代理

⚠ 這支刻意**不需要 docker**：判準是純函式（只看 `status` 與 `Created`），用假容器就驗得完。
  真的把容器留在 `created` 再驗 reconciler 收不收，屬於 test_user_proxy 那一層。
"""

import os
import sys
import datetime as _dt

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from server import config  # noqa: E402
from server.sessions import is_stale_half_built  # noqa: E402

_fails = 0


def check(label, ok):
    global _fails
    if not ok:
        _fails += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    return ok


def _iso(seconds_ago: float) -> str:
    """docker 風格的 RFC3339 時間戳（奈秒精度、Z 結尾），距今 N 秒。"""
    t = _dt.datetime.now(_dt.UTC) - _dt.timedelta(seconds=seconds_ago)
    return t.strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond:06d}000Z"


class _C:
    """一顆假的代理容器：判準只讀 `status` 與 `attrs["Created"]`。"""

    def __init__(self, status, created_ago=None, created=None):
        self.status = status
        self.attrs = {"Created": _iso(created_ago) if created_ago is not None else ("" if created is None else created)}


GRACE = config.ORPHAN_GRACE

print(f"== 只有 created 才是候選（ORPHAN_GRACE={GRACE}s）==")
# ⚠ 其他狀態一律 False——這條擋的是「把判準拿去問一顆正在服務的 running 代理」。
#   `_ensure_user_proxy` 與 `_converge_proxies` 都是在 created 分支裡才呼叫它，但判準
#   自己要能站得住：它是共用的，而共用的東西遲早會被在別的分支呼叫。
for st in ("running", "exited", "restarting", "paused", "dead", "removing"):
    check(f"status={st} → 不是半成品（不管多舊）", is_stale_half_built(_C(st, created_ago=GRACE * 10)) is False)

print("\n== created + 夠舊 → 收掉重建 ==")
check(
    "🔴 剛好跨過寬限期就要收（否則那個人的 GitLab 永久失效）",
    is_stale_half_built(_C("created", created_ago=GRACE + 1)) is True,
)
check("遠比寬限期舊的當然要收", is_stale_half_built(_C("created", created_ago=GRACE * 100)) is True)

print("\n== created + 還新 → 絕對不能碰（那是別的 worker 正在建）==")
# 🔴 這個方向才是這支測試存在的主要理由。反過來寫（一律收）在單機、單一 worker 下**不會
#    有任何症狀**，要等到兩場 session 同時開才會炸，而那時的症狀是「兩場都沒有代理」，
#    完全指不到這個判準。
check("🔴 剛建出來的不可以收", is_stale_half_built(_C("created", created_ago=0)) is False)
check("🔴 寬限期內的不可以收", is_stale_half_built(_C("created", created_ago=GRACE - 5)) is False)

print("\n== 邊界：時間戳解不出來時的方向 ==")
# ⚠ `age_seconds` 對解析失敗回 inf（＝很舊 ＝ 收），對空字串回 0.0（＝很新 ＝ 不收）。
#   兩者方向相反，而這件事只寫在 age_seconds 的 docstring 裡、沒有測試釘過。這兩條
#   把現行行為寫下來——不是主張它是對的（空字串那條的方向與 docstring 花整段解釋的
#   相反，已列為報告的 Q-002），而是讓「有人改了它」變成看得見的事。
check(
    "解不出來的時間戳 → 當成很舊（收掉，寧可重建）", is_stale_half_built(_C("created", created="這不是時間戳")) is True
)
check(
    "空的時間戳 → 當成很新（不收）※ 現行行為，方向與 age_seconds docstring 相反",
    is_stale_half_built(_C("created", created="")) is False,
)

print(f"\n{'done' if _fails == 0 else f'{_fails} FAILED'}")
sys.exit(1 if _fails else 0)
