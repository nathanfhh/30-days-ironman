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
├── dev-container/             可拋棄的審查環境（Dockerfile + entrypoint + firewall + run wrapper）
├── gitlab-proxy/              nginx 反向代理：憑證不進 session、端點白名單
├── pyproject.toml             uv 專案（測試工具鏈）
└── install.sh                 把 skills/ 連進 ~/.claude
```

`dev-container/` 與 `gitlab-proxy/` 跟 skill 沒有程式碼相依，但**設定上是耦合的**，
改動時要一起看：

- 代理的白名單來自 `skills/nathan-code-review/scripts/gitlab_api.py` 實際呼叫的端點。
  skill 加一個端點，`gitlab-proxy/nginx.conf.template` 與該 skill 的
  `references/gitlab-api.md` 兩邊都要跟著改。漏掉的症狀是「直連跑得動、走代理 403」。
- `SSH_AUTH_SOCK` 的落點（`/ssh/ssh_sock`）寫在兩個地方：Dockerfile 的 ENV，以及
  run wrapper 的 `-v` 掛載目標。改一邊要改另一邊，否則容器裡的 agent 會連不上。
- `GITLAB_SSH_HOST` 這個 build ARG 餵兩個地方：`known_hosts`，以及防火牆讀的
  `/etc/ncr/gitlab-ssh-host`。**刻意不用環境變數**——env 是容器裡的 `nathan` 寫得到的，
  政策的來源如果是 env，等於讓被關的人自己挑監獄。
- **`init-firewall.sh` 不吃位置參數，sudoers 也把參數鎖成空**
  （`... init-firewall.sh ""`）。sudoers 的語義是「沒列參數 ＝ 任何參數都准」，
  而只要腳本會把參數用進白名單，agent 就能自己擴大白名單、重建整道牆，
  **連自我驗證都會通過**。要加白名單網域就改腳本頂端的 `ALLOWED_DOMAINS` 再 rebuild，
  不要開參數這條路。

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

## 行為回歸測試（「跑 eval」）

`uv run pytest` 測腳本；`tests/nathan-code-review/` 測**文件**。skill 是散文，改一段話會讓三個檔案外的行為悄悄消失，而單元測試看不到——程式碼一行都沒動。

**當使用者說「跑 eval」時**：

1. 收集 `tests/nathan-code-review/*.yaml`
2. **每個 case 平行派出一個 subagent**（單一訊息內多個 Agent 呼叫），prompt 用 `tests/nathan-code-review/judge.md` 的內容，並附上該 case 的 YAML
3. judge 讀該 case 的 `skill_files`、讀 golden conversation，逐條判定 `behavioral_checks` 與 `anti_checks`，每條附一句理由
4. 匯整成總表（case × result × checks 通過數）＋失敗明細＋`drift_notes`

judge 一定要是 subagent，不能由主 agent 自己判——剛改完 skill 的人腦中有作者意圖，會把「我知道我想講什麼」誤讀成「文件講清楚了」，而那正是要測的東西。

**改 skill 前先跑一次當 baseline，改完重跑；baseline 過的 check 失守就 revert。** 完整的 gate 規則、豁免條件與維護慣例見 `tests/nathan-code-review/README.md`。

## 離線是設計前提，不是降級情境

執行環境可能沒有網路，所以這是硬性規則而不是「盡量」：

- **測試不得連外。** 所有 HTTP 走 `tests/conftest.py` 的 in-process stub（綁 `127.0.0.1`）；`gitlab.example.com` 之類的字串只是 fixture，從來沒被撥出去。掃描相關的測試用丟進 `PATH` 前面的假執行檔餵固定輸出，所以連 ruff/ty/oxlint 有沒有裝都不影響。驗證方式：

  ```bash
  uv run --offline pytest
  ```

- **工具不得為了掃描安裝任何東西。** 特別是不要為了讓 `ty` 解析第三方型別而去 `uv sync`：那會執行受審分支控制的程式碼（PEP 517 build backend 由對方的 `pyproject.toml` 指定），而且需要網路。`ty` 的 `bare` 模式是正常模式，報告如實揭露即可，細節見 `references/scanners.md`。

唯一需要網路的是**開發環境初始化**（`uv sync` 裝 pytest/pydantic）。`uv.lock` 有進版控，之後就能 `uv run --offline`。

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
