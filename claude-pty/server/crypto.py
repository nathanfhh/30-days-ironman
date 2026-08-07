"""靜態加密（HKDF 導出金鑰 + Fernet）。

金鑰從既有的 `SECRET_KEY` 以 HKDF 導出，**不另外保管第二個秘密**——多一把金鑰就多一個
「換了 A 忘了 B」的失效模式，而這個系統已經有一把必須跨重啟不變的金鑰了。

⚠ 這讓 `SECRET_KEY` 的語意再多一項。它原本是「換掉＝所有登入 cookie 立刻失效」，
  現在還要加上「**所有已加密的值一起解不開**」。`.env.example` 記了這件事。

⚠ **`decrypt()` 解不開一律回 `None`，絕不往上拋。** 換一次 `SECRET_KEY` 就是每一筆都解不開，
  拋出去等於整站 500——教訓同憑證徽章那條（讀不到的憑證要降級成
  「當成沒設」，不是讓畫面炸掉）。呼叫端據此把它當「這個人沒設過 PAT」處理。

⚠ **這個降級與 reconciler 的收斂規則會交互，而那個交互曾經是個陷阱。**
  換過金鑰之後這裡對**所有人**都回 `None`；若 reconciler 把「讀不到」直接當成「沒設」，
  就會把所有還在服務中的代理一起收掉。
  解法**不是**讓 reconciler 一律不刪（那樣「清除 PAT」就不會立即生效，而那是安全需求），
  而是**分辨兩者**（沒設過 vs 解不開），呼叫端要各自處理。
  ——所以這裡維持「解不開就回 None」是對的，**但呼叫端要知道自己在問哪一個問題**。
"""
import base64

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from . import config

# 導出時的 domain separation。日後若有第二種用途（例如別的 CLI 的憑證），**換一個 info
# 字串**而不是共用這一個：同一把 SECRET_KEY 底下不同用途要導出不同的金鑰，密文才不會
# 跨用途互相解得開。字串裡的 v1 是給「換演算法時要能並存」留的餘地。
_INFO = b"gitlab-pat-v1"


def _fernet() -> Fernet:
    """每次呼叫都重新導出。

    HKDF-SHA256 是微秒級的，而快取會讓「換掉 `SECRET_KEY` 之後的行為」變成必須重啟行程
    才驗得到——那正是這裡最需要測的一條。便宜的正確性換不便宜的可測性，划算。
    """
    key = HKDF(algorithm=hashes.SHA256(), length=32,
               salt=None, info=_INFO).derive(config.SECRET_KEY.encode())
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt(raw: str) -> str:
    """明文 → 密文字串（可直接存進 Text 欄位）。"""
    return _fernet().encrypt(raw.encode()).decode()


def decrypt(token: str | None) -> str | None:
    """密文字串 → 明文；**任何解不開的情形都回 `None`**。

    涵蓋：沒設過（`None`）、換過 `SECRET_KEY`（`InvalidToken`）、欄位被手動改壞
    （`InvalidToken` / base64 解碼失敗）、型別不對（不是 str）。
    這個函式**刻意不分辨**是哪一種——它回答的是「拿不拿得到值」，兩者都是拿不到，
    而對**使用者**的正確指示也都是同一句「請重新輸入」。

    ⚠ 但 **reconciler 的收斂必須分辨**：「明確清除」要立刻收掉代理（安全需求），
      「換過金鑰」則什麼都不能動（否則會把所有人還在服務中的代理一起收掉）。
      那條路要分辨三態（有值且可解 / 沒設 / 解不開），**不是**這個函式的事。
    """
    if not isinstance(token, str) or not token:
        return None
    try:
        return _fernet().decrypt(token.encode()).decode()
    except (InvalidToken, ValueError, TypeError):
        # ValueError 涵蓋 base64 解碼失敗（欄位被手動改成非 base64 的字串）。
        return None


def is_readable(token: str | None) -> bool:
    """這串密文現在解得開嗎——**不把明文交出去**。

    給「只想知道有沒有設過」的呼叫端用（例如 `auth._to_dict`，帳號清單一頁會問 25 次）。
    直接用 `decrypt(...) is not None` 也會得到同一個答案，但那會把 25 份憑證明文交到
    一個根本不需要它的地方——**能不遞出去的東西就不要遞出去**。

    ⚠ 不可以簡化成 `bool(token)`：換過 `SECRET_KEY` 之後欄位仍然有值但已經解不開，
      那時的事實是「不能用」。答成「已設定」會讓人去查一把完全正常的 token。
    """
    return decrypt(token) is not None
