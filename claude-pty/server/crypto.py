"""靜態加密（HKDF 導出金鑰 + Fernet）：使用者貼進來的各種憑證。

目前有兩種用途，**各自一把金鑰**（見下方 `Purpose`）：CLI 授權 token，以及 GitLab PAT。

金鑰從既有的 `SECRET_KEY` 以 HKDF 導出，**不另外保管第二個秘密**——多一把金鑰就多一個
「換了 A 忘了 B」的失效模式，而這個系統已經有一把必須跨重啟不變的金鑰了。

⚠ 這讓 `SECRET_KEY` 的語意再多一項。它原本是「換掉＝所有登入 cookie 立刻失效」，
  現在還要加上「**所有已加密的值一起解不開**」。`.env.example` 記了這件事。

⚠ **`decrypt()` 解不開一律回 `None`，絕不往上拋。** 換一次 `SECRET_KEY` 就是每一筆都解不開，
  拋出去等於整站 500——讀不到的憑證要降級成「當成沒設」（畫面引導重貼），
  不是讓畫面炸掉。

⚠ 但**做清理動作的呼叫端要小心這個降級**：「明確清除」與「解不開」在這裡同樣回
  `None`，若有誰把「讀不到」當成「使用者清掉了」而去收東西，換一次金鑰就會對所有人
  收一輪。那種呼叫端必須自己分辨三態（有值且可解／沒設／解不開），不是這個函式的事
  ——這裡維持「解不開就回 None」是對的，**但呼叫端要知道自己在問哪一個問題**。
"""

import base64
import enum
import hashlib
import hmac

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from . import config


class Purpose(enum.Enum):
    """導出金鑰時的 domain separation：**每一種用途一個字串，不共用**。

    同一把 `SECRET_KEY` 底下，不同用途要導出不同的金鑰，否則某一種用途的密文可以拿去解
    另一種用途的——那等於兩者共用一把鑰匙，而它們的生命週期與外洩後果並不相同。
    CLI 授權 token 是這套系統自己的登入憑證；GitLab PAT 是使用者在**別的系統**上的身分，
    撤銷方式、爆炸半徑、輪替頻率都不一樣。

    字串裡的 v1 是給「換演算法時要能並存」留的餘地。

    ⚠ 換掉任一個字串＝**那一種用途的既有密文全部解不開**（等同換 `SECRET_KEY` 的效果，
      只是範圍限於這一種用途）。不是可以順手改的東西。
    ⚠ 新增用途就在這裡加一個成員，**絕不可以沿用既有的**。呼叫端一律明講自己是哪一種
      （下面三個函式的 `purpose` 是必填的具名參數），沒有預設值——有預設值就會有人不填，
      而不填的那一天就是兩種用途共用金鑰的那一天。
    """

    CLI_TOKEN = b"cli-token-v1"
    GITLAB_PAT = b"gitlab-pat-v1"


def _fernet(purpose: Purpose) -> Fernet:
    """每次呼叫都重新導出。

    HKDF-SHA256 是微秒級的，而快取會讓「換掉 `SECRET_KEY` 之後的行為」變成必須重啟行程
    才驗得到——那正是這裡最需要測的一條。便宜的正確性換不便宜的可測性，划算。
    """
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=purpose.value).derive(config.SECRET_KEY.encode())
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt(raw: str, *, purpose: Purpose) -> str:
    """明文 → 密文字串（可直接存進 Text 欄位）。`purpose` 決定用哪一把導出金鑰。"""
    return _fernet(purpose).encrypt(raw.encode()).decode()


def decrypt(token: str | None, *, purpose: Purpose) -> str | None:
    """密文字串 → 明文；**任何解不開的情形都回 `None`**。

    涵蓋：沒設過（`None`）、換過 `SECRET_KEY`（`InvalidToken`）、欄位被手動改壞
    （`InvalidToken` / base64 解碼失敗）、型別不對（不是 str）。
    這個函式**刻意不分辨**是哪一種——它回答的是「拿不拿得到值」，兩者都是拿不到，
    而對**使用者**的正確指示也都是同一句「請重新輸入」。

    ⚠ 但 **reconciler 的收斂必須分辨**：「明確清除」要立刻收掉代理（安全需求），
      「換過金鑰」則什麼都不能動（否則會把所有人還在服務中的代理一起收掉）。
      那條路要分辨三態（有值且可解 / 沒設 / 解不開），**不是**這個函式的事。

    ⚠ `purpose` 必須與當初加密時的那一個相同，否則導出的金鑰不同，結果會是 `None`
      ——與「解不開」無法區分。這是刻意的：跨用途解密本來就該失敗。
    """
    if not isinstance(token, str) or not token:
        return None
    try:
        return _fernet(purpose).decrypt(token.encode()).decode()
    except (InvalidToken, ValueError, TypeError):
        # ValueError 涵蓋 base64 解碼失敗（欄位被手動改成非 base64 的字串）。
        return None


def is_readable(token: str | None, *, purpose: Purpose) -> bool:
    """這串密文現在解得開嗎——**不把明文交出去**。

    給「只想知道有沒有設過」的呼叫端用（一頁清單可能問幾十次）。
    直接用 `decrypt(...) is not None` 也會得到同一個答案，但那會把 25 份憑證明文交到
    一個根本不需要它的地方——**能不遞出去的東西就不要遞出去**。

    ⚠ 不可以簡化成 `bool(token)`：換過 `SECRET_KEY` 之後欄位仍然有值但已經解不開，
      那時的事實是「不能用」。答成「已設定」會讓人去查一把完全正常的 token。
    """
    return decrypt(token, purpose=purpose) is not None


# --- mitmweb 的網頁密碼（ADR 0021）-------------------------------------------------


# 導出時的 domain separation 字串。與 `Purpose` 同一個用意（同一把 SECRET_KEY 底下，
# 不同用途要導出不同的值），但這裡**不是**加密而是導一個口令，所以不共用那個 enum：
# 混在一起會讓「這個成員是拿來解密的還是拿來當密碼的」變成要讀實作才知道的事。
# v1 同樣是給「換算法時要能並存」留的餘地。
_MITM_WEB_INFO = b"mitm-web-password-v1"

# base64url 的字母表：A-Z a-z 0-9 - _（加上 `=` 填充，但我們截斷後不會留到）。
_MITM_WEB_LEN = 24


def mitm_web_password(session_id: str) -> str:
    """這一場 mitmweb 的 `web_password`。**確定性導出，不落 DB、不進瀏覽器。**

    控制平面建容器時用 `NCR_MITM_WEB_PASSWORD` 把它送進去（run_kwargs），之後
    `/api/auth/mitm` 用同一個公式當場重算，交給 nginx 以 `Authorization: Bearer` 注入。
    兩端各算各的、算出同一串，所以**一個欄位都不用加**，也沒有「存了忘了輪替」的問題。

    ⚠ **不用 `NCR_SESSION_ID`（Claude Code 的 sessionId）**，雖然它看起來現成。兩個理由：
      1. 它是 entrypoint **在容器內自己產的**（讀 /proc 的 uuid），控制平面不知道它：
         要用就得反過來由控制平面餵進去，兩條路徑（網頁／人自己開）都得改。
      2. 它是**可枚舉的**：capture 落盤目錄名就是 sessionId，而 `ncr/` 根是 per-user
         共用掛載，同一個人開的任何一顆容器裡的 agent 都 `ls` 得出全部場次的 id。
         這個 UI 顯示的是**未脫敏的即時流量**：「token＝sessionId」等於一旦哪天有條路
         讓兄弟容器碰得到 8081，全部場次一次交出去。HMAC 導出沒有這個性質：知道一場的
         推不出別場的，洩漏半徑小得多。

    ⚠ **回的是 base64url 截斷，不是 hex 也不是任意位元組**，三件事都靠它：
      · mitmweb 把 `$` 開頭的 `web_password` 當成 argon2 hash 去驗（v12.2.3 的 `app.py`），
        而我們要它拿去做**明文**比對（Bearer 送的就是這一串）。base64url 的字母表裡
        沒有 `$`，所以這件事是字母表保證的，不是運氣。
      · 這一串會變成 HTTP header 的值與 shell 的 env，字母表裡沒有空白、引號、控制字元。
      · 24 字元 ≈ 144 bits，遠超過猜測攻擊需要的強度；而它同時要塞進 entrypoint 的
        `${token:0:24}`，長度對齊才不會兩邊各留一半。

    ⚠ 換掉 `SECRET_KEY` ＝ **所有還開著的 session 的這個密碼一起作廢**（容器裡的 mitmweb
      還記著舊的，控制平面已經在算新的）。那是刻意的：一次作廢全部正是這個設計買到的
      東西；代價是那些場次的 UI 要等 session 換一顆容器才會通。cookie 本來就會跟著
      SECRET_KEY 一起失效，所以這不是新增的失效模式，只是多一項。
    """
    mac = hmac.new(
        config.SECRET_KEY.encode(),
        _MITM_WEB_INFO + b":" + session_id.encode(),
        hashlib.sha256,
    ).digest()
    return base64.urlsafe_b64encode(mac).decode()[:_MITM_WEB_LEN]
