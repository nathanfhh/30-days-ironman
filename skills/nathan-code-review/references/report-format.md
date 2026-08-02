# The report

## Four rules

The report gets pasted onto the merge request where everyone can read it. These
are the conditions for it being fit to publish.

**1. The conclusion is mechanical.** Any live Critical → `Request Changes`, never
`Approved`. Only Suggestions → `Approved with Comments`. Only Nits, or nothing →
`Approved`. There is deliberately no room between "a Critical was found" and
"here is the verdict"; `report_model.py` rejects a report where the two disagree.
`open_questions` carry no severity and therefore do not affect the conclusion —
that is the point of them.

**2. Self-contained.** It may cite only things a reader can reach: paths inside
the repository (`app/api/account.py:34`), the MR, its commits. Never a path on
the review machine — `/tmp/ncr/...` in a published report is meaningless to
everyone reading it. The report JSON may hold local paths internally; only the
rendered Markdown is bound by this.

**3. About the code, not the person.** Every sentence has to survive being read
in public. Ground each one in code evidence and point at the code. "這段 code",
"這個 endpoint" — not "你".

**4. A finding without a way forward is not finished.** Alongside what is wrong,
each finding carries a repair direction or sample code. This holds for Nits too:
if it is not worth suggesting a direction, it was not worth writing.

## Tone

Constructive and matter-of-fact. The author is going to act on this, so it is
written to be acted on — precise about what and where, generous about why.

## The JSON

`scripts/report_model.py` is the authoritative definition; read it when you need
exact field names or the validation rules. The shape:

| Block | Holds |
|---|---|
| `meta` | skill version, timestamp, round, mode, target, PHI trigger, blind-pass flag |
| `mr` | project path, iid, title, description, branches, web_url, attachments — `null` in local mode |
| `intent_check` | the three Phase 0.5 answers, each `ok`/`doubt` with a note |
| `scans[]` | per tool: status, exit code, artifact path, counts, reason if not run |
| `dimensions` | A–I, each `pass`/`fail`/`na` with a note — the nine-cell grid |
| `findings[]` | id, dimension, severity, status, title, evidence, rationale, fix, source, security payload, accepted_risk |
| `open_questions[]` | unverified concerns: question, context, what would settle it. No severity |
| `rereview` | the Q1 and Q2 answers. Required from round 2 onward |
| `conclusion` | derived, never chosen |
| `publication` | discussion_id, note_id, created_at, url — written back after publishing |
| `pushback[]` | appended when an author disputes a finding; never edits history |

Validate after every write:

```bash
uv run scripts/report_model.py validate <report.json>
```

The validator enforces the invariants that matter under pressure: the conclusion
matches the findings, every finding has a `fix`, a Critical security finding
carries POC / blast radius / treatment, every dimension has a verdict, a skipped
scan states why, and a PHI trigger cites evidence.

## The published Markdown

```bash
uv run scripts/render_report.py <report.json> --out <report.md>
```

Layout — decisions and blockers first, the rest folded away so a long report
stays readable without dropping anything:

```markdown
## 審查結論：Request Changes
> Critical 1 · Suggestion 3 · Nit 2 · 未驗證提問 2
> nathan-code-review 2026.08.03.01 · 第 2 次審查

### 總評

| A 風格 | B 簡潔 | C 安全 |
|:--:|:--:|:--:|
| ✅ | ✅ | ❌ |

| D API 慣例 | E 架構 | F 資料庫 |
|:--:|:--:|:--:|
| ✅ | ❌ | ✅ |

| G 測試 | H 非 Python 檔 | I 回溯分析 |
|:--:|:--:|:--:|
| ❌ | — | ✅ |

### 意圖確認
（only when something was doubted）

### 掃描執行狀況
（every tool, including what was skipped and why）

### Critical

#### F-001 未參數化的 SQL — `app/api/account.py:34`
問題 / POC / blast radius / 風險處置 / 修復方向

<details><summary>Suggestion（3）</summary>…</details>
<details><summary>Nit（2）</summary>…</details>
<details><summary>未驗證提問（2）</summary>…</details>
<details><summary>已解決（1）</summary>…</details>
```

`—` marks a dimension that did not apply, and the note says why.

On a re-review, the answers to Q1 and Q2 (see `re-review.md`) appear in their own
section before the findings.

Markdown is rendered from `assets/report_template.md` for the fixed frame, with
the repeating blocks assembled in `render_report.py`.

## Publishing

1. Render the Markdown.
2. Show the user, ask whether to publish. Wait.
3. On approval, post it as a discussion (`gitlab-api.md`).
4. Write `discussion_id`, `note_id`, `created_at`, and `url` back into
   `publication`, and validate again.
5. Give the user the direct URL to click.

If the user declines, the run ends here. The archived JSON is still the record
that makes the next round a re-review.
