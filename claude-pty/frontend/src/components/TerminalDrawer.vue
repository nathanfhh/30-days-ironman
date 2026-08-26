<script setup lang="ts">
/* ── 終端抽屜：從右側滑入的 ttyd ───────────────────────────────────────────────
 *
 * iframe 的 src 一律是 nginx 的 `/session/<sid>/`，**不是** POST /view 回傳的
 * `direct_url`：後者是另一個 origin（127.0.0.1:41xxx），會被本站 CSP 的 `default-src
 * 'self'` 直接擋掉，而且跨 origin 之後也讀不到 iframe 的狀態。所以直連模式（未走 nginx）
 * 不開抽屜，退回開新分頁——見呼叫端。
 *
 * ⚠ ttyd 帶 `-q`：最後一個 WebSocket 斷線它就自己 exit。抽屜關閉會拆掉 iframe＝斷線，
 *   所以「關掉再開」在抽屜上是常態而不是例外。三件事撐住它：
 *     1. 重開時 ttyd 早就退乾淨了（`views.list_views` 會丟掉 pid 已不存在的殘留列）。
 *     2. 「程序還在、卻已經不服務」的收尾空檔實測打不到（斷開 WS 後 0/5/20/60ms 各重開
 *        一次，四次都拿到新的 pid）；曾為此加過 port 探測，量完發現它擋掉的是 0 次、
 *        代價是每個被代理的請求多一次 TCP connect，所以拿掉。
 *     3. 「開新分頁」**刻意不關抽屜**：先斷 iframe 再讓新分頁連，中間那段一個 client 都
 *        沒有，ttyd 會在新分頁連上之前就退出。兩邊同時連著才是安全的交接。
 *
 * ⚠ 這裡**刻意沒有** Esc 關閉。ttyd 一載入就把焦點搶進終端（實測 activeElement 是
 *   IFRAME），母頁面根本收不到 keydown——而那是對的：Esc 是 TUI 自己的鍵，Claude Code 用它
 *   中斷。做成「還沒點進終端時 Esc 有效、點進去之後失效」比一律不接管更糟。代價要講明：
 *   進了終端之後鍵盤沒有回頭路（xterm 連 Tab 都吃掉），只能用滑鼠關。
 */
import { nextTick, onBeforeUnmount, onMounted, ref, useTemplateRef } from "vue";

import { ApiError, notifyUnauthorized } from "@/api/client";
import { useTerminalSize } from "@/composables/useTerminalSize";
import { toast, toastError } from "@/lib/toast";
import { useSiteStore } from "@/stores/site";

const props = defineProps<{
  sid: string;
  label: string;
  path: string;
  flavor?: string | null;
  /* 這一場有沒有開流量錄製（`profile.capture`）。沒開就沒有 mitmweb 可看——
     那時**按鈕整顆不畫**，不是畫一顆按了會失敗的（後端對那種 session 回 404）。 */
  capture?: boolean;
}>();

const emit = defineEmits<{ close: [] }>();

const store = useSiteStore();

const panel = useTemplateRef<HTMLElement>("panel");
const frame = useTemplateRef<HTMLIFrameElement>("frame");
const fileInput = useTemplateRef<HTMLInputElement>("fileInput");
const closeBtn = useTemplateRef<HTMLButtonElement>("closeBtn");

const open = ref(false);
const closing = ref(false);
const pendingText = ref("連線中…");
const pendingHidden = ref(false);

const { fontSize, attach, stop, bumpFont, FONT_MIN, FONT_MAX } = useTerminalSize({
  sid: props.sid,
  frame,
  panel,
  closing,
});

/* ── 提示輪播 ────────────────────────────────────────────────────────────────
 * ⚠ 提示做成**輪播**，不是並排。標題列的寬度要分給 session 名稱、字級、新分頁、關閉，
 *   並排時最先被擠掉的是名稱，而且窄視窗下原本得靠 media query 一條一條藏——藏掉的那條
 *   就等於不存在。輪播讓每一條都輪得到，佔的寬度只有一條。
 * ⚠ 只在**不只一條**時才轉——一條也在轉的話那不是輪播，是閃爍。
 */
const hintCount = (): number => (store.meta.persistDir ? 2 : 1);
const activeHint = ref(0);
let hintTimer: ReturnType<typeof setInterval> | null = null;
/* ⚠ 兩個條件要**分開記**，不可以 enter→stop / leave→start 這樣直接對接。點過複製鍵之後
   焦點會留在它上面，這時把滑鼠移開就會恢復輪播——而輪播會把沒露臉的那幾條設成 inert，
   於是**使用者正聚焦的那顆按鈕變成 inert，焦點被瀏覽器收走**。代價是「點完就把滑鼠移開」
   時輪播會停著不動，直到焦點離開這一區為止。兩者相比，靜止的提示遠好過焦點在使用者腳下
   消失。 */
const hovering = ref(false);
const focused = ref(false);

function stopHints(): void {
  if (hintTimer) clearInterval(hintTimer);
  hintTimer = null;
}

function startHints(): void {
  stopHints();
  // ⚠ 抽屜關閉中就不要再起計時器：只要日後多一條關閉路徑，close 之後才到的 pointerleave
  //   就會重啟一個掛在已經 detached 的 DOM 上的 interval，而且沒有人會再清它。
  if (closing.value || hintCount() <= 1) return;
  // 6 秒：短到每一條都輪得到，長到讀得完一句。
  hintTimer = setInterval(() => {
    activeHint.value = (activeHint.value + 1) % hintCount();
  }, 6000);
}

function settleHints(): void {
  if (hovering.value || focused.value) stopHints();
  else startHints();
}

/** 這一條有沒有露臉。沒露臉的要退出 Tab 序與無障礙樹：它們在畫面上是透明的，留著的話
 *  鍵盤使用者會 Tab 到一個看不見的複製鍵，螢幕閱讀器也會把它們一起唸出來。 */
const hintOn = (i: number): boolean => (hintCount() > 1 ? activeHint.value === i : true);

/* ── iframe ──────────────────────────────────────────────────────────────── */
function onFrameLoad(): void {
  /* load 事件不等於「連上終端了」。view 若在抽屜開著的期間被回收，nginx 的 auth_request 會
     失敗 → 302 到 `/`，而 `/` 帶著 `X-Frame-Options: DENY`，瀏覽器拒絕在框內算繪——但 load
     照樣觸發。只看 load 的話「連線中…」會消失、換成一片白，什麼線索都沒有。
     iframe 同源，讀得到它現在停在哪；不在 /session/ 底下就是被導走了。 */
  let inside = true;
  try {
    inside = frame.value?.contentWindow?.location.pathname.startsWith("/session/") ?? false;
  } catch {
    inside = false; // 讀不到＝已經不是同源，同樣不是我們的終端
  }
  if (!inside) {
    pendingHidden.value = false;
    pendingText.value = "這個終端已經結束（session 可能被回收或終止了）。關掉這個抽屜再開一次。";
    return;
  }
  pendingHidden.value = true;
  attach();
  /* 在終端裡直接 ⌘V 一張圖：貼上事件發生在 iframe 的文件裡，父頁面收不到，所以監聽掛進去
     （同源，掛得上）。**只攔有檔案的貼上**——純文字放行給 xterm，那是正常的貼字。 */
  try {
    frame.value?.contentDocument?.addEventListener(
      "paste",
      (e) => {
        const f = (e as ClipboardEvent).clipboardData?.files?.[0];
        if (!f) return;
        e.preventDefault(); // 檔案終端吃不了，別讓 xterm 收到一坨 base64
        e.stopPropagation();
        void uploadFile(f);
      },
      true,
    );
  } catch {
    /* 已被導走（非同源）——上面那段本來就會顯示終端已結束 */
  }
}

/* ── 複製與上傳 ──────────────────────────────────────────────────────────── */
/** ⚠ 非 HTTPS 或權限被拒時 clipboard API 不可用——把路徑講出來讓人自己選，總比一句
 *  「複製失敗」然後什麼都不能做好。 */
async function copyPath(path: string, title = "已複製路徑"): Promise<void> {
  try {
    await navigator.clipboard.writeText(path);
    toast(title, "success", { body: path, duration: 4000 });
  } catch {
    toast("無法自動複製", "warning", { body: `請手動輸入：${path}`, duration: 10000 });
  }
}

/** 上傳一個檔案到這一場的持久化目錄，成功後把容器內路徑放進剪貼簿。
 *  ⚠ 不走 api()：這裡是 multipart 不是 JSON。Content-Type 交給瀏覽器組（boundary），自己設
 *    會把 boundary 弄丟。X-Requested-With 是後端要求的反 CSRF 標頭（form 設不了）。
 *  ⚠ **但 401 要走同一條路。**「401 一律導回登入頁」是全站的規格，不是 `api()` 這個函式的
 *    性質。少了下面那三行，cookie 中途失效時使用者按上傳會拿到一句「上傳失敗／未登入」，
 *    然後繼續留在一個什麼都做不了的畫面上（fable 快審 2026-08-26 抓到）。
 *    丟的是 `ApiError(…, 401)` 而不是裸 Error，`toastError` 才認得出來並把那一則吞掉：
 *    該讀的是全域那則「登入已失效」，不是「上傳失敗」。 */
async function uploadFile(file: File | null | undefined): Promise<void> {
  if (!file) return;
  toast("上傳中…", "info", { body: file.name, duration: 2000 });
  const fd = new FormData();
  fd.append("file", file, file.name);
  try {
    const res = await fetch(`/api/sessions/${encodeURIComponent(props.sid)}/upload`, {
      method: "POST",
      body: fd,
      credentials: "same-origin",
      headers: { "X-Requested-With": "fetch" },
    });
    if (res.status === 401) {
      notifyUnauthorized();
      throw new ApiError("未登入", 401);
    }
    const data = (await res.json().catch(() => ({}))) as { path?: string; error?: string };
    if (!res.ok) throw new Error(data.error || `${res.status} ${res.statusText}`);
    await copyPath(data.path ?? "", "已上傳，路徑在剪貼簿");
  } catch (ex) {
    // 8 秒不是隨手訂的：這一則要讀的是後端說的原因（副檔名、大小、磁碟滿了）
    toastError("上傳", ex, { duration: 8000 });
  }
}

function onFilePicked(): void {
  void uploadFile(fileInput.value?.files?.[0]);
  if (fileInput.value) fileInput.value.value = ""; // 同一個檔連傳兩次也要觸發 change
}

/* 流量畫面：mitmweb 自己送 `X-Frame-Options: DENY`，所以只能新分頁，不能像終端那樣嵌。
   路徑的**尾斜線不可省**：那個 SPA 是路徑相對的（`./static/…`、WS 由 location.pathname
   現場組），少了它資源會解析到 `/session/<sid>/` 底下，也就是終端那條路由。
   token 完全不經過這裡：nginx 在 auth_request 之後自己注入 Bearer（ADR 0021）。 */
function openMitm(): void {
  globalThis.open(`${props.path}mitm/`, "_blank", "noopener");
}

function popOut(): void {
  globalThis.open(props.path, "_blank", "noopener");
  toast("已在新分頁開啟", "info", {
    body: "抽屜刻意不關：先斷線再讓新分頁連，ttyd 會在中間就退出",
  });
}

/* ── 開關 ────────────────────────────────────────────────────────────────── */
let closeTimer: ReturnType<typeof setTimeout> | null = null;

function onPanelTransitionEnd(e: TransitionEvent): void {
  /* ⚠ 必須濾 target 與 propertyName：transitionend 會冒泡，而工具列那幾顆按鈕各有 120ms 的
     background 過渡。用滑鼠按下關閉鍵時，面板一滑走游標就離開了按鈕，hover-out 的過渡比
     面板的 240ms transform 早到——實測 179ms 就收到 `.icon-btn` 的 background-color，
     抽屜在 182ms 被拆掉，滑到 75% 就整個消失。 */
  if (!closing.value || e.target !== panel.value || e.propertyName !== "transform") return;
  if (closeTimer) clearTimeout(closeTimer);
  emit("close");
}

function requestClose(): void {
  if (closing.value) return;
  closing.value = true;
  stop();
  stopHints();
  open.value = false;
  // 動畫跑完才讓呼叫端把節點拆掉；prefers-reduced-motion 下 transition 被關掉、
  // transitionend 永遠不會來，所以另外掛一個 timeout 保底。
  closeTimer = setTimeout(() => emit("close"), 400);
}

let shellEl: HTMLElement | null = null;

onMounted(() => {
  // 先讓瀏覽器把「在畫面外」的起始狀態畫過一幀，再打開關——同一幀內掛上 src 與 data-open
  // 的話，transform 沒有起點可以過渡，抽屜會直接出現而不是滑進來。
  void nextTick(() => {
    requestAnimationFrame(() => {
      if (!closing.value) open.value = true;
    });
    // 限定在工具列裡找：抽屜掛著 aria-modal="true"，螢幕閱讀器會把背景整片藏起來，焦點若
    // 還留在背景那顆「終端」按鈕上，鍵盤使用者等於在一個被宣告不存在的頁面裡按 Tab。
    closeBtn.value?.focus();
  });
  // aria-modal 只影響螢幕閱讀器的虛擬游標，**不影響 Tab 順序**：背景的按鈕與連結全都還在
  // Tab 序裡，卻被 scrim 蓋住點不到。inert 才是同時處理兩者的那一個。
  shellEl = document.querySelector(".shell");
  if (shellEl) shellEl.inert = true;
  startHints();
});

onBeforeUnmount(() => {
  stopHints();
  if (closeTimer) clearTimeout(closeTimer);
  if (shellEl) shellEl.inert = false;
  /* 關閉時把焦點送回那一列的「終端」鍵。**不能記住原本的節點**：列表每 15 秒重繪一次，而
     看終端超過 15 秒是常態，那顆節點多半已經被換掉。所以用 sid 重查。 */
  /* ⚠ `CSS.escape` 不是每個環境都有（jsdom 就沒有）。舊版直接呼叫它——在真瀏覽器裡沒事，
     但在拿不到它的地方會讓整個 beforeUnmount 拋出，於是**元件拆不掉、抽屜留在畫面上**，
     而 Vue 只印一行 warn，畫面沒有任何跡象。單元測試當場抓到的。
     sid 是 uuid4 的 hex，本來就沒有需要逸出的字元，退回原字串是安全的。 */
  const safe = typeof CSS !== "undefined" && CSS.escape ? CSS.escape(props.sid) : props.sid;
  document.querySelector<HTMLElement>(`[data-act="open"][data-id="${safe}"]`)?.focus();
});
</script>

<template>
  <Teleport to="body">
    <!-- `data-sid`：目前開的是哪一場。畫面上用不到，但診斷時只有這裡問得到——沒有它就得
         回頭去比對列表按鈕的 data-id，而抽屜開著的時候列表是 inert 的。 -->
    <div class="drawer" data-testid="drawer" :data-sid="sid" :data-open="open ? '1' : undefined">
      <!-- 遮罩用自己的 act 名稱：與關閉鍵共用 `data-act="close"` 的時候，
           `querySelector('[data-act="close"]')` 會先選到這個不可聚焦的 div，focus() 靜靜地
           什麼都沒做，焦點一直留在抽屜背後。 -->
      <div class="drawer__scrim" data-act="scrim" @click="requestClose"></div>
      <section
        ref="panel"
        class="drawer__panel"
        role="dialog"
        aria-modal="true"
        :aria-label="`終端：${label || sid}`"
        @transitionend="onPanelTransitionEnd"
      >
        <header class="drawer__bar">
          <div class="drawer__id">
            <i class="fa-solid fa-terminal" aria-hidden="true"></i>
            <span class="drawer__title">{{ label || sid }}</span>
            <code class="drawer__sid">{{ sid }}</code>
            <!-- 這個終端是哪一顆 ttyd 在服務。兩顆（C / Rust）是同一個 UI，肉眼分不出來，
                 而出問題時「你看到的是哪一版」是第一個要問的問題。值來自這個 view 的 DB
                 記錄——**不是**這個人現在的偏好。舊的 view 記錄沒有這個值，那就不顯示。 -->
            <!-- prettier-ignore -->
            <span
              v-if="flavor"
              class="drawer__bin tip"
              data-testid="drawer-bin"
              :data-tip="`這個終端由 ${flavor} 版 ttyd 提供。要換另一顆請到「設定」——新開的 session 立刻套用，這一場要把終端分頁全部關掉、下次再開才會換。`"
            >{{ flavor }}</span>
          </div>
          <div class="drawer__tools">
            <!-- ⚠ 疊法用 grid（全部放進同一格 grid-area: 1/1），不是 absolute：容器的寬高
                 因此等於**最寬/最高那一條**，輪播時版面不會每 6 秒抽動一次。
                 ⚠ hover／focus 要暫停：裡面有可點的複製鍵，會動的點擊目標很惡劣。 -->
            <div
              class="drawer__hints"
              data-testid="drawer-hints"
              role="group"
              aria-label="終端使用提示"
              @pointerenter="((hovering = true), settleHints())"
              @pointerleave="((hovering = false), settleHints())"
              @focusin="((focused = true), settleHints())"
              @focusout="((focused = false), settleHints())"
            >
              <!-- 容器一收，cwd 底下寫的東西就沒了——這件事在終端裡沒有任何線索，而代價是
                   使用者辛苦產出的檔案。所以「哪個目錄留得住」要常駐。
                   ⚠ 做成 button 不是 span：它可以點（複製路徑），而可點的東西必須是原生
                     可聚焦、可用 Enter 觸發的元素，否則鍵盤使用者按不到。
                   ⚠ 讀 `data-persist-path` 而**不是** `data-copy`：後者是全域「點一下複製」
                     的公開標記，掛上去會讓這一次點擊被兩個 handler 接走（兩則 toast、
                     剪貼簿寫兩次）。 -->
              <button
                v-if="store.meta.persistDir"
                class="drawer__hint drawer__hint--persist tip tip--right tip--wide"
                type="button"
                data-act="copy-persist"
                :data-persist-path="store.meta.persistDir"
                data-testid="drawer-persist"
                :data-on="String(hintOn(0))"
                :inert="hintOn(0) ? undefined : true"
                :aria-hidden="hintOn(0) ? 'false' : 'true'"
                data-tip="只有這個目錄的內容留得到下一場（換一顆 container 也還在），而且是你個人的，別人看不到。工作目錄與家目錄其他地方都是容器的可寫層，session 一結束就消失。點一下複製路徑。"
                @click="copyPath(store.meta.persistDir)"
              >
                <i class="fa-solid fa-box-archive"></i>
                <!-- ⚠ 複製圖示緊貼在**路徑後面**、而且與路徑包成同一組。被複製的只有路徑：
                     圖示放在整句尾巴會像是整句都會被複製。組內貼緊、組外拉開。 -->
                <!-- prettier-ignore -->
                <span class="drawer__copy"><code>{{ store.meta.persistDir }}</code><i class="fa-regular fa-copy drawer__copy-icon" aria-hidden="true"></i></span>
                會留著
              </button>
              <!-- TUI 會開滑鼠追蹤（Claude Code 實測 ?1000/?1002/?1003/?1006 全開），一開啟，
                   拖曳就被當成應用程式的滑鼠事件送進 TUI，終端不再拿它來選取。要選字得按
                   修飾鍵繞過追蹤。這件事沒有任何畫面線索，不寫出來只能靠人猜。 -->
              <!-- prettier-ignore -->
              <span
                class="drawer__hint tip tip--right tip--wide"
                data-testid="drawer-mouse"
                :data-on="String(hintOn(hintCount() - 1))"
                :inert="hintOn(hintCount() - 1) ? undefined : true"
                :aria-hidden="hintOn(hintCount() - 1) ? 'false' : 'true'"
                data-tip="終端把滑鼠事件收走了，所以直接拖曳不會選字。按住修飾鍵拖曳＝選取，放開就已經複製（copyOnSelect）。貼上用 ⌘V／Ctrl+Shift+V。"
              ><i class="fa-solid fa-arrow-pointer"></i> ⌥/Alt 拖曳選字即複製</span>
            </div>
            <!-- 字級。⌘/Ctrl +- 在這裡不好用：macOS 根本沒綁 Ctrl+±（是 ⌘±），而焦點一旦
                 進了終端，那組鍵會被 xterm 收走送進 TUI。瀏覽器縮放又是整頁一起縮。
                 父頁面讀得到 iframe 的 window.term（同源），所以直接調它的 fontSize。 -->
            <span class="drawer__zoom" role="group" aria-label="終端字級">
              <button
                class="icon-btn"
                data-act="font-"
                data-testid="drawer-font-dec"
                aria-label="縮小終端字級"
                title="縮小字級"
                :disabled="fontSize !== null && fontSize <= FONT_MIN"
                @click="bumpFont(-1)"
              >
                <i class="fa-solid fa-minus"></i>
              </button>
              <!-- 把實際數值寫出來，不要只有加減：使用者要知道現在是幾 px 才調得準，也才看
                   得出已經頂到上下限（到界時該側的按鈕會 disabled）。 -->
              <!-- prettier-ignore -->
              <output class="drawer__zoom-value" data-testid="drawer-font-value" aria-live="polite">{{ fontSize === null ? "—" : `${fontSize}px` }}</output>
              <button
                class="icon-btn"
                data-act="font+"
                data-testid="drawer-font-inc"
                aria-label="放大終端字級"
                title="放大字級"
                :disabled="fontSize !== null && fontSize >= FONT_MAX"
                @click="bumpFont(1)"
              >
                <i class="fa-solid fa-plus"></i>
              </button>
            </span>
            <!-- 貼圖／上傳：PTY 是字元流，檔案過不去，所以「人上傳、人貼路徑」——上傳完路徑
                 自動進剪貼簿，回終端 ⌘V 就是那個路徑。accept 只是選檔視窗的 UX 過濾，
                 白名單的真相在後端 config.UPLOAD_EXTS。 -->
            <button
              class="icon-btn"
              data-act="upload"
              data-testid="drawer-upload"
              aria-label="上傳檔案，路徑會複製到剪貼簿"
              title="上傳檔案（路徑進剪貼簿）"
              @click="fileInput?.click()"
            >
              <i class="fa-solid fa-paperclip"></i>
            </button>
            <input
              ref="fileInput"
              type="file"
              hidden
              data-testid="drawer-file"
              accept=".png,.jpg,.jpeg,.gif,.webp,.pdf,.txt,.md"
              @change="onFilePicked"
            />
            <!-- ⚠ 做成 icon-btn 而不是帶字的 btn：這一列的寬度要分給 session 名稱、
                 提示輪播、字級與新分頁，多一顆帶字的會先擠掉名稱。 -->
            <button
              v-if="capture"
              class="icon-btn"
              data-act="mitm"
              data-testid="drawer-mitm"
              aria-label="開啟這一場的流量畫面（新分頁）"
              title="流量畫面（錄到的請求，新分頁開啟）"
              @click="openMitm"
            >
              <i class="fa-solid fa-network-wired"></i>
            </button>
            <button class="btn" data-act="pop" @click="popOut">
              <i class="fa-solid fa-arrow-up-right-from-square"></i> 新分頁
            </button>
            <button
              ref="closeBtn"
              class="icon-btn"
              data-act="close"
              data-testid="drawer-close"
              aria-label="關閉終端"
              title="關閉"
              @click="requestClose"
            >
              <i class="fa-solid fa-xmark"></i>
            </button>
          </div>
        </header>
        <div class="drawer__body">
          <iframe
            ref="frame"
            class="drawer__frame"
            data-testid="drawer-frame"
            :title="`終端：${label || sid}`"
            :src="path"
            @load="onFrameLoad"
          ></iframe>
          <!-- prettier-ignore -->
          <p class="drawer__pending" data-testid="drawer-pending" :hidden="pendingHidden">{{ pendingText }}</p>
        </div>
      </section>
    </div>
  </Teleport>
</template>
