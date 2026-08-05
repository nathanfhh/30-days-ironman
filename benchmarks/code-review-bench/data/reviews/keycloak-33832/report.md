## 審查結論：Approved with Comments

> Critical 0 · Suggestion 5 · Nit 4 · 未驗證提問 2
> nathan-code-review 2026.08.02.05 · 第 1 次審查

### 總評

| A 風格 | B 簡潔 | C 安全 |
|:--:|:--:|:--:|
| ❌ | ❌ | ❌ |

| D API 慣例 | E 架構 | F 資料取用與資料庫 |
|:--:|:--:|:--:|
| — | ❌ | — |

| G 測試 | H 非 Python 檔 | I 回溯分析 |
|:--:|:--:|:--:|
| ❌ | ✅ | ❌ |

- **A 風格**（未通過）：新增的三個 Java 檔命名、縮排、License header 都與 repo 既有慣例一致，方法長度也都在合理範圍。唯一列出的是 F-009：getBouncyCastleProvider() 用「建一個預設型別的 KeyStore 再問它的 provider 是誰」來表達「這裡沒有 BouncyCastle」，讀者得先知道預設 keystore 型別與其 provider 才看得懂。
- **B 簡潔**（未通過）：F-003（兩行回傳值被丟掉的 ASN1Encoder 呼叫）、F-006（debugf 用法與無條件字串建構）、F-007（未被使用的 hamcrest 測試相依）。三條都是「做了但沒有用到」的殘留物。ASN1Encoder / ASN1Decoder 本身沒有過度設計——只實作了 INTEGER 與 SEQUENCE 兩個 tag，剛好夠 ECDSA 簽章用。
- **C 安全**（未通過）：新的 DER 解析器處理的是攻擊者可控的 JWS 簽章位元組，因此逐條追過。concatenatedRSToASN1DER 對輸入長度不做檢查（signature 太短時 System.arraycopy 丟 ArrayIndexOutOfBoundsException），但這是原封不動抄自既有的 BCECDSACryptoProvider.concatenatedRSToASN1DER，不是本次引入的退步；上層 ECDSASignatureVerifierContext.verify 也有 catch (Exception)。沒有找到可被利用的路徑。唯一列出的是 F-008 這條 Nit 等級的強化建議。另註：AuthzClientCryptoProvider 把絕大多數方法實作成 UnsupportedOperationException，這是明示的能力邊界而不是安靜的降級，方向正確。
- **D API 慣例**（不適用）：本次沒有 HTTP API、URL 路由、request/response schema 或驗證層的異動。唯一的介面契約變更是 CryptoProvider 這個 SPI，其相容性影響列在 dimension I（F-002）。
- **E 架構**（未通過）：F-001：provider 選擇這個「全域只能有一個答案」的決策，現在由散在四個模組的四個魔術數字（100 / 200 / 200 / 200）決定，而且相同數字之間沒有決勝規則。此外已確認 AuthzClient 的三個 create() overload 都收斂到 create(Configuration)（AuthzClient.java:60、72、94），constructor 是 private，所以 CryptoIntegration.init 這道初始化涵蓋了這個 class 的全部進入點，沒有漏掉的路徑。
- **F 資料取用與資料庫**（不適用）：本次沒有資料庫存取、schema 變更或持久化資料格式的異動。ASN.1 編解碼全部在記憶體內完成，ASN1Decoder / ASN1Encoder 都是每次呼叫新建實例（AuthzClientCryptoProvider.java:114-121、127-128），沒有跨執行緒共用的可變狀態。CryptoIntegration.cryptoProvider 是全域單例，但初始化本來就有 double-checked locking 且 field 是 volatile，本次未改動這部分。
- **G 測試**（未通過）：新增了 ECDSAAlgorithmTest，這比沒有測試好。但兩個問題讓它的保障低於表面：F-004（只驗證自己的 round-trip，編碼結果從未交回 JDK 驗簽）、F-005（三個測試共用同一把預設 EC 金鑰，DER 長度 >127 的分支從未被執行）。CryptoIntegration 的 provider 排序改動則完全沒有測試——目前 repo 內找不到任何針對 detectProvider 的測試（grep "Multiple crypto providers" 全 repo 無結果）。
- **I 回溯分析**（未通過）：F-002：CryptoProvider 這個 public interface 新增了 abstract 的 order()。repo 內的四個實作本次都補上了（已用 grep -rln "implements CryptoProvider" 確認只有 default / elytron / fips1402 / 新增的 authz-client 四個，測試碼裡也沒有匿名實作），in-tree build 不會壞；問題在 repo 外。另已確認 detectProvider 的行為改變沒有破壞既有呼叫端的簽章（CryptoIntegration.init / getProvider 的簽章未動），改變的是語意而非型別。

### 意圖確認

以下項目在審查前留有疑慮。疑慮不阻擋審查，列出是因為這個決定屬於人，不屬於審查流程：

- **該在這個 MR 做？**：這個 MR 同時做了三件受眾不同的事：(1) authz-client 新增自用的 CryptoProvider 與 META-INF/services；(2) common 模組的 public interface CryptoProvider 新增 abstract 的 order()（common/src/main/java/org/keycloak/common/crypto/CryptoProvider.java:44）；(3) CryptoIntegration 移除「classpath 上有多個 provider 就直接失敗」的啟動期保護（common/src/main/java/org/keycloak/common/crypto/CryptoIntegration.java:57-71）。(2) 影響所有 repo 外的實作者，(3) 改變 server 與 FIPS 部署的全域選擇行為，兩者的風險面和 (1) 完全不同。(3) 是為了讓 (1) 能運作才做的，綁在一起可以理解，但至少該在 MR 描述裡明講「本次移除了一道啟動期保護」，否則這個改動會夾在一個看起來只碰 authz-client 的 MR 裡被帶過。

### 掃描執行狀況

| 工具 | 狀態 | 說明 |
|---|---|---|
| trivy | 略過 | trivy 未安裝（不在 PATH 上），本次未執行相依套件弱點、設定錯誤與憑證外洩掃描。此變更動到 authz/client/pom.xml，屬於 trivy 的守備範圍，這塊沒有掃到。 |
| opengrep | 略過 | opengrep 未安裝（不在 PATH 上），且 NCR_OPENGREP_RULES 指向的 Semgrep 規則目錄不存在。兩個條件都缺，本次未執行 SAST 掃描。 |
| ruff | 已執行 | in_diff 0、outside_diff 6 |
| ty | 略過 | ty 未安裝（不在 PATH 上）。本次 diff 內也沒有 Python 檔，即使安裝也無適用對象。 |
| oxlint | 略過 | oxlint 未安裝（不在 PATH 上）。本次 diff 內沒有 JavaScript / TypeScript 檔，即使安裝也無適用對象。 |
| codegraph | 略過 | codegraph 未安裝，無法建立符號圖。本次的呼叫者列舉、實作者列舉與完整性判定全部改用 grep 完成（例如以 grep -rn "implements CryptoProvider" 確認 repo 內只有四個實作、以 grep -rn "CryptoIntegration.init(" 列出所有初始化進入點）。 |
| ncr-fresh-eyes | 略過 | 本次執行環境沒有任何可派發 subagent 的工具（沒有 Agent / Task tool，以 ToolSearch 查詢後確認）。依 SKILL.md Phase 3 的規定，不得由主 agent 自行模擬 fresh eyes，因此本次沒有「未被本 skill 塑形過的第一眼」這一層檢查，全部發現都來自 dimension 檢查與人工追碼。 |
| ncr-quality-check | 略過 | 同上，環境無法派發 subagent，Phase 4 的報告品質複核未執行。本報告只經過 report_model.py 的機械驗證。 |

<details>
<summary>Suggestion（5）</summary>

#### F-001 移除「多個 crypto provider 就失敗」的保護後，相同 order 的 provider 之間沒有決勝規則 — `common/src/main/java/org/keycloak/common/crypto/CryptoIntegration.java:57`

面向 E 架構 · Suggestion

**問題**：舊版在 classpath 上偵測到多個 CryptoProvider 時直接丟 IllegalStateException，訊息是「Make sure only one cryptoProvider available on the classpath」。新版改成依 order() 由大到小排序後取第一個。但 DefaultCryptoProvider、WildFlyElytronProvider、FIPS1402Provider 三者的 order() 都回傳 200，也就是舊保護原本針對的那個情境（crypto-default 與 crypto-fips1402 同時出現）在新規則下完全沒有被區分：Stream.sorted 是 stable sort，排序後的順序會退回 ServiceLoader 的掃描順序，也就是 classpath 順序，而那在不同打包方式、不同檔案系統、不同 JVM 之間並不保證一致。結果是原本會在啟動時大聲失敗的設定錯誤，現在變成安靜地挑一個，唯一的痕跡是 CryptoIntegration.java:63-69 的 debug 等級 log，而 debug 預設不會開。FIPS 部署誤選到非 FIPS 的 BouncyCastle 屬於 compliance 等級的問題，這正是原本那道保護存在的理由。已找過反證：repo 內三條初始化路徑目前都有上游保護——Quarkus 走 KeycloakRecorder.setCryptoProvider(FipsMode) 明確指定 class name，不經過 detectProvider；admin-cli 走 ClassLoaderUtil.resolveClassLoader，會依 bc-fips jar 是否存在只放行一組 crypto jar；Quarkus 下 KeycloakApplication 的 init 因為 provider 已被 setProvider 設好而是 no-op。這些反證是嚴重度停在 Suggestion 而非 Critical 的原因，但 keycloak-common / keycloak-core 是對外發佈的 artifact，把它們 embed 進自家應用的使用者不受這些保護。

**證據**：
- `common/src/main/java/org/keycloak/common/crypto/CryptoIntegration.java:57`
- `common/src/main/java/org/keycloak/common/crypto/CryptoIntegration.java:60-71`
- `crypto/default/src/main/java/org/keycloak/crypto/def/DefaultCryptoProvider.java:82-85`
- `crypto/elytron/src/main/java/org/keycloak/crypto/elytron/WildFlyElytronProvider.java:75-79`
- `crypto/fips1402/src/main/java/org/keycloak/crypto/fips/FIPS1402Provider.java:113-117`

**修復方向**：保留 order() 排序，但補回無法決定時的失敗：排序後比較前兩名的 order()，相同就沿用原本的 IllegalStateException 並把兩個 class name 列出來。這樣 authz-client 的 100 vs 其他的 200 仍然正常運作，被擋掉的只有真正分不出勝負的情況。另外把 "Ignored crypto providers" 這行從 debugf 提升到 warn——classpath 上出現預期外的 provider 是設定異常，不是除錯資訊。

#### F-002 CryptoProvider 新增的 order() 是 abstract method，會讓 repo 外的既有實作編譯與連結失敗 — `common/src/main/java/org/keycloak/common/crypto/CryptoProvider.java:39-44`

面向 I 回溯分析 · Suggestion

**問題**：order() 直接加在 public interface CryptoProvider 上，沒有 default 實作。repo 內的四個實作本次都補上了（已用 grep -rln "implements CryptoProvider" 確認只有 crypto/default、crypto/elytron、crypto/fips1402 與新增的 authz-client 四個，測試碼裡也沒有匿名實作），所以 in-tree build 不會壞。但 keycloak-common 是對外發佈的 artifact：升級後，repo 外任何既有的 CryptoProvider 實作在 source 層會 compile error，已編譯的 class 在執行期被呼叫到 order() 會拿到 AbstractMethodError。這不是推測——在 Java 裡對既有 interface 加 abstract method 就是不相容變更。而且 authz/client/pom.xml:18-19 自己就寫明真正給第三方用的 org.keycloak:keycloak-authz-client 位於另一個 repo，代表跨 repo 的實作確實存在。

**證據**：
- `common/src/main/java/org/keycloak/common/crypto/CryptoProvider.java:39-44`
- `authz/client/pom.xml:18-19`

**修復方向**：改成 default method：`default int order() { return 0; }`（或給一個具名的基準常數）。既有實作不改也能編譯，需要調整優先序的才 override。順帶建議把散在四個模組的 100 / 200 抽成 CryptoProvider 上的具名常數（例如 ORDER_FALLBACK = 100、ORDER_DEFAULT = 200），現在這四個數字之間的關係只存在於 javadoc 的一句話裡。

#### F-003 concatenatedRSToASN1DER 內有兩行結果被丟掉的 ASN1Encoder 呼叫 — `authz/client/src/main/java/org/keycloak/authorization/client/util/crypto/AuthzClientCryptoProvider.java:114-115`

面向 B 簡潔 · Suggestion

**問題**：第 114-115 行 `ASN1Encoder.create().write(rBigInteger);` 與 `ASN1Encoder.create().write(sBigInteger);` 的回傳值沒有被接住，實際被用到的是第 117-121 行那一組重新建立的 encoder。比對同語意的既有實作 crypto/default/.../BCECDSACryptoProvider.concatenatedRSToASN1DER，也沒有對應的東西，判定是重構後留下的殘骸。它讓每次 ES* 驗簽多做兩次 BigInteger 編碼與兩個 ByteArrayOutputStream 配置，成本很小；真正的代價是可讀性——在密碼學程式碼裡看到兩行「看起來有做事但沒有」的呼叫，下一個維護者得先證明它無害才敢動它。

**證據**：
- `authz/client/src/main/java/org/keycloak/authorization/client/util/crypto/AuthzClientCryptoProvider.java:114-115`
- `authz/client/src/main/java/org/keycloak/authorization/client/util/crypto/AuthzClientCryptoProvider.java:117-121`

**修復方向**：刪掉第 114-115 兩行。

#### F-004 ECDSAAlgorithmTest 只驗證自己的 round-trip，編碼結果從未交回 JDK 驗簽 — `authz/client/src/test/java/org/keycloak/authorization/client/test/ECDSAAlgorithmTest.java:46-57`

面向 G 測試 · Suggestion

**問題**：測試流程是 JDK 簽章 → asn1derToConcatenatedRS → concatenatedRSToASN1DER → asn1derToConcatenatedRS，最後只斷言前後兩次的 concat 結果相同（第 56 行）。這條斷言在 encoder 與 decoder 犯下互相對稱的錯誤時仍然會通過：DER length 少寫或多寫一個位元組、SEQUENCE tag 用錯、長度旗標位元設錯——只要 decoder 用同樣的方式讀回來，assertArrayEquals 就對得上。而這個 provider 的實際契約是「產生一段 JDK Signature.verify 吃得下的 DER」，那個契約在測試裡完全沒有被檢查：第 54 行產生的 asn1Des 之後就沒有再被驗證過，只被餵回自己的 decoder。

**證據**：
- `authz/client/src/test/java/org/keycloak/authorization/client/test/ECDSAAlgorithmTest.java:46-57`
- `authz/client/src/test/java/org/keycloak/authorization/client/test/ECDSAAlgorithmTest.java:56`

**修復方向**：在第 54 行之後補一條對 JDK 的交叉驗證，例如：`Signature v = Signature.getInstance(JavaAlgorithm.getJavaAlgorithm(algorithm.name())); v.initVerify(keyPair.getPublic()); v.update(data); Assert.assertTrue(v.verify(asn1Des));`。或者更直接：`Assert.assertArrayEquals(sign, asn1Des)`——兩邊都應該是 minimal DER，逐位元組相同，任何編碼偏差都會立刻現形。

#### F-005 三個 ES 測試共用同一把預設 EC 金鑰，DER 長度 >127 的分支從未被執行 — `authz/client/src/test/java/org/keycloak/authorization/client/test/ECDSAAlgorithmTest.java:42`

面向 G 測試 · Suggestion

**問題**：第 42 行是 `KeyPairGenerator.getInstance("EC").genKeyPair()`，沒有呼叫 initialize()，SunEC 的預設是 256-bit（secp256r1），而三個測試方法共用 constructor 產生的這一把。ECDSAAlgorithm 的 getSignatureLength() 對 ES384 / ES512 分別是 96 / 132（對應 P-384 / P-521），但一把 P-256 金鑰簽出來的 r、s 只有 32 bytes 左右，integerToBytes 會補上一長串前導零，BigInteger 再把零丟掉，於是編碼出來的 SEQUENCE 內容永遠不到 127 bytes。結果是 ASN1Encoder.writeLength 的 long-form 分支（length > 127，第 67-79 行）與 ASN1Decoder.readLength 對應的多位元組長度解析（第 137-154 行）——這個手刻 DER 實作裡最容易寫錯、也最需要被測到的部分——三個測試一次都沒有跑到。只有真正的 P-521 簽章（SEQUENCE 內容約 138 bytes）會走進去。順帶一提，測試通過並不代表 ES384 / ES512 被驗證了，只代表補零後的 round-trip 是對稱的。

**證據**：
- `authz/client/src/test/java/org/keycloak/authorization/client/test/ECDSAAlgorithmTest.java:42`
- `authz/client/src/test/java/org/keycloak/authorization/client/test/ECDSAAlgorithmTest.java:59-72`
- `authz/client/src/main/java/org/keycloak/authorization/client/util/crypto/ASN1Encoder.java:66-79`
- `authz/client/src/main/java/org/keycloak/authorization/client/util/crypto/ASN1Decoder.java:137-154`

**修復方向**：依演算法產生對應曲線的金鑰，把 keyPair 從 constructor 移進 test(ECDSAAlgorithm) 裡：`KeyPairGenerator kpg = KeyPairGenerator.getInstance("EC"); kpg.initialize(new ECGenParameterSpec(curveFor(algorithm)));`，其中 ES256 → secp256r1、ES384 → secp384r1、ES512 → secp521r1。這樣 ES512 的案例才會真的踩到 long-form 長度分支。

</details>

<details>
<summary>Nit（4）</summary>

#### F-006 debugf 被當成 debug 使用，訊息無條件建構且尾端多一個逗號 — `common/src/main/java/org/keycloak/common/crypto/CryptoIntegration.java:64-70`

面向 B 簡潔 · Nit

**問題**：第 69 行 `logger.debugf(builder.toString())` 把一段執行期組出來的字串當成 format template 傳給 debugf。class name 裡不會出現 %，所以今天不會有格式化例外，但語意上該用 logger.debug()。另外第 65-68 行的字串組裝在 debug 沒開時也會執行；這段只在有多個 provider 時走到，成本很小，但既然是 debug 訊息就沒有理由無條件建構。產出的字串尾端也固定多一個 ", "。

**證據**：
- `common/src/main/java/org/keycloak/common/crypto/CryptoIntegration.java:64-70`

**修復方向**：改成 `if (logger.isDebugEnabled()) { logger.debug("Ignored crypto providers: " + foundProviders.stream().skip(1).map(p -> p.getClass().getName()).collect(Collectors.joining(", "))); }`。Collectors 已經 import 在這個檔案裡。配合 F-001，這行其實更適合改用 warn。

#### F-007 新增的 hamcrest 測試相依沒有被用到 — `authz/client/pom.xml:67-71`

面向 B 簡潔 · Nit

**問題**：這個模組底下目前只有 ECDSAAlgorithmTest 一個測試檔（find authz/client/src/test -name '*.java' 只回一個結果），它只用了 org.junit.Assert，import 清單裡沒有任何 org.hamcrest。junit:junit 本身已經會帶進 hamcrest-core，額外宣告 org.hamcrest:hamcrest 會讓兩個世代的 hamcrest 同時進 test classpath。已確認版本由 parent 的 dependencyManagement 管理（common/pom.xml:48-57 是完全相同的寫法），所以不會 build 失敗——這條相依單純是死的。

**證據**：
- `authz/client/pom.xml:67-71`
- `authz/client/src/test/java/org/keycloak/authorization/client/test/ECDSAAlgorithmTest.java:22-31`

**修復方向**：移除 authz/client/pom.xml:67-71 這五行；之後真的需要 assertThat / matcher 時再加回來。

#### F-008 手刻的 DER decoder 沒有防負長度（readLength 可能回傳 -1） — `authz/client/src/main/java/org/keycloak/authorization/client/util/crypto/ASN1Decoder.java:127-136`

面向 C 安全 · Nit

**問題**：readLength() 遇到 0x80（indefinite-length encoding）時回傳 -1（第 134 行），而 read(int length) 直接 `new byte[length]`（第 170 行）。負長度會丟 NegativeArraySizeException，那是 unchecked 例外，會直接穿過宣告 throws IOException 的 readInteger()。這裡明確標示反證的結果：追過目前唯一的呼叫者 asn1derToConcatenatedRS，輸入必須先經過 readSequence()，而 readNext() 會把 -1 再加回 reset() 回傳的 header 長度，切出來的子陣列不可能是 [tag, 0x80, ...]，所以今天走不到；上層 ECDSASignatureVerifierContext.verify 也有 catch (Exception)。也就是說這不是現行漏洞，而是一個新加的、直接面對攻擊者可控位元組的 DER parser 少了一道長度合理性檢查——距離下一個呼叫者只有一步。同一段的 readSequence 在元素長度總和超過宣告長度時會讓 length 變負、迴圈靜默結束，也就是格式錯誤的 SEQUENCE 會被當成正常解析完畢。

**證據**：
- `authz/client/src/main/java/org/keycloak/authorization/client/util/crypto/ASN1Decoder.java:127-136`
- `authz/client/src/main/java/org/keycloak/authorization/client/util/crypto/ASN1Decoder.java:169-170`
- `authz/client/src/main/java/org/keycloak/authorization/client/util/crypto/ASN1Decoder.java:55-62`

**修復方向**：在 read(int) 開頭加 `if (length < 0) { throw new IOException("Invalid length: " + length); }`，或讓 readLength() 對 indefinite-length 直接丟 IOException——DER 本來就不允許 indefinite length，這個 decoder 也只用來讀 DER。readSequence 的迴圈條件可一併改成結束時檢查 length == 0，不為零就報錯。

#### F-009 getBouncyCastleProvider() 用 KeyStore 迂迴取得 JDK 預設 provider — `authz/client/src/main/java/org/keycloak/authorization/client/util/crypto/AuthzClientCryptoProvider.java:60-67`

面向 A 風格 · Nit

**問題**：這個實作是 `KeyStore.getInstance(KeyStore.getDefaultType()).getProvider()`，也就是「建一個預設型別的 KeyStore，再問它是誰做的」，用來表達「這個 provider 沒有 BouncyCastle」。要看懂得先知道預設 keystore 型別是 PKCS12、它背後的 provider 是 SUN。同樣情境下 WildFlyElytronProvider.getBouncyCastleProvider() 直接回 null，而 BouncyIntegration.loadProvider() 對 null 有既有處理（退回 Security.getProviders()[0]）。已確認在 authz-client 的相依範圍內（common + core），BouncyIntegration.PROVIDER 只被 CryptoIntegration.java:34 的一行 debug log 讀到（grep -rn "BouncyIntegration" core/ common/ 只有這兩處），所以回哪個值目前沒有功能差別，純粹是可讀性問題。

**證據**：
- `authz/client/src/main/java/org/keycloak/authorization/client/util/crypto/AuthzClientCryptoProvider.java:60-67`
- `crypto/elytron/src/main/java/org/keycloak/crypto/elytron/WildFlyElytronProvider.java:70-73`
- `common/src/main/java/org/keycloak/common/util/BouncyIntegration.java:33-40`

**修復方向**：與 WildFlyElytronProvider 一致回 null，讓 BouncyIntegration 既有的 null 處理接手；或保留現寫法但加一行註解說明「authz-client 沒有 BC，這裡刻意回 JDK 預設 provider」，讓下一個讀者不必自己推導 KeyStore 與 Provider 的關係。

</details>

<details>
<summary>未驗證提問（2）</summary>

#### Q-001 keycloak-common 對外發佈之後，repo 外的 CryptoProvider 實作（特別是 org.keycloak:keycloak-authz-client 所在的那個 repo）是否已經同步補上 order()，而且兩邊會在同一個 release 一起出？

面向 I 回溯分析

**背景**：authz/client/pom.xml:18-19 明講這個模組只供本 repo 的 testsuite 使用，真正給第三方用的 keycloak-authz-client 在另一個 repo；commit message 也寫「in keycloak main repository」，暗示有一個對應的跨 repo 改動。本 checkout 看不到那個 repo，本次執行環境也沒有網路，無法查證。in-tree 的四個實作都已更新（grep -rln "implements CryptoProvider" 只有四個檔案）。這是 F-002 的爆炸半徑，不是 F-002 的成立與否。

**如何確認**：到 keycloak-client repo（以及任何下游自訂 crypto provider）確認 order() 已補上並會與本次改動同版本發佈；或改用 F-002 建議的 default method，讓這個問題不需要被回答。

#### Q-002 實務上真的存在 keycloak-crypto-default 與 keycloak-crypto-fips1402 同時被同一個 classloader 看到的部署嗎？

面向 E 架構

**背景**：這決定 F-001 的爆炸半徑。舊 code 的錯誤訊息（"Make sure only one cryptoProvider available on the classpath"）暗示這個情況發生過，否則不會有人寫那道保護。但 repo 內三條初始化路徑目前都被上游擋住：KeycloakRecorder.setCryptoProvider 依 FipsMode 明確指定 class name；ClassLoaderUtil.resolveClassLoader 依 bc-fips jar 是否存在只放行一組 crypto jar；Quarkus 下 KeycloakApplication 的 init 是 no-op。從這個 checkout 無法判定 embedded 使用者或第三方發佈物的情況。git log -S 也查不到那道保護當初被加進來的 commit（此 checkout 的歷史不完整）。

**如何確認**：找出當初加入該 IllegalStateException 的 commit 與對應 issue，或 support 端實際收到過這個錯誤的案例。任一者都能把 F-001 的嚴重度往上或往下定住。

</details>
