"""憑證進容器（從 sessions.py 拆出）。

只被 SessionManager.create 用：_guard_credentials 擋「沒設」，_put_cli_token 送進去。
"""

from __future__ import annotations

import io
import os
import tarfile

from . import config
from . import auth as auth_mod
from .errors import SessionError


def _put_cli_token(container, user_id: int, delivery: str) -> bool:
    """把這個人的 CLI 憑證寫進容器自己的 writable layer，回傳有沒有真的寫。

    `delivery == "env"` 時**什麼都不做**：那條路的值已經在 build_run_kwargs 放進環境了，
    這裡再送一份只會讓同一個秘密多躺一個地方。

    **不經環境變數**，理由見 `config.SESSION_TOKEN_FILE`。tar 裡直接帶 uid 與 0600：
    entrypoint 以 `config.SESSION_UID` 執行，root 寫的檔它讀不到。

    ⚠ **失敗一律降級，不中斷建立。** 拿不到憑證的終端會停在登入提示，那是使用者看得懂
      的失敗；為此讓整場開不起來不成比例。
    ⚠ 例外只印型別不印訊息——put_archive 的錯誤訊息可能回夾 payload。
    """
    if delivery != "fd":
        return False
    token = auth_mod.cli_token(user_id)
    if not token:
        return False
    data = token.encode()
    stem = os.path.basename(config.SESSION_TOKEN_DIR)  # cpty
    parent = os.path.dirname(config.SESSION_TOKEN_DIR)  # /run

    # ⚠ **目錄要一起送，而且要設成他的。** entrypoint 讀完就 `rm`，而 unlink 要的是父目錄
    #   的寫權限——檔案 0600 給對了人也沒用，`/run` 是 root 的。見 config 那段的實測。
    d = tarfile.TarInfo(stem)
    d.type, d.mode = tarfile.DIRTYPE, 0o700
    d.uid = d.gid = config.SESSION_UID

    f = tarfile.TarInfo(f"{stem}/{os.path.basename(config.SESSION_TOKEN_FILE)}")
    f.size, f.mode = len(data), 0o600
    f.uid = f.gid = config.SESSION_UID

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        tar.addfile(d)
        tar.addfile(f, io.BytesIO(data))
    try:
        container.put_archive(parent, buf.getvalue())
        return True
    except Exception as e:  # noqa: BLE001 — 見 docstring
        print(f"[claude-pty] ⚠ 憑證送不進容器（{type(e).__name__}）：終端會停在登入提示", flush=True)
        return False


_CLAUDE_BASE = {"cli": "claude", "brand": "anthropic"}


def claude_credentials_state(user_id: int | None) -> dict:
    """這個人開 session 時，Claude Code 拿不拿得到登入憑證。

    憑證＝他自己貼進來的 setup-token（`claude setup-token` 的輸出，加密存 DB、開場時
    交給那一場；**預設走檔案描述符、不進環境變數**，env 只是逃生口，見
    `config.TOKEN_DELIVERIES`）。**唯一來源**，控制平面不讀 host 上的任何憑證檔——
    「檔案在就順便用」是一條平常不走、出事才走、而且沒人測過的路徑。

    只有兩種狀態：已設定／未設定。token 的到期時刻**不可知**（它不揭露自己的壽命），
    所以沒有「剩 N 天」的預警——過期不會有任何預告，**症狀是開場失敗**（終端裡只會
    看到登入提示）。detail 把這件事講在前面，事到臨頭那句話就是操作指南。
    ⚠ 這裡曾經回一個永遠是空陣列的 `stamps`，形狀是留給「到期時刻」用的。setup-token
      不揭露壽命之後那個能力就沒了，而空欄位讓前端跑一個永遠不會執行的迴圈——那個
      「未來也許會用到」的形狀留了很久，一直是死的。要再加預警請先確認拿得到時刻。

    解不開（換過 SECRET_KEY）與沒設過**刻意同一種畫面**：對使用者的正確指示都是
    同一句「重新貼一次」。

    每次呼叫都重讀 DB，不快取：他剛在帳號頁貼完，招牌 15 秒內就該轉綠。
    """
    token = auth_mod.cli_token(user_id) if user_id is not None else None
    if token is None:
        return {
            **_CLAUDE_BASE,
            "ok": False,
            "state": "bad",
            "label": "Claude 未設定憑證",
            "detail": "在 host 上執行 `claude setup-token`，把輸出貼到"
            "帳號管理頁的「CLI 憑證」。沒有它，session 會以未登入狀態"
            "啟動，開場只會看到登入提示。",
        }
    return {
        **_CLAUDE_BASE,
        "ok": True,
        "state": "ok",
        "label": "Claude 憑證已設定",
        "detail": "token 過期不會有預告，症狀是新開的 session 開場失敗"
        "（終端停在登入提示）。遇到就在 host 重跑 `claude setup-token`，"
        "把新的貼回帳號管理頁。已在跑的 session 不受影響。",
    }


def credentials_state(user_id: int | None) -> dict:
    """憑證狀態（招牌徽章用）。形狀維持 {cli: state}，讀取端以 cli 為鍵。"""
    return {"claude": claude_credentials_state(user_id)}


def _guard_credentials(user_id: int | None) -> None:
    """沒設 token 就不要建 session。

    沒有憑證，claude 照樣起得來——只是登出狀態，終端裡停在登入提示，開不了場。
    在「建立」這一刻擋下來，錯誤訊息才有地方告訴人下一步是什麼；放行的話，同一個
    事實要到開了終端才發現，而那個畫面不會解釋原因。
    """
    state = claude_credentials_state(user_id)
    if state["ok"]:
        return
    raise SessionError(
        "尚未設定 Claude 憑證。請在 host 上執行 `claude setup-token`，把輸出貼到帳號管理頁的「CLI 憑證」再開。"
    )
