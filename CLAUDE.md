# 30-days-ironman

Claude Code skills, and the tests that keep their scripts honest.

## Layout

```
.
├── skills/<skill-name>/       一個 skill 一個目錄
│   ├── SKILL.md               入口，frontmatter 有 name / description / version
│   ├── references/            隨用隨讀的參考文件
│   ├── agents/                subagent 定義（會被 install.sh 另外連進 ~/.claude/agents）
│   ├── assets/                樣板
│   └── scripts/               PEP 723 單檔腳本，用 `uv run <script>` 執行
├── tests/                     所有 skill 共用一組測試
├── pyproject.toml             uv 專案（測試工具鏈）
└── install.sh                 把 skills/ 連進 ~/.claude
```

## 這個 repo 是「上線中」的

`install.sh` 建立的是 **symlink，不是複製**：

```
~/.claude/skills/<name>  ->  <repo>/skills/<name>
```

所以在這裡改一行，下一次對話就吃得到。好處是迭代快，代價是**沒有暫存區**——改壞了就是直接壞在下一次真實審查上。動 `scripts/` 之前先跑測試。

```bash
./install.sh                    # 安裝全部
./install.sh nathan-code-review # 只裝一個
```

改完在 Claude Code 裡執行 `/reload-skills`。

## 測試

```bash
uv sync                # 一次就好
uv run pytest          # 全部
uv run pytest tests/test_gitlab_api.py -v
```

`tests/conftest.py` 提供兩件事：

- `load_script(name)`／`gitlab_api`、`report_model`、`render_report` fixtures——`scripts/` 底下是 PEP 723 單檔、不是套件，所以用路徑匯入。
- `stub_server` fixture——一個 in-process 的 HTTP stub。`http_request` 裡值得釘住的路徑（重試、不跟隨轉址、壞掉的 JSON、逾時）在健康的 GitLab 上永遠打不出來，只能靠它。**測試不會連到任何真實 GitLab。**

寫新測試時：`Reply(status=..., body=..., headers=..., delay=...)` 排隊給 stub server，`stub_server.requests` 拿回實際收到的請求。要測重試就掛 `fast_retries` fixture，否則真的 backoff 會讓每個測試多花好幾秒。

## Lint

```bash
uvx ruff check skills/ tests/
```

`pyproject.toml` 明訂 `target-version = "py311"`。這不是裝飾：ruff 在沒有這一行時會去讀 `requires-python` 推斷版本，所以**在 repo 根目錄新增 pyproject.toml 這件事本身，就會改變 skill 腳本的 lint 結果**（版本相關的規則會突然開始／停止觸發），即使那些程式碼一行都沒動。寫死在這裡，之後調整 Python 下限就是一個明確的決定，而不是 lint 結果的意外變動。

## 改動 skill 之後要 version bump

`skills/<name>/SKILL.md` 的 frontmatter 有 `version: YYYY.mm.dd.NN`。這個值會被複製進每一份審查報告的 `meta.skill_version`，是「哪一版產生了哪一份報告」的唯一線索——之後回頭做校準分析時就靠它。

**只要動了 skill 的任何內容就要 bump**，包含 `SKILL.md`、`references/`、`agents/`、`assets/`、`scripts/`。規則：

- **同一天再改** → 末兩碼 `NN` +1。`2026.08.02.01` → `2026.08.02.02`
- **不同天** → 日期換成今天，`NN` 回到 `01`。`2026.08.02.03` → `2026.08.05.01`

`NN` 是兩位數、補零。同一天改到第十次就是 `.10`。

## 寫作慣例

- **skill 內容用英文**（`SKILL.md`、`references/`、`agents/`），因為那是模型讀的。
- **使用者看得到的輸出用繁體中文（zh-TW）**：報告、終端訊息、回覆作者。技術名詞保持英文——severity 名稱、工具名、檔案路徑、識別字、結論字串。
- 註解與 docstring 解釋**為什麼**，不重述程式碼在做什麼。
- 腳本一律標準庫優先；真的需要相依時寫進 PEP 723 的 `# /// script` 區塊，不要加進 `pyproject.toml`（那裡只放測試工具鏈）。

## nathan-code-review 的執行前提

```bash
GITLAB_TOKEN          # GitLab API token，scope 要有 api
NCR_OPENGREP_RULES    # Semgrep rules 目錄，預設 $HOME/semgrep-rules
```

盤點目前環境：

```bash
uv run skills/nathan-code-review/scripts/preflight.py --human
```
