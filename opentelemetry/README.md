# OpenTelemetry 觀測

Claude Code 審查場次的觀測整組：收下 trace、把一場審查攤開成「每個角色花了多少
時間、多少 token、多少錢、快取吃得好不好」。它是選配——沒起 Jaeger，dev container
照常運作，只是這一場沒有紀錄。

```
jaeger-compose.yaml    收集端：all-in-one Jaeger v2，OTLP 收 traces，badger 落地
jaeger-config.yaml     Jaeger 設定（trace 保存 30 天；期限烙在寫入當下，事後改不了）
telemetry-report.py    時間與 token：從 Jaeger 撈，按 subagent 角色攤開（終端表格）
cost-report.py         錢：從 session transcript 的 usage 算，逐請求精確
session-report.py      場次報表頁：結論＋甘特時間軸＋每角色合表，輸出單檔 HTML
```

## 啟動收集端

```bash
docker network create gitlab-proxy 2>/dev/null || true   # 沒跑 Day 18 代理的人先建網
docker compose -f opentelemetry/jaeger-compose.yaml up -d
```

UI 在 <http://localhost:16686>（只綁 loopback），搜 service = `claude-code`。
之後照常跑 `dev-container/run-ncr-dev-container.sh`——wrapper 偵測到 jaeger 在跑
就自動開錄，並把 `skill.version`（自動抓）與 `experiment` 黏上該場所有 span：

```bash
NCR_EXPERIMENT=before-xxx dev-container/run-ncr-dev-container.sh   # 改 skill 之前
NCR_EXPERIMENT=after-xxx  dev-container/run-ncr-dev-container.sh   # 改完之後
```

沒有對照組就量不出改進，`experiment` 這層就是對照組的錨。

## 輸出報表

```bash
# 場次報表頁（結論、甘特時間軸、每角色時間×token×成本×快取命中）
uv run opentelemetry/session-report.py <session-id 前綴> --open

# 終端快查：時間與 token
uv run opentelemetry/telemetry-report.py --session <id 前綴>

# 終端快查：每角色精確成本
uv run opentelemetry/cost-report.py                    # 最新一場
uv run opentelemetry/cost-report.py <session.jsonl>    # 指定場次
```

## 資料源紀律（三源各取所長，不互相冒充）

| 要什麼 | 從哪來 | 為什麼 |
|---|---|---|
| 結論、MR 標題、掃描狀態 | report.json（archive） | 審查的正式產出物；拿不到就顯示「未封存」，不隱藏 |
| 時間、甘特 | Jaeger trace | span 有精確起迄；角色歸因走 span 父子鏈（派遣 span 自帶的 agent_id 實測會指錯人） |
| token、成本、快取命中率 | session transcript | 逐請求 usage 才是帳單事實；trace 上的成本只能估 |

成本算法已與 ccusage 對同一 session 對帳：四項 token 逐位一致、總金額一致到小數
第 8 位（2026-08-06，Claude Code 2.1.222）。牌價快照在 `cost-report.py` 開頭，
模型不在表上就只報 token、金額標「無牌價」，不猜。

快取命中率 = cache 讀 ÷（輸入＋cache 讀＋cache 寫）。

## 測試

```bash
uv run pytest tests/test_telemetry_scripts.py
```
