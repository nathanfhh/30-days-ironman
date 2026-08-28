"""ADR 0022 regression：skill 與 agents 鋪進 per-user 空間（純檔案系統，不碰 docker）。

uv run --with flask --with docker python tests/test_provision_skills.py

守的是三件事，每一件都對應一個**不會報錯**的壞掉方式：
  · agents/*.md 有沒有單獨落到 `claude/agents/`——沒有的話 skill 會靜靜退到
    general-purpose fallback，掃描照跑、報表卻分不出誰是誰。
  · 重鋪會不會留下上一版的殘檔——留下的話模型會照樣把它讀進去。
  · `claude/skills` 被換成 symlink 時會不會照著寫出去——會的話等於讓被關的人指定
    控制平面往哪裡寫。
"""

import os
import shutil
import sys
import tempfile

_tmp = tempfile.mkdtemp(prefix="claude-pty-provskills-")
os.environ["CLAUDE_PTY_DB_URL"] = f"sqlite:///{os.path.join(_tmp, 'test.db')}"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from server import config, provision  # noqa: E402
from server.errors import SessionError  # noqa: E402

_pass = _fail = 0


def check(label, ok):
    global _pass, _fail
    _pass += ok
    _fail += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")


def make_src(root: str) -> str:
    """一個長得像 repo `skills/` 的來源：一個 skill、兩個 agent、一份 reference。"""
    src = os.path.join(root, "skills")
    os.makedirs(os.path.join(src, "demo-skill", "agents"))
    os.makedirs(os.path.join(src, "demo-skill", "references"))
    for name, body in (
        (os.path.join("demo-skill", "SKILL.md"), "# demo"),
        (os.path.join("demo-skill", "agents", "demo-scan.md"), "scan agent"),
        (os.path.join("demo-skill", "agents", "demo-check.md"), "check agent"),
        (os.path.join("demo-skill", "agents", "notes.txt"), "不是 .md，不該被當成 agent"),
        (os.path.join("demo-skill", "references", "r.md"), "ref"),
    ):
        with open(os.path.join(src, name), "w", encoding="utf-8") as f:
            f.write(body)
    return src


def make_space(root: str) -> str:
    space = os.path.join(root, "space")
    os.makedirs(os.path.join(space, "claude"))
    return space


work = tempfile.mkdtemp(prefix="claude-pty-provskills-work-")
config.SKILLS_SRC_SELF = make_src(work)
space = make_space(work)

print("== 鋪一次：skill 整棵樹進 claude/skills，agents/*.md 另外進 claude/agents ==")
names = provision.sync_skills_and_agents(space)
check("回報鋪了 demo-skill", names == ["demo-skill"])
check(
    "skill 樹在 claude/skills/demo-skill",
    os.path.isfile(os.path.join(space, "claude", "skills", "demo-skill", "SKILL.md")),
)
check(
    "reference 也跟著進去",
    os.path.isfile(os.path.join(space, "claude", "skills", "demo-skill", "references", "r.md")),
)
# 🔴 這一條就是這次的病灶：只鋪 skills/ 不鋪 agents/ 時，上面全過、這裡全滅，
#    而 session 跑起來完全不報錯。
agents_dir = os.path.join(space, "claude", "agents")
check(
    "agents/*.md 單獨落在 claude/agents（Claude Code 真正認的位置）",
    os.path.isfile(os.path.join(agents_dir, "demo-scan.md"))
    and os.path.isfile(os.path.join(agents_dir, "demo-check.md")),
)
check(
    "非 .md 不會被當成 agent 鋪過去",
    not os.path.exists(os.path.join(agents_dir, "notes.txt")),
)

print("== 重鋪：先刪後複製，上一版多出來的檔案不留下 ==")
stale = os.path.join(space, "claude", "skills", "demo-skill", "references", "old.md")
with open(stale, "w", encoding="utf-8") as f:
    f.write("上一版留下的殘檔")
provision.sync_skills_and_agents(space)
check("殘檔被清掉（不是就地覆寫）", not os.path.exists(stale))
check(
    "重鋪之後正常檔案還在",
    os.path.isfile(os.path.join(space, "claude", "skills", "demo-skill", "SKILL.md")),
)

print("== 🔴 claude/skills 被換成 symlink：拒絕開場，不照著寫出去 ==")
victim = os.path.join(work, "victim")
os.makedirs(victim, exist_ok=True)
shutil.rmtree(os.path.join(space, "claude", "skills"))
os.symlink(victim, os.path.join(space, "claude", "skills"))
try:
    provision.sync_skills_and_agents(space)
    check("symlink 應該要拋 SessionError", False)
except SessionError as e:
    check("symlink → SessionError", "不是一個正常目錄" in str(e))
check("沒有跟著連結寫到別處去", os.listdir(victim) == [])

print("== 🔴 claude/agents 底下的個別檔案被換成 symlink：不寫穿到目標 ==")
# shutil.copyfile 開目的地是 open(dst, "wb")——**跟著目的地的 symlink 走**，
# truncate 目標、連結留著。容器放一條指向別人 owner.json 的連結，下一場 provision
# 就會以控制平面的身分把它蓋掉，那個使用者從此開不了場。沒有競速視窗，一次就成。
space3 = make_space(tempfile.mkdtemp(prefix="claude-pty-provskills-agentlink-"))
config.SKILLS_SRC_SELF = os.path.join(work, "skills")
provision.sync_skills_and_agents(space3)  # 先鋪一次，agents/ 才存在
outsider = os.path.join(work, "outsider.json")
with open(outsider, "w", encoding="utf-8") as f:
    f.write('{"user_id": 2}')
planted = os.path.join(space3, "claude", "agents", "demo-scan.md")
os.unlink(planted)
os.symlink(outsider, planted)
try:
    provision.sync_skills_and_agents(space3)
except SessionError:
    pass  # 擋下來也算過，只要沒寫穿
with open(outsider, encoding="utf-8") as f:
    body = f.read()
check("沒有寫穿到 symlink 指的那個檔", body == '{"user_id": 2}')
check("agent 檔本身不再是 symlink", not os.path.islink(planted))

print("== 🔴 接線回歸：provision_user_space 真的會呼叫它 ==")
# 只測函式不測接線的話，這次的原始 bug（「有函式但沒有人呼叫」）再犯一次也不會紅。
space4 = os.path.join(tempfile.mkdtemp(prefix="claude-pty-provskills-wire-"), "user-1")
os.makedirs(space4)
_saved = (config.MOUNTS, config.user_space)
try:
    config.MOUNTS = {"sentinel": "非空即可，空的話 provision 會整個跳過"}
    config.user_space = lambda user_id, host=False: space4
    provision.provision_user_space(1, "tester")
    check(
        "開場之後 claude/agents/*.md 真的在",
        os.path.isfile(os.path.join(space4, "claude", "agents", "demo-scan.md")),
    )
    check(
        "開場之後 claude/skills/<name> 真的在",
        os.path.isfile(os.path.join(space4, "claude", "skills", "demo-skill", "SKILL.md")),
    )
finally:
    config.MOUNTS, config.user_space = _saved

print("== 來源不存在：回空清單，不是錯 ==")
config.SKILLS_SRC_SELF = os.path.join(work, "沒有這個目錄")
space2 = make_space(tempfile.mkdtemp(prefix="claude-pty-provskills-empty-"))
check("回 []", provision.sync_skills_and_agents(space2) == [])

print(f"\n{_pass} passed, {_fail} failed")
sys.exit(1 if _fail else 0)
