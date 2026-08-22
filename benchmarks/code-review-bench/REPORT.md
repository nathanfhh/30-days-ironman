# nathan-code-review 在 Code Review Bench 上的成績

**全部 50 個 PR**、6 個工具、1,395 條 claim 的盲測驗證。原始資料在 `scores/`，每個中間產物都在 `data/`。

- **資料集**：[withmartian/code-review-benchmark][crb] 的 offline 半邊
- **執行日期**：2026-08-03
- **skill 版本**：`nathan-code-review` 2026.08.02.05
- **judge / verifier**：subagent，取代上游三處外部 LLM 呼叫

[crb]: https://github.com/withmartian/code-review-benchmark

---

## 0. 被測的到底是什麼

`nathan-code-review` 平常不是這樣跑的，所以得先講清楚被測的是哪一段。

**日常用法**是在 Claude Code 裡貼一個 GitLab MR URL，然後：

```
Phase 0    preflight——盤點哪些工具與憑證在，缺的記進 scans[] 並在報告裡揭露
Phase 0.5  intent——這個改動該不該做、該不該在這個 MR 做、時機對不對
Phase 1    判斷是首次審查還是 re-review
Phase 2    確定性掃描（ruff / ty / oxlint / opengrep / trivy，各自一個 subagent）
Phase 3    深度審查——fresh-eyes 先看，再跑九個面向
Phase 4    交付——寫 JSON、驗證 schema、quality-check、
           **問過使用者才發佈**，然後貼成 GitLab discussion
```

輸入不一定是 MR URL：branch 名稱、未 commit 的改動、或最多三個檔案都可以，各自走不同模式。另外還有一條完全不同的分支——**作者對已發佈的審查表達異議**時，不重跑 pipeline，改讀 `references/pushback.md` 重新評估立場。

**benchmark 跑到的是 Phase 0 到 Phase 3，加上報告產出。** 沒跑到的是：

| 沒跑到 | 為什麼 |
|---|---|
| Phase 4 的發佈 | 沒有 GitLab，也不該對真實 MR 發文 |
| `ncr-quality-check` | subagent 裡不能再派 subagent（§7） |
| re-review 分支 | benchmark 沒有「上次審查過」的狀態 |
| pushback 分支 | benchmark 沒有作者回覆 |
| 大部分掃描器 | 環境裡只有 ruff（§7） |

所以這份成績單量的是**「讀一份 diff、產出一份有嚴重度分級的報告」**這件事，不是整個 skill。發佈前的把關（quality-check）與對話式的往返（re-review、pushback）都沒有被評分——而那兩塊剛好是日常使用時最常觸發的部分。

---

## 1. 一句話結論

在 benchmark 自己的算法下，這個 skill **敬陪末座**（F1 22.0%）；在把 ground truth 送去盲測驗證之後，它**排第一**（F1 87.6%）。

這兩個數字都是真的。差別不在工具變好了，而在**衡量的東西換了**：前者問「你講的話有幾成命中人類寫下來的那份清單」，後者問「你講的話有幾成是真的」。

---

## 2. Raw 分數（上游算法，我們的 judge）

| 工具 | precision | recall | F1 | 候選數 |
|---|---:|---:|---:|---:|
| cubic-v2 | 56.8% | 67.2% | **61.5%** | 175 |
| augment | 47.4% | 59.1% | 52.6% | 178 |
| greptile-v4-1 | 40.6% | 48.9% | 44.4% | 168 |
| coderabbit | 26.3% | 56.9% | 35.9% | 318 |
| claude-code | 32.9% | 39.4% | 35.9% | 173 |
| **nathan-code-review** | **13.0%** | **73.0%** | **22.0%** | **775** |

候選數是各工具實際提出的候選條數。precision 依上游慣例為 TP/(TP+FP)，其中「對上一條已被其他候選命中的 golden」的重複命中兩邊都不計（我們 4 條、cubic-v2 13 條、coderabbit 21 條、claude-code 9 條、augment 7 條、greptile-v4-1 3 條），所以 TP+FP 會略小於候選數。

recall **最高**，precision 最後一名，總分墊底。

原因是算法本身：上游的 `precision = 命中的 golden 數 / 候選總數`。137 條 golden comment 對上我們的 775 個候選，即使每一個候選都正確，precision 的上限也只有 17.7%。**這個指標懲罰的是「說得多」，而不是「說錯」**——兩者在分數上完全無法區分。

`open_questions`（skill 刻意不給 severity 的未驗證提問）也一併算進候選。排除它們的話是 P=14.9% / R=70.8% / F1=24.7%，差距不大，不影響結論。兩種算法都列在 `scores/raw_cells.csv` 與 `raw_cells_lenient.csv`。

### judge 校準

整套流程建立在「用 subagent 取代 API 呼叫」上，所以這件事必須被量測而不是宣稱。同業工具的候選清單是上游 judge 讀過的同一批 bytes，而上游用三個 judge model 各發表過一次結果：

| 工具 | 我們的 judge | opus-4.5 | sonnet-4.5 | gpt-5.2 | 上游三者的落差 | 我們與其均值的差 |
|---|---:|---:|---:|---:|---:|---:|
| cubic-v2 | 61.5% | 61.8% | 61.4% | 59.0% | 2.8pt | +0.8pt |
| augment | 52.6% | 53.5% | 53.4% | 49.6% | 4.0pt | +0.5pt |
| greptile-v4-1 | 44.4% | 44.0% | 40.4% | 39.5% | 4.5pt | +3.1pt |
| coderabbit | 35.9% | 35.2% | 37.1% | 33.3% | 3.8pt | +0.8pt |
| claude-code | 35.9% | 37.6% | 34.8% | 33.0% | 4.5pt | +0.7pt |

最大偏差 3.1pt **小於上游自己三個 judge 之間的最大歧異 4.5pt**。名次對其中兩個 judge 完全一致；對 opus-4.5 有一處對調（coderabbit 與 claude-code），而那兩家在我們的 judge 下同分 35.9%，本來就在雜訊內。代餵的 judge 沒有引入新的誤差來源。（這是全部 50 個 PR 重算的；19 個 PR 時是 2.6pt vs 5.1pt，結論不變。）

這同時給出雜訊底線：**在這個資料集上，4–5 個百分點以內的 F1 差距不代表工具品質差異。**

---

## 3. 校正：把 ground truth 也送去驗證

### 3.1 為什麼要做

上游自己的 methodology 寫得很清楚：

> the gold set caps measurement at human performance. If a model finds a real bug that human annotators missed, it gets penalized — the bug isn't in the gold set, so the discovery is scored as a false positive rather than evidence of superior recall.

他們提出的解法叫 adversarial validation。它**還沒有被實作**——不是疏漏，是排程：`methodology/full.md` §12 的 Stage 2 清單裡明寫 `Begin adversarial validation, following the triggers described in §6`，而 `offline/` 底下 grep 不到任何相關程式碼。

所以這一節是把一個上游已經寫好、但還沒輪到的東西提前跑一次。詳見 §6.1。

### 3.2 怎麼做

每個 PR 蒐集一個匿名池：**全部 golden comments ＋ 全部工具未命中的候選**，用 slug 的 sha256 排序打散，編號 c001…。驗證員拿到 diff 與完整 checkout，逐條判 `real` / `not_real` / `unclear`，並給出 `file:line` 證據與 cluster 標籤。

**驗證員不知道哪些 claim 是人寫的。** 這是整個設計的重點——同一個讀者、同一把尺，同時量 ground truth 與被 ground truth 否決的東西。少了這一步，「golden set 漏了東西」就只是我們自己工具的自我主張。

### 3.3 結果

1,395 條 claim：**1,206 real、140 not_real、49 unclear**（86.5% 判為真實）。

**人類寫的 golden comments，137 條裡有 23 條沒通過盲測驗證——16.8%。** 這是這次跑下來最該被記住的數字，而且它跟哪個工具好無關。

### 3.4 校正後分數

precision 只取決於工具自己的輸出，所以直接可比。recall 的分母怎麼取則是關鍵，我列三種。

**（a）recall 對「通過驗證的 golden comments」——完全對稱，沒有任何工具能影響這個標的**

| 工具 | precision | recall | F1 |
|---|---:|---:|---:|
| **nathan-code-review** | **89.2%** | 85.1% | **87.1%** |
| augment | 87.6% | 68.4% | 76.8% |
| cubic-v2 | 77.7% | 69.3% | 73.3% |
| greptile-v4-1 | 87.5% | 57.0% | 69.0% |
| coderabbit | 71.1% | 63.2% | 66.9% |
| claude-code | 82.1% | 47.4% | 60.1% |

**這是主要結論。** 注意 greptile-v4-1（87.5%）與 augment（87.6%）的 precision 幾乎與我們並駕齊驅——它們話少而準，是另一種完全合理的取捨；差距主要出現在 recall。

**（b）leave-one-out recall**——標的是「通過驗證的 golden ＋ 至少有另一個工具也提出的真問題」，扣掉每個工具自己獨有的發現。**事後稽核（§3.6）判定這張表不對稱**：每個工具只從自己的標的扣掉自己的獨有發現，所以我們的考卷 440 題、同業 797～853 題（他們的標的仍包含我們的 434 個獨有 cluster）。保留於此供對照，不作為結論：

| 工具 | precision | LOO recall | F1 |
|---|---:|---:|---:|
| nathan-code-review | 89.2% | 60.2% | 71.9% |
| coderabbit | 71.1% | 19.7% | 30.8% |
| augment | 87.6% | 16.3% | 27.5% |
| greptile-v4-1 | 87.5% | 15.7% | 26.6% |
| claude-code | 82.1% | 14.4% | 24.5% |
| cubic-v2 | 77.7% | 14.3% | 24.2% |

**（c）recall 對「完整擴充 ground truth」——這個數字我算了但不該拿來比較，理由見下。**

### 3.5 一個我做錯又改掉的地方

第一版校正表用「通過驗證的 golden ∪ 所有被確認的 cluster」當 recall 分母，我們的工具拿到 84.2% F1，其他人 26–38%。

那個表是錯的，而且錯在對我們有利。752 個被確認的 cluster 裡，我們提出了 594 個，其中 **426 個只有我們提出**。也就是說，超過一半的「標準答案」是我們自己寫的——用它來量 recall，量到的是話多，不是 recall。這正是這整個校正要修掉的偏誤的鏡像。

(a) 和 (b) 是替代方案。原始表仍在 `scores/summary.json` 的 `corrected` 欄位，標示清楚，沒有刪掉。

### 3.6 事後稽核：換一個算法，名次就換

跑完之後我派了一個沒有任何本次 context 的 agent 稽核整套設計，指令是「假設作者誠實但有動機，找出機制上偏袒受測工具的地方」。它的核心發現：**校正後的 precision 不再對話多收費**。分子擴成「命中 golden ＋ 驗證員確認為真的發現」，而驗證員對 86.5% 的 claim 判真；我們有 671 條未命中候選可被平反，同業 70～219 條。原始算法裡 precision 是唯一處罰話多的項目，校正把處罰拿掉了，recall 卻仍獎勵話多。

它用兩種同樣站得住的算法重算（已獨立驗證，原始數據在 `scores/alternative_views.json`）：

| 算法 | 我們 F1 | 名次 | 第一名 |
|---|---:|:---:|---|
| 上游原規則（§2） | 22.0% | 6/6 | cubic-v2 61.5% |
| 盲測校正（§3.4a） | 87.1% | 1/6 | — |
| 只對驗證過的 golden，不給 discovery credit | 21.9% | 6/6 | cubic-v2 57.2% |
| 共用考卷（golden＋至少兩工具都提的 cluster） | 49.3% | 6/6 | augment 57.9% |

第三列幾乎等於第一列：把 23 條失效 golden 拿掉，raw 名次根本沒動——**名次反轉 100% 來自 discovery credit，0% 來自「golden 有錯」這個發現**。後兩種算法把「只有一個工具找到的真問題」記零分，對發現型工具不利，所以它們是下界不是真值；但結論已經清楚：**名次取決於算法選擇，名次不是這個實驗的產出。**

發言量與 recall 的關係（對驗證過的 golden；按嚴重度事後截斷。截斷後發佈本身是可實作的部署策略，所以這條曲線是限額部署可達成績的地板，不是「事前告知限額重跑」的量測，那個實驗沒做）：150 條 42.1%、200 條 49.1%、408 條 71.9%、775 條 85.1%；cubic-v2 用 175 條達 69.3%。

不挑算法也成立的只有三件事：golden 有 16.8% 站不住；共用考卷上我們找到的真問題最多（recall 85.1%，唯一超過 80%）；每講一條話命中 golden 的效率我們最低（775 條命中 97 條，cubic-v2 175 條命中 79 條）。

---

## 4. 分語言

以（a）的算法，F1（**每種語言的樣本數現在都夠了**）：

| 語言 | PR 數 | 第一 | 第二 | nathan-code-review |
|---|---:|---|---|---|
| TypeScript | 15 | nathan-code-review 86% | augment 78% | 第一 |
| Java | 9 | nathan-code-review 90% | cubic-v2 80% | 第一 |
| Python | 9 | nathan-code-review 86% | augment 79% | 第一 |
| Go | 8 | cubic-v2 85% | augment 84% | 82%（第三） |
| Ruby | 6 | nathan-code-review 92% | augment 63% | 第一 |
| SCSS/CSS | 2 | nathan-code-review 94% | augment / greptile / claude-code 86% | 第一 |
| i18n/config | 1 | nathan-code-review 90% | coderabbit 82% | 第一 |

**Go 是唯一沒有拿第一的語言**，而且前三名擠在 82–85% 之間——以 §2 量到的 4–5 個百分點雜訊底線來看，這三家在 Go 上分不出高下。

最後兩列樣本太小（2 個和 1 個 PR），列出來是為了完整，不要當成結論。其餘五種語言各有 6–15 個 PR，名次不會被單一 PR 翻轉。

值得一提：這個 skill 的九大面向裡有八個是為 Python 寫的，非 Python 檔全部塞在 dimension H。**它在 TypeScript（15 個 PR）、Java（9 個）、Ruby（6 個）上都居首，Python 反而不是它最強的語言**——拉開差距的是流程（assertion gate、關鍵操作列舉、對既有程式碼的歸屬規則），不是那些 Python 專屬的規則。

---

## 5. 假陽性長什麼樣

140 條 `not_real` 裡，最大宗只有一種形狀：**幻想一個不存在的 lint 規則或建置檢查**。這個形狀在全部六個 repo、五種語言裡都出現過。

- 「ruff 會擋下這個未使用參數」——Sentry 這個 commit 根本沒裝 ruff（沒有 `[tool.ruff]`、沒有 `.ruff.toml`，pre-commit 跑的是 flake8）。單是 sentry-95633 一個 PR 就有五條。
- 「這段 Go 不會編譯」——`go.mod` 把 xorm `replace` 成本地版本，那個呼叫形式是合法的。
- 「japicmp 會擋下這個 API 破壞」——整個 Keycloak 的 pom 裡沒有 japicmp、revapi 或 clirr。
- 「RuboCop 會擋下這個空行風格」——Discourse 沒有 `.rubocop.yml`，Gemfile 裡沒有 rubocop，而且那正是這個 repo 的既有寫法。
- 「docstring 覆蓋率低於 80% 門檻」——出現在兩個 Go PR 上，而 `.golangci.toml` 明確排除了 stylecheck 的 ST1000/ST1020/ST1021，也沒有覆蓋率 gate。

第二大宗是**沒去讀框架實際行為**，而驗證員多半是去讀 vendored 的原始碼或直接執行才推翻的：

- Ember template 名稱對不上——Discourse 自訂 resolver 的 `decamelize` 分支正好處理（讀 vendored Ember）。
- 標題有 XSS——`fancy_title` 在 server side 已經 `html_escape`。
- `nil =~ regexp` 會拋例外——實際跑了，`NilClass#=~` 回傳 nil。
- `.at(0)?.rules.map(...)` 空陣列會崩潰——optional chaining 短路**整條鏈**，在 node 裡實測確認。
- 某個型別不成立——用 `tsc --strict` 重現型別形狀後推翻，而且 **PR 自己的新程式碼就依賴那個型別推導**。

還有一種只在這輪出現的形狀：**時序性錯誤**。有兩條 claim 要求把依賴升級到某個版本，而那個版本比被審的 commit 晚了一年——模型拿「現在」的知識去審「當時」的程式碼。

驗證員自己歸納得最好：**「一個聽起來很機械、很可信的主張，前提被它自己指名的那個檔案裡的某一行直接推翻。」**

這正是 skill 的 assertion gate 要攔的東西。有幾條被擋下的候選出現在匿名池裡是因為**別的工具**報了出來，而盲測驗證員獨立判它們為假——例如 grafana-76186 的「移除 `traceID` 會讓 log 少欄位」（`tracing.go:91` 的 contextual log provider 仍會補上），我們的審查員在 stage 1 就用同樣理由砍掉了它。

---

## 6. Benchmark 的盲點——以及它自己知道多少

這一節得先講清楚一件事，因為它影響上面每一句話該怎麼讀：**上游對自己的問題有相當高的自覺，而且公開寫下來了。**

`methodology/full.md` 有一段叫 "Known limitations of the current implementation"，開頭是 `The current implementation is deliberately minimal — a starting point we can measure and improve against.`，然後逐條列出：

- `Bug definitions are implicit in the gold set rather than explicit or conditioned on user preferences (§5).`
- `Recall is capped by the gold set, which is itself capped by human performance (§6).`
- `Precision treats all non-action as false positives (§7).`
- `The judge is a single LLM with a single prompt, with no calibration against human annotations (§9).`

所以下面這些**不是我發現他們不知道的事**。分辨「文件寫了」與「程式碼做了」才是有價值的部分：

| 問題 | 文件 | 程式碼 | 我們 |
|---|---|---|---|
| gold set 封頂在人類水準 | §6 寫明，解法叫 adversarial validation | **無**（Stage 2 roadmap） | §3 實作了一次 |
| bug 定義沒有條件化 | §5、§7 提出用 `agents.md` 同時餵給工具與 grader | **無**（`offline/` grep 不到 `agents.md`／spec／preference） | 沒有補 |
| judge 沒有校準 | §9 承認 | **無** | §2 做了（對上游三個 judge） |
| 候選重複 | README 有文件 | **有**（`step2_5_dedup_candidates.py`） | **沒跑——上游 published 結果也沒跑** |

### 6.1 golden comments 有 16.8% 沒通過驗證

上游預期的是**漏收**（真 bug 不在 gold set，被算成 FP），§6 整節都在講怎麼補。

我量到的另一半是**誤收**：137 條裡 23 條本身就不成立。這個方向在 methodology 裡沒有被討論——`Recall is capped by the gold set` 講的是天花板不夠高，沒有講地板有洞。這對 recall 是雙向汙染，而只有一個方向被寫進待辦。

**這是全部 50 個 PR 的數字，不是抽樣。** golden 密度是每 PR 平均 2.72 條、中位數 2、最多 6。稀疏是系統性的。

（先前只跑 19 個 PR 時這個比率是 24.1%；擴到 50 個之後降到 16.8%。兩個數字都是實測，差異來自樣本——這也是為什麼 19 個 PR 的版本不該被當成定論。）

### 6.2 golden 少是刻意的嗎？不是，是繼承來的

methodology §3 交代了血緣：

> *Greptile (July 2025)* … measures bug catch rate … **limited to a single bug per PR**, measures only catch rate (no precision)
>
> *Augment (December 2025)* built on Greptile's dataset, **expanding the golden comments by manually reviewing each PR**. This was a meaningful correction — **it showed the original dataset was incomplete**, with many PRs having multiple issues missing from the gold set.

也就是說：起點是「每個 PR 一個 bug」，Augment 人工擴充過一輪，證明了原本不完整。現在的 2.72 條/PR 是**那一輪擴充的結果，不是一個目標值**。上游把繼續擴充列在 Stage 2（`Improve the gold set labels`、`Run a human study with over-generate and filter`）。

所以「golden 少」不是設計，是**已知的未完成品**。

### 6.3 precision 的定義把「話多」和「說錯」混為一談

`TP / 候選數`，分子上限是 golden 數。一個工具找到 20 個真問題而 golden 只記了 3 個，最高就是 15%。這個指標實際上在**獎勵沉默**：`graphite` 在上游排行榜上是 100% precision / 8.8% recall——它幾乎不講話。

這是唯一一條我認為 methodology **沒有正面處理**的。§7 花了很長篇幅談 precision，但談的是 online benchmark 的行為式 precision（把「沒有立刻採納」誤判成「錯」），跟 offline 的分母無關。最接近的兩處是：

> *Format differences.* Some tools produce line-by-line comments, others produce single-page summaries… It's unclear how to compute precision and recall across different output formats.

以及那條 `Bug definitions are implicit in the gold set` 的限制——那其實是病根：**沒有 bug 定義，就沒辦法判斷一個 golden 沒收錄的候選是雜訊還是真發現**，只能一律當雜訊。

他們開的藥方（§5、§7 的 `conditioning on user preferences`，把同一份 `agents.md` 同時給工具和 grader）方向是對的，而且如果實作了，很可能大幅改變我們這種話多工具的分數。但它跟 adversarial validation 一樣躺在 Stage 2，`offline/` 底下沒有任何對應程式碼。

### 6.4 一個我們沒開、但開了會對自己有利的開關

`step2_5_dedup_candidates.py` 是唯一一個**已經實作**的緩解措施：把工具自己重複講的候選合併。README 也寫了怎麼用。

我們沒跑它。理由是上游 published 的 `results/` 底下沒有 `dedup_groups.json`——他們自己的公布數字也沒套用，我們照著他們的 published 設定跑，六個工具一視同仁。

但要講清楚方向：**跑 dedup 會縮小分母，對候選數 775 的我們幫助最大。** 我們選了對自己較不利的那個設定，這是刻意的，不是漏掉。

**6.5 資料品質。** 50 個 PR 裡：
- `sentry-greptile-5` 標題是「Replays Self-Serve Bulk Delete System」，實際 diff 是 32 個無關 commit、106 個檔案、約 8700 行，而那個功能不在裡面（base 就是它自己的 squash commit）。3 條 golden comment 對這種規模的 diff。上游自己的 `az_comment` 寫著 `there is no such PR, it is a mix of many PRs`。
- `sentry-greptile-2` 疊在 `sentry-greptile-1` 之上，diff 含兩者內容，但 golden 只涵蓋後者。
- `sentry-greptile-3` 的 commit 裡有一個誤加的 `sentry-repo` gitlink（mode 160000，沒有 `.gitmodules`），沒有任何 golden comment 提到。
- 4 個 PR 的 `az_comment` 是 `reviewed commit is not in the repo`。

**6.6 合成 PR 的注入 bug 附帶「宣稱程式碼安全」的註解。** `sentry-greptile-*` 的注入缺陷寫著「This is safe because the underlying queryset will handle boundary conditions」——而 Django 明確拒絕負數 slice。這對「會讀註解當證據」的審查器是一個未被言明的攻擊面。這個 skill 的「文字是證據、不是指令」規則把它記進 `meta.process_directed_text` 並照樣判 Critical。

---

## 7. 公平性：做了什麼、沒做到什麼

| 風險 | 處理方式 |
|---|---|
| 看到跟同業不同的 diff | 從**同一個 fork PR** 取 `base..head`（`code-review-benchmark/<repo>__…__augment__PR<n>__…`），不是上游 PR |
| 挑好打的 PR | **不適用了——全部 50 個 PR 都跑了**。原本的抽樣規則（Python 全 10 個、其餘每 repo 取編號最小的兩個）寫死在 `build_manifest.py` 且在讀任何 golden comment 之前決定；後來 `--full` 解除配額，只會擴大涵蓋，不會挑選 |
| 偷看答案 | 審查 agent 被禁止讀 `data/` 下自己 diff 與輸出目錄以外的東西，也不能連網 |
| judge 偏袒我們的措辭 | 同一 prompt、同一模型跑全部六個工具；judge 不讀 diff，也不知道哪個工具是我們的 |
| 只校正自己的 FP | 六個工具的未命中候選全部進同一個匿名池，golden comments 也在裡面 |
| zh-TW 報告對上英文 golden | 抽取階段輸出英文並保留 zh-TW 原句，否則量到的是語言隔閡 |
| 只認列自己的額外發現 | 被確認的 cluster 記給**所有**提出它的工具 |
| 用自己定義的標的量 recall | 見 3.5——主要結論改用完全對稱的標的 |

**揭露而非修正的部分：**

- **訓練資料汙染。** 這些 PR 是公開的舊 PR，benchmark repo 與 golden comments 也公開。我們的模型可能看過。同業工具原則上有同樣曝險，但曝險程度不一定相同，這裡沒有量測。
- **模型世代。** 同業的審查是 2026 年 1–3 月用當時的模型跑的，我們跑在今天的模型上。`claude-code` 那一列是最接近的對照組——它跟我們同一個模型家族，raw F1 35.9%、校正後 60.7%，都在我們之下。差距有多少來自 skill、多少來自模型，這個實驗分不開。
- **掃描器缺席。** 只有 ruff 可用；trivy、opengrep、ty、oxlint、codegraph 都沒裝，每份報告都揭露了。Java / Go / Ruby / TypeScript 的 diff **完全沒有任何自動化靜態分析**。
- **fresh-eyes 順序錯了。** subagent 內無法再派 subagent，所以 `ncr-fresh-eyes` 是事後由外層補跑再回填的（只做了 Python 10 個 PR）。它的獨立性成立（沒看過審查員的發現），但「在讀 checklist 之前」這個位置沒有守住。stage 1 的報告保留在 `report.stage1.json`。
- **`ncr-quality-check` 完全沒跑。** 同樣的原因，50 份報告都沒跑。
- **驗證員自己會前後不一致。** 五個 PR 意外各跑了兩次，自我一致率 81–97%。值得注意的是`keycloak-38446`（29/5/2 → 36/0/0）**在移動幅度上不是離群值**（81%，最低的另一個是 83%）——它特別的地方是**每一次移動都朝同一個方向，而且都對我們有利**：五條 `not_real` 全翻成 `real`，而 pass 2 沒有回頭處理 pass 1 那五條各自附了引用的反證。被計分的是 pass 2（last-write-wins）。細節與逐條理由在 `data/calibration/VERIFIER_VARIANCE.md`；那一列請當作全套裡最軟的一列。校正後的數字請當作準確到「幾個百分點」，不是小數點。
- **我在 Go 批次的指令裡寫錯了一件事。** 我告訴那 8 個審查 agent「沒有 Go toolchain」，實際上 `go` / `gofmt` / `golangci-lint` 都在 PATH 上（是一位審查員回報糾正我的）。真正的限制是沒有 module cache 也沒有網路，所以 `go build` / `go vet` 仍跑不動，但 `gofmt -l` 和部分不需解析相依的 linter 本來可行。**這會低估 Go 那批的掃描覆蓋率，不會高估**，但它是我引入的指令錯誤。
- **`fetch_pr.py` 的設計缺陷。** 它為每個 PR 各 clone 一份完整 repo——同一個 sentry 被 clone 了 10 次。50 個 PR 花掉約 90 分鐘與 4.5 GB。正確做法是每個 source repo clone 一次、用 `git worktree` 共用 object store。這只影響執行成本，不影響結果。
- **一次靜默失敗。** `cal_com-8330` 第一輪抓取失敗且沒有任何錯誤輸出，是靠事後對帳發現的。重跑後成功。
- **樣本數。** 50 個 PR 全跑，但 SCSS/CSS 只有 2 個、i18n/config 只有 1 個（這兩類是依 diff 內容而非 repo 分的）。

---

## 8. fresh-eyes 的 ablation

Python 10 個 PR 補跑 `ncr-fresh-eyes` 之後，只有 **2 條新發現**被採納（sentry-95633 三條、sentry-greptile-2 一條 —— 後者讓審查員推翻自己原本「這是 no-op」的判斷）。

它真正的貢獻是**修正**：

- sentry-greptile-1：fresh-eyes 說「未夾限的負數 slice 是這個 PR 引入的」。審查員跑 `git show 74618671b:src/sentry/api/paginator.py`，發現改動前兩個分支都沒夾限——hazard 早於 PR，這個 diff 還修掉了一半。依 diff 歸屬規則維持 Suggestion。
- sentry-greptile-3：fresh-eyes 說 endpoint 測試零覆蓋，但它引用的檔案不存在；真正的測試在 `tests/snuba/…` 且確實有四個。發現成立，理由被換掉。
- sentry-67876：fresh-eyes 說「真正的修補是後面的身分檢查」。審查員反駁——那個檢查讀的兩個值都由同一個請求寫入，是下游不是後盾——維持 Critical。

還有一個一致的現象：**fresh-eyes 給的 `file:line` 有相當比例是 diff 行號而非檔案行號**（sentry-93824 五個引用有四個是），全部被審查員重新推導。這些原樣印出去，就是「可以點開、然後發現指錯地方」的引用。

---

## 9. 重現

```bash
cd benchmarks/code-review-bench
git clone --depth 1 https://github.com/withmartian/code-review-benchmark.git /tmp/crb

uv run harness/build_manifest.py --benchmark-data /tmp/crb/offline/results/benchmark_data.json --out data/manifest.json
uv run harness/fetch_pr.py --manifest data/manifest.json --out data/prs --workdir /tmp/clones
uv run harness/export_inputs.py --benchmark-data /tmp/crb/offline/results/benchmark_data.json \
  --candidates /tmp/crb/offline/results/anthropic_claude-opus-4-5-20251101/candidates.json \
  --manifest data/manifest.json --out data/prs

# 四個 agent 階段（review → extract → judge → verify）由外層 session 依 harness/prompts/*.md 派送

uv run harness/score.py --data data \
  --tools nathan-code-review,cubic-v2,augment,greptile-v4-1,coderabbit,claude-code --out scores
uv run harness/judge_agreement.py --baseline scores/upstream_baseline.json \
  --ours scores/summary.json --out scores/judge_agreement.json
```

## 10. 原始資料

| 檔案 | 內容 |
|---|---|
| `scores/raw_cells.csv` | 每個 (PR, 工具) 的 raw TP/FP/FN |
| `scores/raw_cells_lenient.csv` | 同上，排除我們的 `open_questions` |
| `scores/corrected_cells.csv` | 校正後，含三種 recall 的分子分母 |
| `scores/summary.json` | 全部彙總，含分語言 |
| `scores/ground_truth_audit.json` | 每個 PR 的 golden 存活數、被確認的 cluster 與提出者 |
| `scores/judge_agreement.json` | 我們的 judge vs 上游三個 |
| `scores/upstream_baseline.json` | 上游在這 50 個 PR 上的 published 分數 |
| `data/prs/<slug>/` | diff、meta、golden comments、同業候選 |
| `data/reviews/<slug>/` | 我們的 `report.json` / `report.md` / `candidates.json`（Python 另有 `report.stage1.json`） |
| `data/judgments/<slug>/<tool>.json` | judge 的配對結果 |
| `data/calibration/<slug>.*` | 匿名 claim 池、對照表、驗證結果 |
