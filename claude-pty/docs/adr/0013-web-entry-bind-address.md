# ADR 0013：網頁入口預設只綁 loopback，對外是部署層的明確選擇

- 狀態：已接受；已實作

## 背景

一次改動把 nginx 的發布位址從 `127.0.0.1:8080:8080` 改成 `8080:8080`，夾在一個不相干的
feature commit 裡、commit message 沒提到。結果是站台從「只有本機連得到」變成「同網段
任何一台機器都連得到」，而好幾份文件、連同一段 CSRF 安全推理，都還寫著 loopback。

問題不在「能不能對外」——那是部署者的自由。問題在於**這個決定當時沒有留下任何痕跡**：
一行 YAML 的改動悄悄作廢了數份文件的前提。

## 決策

**發布位址由 `CLAUDE_PTY_BIND_ADDR` 決定，預設 `127.0.0.1`。**

```yaml
ports:
  - "${CLAUDE_PTY_BIND_ADDR:-127.0.0.1}:8080:8080"
```

要對外就在 `.env` 明確寫 `CLAUDE_PTY_BIND_ADDR=0.0.0.0`。三個性質：

1. **預設是安全的那一端**（同 [ADR 0011](0011-optional-ssh-agent-forwarding.md) 的形狀：
   爆炸半徑大的能力一律 opt-in）。
2. **開啟這件事留得下痕跡**：它住在 `.env`（不進版控但看得到）而不是一行被改掉的 YAML；
   `.env.example` 把代價逐條寫在旁邊。
3. **不需要改 compose 就能切換**——原本要對外得改版控裡的檔案，那正是「順手改一下」會
   發生的原因。

## 對外時的代價

開這個開關**不只是多開一個 port**，它把下列每一件事的暴露面從「本機」放大到「網段」：

- **傳輸是明文 HTTP**（`CLAUDE_PTY_COOKIE_SECURE` 預設 0）：登入密碼、session cookie
  全部明文過網路。要對外就得在前面擺 TLS 終結並設 `COOKIE_SECURE=1`。
- **仍不是租戶隔離**：狀態層雖已 per-user（[ADR 0014](0014-per-user-agent-state.md)），
  但它能在 host 的 dockerd 上開特權容器（[ADR 0009](0009-containerized-deployment-docker-socket.md)
  已接受的風險）；對外開放仍只適合互信操作者。
- **SSH agent 轉發若開著**（[ADR 0011](0011-optional-ssh-agent-forwarding.md)），還多一把
  能以你身分認證任何主機的 key。
- **CSRF 的推理要重看**：現行防線的一部分建立在「site 是 localhost」上，換成實際主機名
  之後不再自動成立。

正確用法是「整個網段可信、或前面已經有 TLS 與存取控制」，不是「我想從筆電連連看」。
後者請用 SSH port forwarding：`ssh -N -L 8080:127.0.0.1:8080 <host>`。

## 後果

- 預設部署與文件重新一致，不必再靠人記得同步。
- 想對外的人得寫一行 `.env`，那一行旁邊就是代價清單。
- **沒有解決**明文 HTTP 本身：TLS 終結仍要部署者自己擺，這個開關只是讓「我知道我在做
  什麼」變成一個明確的動作。
