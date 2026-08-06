# nathan-code-review

一套把團隊慣例與嚴重度判準編進去的 Code Review Skill。給 GitLab Merge Request、branch
或工作區的變更做審查，產出繁體中文報告，經你同意後才發佈到 MR 討論串。

> 這份 README 是給人看的。Skill 本身（`SKILL.md` 與 `references/`）以英文撰寫——
> AI Agent 用英文思考，但呈現給使用者的一切都是繁體中文。

## 安裝

安裝器在 repo 根目錄，不在這個資料夾裡——它處理的是 Claude Code 的佈署，
不是這個 skill 的內容：

```bash
../../install.sh nathan-code-review
```

建立兩處 symlink：`~/.claude/skills/nathan-code-review` 指向本目錄，
`~/.claude/agents/ncr-*.md` 指向 `agents/` 底下的六個 subagent 定義。
接著在 Claude Code 中執行 `/reload-skills`。

`ncr-*` subagents 沒安裝也能運作——skill 會改用 general-purpose subagent 並把對應的
`agents/ncr-*.md` 當 prompt 讀進去。差別只在 transcript 裡看到的 `subagent_type`，
以及能不能釘住模型。

## 環境需求

| 項目 | 用途 | 沒有的話 |
|---|---|---|
| `GITLAB_TOKEN` | GitLab API（scope: `api`）。找不到時退而讀 `NCR_GITLAB_TOKEN` | MR 模式無法運作；本機模式不受影響 |
| `uv` | 執行 `scripts/`（PEP 723，依賴自動處理） | 必要 |
| `git` | 取碼與算 diff | 必要 |
| `trivy` | 供應鏈、設定、憑證掃描 | 跳過該步驟，並在報告中揭露 |
| `opengrep` + rules | SAST。規則集路徑走 `NCR_OPENGREP_RULES`，預設 `~/semgrep-rules` | 同上 |
| `ruff` / `ty` / `oxlint` | 靜態分析。各自獨立 | 缺哪個跳哪個，其餘照跑 |
| `codegraph` | 符號圖，供 E 架構與 I 回溯分析導航 | 全面改用 `grep` |

盤點目前環境：

```bash
uv run scripts/preflight.py --human
```

沒有任何一個外部工具是硬依賴。缺席的項目一律「跳過該步驟 + 在最終報告揭露」，
審查繼續進行——報告會誠實說出哪些檢查沒跑。

## 怎麼觸發

- 貼一條 GitLab MR URL
- 「幫我 review 這個 branch」／「審查一下目前的變更」
- 指定檔案：「看一下 `app/api/account.py`」
- 作者回覆之後：「他說這條他不同意」→ 走 pushback 分支，不重跑審查

## 資料放在哪

```
/tmp/ncr/{group}-{repo}-mr{iid}/     工作副本，用完即棄，重開機自動清理
$HOME/ncr/{group}/{subgroup}/{repo}/ 歷史報告，永久保存
```

歷史報告是下一輪判定「首次 vs 再次審查」的依據。細節見
`references/workspace-paths.md`。

## 結構

```
SKILL.md          觸發判定、分流、Phase 0–4 骨架、每條分支都要的硬規則
references/       按需載入的細則（九面向、GitLab API、報告格式、反蒙蔽、pushback…）
agents/           六個 ncr-* subagent 定義，一檔兩用
scripts/          五支 stdlib-only 腳本（唯一例外：report_model.py 用 pydantic）
assets/           報告 Markdown 樣板
```

`scripts/report_model.py` 是整條管線的契約。報告的結論由 findings 機械推導，
不由 AI 自由發揮；驗證不過就發不出去：

```bash
uv run scripts/report_model.py validate <report.json>
```

它是唯一要抓套件的腳本。**受限網路（如 dev container 的限制模式）下第一次跑會失敗**——
`uv` 得先下載 pydantic，而防火牆擋掉了 PyPI。先在牆外把 cache 暖起來，之後離線可用：

```bash
uv run scripts/report_model.py --help
```

## 版本

`YYYY.mm.dd.NN`，寫在 `SKILL.md` frontmatter，並複製進每份報告的
`meta.skill_version`，方便日後回頭分析是哪一版產出的結果。
