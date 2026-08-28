"""ADR 0022 regression：skill 與 agents 鋪進 per-user 空間（純檔案系統，不碰 docker）。

uv run --with flask --with docker python tests/test_provision_skills.py

守的是三件事，每一件都對應一個**不會報錯**的壞掉方式：
  · agents/*.md 有沒有單獨落到 `claude/agents/`——沒有的話 skill 會靜靜退到
    general-purpose fallback，掃描照跑、報表卻分不出誰是誰。
  · 重鋪會不會留下上一版的殘檔——留下的話模型會照樣把它讀進去。
  · `claude/skills` 被換成 symlink 時會不會照著寫出去——會的話等於讓被關的人指定
    控制平面往哪裡寫。

2026-08-28 把 `/proc/self/fd` 換成 `rmtree(dir_fd=)` + `rename(dst_dir_fd=)` 之後，
再加六組，**那個做法的核心主張原本一條測試都沒有**：
  · `claude/skills/<name>` 自己被換成 symlink（既有那條蓋的是 `claude/skills` 那一層，
    被 `_open_child_dir` 擋在更前面，根本走不到 `_replace_tree`）。
  · 樹**裡面**被塞進一條指向外部目錄的 symlink，那個目錄既不被刪也不被寫入。
  · fd 驗過之後 `claude/skills` 被整個抽換，寫入仍落在驗過的那個 inode。這一條是唯一
    測得到 `dir_fd=`／`dst_dir_fd=` 的形狀：改成字串路徑就只有它會紅。
  · 目的地是一棵深樹時不可以冒 500（遞迴版 rmtree 會爆 RecursionError）。
  · 來源含 dangling symlink 時目的地要是原樣的斷連結（copytree(symlinks=True) 的語義）。
  · `.skills-*` 暫存目錄有沒有被清掉，**含中途拋例外那條路**。
"""

import errno
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

# --- 以下六組守的是 `_replace_tree` 那個 fd 做法本身 --------------------------------
#
# 上面那些在舊版（`/proc/self/fd/<fd>` 前綴）也全過，所以它們證明不了這次換掉的東西。


def fresh(tag: str) -> str:
    """一個已經正常鋪過一次的空間。之後才好在上面動手腳。"""
    config.SKILLS_SRC_SELF = os.path.join(work, "skills")
    s = make_space(tempfile.mkdtemp(prefix=f"claude-pty-provskills-{tag}-"))
    provision.sync_skills_and_agents(s)
    return s


def leftovers(space: str) -> list[str]:
    return sorted(d for d in os.listdir(space) if d.startswith(".skills-"))


print("== 🔴 claude/skills/<name> 自己被換成 symlink：拒絕，而且不寫穿 ==")
# 既有那條 symlink 測試蓋的是 `claude/skills` 那一層，被 `_open_child_dir` 擋在更前面；
# **這一層從來沒有測過**。擋它的是 `os.rename(dst_dir_fd=)` 不解析目的端最後一個元件：
# 來源是目錄、目的地是連結 → ENOTDIR，是失敗而不是照著連結寫出去。
space_nl = fresh("namelink")
victim_nl = tempfile.mkdtemp(prefix="claude-pty-provskills-namevictim-")
with open(os.path.join(victim_nl, "keep.txt"), "w", encoding="utf-8") as f:
    f.write("外人的檔案")
link_nl = os.path.join(space_nl, "claude", "skills", "demo-skill")
shutil.rmtree(link_nl)
os.symlink(victim_nl, link_nl)
try:
    provision.sync_skills_and_agents(space_nl)
    check("skills/<name> 是 symlink 時應該拋 SessionError", False)
except SessionError as e:
    check("skills/<name> 是 symlink → SessionError", "鋪不進使用者空間" in str(e))
check("外部目錄沒被寫進東西", os.listdir(victim_nl) == ["keep.txt"])
check(
    "外部目錄裡原本的檔案還在（沒被 rmtree 跟著連結刪掉）",
    os.path.isfile(os.path.join(victim_nl, "keep.txt")),
)
check("失敗之後沒留下 .skills-* 暫存目錄", leftovers(space_nl) == [])

print("== 🔴 樹裡面被塞一條指向外部目錄的 symlink：外部目錄既不刪也不寫 ==")
# 這是整個改動的核心主張。`rmtree(dir_fd=)` 逐層 O_NOFOLLOW 開、比對 st_dev/st_ino，
# 遇到連結是把連結本身 unlink 掉，不會走進去把別人的目錄清空。
space_rl = fresh("reflink")
outside = tempfile.mkdtemp(prefix="claude-pty-provskills-outside-")
with open(os.path.join(outside, "keep.txt"), "w", encoding="utf-8") as f:
    f.write("外人的檔案")
refs = os.path.join(space_rl, "claude", "skills", "demo-skill", "references")
shutil.rmtree(refs)
os.symlink(outside, refs)
# ⚠ 包起來而不是讓它直接炸：壞掉的實作在這裡會拋，而未接的例外會**中止整個檔案**，
#   後面幾節連跑都沒跑到；那時看到的紅燈說的是「掛在這」，不是「哪些性質壞了」。
try:
    provision.sync_skills_and_agents(space_rl)
    check("樹裡有一條外部連結時照樣鋪得完", True)
except SessionError as e:
    check(f"樹裡有一條外部連結時不該失敗（{e}）", False)
check("外部目錄整個沒動（沒被刪空、也沒多出東西）", os.listdir(outside) == ["keep.txt"])
check("references 被換回真目錄，不是連結", os.path.isdir(refs) and not os.path.islink(refs))
check("重鋪之後 reference 內容是來源那一份", os.path.isfile(os.path.join(refs, "r.md")))

print("== 🔴 fd 驗過之後 claude/skills 被整個抽換：寫入仍落在驗過的那個 inode ==")
# 這條就是 `dir_fd=` / `dst_dir_fd=` 存在的理由，也是唯一測得到它的形狀：字串路徑版本
# 在這裡會照著新補上的連結把樹寫進 victim，而 fd 版本寫的還是當初驗過的那個目錄。
# 抽換的時機用 copytree 當掛鉤（`_replace_tree` 裡它正好跑在 rmtree／rename 之前）。
space_sw = fresh("swap")
victim_sw = tempfile.mkdtemp(prefix="claude-pty-provskills-swapvictim-")
skills_sw = os.path.join(space_sw, "claude", "skills")
moved_sw = os.path.join(space_sw, "claude", "skills-真正驗過的那個")
_real_copytree = shutil.copytree


def _swap_after_copy(*a, **kw):
    shutil.copytree = _real_copytree  # 只作用一次
    result = _real_copytree(*a, **kw)
    os.rename(skills_sw, moved_sw)  # 把驗過的目錄從名字上抽走
    os.symlink(victim_sw, skills_sw)  # 名字改指別人家
    return result


shutil.copytree = _swap_after_copy
try:
    provision.sync_skills_and_agents(space_sw)
except SessionError:
    pass  # 擋下來也算過，只要沒寫進 victim（下面那兩條才是重點）
finally:
    shutil.copytree = _real_copytree
check("victim 一個字都沒被寫進去", os.listdir(victim_sw) == [])
check(
    "skill 落在當初驗過的那個目錄裡",
    os.path.isfile(os.path.join(moved_sw, "demo-skill", "SKILL.md")),
)

print("== 🔴 目的地是一棵深樹：刪得掉，或明確 SessionError，就是不可以 500 ==")
# 目的地是容器寫得到的，它可以造一棵很深的樹讓遞迴版的 rmtree 爆 RecursionError；
# 那不是 OSError，沒接的話直接變成 500 HTML traceback（app.py 只有 SessionError 的
# errorhandler）。3.13 的 `_rmtree_safe_fd` 已經改成堆疊式，但 3.11／3.12 還是遞迴，
# 而 pyproject 的下限就是 3.11。
space_dp = fresh("deep")
deep = os.path.join(space_dp, "claude", "skills", "demo-skill", "深")
os.makedirs(os.path.join(deep, *(["a"] * 40)))
with open(os.path.join(deep, *(["a"] * 40), "bottom.txt"), "w", encoding="utf-8") as f:
    f.write("最底下")
try:
    provision.sync_skills_and_agents(space_dp)
    check(
        "深樹刪乾淨、重鋪正常",
        not os.path.exists(deep)
        and os.path.isfile(os.path.join(space_dp, "claude", "skills", "demo-skill", "SKILL.md")),
    )
except SessionError:
    check("深樹：明確 SessionError（不是 500）", True)
except Exception as e:  # noqa: BLE001
    check(f"深樹不該冒出 {type(e).__name__}（那會是 500）", False)
check("深樹那一輪沒留下 .skills-* 暫存目錄", leftovers(space_dp) == [])
# 上面那 40 層在 3.13 上刪得動（`_rmtree_safe_fd` 已經是堆疊式），所以它證明的是「會動」，
# 證明不了「爆掉時接得住」。把 RecursionError 直接注進去補那一半：這條在 3.11／3.12 上
# 是真的會發生的事，而 pyproject 的下限就是 3.11。
space_re = fresh("recursion")
_real_rmtree = shutil.rmtree


def _recursion_boom(*a, **kw):
    if kw.get("dir_fd") is not None:  # 只打目的地那一次，暫存目錄的清理照舊
        raise RecursionError("模擬遞迴版 rmtree 在深樹上爆掉")
    return _real_rmtree(*a, **kw)


shutil.rmtree = _recursion_boom
try:
    provision.sync_skills_and_agents(space_re)
    check("rmtree 爆 RecursionError 時應該拋 SessionError", False)
except SessionError:
    check("RecursionError → SessionError（不是 500）", True)
except RecursionError:
    check("RecursionError 冒出去了，那會是 500", False)
finally:
    shutil.rmtree = _real_rmtree
check("RecursionError 那一輪也沒留下 .skills-* 暫存目錄", leftovers(space_re) == [])

print("== 來源含 dangling symlink：目的地也是原樣的斷連結 ==")
# `copytree(symlinks=True)` 的語義：連結原樣複製，不解析、不跟著建目標。少了 symlinks=True
# 的話這裡會直接拋 FileNotFoundError（它會去讀連結指的那個不存在的檔）。
src_dl = make_src(tempfile.mkdtemp(prefix="claude-pty-provskills-danglingsrc-"))
os.symlink(
    "../沒有這個地方/target.md",
    os.path.join(src_dl, "demo-skill", "references", "dangling.md"),
)
config.SKILLS_SRC_SELF = src_dl
space_dl = make_space(tempfile.mkdtemp(prefix="claude-pty-provskills-dangling-"))
try:
    provision.sync_skills_and_agents(space_dl)
    check("來源有斷連結時照樣鋪得完", True)
except SessionError as e:
    check(f"來源有斷連結時不該失敗（{e}）", False)
dst_dl = os.path.join(space_dl, "claude", "skills", "demo-skill", "references", "dangling.md")
check("dangling symlink 原樣複製過去", os.path.islink(dst_dl))
check(
    "連結指向不變",
    os.path.islink(dst_dl) and os.readlink(dst_dl) == "../沒有這個地方/target.md",
)
check("沒有順手把目標建出來（連結還是斷的）", not os.path.exists(dst_dl))

print("== 暫存目錄要清掉：成功那條、以及中途拋例外那條 ==")
space_st = fresh("staging")
check("成功之後 root 底下沒有 .skills-*", leftovers(space_st) == [])
space_bo = fresh("staging-boom")
_real_copytree2 = shutil.copytree


def _boom(*a, **kw):
    _real_copytree2(*a, **kw)  # 先真的複製出來，暫存目錄才有東西可留
    raise OSError(errno.EIO, "模擬複製到一半掛掉")


shutil.copytree = _boom
try:
    provision.sync_skills_and_agents(space_bo)
    check("複製失敗時應該拋 SessionError", False)
except SessionError:
    check("複製失敗 → SessionError（不是 500）", True)
finally:
    shutil.copytree = _real_copytree2
check("拋例外之後 root 底下也沒有 .skills-*", leftovers(space_bo) == [])

print("== 來源不存在：回空清單，不是錯 ==")
config.SKILLS_SRC_SELF = os.path.join(work, "沒有這個目錄")
space2 = make_space(tempfile.mkdtemp(prefix="claude-pty-provskills-empty-"))
check("回 []", provision.sync_skills_and_agents(space2) == [])

print(f"\n{_pass} passed, {_fail} failed")
sys.exit(1 if _fail else 0)
