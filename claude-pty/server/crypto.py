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
