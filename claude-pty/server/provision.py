"""使用者空間的準備（從 sessions.py 拆出）。

ensure_system_user 與 provision_user_space 只被 SessionManager.create 用；
_write_json_atomic 是它們的工具。
"""

from __future__ import annotations

import json
import os
import tempfile
from contextlib import suppress


from . import config
from .db import session_scope
from .errors import SessionError
from .models import User


def ensure_system_user() -> int:
    """取得（必要時建立）預設的 system 使用者 id。

    sessions.user_id 為 NOT NULL FK，但真正的登入要到 ADR 0008 階段 4 才接上；在那之前
    所有 session 掛在這個 owner 下。password_hash 填不可用值（`!` 為 Unix 慣例的「停用」
    標記，argon2 驗證永遠不會通過），確保這個帳號無法被登入。
    """
    # ⚠ `immediate=True`：這是典型的「檢查再動作」（查有沒有 → 沒有就插），而 db.py 的
    #   模組 docstring 把那條規則寫成絕對的。它原本用預設的 deferred，是全樹唯一的反例
    #   （審查 F-037）——兩條執行緒同時走到會雙雙判定「不存在」，第二個插入撞 username
    #   UNIQUE 拋 IntegrityError，而 app.py 沒有它的 errorhandler → 500 HTML traceback。
    #   不會真的建出兩個 system 帳號（UNIQUE 擋住了），所以是錯誤呈現問題；但留著它，
    #   下一個人就有理由相信那條規則只是建議。
    with session_scope(immediate=True) as s:
        user = s.query(User).filter_by(username=config.SYSTEM_USERNAME).one_or_none()
        if user is None:
            user = User(username=config.SYSTEM_USERNAME, password_hash="!", is_admin=True)
            s.add(user)
            s.flush()
        return user.id


def _write_json_atomic(path: str, payload: dict) -> None:
    """把 JSON 原子地放到 `path`（先寫暫存、fsync、再 replace 就位）。

    ⚠ **不可以 `open(path,"w")` 直接寫。** 讀這個檔的是容器裡的 CLI，而它對「半截 JSON」
      的反應不是報錯而是**當成全新安裝**——三道互動對話全部回來，最後那道預設停在
      「No, exit」，driver 送出的第一個 Enter 就把容器收掉。行程在 write 中途被 kill
      （OOM、重新部署）留下的半截檔，就會讓那個使用者從此每一場都這樣死。

    ⚠ 暫存檔名必須是**每次呼叫**唯一，不可以用 pid。控制平面是 threaded（gunicorn
      `--threads 8`），而 provision 跑在交易之外——同一個使用者同時開兩場 session 是完全
      正常的（配額預設 10）。兩條執行緒拿到的是同一個 pid，於是開同一個暫存檔、交錯
      寫入，然後各自 replace 就位——正好產生這個函式要防的半截檔。
    """
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)  # 同目錄、同檔案系統 → POSIX 保證原子
    except BaseException:
        with suppress(OSError):
            os.unlink(tmp)  # 失敗不要留一地 .tmp-xxxx
        raise


def _claude_json_seed() -> dict:
    """第一次要寫進 per-user `.claude.json` 的內容。"""
    return {
        **config.CLAUDE_JSON_SEED,
        # 信任狀態是 per-project 的，key 就是容器內的 cwd。**用 config 的值組**，
        # 不可以寫死字面值。
        "projects": {config.WORKDIR: {"hasTrustDialogAccepted": True}},
    }


def provision_user_space(user_id: int, username: str) -> None:
    """備妥某個使用者的狀態空間（ADR 0014）。idempotent，每次建立 session 都會呼叫。

    **lazy 而不是建帳號時就建**：帳號早就存在了（這個功能是後來才加的），lazy 天生
    idempotent、不需要 backfill，而且「沒開過 session 的人不佔目錄」也比較乾淨。

      1. 建出要掛進去的目錄，**0700**。必須由我們建，不能讓 docker daemon 隱式
         建立——那樣在 Linux 上會是 root:root，容器內那個使用者（`config.SESSION_UID`，
         實測是 1001 不是直覺的 1000，見那個常數的說明）寫不進去，
         症狀是 claude 起得來但什麼都存不下（同 trivy 快取目錄那個坑）。
         0700 是因為 `ncr/mitm/` 裡是**完整的 API 請求本文**（prompt 全文）；預設的 0755
         在多帳號的 host 上等於發給每一個本機使用者。
      2. 驗**擁有者**（見下）。
      3. 備妥 `.claude.json`：沒有就寫種子；壞掉就重寫；好的就只補缺的 WORKDIR 信任 key。

    ⚠ **擁有者標記不是形式**。目錄名是 `user-{id}`，而 id 是 DB 的 autoincrement——
      它只在**同一份 registry 的生命週期內**穩定。SQLite 檔（deploy/data/，不進版控）
      一旦遺失或重建，id 會從 1 重發，新的 user-1 就直接繼承前一個 user-1 的 transcript、
      persistent-data 與 mitm/ 裡的 prompt 全文。ADR 0010「帳號不能刪」擋得住活 DB 內的
      重用，擋不住換代。所以第一次 provision 時把 username 寫進 `owner.json`，之後每次
      比對；對不上就**拒絕開**並要人工處理——靜默地把別人的對話交出去比擋下來糟得多。

    ⚠ `.claude.json` 的三種狀態要分開處理，不能只有「有／沒有」：
      - **沒有** → 寫種子。
      - **壞掉或空的** → 也要重寫。舊版寫到一半被 kill 會留下這種檔，而「存在就跳過」
        會讓它永遠修不好——那個使用者從此每一場都撞 onboarding。
      - **好的** → 只補一件事：`projects` 裡缺當前 `config.WORKDIR` 的信任 key 就補上。
        WORKDIR 一改，**既有使用者**的檔案裡不會有新 cwd 的信任狀態，下一場全部撞信任
        對話——這不是「只有第一場會遇到」的問題。補寫是 read-modify-write，理論上會與
        容器內正在寫同一個檔的 claude 互相覆蓋，但只在「剛改過 WORKDIR」這個罕見窗口
        內才會發生，而且被覆蓋掉的是 numStartups 那類會自己長回來的東西。
    """
    if not config.MOUNTS:  # 測試隔離：不建任何東西（同 user_mounts）
        return
    root = config.user_space(user_id, host=False)
    for sub in ("claude", "persistent-data", "ncr"):
        os.makedirs(os.path.join(root, sub), mode=0o700, exist_ok=True)
    # ⚠ `persistent-data/uploads` 由**控制平面**建，不讓上傳那條路徑臨時 `makedirs`。
    #
    # ⚠ 而且**不可以用 `makedirs` 建它**。上面那四層是掛載點本身、容器換不掉；`uploads`
    #   不是——它住在 `persistent-data/` 底下，而那一層是 session 容器的 rw 掛載，容器
    #   可以把它刪掉換成一條指向別處的連結。`makedirs` 會跟著連結走，於是控制平面（APP_UID
    #   的身分）就在對方指定的任意位置建出一個 0700 目錄；接著那圈 `chmod` 也會跟著走。
    #   這正是 app._open_uploads_dir 那支函式在防的事，在這裡自己踩一遍就白做了。
    #   所以走 mkdirat：拿 persistent-data 的 fd 當錨，開的時候 O_NOFOLLOW，
    #   權限用 fchmod 對著 fd 設，全程不經過字串路徑。
    _pd_fd = os.open(os.path.join(root, "persistent-data"), os.O_RDONLY | os.O_DIRECTORY)
    try:
        with suppress(FileExistsError):
            os.mkdir("uploads", 0o700, dir_fd=_pd_fd)
        try:
            _up_fd = os.open("uploads", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=_pd_fd)
        except OSError as e:
            # 開不起來＝它不是一個正常目錄（被換成連結或普通檔）。這是**拒絕開場**的理由，
            # 不是可以繞過的雜訊：繞過就等於接受一個已經被動過手腳的空間。
            raise SessionError(
                f"使用者空間裡的 persistent-data/uploads 不是一個正常目錄（{e}）。"
                f"這通常代表容器內有東西把它換掉了，先人工檢查再開場。"
            ) from e
        try:
            os.fchmod(_up_fd, 0o700)
        finally:
            os.close(_up_fd)
    finally:
        os.close(_pd_fd)
    # ⚠ `makedirs(mode=...)` **只對它新建的那一層生效**，已經存在的目錄權限不會動。
    #   所以每一層都要明確 chmod——升級前用預設 0755 建出來的空間，否則會一直維持
    #   世界可讀，而 `mitm/` 裡是完整的 API 請求本文。
    # ⚠ 這一圈**只涵蓋掛載點本身那四層**。`persistent-data/uploads` 不在裡面，因為
    #   `os.chmod` 跟著連結走，而那一層是容器換得掉的（見上）——它的權限在上面用
    #   `fchmod` 對著已經驗過的 fd 設好了。
    for d in (root, *(os.path.join(root, x) for x in ("claude", "persistent-data", "ncr"))):
        with suppress(OSError):
            os.chmod(d, 0o700)

    # ⚠ `username` 是**必要參數**，不給預設值。曾經是 `str | None = None`，而傳 None
    #   會讓下面整段擁有者驗證靜默跳過——那是這個函式最重要的一道防線，卻可以被一個
    #   省略的參數關掉，且簽章與呼叫端都看不出來。要它就一定要拿得出是誰。
    owner_path = os.path.join(root, "owner.json")
    try:
        with open(owner_path, encoding="utf-8") as f:
            owner = json.load(f)
    except FileNotFoundError:
        owner = None  # 真的還沒有人認領——這一種才可以蓋章
    except (OSError, ValueError) as e:
        # ⚠ **壞掉的標記不等於沒有標記。** 當成「還沒有擁有者」就會直接重新蓋章，
        #   把上一個人的 transcript、persistent-data 與 mitm/ 的 prompt 全文靜默
        #   交給現在這個帳號——那正是這個標記存在的理由。讀不出來就停下來問人。
        raise SessionError(
            f"{owner_path} 讀不出來（{e}）——在確認這個空間屬於誰之前不會繼續。"
            f"請人工檢查：內容還原得了就修好它，確定是要重新指派就把整個 "
            f"{root} 移走。"
        ) from e
    # ⚠ 「解析得出來」不等於「是我們寫的那個形狀」。內容是 `[]` 的話下面的
    #   `owner.get()` 會 AttributeError——那會變成 500，而不是這裡精心寫的
    #   SessionError。下面的 `.claude.json` 有這道 isinstance 護欄，這裡原本漏了。
    if owner is not None and not isinstance(owner, dict):
        raise SessionError(
            f"{owner_path} 的內容不是預期的物件（{type(owner).__name__}）——"
            f"在確認這個空間屬於誰之前不會繼續。請人工檢查後修好它，"
            f"或把整個 {root} 移走。"
        )
    if owner is None:
        # ⚠ 「沒有標記」只有在**空間本身也是全新的**時候才可以認領。已經有 .claude.json
        #   就代表這個目錄有人用過（那個檔是第一次 provision 就會寫的），而標記卻不在
        #   ——那是升級前留下的空間，或有人手動動過。直接蓋章一樣是把別人的 transcript
        #   與 mitm 全文交出去，只是換一條路徑到達同一個壞結果。
        if os.path.exists(os.path.join(root, "claude", ".claude.json")):
            raise SessionError(
                f"{root} 裡已經有資料，卻沒有擁有者標記（owner.json）。在確認它屬於誰"
                f"之前不會繼續：確定是 {username!r} 的就手動補上標記，不是的話把整個"
                f"目錄移走。"
            )
        _write_json_atomic(owner_path, {"user_id": user_id, "username": username})
    elif owner.get("username") != username:
        raise SessionError(
            f"{root} 是 {owner.get('username')!r} 的空間，但這個 session 的擁有者是 "
            f"{username!r}。這通常表示 registry 重建過、user id 被重新指派——"
            f"繼續下去會把別人的對話與 capture 交給現在這個帳號。請人工確認後"
            f"改名或移走那個目錄再試。"
        )

    seed_path = os.path.join(root, "claude", ".claude.json")
    try:
        with open(seed_path, encoding="utf-8") as f:
            existing = json.load(f)
        if not isinstance(existing, dict):
            raise ValueError("不是物件")
    except (FileNotFoundError, OSError, ValueError):
        # 三種都寫種子，主體一樣所以合成一條：不存在（第一次）、空的、壞的。
        # 後兩種不是「使用者的狀態」而是上一次寫到一半的殘骸——當成狀態跳過的話，
        # 那個使用者從此每一場都撞 onboarding，而且永遠修不好。
        _write_json_atomic(seed_path, _claude_json_seed())
        return
    # ⚠ 這裡有**第四態**：內容是有效的 dict，但根本沒有 `projects` 鍵（或它不是 dict）。
    #   原本寫成 `if isinstance(projects, dict) and ...`，那個情況會靜靜地什麼都不做，
    #   信任 key 永遠補不上去。缺鍵就當成空的補進去。
    projects = existing.get("projects")
    if not isinstance(projects, dict):
        projects = existing["projects"] = {}
    if config.WORKDIR not in projects:
        projects[config.WORKDIR] = {"hasTrustDialogAccepted": True}
        _write_json_atomic(seed_path, existing)
