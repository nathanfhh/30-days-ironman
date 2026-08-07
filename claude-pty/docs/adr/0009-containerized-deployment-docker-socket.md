# ADR 0009：容器化部署——控制平面掛 host docker socket（DooD），不用 DinD

- 狀態：已接受

## 背景

部署形態是「全部跑在 Docker 裡」——控制平面（Flask + reconciler）本身也是容器。它必須
能建立 / 終止 / attach session container，因此需要取得 Docker daemon 的控制權。兩種做法：

- **DooD（Docker outside of Docker）**：把 host 的 `/var/run/docker.sock` 掛進控制平面
  容器，操作 host daemon；session container 是控制平面的**兄弟**而非子容器。
- **DinD（Docker in Docker）**：控制平面容器內另跑一個 daemon（需 `--privileged`），
  session container 建在內層 daemon。

## 決策

**採 DooD：掛 host docker socket 給控制平面容器。**

### 理由

1. **DinD 會讓 [ADR 0006](0006-session-runtime-profile.md) 的 profile 能力直接失效**。
   restricted 需要把 session 接上 session network、telemetry 需要連到 Jaeger 容器——
   兩者都是 host daemon 的資源，內層 daemon 看不到。
2. **DinD 的隔離感是假的**。它需要 `--privileged`，而 privileged 容器逃逸到 host root
   是已知且容易的。兩者被攻破的終點相近。
3. **bind mount 的路徑由 daemon 解讀**。系統依賴多個 host 路徑（設定目錄、repo 的
   `entrypoint.sh`、錄製 addon）。DooD 直接可用；DinD 下這些在內層不存在，得多搬一層。
4. **映像檔不必重複**。DinD 有自己的 image store，等於再存一份。

### 明確接受的風險

**掛 docker socket ≈ 給該容器 host root**（可建立 privileged 容器掛 `/`）。這無法藉由
容器化消除——**控制平面本來就必須擁有建立容器的權力，它天生是特權元件**。

> 容器化買到的是部署一致性，不是安全邊界。

不可因為「它在容器裡」而產生虛假的安全感。降低風險的手段依實際有效性排序：

1. **rootless Docker**——唯一實質改變 blast radius 者（socket 權力降為該使用者的容器，
   而非 host root）。**尚未做、會去做。**
2. 控制平面容器保持最小、不在其中執行任何不受信任的程式碼；對外一律經 nginx + authn/authz。
   ——這是**紀律，不是機制**：它降低出錯機率，擋不住已經出的錯。
3. socket proxy 僅能依 API endpoint 類別過濾，**擋不住「建立特權容器」**——而這套系統
   本來就要建容器、掛目錄，proxy 攔不住在放行類別內的濫用。屬 defence-in-depth，不是
   邊界，不可高估。

### 不變的紅線

**session container 一律不掛 docker socket**（[ADR 0004](0004-flask-control-plane.md) 既有
規定）。裡面跑的是會執行不受信任程式碼的 CLI；給它 socket 等同直接放棄整個隔離。

## 實作影響

1. **路徑必須解耦**。控制平面進容器後，`expanduser("~")` / `__file__` 會解析成**容器內**
   路徑，但 daemon 拿去 **host** 上找 → 掛載失敗或靜默建出空目錄。「傳給 daemon 的 host
   路徑」必須由 env 明確給定，與「控制平面自己看到的路徑」分開。存在性檢查（`isfile/isdir`
   後才決定掛不掛）讀的是控制平面自己的檔案系統，故相關路徑仍需 `:ro` 掛進控制平面一份。
2. **ttyd 改在控制平面容器內執行**，綁容器內 `0.0.0.0`，nginx 以容器名連入。這些 port
   完全不發布到 host，只存在於內部網路——[ADR 0005](0005-edge-auth-and-web-exposure.md)
   「ttyd 不對外曝露」的性質更徹底。
3. **nginx 的 upstream** 由 loopback 改為控制平面容器名。

### 容器以非 root 執行

`USER app` + compose 的 `group_add`。這**不改變**「掛 socket ≈ host root」的結論——能建
特權容器就能逃逸——只是縱深防禦。兩個實測出來的必要條件：

- **docker socket 的群組**：socket 是 `srw-rw---- root:root`，非 root 要讀它就得屬於該
  gid（compose 以 `group_add` 處理）。
- **`app` 的 uid 是 build arg（`APP_UID`）不是常數**：掛進來的 host 檔案帶著 host 的 uid，
  對不上就讀不到。Linux 上 `APP_UID` 要設成 `id -u`；改了要重新 build 才生效。

## 後果

- 部署一致（全 Docker），沿用 host daemon 既有的 network / image 生態，profile 能力無需
  改寫。
- 控制平面容器等同受信任的特權元件，其安全性依賴「不在其中跑不受信任程式碼」＋前置
  nginx 與 auth，而非容器邊界本身。
