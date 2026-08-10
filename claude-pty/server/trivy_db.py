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


def is_fresh(now: _dt.datetime | None = None) -> bool:
    """距離上次成功更新，還在節流間隔內嗎。

    ⚠ 這**不是**在判斷 trivy 的 DB 本身新不新鮮，只是「要不要費事起一顆容器」的節流器。
      真正的鮮度判斷在容器裡由 trivy 自己做——`--download-db-only` 本來就會看 metadata
      決定要不要抓。所以這支保守一點（讀不到就回 False → 起容器）永遠是安全的，
      只是多花一次容器啟動。

    ⚠ **為什麼不直接讀 volume 裡的 metadata.json**：那要把 volume 掛進控制平面，而控制
      平面的 image 沒有 `/home/nathan/.cache/trivy`。實測的規則是「**掛載時仍為空**就會被
      該 image 初始化」——掛了而沒寫東西還救得回來，但一旦有東西在 root 擁有的狀態下被
      寫進去就**永久**卡住，而且無聲。不掛它也達得到目的，就不要多開這個機會。
    """
    try:
        last = os.path.getmtime(config.TRIVY_DB_STAMP)
    except OSError:
        return False                       # 沒更新過（或看不到）→ 去更新
    now = now or _dt.datetime.now(_dt.timezone.utc)
    return (now.timestamp() - last) < config.TRIVY_DB_MIN_INTERVAL


def _touch_stamp() -> None:
    """記下「這次更新成功了」。失敗不影響結果，只是下次少一個節流的依據。"""
    try:
        os.makedirs(os.path.dirname(config.TRIVY_DB_STAMP), exist_ok=True)
        with open(config.TRIVY_DB_STAMP, "w", encoding="utf-8") as f:
            f.write(_dt.datetime.now(_dt.timezone.utc).isoformat())
    except OSError:
        pass


def _has_db() -> bool | None:
    """曾經成功更新過嗎——**用時間戳推**，不是去看 DB 檔。

    回 True／False／None（無從判斷）。

    ⚠ cache 改成 named volume 之後，控制平面**看不到**那份 DB（理由見 `is_fresh`），
      所以「有沒有既有 DB」只能從自己的紀錄推。判準是保守的：曾經成功過就當還有
      （→ `stale`，警告但照常開場），從沒成功過才敢說沒有（→ `missing`）。
      寧可把「其實沒有」報成 stale，也不要把「其實有」報成 missing——後者會讓人白跑
      一趟去救一個沒壞的東西。
    """
    try:
        os.stat(config.TRIVY_DB_STAMP)
        return True
    except FileNotFoundError:
        # 目錄看得到 → 真的沒更新成功過；連目錄都看不到才是無從判斷。
        return False if os.path.isdir(os.path.dirname(config.TRIVY_DB_STAMP)) else None
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

    if is_fresh():
        return {"status": "fresh", "detail": "距上次更新未滿節流間隔，未起容器"}

    owner = _owner()
    # ⚠ **租約這一層自己也會拋。** `acquire_lease` 走 SQLite 的 BEGIN IMMEDIATE，
    #   busy_timeout 用盡就是 OperationalError。初版把它放在 try 外面，於是「永遠不拋」
    #   這個寫在四個地方的合約，唯一還會拋的那層剛好沒被包住——而測試只驗過 docker 層。
    try:
        got = acquire_lease(LEASE_NAME, owner, _lease_ttl())
    except Exception as e:                 # noqa: BLE001 — DB 忙碌／鎖不到都算這次做不成
        return {"status": "error", "detail": f"取租約失敗 {type(e).__name__}: {e}"}
    if not got:
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
            volumes={config.TRIVY_CACHE_VOLUME: {
                "bind": "/home/nathan/.cache/trivy", "mode": "rw"}},
            remove=True,
            detach=False,
            # ⚠ **不可以把 stdout 與 stderr 都設成 False。** `detach=False` 時 docker-py
            #   跑完會去撈那顆容器的輸出，兩個都關掉它就送出 `?stdout=0&stderr=0`，
            #   daemon 直接回 400「you must choose at least one stream」——於是**容器真的
            #   跑完了、DB 也更新了，這支卻回 error，時間戳也沒寫**，下一場再更新一次。
            #   2026-08-10 端到端實測踩到。假 client 的單元測試驗不到：它不會照真 API
            #   的規則檢查參數，只記下你傳了什麼。
            #   回傳的 bytes 直接丟掉——我們只要結束碼，不要那串進度條。
        )
        _touch_stamp()
        return {"status": "ok", "detail": "DB 已更新"}
    except docker.errors.ContainerError as e:
        # 容器跑起來了但 trivy 回非 0（離線、逾時、鏡像站掛掉）。
        has = _has_db()
        if has is False:
            return {"status": "missing",
                    "detail": f"更新失敗且沒有既有 DB，本次 A2 無 DB 可用：{e.exit_status}"}
        # has 為 None（看不到那個路徑）時也走這條：有沒有舊 DB 不確定，但「更新失敗」
        # 這件事是確定的，而它不該擋開場。
        # ⚠ 措辭不宣稱「沿用既有 DB」——那是從時間戳**推**的，不是看到 DB 檔。
        #   volume 被砍掉而時間戳還在的組合，說得太滿就是假話。
        return {"status": "stale",
                "detail": f"更新失敗；曾更新成功過，推定仍有可用的 DB（exit {e.exit_status}）"}
    except Exception as e:                 # noqa: BLE001 — daemon 不通／image 不在／逾時
        return {"status": "error", "detail": f"{type(e).__name__}: {e}"}
    finally:
        # 提早交還：這是一次性的短工作，讓租約壓著 TTL 只會讓下一個開場的人被誤判成
        # 「有人正在更新」而白跳過一次。
        # ⚠ **一定要吞掉例外。** finally 裡拋出去會**取代掉上面已經算好的回傳值**——
        #   更新明明成功了，呼叫端卻收不到那個 "ok"，狀態行就此消失。而「結果一定要
        #   印出來」正是這支存在的理由。還不掉頂多讓下一個人被判 skipped 一次，
        #   那比吃掉結果輕得多。
        try:
            release_lease(LEASE_NAME, owner)
        except Exception:                  # noqa: BLE001 — 見上
            pass
