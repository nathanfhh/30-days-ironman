## 審查結論：Request Changes

> Critical 2 · Suggestion 7 · Nit 5 · 未驗證提問 2
> nathan-code-review 2026.08.02.05 · 第 1 次審查

### 總評

| A 風格 | B 簡潔 | C 安全 |
|:--:|:--:|:--:|
| ❌ | ❌ | ❌ |

| D API 慣例 | E 架構 | F 資料取用與資料庫 |
|:--:|:--:|:--:|
| — | ❌ | ❌ |

| G 測試 | H 非 Python 檔 | I 回溯分析 |
|:--:|:--:|:--:|
| ❌ | — | ❌ |

- **A 風格**（未通過）：error() 與 get_document_origin() 的參數沒有型別標註，與同檔案其他函式（build_repository_query）不一致；_(error_short) 把 gettext 從字面字串移到變數上，訊息無法再被 makemessages 抽取。詳見 F-008、F-011。
- **B 簡潔**（未通過）：三段重複的 render_to_response 收斂成 error()，這部分是明確的改善。扣分在測試側：同一段 urlencode({code, state}) 在 test_integration.py 重複五次、state 值硬編五次，且多處 resp = self.client.get(...) 指派後立刻被覆寫。詳見 F-012。
- **C 安全**（未通過）：本 MR 的核心目的是身分驗證，但 OAuth state 是一個全域固定常數（F-001，Critical）；身分檢查也只覆蓋 Integration 已存在的路徑，首次安裝路徑完全未經檢查（F-003）；比對用的是可變的 login 而非不可變的 sender id（F-009）；authorize URL 以字串串接組成、redirect_uri 未做百分比編碼（F-005）。
- **D API 慣例**（不適用）：本次沒有新增或修改任何 REST API endpoint、URL 樣式或序列化 schema。變更集中在 Django pipeline view 的流程控制，沿用既有的 sentry-extension-setup / sentry-organization-integrations-setup 路由。
- **E 架構**（未通過）：OAuthLoginView 是把 sentry.identity.oauth2 的 OAuth2LoginView + OAuth2CallbackView 重寫了一次（程式碼註解自己寫著 "similar to OAuth2CallbackView..."），過程中丟掉了原本就有的三件事：例外處理（F-004）、urlencode（F-005）、customer domain 的 subdomain 還原（F-006）。
- **F 資料取用與資料庫**（未通過）：integration.metadata["sender"]["login"] 直接下標，而同 repo 的 installation.py:44 對同一種資料狀態明確做了 "sender" not in metadata 的防護，代表這個 key 確實可能不存在（F-002，Critical）。另外同一個 Integration 在同一個 request 內被查了兩次（F-010）。
- **G 測試**（未通過）：test_installation_not_found 因為帶了一個不等於 pipeline.signature 的 state，在 state 比對就被擋下，根本走不到 404 的 installation 查詢；原本的斷言被換成 "Invalid installation request."，測試名稱宣稱覆蓋的路徑實際上已無覆蓋（F-007）。新增的 test_github_user_mismatch 本身是好的：它斷言了實際 response 內容而不只是狀態碼。
- **H 非 Python 檔**（不適用）：diff 只含三個 .py 檔（src/sentry/integrations/github/integration.py、src/sentry/web/frontend/pipeline_advancer.py、tests/sentry/integrations/github/test_integration.py），沒有非 Python 檔案。
- **I 回溯分析**（未通過）：已確認 FORWARD_INSTALL_FOR 全 repo 無其他引用、GitHubEnterpriseIntegrationProvider 自行覆寫 get_pipeline_views（github_enterprise/integration.py:328）因此不受本次 pipeline 變更影響，這兩項都沒有回溯風險。但 get_pipeline_views 多一個 view 會改變 Pipeline.signature，使部署當下所有進行中的安裝 session 失效（F-013）。

### 掃描執行狀況

| 工具 | 狀態 | 說明 |
|---|---|---|
| ruff | 已執行 | in_diff 1、outside_diff 250 |
| ty | 略過 | ty 未安裝（不在 PATH 上），本次未執行 Python 型別檢查；型別相關的判讀改為人工比對 pyproject.toml 的 mypy 設定與周邊程式碼。 |
| oxlint | 略過 | oxlint 未安裝（不在 PATH 上）。本次 diff 沒有 JavaScript/TypeScript 檔案，即使安裝也不會有可掃描的對象。 |
| trivy | 略過 | trivy 未安裝（不在 PATH 上），本次未執行相依套件弱點、設定錯誤與憑證外洩掃描。本 diff 未新增相依套件，但「是否有憑證被寫進程式碼」這一項沒有工具背書，僅由人工閱讀確認（client_secret 取自 options，未硬編）。 |
| opengrep | 略過 | opengrep 未安裝（不在 PATH 上），本次未執行 SAST 掃描。OAuth/CSRF 類規則因此沒有機器覆蓋，本報告 C 面向的結論全部來自人工追蹤。 |
| codegraph | 略過 | codegraph 未安裝（不在 PATH 上），無法建立符號圖。呼叫者列舉與影響面分析改用 grep 逐一比對（例如 FORWARD_INSTALL_FOR 的引用點、metadata["sender"] 的讀取點、GitHubIntegrationProvider 的子類別）。 |
| ncr-fresh-eyes | 已執行 | 流程偏差，據實揭露：本次審查的執行環境沒有可派送 subagent 的工具，fresh eyes 無法從審查者的 context 內派出，改由外部協調者派送，且抵達時間晚於 Phase 3（審查者已讀過 review-dimensions.md）。因此它「先於清單、未被框住」的設計目的只達成了一半：它自己沒有清單，但它的觀察是在既有findings 已成形之後才進入本報告。七項觀察全部已被人工複驗，沒有任何一項帶來新的 finding；其中三項的 file:line 引用有偏移（見 F-001、F-012 的說明），本報告採用複驗後的行號。 · observations 7、adopted_as_new_finding 0、already_covered 7 |
| ncr-quality-check | 略過 | 無法從審查者 context 派送 subagent，Phase 4 的 ncr-quality-check 未執行。報告僅通過 report_model.py 的機械驗證，沒有第二個獨立視角覆核用詞與嚴重度校準。所有 file:line 引用已於 fresh eyes 回報後逐一以 sed 對照原始檔複驗一次。 |

### Critical

#### F-001 OAuth state 用的是全域固定常數，state 比對等於沒有比對 — `src/sentry/integrations/github/integration.py:402`

面向 C 安全 · Critical

**問題**：state = pipeline.signature，而 Pipeline.signature 在 pipeline/base.py:132-133 的定義是 md5_text(*pipe_ids).hexdigest()，其中 pipe_ids 是 pipeline view 的完整類別名稱清單。對 GitHub 來說就是 md5("sentry.integrations.github.integration.OAuthLoginView" + "sentry.integrations.github.integration.GitHubInstallation")，值是 9cae5e88803f35ed7970fc131e6e65d3——對所有使用者、所有安裝、所有時間點都一樣，而且因為 Sentry 是開源的，任何人都能自己算出來。測試本身把這個值硬編在 test_integration.py:235，就是它是常數的直接證據。

OAuth 的 state 存在的唯一理由，是把 callback 綁回「發起這次流程的那個 session」。用一個公開可推導的常數，這個綁定完全不存在，第 412 行的 request.GET.get("state") != pipeline.signature 只是一個永遠會被攻擊者滿足的形式檢查。

已找過反證：PipelineAdvancerView 確實會用 session 取回 pipeline（pipeline_advancer.py:32-35）並要求 pipeline.is_valid()，所以流程本身是綁 session 的。但這個 view 明確設定 auth_required = False 與 csrf_protect = False（pipeline_advancer.py:25-27），跨站 GET 進得來，而這正是 state nonce 要擋的東西。同 repo 的 OAuth2LoginView 用 secrets.token_hex() 並 pipeline.bind_state("state", state)（identity/oauth2.py:245、252），OAuth2CallbackView 再以 pipeline.fetch_state("state") 比對（identity/oauth2.py:325）——本檔案偏離了 Sentry 自己既有且正確的作法。

關於「這是否只是 defense-in-depth、真正的防線是後面的身分比對」這個反論：不成立，因為後面那道比對讀的正是這條路徑寫進去的值。OAuthLoginView 在第 397-399 行先從 query string 綁 installation_id、第 438 行再把 github_authenticated_user 寫進 pipeline state，兩者都發生在同一個可被跨站 GET 觸發的請求裡；GitHubInstallation 第 501-504 行的比對只是把這兩個值拿來對照。換句話說，攻擊者若能決定這個請求的內容，就同時決定了比對的兩邊，身分比對不是 state 的後備，而是 state 的下游。

**證據**：
- `src/sentry/integrations/github/integration.py:402`
- `src/sentry/integrations/github/integration.py:412`
- `src/sentry/pipeline/base.py:133`
- `src/sentry/identity/oauth2.py:245`
- `src/sentry/web/frontend/organization_integration_setup.py:21`
- `src/sentry/web/frontend/pipeline_advancer.py:27`
- `tests/sentry/integrations/github/test_integration.py:235`

**POC**：

```
整條鏈都是 GET，且沿路兩個 view 都關掉 CSRF：
OrganizationIntegrationSetupView.csrf_protect = False（src/sentry/web/frontend/organization_integration_setup.py:21，第 51 行呼叫 pipeline.initialize()）；PipelineAdvancerView.auth_required = False、csrf_protect = False（src/sentry/web/frontend/pipeline_advancer.py:25、27）。

1. 算出 state（離線即可，Sentry 是開源的）：
   python -c "import hashlib;m=hashlib.md5();[m.update(s.encode()) for s in ('sentry.integrations.github.integration.OAuthLoginView','sentry.integrations.github.integration.GitHubInstallation')];print(m.hexdigest())"
   → 9cae5e88803f35ed7970fc131e6e65d3
2. 攻擊者在自己的 GitHub 帳號安裝這個 App，記下 installation_id（webhook 會把 metadata.sender.login 寫成攻擊者），並在自己的瀏覽器走一次 authorize、攔下未使用的 code。
3. 誘導已登入且具 org:integrations scope 的受害者載入一個攻擊者頁面，該頁面依序發兩個跨站 GET：
   (a) https://sentry.io/organizations/VICTIM_ORG_SLUG/integrations/github/setup/
       → 在受害者 session 內初始化 pipeline，step_index=0
   (b) https://sentry.io/extensions/github/setup/?installation_id=<attacker_installation>&code=<attacker_code>&state=9cae5e88803f35ed7970fc131e6e65d3
4. (b) 進入 OAuthLoginView：第 397-399 行綁上攻擊者的 installation_id；第 412 行的 state 比對通過（它是常數）；token 交換取得攻擊者的 access_token；第 438 行把 github_authenticated_user 寫成攻擊者的 login；next_step。
5. 同一個請求接著進入 GitHubInstallation：installation_id 取自剛綁上的 state，Integration 由 webhook 建立且尚無 OrganizationIntegration，第 501-504 行比對「攻擊者 login == metadata.sender.login（攻擊者）」→ 通過 → finish_pipeline。
驗證方式：以受害者 session cookie 重放 (a)(b) 兩個 GET，再查 OrganizationIntegration 是否出現 victim-org × attacker installation 的組合。
```

**影響範圍**：受害者 organization 被綁上攻擊者控制的 GitHub App installation。後果是該 org 的 code mapping、stacktrace link、commit 歸屬與 repository 清單全部指向攻擊者的 repository，攻擊者可任意改動或撤除這些內容；org 成員在 Sentry 介面上看到的原始碼片段來源因此不再可信。反向的資料外洩有限（攻擊者不會因此讀到 org 既有的 repository），但完整性受損，且必須由管理員手動察覺並移除。前提是受害者需具備 org:integrations scope 並載入攻擊者頁面；本變更不涉及 PHI，沒有病患資料成本。

**風險處置**：Mitigate（降低）

**修復參考**：src/sentry/identity/oauth2.py:245-252、324

**修復方向**：改用一次性隨機值，並存進 pipeline state，與 OAuth2LoginView 一致：

```python
import secrets
...
if not request.GET.get("state"):
    state = secrets.token_hex()
    pipeline.bind_state("oauth_state", state)
    ...

# callback 端
if not constant_time_compare(request.GET.get("state", ""), pipeline.fetch_state("oauth_state") or ""):
    return error(request, self.active_organization)
```

更好的做法是直接沿用 sentry.identity.oauth2 的 OAuth2LoginView / OAuth2CallbackView，不要在 integration.py 重寫一份；那樣 state、urlencode、subdomain 綁定與例外處理都自動一致（見 F-004 / F-005 / F-006）。若沿用，記得測試不能再硬編 state 值，改成從 pipeline state 取出。

#### F-002 integration.metadata["sender"] 直接下標，缺 key 時會 KeyError 變成 500 — `src/sentry/integrations/github/integration.py:501`

面向 F 資料取用與資料庫 · Critical

**問題**：第 503 行 integration.metadata["sender"]["login"] 假設 metadata 一定有 sender。實際上 sender 只有在 webhook 路徑才會被寫入：

- webhook.py:202-207 收到 installation.created 時，把 sender 放進 state；
- integration.py:376-377 的 build_integration 只在 state.get("sender") 為真時才寫 metadata["sender"]；
- pipeline 路徑從頭到尾沒有 bind_state("sender", ...)（已 grep 全 src/，零命中），所以由使用者走完 pipeline 產生的 metadata 沒有 sender；
- 而 integrations/pipeline.py:32-42 的 ensure_integration 是 integration.update(**defaults)，metadata 是整包覆蓋，不是 merge——安裝流程完成時會把 webhook 先前寫進去的 sender 一併洗掉。

這段程式碼被觸及的條件是「Integration 存在且 ACTIVE，但沒有任何 OrganizationIntegration」。這個狀態是真實存在的：deletions/defaults/organizationintegration.py 的刪除任務只刪 OrganizationIntegration，不動 Integration；而且同 repo 的 GitHubIntegrationsInstallationEndpoint 處理的正是同一種狀態，它在 installation.py:44 明確寫了 `if "sender" not in integration.metadata: return HttpResponse(status=404)`。也就是說，codebase 自己已經承認這個 key 可能不存在——這一段沒有跟上。

找過反證：新增的 test_github_user_mismatch 只覆蓋「webhook 建立、sender 存在」那一種，走不到缺 key 的分支；既有的 test_github_prevent_install_until_pending_deletion_is_complete 則是把 integration 與 oi 一起刪掉，也走不到。所以沒有測試會擋下這個情況。落地後的行為是 KeyError → HTTP 500，使用者看到的不是設計好的 github-integration-failed.html，而是一個未處理的錯誤頁。

**證據**：
- `src/sentry/integrations/github/integration.py:501`
- `src/sentry/integrations/github/integration.py:503`
- `src/sentry/integrations/github/installation.py:44`
- `src/sentry/integrations/pipeline.py:42`
- `src/sentry/integrations/github/integration.py:376`

**修復方向**：用取值後判斷取代直接下標，並讓缺 key 的情況走同一條 error() 出口：

```python
sender = integration.metadata.get("sender") or {}
if pipeline.fetch_state("github_authenticated_user") != sender.get("login"):
    return error(request, self.active_organization)
```

注意 sender.get("login") 為 None 時，pipeline state 也不可能是 None（它來自 GitHub /user 的 login），所以這個寫法會安全地落到 error() 而不是意外放行。並補一個測試：Integration 為 ACTIVE、metadata 無 sender、無 OrganizationIntegration，斷言回傳的是 github-integration-failed.html 而非 500。

<details>
<summary>Suggestion（7）</summary>

#### F-003 身分檢查沒有覆蓋首次安裝路徑：Integration 不存在時直接放行 — `src/sentry/integrations/github/integration.py:477`

面向 C 安全 · Suggestion

**問題**：把「把 installation 綁到 organization」這個危險操作的所有到達路徑列出來，一共四條：

1. Integration.DoesNotExist（integration.py:481-482）→ 直接 return pipeline.next_step()，**沒有任何身分檢查**；
2. Integration 存在且已有 OrganizationIntegration → error()，擋下；
3. Integration 存在、ACTIVE、無 OrganizationIntegration → 走第 502-504 行的身分檢查；
4. Integration 存在但非 ACTIVE → error()。

新加的檢查只在第 3 條路上。第 1 條是 installation.created webhook 還沒送達（或送達失敗）時的狀態，此時 Sentry 端沒有任何 sender 可比對，於是流程原封不動地放行 —— 之後 build_integration 會拿 App 的 JWT 去 GET https://api.github.com/app/installations/{id}（integration.py:346-349），這個呼叫只證明該 installation 存在，不證明呼叫者有權處置它。

這不是本次變更造成的退步（改動前這是唯一路徑），但它是新控制項的覆蓋缺口：只要能在 webhook 落地前搶先完成 pipeline，檢查就整條被繞過。webhook 送達與瀏覽器 redirect 之間的實際時序無法從程式碼判定，因此本項的定位是「控制項有缺口」而非「已證實可利用」（時序問題另記於 Q-001）。

**證據**：
- `src/sentry/integrations/github/integration.py:477`
- `src/sentry/integrations/github/integration.py:483`
- `src/sentry/integrations/github/integration.py:502`

**修復方向**：不要以「Sentry 這邊記到的 sender」作為唯一依據，改成向 GitHub 求證授權使用者本身對這個 installation 的權限，這樣四條路徑一次全部覆蓋，也順帶解掉 F-002 與 F-009：

```python
# 以使用者的 OAuth token 呼叫 GET https://api.github.com/user/installations
# 回傳的 installations[].id 若不含本次的 installation_id，就 error()
installations = get_user_installations(payload["access_token"])
if int(installation_id) not in {i["id"] for i in installations}:
    return error(request, self.active_organization)
```

若短期內不想加這個 API 呼叫，至少在第 1 條路徑上補一則 log（含 installation_id 與 github_authenticated_user），讓「webhook 尚未落地就完成綁定」這件事在事後可稽核。

#### F-004 get_user_info 與 safe_urlopen 的例外沒有接，會變成 500 而不是錯誤頁 — `src/sentry/integrations/github/integration.py:423`

面向 E 架構 · Suggestion

**問題**：第 434 行的 get_user_info() 在 identity/github/provider.py:7-17 內會呼叫 resp.raise_for_status()，GitHub 回非 2xx 時直接丟 requests.HTTPError；第 423 行的 safe_urlopen 也可能丟連線／SSL 例外。兩者都沒有被 try 包住，所以一旦 GitHub 端出狀況，使用者拿到的是未處理例外（500），而不是這個 MR 特地整理出來的 github-integration-failed.html。

對照組就在同 repo：OAuth2CallbackView.exchange_token（identity/oauth2.py:287-313）把 SSLError、ConnectionError、JSONDecodeError 都接起來並轉成可顯示的錯誤訊息。本 view 只保留了 safe_urlread + parse_qsl 那一段的 try（integration.py:425-429），漏掉外層兩個呼叫。

附帶一個結果：第 435 行的 `if "login" not in authenticated_user_info` 幾乎是死碼——GitHub /user 回 200 時一定有 login，回非 200 則已經在 raise_for_status 就丟出去了。真正會發生的失敗模式沒被處理，被處理的那個幾乎不會發生。

**證據**：
- `src/sentry/integrations/github/integration.py:423`
- `src/sentry/integrations/github/integration.py:434`
- `src/sentry/identity/github/provider.py:16`
- `src/sentry/identity/oauth2.py:292`

**修復方向**：把 token 交換與 /user 兩段一起包起來，失敗一律走既有的 error() 出口，並留下可追查的 log：

```python
try:
    req = safe_urlopen(url=ghip.get_oauth_access_token_url(), data=data)
    payload = dict(parse_qsl(safe_urlread(req).decode("utf-8")))
except Exception:
    logger.info("github.oauth.token-exchange-failed", exc_info=True)
    payload = {}

if "access_token" not in payload:
    return error(request, self.active_organization)

try:
    authenticated_user_info = get_user_info(payload["access_token"])
except Exception:
    logger.info("github.oauth.user-info-failed", exc_info=True)
    return error(request, self.active_organization)
```

#### F-005 authorize URL 以字串串接組成，redirect_uri 未做百分比編碼 — `src/sentry/integrations/github/integration.py:407`

面向 C 安全 · Suggestion

**問題**：第 407-409 行用 f-string 直接把 client_id、state、redirect_uri 串進 query string，沒有經過 urlencode。redirect_uri 的值來自 absolute_uri(...)，含有 `://` 與 `/`，依 RFC 6749 §3.1 這些字元在 query 參數位置應該被百分比編碼；測試在 test_integration.py:235 斷言 `redirect_uri=http://testserver/extensions/github/setup/` 這個未編碼的字串，等於把這個缺陷固定下來。

目前 GitHub 端能容忍未編碼的形式，所以這不是「現在就壞」；但只要 system.url-prefix 或路由日後帶上任何需要編碼的字元（query、非 ASCII、`&`），這裡就會靜默地產生錯誤的 redirect_uri。同 repo 的 OAuth2LoginView（identity/oauth2.py:245-250）用的是 urlencode(params)，並且會帶上 response_type=code；本實作兩者都沒有。

**證據**：
- `src/sentry/integrations/github/integration.py:407`
- `src/sentry/identity/oauth2.py:250`
- `tests/sentry/integrations/github/test_integration.py:235`

**修復方向**：改用 urlencode 組裝：

```python
from urllib.parse import urlencode

params = {
    "client_id": github_client_id,
    "response_type": "code",
    "state": state,
    "redirect_uri": redirect_uri,
}
return self.redirect(f"{ghip.get_oauth_authorize_url()}?{urlencode(params)}")
```

測試端相對應地改成解析 query（parse_qs）後逐一斷言，不要比對整串原始字串，這樣參數順序或編碼形式調整時測試不會誤報。

#### F-006 沒有 bind_state("subdomain")，customer domain 的回程還原分支永遠不會觸發 — `src/sentry/integrations/github/integration.py:404`

面向 E 架構 · Suggestion

**問題**：OAuthLoginView 把 redirect_uri 固定成 absolute_uri(reverse("sentry-extension-setup", ...))，也就是系統主網域上的 sentry-extension-setup 路由（extensions/github/setup/）。對啟用 customer domain 的 organization 來說，安裝流程是從 acme.sentry.io 開始的，經過 GitHub 之後會落在主網域。

PipelineAdvancerView 本來就備有把使用者送回原 subdomain 的機制（pipeline_advancer.py:54-59）：`subdomain = pipeline.fetch_state("subdomain")`，不符就 redirect 回 generate_organization_url(subdomain)。但這個 state 只有 OAuth2LoginView 會寫（identity/oauth2.py:253-254 `if request.subdomain: pipeline.bind_state("subdomain", request.subdomain)`），新的 OAuthLoginView 沒有寫，所以對 GitHub pipeline 而言這個還原分支恆為 no-op。

可從程式碼直接確認的後果是：回程之後 self.determine_active_organization(request) 走的是 base.py:230-235 的 _find_implicit_slug，此時沒有 subdomain，只能退回 session 的 activeorg（甚至再退到 user 的第一個 organization，base.py:161-167）。也就是說錯誤頁的 document_origin 與 pending-deletion 檢查所依據的 organization，不再保證是使用者當初發起安裝的那一個。實際使用者可見的影響需要真實 customer domain 環境才能確認，另記於 Q-002。

**證據**：
- `src/sentry/integrations/github/integration.py:404`
- `src/sentry/identity/oauth2.py:254`
- `src/sentry/web/frontend/pipeline_advancer.py:54`
- `src/sentry/web/frontend/base.py:230`

**修復方向**：在第一段（尚未帶 state、要導去 GitHub 之前）補上與 OAuth2LoginView 相同的綁定：

```python
if request.subdomain:
    pipeline.bind_state("subdomain", request.subdomain)
```

這樣回程時 PipelineAdvancerView 既有的分支就會把使用者帶回 acme.sentry.io 再繼續 pipeline，不需要新增其他機制。並補一個 customer domain 的整合測試，斷言回程 response 是往 organization URL 的 redirect。

#### F-007 test_installation_not_found 已經測不到它名字宣稱的路徑 — `tests/sentry/integrations/github/test_integration.py:379`

面向 G 測試 · Suggestion

**問題**：這個測試傳入的 state 是 ddd023d87a913d5226e2a882c4c4cc05，而 GitHub pipeline 的 pipeline.signature 是 9cae5e88803f35ed7970fc131e6e65d3（可由 md5 兩個 view 的 FQN 算出，也就是同檔案其他測試硬編的那個值）。兩者不相等，所以請求在 integration.py:412 的 state 比對就被擋下並回傳 error()，永遠走不到 build_integration → get_installation_info 的 404 分支。

連帶地：第 380-382 行 responses.replace(... status=404) 這段 setup 成了死碼，第 385-387 行第一次 self.client.get 的結果立刻被覆寫，而原本的斷言 `b"The GitHub installation could not be found."`（對應 integration.py:356 的 IntegrationError）被換成 `b"Invalid installation request."`。結果是：測試會過，但過的理由跟測試名稱無關，而 build_integration 的 404 處理從此沒有任何覆蓋。

**證據**：
- `tests/sentry/integrations/github/test_integration.py:379`
- `tests/sentry/integrations/github/test_integration.py:392`
- `tests/sentry/integrations/github/test_integration.py:397`
- `src/sentry/integrations/github/integration.py:412`

**修復方向**：把 state 改成正確值讓流程真的走到底，並把斷言換回原本的訊息：

```python
resp = self.client.get(
    "{}?{}".format(
        self.setup_path,
        urlencode({"code": "12345678901234567890", "state": VALID_STATE}),
    )
)
resp = self.client.get(
    "{}?{}".format(self.setup_path, urlencode({"installation_id": self.installation_id}))
)
assert b"The GitHub installation could not be found." in resp.content
```

另外建議獨立補一個 test_invalid_state，專門驗證 state 不符時回 "Invalid installation request."——那是一條值得測的路徑，只是不該由這個測試代打。

#### F-008 _(error_short) 對變數取 gettext，三段使用者可見訊息會從翻譯檔消失 — `src/sentry/integrations/github/integration.py:141`

面向 A 風格 · Suggestion

**問題**：改動前，三段 error_short 文字是以字面字串寫在 _() 裡（例如 _("GitHub installation pending deletion.")），Django 的 makemessages 會把它們抽進 .po。改動後字面字串移到 error() 的參數與預設值上（第 132 行 "Invalid installation request."、呼叫端的 "GitHub installation pending deletion." 與 "Github installed on another Sentry organization."），第 141 行變成對變數 _(error_short)。

makemessages 是靜態掃描，只認得 _() 裡的字面字串，抽不到變數。結果是這三個 msgid 會從翻譯檔中消失，執行期查表落空、一律回退成英文——而且是靜默的，沒有任何錯誤。error_long 沒有這個問題，因為 ERR_* 常數本身就是 gettext_lazy(...) 的字面字串。

**證據**：
- `src/sentry/integrations/github/integration.py:141`
- `src/sentry/integrations/github/integration.py:132`

**修復方向**：讓字面字串留在 _() 內，error() 收的就是已經是 lazy 字串的值：

```python
def error(
    request,
    org,
    error_short=_("Invalid installation request."),
    error_long=ERR_INTEGRATION_INVALID_INSTALLATION_REQUEST,
):
    ...
    "data": {"error": error_short},
```

呼叫端相對應改成 error_short=_("GitHub installation pending deletion.") 與 error_short=_("Github installed on another Sentry organization.")。這樣三個 msgid 都會被 makemessages 重新抽到。

#### F-009 身分比對用可變的 login，而不是 webhook 已經存下來的不可變 sender id — `src/sentry/integrations/github/integration.py:503`

面向 C 安全 · Suggestion

**問題**：webhook.py:202-207 同時存了 sender.id 與 sender.login，get_user_info() 回傳的 /user payload 也同時含 id 與 login，但第 503 行比對的是 login。GitHub 的 login 是可以改的，而且舊 login 會被釋出讓別人註冊。

兩個方向都不理想：使用者改名後，本人回來重新安裝會被誤判為不符而擋下（可用性問題）；而舊 login 被第三方註冊後，該第三方的 login 就會與 metadata 內留存的舊值相符（安全問題）。用 id 比對兩邊都消失，而且不需要多存任何東西——資料已經在 metadata 裡了。

**證據**：
- `src/sentry/integrations/github/integration.py:503`
- `src/sentry/integrations/github/webhook.py:204`
- `src/sentry/identity/github/provider.py:17`

**修復方向**：改綁 id：

```python
pipeline.bind_state("github_authenticated_user_id", authenticated_user_info["id"])
...
sender = integration.metadata.get("sender") or {}
if pipeline.fetch_state("github_authenticated_user_id") != sender.get("id"):
    return error(request, self.active_organization)
```

舊資料若可能只有 login 沒有 id，可暫時保留「id 存在就比 id，否則比 login」的過渡邏輯，並在註解寫清楚何時可以移除。

</details>

<details>
<summary>Nit（5）</summary>

#### F-010 同一個 Integration 在同一次 request 內被查了兩次 — `src/sentry/integrations/github/integration.py:478`

面向 F 資料取用與資料庫 · Nit

**問題**：第 476-479 行為了判斷 installations_exist 已經做了 Integration.objects.get(external_id=installation_id)，第 494-496 行又用 external_id + status=ACTIVE 查了一次同一列。兩次查詢之間沒有任何寫入，第二次純粹是為了拿到物件本身與加上 status 條件。除了多一次 DB round-trip，兩段查詢條件不一致（前者不看 status）也讓「這條路徑到底涵蓋哪些 Integration」比需要的更難讀。

補充歸屬：第一段查詢沒有 status 條件是既有行為（改動前是 Integration.objects.get(external_id=request.GET["installation_id"])，同樣沒有 status），本次只換了變數名稱。真正由這次變更產生的是「兩段條件不一致」這件事本身——第二段新增了 status=ObjectStatus.ACTIVE，讓落差第一次變得可見。

**證據**：
- `src/sentry/integrations/github/integration.py:478`
- `src/sentry/integrations/github/integration.py:494`

**修復方向**：查一次、留住物件即可：

```python
try:
    integration = Integration.objects.get(
        external_id=installation_id, status=ObjectStatus.ACTIVE
    )
except Integration.DoesNotExist:
    return pipeline.next_step()

if OrganizationIntegration.objects.filter(integration=integration).exists():
    return error(request, self.active_organization, error_short=..., error_long=...)
```

注意合併時要保留原本「Integration 不存在 → next_step」與「Integration 存在但非 ACTIVE」的行為差異，別讓兩者被合併成同一個出口。

#### F-011 新增的 error()、get_document_origin()、OAuthLoginView.dispatch 缺型別標註 — `src/sentry/integrations/github/integration.py:129`

面向 A 風格 · Nit

**問題**：同檔案的 build_repository_query 與 GitHubInstallation.dispatch(self, request: Request, pipeline: Pipeline) -> HttpResponse 都有完整標註，新增的三處沒有：error(request, org, ...) 完全沒標、get_document_origin(org) -> str 只標了回傳、OAuthLoginView.dispatch 的 pipeline 沒標。

影響主要在可讀性：org 實際收到的是 self.active_organization，型別是 RpcUserOrganizationContext | None（base.py:134），所以 get_document_origin 內才會寫 org.organization——不標註的話，讀者要往回追兩層才知道這個參數不是 Organization。

pyproject.toml 目前把 sentry.integrations.github.integration 列在「sentry modules with typing issues」的放寬清單（pyproject.toml:307，區塊註解寫著 "remove the module from the list and fix the issues!"），所以 CI 不會因此失敗；但新程式碼是往清單相反的方向加。

**證據**：
- `src/sentry/integrations/github/integration.py:129`
- `src/sentry/integrations/github/integration.py:149`
- `src/sentry/integrations/github/integration.py:390`
- `src/sentry/integrations/github/integration.py:447`

**修復方向**：補上標註，型別直接沿用既有定義：

```python
def error(
    request: Request,
    org: RpcUserOrganizationContext | None,
    error_short: str | StrPromise = ...,
    error_long: StrPromise = ERR_INTEGRATION_INVALID_INSTALLATION_REQUEST,
) -> HttpResponse:

def get_document_origin(org: RpcUserOrganizationContext | None) -> str:

class OAuthLoginView(PipelineView):
    def dispatch(self, request: Request, pipeline: Pipeline) -> HttpResponse:
```

#### F-012 測試裡重複五次的 state 硬編值、索引式斷言與被覆寫的 resp 指派 — `tests/sentry/integrations/github/test_integration.py:242`

面向 B 簡潔 · Nit

**問題**：三件互相加成的可維護性問題：

1. `urlencode({"code": "12345678901234567890", "state": "9cae5e88803f35ed7970fc131e6e65d3"})` 這段在檔案裡出現五次（242、338、445、707、737）。這個 md5 值一旦因為 pipeline view 增減或改名而變動，五處都要一起改，而且失敗訊息只會顯示斷言不符，看不出真正原因。
2. 第 257 行 `responses.calls[2]` 用位置索引取請求，是因為 _stub_github 在前面插了兩個新的 responses.add。之後任何人再插一個 stub，這行就會靜默地驗到別的請求。
3. 第 342-343、368-369、449-450、698-701、730-733 都是 `resp = self.client.get(...)` 指派後立刻被下一行覆寫，第一個 resp 從未被讀取。讀者會以為那次呼叫的回應有被檢查。

（行號註記：responses.calls[2] 在 test_integration.py:257，不在 445；445 是 test_github_user_mismatch 內的 state 字面值。已逐行對照原始檔確認。）

**證據**：
- `tests/sentry/integrations/github/test_integration.py:242`
- `tests/sentry/integrations/github/test_integration.py:338`
- `tests/sentry/integrations/github/test_integration.py:445`
- `tests/sentry/integrations/github/test_integration.py:257`
- `tests/sentry/integrations/github/test_integration.py:342`

**修復方向**：1. 抽成模組層級常數，或直接從 pipeline 取：`PIPELINE_STATE = md5_text("sentry.integrations.github.integration.OAuthLoginView", "sentry.integrations.github.integration.GitHubInstallation").hexdigest()`，五處改引用它（若採納 F-001 改成隨機 state，這裡改成從 session 取出 pipeline state）。
2. 第 257 行改成用 URL 找請求，而不是用索引：
```python
token_call = next(c for c in responses.calls if "access_tokens" in c.request.url)
assert token_call.request.headers["Authorization"] == "Bearer jwt_token_1"
```
3. 被覆寫的第一個 resp 直接不指派（`self.client.get(...)`），或補上該有的斷言。

#### F-013 pipeline 多一個 view 會改變 signature，部署當下進行中的安裝 session 全數失效 — `src/sentry/integrations/github/integration.py:344`

面向 I 回溯分析 · Nit

**問題**：Pipeline.signature 由 pipeline view 的類別名稱算出（base.py:127-130），而 Pipeline.is_valid() 要求 state.signature == self.signature（base.py:146-152）。get_pipeline_views 從 [GitHubInstallation()] 變成 [OAuthLoginView(), GitHubInstallation()]，signature 隨之改變，所以部署瞬間所有已存在的 GitHub 安裝 pipeline state 都會判為 invalid，使用者在 PipelineAdvancerView 拿到「Invalid request.」並被導回首頁（pipeline_advancer.py:50-52）。

影響範圍有界：PIPELINE_STATE_TTL 是 1 小時（pipeline/constants.py:2），且使用者重新發起安裝即可恢復。已確認 GitHubEnterpriseIntegrationProvider 自行覆寫 get_pipeline_views（github_enterprise/integration.py:328），不受這次變更影響。

**證據**：
- `src/sentry/integrations/github/integration.py:344`
- `src/sentry/pipeline/base.py:133`
- `src/sentry/pipeline/base.py:149`
- `src/sentry/pipeline/constants.py:2`

**修復方向**：程式碼不需要改，這是換 pipeline 步驟必然的代價。建議在 MR 描述或部署記錄裡寫明「部署後一小時內，正在進行中的 GitHub 安裝流程會被要求重新開始」，讓 support 端不會把這批回報當成新 bug；若在意，可挑安裝量低的時段部署。

#### F-014 pipeline_advancer.py 的 E402：import 仍卡在檔案中段，但擋住它的區塊已經被這次變更移走 — `src/sentry/web/frontend/pipeline_advancer.py:19`

面向 A 風格 · Nit

**問題**：ruff 在 diff 範圍內只回報這一件：pipeline_advancer.py:19 的 `from rest_framework.request import Request` 觸發 E402（module level import not at top of file）。這是既有問題，不是這次引入的——改動前它就已經在檔案中段。但這次剛好把它上方的 FORWARD_INSTALL_FOR 區塊搬走了，現在它與檔頭 import 區之間只剩 PIPELINE_CLASSES 一個常數，順手收掉的成本比以往任何時候都低。專案其他既有 ruff 問題共 250 件，均不在本次 diff 範圍內，不列入本次。

**證據**：
- `src/sentry/web/frontend/pipeline_advancer.py:19`

**修復方向**：把第 19 行的 `from rest_framework.request import Request` 併入檔頭的 import 區（放在 `from django.utils.translation import gettext_lazy as _` 之後的第三方區塊），並刪掉中間那行。改完 `uvx ruff check src/sentry/web/frontend/pipeline_advancer.py` 應為零。若這個 import 位置是為了避開循環匯入才刻意保留，請補一行註解說明，否則下一個人一定會再嘗試搬它一次。

</details>

<details>
<summary>未驗證提問（2）</summary>

#### Q-001 在真實環境中，攻擊者能否穩定地趕在 GitHub 的 installation.created webhook 落地之前完成 pipeline，從而走 F-003 描述的未檢查路徑？

面向 C 安全

**背景**：F-003 已從程式碼確認 Integration.DoesNotExist 那條路徑沒有任何身分檢查（integration.py:481-482）。但這條路徑是否可被主動觸發，取決於 GitHub 送 webhook 與把使用者瀏覽器 redirect 回 Sentry 這兩件事的實際時序，以及 Sentry 端 webhook 處理的排隊延遲。這些都不在 repo 內，無法從這次的 checkout 判定，因此不為它指派 severity。

**如何確認**：在 staging 上安裝一次 App，記錄 installation.created webhook 抵達並完成處理的時間戳，與使用者被 redirect 回 sentry-extension-setup（extensions/github/setup/）的時間戳，比較兩者順序與間隔；或直接改成 F-003 建議的 GET https://api.github.com/user/installations 求證方式，讓時序問題不再重要。

#### Q-002 對啟用 customer domain 的 organization，經過 GitHub 往返後回到主網域，pipeline session 與 active organization 是否仍然解析正確？

面向 E 架構

**背景**：F-006 已確認可從程式碼判定的部分：subdomain 沒有被 bind，PipelineAdvancerView 的還原分支（pipeline_advancer.py:54-59）因此恆為 no-op。但回程之後 session cookie 在主網域是否仍然帶得到、_find_implicit_slug（base.py:230-235）退回 session activeorg 後解析到的是不是原本那個 organization，取決於實際的 cookie domain 設定與反向代理行為。既有測試（test_integration.py:693-710）在 testserver 上通過，但那個環境沒有真正的 subdomain，證明不了生產行為。

**如何確認**：在有真實 customer domain 的環境（acme.sentry.io）走一次完整安裝，觀察回程落點、是否被導回 organization URL、以及失敗頁的 document_origin 是否為該 organization 的 URL。

</details>
