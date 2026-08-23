"""帳號與認證（ADR 0005 authn/authz、ADR 0008 users 表）。

密碼一律 **argon2id**（argon2-cffi 的預設演算法），絕不明文、不用 sha256/md5、不自刻。
登入狀態走 Flask 的簽章 cookie——多 worker 共用同一把 SECRET_KEY 即可互相驗證，
不需要伺服端 session 儲存（KISS）。
"""

from __future__ import annotations

import unicodedata
from contextlib import suppress

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from . import config, crypto, gitlab_proxy
from .db import session_scope
from .models import User

_ph = PasswordHasher()  # 預設即 argon2id，參數為 argon2-cffi 建議值

# 使用者不存在時拿來做假驗證的雜湊，讓「查無此人」與「密碼錯」耗時相近，
# 避免以回應時間列舉帳號（user enumeration）。
_DUMMY_HASH = _ph.hash("dummy-password-for-constant-time-compare")


class AuthError(RuntimeError):
    pass


# --- 密碼 -------------------------------------------------------------------------


def hash_password(password: str) -> str:
    _validate_password(password)
    return _ph.hash(password)


def _validate_password(password: str) -> None:
    if not isinstance(password, str) or len(password) < config.MIN_PASSWORD_LENGTH:
        raise AuthError(f"密碼長度至少 {config.MIN_PASSWORD_LENGTH} 字元")


# --- 使用者操作 --------------------------------------------------------------------

# 「印得出來卻看不見」的字元：`isprintable()` 是 True、`isspace()` 也是 False，所以穿得過
# 一般的檢查——但在清單、下拉、稽核紀錄上，`admin` 與 `adminㅤ` 肉眼**完全相同**，而帳號
# 不能刪（ADR 0010），混進來就永遠分不出誰是誰。
#
# ⚠ 這裡用的是 Unicode 的 **Default_Ignorable_Code_Point** 屬性——「這些碼位存在但不該
#   被畫出來」，正是這個問題的官方定義。上一版自己列了五個字元，對抗性測試當場又找出
#   七個穿得過去的（2026-07-26）：**黑名單一定漏**，因為它列的是實例不是規則。
#   stdlib 的 unicodedata 沒有暴露這個屬性，只能把區間寫出來（Unicode 15.1）。
_DEFAULT_IGNORABLE = (
    (0x00AD, 0x00AD),
    (0x034F, 0x034F),
    (0x061C, 0x061C),
    (0x115F, 0x1160),
    (0x17B4, 0x17B5),
    (0x180B, 0x180F),
    (0x200B, 0x200F),
    (0x202A, 0x202E),
    (0x2060, 0x206F),
    (0x3164, 0x3164),
    (0xFE00, 0xFE0F),
    (0xFEFF, 0xFEFF),
    (0xFFA0, 0xFFA0),
    (0xFFF0, 0xFFF8),
    (0x1BCA0, 0x1BCA3),
    (0x1D173, 0x1D17A),
    (0xE0000, 0xE0FFF),
)
# 不屬於 Default_Ignorable、卻整格畫成空白的漏網之魚
_BLANK_GLYPHS = {"⠀"}  # U+2800 BRAILLE PATTERN BLANK（So）


def _is_invisible(ch: str) -> bool:
    cp = ord(ch)
    return ch in _BLANK_GLYPHS or any(lo <= cp <= hi for lo, hi in _DEFAULT_IGNORABLE)


def _fold(name: str) -> str:
    """唯一性比對用的鍵：NFKC 正規化 + casefold。

    ⚠ **一定要在 Python 這一端算。** 原本寫成 `func.lower(User.username) == name.lower()`，
      左邊交給資料庫執行，而 **SQLite 內建的 lower() 只處理 ASCII**（3.47.1 實測：
      `lower('Ärger')` 回 `'Ärger'`）。右邊 Python 給的是 `'ärger'`，比不中就放行。
      更糟的是它**有方向性**：先建 `über` 再建 `Über` 擋得下來，先建 `Über` 再建 `über`
      就穿過去——只測一個方向會以為修好了（探索性測試 2026-07-26 打出來的）。

    NFKC 順帶收斂兩件事：`café`（U+00E9）與 `café`（e + U+0301）視覺相同卻是不同字串；
    全形 `ａｄｍｉｎ` 與 `admin` 也是。casefold() 比 lower() 更適合比對（德文 ß → ss）。
    """
    return unicodedata.normalize("NFKC", name).casefold()


def _display_width(name: str) -> int:
    """概略的顯示欄寬——東亞寬字元佔兩欄。

    上限要管的是**版面**，而版面吃的是欄寬不是碼位數：32 個中文字是 32 個碼位、卻會把
    帳號清單撐成 64 欄。
    """
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in name)


def _clean_username(raw) -> str:
    """把外部給的使用者名稱驗過並正規化，失敗一律拋 AuthError（→ 400，不是 500）。

    ⚠ 型別要先驗。`raw.strip()` 遇到 int/bool/list/dict 會拋 AttributeError，那是
      未捕捉的例外——回應變成 500 的 HTML 錯誤頁，日誌裡還留一整段 traceback。
      而這條路 **未登入就打得到**（`create_user` 由 admin 走，但同樣的型別問題在
      `authenticate` 也存在，見那支自己的防護），等於誰都能刷日誌。
      ⚠ **login 並不走這支。** 這裡原本寫著「login 也走同一個正規化」，但
        `_clean_username` 只有 create_user 呼叫；`authenticate` 自己做型別檢查後對
        `username.strip()` 精確比對（那是刻意的，見 create_user 裡的說明）。照原本
        那句話讀，會以為登入也繼承了隱形字元的拒絕——它沒有（審查 F-014）。

    禁空白與不可列印字元：帳號名稱會出現在清單、下拉、稽核紀錄裡，含換行或 Tab 的
    名字在那些地方與含空白的名字**看起來一模一樣**，而這個系統沒有刪除帳號的功能
    （ADR 0010），認錯人就只能一直錯下去。

    ⚠ **同形異字擋不住，這是刻意接受的。** 西里爾的 `аdmin`（U+0430）與拉丁的 `admin`
      在畫面上無法分辨，而 _fold() 也不會把它們收斂到一起。要擋就得限定單一文字系統，
      那會連中文帳號一起擋掉——代價比威脅大：帳號**只有管理員建得出來**，所以這條路
      需要一個管理員去騙另一個管理員，而不是外人打得進來的。

    ⚠ 組合記號（Mn）也不全擋。U+05C7（希伯來母音點）之類的字元接在拉丁字母後面幾乎
      看不見，但它們是希伯來文／泰文／天城體帳號的正常構件——全擋等於擋掉那些語言。
      Default_Ignorable 涵蓋的是「本來就不該畫出來」的那一群，那條線才畫得乾淨。
    """
    if not isinstance(raw, str):
        raise AuthError("使用者名稱必須是字串")
    name = raw.strip()
    if not name:
        raise AuthError("使用者名稱不可為空")
    width = _display_width(name)
    if width > config.USERNAME_MAX:
        raise AuthError(f"使用者名稱最長 {config.USERNAME_MAX} 欄寬（給了 {width}；中文與全形字元各算兩欄）")
    if any(ch.isspace() or not ch.isprintable() for ch in name):
        raise AuthError("使用者名稱不可含空白、換行或其他不可列印字元")
    # ⚠ 正規化**前後都要看**。U+FFA0 的 NFKC 就是 U+1160，而 U+1160 本來就在名單裡
    #   ——上一版只檢查原字串，於是名單漏掉了自己映射過去的那個字元。
    folded = unicodedata.normalize("NFKC", name)
    # 訊息只列**使用者真的打進來**的那些碼位。兩邊聯集的話，打一個 U+3164 會被回報成
    # 「U+1160、U+3164」（因為前者是後者的 NFKC）——正確但看了會愣住，而錯誤訊息的
    # 職責是讓人知道要改掉哪一個字。原字串裡找不到才退回用正規化後的（那表示問題是
    # 正規化之後才浮現的，那時列出對照反而是需要的資訊）。
    bad = {ch for ch in name if _is_invisible(ch)} or {ch for ch in folded if _is_invisible(ch)}
    if bad:
        raise AuthError(
            "使用者名稱含有看不見的字元（"
            + "、".join(f"U+{ord(ch):04X}" for ch in sorted(bad, key=ord))
            + "），在清單上會與沒有它的名字完全一樣"
        )
    return name


def create_user(username: str, password: str, is_admin: bool = False) -> dict:
    username = _clean_username(username)
    pw_hash = hash_password(password)
    # ⚠ 這是一筆「讀全表 → 寫一列」的交易，必須整段互斥。
    #
    #   SQLite 的預設交易是 deferred：先讀只拿 read lock，要寫才升級——而在 WAL 底下，
    #   若中間有別人寫過，升級會**當場**回 SQLITE_BUSY，busy_timeout 對這種情況無效
    #   （它等的是鎖，不是快照衝突）。實測 4 併發 × 20 輪有 12.5% 回 500
    #   `database is locked`（對抗性測試 2026-07-26）。immediate=True 讓交易一開始就取
    #   寫鎖，既不再撞這個，也順帶讓「檢查重名 → 插入」真正原子。
    #
    with session_scope(immediate=True) as s:
        # ⚠ 唯一性用 _fold() 的鍵比，**不分大小寫、也不分正規化形式**。UNIQUE 索引比的
        #   是原字串，於是 `casey`/`Casey`、`café`(NFC)/`café`(NFD)、`ａdmin`/`admin`
        #   都可以並存且各自登得進去——清單上兩列一模一樣，稽核時分不出是誰做的，而帳號
        #   不能刪（ADR 0010），建錯了就永遠留著。
        #
        #   比對鍵在 **Python 端**算，不能下推給資料庫：SQLite 的 lower() 只處理 ASCII，
        #   詳見 _fold 的說明。代價是這裡要把使用者全撈出來比一遍——建帳號是管理員偶爾
        #   才做一次的事，這個規模的全表掃描便宜得多，換來的是不必為了一個索引去改
        #   schema（而 ALTER TABLE ADD COLUMN 帶不出 UNIQUE，那是另一個坑）。
        #
        #   登入端刻意**維持精確比對**：既有資料庫裡可能已經有這種成對的帳號（本機就有
        #   一組），把登入改成正規化比對會讓那一對變成不知道該驗哪一個的密碼。擋住新的、
        #   不動既有的，是唯一不會弄壞現況的做法。
        key = _fold(username)
        clash = next((u for u in s.query(User).all() if _fold(u.username) == key), None)
        if clash is not None:
            raise AuthError(f"使用者 {clash.username} 已存在（名稱比對不分大小寫與正規化形式）")
        user = User(username=username, password_hash=pw_hash, is_admin=is_admin)
        s.add(user)
        s.flush()
        return _to_dict(user)


def authenticate(username: str, password: str) -> dict:
    """驗證帳密，成功回傳 user dict；任何失敗一律拋同一則訊息（不透露是帳號還是密碼錯）。"""
    # ⚠ 不能直接 `(username or "").strip()`：非字串（int / list / dict）會在這裡拋
    #   AttributeError，而 login 是**未登入就打得到**的端點——任何人都能讓它回 500 並在
    #   日誌裡留下一整段 traceback。型別不對就是帳密錯誤，走同一則訊息、同一個時間路徑。
    if not isinstance(username, str):
        username = ""
    # ⚠ **密碼也要**，而且理由一模一樣。`password or ""` 只擋得掉 falsy 的非字串
    #   （0、[]、{}）；truthy 的非字串（`1`、`True`、`[1]`、`{"a":1}`、`3.14`）會原樣進到
    #   argon2，它在 `password.encode()` 那一步拋 AttributeError——未捕捉，回 500。
    #   2026-07-26 對線上實測 `{"username":"nobody","password":1}` → 500（交叉審查指出）。
    #   上一輪修 username 時只改了一行，隔壁那一行是同一個洞。
    if not isinstance(password, str):
        password = ""
    # ⚠ **這一筆刻意維持 deferred。** 穩態下它是純讀：唯一的寫是下面那個 rehash，而那只在
    #   argon2 參數升級之後才會發生一次。而交易體內有一次 argon2id verify（幾十到上百 ms）
    #   ——用 immediate 的話，每一發登入都抱著全域寫鎖跑那段雜湊，登入彼此序列化，還擋住
    #   全站的 touch / 開終端 / 建立。而 login 是**未登入就打得到**的端點：灌一串假帳密就
    #   能近乎獨佔寫鎖。判準是「這筆交易會不會寫」，這一筆平常不寫。
    with session_scope() as s:
        user = s.query(User).filter_by(username=username.strip()).one_or_none()
        stored = user.password_hash if user else _DUMMY_HASH
        try:
            _ph.verify(stored, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            # 帳號不存在、密碼錯、或 hash 不可用（如 system 帳號的 "!"）都走這裡
            raise AuthError("帳號或密碼錯誤") from None
        if user is None:  # 假驗證竟通過也不可能放行
            raise AuthError("帳號或密碼錯誤")
        needs_rehash = _ph.check_needs_rehash(user.password_hash)
        old_hash = user.password_hash
        info = _to_dict(user)
        uid = user.id
    # 參數升級時自動換新雜湊。**另開一筆 immediate 小交易**：寫的部分很短，而上面那段慢。
    # ⚠ 讀與寫分開之後中間有窗口，所以要守「hash 沒被別人換過才寫」——不然這一筆會蓋掉
    #   期間發生的改密碼（他改完密碼、這裡拿舊 hash 重算一份寫回去，等於把密碼改回去）。
    # ⚠ 失敗不可以害登入失敗：rehash 是保養，不是認證的一部分。下一次登入還會再試。
    # ⚠ **雜湊要在交易外算。** argon2id 一次上百 ms，那正是這支函式一開始要避開的東西——
    #   放進交易裡等於把剛趕出去的慢動作從前門請回來（第一版就是這樣寫的）。
    if needs_rehash:
        new_hash = _ph.hash(password)
        with suppress(Exception), session_scope(immediate=True) as s:
            row = s.get(User, uid)
            if row is not None and row.password_hash == old_hash:
                row.password_hash = new_hash
    return info


def change_password(user_id: int, new_password: str, old_password: str | None = None, require_old: bool = True) -> dict:
    """改密碼。require_old=True（使用者自行修改）時必須驗舊密碼；admin 代改可略過。

    回傳改完之後的 user（含遞增過的 password_version），另外帶三個欄位講收終端的結果：
    `views_closed`、`views_failed`（`-1` 代表整個動作拋出來、連收幾場都不知道）、
    以及失敗時的 `views_error`。**呼叫端不可以只看有沒有拋例外就回報成功**，密碼改掉了
    而終端沒收乾淨是一種部分成功，要講出來。

    ⚠ **不為「操作中的這一台」留特例**（ADR 0010）：password_version 一遞增，這個帳號的
      每一張 cookie 都當場失效，包含按下送出的那一張——`app.change_own_password` 自己
      `session.clear()` 收尾，`admin_change_password` 則本來就不是他在操作。留一個例外只
      換到少按幾個鍵，卻讓「全部失效」變成說一套做一套。

    ⚠ **而且 cookie 不是全部：版號管不到一條已經升級完成的 WebSocket**（授權只發生在連線
      交出去之前，之後不會再有人回頭問它還算不算數）。所以這裡直接收掉他所有開著的終端。
      **這一步刻意寫在這支函式裡，不留給呼叫端**：它原本是每個呼叫端要自己記得的事
      （`app._cut_live_terminals`），而 `cli.py` 的 `set-password` 就沒有記得——管理員從
      CLI 讓一個被盜帳號退場，對方的分頁仍然是一個能打字的 shell，而畫面回報成功
      （審查 F-003）。不變式跟著操作走，第四個呼叫端才不會再漏一次。
    ⚠ 收終端要在**交易關掉之後**：`close_user_views` 會自己開交易，在 `session_scope`
      裡面呼叫就是 SQLite 上的巢狀交易——這個 codebase 已經為那件事付過一次
      `database is locked` 的代價。
    """
    from . import views  # 區域 import：views 不 import auth，但擺模組層會綁死載入順序

    new_hash = hash_password(new_password)
    # ⚠ 這一筆的交易體裡有一次 argon2 verify（驗舊密碼）與一次 hash，兩者都是上百 ms 而且
    #   都在寫鎖持有期內——**這是明知而接受的**，不是漏看：改密碼是人手動觸發的低頻動作，
    #   而這支函式的不變式（password_version 遞增、當場收掉所有終端、system 帳號的擋門）
    #   綁得很緊，為了那 100 ms 去拆讀寫兩段，換來的風險比省下的鎖時間大。
    #   `authenticate` 的處置不同，因為它是**未登入就打得到**而且高頻——見那支的註解。
    with session_scope(immediate=True) as s:
        user = s.get(User, user_id)
        if user is None:
            raise AuthError("使用者不存在")
        # ⚠ system 的 password_hash 是不可用值 `!`（argon2 永遠驗不過），那是它「無法被
        #   登入」的**唯一**保障——給它設一個真密碼就等於把一個 is_admin=True 的帳號變成
        #   可登入的管理員。它出現在 /api/users 清單上，admin 點得到那顆「重設密碼」，
        #   所以這道防線要擋在這裡，不能只靠畫面不提供。
        if user.username == config.SYSTEM_USERNAME:
            raise AuthError("不可設定 system 帳號的密碼（它必須維持無法登入）")
        if require_old:
            # 非字串的舊密碼同樣會在 argon2 的 .encode() 拋 AttributeError（見 authenticate）
            if not isinstance(old_password, str):
                old_password = ""
            try:
                _ph.verify(user.password_hash, old_password)
            except (VerifyMismatchError, VerificationError, InvalidHashError):
                raise AuthError("原密碼錯誤") from None
        user.password_hash = new_hash
        user.password_version += 1  # 使既有簽章 cookie 全部失效（review H4）
        result = _to_dict(user)
    # ⚠ 交易關掉之後才做——見 docstring：close_user_views 自己會開交易。
    #   而且**一定要做**：cookie 全滅擋不到一條已經升級完成的 WebSocket。
    # ⚠ **不再把例外吞掉。** 舊版是 `with suppress(Exception)`，理由寫的是「收不掉終端
    #   不可以讓改密碼本身失敗」——那半句仍然對（密碼已經改了，`password_version` 也遞增
    #   了，回滾不了），但吞掉的後果是呼叫端拿到一個乾淨的成功，而對方的分頁其實還是
    #   一個能打字的 shell。改成**照樣回成功、但把收終端的結果一起交出去**，讓呼叫端
    #   有辦法講出「密碼改了，終端沒收乾淨」這句話。
    try:
        closed, failed = views.close_user_views(user_id)
    except Exception as e:  # noqa: BLE001 — 這一步失敗不能讓已經改掉的密碼變成錯誤回應
        result["views_closed"] = 0
        result["views_failed"] = -1  # -1＝連查都查不動，比「收了 N 場失敗 M 場」更糟
        result["views_error"] = str(e)
    else:
        result["views_closed"] = closed
        result["views_failed"] = failed
    return result


def get_user(user_id: int) -> dict | None:
    with session_scope() as s:
        user = s.get(User, user_id)
        return _to_dict(user) if user else None


def set_ttyd_bin(user_id: int, value: str) -> dict:
    """設定「這個人開終端要用哪一顆 ttyd」。回傳更新後的使用者。

    ⚠ 值一律先過白名單（`config.TTYD_BINS`）再落地：它最終會變成 argv[0]，把任意字串
      存進去等於把 exec 的第一個參數交給呼叫端。端點那邊也擋一次——這裡是最後一道。
    """
    # 型別先驗，理由同 app.set_prefs：`x in dict` 對不可 hash 的值會拋 TypeError。
    if not isinstance(value, str) or value not in config.TTYD_BINS:
        raise ValueError(f"不認得的 ttyd 種類：{value!r}")
    with session_scope(immediate=True) as s:
        user = s.get(User, user_id)
        if user is None:
            raise ValueError("使用者不存在")
        user.ttyd_bin = value
        return _to_dict(user)


def list_users() -> list[dict]:
    """**全部**帳號。

    ⚠ 退場過的（被 admin 改掉密碼的）不可以濾掉——本來也沒有可以濾的旗標。帳號不能
      刪（ADR 0010），名單上看不到他就沒有人能把新密碼給他讓他回來；他過去的
      session 歷史也是永久保存的，少了他那些紀錄就對不到人。
    """
    with session_scope() as s:
        return [_to_dict(u) for u in s.query(User).order_by(User.username).all()]


def page_users(limit: int, offset: int = 0) -> tuple[list[dict], int]:
    """一頁帳號 + 總筆數（排序、不濾人的規則同 list_users）。

    總數在同一個交易裡算，不是拿 len() 去數這一頁——那會回報成「共 10 筆」。
    """
    with session_scope() as s:
        total = s.query(User).count()
        rows = s.query(User).order_by(User.username).limit(limit).offset(offset).all()
        return [_to_dict(u) for u in rows], total


def set_cli_token(user_id: int, token) -> None:
    """存這個人的 CLI 授權 token（`claude setup-token` 的輸出），加密後入庫。

    驗證只做「這像不像一個 token」的最低限度：單行、可見 ASCII、長度合理。**不驗前綴
    也不打外部服務**——格式是上游的事，隨版本會變；真偽只有開場時才知道（token 失效
    的症狀就是開場失敗，見 claude_credentials_state 的 detail）。
    """
    if not isinstance(token, str):
        raise AuthError("token 必須是字串")
    token = token.strip()
    if not token:
        raise AuthError("token 是空的——請貼上 `claude setup-token` 的完整輸出")
    if len(token) > 4096:
        raise AuthError("token 長得不像話（超過 4096 字元），請確認貼的是 token 本身")
    if not all(33 <= ord(ch) <= 126 for ch in token):
        # 換行／空白／控制字元都擋。⚠ 理由**不是**「它會進環境變數」——預設那條早就改成
        # 檔案描述符了（見 config.TOKEN_DELIVERIES）。是這兩件事：值會被寫進送進容器的
        # tar，而 env 那條逃生口也還在；何況多行的「token」幾乎一定是整段終端輸出連說明
        # 文字一起貼進來了。兩條路都不該收，所以這道檢查與交付方式無關，一律擋。
        raise AuthError("token 只能是單行可見字元——看起來貼進來的不只 token 本身")
    with session_scope(immediate=True) as s:
        user = s.get(User, user_id)
        if user is None:
            raise AuthError("使用者不存在")
        user.cli_token_enc = crypto.encrypt(token, purpose=crypto.Purpose.CLI_TOKEN)


def clear_cli_token(user_id: int) -> None:
    with session_scope(immediate=True) as s:
        user = s.get(User, user_id)
        if user is None:
            raise AuthError("使用者不存在")
        user.cli_token_enc = None


def cli_token(user_id: int) -> str | None:
    """解密回明文 token；沒設過或解不開都回 None（crypto.decrypt 的既定語意——
    SECRET_KEY 換過之後舊密文就是「沒設」，畫面會引導重貼，不會炸）。"""
    with session_scope() as s:
        user = s.get(User, user_id)
        if user is None or user.cli_token_enc is None:
            return None
        return crypto.decrypt(user.cli_token_enc, purpose=crypto.Purpose.CLI_TOKEN)


# --- GitLab PAT（ADR 0016）---------------------------------------------------------


def set_gitlab_pat(user_id: int, value) -> None:
    """設定／清除這個人的 GitLab PAT。**空字串（或只有空白）＝清除。**

    清除只有這一種方式，不另外做 DELETE 端點——畫面上「把輸入框清空後儲存」就是使用者
    心裡的清除動作，兩個入口只會讓人猜哪一個才算數。

    ⚠ 值一律 strip 再落地：PAT 從剪貼簿貼進來很容易帶到換行或尾隨空白，而它最後會被塞進
      HTTP 標頭——帶著 `\\n` 的字串放進標頭是會出事的（標頭注入），而症狀是「一模一樣的
      token 用 curl 可以、在這裡不行」。
    ⚠ **字元集在這個入口就要擋**（`gitlab_proxy.validate_pat`），不能只在產生 nginx 設定時
      擋。只擋產生端的話：畸形的值會被加密存起來、設定頁顯示「已設定」，然後每一顆代理
      都靜靜地建不起來——使用者看到「設定頁說有、session 說沒有」，而**沒有任何地方會告訴
      他那個值不合法**。擋在入口才有人看得到 400。
    ⚠ **絕不記錄這個值**：不 print、不寫 log、不放進例外訊息。
    """
    if not isinstance(value, str):
        raise AuthError("PAT 必須是字串")
    value = value.strip()
    if value:
        # 空字串是「清除」，不是一個要驗的 PAT——先判空再驗，順序不能反。
        try:
            value = gitlab_proxy.validate_pat(value)
        except gitlab_proxy.PatRejected as e:
            raise AuthError(str(e)) from e
    with session_scope(immediate=True) as s:
        user = s.get(User, user_id)
        if user is None:
            raise AuthError("使用者不存在")
        user.gitlab_pat_enc = crypto.encrypt(value, purpose=crypto.Purpose.GITLAB_PAT) if value else None


def gitlab_pat(user_id: int) -> str | None:
    """這個人的 PAT 明文；沒設過、或解不開（換過 SECRET_KEY）都回 `None`。

    ⚠ **要分辨「沒設」與「解不開」請用 `gitlab_pat_state()`。** 這支刻意不分辨——它回答的
      是「拿不拿得到值來用」，兩種情形都是拿不到。但 reconciler 的收斂**必須**分辨，
      那是另一個問題。
    ⚠ 取到之後只能往 `put_archive` 去，不可以落進任何 log、環境變數、或 `docker inspect`
      看得到的地方。
    """
    with session_scope() as s:
        user = s.get(User, user_id)
        if user is None or user.gitlab_pat_enc is None:
            return None
        return crypto.decrypt(user.gitlab_pat_enc, purpose=crypto.Purpose.GITLAB_PAT)


def gitlab_pat_state(user_id: int) -> str:
    """`"ok"` / `"none"` / `"unreadable"`——**這三態的差別決定 reconciler 刪不刪代理**。

    | 回傳 | DB 的樣子 | reconciler 該做什麼 |
    |---|---|---|
    | `"ok"` | 有值且解得開 | 確保代理在、設定是最新的 |
    | `"none"` | `NULL`（使用者**明確清除**，或從沒設過） | **移除代理**——「我覺得外洩了」要立刻生效 |
    | `"unreadable"` | 有值但解不開（**換過 `SECRET_KEY`**） | **什麼都不做** |

    ⚠ **為什麼一定要分辨。** 「讀不到就不刪任何還能用的東西」這條規則本身是對的：換一次
      `SECRET_KEY` 會讓**所有人**的 PAT 一起解不開，拿它當期望狀態就是把所有還在服務中的
      代理一起收掉。但同一條規則會讓「清除 PAT」不再立即生效——而那是安全需求。
      **兩者的衝突只能靠分辨解決，不能選一邊。**

    ⚠ **第三種情況**：欄位有值但被手動改壞。它會落在 `"unreadable"`，於是代理帶著舊 PAT
      服務到 session 結束。方向是保守的（不會誤刪還能用的東西），**這是想過之後接受的，
      不是漏判**——使用者在設定頁重新輸入一次就回到 `"ok"`。

    ⚠ 使用者不存在時回 `"none"`：對呼叫端而言「這個人沒有可用的 PAT」是同一件事。
    """
    with session_scope() as s:
        user = s.get(User, user_id)
        if user is None or user.gitlab_pat_enc is None:
            return "none"
        return "ok" if crypto.is_readable(user.gitlab_pat_enc, purpose=crypto.Purpose.GITLAB_PAT) else "unreadable"


def gitlab_proxy_error(user_id: int) -> str | None:
    """這個人的代理**連續**起不來時，nginx 自己說的最後一句話；沒問題就回 `None`。

    由 reconciler 在跨過 `config.PROXY_FAIL_THRESHOLD` 輪之後寫入、代理恢復的那一輪清掉
    （見 `reconciler._note_proxy_down` / `_note_proxy_ok`）。

    ⚠ **這是診斷麵包屑，不是權威狀態**：沒有任何判斷會讀它，它只負責把「本來只在容器
      log 裡的一句話」端到人看得到的地方。之所以需要，是因為代理起不來時使用者看到的
      症狀是「GitLab 連不到」，而那個症狀會把他導向完全錯的排查方向（去查 token、
      查網路、查 GitLab 是不是掛了）。
    """
    with session_scope() as s:
        user = s.get(User, user_id)
        return user.gitlab_proxy_error if user else None


# 這裡**刻意沒有 delete_user()，也沒有「停用」**。
#
# 不刪除：刪除會沿 FK cascade 掉 `sessions` 登錄（容器變孤兒），也讓稽核鏈斷在半路。
# `session_history` 仍保留 user_id ON DELETE SET NULL 與 username 快照——那是給
# 「有人直接動資料庫」的兜底，不是應用層還會走的路徑。
#
# 讓一個人退場的做法是**管理員改掉他的密碼**（app.admin_change_password）：
#   1. password_version 遞增 → 他既有的 cookie 全部當場失效；
#   2. 接著切斷他所有開著的終端（cookie 管不到已升級的 WebSocket，見 views.close_user_views）；
#   3. 他不知道新密碼，登不回來。
#
# ⚠ **這不等於「停用帳號」，別把它講成那樣。** 這一段以前寫的是「三件合起來與停用的效果
#   相同」，而那句話不成立，缺口是具體的：
#     · **他的容器繼續跑。** 這是設計（切存取權不終止工作，ADR 0003／0010），但它意味著
#       他名下的 session 還活著，而不是「什麼都停了」。
#     · **他存的 GitLab 憑證還在，per-user proxy 也還在**（ADR 0016）。改密碼刻意不動它，
#       因為改密碼是例行操作；代價是那條對外的路不會因為改密碼而關上。
#     · 收終端**可能失敗**。以前失敗會被吞掉、畫面照樣回成功；現在會回報（見 change_password），
#       但「回報得出來」不等於「一定收得掉」。
#     · **收終端是一次快照，不是一道閘。** `close_user_views` 先把要收的 session 列出來、
#       再一個一個收；在那之間，一個「已經通過 before_request、只是還沒跑到 open_view」的
#       併發請求仍然開得出新的 ttyd，而它不在那份快照裡。窗口很窄（同一個請求週期內），
#       但它確實存在，而且**修法不在這裡**：要關掉它得有一個「帳號狀態」讓 open_view 在
#       真正建立之前再問一次，也就是下面那段說的那條路。沒有那個狀態，這裡再怎麼補都是
#       把窗口變窄而不是關上。
#   要真正的撤銷語意（不能再登入、憑證作廢、工作終止、失敗可重試），需要的是一個獨立的
#   帳號狀態與一條對帳迴圈，這個工具目前沒有做，也不假裝有。它是單機／小團隊的東西，
#   威脅模型是「把某個人請出去」，不是「即時圍堵一個正在攻擊的內部人」。
#
# 要讓他回來，就把新密碼告訴他，這也同時取代了「復用」。


def _to_dict(user: User) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "is_admin": user.is_admin,
        "password_version": user.password_version,
        # 這個人開終端時要用哪一顆 ttyd（見 config.TTYD_BINS）。一律經收斂函式，
        # 讓沒設過（NULL）與白名單改掉之後留下的舊值都退回預設。
        "ttyd_bin": config.ttyd_bin_or_default(user.ttyd_bin),
        # 有沒有設過 GitLab PAT（ADR 0016）。**只給狀態，永遠不給值**——明文不用說，
        # 連密文都不出去。這裡是唯一出口，所以這條規矩只要守住這一行。
        # ⚠ 用 `is_readable` 而不是 `bool(user.gitlab_pat_enc)`：換過 SECRET_KEY 之後欄位
        #   仍然有值但已經解不開，那時的事實是「不能用」。顯示成「已設定」會讓人以為好好
        #   的，然後去 GitLab 查一把完全正常的 token。
        "gitlab_pat_configured": crypto.is_readable(user.gitlab_pat_enc, purpose=crypto.Purpose.GITLAB_PAT),
        "created_at": user.created_at.isoformat(),
    }
