"""比對「image 裡真的裝了什麼」與「uv.lock 說該裝什麼」。

    python3 deploy/verify_locked_runtime.py <installed.txt> <locked.txt>

為什麼 build 成功還不夠：`pip install --require-hashes` 保證裝進去的檔案沒被掉包，但它
保證不了那份 requirements 是從**現在這一份 uv.lock** 導出來的。兩者分岔的路徑是具體的
（有人手改 Dockerfile 的版本、或 export 的旗標組合換了），而症狀是零——image 照樣 build
成功、服務照樣起得來，只是跑的版本跟版控裡宣稱的不同。

⚠ 只比**交集**。lockfile 裡帶平台條件式的那些（colorama 只在 Windows、pywin32 同理）
  本來就不會落地到 linux image 上，把它們算成缺漏會讓這道閘永遠紅、然後被關掉。
"""

from __future__ import annotations

import re
import sys

_LINE = re.compile(r"^([A-Za-z0-9._-]+)==([^\s;\\]+)")


def parse(path: str) -> dict[str, str]:
    out: dict[str, str] = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            m = _LINE.match(line.strip())
            if m:
                out[m.group(1).lower().replace("_", "-")] = m.group(2)
    return out


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 2
    got, want = parse(argv[1]), parse(argv[2])
    if not got or not want:
        print(f"::error::讀不到套件清單（installed={len(got)} locked={len(want)}）")
        return 1
    shared = sorted(set(got) & set(want))
    bad = [(k, got[k], want[k]) for k in shared if got[k] != want[k]]
    for k, g, w in bad:
        print(f"::error::{k}：image 裡是 {g}，uv.lock 說 {w}")
    only_image = sorted(set(got) - set(want))
    print(f"對得上 {len(shared) - len(bad)}/{len(shared)} 個")
    if only_image:
        # 不是失敗：專案自己那顆（--no-emit-project）與 pip 內建的那幾個會落在這裡。
        print(f"image 獨有（不在 lockfile 的 server extra 裡）：{only_image}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
