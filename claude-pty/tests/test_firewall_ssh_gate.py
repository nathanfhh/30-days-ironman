"""🔴 SSH:22 的放行條件是「ssh-agent 在不在」——真的套規則，兩個方向都驗。

    uv run --with docker python tests/test_firewall_ssh_gate.py

需要 docker + dev-container 的 image（規則要真的套進 iptables 才算數）。

## 為什麼要有這一支

22 當初進白名單是為了服務「run wrapper 轉發進來的 ssh-agent」。agent 後來變成
opt-in，白名單卻沒跟著動——**規則活得比它的理由久**，於是每一個沒掛 agent 的容器
都開著一個對它毫無用途的出口。

靜態斷言（`test_profile_mapping` 那幾條）看得到腳本裡有那個判斷，但看不到
**iptables 裡最後有沒有那條規則**——腳本可以有判斷而規則照樣被加進去（順序寫錯、
判斷寫在錯的分支）。所以這裡真的起容器、真的套規則、真的去讀 `iptables -S`。

## 兩個方向缺一不可

  · 有 agent → 22 通。少了這半邊，「全部擋掉」也會綠，而那會讓人的路徑壞掉。
  · 沒 agent → 22 不通。少了這半邊，這支測試就只是在描述現況。
"""
import os
import subprocess
import sys
import tempfile

IMAGE = os.environ.get("CLAUDE_PTY_IMAGE", "ncr-dev-container")
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FW = os.path.join(REPO, "dev-container", "init-firewall.sh")
# ⚠ 與 Dockerfile 的 ENV SSH_AUTH_SOCK、init-firewall.sh 的 SSH_AGENT_SOCK 同一個值。
SOCK_BIND = "/ssh/ssh_sock"

_fails = 0


def check(label, ok):
    global _fails
    if not ok:
        _fails += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    return ok


# 22 的另一半條件是「GITLAB_SSH_HOST 解析得出 IP」，而那個主機名來自 build 時寫死的
# 檔案。測試把它覆蓋成一個**一定解析得到**的公開主機名——否則正向那半會被環境跳過，
# 而「一半綠、一半靜靜跳過」正是這支測試要防的事（它守的是一條安全規則）。
PROBE_HOST = os.environ.get("CLAUDE_PTY_FW_PROBE_HOST", "github.com")


def rules_with_agent(agent: bool) -> str:
    """在容器內真的跑一次 init-firewall.sh，回傳套完之後的 iptables 規則。

    ⚠ 用 `--cap-add=NET_ADMIN`：套 iptables 需要它，正式路徑（build_run_kwargs）給的
      也是同一個能力，不是 --privileged。
    ⚠ 掛的是 repo 最新版的腳本（比照正式路徑的 bind mount），這樣測的才是「改完之後」
      的行為，不是 image 裡烘的那一份。
    """
    tmp = tempfile.mkdtemp(prefix="fwgate-")
    host_file = os.path.join(tmp, "gitlab-ssh-host")
    with open(host_file, "w", encoding="utf-8") as f:
        f.write(PROBE_HOST + "\n")
    argv = ["docker", "run", "--rm", "--cap-add=NET_ADMIN",
            "-v", f"{FW}:/usr/local/bin/init-firewall.sh:ro",
            "-v", f"{host_file}:/etc/ncr/gitlab-ssh-host:ro"]
    if agent:
        # 造一個**真的** unix socket 當 agent：`[ -S ]` 只認 socket，一般檔案不算。
        sock = os.path.join(tmp, "agent.sock")
        subprocess.run(
            [sys.executable, "-c",
             f"import socket;s=socket.socket(socket.AF_UNIX);s.bind({sock!r})"],
            check=True)
        argv += ["-v", f"{sock}:{SOCK_BIND}"]
    argv += ["--entrypoint", "bash", IMAGE, "-c",
             # ⚠ **無參數地跑**：sudoers 把它鎖成 `init-firewall.sh ""`，多帶一個
             #   空字串就不匹配，會變成「a password is required」而不是權限錯誤。
             "sudo /usr/local/bin/init-firewall.sh >/tmp/fw.log 2>&1; "
             "cat /tmp/fw.log; "
             # ⚠ 讀規則走 **firewall-counters.sh**，不是 `sudo iptables -S`：sudoers
             #   只白名單那幾支固定的腳本，直接 sudo iptables 會變成「a password is
             #   required」，而那個失敗看起來會像「規則沒套上」——完全誤導。
             "echo '--- RULES ---'; sudo /usr/local/bin/firewall-counters.sh"]
    out = subprocess.run(argv, capture_output=True, text=True, timeout=300)
    return out.stdout + out.stderr


print("== 沒有 agent：不該有任何 22 的放行 ==")
no_agent = rules_with_agent(agent=False)
_rules_no = no_agent.split("--- RULES ---")[-1]
# ⚠ 比對的 token 必須是 `dpt:22`。規則是用 `iptables -L OUTPUT -v -n -x` 印出來的
#   （dev-container/firewall-counters.sh），那個格式渲染成 `tcp dpt:22`；`--dport 22`
#   是 `iptables -S` 的寫法，在這份輸出裡**永遠不會出現**——原本比它，等於這條斷言
#   恆為真。把 guard 拿掉、對每個容器無條件放行 22，它照樣是綠的（審查 F-005），
#   而這支測試的檔頭自述正是「兩個方向缺一不可」。下面正向那條用的就是 dpt:22。
check("🔴 iptables 裡沒有 dpt:22 的 ACCEPT", "dpt:22" not in _rules_no)
check("而且說得出原因（沒有轉發 agent）", "沒有轉發 ssh-agent" in no_agent)

print("== 有 agent：22 該通（否則人的路徑會壞）==")
with_agent = rules_with_agent(agent=True)
_rules_yes = with_agent.split("--- RULES ---")[-1]
# ⚠ image build 時沒帶 --build-arg 的話 GITLAB_SSH_HOST 是空的，那條路徑本來就不會
#   開 22——那不是這支要測的失敗，講清楚並跳過，不要報成紅燈。
if "未設定 GITLAB_SSH_HOST" in with_agent:
    print("  SKIP  image 未帶 GITLAB_SSH_HOST（build arg），22 那條規則本來就不會出現")
elif "解析不到" in with_agent:
    print("  SKIP  這台機器解析不到那個 GitLab 主機名（離線／DNS 不通）")
else:
    check("🔴 有 agent 時 22 真的被放行（規則真的在 iptables 裡）", "dpt:22" in _rules_yes)
    # 只通解析出來的那一台，不是 blanket。`iptables -L -v -n -x` 的欄位順序是
    # pkts bytes target prot opt in out **source destination** …，所以 destination
    # 是 dpt:22 那一行的倒數第二欄；blanket 的話它會是 0.0.0.0/0。
    _out22 = [ln.split() for ln in _rules_yes.splitlines()
              if "dpt:22" in ln and "ACCEPT" in ln and "spt:" not in ln]
    check("而且只通解析出來的那個位址（不是 blanket 的 22）",
          bool(_out22) and _out22[0][-2] != "0.0.0.0/0")

print(f"\n{'done' if _fails == 0 else f'{_fails} FAILED'}")
sys.exit(1 if _fails else 0)
