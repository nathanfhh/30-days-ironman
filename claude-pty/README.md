# agent-tty（claude-pty）

多人共用的網頁終端控制平面：每個人登入後開自己的 session（一場 = 一顆 container），
在瀏覽器裡直接操作容器內的 Claude Code。Flask 控制平面 + nginx 單一入口 + on-demand
ttyd；SQLite 是唯一的狀態仲裁者。

> 部署方式、安全邊界與完整的設計決策（ADR）另有專文，陸續補齊。本檔先寫使用者
> 選擇時最需要知道的一件事：終端程式有兩顆，它們不一樣。

## 終端程式：兩顆 ttyd，差異不是快慢

每個終端由一顆 ttyd 程序服務。這套系統帶兩顆、由每個使用者自選（帳號選單 →
設定 → 終端程式；只影響之後開的終端，正在跑的不會被換掉）：

| | C 版（上游） | Rust 版（fork） |
|---|---|---|
| 來源 | [tsl0922/ttyd](https://github.com/tsl0922/ttyd) release 1.7.7，binary 以 SHA-256 釘死 | [nathanfhh/ttyd](https://github.com/nathanfhh/ttyd) 的 Rust 重寫，釘 commit（見 `deploy/Dockerfile` 的 `TTYD_RUST_REF`） |
| 網頁標題 | **只有畫面遮蔽**（`titleFixed`，client 選項）：瀏覽器被要求顯示別的字，但**真正的標題——完整命令列加容器主機名——在那之前就已經送給每一個連上的 client** | **伺服器端換掉**（`--title`）：宣告出去的標題只剩固定字樣加該場編號，**命令列一個字都不上線** |
| 第二層授權 | 無（只有 nginx 的 `auth_request` 那一層） | `--auth-url`：ttyd 自己在放行每個請求前，再問一次控制平面「這個人能不能看這一場」（與 nginx 那層是縱深，不是重複） |

**標題那一列是重點。** 分頁標題會進瀏覽紀錄、工作階段同步、截圖。C 版的 `titleFixed`
做到的是「畫面上看不到」，不是「沒有送出去」——選 C 版，這個洩漏面就存在，只是被
蓋住。選 binary 的人應該知道自己在選什麼。

兩件容易想錯的事，寫在這裡省得下一個人重試：

- **C 版對它沒有的旗標是靜默忽略，不是拒絕啟動。** 把 `--title` 塞給 C 版不會炸，
  終端照常開，只是那道保護根本沒生效、也沒有任何錯誤。所以「哪顆 binary 拿到哪些
  旗標」由控制平面的參數策略保證（`server/views.py` 的 `_TTYD_EXTRAS`），不是靠
  「塞錯會壞」防呆。
- **差異在 build 時就被釘死。** `deploy/Dockerfile` 在編出 Rust 版之後、搬進 image
  之後，各對真的 binary 斷言一次：Rust 版的 `--help` 必須列出
  `--title` / `--auth-url` / `--auth-cache-ttl`（缺任一支 build 直接失敗），
  C 版必須沒有 `--title`。fork 哪天改了旗標，會在 build 當場現形，不會等到
  執行時靜默少一層。

### Build 時間

Rust 版沒有預編 binary 可下載：image 的第一階段用 `cargo build --release --locked`
從釘死的 commit 現編。**第一次 build 會久**（Rust 編譯，數分鐘起跳）；之後只要
`TTYD_RUST_REF` 不變，這一層會命中 Docker cache，幾乎不花時間。

## 測試

```bash
tests/run-all.sh          # 快速組（不需要 docker）
tests/run-all.sh --all    # 全部（需要 docker；ttyd 在 PATH 上則含真終端測試）
```
