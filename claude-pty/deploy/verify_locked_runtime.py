"""比對「image 裡真的裝了什麼」與「uv.lock 說該裝什麼」。

    python3 deploy/verify_locked_runtime.py <installed.txt> <locked.txt> [marker-env.json]

`installed.txt` 是 image 內 `pip freeze` 的輸出（`/app/installed-requirements.txt`），
`locked.txt` 是 `uv export --frozen --no-dev --extra server --group build --no-emit-project`
的輸出。

為什麼 build 成功還不夠：`pip install --require-hashes` 保證裝進去的檔案沒被掉包，但它
保證不了那份 requirements 是從**現在這一份 uv.lock** 導出來的。兩者分岔的路徑是具體的
（有人手改 Dockerfile 的版本、或 export 的旗標組合換了），而症狀是零：image 照樣 build
成功、服務照樣起得來，只是跑的版本跟版控裡宣稱的不同。

⚠ **這支的第一版是假綠的，那個錯誤值得留在這裡。** 它只比兩邊的交集，理由是「lockfile
  裡帶平台條件式的 colorama／pywin32 本來就不會落地到 linux image 上」。那個理由對，
  但推出來的做法錯：交集把「這個套件整顆不見了」跟「這個套件本來就不該在」壓成同一件事。
  實測拿掉整顆 Flask，舊版印「對得上 23/23 個」然後 exit 0。
  正確的做法不是放寬比對，是**把「本來就不該在」講清楚**：用 marker 算出哪些該在，
  用一份寫得出名字的清單交代哪些被刻意移除，剩下的一個都不准少。

四類判定，任何一類不成立就 exit 1：

  1. **少了**：marker 成立、又不在 BUILD_ONLY 裡的套件，image 裡必須有
  2. **沒有釘版本**：該有的套件在，但形狀是 `name @ url` 而不是 `name==版本`
  3. **版本不同**：兩邊都有但版本對不上
  4. **多了**：image 裡有、而 lockfile 的 runtime 集合沒有的

⚠ **第 2 類是這支第二版自己造出來的洞，Codex 複驗抓到的。** 那一版為了不讓專案本身
  （`claude-pty @ file:///app`）被誤判成缺漏，在「少了」的計算裡直接把整袋未釘版本扣掉：
  `set(required) - set(installed) - unpinned`。於是把 `Flask==3.1.3` 換成
  `Flask @ file:///tmp/flask` 之後，它既不算少（被 unpinned 扣掉）也不算多（flask 在
  lockfile 裡），實測 exit 0。**修一個假綠的時候造出一個更窄的假綠**，形狀完全一樣：
  把「合法的例外」與「該抓的情況」用同一個減法壓成一件事。
  現在專案本身靠 `ALLOWED_EXTRA` 這個寫得出名字的清單放行，而任何**該釘版本卻沒釘**的
  必要套件單獨成一類：版本驗不了就是驗不了，不能因為它「人在」就算過。

第 3 類為什麼也要紅：這顆 image 的相依應該**完全**由 lockfile 決定。多出來的東西代表有一條
繞過 lockfile 的安裝路徑（Dockerfile 手動 `pip install`、某個套件的 post-install），而那條
路徑上的東西不會出現在 CVE 掃描的比對基準裡。要合法多出東西，就把它寫進 lockfile 或
`ALLOWED_EXTRA`，兩條路都會在版控裡留下痕跡。
"""

from __future__ import annotations

import json
import sys

try:
    from packaging.markers import default_environment
    from packaging.requirements import Requirement
except ImportError:  # pragma: no cover - 取決於環境
    # ⚠ 刻意**不**退回自刻的 marker 解析。marker 的語法有 and／or／in／版本比較，
    #   自己寫一份剛好會在「看起來對但算錯」的地方失效，而這支的整個價值就是不要假綠。
    print("::error::這支需要 `packaging`（marker 要照規格評估）：pip install packaging", file=sys.stderr)
    raise SystemExit(2) from None


# Dockerfile 最後一步 `pip uninstall -y uv setuptools wheel` 移除的 build 工具。
# 它們在 lockfile 的 `--group build` 裡（build 那一步真的要用），但**刻意不留在 runtime**：
# 留著不會被任何東西 import，卻會被 CVE 掃描算進去。
# ⚠ 這份清單要跟 Dockerfile 那一行同步。少寫一個，這支會報「少了」；多寫一個，真的少裝時
#   會被靜靜放過，也就是回到假綠。
BUILD_ONLY = frozenset({"uv", "setuptools", "wheel"})

# 允許出現在 image、但不在 lockfile 的 runtime 集合裡的東西。
# 目前只有專案自己：`uv export` 帶 `--no-emit-project` 所以它不在 locked 裡，而 `pip freeze`
# 會印成 `claude-pty @ file:///app`（沒有 `==`），落在「未釘版本」那一袋。
# ⚠ **未釘版本的那一袋也要過這道白名單。** 第一版沒有，於是這個常數是死碼：`claude-pty`
#   永遠不會出現在釘版本的集合裡，所以永遠不會被扣掉。真正的後果是另一邊：任何人用
#   `pip install git+https://...` 之類的方式塞東西進去，落地的也是 `name @ url` 這種形狀，
#   而它會整個繞過「多了」的判定。所以兩袋一起檢查。
ALLOWED_EXTRA = frozenset({"claude-pty"})


def _canon(name: str) -> str:
    return name.lower().replace("_", "-").replace(".", "-")


def _logical_lines(path: str) -> list[str]:
    """把續行接起來、拿掉註解與 `--hash=` 之類的旗標，回傳一行一個 requirement。"""
    raw = open(path, encoding="utf-8").read()
    joined = raw.replace("\\\n", " ")
    out = []
    for line in joined.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        tokens = [t for t in line.split() if not t.startswith("-")]
        if not tokens:
            continue
        out.append(" ".join(tokens))
    return out


def parse_locked(path: str, env: dict) -> tuple[dict[str, str], dict[str, str]]:
    """回傳 (marker 成立的, marker 不成立的)，兩袋都是 {正規化名稱: 版本}。"""
    on: dict[str, str] = {}
    off: dict[str, str] = {}
    for line in _logical_lines(path):
        req = Requirement(line)
        ver = next((s.version for s in req.specifier if s.operator == "=="), None)
        if ver is None:
            continue  # export 一律是 `==`；不是的話不在這支的職責範圍
        target = on if (req.marker is None or req.marker.evaluate(env)) else off
        target[_canon(req.name)] = ver
    return on, off


def parse_installed(path: str) -> tuple[dict[str, str], set[str]]:
    """回傳 ({正規化名稱: 版本}, 沒有釘版本的名稱)。後者就是 `name @ url` 那種。"""
    pinned: dict[str, str] = {}
    unpinned: set[str] = set()
    for line in _logical_lines(path):
        req = Requirement(line)
        ver = next((s.version for s in req.specifier if s.operator == "=="), None)
        if ver is None:
            unpinned.add(_canon(req.name))
        else:
            pinned[_canon(req.name)] = ver
    return pinned, unpinned


def main(argv: list[str]) -> int:
    if not 3 <= len(argv) <= 4:
        print(__doc__)
        return 2

    env = dict(default_environment())
    if len(argv) == 4:
        # CI 從 deploy image 自己問出來（見 .github/workflows/tests.yml）：marker 要用
        # **那顆 image** 的環境算，不是用跑這支腳本的那台機器的。
        env.update(json.load(open(argv[3], encoding="utf-8")))

    on, off = parse_locked(argv[2], env)
    installed, unpinned = parse_installed(argv[1])

    if not on:
        print(f"::error::lockfile 讀不到任何 marker 成立的套件（{argv[2]}）", file=sys.stderr)
        return 1
    if not installed and not unpinned:
        print(f"::error::image 的套件清單讀不到任何東西（{argv[1]}）", file=sys.stderr)
        return 1

    required = {k: v for k, v in on.items() if k not in BUILD_ONLY}

    # ⚠ `- unpinned` 是刻意留著的：它讓「以 direct reference 出現」不會被算成兩次錯
    #   （既少了又沒釘版本）。真正抓它的是下面那一類，不是把它默默放行。
    missing = sorted(set(required) - set(installed) - unpinned)
    # 該有、人也在，但形狀是 `name @ url`：版本驗不了，等同沒有比對過。
    # ALLOWED_EXTRA 裡的（專案自己）不在 required 裡，所以不會落到這一類。
    unpinned_required = sorted((set(required) & unpinned) - ALLOWED_EXTRA)
    mismatch = sorted(
        (k, installed[k], required[k]) for k in set(required) & set(installed) if installed[k] != required[k]
    )
    extra = sorted((set(installed) | unpinned) - set(on) - ALLOWED_EXTRA)

    for k in missing:
        print(f"::error::少了 {k}=={required[k]}：lockfile 說該裝（marker 成立、也不是 build 工具），image 裡沒有")
    for k in unpinned_required:
        print(
            f"::error::{k} 沒有釘版本：lockfile 說該裝 {required[k]}，但 image 裡它是 `{k} @ ...` 這種"
            f" direct reference，版本驗不了。它是從 lockfile 以外的地方裝進來的"
        )
    for k, got, want in mismatch:
        print(f"::error::{k}：image 裡是 {got}，uv.lock 說 {want}")
    for k in extra:
        ver = installed.get(k, "（沒有釘版本，多半是 `name @ url` 裝進去的）")
        print(
            f"::error::多了 {k} {ver}：image 裡有，但 lockfile 的 runtime 集合沒有。"
            f"要嘛把它加進 lockfile，要嘛加進這支的 ALLOWED_EXTRA 並寫清楚理由"
        )

    print(
        f"lockfile runtime {len(required)} 個（marker 不成立跳過 {len(off)} 個："
        f"{', '.join(sorted(off)) or '無'}；build 工具跳過 {len(BUILD_ONLY & set(on))} 個）"
    )
    tail = f"、{len(unpinned)} 個未釘版本（{', '.join(sorted(unpinned))}）" if unpinned else ""
    print(f"image 內 {len(installed)} 個釘版本{tail}")
    bad = len(missing) + len(unpinned_required) + len(mismatch) + len(extra)
    print("全部對得上" if not bad else f"{bad} 項不符")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
