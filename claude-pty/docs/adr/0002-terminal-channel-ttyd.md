# ADR 0002：終端通道——ttyd 包 `docker attach`，並改掉 detach-keys 陷阱

- 狀態：已接受

## 背景

[ADR 0001](0001-dockerd-as-session-daemon.md) 確立 dockerd 為 session daemon 後，瀏覽器
要接上同一個 session，需要一個終端通道，而且不自己實作終端模擬器——用現成套件。

## 決策

**瀏覽器端用 ttyd 包 `docker attach`：**

```
ttyd -W docker attach --detach-keys=ctrl-x,ctrl-x <container>
```

- ttyd 只當「xterm.js ⇄ WebSocket ⇄ 子程序」的薄轉接層；其子程序（`docker attach`
  CLI）斷線死掉無妨——持久的東西在 dockerd 手上，ttyd 每條連線重新 attach 即可。
- **`--detach-keys` 必改**：docker CLI 預設 detach 序列 `ctrl-p,ctrl-q` 會把單獨的
  `Ctrl+P` 扣住、直到下一個按鍵才放行（spike 實測），而 `Ctrl+P` 是歷史導航常用鍵，
  不改則互動體感直接壞掉。detach-keys 是 **docker CLI 客戶端行為，attach 底層不存在
  此攔截**（spike 實測 `0x10` 直達）。

## 後果

- 前端零自製：終端渲染、IME、貼上都是 xterm.js（ttyd 內建）的既有能力。
- ttyd 本身無 session 管理概念——多 session 時每個 container 一個 ttyd 埠，切分與路由
  的方式見 [ADR 0005](0005-edge-auth-and-web-exposure.md)、[ADR 0008](0008-persistent-registry-and-on-demand-ttyd.md)。
- **resize 權責**：attach 的 resize 為 last-writer-wins（spike 實測 CLI 連上即以自己的
  尺寸覆蓋）。多客戶端同時在線時以瀏覽器端為權威。
- ttyd 是可拋棄的貼皮：container `docker rm` 後其 `docker attach` 子程序自然退出，ttyd
  隨之收掉；session 真身始終在 dockerd。這個「一次觀看、隨開隨收」的性質在
  [ADR 0008](0008-persistent-registry-and-on-demand-ttyd.md) 被推到 on-demand。
