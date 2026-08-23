"""互斥租約：讓「同一件事同一時間只有一個執行者」在多 process 下也成立。

原本住在 `reconciler.py`，只有對帳器一個使用者。搬出來的原因是 `sessions.py` 也需要它
（建 session 前的 trivy DB 更新要串行化），而 `reconciler` 已經 import `sessions`——
留在原地就是循環 import。

⚠ 這裡刻意**不 import 任何本專案的模組**，只依賴 db 與 models。它是最底層的原語，
  一旦讓它認識上層模組，循環 import 就會從另一個方向長回來。
"""

from __future__ import annotations

import datetime as _dt

from .db import session_scope
from .models import Lease, utcnow


def acquire_lease(name: str, owner: str, ttl: int) -> bool:
    """取得/續約互斥租約；被別人持有且未過期則回 False（review M2）。

    交易以 immediate 開啟，讓「讀租約 → 判斷 → 寫回」在 SQLite 下也是互斥的，
    否則兩個執行者可能同時判定「沒人持有」。

    ⚠ 互斥靠的就是這個 immediate：漏了它，兩個執行者同時看到一張過期的租約
      時會雙雙判定可接手，接著同時跑破壞性清理——正是這張租約要防的事
      （`sessions.create()` 的配額交易與 `views._claim_port` 是同一個形狀的坑）。
    """
    now = utcnow()
    with session_scope(immediate=True) as s:
        row = s.get(Lease, name)
        if row is None:
            s.add(Lease(name=name, owner=owner, expires_at=now + _dt.timedelta(seconds=ttl)))
            return True
        if row.owner != owner and row.expires_at > now:
            return False  # 別人持有中且未過期
        row.owner = owner  # 自己續約，或接手已過期的租約
        row.expires_at = now + _dt.timedelta(seconds=ttl)
        return True


def still_leader(name: str, owner: str) -> bool:
    """租約是否仍屬於自己且未過期。

    租約只在每輪**開頭**取得，但一輪可能跑很久（大量 exited container 要逐個
    force-remove）。跑超過 TTL 時另一個實例會合法接手，而舊的仍在迴圈裡做破壞性操作
    ——兩個執行者同時刪同一批東西。破壞性動作前再確認一次，過期就讓這輪停手
    （下一輪重新競爭租約）。
    """
    with session_scope() as s:
        row = s.get(Lease, name)
        return bool(row and row.owner == owner and row.expires_at > utcnow())


def release_lease(name: str, owner: str) -> bool:
    """提早交還租約；不是自己的就不動，回傳有沒有真的還掉。

    對帳器不需要這支（它跑完一輪就等下一輪重新競爭，過期即可）。trivy DB 更新需要：
    那是**一次性**的短工作，做完之後如果還讓租約壓著 TTL，同一時間開第二場的人會被
    判成「別人正在更新」而白白跳過一次本來做得到的檢查。
    """
    with session_scope(immediate=True) as s:
        row = s.get(Lease, name)
        if row is None or row.owner != owner:
            return False
        s.delete(row)
        return True
