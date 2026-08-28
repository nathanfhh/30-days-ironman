"""使用者空間的準備（從 sessions.py 拆出）。

ensure_system_user 與 provision_user_space 只被 SessionManager.create 用；
_write_json_atomic 是它們的工具。
"""

from __future__ import annotations

import json
import os
import shutil
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


def _open_child_dir(parent_fd: int, name: str) -> int:
    """在 `parent_fd` 底下建（若無）並開啟子目錄，確認它**真的是目錄**。

    ⚠ 這一層住在 `claude/` 底下，而 `claude/` 是 session 容器的 rw 掛載——掛載點本身
    容器換不掉，**它裡面的東西容器換得掉**。所以不能用字串路徑 `makedirs`／`copytree`：
    容器把 `skills` 換成一條指向別處的連結之後，控制平面就會以自己的身分照著那條連結
    去別的地方建目錄、寫檔案。這正是 `persistent-data/uploads` 那段在防的事，換一個
    目錄名不會換一個結論。

    ⚠ 兩行合起來才擋得住，缺一不可，**不要改寫成 `makedirs(..., exist_ok=True)`**：
      · `mkdir` 對「這個名字已經是一條 symlink」回的是 **EEXIST，不會跟著連結走**去
        對方指定的地方建目錄。`exist_ok=True` 的 `makedirs` 把這個 EEXIST 吞掉之後
        還會多做一次 `os.path.isdir()`，而那是**解析連結**的檢查，指向目錄的連結會被
        判成「已經有了，沒事」，於是下一步就照著它走出去。
      · `os.open` 的 `O_NOFOLLOW`（配 `O_DIRECTORY`）把上面那條 EEXIST 兜起來：名字被
        佔住時它拒絕開，我們才有機會拒絕開場而不是接手一個被動過手腳的空間。
    ⚠ 所以判斷「它是不是被換掉了」**只能看 open 有沒有失敗，不可以拿 errno 分型別**：
      同一個 `O_NOFOLLOW|O_DIRECTORY` 打在 symlink 上，macOS 回 ENOTDIR、Linux 回 ELOOP。
    """
    with suppress(FileExistsError):
        os.mkdir(name, 0o700, dir_fd=parent_fd)
    try:
        fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
    except OSError as e:
        # 開不起來＝它不是一個正常目錄（被換成連結或普通檔）。**拒絕開場**，不是繞過：
        # 繞過等於接受一個已經被動過手腳的空間。
        raise SessionError(
            f"使用者空間裡的 claude/{name} 不是一個正常目錄（{e}）。"
            f"這通常代表容器內有東西把它換掉了，先人工檢查再開場。"
        ) from e
    with suppress(OSError):
        os.fchmod(fd, 0o700)
    return fd


def _replace_tree(parent_fd: int, root: str, name: str, src: str) -> None:
    """把 `src` 這棵樹整個換到 `parent_fd` 底下的 `name`（先在別處組好，再一次換過去）。

    先刪再建，不是就地覆寫：覆寫留得下上一版多出來的檔案（skill 改名、reference 刪掉），
    而那些殘檔會被模型照樣讀進去。

    ⚠ **全程不可以出現一條從字串路徑走下來、指到 `parent_fd` 那一層的名稱。** `claude/`
      是 session 容器的 rw 掛載，它裡面的東西容器換得掉；而字串路徑的每個 syscall 都會
      重走一次名稱解析，`_open_child_dir` 用 `O_NOFOLLOW` 驗過的結果當場作廢：容器只要
      在驗證後把 `skills` rename 掉再補一條 symlink，寫入就落到它指定的地方，不必競速。
      所以刪與換都對著 `parent_fd` 做：

      · `shutil.rmtree(..., dir_fd=)`：Python 3.11 起支援（pyproject 的
        `requires-python = ">=3.11"` 正好在線上），macOS 上 `shutil._use_fd_functions`
        也是 True，內部走的就是 fd 相對的 `_rmtree_safe_fd`。**不要自己寫一支
        `_rmtree_at`**：標準庫這支已經逐層 `O_NOFOLLOW` 開、再比對 st_dev/st_ino，
        自己寫只會少幾道。
      · `os.rename(..., dst_dir_fd=)`：目的端的**最後一個路徑元件不解析 symlink**。
        於是 `skills/<name>` 被換成 symlink 時 rename 回 ENOTDIR（來源是目錄、目的地不是），
        被換成非空目錄時回 ENOTEMPTY；**兩種都是失敗，不是誤寫到別處**。這就是拿來取代
        舊版 `/proc/self/fd/<fd>` 前綴的東西（那條路只有 Linux 有，macOS 上整套測試會紅）。

    ⚠ 暫存目錄放在 `root`（也就是 `<space>/user-N/`），**不是** `claude/` 底下。它安全的
      唯一理由是 `config.user_mounts()` 只把 `claude/`、`persistent-data/`、`ncr/` 這三層
      掛進容器，**root 那一層容器碰不到**，那裡是可信地面，才可以放心用字串路徑組樹。
      要動 `user_mounts()` 就要回來看這裡。
    ⚠ 名字用 `tempfile.mkdtemp` 不用 pid：控制平面是 threaded，同一個使用者同時開兩場是
      正常的，pid 命名會讓兩條執行緒共用同一個暫存目錄（同 `_write_json_atomic` 的警告）。
    """
    staging = None
    try:
        staging = tempfile.mkdtemp(dir=root, prefix=".skills-")
        # 先在可信地面上把整棵樹組好（`symlinks=True`：來源裡的連結原樣複製，不解析、
        # 不跟著走），再一次 rename 過去，中途失敗不會在使用者空間留下半棵樹。
        shutil.copytree(src, os.path.join(staging, name), symlinks=True)
        shutil.rmtree(name, dir_fd=parent_fd, ignore_errors=True)
        os.rename(os.path.join(staging, name), name, dst_dir_fd=parent_fd)
    except (OSError, RecursionError) as e:
        # OSError：rename 的 ENOTDIR／ENOTEMPTY／EEXIST（＝目的地被動過手腳）、mkdtemp 與
        #   copytree 的失敗（`shutil.Error` 本身就是 OSError 的子類）。
        # RecursionError：容器可以在目的地造一棵萬層深的樹，把遞迴版本的 rmtree 爆掉。
        # 兩者都要變成講得清楚的 SessionError，不接的話使用者拿到的是 500 HTML traceback，
        # 而 app.py 只有 SessionError 的 errorhandler。
        raise SessionError(
            f"鋪不進使用者空間的 claude/skills/{name}（{e}）。這通常代表容器內有東西把它換掉了，先人工檢查再開場。"
        ) from e
    finally:
        # 成功時 staging 只剩一個空殼（`name` 已經被 rename 走），失敗時裡面還有半棵樹。
        # 兩種都要清，否則使用者空間會慢慢長出一堆 `.skills-xxxx`。
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)


def _write_agent(agents_fd: int, name: str, body: bytes) -> None:
    """把一份 agent 定義寫進 `claude/agents/<name>`，不跟著任何連結走。

    ⚠ **不可以用 `shutil.copyfile`。** 它開目的地是 `open(dst, "wb")`——**跟著目的地的
    symlink 走**：truncate 連結指到的那個檔，連結本身原封不動留著。而 `claude/agents/`
    是 session 容器寫得到的，於是容器裡放一條
    `ncr-fresh-eyes.md → ../../../user-2/owner.json`（相對連結在容器裡是斷的，在 host
    側那一層解析出來正好是別人的空間），下一場 provision 就會以控制平面的身分把別人的
    `owner.json` 蓋成一份 markdown——那個使用者從此永久撞「擁有者標記讀不出來」開不了場。
    同一手可以指向 registry 的 SQLite，或任何 APP_UID 寫得到的檔。
    **沒有競速視窗，一次就成**（2026-08-28 實測）。

    所以：先 unlink（連結本身就是這樣被拆掉的，而不是被寫穿），再用
    `O_CREAT|O_EXCL|O_NOFOLLOW` 對著 `agents_fd` 建新檔——中間被搶著補一條連結進來的話
    `O_EXCL` 會擋下，寧可拒絕開場也不寫出去。
    """
    with suppress(OSError):
        os.unlink(name, dir_fd=agents_fd)
    try:
        fd = os.open(
            name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=agents_fd,
        )
    except OSError as e:
        raise SessionError(
            f"寫不進使用者空間的 claude/agents/{name}（{e}）。這通常代表容器內有東西把它換掉了，先人工檢查再開場。"
        ) from e
    try:
        os.write(fd, body)
    finally:
        os.close(fd)


def sync_skills_and_agents(root: str) -> list[str]:
    """把 repo 的 `skills/` 與各 skill 的 `agents/*.md` 鋪進這個使用者的 `claude/`。

    回傳這次鋪進去的 skill 名稱（測試與 log 用）。來源不在就什麼都不做——**不是錯**：
    有人只想用容器跑別的東西，沒有 skill 也該開得起來。

    每次開場都重鋪一遍，所以這裡同時是安裝與自我修復：repo 改了一行，下一場就吃得到，
    而使用者在容器裡把 skill 改壞了，下一場自己會好。ADR 0022。

    ⚠ **`agents/*.md` 要另外鋪一份到 `claude/agents/`。** 那是 Claude Code 真正認的位置；
      只把它們留在 `skills/<name>/agents/` 底下等於沒安裝，而沒安裝**不會報錯**——
      skill 會退到「用 general-purpose subagent 帶同一份 prompt」那條 fallback，
      掃描照跑、報表卻分不出誰是誰（見 config.SKILLS_SRC_SELF）。
    """
    src_root = config.SKILLS_SRC_SELF
    if not os.path.isdir(src_root):
        return []
    skills = sorted(d for d in os.listdir(src_root) if os.path.isdir(os.path.join(src_root, d)))
    if not skills:
        return []

    # 上一輪被硬 kill（OOM、重新部署）會在 root 底下留一個 `.skills-xxxx`。留著不影響
    # 正確性，但會一直長，所以每次開場順手清一遍。`root` 沒掛進容器，這裡用字串路徑是
    # 安全的（見 `_replace_tree` 的說明）。
    with suppress(OSError):
        for leftover in os.listdir(root):
            if leftover.startswith(".skills-"):
                shutil.rmtree(os.path.join(root, leftover), ignore_errors=True)

    claude_fd = os.open(os.path.join(root, "claude"), os.O_RDONLY | os.O_DIRECTORY)
    try:
        skills_fd = _open_child_dir(claude_fd, "skills")
        agents_fd = _open_child_dir(claude_fd, "agents")
    finally:
        os.close(claude_fd)
    try:
        # ⚠ 這裡曾經把 `skills_fd` 換回字串路徑 `/proc/self/fd/<fd>` 再交給 copytree。
        #   那條路只有 Linux 有：**macOS 上 `/proc` 不存在**，於是整支功能連同兩支不相干的
        #   測試一起紅在 `OSError: [Errno 30] Read-only file system: '/proc'`。
        #   現在改成 fd 全程不落地成路徑（`rmtree(dir_fd=)` + `rename(dst_dir_fd=)`），
        #   理由與前提寫在 `_replace_tree` 的 docstring。
        for name in skills:
            _replace_tree(skills_fd, root, name, os.path.join(src_root, name))
            # 一檔兩用：agents/*.md 既是 skill 的一部分（上面那棵樹裡有），也要單獨
            # 出現在 agents/ 才叫得動。檔名直接用原檔名，與 install.sh 的連法一致。
            src_agents = os.path.join(src_root, name, "agents")
            if not os.path.isdir(src_agents):
                continue
            for md in sorted(os.listdir(src_agents)):
                if not md.endswith(".md"):
                    continue
                with open(os.path.join(src_agents, md), "rb") as fh:
                    _write_agent(agents_fd, md, fh.read())
    finally:
        os.close(skills_fd)
        os.close(agents_fd)
    return skills


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

    # ⚠ 位置有講究：**擁有者驗證之後**（不確定這個空間屬於誰就不該往裡面鋪東西），
    #   **`.claude.json` 那段之前**（那段有一條 early return，擺在後面會被跳過）。
    sync_skills_and_agents(root)

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
