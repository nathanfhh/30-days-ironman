"""管理 CLI：建立第一個管理員、列出/重設帳號（ADR 0008 階段 4）。

解 chicken-and-egg：`POST /api/users` 需要管理員身分，但第一個管理員還不存在。
密碼一律由互動輸入（不從 argv 讀——argv 會出現在 `ps` 與 shell history）。

    uv run --with flask --with docker --with sqlalchemy --with argon2-cffi \
        python -m server.cli create-admin alice
"""

from __future__ import annotations

import argparse
import getpass
import sys

from . import auth
from .db import init_db


def _prompt_password() -> str:
    pw = getpass.getpass("密碼：")
    if pw != getpass.getpass("再輸入一次："):
        print("兩次輸入不一致", file=sys.stderr)
        raise SystemExit(1)
    return pw


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="claude-pty-admin", description="claude-pty 帳號管理")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_admin = sub.add_parser("create-admin", help="建立管理員帳號")
    p_admin.add_argument("username")

    p_user = sub.add_parser("create-user", help="建立一般使用者")
    p_user.add_argument("username")

    sub.add_parser("list-users", help="列出所有帳號")

    p_pw = sub.add_parser("set-password", help="重設某帳號密碼（免舊密碼）")
    p_pw.add_argument("username")

    args = parser.parse_args(argv)
    init_db()

    try:
        if args.cmd in ("create-admin", "create-user"):
            user = auth.create_user(args.username, _prompt_password(), is_admin=(args.cmd == "create-admin"))
            role = "管理員" if user["is_admin"] else "使用者"
            print(f"已建立{role}：{user['username']}（id={user['id']}）")
        elif args.cmd == "list-users":
            for u in auth.list_users():
                mark = " [admin]" if u["is_admin"] else ""
                print(f"  {u['id']:>3}  {u['username']}{mark}  建立於 {u['created_at']}")
        elif args.cmd == "set-password":
            users = {u["username"]: u for u in auth.list_users()}
            if args.username not in users:
                print(f"查無帳號：{args.username}", file=sys.stderr)
                return 1
            r = auth.change_password(users[args.username]["id"], _prompt_password(), require_old=False)
            print(f"已重設 {args.username} 的密碼")
            # ⚠ 「密碼改了」不等於「他被請出去了」：cookie 全滅擋不到一條已經升級完成的
            #   WebSocket，所以要接著收終端——而收不掉的時候不可以只印成功就走人。
            _f = r.get("views_failed")
            if _f == -1:
                # 整個動作拋出來，連「有幾場要收」都沒問到——比「N 場收不掉」更糟。
                print(
                    f"⚠ 收終端這一步整個失敗了（{r.get('views_error', '原因不明')}），"
                    f"連有幾場要收都沒查到。他既有的連線可能還可以打字。"
                    f"請再跑一次；再失敗就直接終止他的 session。",
                    file=sys.stderr,
                )
                return 1
            if _f:
                print(
                    f"⚠ 有 {_f} 場的終端沒有收乾淨（收掉 {r.get('views_closed', 0)} 個）。"
                    f"那些連線在收掉之前仍然可以打字，請再跑一次，"
                    f"或直接終止那幾場 session。",
                    file=sys.stderr,
                )
                return 1
    except auth.AuthError as e:
        print(f"錯誤：{e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
