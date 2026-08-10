"""trivy 漏洞 DB 的更新——**網頁這條路徑原本完全沒有這件事**。

## 為什麼需要它

A2 軌道（trivy 掃描）要有 DB 才做得了事，而 DB 要從 ghcr.io / mirror.gcr.io 抓。
兩條路徑本來只有一條在更新：

  - **run script**：每次啟動前，在**容器起來之前**、另一顆一次性容器裡更新一次
    （`--entrypoint bash` 繞過選單、不套防火牆）。
  - **網頁**：`entrypoint.sh` 裡 `trivy` 的出現次數是 **0**。也就是說，從網頁開的
    session **從來沒有更新過 DB**，全靠「這台機器上曾經有人跑過 run script」。

而 restricted profile 的白名單沒有 ghcr.io，所以 session 自己在牆內也抓不到——
DB 過期就是一路過期下去，而 A2 照樣「跑完」、照樣回報，只是用著愈來愈舊的資料。

⚠ `config.MOUNTS` 那段註解一度寫著「entrypoint.sh 在套 iptables 之前必須等 DB 更新
  跑完」。**那個機制不存在**，註解描述的是一個沒有人實作的世界。這支就是把它補上，
  但補在控制平面而不是 entrypoint，理由見下。

## 為什麼放控制平面，不是 entrypoint

實測（2026-08-10）兩個併發的 `trivy image --download-db-only` 打同一個 cache：

  - 兩邊都 exit 0，產出的 DB 拿去掃 `requests==2.19.1` 回 5 筆，與已知良好的 DB 逐筆
    相同 → **不會壞**。靠的是「下載到暫存、完成後 atomic rename」，不是互斥鎖
    （cache 目錄裡沒有任何鎖檔）。
  - 但**兩邊都真的下載了 103.85 MiB，沒有去重**。

所以放在每一場的 entrypoint，N 場同時開就是 N 份下載；放控制平面是單一入口，一個
租約就串行化得掉。代價是與 run script 的邏輯各寫一份，那是划算的交換。
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import socket

import docker

from . import config
from .leases import acquire_lease, release_lease

# 租約名稱。與 reconciler 的 "reconciler" 分開：這兩件事沒有互斥關係，共用一個名字
# 只會讓對帳輪次無謂地擋住 DB 更新。
LEASE_NAME = "trivy-db"

# 租約 TTL。要**大於**那顆一次性容器的逾時上限，否則更新還沒跑完租約就過期，
# 第二個人會判定可接手而跑第二份下載——正是這張租約要防的事。
_LEASE_SLACK = 60


def _lease_ttl() -> int:
    return config.TRIVY_DB_TIMEOUT + _LEASE_SLACK


def _owner() -> str:
    """租約持有者的識別。同一台機器上多個 worker 要各自可辨識。"""
    return f"{socket.gethostname()}:{os.getpid()}"


def is_fresh(now: _dt.datetime | None = None) -> bool | None:
    """看 metadata.json 判斷 DB 還新不新鮮。

    回 `True`／`False`／`None`（讀不到，無從判斷）。

    ⚠ **讀不到一律當「不新鮮」處理**（呼叫端把 None 視同 False）。理由是這支讀的是
      `TRIVY_CACHE_SELF`——控制平面自己看得到的那個路徑——而它不保證存在（本機開發、
      或哪天 cache 改成 named volume 就讀不到了）。讀不到就起容器，讓 trivy 自己用
      `--download-db-only` 判斷：它本來就會檢查鮮度，該 no-op 的時候會 no-op。
      這條 fallback 讓「省一顆容器」變成純優化，而不是正確性的前提。
    """
    path = os.path.join(config.TRIVY_CACHE_SELF, "db", "metadata.json")
    try:
        with open(path, encoding="utf-8") as f:
            meta = json.load(f)
        nxt = meta.get("NextUpdate")
        if not nxt:
            return None
        # trivy 寫的是 RFC3339；Python 3.11 前的 fromisoformat 不吃結尾的 Z。
        deadline = _dt.datetime.fromisoformat(str(nxt).replace("Z", "+00:00"))
    except Exception:                      # noqa: BLE001 — 檔案不在／壞掉／格式改了都算讀不到
        return None
    now = now or _dt.datetime.now(_dt.timezone.utc)
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=_dt.timezone.utc)
    return now < deadline


def _has_db() -> bool | None:
    """本機看得到既有的 DB 檔嗎。回 True／False／None（無從判斷）。

    ⚠ 「檔案不在」與「這個路徑我根本看不到」是**兩個不同的答案**，不可以混為一談：
      前者是確定沒有 DB（→ `missing`，要明講 A2 這場沒東西可掃），後者只是控制平面
      看不到那份 cache（本機開發、或哪天改成 named volume），那時不該擅自宣稱沒有。
      初版把兩者都回 None，於是「真的沒有 DB」被降級報成 `stale`——測試當場抓到。
    """
    path = os.path.join(config.TRIVY_CACHE_SELF, "db", "trivy.db")
    try:
        return os.path.getsize(path) > 0
    except FileNotFoundError:
        # cache 根目錄看得到 → 檔案不在就是真的不在；連根都看不到才是無從判斷。
        return False if os.path.isdir(config.TRIVY_CACHE_SELF) else None
    except OSError:
        return None


def update(client: docker.DockerClient | None = None) -> dict:
    """必要時更新 trivy DB。**永遠不拋**，回一份可以直接寫進 log 的結果。

    `{"status": ..., "detail": "..."}`，status 六選一：

      - `"disabled"` — 部署者關掉了（`CLAUDE_PTY_TRIVY_DB_UPDATE`）
      - `"fresh"`    — 還新鮮，連容器都沒起
      - `"skipped"`  — 租約在別人手上（有人正在更新），這次不等
      - `"ok"`       — 更新成功
      - `"stale"`    — 更新失敗，但有既有 DB 可以用
      - `"missing"`  — 更新失敗且沒有既有 DB，A2 這一場等於沒有 DB
      - `"error"`    — docker 本身出問題

    ⚠ **這是選配設施，不是建立 session 的前提。** 任何失敗都只降級、不擋開場——
      沒有 DB 的 A2 由 skill 自己走它的降級規則（跳過並揭露），那比「開不了場」好。
    """
    if not config.TRIVY_DB_UPDATE:
        return {"status": "disabled", "detail": "CLAUDE_PTY_TRIVY_DB_UPDATE 已關閉"}

    if is_fresh() is True:
        return {"status": "fresh", "detail": "DB 仍在有效期內，未起容器"}

    owner = _owner()
    if not acquire_lease(LEASE_NAME, owner, _lease_ttl()):
        # 不等待：等於白白讓開場慢一個下載的時間，而對方成功之後這一場自然就命中快取。
        return {"status": "skipped", "detail": "另一個執行者正在更新，這次跳過"}

    try:
        c = client or docker.from_env(timeout=config.DOCKER_TIMEOUT)
        # ⚠ **不可以帶 session 的 label。** 帶了的話 reconciler 的孤兒清理會把這顆
        #   「有 label 卻不在 DB 裡」的容器當成孤兒——雖然它 --rm 很快就走，但那是在
        #   賭時序。不帶 label 就完全不在對帳的視野裡。
        # ⚠ 走 image 的預設使用者（nathan），不指定 --user：cache 的擁有權要與 session
        #   容器寫進去的那些檔案一致。
        c.containers.run(
            config.IMAGE,
            command=["-c", f"timeout -k 10 {config.TRIVY_DB_TIMEOUT} "
                           f"trivy image --download-db-only"],
            entrypoint="bash",
            volumes={config.TRIVY_CACHE_HOST: {
                "bind": "/home/nathan/.cache/trivy", "mode": "rw"}},
            remove=True,
            detach=False,
            stdout=False,
            stderr=False,
        )
        return {"status": "ok", "detail": "DB 已更新"}
    except docker.errors.ContainerError as e:
        # 容器跑起來了但 trivy 回非 0（離線、逾時、鏡像站掛掉）。
        has = _has_db()
        if has is False:
            return {"status": "missing",
                    "detail": f"更新失敗且沒有既有 DB，本次 A2 無 DB 可用：{e.exit_status}"}
        # has 為 None（看不到那個路徑）時也走這條：有沒有舊 DB 不確定，但「更新失敗」
        # 這件事是確定的，而它不該擋開場。
        return {"status": "stale",
                "detail": f"更新失敗，沿用既有 DB（exit {e.exit_status}）"}
    except Exception as e:                 # noqa: BLE001 — daemon 不通／image 不在／逾時
        return {"status": "error", "detail": f"{type(e).__name__}: {e}"}
    finally:
        # 提早交還：這是一次性的短工作，讓租約壓著 TTL 只會讓下一個開場的人被誤判成
        # 「有人正在更新」而白跳過一次。
        release_lease(LEASE_NAME, owner)
