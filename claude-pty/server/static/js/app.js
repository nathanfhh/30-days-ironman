/* agent-tty 控制台共用腳本：API 封裝、主題套用、小工具。無框架、無建置步驟。 */

/** HTML 逸出：所有進到 innerHTML 的動態值都必須經過（session id / 使用者名稱皆為外部資料）。 */
function esc(v) {
  return String(v ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

/** 「多久以前」。⚠ 這是全站唯一一份說法——session 列表與帳號清單都用它，兩邊各寫一份
 *  遲早會漂移成「3 分鐘前」與「3 分前」。刻意不引入 dayjs 之類的相依：這個專案沒有建置
 *  步驟，而 CSP 也不允許外部資源，為了四個分支背一整包函式庫不划算。 */
function relTime(iso) {
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  if (diff < 60) return `${Math.floor(diff)} 秒前`;
  if (diff < 3600) return `${Math.floor(diff / 60)} 分鐘前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小時前`;
  return `${Math.floor(diff / 86400)} 天前`;
}

/** 絕對時刻，使用者本地時區。伺服端一律送 UTC ISO，格式化只在這裡做。 */
function absTime(iso) {
  return new Date(iso).toLocaleString("zh-TW", {
    year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
  });
}

/** 列表裡的時間欄：顯示「多久以前」，hover/focus 給出原始時刻。
 *
 * 相對時間掃一眼就懂，但要對照 log、要跟別人講「就是那個時間點」時它沒有用；
 * 兩者不必二選一——把精確值收進 tooltip 就好。列表上每一個時間欄都該用這個，
 * 不要有的有、有的沒有。
 * ⚠ `tip--right`：這些欄位靠列的右半邊，置中的 tooltip 會頂出視窗右緣。
 * ⚠ **不加 tabindex**。一列有 2–3 個時間欄，一頁 20 列就是多 40–60 個 tab 停留點，
 *   鍵盤使用者要一路按過去才到得了「開啟 / 終止」。這些是純資訊、不可操作的欄位，
 *   把它們做成 tab stop 付的代價遠大於「鍵盤也叫得出 tooltip」換來的。
 *   （真的需要鍵盤可及時，該做的是一顆明確的「顯示絕對時間」切換，不是 20 個焦點。） */
function relTimeCell(iso, cls = "metric") {
  if (!iso) return `<span class="${cls} metric__none">—</span>`;
  return `<span class="${cls} tip tip--right" `
       + `data-tip="${esc(absTime(iso))}">${esc(relTime(iso))}</span>`;
}

/** 呼叫控制平面 API。失敗時把後端的中文錯誤訊息原樣拋出，供畫面顯示。 */
async function api(path, { method = "GET", body } = {}) {
  const res = await fetch(path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
    credentials: "same-origin",
  });
  if (res.status === 401) {          // cookie 過期或被登出 → 回登入頁
    location.href = "/login";
    throw new Error("未登入");
  }
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try {
      const data = await res.json();
      if (data && data.error) msg = data.error;
    } catch { /* 非 JSON 回應就沿用狀態碼 */ }
    const err = new Error(msg);
    // 狀態碼帶著走：有些呼叫端要分辨「時機不對」(409) 與「真的壞了」，而訊息本身
    // 是給人看的中文，拿它做判斷遲早會因為改一個字而失效。
    err.status = res.status;
    throw err;
  }
  return res.status === 204 ? null : res.json();
}

/** 是否在 nginx 之後（決定終端要開單一入口路徑還是 loopback 直連）。 */
function behindProxy() {
  return document.documentElement.dataset.behindProxy === "1";
}

/** session 內唯一「寫了會留下來」的目錄（容器內路徑；SSOT 在 config.DATA_BIND）。
 *
 * 拿不到就回空字串，呼叫端**不要顯示**——寧可少一條提示，也不要在標題列印一個
 * 空的 `<code>`（瀏覽器留著上一版 HTML 的快取時就會這樣）。 */
function persistDir() {
  return document.documentElement.dataset.persistDir || "";
}

/** 頁面上方的短暫提示。 */
/* ── 通知 toast ────────────────────────────────────────────────────────────────
 * 右上角堆疊，附倒數進度條，滑鼠移上去暫停。
 *
 * **倒數就是進度條那個 CSS animation 本身**，不另外開 setTimeout。這樣「hover 暫停」
 * 只要一行 `animation-play-state: paused` 就成立，而且畫面上的進度條與真正的剩餘時間
 * 永遠一致——用 JS 計時器的話，兩者是各走各的，暫停時就會對不上（進度條停了、計時器
 * 照跑，或反過來）。關閉時機由 animationend 決定。
 */
const TOAST_LEVELS = {
  info: "fa-circle-info",
  success: "fa-circle-check",
  warning: "fa-triangle-exclamation",
  danger: "fa-circle-exclamation",
  // 舊呼叫端用的字眼，映射過去（一次改完所有呼叫點反而容易漏）
  ok: "fa-circle-check",
  error: "fa-circle-exclamation",
};
const TOAST_ALIAS = { ok: "success", error: "danger" };

/** @param pausable 進度條是否可被 hover 暫停。倒數代表「時間到就會發生某件事」時
 *  必須傳 false——那個動作由別的計時器決定，暫停進度條只會讓畫面與事實不符。*/
function toast(title, level = "info", { body = "", duration = 5000, pausable = true } = {}) {
  if (!title) return;
  const kind = TOAST_ALIAS[level] || (TOAST_LEVELS[level] ? level : "info");
  let stack = document.getElementById("toast-stack");
  if (!stack) {
    stack = document.createElement("div");
    stack.id = "toast-stack";
    stack.className = "toast-stack";
    // aria-live：讓螢幕閱讀器唸出來。polite 而非 assertive——這些是操作結果回報，
    // 不該打斷使用者正在讀的內容。
    stack.setAttribute("aria-live", "polite");
    document.body.appendChild(stack);
  }

  const el = document.createElement("div");
  el.className = "toast";
  el.dataset.level = kind;
  if (!pausable) el.dataset.pausable = "0";
  el.innerHTML =
    `<i class="toast__icon fa-solid ${TOAST_LEVELS[kind]}"></i>` +
    `<div class="toast__body">` +
      `<div class="toast__title"></div>` +
      `<div class="toast__desc"></div>` +
    `</div>` +
    `<button class="toast__close" type="button" aria-label="關閉">` +
      `<i class="fa-solid fa-xmark"></i></button>` +
    `<span class="toast__bar"></span>`;
  // ⚠ 一律 textContent：標題與內文都可能含後端回傳或使用者輸入的字串。
  //   （tests/test_web.py 的 TEXT_SINKS 白名單正是建立在這一點上，改成 innerHTML
  //    會讓那些 XSS 檢查對 toast 全面失效。）
  el.querySelector(".toast__title").textContent = title;
  const descEl = el.querySelector(".toast__desc");
  descEl.textContent = body;
  descEl.hidden = !body;      // 沒有內文就不要留一行空白撐高
  const bar = el.querySelector(".toast__bar");
  bar.style.animationDuration = `${duration}ms`;

  let closing = false;
  function close() {
    if (closing) return;      // 進度條跑完與手動關閉可能同時發生
    closing = true;
    el.dataset.closing = "1";
    // 等離場過渡跑完再移除；transitionend 沒來（例如分頁在背景、動畫被略過）也要收，
    // 否則節點會永遠留著
    const done = () => el.remove();
    el.addEventListener("transitionend", done, { once: true });
    setTimeout(done, 400);
  }

  bar.addEventListener("animationend", close);
  el.querySelector(".toast__close").addEventListener("click", close);
  stack.appendChild(el);
  // 下一影格才加 shown，讓進場過渡有起始狀態可以過渡（同一影格內設會被合併掉）
  requestAnimationFrame(() => { el.dataset.shown = "1"; });
  return el;
}

/** 舊介面：保留 flash 這個名字，改由 toast 呈現（呼叫端不必全部改寫）。 */
function flash(message, tone = "ok") {
  return toast(message, tone);
}

/** 錯誤的統一呈現：標題講「哪個動作失敗」，內文放後端原文——只丟一句技術訊息當標題，
 *  使用者往往看不出是哪一步出的問題。 */
function toastError(action, err) {
  return toast(`${action}失敗`, "danger", { body: err?.message || String(err) });
}

/* ── 跨頁通知 ─────────────────────────────────────────────────────────────────
 * 這不是 SPA：登入成功就 location.href 換頁，當下發的 toast 會隨著舊文件一起消失。
 * 所以把它寄放在 sessionStorage，由下一個頁面載入時取出來顯示——也就是傳統的
 * flash message，只是改由前端接力。
 *
 * 用 sessionStorage 而非 localStorage：訊息屬於「這個分頁的這一次操作」，關掉分頁就
 * 該消失，也不該在另一個分頁莫名其妙跳出來。 */
const PENDING_TOAST_KEY = "claude-pty:pending-toast";

/** 存一則通知，等下一頁載入時顯示。 */
function toastAfterNav(title, level = "info", body = "") {
  try {
    sessionStorage.setItem(PENDING_TOAST_KEY, JSON.stringify({ title, level, body }));
  } catch { /* 無痕模式等情境下 storage 不可用——不顯示通知即可，別擋住流程 */ }
}

(function drainPendingToast() {
  let raw = null;
  try {
    raw = sessionStorage.getItem(PENDING_TOAST_KEY);
    // 先移除再顯示：只該出現一次，重新整理不該又跳一遍
    if (raw) sessionStorage.removeItem(PENDING_TOAST_KEY);
  } catch { return; }
  if (!raw) return;
  try {
    const t = JSON.parse(raw);
    // app.js 在 body 結尾載入，此時 document.body 已存在，可以直接掛
    toast(t.title, t.level, { body: t.body });
  } catch { /* 內容壞掉就當作沒有 */ }
})();


/* ── 品牌標誌（內嵌 SVG）────────────────────────────────────────────────────────
 * Font Awesome 沒有 Anthropic / OpenAI 的圖示，且品牌標誌不該相依外部 CDN。
 * 一律以 fill="currentColor" 繪製：顏色由 CSS 的 color 繼承，因此**深色/淺色主題
 * 自動適配、不需要各準備一份**（換主題只改 --color-* 變數，標誌跟著變）。
 */
const BRAND_PATHS = {
  anthropic:
    "M17.3041 3.541h-3.6718l6.696 16.918H24Zm-10.6082 0L0 20.459h3.7442l1.3693-3.5527h7." +
    "0052l1.3693 3.5528h3.7442L10.5363 3.5409Zm-.3712 10.2232 2.2914-5.9456 2.2914 5.9456Z",
};

/** 回傳品牌標誌的 inline SVG；顏色跟隨 currentColor（主題自動適配）。 */
function brandIcon(name, cls = "picker__icon") {
  const d = BRAND_PATHS[name];
  if (!d) return "";
  return `<svg class="${esc(cls)}" viewBox="0 0 24 24" width="1.05em" height="1.05em"
    fill="currentColor" aria-hidden="true"><path d="${d}"/></svg>`;
}

/* ── 自訂下拉 ──────────────────────────────────────────────────────────────────
 * 取代原生 <select>：外觀跨平台不受控、且塞不進圖示。
 * 保留原生語意：role=listbox/option、鍵盤（↑↓/Enter/Esc/Home/End）、aria-selected。
 *
 * 用法：createPicker(el, [{value, label, icon, hint}], initialValue, {search}) → { get value() }
 *
 * `search`：給選項多到要用找的那種（例如使用者清單）。展開時最上面多一格輸入框，
 * 打字即時過濾。選項少的時候不要開——多一格空輸入框只是噪音。
 */
/* 把浮層放到觸發元件的下方（或上方）。picker 與日期區間選擇器共用。
 *
 * ⚠ 用 `position: fixed` 而不是相對按鈕的 absolute：這些浮層會出現在有
 *   `overflow-y: auto` 的容器裡（modal 的 .modal__scroll），absolute 的浮層會被那個
 *   容器裁掉——展開後只看得到頂端一小條，後面的選項根本點不到（使用者回報）。
 * ⚠ fixed 的定位基準不一定是視窗：祖先只要有 backdrop-filter / transform / filter
 *   就會變成它的 containing block，而招牌與對話框遮罩兩者都有 backdrop-filter。
 *   所以座標**先歸零、量出實際落點、再回推差值**——不去猜基準是誰。
 */
function anchorPanel(anchor, panel, { mount = null, matchWidth = false } = {}) {
  const r = anchor.getBoundingClientRect();
  // fixed 之後 `min-width:100%` 是對視窗算的，要的話得自己補上觸發元件的寬度
  if (matchWidth) panel.style.minWidth = `${r.width}px`;
  // 先歸零，量「top/left 設成 0 時它實際落在視窗哪裡」，用這個差值回推想要的位置。
  // ⚠ **不要**拿 offsetParent 的 rect 換算：fixed 的基準是那個祖先的 **padding box**，
  //   而 getBoundingClientRect() 回的是 border box，兩者差了 border + padding
  //   （實測招牌那顆因此偏 4px——想要 6px 間距，量出來只有 2px）。歸零量測不必知道
  //   基準是誰、也不必知道它有多少 padding，換誰當祖先都會對。
  panel.style.top = "0px";
  panel.style.left = "0px";
  const zero = panel.getBoundingClientRect();
  const { height: h, width: w } = zero;
  // 下方空間不足就往上開。固定往下開的話，位在畫面底部的浮層一展開就整片跑到視窗外，
  // 使用者根本點不到後面的內容。
  const up = window.innerHeight - r.bottom < h + 16 && r.top > h + 16;
  if (mount) mount.dataset.drop = up ? "up" : "down";
  // 靠右緣的浮層展開後可能超出視窗，往左收回來（留 8px 邊距）
  const wantLeft = Math.max(8, Math.min(r.left, window.innerWidth - w - 8));
  /* ⚠ 垂直也要夾。翻上翻下只在「其中一側塞得下」時有解——浮層比可用高度還高時（日期
   *   面板 416px，遇到矮視窗或觸發元件剛好在中間），兩側都不夠，於是它就整片露到畫面
   *   外面去（使用者回報）。夾回可視範圍，超高的部分交給面板自己捲（CSS 有 max-height
   *   與 overflow-y）。 */
  const wantTop = up ? r.top - h - 6 : r.bottom + 6;
  const maxTop = Math.max(8, window.innerHeight - h - 8);
  panel.style.top = `${Math.max(8, Math.min(wantTop, maxTop)) - zero.top}px`;
  panel.style.left = `${wantLeft - zero.left}px`;
}


function createPicker(mount, options, initial, { search = false } = {}) {
  const state = { value: initial ?? options[0].value, open: false, active: 0, query: "" };
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "picker__button";
  btn.setAttribute("aria-haspopup", "listbox");
  btn.setAttribute("aria-expanded", "false");
  const menu = document.createElement("ul");
  menu.className = "picker__menu";
  menu.setAttribute("role", "listbox");
  menu.hidden = true;
  mount.className = "picker";
  mount.append(btn, menu);
  // 讓自動化測試（tests/e2e_*.py）點得到，不必靠 class 名稱去猜結構
  btn.dataset.testid = `${mount.id}-button`;
  menu.dataset.testid = `${mount.id}-menu`;

  const optOf = (v) => options.find((o) => o.value === v) || options[0];
  /** 目前查詢字串下看得到的選項。不分大小寫，比對 label。 */
  const visible = () => {
    const q = state.query.trim().toLowerCase();
    return q ? options.filter((o) => o.label.toLowerCase().includes(q)) : options;
  };

  const iconHtml = (o) => o.brand
    ? brandIcon(o.brand)
    : `<i class="picker__icon ${esc(o.icon || "fa-solid fa-circle")}"></i>`;

  function renderButton() {
    const o = optOf(state.value);
    btn.innerHTML = iconHtml(o) +
      `<span>${esc(o.label)}</span>` +
      `<i class="picker__caret fa-solid fa-chevron-down"></i>`;
  }

  function renderMenu() {
    const shown = visible();
    const searchBox = search
      ? `<li class="picker__search"><input class="input input--sm" type="search"
           data-testid="${esc(mount.id)}-search" placeholder="輸入名稱篩選"
           value="${esc(state.query)}" aria-label="篩選選項"></li>` : "";
    const rows = shown.length
      ? shown.map((o, i) => `
      <li class="picker__option" role="option" data-value="${esc(o.value)}"
          data-testid="${esc(mount.id)}-opt-${esc(o.value || "any")}"
          aria-selected="${o.value === state.value}" data-active="${i === state.active}">
        ${iconHtml(o)}
        <span>${esc(o.label)}</span>
        ${o.hint ? `<span class="picker__hint">${esc(o.hint)}</span>` : ""}
      </li>`).join("")
      : `<li class="picker__empty">找不到符合的項目</li>`;
    menu.innerHTML = searchBox + rows;
    if (search) {
      const input = menu.querySelector("input");
      // ⚠ 每次 renderMenu 都會重建 DOM，游標與選字會跟著沒了——重繪後要把游標放回尾端，
      //   否則打第二個字時游標已經跑到最前面。
      input.addEventListener("input", () => {
        state.query = input.value;
        state.active = 0;
        renderMenu();
      });
      // 在輸入框裡打字時，方向鍵/Enter 交給下面的 keydown 處理，其餘按鍵不要往上冒泡
      // ——否則每一個字元都會被當成 picker 的鍵盤指令。
      input.addEventListener("keydown", (e) => {
        if (["ArrowDown", "ArrowUp", "Enter", "Escape", "Home", "End"].includes(e.key)) {
          btn.dispatchEvent(new KeyboardEvent("keydown", { key: e.key, bubbles: false }));
          e.preventDefault();
        }
        e.stopPropagation();
      });
      queueMicrotask(() => {
        input.focus();
        input.setSelectionRange(input.value.length, input.value.length);
      });
    }
  }

  function open() {
    // 停用中就不開。⚠ CSS 的 `pointer-events: none` 只擋得住滑鼠——鍵盤使用者照樣
    // Tab 得進來、按 Enter 展開、改得動值，而伺服端稍後會靜靜把那個值丟掉
    // （review 2026-07-25）。停用必須同時對兩種輸入方式成立。
    if (mount.closest('[data-disabled="1"]')) return;
    state.open = true;
    state.query = "";        // 每次重新展開都從完整清單開始，不要留著上次打的字
    state.active = Math.max(0, options.findIndex((o) => o.value === state.value));
    renderMenu();
    menu.hidden = false;
    btn.setAttribute("aria-expanded", "true");
    place();
    // 選單是 fixed 定位的，頁面一捲它就會留在原地、跟按鈕脫節。
    // ⚠ **跟著重新定位，不要關掉**。關掉看似單純，但會把兩種正常操作一起殺掉：
    //   (1) 選單自己就是可捲的（選項多的時候），捲它會關掉自己；
    //   (2) 瀏覽器為了把元素捲進視野而產生的捲動也算——選項在畫面下緣時，
    //       游標剛移過去就關了（e2e 抓到的就是這條：點選項時的 scroll-into-view）。
    //   捲到按鈕整個離開視窗才關，那時它已經沒有可對齊的目標了。
    // capture 才收得到內層捲動容器（如 .modal__scroll）的事件。
    window.addEventListener("scroll", onScroll, { capture: true });
    window.addEventListener("resize", close);
  }

  function onScroll(e) {
    if (menu.contains(e.target)) return;      // 捲的是選單自己，不必動
    const r = btn.getBoundingClientRect();
    if (r.bottom < 0 || r.top > window.innerHeight) close();   // 按鈕捲出視窗了
    else place();
  }

  const place = () => anchorPanel(btn, menu, { mount, matchWidth: true });

  function close() {
    state.open = false;
    menu.hidden = true;
    btn.setAttribute("aria-expanded", "false");
    window.removeEventListener("scroll", onScroll, { capture: true });
    window.removeEventListener("resize", close);
  }
  function pick(v, origin) {
    state.value = v;
    renderButton();
    close();
    // detail 帶上點擊座標：主題切換的同心圓過渡需要圓心
    mount.dispatchEvent(new CustomEvent("change", {
      detail: { value: v, origin }, bubbles: true,
    }));
  }

  btn.addEventListener("click", () => (state.open ? close() : open()));
  menu.addEventListener("click", (e) => {
    const li = e.target.closest(".picker__option");
    if (li) pick(li.dataset.value, { x: e.clientX, y: e.clientY });
  });
  btn.addEventListener("keydown", (e) => {
    if (["ArrowDown", "ArrowUp", "Enter", " "].includes(e.key) && !state.open) {
      e.preventDefault(); open(); return;
    }
    if (!state.open) return;
    // ⚠ 一律走 visible()：開了搜尋之後「畫面上第 3 個」與「options 的第 3 個」不是同一個，
    //   用 options 索引會選到看不見的那一項。
    const shown = visible();
    if (e.key === "Escape") { e.preventDefault(); close(); }
    else if (!shown.length) { /* 找不到任何項目時方向鍵沒有東西可選 */ }
    else if (e.key === "ArrowDown") { e.preventDefault(); state.active = (state.active + 1) % shown.length; renderMenu(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); state.active = (state.active - 1 + shown.length) % shown.length; renderMenu(); }
    else if (e.key === "Home") { e.preventDefault(); state.active = 0; renderMenu(); }
    else if (e.key === "End") { e.preventDefault(); state.active = shown.length - 1; renderMenu(); }
    else if (e.key === "Enter") {
      e.preventDefault();
      const r = btn.getBoundingClientRect();   // 鍵盤操作：以按鈕中心為圓心
      pick(shown[Math.min(state.active, shown.length - 1)].value,
           { x: r.left + r.width / 2, y: r.top + r.height / 2 });
    }
  });
  document.addEventListener("click", (e) => { if (!mount.contains(e.target)) close(); });

  renderButton();
  return {
    get value() { return state.value; },
    set value(v) { state.value = v; renderButton(); },
    /** 換掉整份選項（清單是動態來源時用）。
     *
     * ⚠ `options` 是 closure 變數，被 optOf / renderMenu / 鍵盤處理**共用**——所以這裡
     *   要就地換內容（splice）而不是重新賦值：重新賦值只會換掉這個函式看到的那個綁定，
     *   其餘全部繼續指著舊陣列，症狀是「按鈕顯示新的、展開卻是舊清單」。
     * ⚠ 值一定要重新落地：新清單可能沒有目前選中的值。
     *   呼叫端給 `value` 就用它，沒給就退回新清單的第一個——絕不留一個不在清單裡的值，
     *   那會讓按鈕顯示 A、送出去的卻是 B。 */
    setOptions(next, value) {
      options.splice(0, options.length, ...next);
      const wanted = next.some((o) => o.value === value) ? value : (next[0] || {}).value;
      state.value = wanted;
      state.active = 0;
      renderButton();
      if (state.open) renderMenu();
    },
    /** 清單還沒到位時把它鎖起來。用**原生 disabled**（不是 pointer-events: none）：
     *  後者只擋滑鼠，鍵盤 Tab 過去照樣按得下，而這裡要擋的正是「值還不能信就被送出去」。
     *  鎖的當下要順手關掉展開的選單——不然它會停在畫面上，點了沒反應。 */
    set disabled(on) {
      btn.disabled = !!on;
      mount.dataset.loading = on ? "1" : "";
      if (on && state.open) close();
    },
    get disabled() { return btn.disabled; },
  };
}

/* ── 日期區間選擇器 ────────────────────────────────────────────────────────────
 *
 * 這裡原本是兩個 `<input type="datetime-local">`。原生控制項在「挑一段區間」這件事上
 * 很難用：看不到月曆所以挑不出「上週三到這週五」這種相對關係、兩個欄位彼此無關（迄可
 * 以早於起，錯了得自己發現）、而且一個月要按幾十次上下鍵。使用者回報「很難用」，要求
 * 參考 element-plus 的 datetimerange。
 *
 * 做法與它同形：一顆按鈕顯示目前區間，展開後左右兩個月並排；點第一下定起點、第二下定
 * 終點，中間滑過會即時預覽整段；上方各有日期/時間輸入框給要精確打字的人；選反了自動
 * 對調。按「確定」才送出——半截的區間不該觸發查詢。
 *
 * ⚠ 刻意**不放**「最近 7 天」那類快捷。左邊那格「時間範圍」下拉已經是相對區間了
 *   （一天內／一週內／一個月內），這裡再放一份語意近乎相同、行為卻不同（絕對 vs 相對）
 *   的按鈕，只會讓人不知道兩者差在哪。這個面板的職責就是「指定一段明確的區間」。
 */
const _RP_DOW = ["日", "一", "二", "三", "四", "五", "六"];
const _rpPad = (n) => String(Math.floor(Math.abs(n))).padStart(2, "0");
const _rpDay = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate());
const _rpMonth = (d) => new Date(d.getFullYear(), d.getMonth(), 1);
const _rpAddMonths = (d, n) => new Date(d.getFullYear(), d.getMonth() + n, 1);
const _rpYmd = (d) => `${d.getFullYear()}-${_rpPad(d.getMonth() + 1)}-${_rpPad(d.getDate())}`;
const _rpHm = (d) => `${_rpPad(d.getHours())}:${_rpPad(d.getMinutes())}`;
const _rpSameDay = (a, b) => !!(a && b) && _rpDay(a).getTime() === _rpDay(b).getTime();

/** 帶時區偏移的 ISO。後端只收帶時區的（見 app._iso_or_none），不猜是哪一區的牆上時間。 */
function _rpIso(d) {
  const off = -d.getTimezoneOffset();
  return `${_rpYmd(d)}T${_rpHm(d)}:00`
       + `${off >= 0 ? "+" : "-"}${_rpPad(off / 60)}:${_rpPad(off % 60)}`;
}

/** 一個月曆格子要畫的 42 天（含前後月補格）。從當月 1 號回推到那一週的星期日。 */
function _rpCells(view) {
  const first = _rpMonth(view);
  const start = new Date(first);
  start.setDate(1 - first.getDay());
  return Array.from({ length: 42 }, (_, i) => {
    const d = new Date(start);
    d.setDate(start.getDate() + i);
    return d;
  });
}

/** 今天（當地時間的 00:00）。未來的日期不給選——這個區間篩的是**已經發生過的**
 *  session（建立時間／結束時間），挑未來查不到任何東西，只會讓人以為是壞了。 */
const _rpToday = () => _rpDay(new Date());
const _rpFuture = (d) => _rpDay(d) > _rpToday();

function createRangePicker(mount, { onChange } = {}) {
  // draft 是面板裡正在編輯的值；committed 是已經送出去查詢的值。分開才做得到「按確定
  // 才生效」，也才能在取消展開時把改到一半的東西丟掉。
  const state = {
    from: null, to: null,          // committed
    dFrom: null, dTo: null,        // draft
    view: _rpMonth(new Date()),
    picking: "from", hover: null, open: false,
  };

  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "picker__button rangepick__trigger";
  btn.setAttribute("aria-haspopup", "dialog");
  btn.setAttribute("aria-expanded", "false");
  btn.dataset.testid = "range-trigger";

  const panel = document.createElement("div");
  panel.className = "rangepick__panel";
  panel.setAttribute("role", "dialog");
  panel.setAttribute("aria-label", "選擇時間範圍");
  panel.hidden = true;
  panel.dataset.testid = "range-panel";

  mount.className = "picker rangepick";
  mount.append(btn, panel);

  function renderButton() {
    const { from, to } = state;
    const label = (from || to)
      ? `${from ? `${_rpYmd(from)} ${_rpHm(from)}` : "不限"}`
        + ` → ${to ? `${_rpYmd(to)} ${_rpHm(to)}` : "不限"}`
      : "點此指定區間";
    btn.innerHTML = `<i class="picker__icon fa-solid fa-calendar-days"></i>`
      + `<span class="rangepick__value" data-empty="${from || to ? 0 : 1}">${esc(label)}</span>`
      + `<i class="picker__caret fa-solid fa-chevron-down"></i>`;
  }

  /** 目前要拿來畫「範圍」的兩端：終點還沒定時用游標懸停的那天預覽。 */
  function span() {
    const a = state.dFrom;
    const b = state.dTo || (state.picking === "to" ? state.hover : null);
    if (!a || !b) return [null, null];
    return a <= b ? [a, b] : [b, a];
  }

  function monthHtml(view, side) {
    // 只放「這一格屬於哪一天、是不是鄰月」這種不會變的東西。選取狀態由 paintDays()
    // 事後套上去——原因見 paintDays 的說明。
    const cells = _rpCells(view).map((d) => {
      const other = d.getMonth() !== view.getMonth() ? " is-other" : "";
      const gone = _rpFuture(d) ? " is-disabled" : "";
      return `<button type="button" class="rangepick__day${other}${gone}"
                      data-day="${_rpYmd(d)}"${gone ? " disabled" : ""}
                      tabindex="-1">${d.getDate()}</button>`;
    }).join("");
    // 上一月/下一月只放在對應的那一側：兩個月曆是連動的（右邊永遠是左邊 +1），
    // 兩側都放前後鍵會讓人以為可以各自獨立翻。
    const atMax = _rpMonth(state.view).getTime() >= _rpMaxView().getTime();
    const nav = side === "left"
      ? `<button type="button" class="rangepick__nav" data-move="-12" aria-label="上一年"
                 data-testid="range-prev-year"><i class="fa-solid fa-angles-left"></i></button>
         <button type="button" class="rangepick__nav" data-move="-1" aria-label="上個月"
                 data-testid="range-prev-month"><i class="fa-solid fa-angle-left"></i></button>`
      : "";
    const nav2 = side === "right"
      ? `<button type="button" class="rangepick__nav" data-move="1" aria-label="下個月"
                 data-testid="range-next-month" ${atMax ? "disabled" : ""}>
           <i class="fa-solid fa-angle-right"></i></button>
         <button type="button" class="rangepick__nav" data-move="12" aria-label="下一年"
                 data-testid="range-next-year" ${atMax ? "disabled" : ""}>
           <i class="fa-solid fa-angles-right"></i></button>`
      : "";
    return `
      <div class="rangepick__cal">
        <div class="rangepick__calhead">
          <span class="rangepick__navs">${nav}</span>
          <span class="rangepick__month">${view.getFullYear()} 年 ${view.getMonth() + 1} 月</span>
          <span class="rangepick__navs">${nav2}</span>
        </div>
        <div class="rangepick__dow">${_RP_DOW.map((d) => `<span>${d}</span>`).join("")}</div>
        <div class="rangepick__grid">${cells}</div>
      </div>`;
  }

  function hint() {
    if (!state.dFrom && !state.dTo) return "點一下選起點，再點一下選終點";
    if (state.picking === "to" && !state.dTo) return "再點一下選終點（選反了會自動對調）";
    return "可再點一下重新選，或直接改上方的日期與時間";
  }

  /* 把選取狀態塗到**已經存在**的格子上。
   *
   * ⚠ 這件事不能靠重新產生 HTML。滑鼠移過日期時會即時預覽區間，若那時把面板整段
   *   innerHTML 重建，游標底下的那顆按鈕就被換成新節點——mousedown 落在舊節點、
   *   mouseup 落在新節點，瀏覽器**不會**產生 click，使用者要點兩下才選得到終點
   *   （使用者回報，2026-07-26）。同樣的重建也讓 hover 每次都重排整個面板。
   *   所以：骨架只在展開與換月時建，其餘一律就地改 class 與 input 的 value。
   */
  function paintDays() {
    const [lo, hi] = span();
    const today = new Date();
    for (const el of panel.querySelectorAll("[data-day]")) {
      const [y, m, dd] = el.dataset.day.split("-").map(Number);
      const d = new Date(y, m - 1, dd);
      const edge = _rpSameDay(d, state.dFrom) || _rpSameDay(d, state.dTo);
      el.classList.toggle("is-today", _rpSameDay(d, today));
      el.classList.toggle("is-edge", edge);
      el.classList.toggle("is-in",
        !edge && !!(lo && hi) && _rpDay(d) > _rpDay(lo) && _rpDay(d) < _rpDay(hi));
    }
    const put = (key, v) => {
      const el = panel.querySelector(`[data-edit="${key}"]`);
      // 正在打字的欄位不要被蓋掉（改日期會連動到這裡）
      if (el && el !== document.activeElement) el.value = v;
    };
    put("from-date", state.dFrom ? _rpYmd(state.dFrom) : "");
    put("from-time", state.dFrom ? _rpHm(state.dFrom) : "");
    put("to-date", state.dTo ? _rpYmd(state.dTo) : "");
    put("to-time", state.dTo ? _rpHm(state.dTo) : "");
    const h = panel.querySelector(".rangepick__hint");
    if (h) h.textContent = hint();
  }

  function renderPanel() {
    panel.innerHTML = `
      <div class="rangepick__heads">
        <label class="rangepick__head">
          <input class="input input--sm" type="date" data-edit="from-date"
                 max="${_rpYmd(new Date())}"
                 data-testid="range-from-date" value="${state.dFrom ? _rpYmd(state.dFrom) : ""}">
          <input class="input input--sm" type="time" data-edit="from-time"
                 data-testid="range-from-time" value="${state.dFrom ? _rpHm(state.dFrom) : ""}">
        </label>
        <i class="rangepick__arrow fa-solid fa-arrow-right-long" aria-hidden="true"></i>
        <label class="rangepick__head">
          <input class="input input--sm" type="date" data-edit="to-date"
                 max="${_rpYmd(new Date())}"
                 data-testid="range-to-date" value="${state.dTo ? _rpYmd(state.dTo) : ""}">
          <input class="input input--sm" type="time" data-edit="to-time"
                 data-testid="range-to-time" value="${state.dTo ? _rpHm(state.dTo) : ""}">
        </label>
      </div>
      <div class="rangepick__cals">
        ${monthHtml(state.view, "left")}
        ${monthHtml(_rpAddMonths(state.view, 1), "right")}
      </div>
      <div class="rangepick__foot">
        <span class="rangepick__hint">${esc(hint())}</span>
        <button type="button" class="btn" data-act="clear" data-testid="range-clear">
          <i class="fa-solid fa-eraser"></i> 清除</button>
        <button type="button" class="btn btn--primary" data-act="ok" data-testid="range-ok">
          <i class="fa-solid fa-check"></i> 確定</button>
      </div>`;
    paintDays();
  }

  /* 點某一天。時間部分沿用已經設過的；沒設過就給「整天」的兩端——挑 7/19 到 7/26 時
     多數人要的是那兩天的全部，而不是 00:00 到 00:00（會少掉最後一天）。 */
  function pickDay(ymd) {
    const [y, m, d] = ymd.split("-").map(Number);
    const keep = (base, hh, mm) => new Date(y, m - 1, d,
      base ? base.getHours() : hh, base ? base.getMinutes() : mm, 0, 0);
    if (state.picking === "from" || (state.dFrom && state.dTo)) {
      state.dFrom = keep(state.dFrom, 0, 0);
      state.dTo = null;
      state.picking = "to";
    } else {
      state.dTo = keep(state.dTo, 23, 59);
      // 選反了就對調，不要丟掉他的第二次點擊、也不要跳錯誤訊息
      if (state.dFrom && state.dTo < state.dFrom) {
        const a = state.dFrom;
        state.dFrom = new Date(state.dTo.getFullYear(), state.dTo.getMonth(),
                               state.dTo.getDate(), 0, 0, 0, 0);
        state.dTo = new Date(a.getFullYear(), a.getMonth(), a.getDate(), 23, 59, 0, 0);
      }
      state.picking = "from";
    }
    state.hover = null;
    paintDays();
  }

  /** 上方輸入框改動：日期與時間分開兩欄，任一欄改了都要組回同一個 Date。 */
  function editField(which, kind, value) {
    const cur = which === "from" ? state.dFrom : state.dTo;
    let next = null;
    if (kind === "date") {
      if (value) {
        const [y, m, d] = value.split("-").map(Number);
        next = new Date(y, m - 1, d, cur ? cur.getHours() : (which === "from" ? 0 : 23),
                        cur ? cur.getMinutes() : (which === "from" ? 0 : 59), 0, 0);
      }
    } else {
      // 只有時間、沒有日期時無從組出一個時刻——先挑日期再說，不要自作主張補今天
      if (!cur || !value) return;
      const [hh, mm] = value.split(":").map(Number);
      next = new Date(cur.getFullYear(), cur.getMonth(), cur.getDate(), hh, mm, 0, 0);
    }
    // max 屬性只擋得住原生的日期選擇器，直接打字仍然送得進未來的日期
    if (next && _rpFuture(next)) next = new Date();
    if (which === "from") state.dFrom = next;
    else state.dTo = next;
    state.picking = state.dFrom && !state.dTo ? "to" : "from";
    // 跳到別的月份才需要重建骨架（格子換了一批）；同一個月就地重畫就好
    const wantView = next ? _rpMonth(next) : state.view;
    if (wantView.getTime() !== state.view.getTime()) {
      state.view = wantView;
      renderPanel();
    } else {
      paintDays();
    }
  }

  /* 左邊那個月最多只能到「上個月」，因為右邊永遠是它 +1——這樣右邊剛好停在本月，
     不會出現一整面全部反灰的未來月份。 */
  const _rpMaxView = () => _rpAddMonths(_rpMonth(new Date()), -1);
  const clampView = (v) => (v > _rpMaxView() ? _rpMaxView() : v);

  function open() {
    if (mount.closest('[data-disabled="1"]')) return;
    state.open = true;
    state.dFrom = state.from;
    state.dTo = state.to;
    state.picking = "from";
    state.hover = null;
    // 展開時對齊到已選區間的月份；沒選過就讓「本月」落在右邊那一格
    state.view = clampView(_rpMonth(state.from || _rpAddMonths(new Date(), -1)));
    renderPanel();
    panel.hidden = false;
    btn.setAttribute("aria-expanded", "true");
    place();
    window.addEventListener("scroll", onScroll, { capture: true });
    window.addEventListener("resize", close);
  }

  function close() {
    state.open = false;
    panel.hidden = true;
    btn.setAttribute("aria-expanded", "false");
    window.removeEventListener("scroll", onScroll, { capture: true });
    window.removeEventListener("resize", close);
  }

  // 與 picker 同一套：捲動時**重新定位而不是關掉**（理由見 createPicker.onScroll）
  const place = () => anchorPanel(btn, panel, { mount });
  function onScroll(e) {
    if (panel.contains(e.target)) return;
    const r = btn.getBoundingClientRect();
    if (r.bottom < 0 || r.top > window.innerHeight) close();
    else place();
  }

  function commit() {
    state.from = state.dFrom;
    state.to = state.dTo;
    renderButton();
    close();
    mount.dispatchEvent(new CustomEvent("change", { detail: value(), bubbles: true }));
    if (onChange) onChange(value());
  }

  const value = () => ({
    from: state.from ? _rpIso(state.from) : "",
    to: state.to ? _rpIso(state.to) : "",
  });

  btn.addEventListener("click", () => (state.open ? close() : open()));
  panel.addEventListener("click", (e) => {
    const nav = e.target.closest("[data-move]");
    if (nav) {
      state.view = clampView(_rpAddMonths(state.view, Number(nav.dataset.move)));
      renderPanel();
      return;
    }
    const day = e.target.closest("[data-day]");
    if (day) { pickDay(day.dataset.day); return; }
    const act = e.target.closest("[data-act]");
    if (!act) return;
    if (act.dataset.act === "clear") { state.dFrom = state.dTo = null; state.picking = "from"; commit(); }
    else if (act.dataset.act === "ok") commit();
  });
  // 懸停預覽：只在「已定起點、還沒定終點」時有意義
  panel.addEventListener("mouseover", (e) => {
    const day = e.target.closest("[data-day]");
    if (!day || state.picking !== "to" || !state.dFrom) return;
    const [y, m, d] = day.dataset.day.split("-").map(Number);
    const next = new Date(y, m - 1, d);
    if (_rpSameDay(next, state.hover)) return;    // 同一天不必重畫（滑過整格會觸發很多次）
    state.hover = next;
    paintDays();
  });
  panel.addEventListener("change", (e) => {
    const el = e.target.closest("[data-edit]");
    if (!el) return;
    const [which, kind] = el.dataset.edit.split("-");
    editField(which, kind, el.value);
  });
  btn.addEventListener("keydown", (e) => {
    if (["Enter", " ", "ArrowDown"].includes(e.key) && !state.open) { e.preventDefault(); open(); }
  });
  panel.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { e.preventDefault(); close(); btn.focus(); }
  });
  /* 點面板外面就收起來。
   *
   * ⚠ **必須用捕獲階段**。冒泡階段的話，等事件走到 document 時，面板裡被點的那顆日期
   *   按鈕早就被 renderPanel() 的 innerHTML 重建掉了——節點已不在 DOM 裡，
   *   `mount.contains(e.target)` 於是回 false，面板每點一天就自己關掉一次
   *   （真瀏覽器實測 2026-07-26；純看程式碼看不出來，因為兩個 handler 各自都是對的）。
   *   捕獲階段在事件抵達目標**之前**跑，那時 DOM 還是完整的。 */
  document.addEventListener("click", (e) => {
    if (state.open && !mount.contains(e.target)) close();
  }, true);

  renderButton();
  return {
    get value() { return value(); },
    /** 從網址填回來（重新整理／別人分享的連結）。不觸發 change。 */
    set value(v) {
      const parse = (s) => {
        if (!s) return null;
        const d = new Date(s);
        return Number.isNaN(d.getTime()) ? null : d;
      };
      state.from = parse(v && v.from);
      state.to = parse(v && v.to);
      renderButton();
    },
  };
}


/* ── 開關：二選一的選項不該用下拉 ──────────────────────────────────────────────
   下拉是「從 N 個裡挑一個」的元件；只有開與關兩種狀態時，它讓使用者多按一次、多讀
   一份選單，卻沒有多給任何資訊。開關把狀態直接畫在畫面上——不必展開就知道是開是關。

   value 介面與 createPicker 一致（get/set value），所以表單讀值那邊完全不必改。
   off/on 是**值**而非布林：網路能力的兩端是 restricted / unrestricted，不是 true/false。 */
function createSwitch(mount, { off, on, initial, offLabel, onLabel, offIcon, onIcon,
                                hint, name }) {
  const state = { value: initial ?? off };
  mount.className = "switch";
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "switch__control";
  btn.setAttribute("role", "switch");
  // 沒有這個名字，螢幕閱讀器只會唸出「switch，已勾選」——聽的人無從得知這是網路能力、
  // 流量錄製還是 telemetry。旁邊那行狀態文字對它來說只是不相干的兄弟節點。
  btn.setAttribute("aria-label", name || mount.id || "開關");
  // ⚠ DOM 只建一次，之後只改屬性與文字。**不可以在 render 裡重設 innerHTML**：那樣每次
  //   切換都是全新的節點，沒有起始狀態可以過渡，瀏覽器會直接套最終值——CSS 上明明寫了
  //   transition，畫面卻是瞬間跳過去。
  btn.innerHTML = '<span class="switch__track"><span class="switch__thumb">' +
    '<i class="switch__icon fa-solid"></i></span></span>';
  const icon = btn.querySelector(".switch__icon");
  const text = document.createElement("span");
  text.className = "switch__label";
  text.innerHTML = '<span class="switch__text"></span><span class="switch__hint"></span>';
  const textMain = text.querySelector(".switch__text");
  const textHint = text.querySelector(".switch__hint");
  mount.append(btn, text);

  function render() {
    const isOn = state.value === on;
    btn.setAttribute("aria-checked", String(isOn));
    // 狀態同步到外層，好讓 CSS 選得到標籤那側的元素（它們是按鈕的兄弟，選不到 aria-checked）
    mount.dataset.on = String(isOn);
    // 只換圖示的那一個 class，其餘保留（重建 <i> 會讓圖示閃一下）
    icon.className = `switch__icon fa-solid ${(isOn ? onIcon : offIcon) || "fa-circle"}`;
    textMain.textContent = isOn ? onLabel : offLabel;
    // 文字**一律**填入，靠 CSS 淡入淡出；清成空字串的話它是瞬間消失，過渡就沒了
    textHint.textContent = hint || "";
  }

  function toggle() {
    state.value = state.value === on ? off : on;
    render();
    mount.dispatchEvent(new CustomEvent("change", {
      detail: { value: state.value }, bubbles: true,
    }));
  }

  btn.addEventListener("click", toggle);
  text.addEventListener("click", toggle);   // 標籤也可點：命中區大一點，不必瞄準那顆小圓
  btn.addEventListener("keydown", (e) => {
    // role=switch 的鍵盤約定：空白/Enter 切換；方向鍵是「明確指定開或關」而非切換
    if (e.key === " " || e.key === "Enter") { e.preventDefault(); toggle(); }
    else if (e.key === "ArrowRight" && state.value !== on) { e.preventDefault(); toggle(); }
    else if (e.key === "ArrowLeft" && state.value !== off) { e.preventDefault(); toggle(); }
  });

  render();
  return { get value() { return state.value; }, set value(v) { state.value = v; render(); } };
}

/* ── 終端抽屜：從右側滑入的 ttyd ───────────────────────────────────────────────
 *
 * iframe 的 src 一律是 nginx 的 `/session/<sid>/`，**不是** POST /view 回傳的
 * direct_url：後者是另一個 origin（127.0.0.1:41xxx），會被本站 CSP 的
 * `default-src 'self'` 直接擋掉，而且跨 origin 之後也讀不到 iframe 的狀態。
 * 所以直連模式（未走 nginx）不開抽屜，退回開新分頁——見 sessions.html 的呼叫端。
 *
 * ⚠ ttyd 帶 `-q`：最後一個 WebSocket 斷線它就自己 exit。抽屜關閉會拆掉 iframe＝斷線，
 * 所以「關掉再開」在抽屜上是常態而不是例外（開分頁時代不會有人這樣點）。三件事撐住它：
 *
 *   1. **重開時 ttyd 早就退乾淨了**——`views.list_views` 會丟掉 pid 已不存在的殘留列，
 *      POST /view 於是重新起一個。連 iframe 自己的重新整理都不會壞：nginx 的
 *      auth_request（`/api/auth/view`）在沒有存活 view 時會當場重建。
 *   2. **「程序還在、卻已經不服務」的收尾空檔**：實測打不到。斷開 WS 之後 0/5/20/60ms
 *      各重開一次，四次都拿到新的 pid、首頁都回 200。曾經為此在 list_views 加了一道
 *      port 探測，量完發現它擋掉的是 0 次、代價卻是每一個被代理的請求（每張 asset、
 *      每次 WS upgrade，auth_request 都會呼叫 list_views）多一次 TCP connect，所以拿掉。
 *      真的退化時的症狀是 iframe 顯示 502——看得見，不需要事先偵測。
 *   3. **「開新分頁」刻意不關抽屜**：先斷 iframe 再讓新分頁連，中間那段一個 client 都
 *      沒有，ttyd 會在新分頁連上之前就退出。兩邊同時連著才是安全的交接。
 */
let openDrawer = null;      // 同時只留一個抽屜；再開一個就先把舊的收掉

function terminalDrawer({ sid, label, path, flavor = null, trigger = null }) {
  if (openDrawer) openDrawer.close();

  // 關閉時把焦點送回那一列的「終端」鍵。**不能用 document.activeElement 記住原本的節點**：
  // 呼叫端進 handler 第一件事是 `btn.disabled = true`，而停用一個正被聚焦的元素會立刻
  // blur 它——走到這裡時 activeElement 早就是 <body> 了（2026-07-26 實測）。
  // 也不能記住節點本身：列表每 15 秒重繪一次，而看終端超過 15 秒是常態，那顆節點多半
  // 已經被換掉。所以用 sid 重查，查不到才退回呼叫端傳進來的原節點。
  const restoreFocus = () => {
    const back = document.querySelector(`[data-act="open"][data-id="${CSS.escape(sid)}"]`)
              || (trigger?.isConnected ? trigger : null);
    back?.focus();
  };

  const wrap = document.createElement("div");
  wrap.className = "drawer";
  wrap.dataset.testid = "drawer";
  // 目前開的是哪一場。畫面上用不到，但診斷時只有這裡問得到——沒有它就得回頭去比對
  // 列表按鈕的 data-id，而抽屜開著的時候列表是 inert 的。
  wrap.dataset.sid = sid;
  wrap.innerHTML = `
    <!-- 遮罩用自己的 act 名稱：它與關閉鍵共用 data-act="close" 的時候，
         下面那句 querySelector('[data-act="close"]') 會先選到這個不可聚焦的 div，
         focus() 靜靜地什麼都沒做，焦點一直留在抽屜背後（review 2026-07-26 實測）。 -->
    <div class="drawer__scrim" data-act="scrim"></div>
    <section class="drawer__panel" role="dialog" aria-modal="true"
             aria-label="終端：${esc(label || sid)}">
      <header class="drawer__bar">
        <div class="drawer__id">
          <i class="fa-solid fa-terminal" aria-hidden="true"></i>
          <span class="drawer__title">${esc(label || sid)}</span>
          <code class="drawer__sid">${esc(sid)}</code>
          <!-- 這個終端是哪一顆 ttyd 在服務。兩顆（C / Rust）是同一個 UI，肉眼分不出來，
               而出問題時「你看到的是哪一版」是第一個要問的問題。值來自這個 view 的 DB
               記錄——**不是**這個人現在的偏好：改偏好不會換掉已經在跑的 ttyd。
               舊的 view 記錄沒有這個值，那就不顯示（不知道就別猜）。 -->
          ${flavor ? `<span class="drawer__bin tip" data-testid="drawer-bin"
                data-tip="這個終端由 ${esc(flavor)} 版 ttyd 提供。要換另一顆請到「設定」——只影響之後開的終端，不會換掉正在跑的。"
                >${esc(flavor)}</span>` : ""}
        </div>
        <div class="drawer__tools">
          <!-- ⚠ 三條提示做成**輪播**，不是並排。標題列的寬度要分給 session 名稱、字級、
               新分頁、關閉，三條並排時最先被擠掉的是名稱（工具區 flex:none），而且窄視窗
               下原本得靠 media query 一條一條藏——藏掉的那條就等於不存在。輪播讓每一條
               都輪得到，佔的寬度只有一條。
               ⚠ 疊法用 grid（全部放進同一格 grid-area: 1/1），不是 absolute：容器的寬高
               ——⚠ 這段註解在 template literal 裡，**不可以用反引號**（會把字串截斷）。
                 因此等於**最寬/最高那一條**，輪播時版面不會每 6 秒抽動一次。
               ⚠ hover／focus 要暫停：裡面有可點的複製鍵，會動的點擊目標很惡劣。 -->
          <div class="drawer__hints" data-testid="drawer-hints"
               role="group" aria-label="終端使用提示">
            <!-- 容器一收，cwd 底下寫的東西就沒了——這件事在終端裡沒有任何線索，而代價是
                 使用者辛苦產出的檔案。所以「哪個目錄留得住」要常駐。
                 路徑由後端給（config.DATA_BIND 是 SSOT）。
                 ⚠ 做成 button 不是 span：它可以點（複製路徑），而可點的東西必須是原生
                   可聚焦、可用 Enter 觸發的元素，否則鍵盤使用者按不到。 -->
            ${persistDir() ? `
            <button class="drawer__hint drawer__hint--persist tip tip--right tip--wide"
                    type="button" data-act="copy-persist" data-copy="${esc(persistDir())}"
                    data-testid="drawer-persist"
                    data-tip="只有這個目錄的內容留得到下一場（換一顆 container 也還在），而且是你個人的，別人看不到。工作目錄與家目錄其他地方都是容器的可寫層，session 一結束就消失。點一下複製路徑。">
              <i class="fa-solid fa-box-archive"></i>
              <!-- ⚠ 複製圖示緊貼在**路徑後面**、而且與路徑包成同一組。被複製的只有路徑：
                   圖示放在整句尾巴會像是整句都會被複製，而與「會留著」等距則歸屬不明。
                   組內貼緊、組外拉開，一眼看得出它是屬於哪個東西的。 -->
              <span class="drawer__copy"
                ><code>${esc(persistDir())}</code
                ><i class="fa-regular fa-copy drawer__copy-icon" aria-hidden="true"></i
              ></span>
              會留著
            </button>` : ""}
            <!-- TUI 會開滑鼠追蹤（Claude Code 實測 ?1000/?1002/?1003/?1006 全開），一開啟，
                 拖曳就被當成應用程式的滑鼠事件送進 TUI，終端不再拿它來選取。要選字得按修飾鍵
                 繞過追蹤——ttyd 那邊已設 macOptionClickForcesSelection。這件事沒有任何畫面
                 線索，不寫出來只能靠人猜。 -->
            <span class="drawer__hint tip tip--right tip--wide" data-testid="drawer-mouse"
                  data-tip="終端把滑鼠事件收走了，所以直接拖曳不會選字。按住修飾鍵拖曳＝選取，放開就已經複製（copyOnSelect）。貼上用 ⌘V／Ctrl+Shift+V。">
              <i class="fa-solid fa-arrow-pointer"></i> ⌥/Alt 拖曳選字即複製</span>
          </div>
          <!-- 字級。⌘/Ctrl +- 在這裡不好用：macOS 根本沒綁 Ctrl+±（是 ⌘±），而焦點一旦
               進了終端，那組鍵會被 xterm 收走送進 TUI。瀏覽器縮放又是整頁一起縮。
               父頁面讀得到 iframe 的 window.term（同源），所以直接調它的 fontSize。 -->
          <span class="drawer__zoom" role="group" aria-label="終端字級">
            <button class="icon-btn" data-act="font-" data-testid="drawer-font-dec"
                    aria-label="縮小終端字級" title="縮小字級">
              <i class="fa-solid fa-minus"></i></button>
            <!-- 把實際數值寫出來，不要只有加減：使用者要知道現在是幾 px 才調得準，
                 也才看得出已經頂到上下限（到界時該側的按鈕會 disabled）。 -->
            <output class="drawer__zoom-value" data-testid="drawer-font-value"
                    aria-live="polite">—</output>
            <button class="icon-btn" data-act="font+" data-testid="drawer-font-inc"
                    aria-label="放大終端字級" title="放大字級">
              <i class="fa-solid fa-plus"></i></button>
          </span>
          <button class="btn" data-act="pop">
            <i class="fa-solid fa-arrow-up-right-from-square"></i> 新分頁</button>
          <button class="icon-btn" data-act="close" data-testid="drawer-close"
                  aria-label="關閉終端" title="關閉">
            <i class="fa-solid fa-xmark"></i></button>
        </div>
      </header>
      <div class="drawer__body">
        <iframe class="drawer__frame" data-testid="drawer-frame"
                title="終端：${esc(label || sid)}"></iframe>
        <p class="drawer__pending" data-testid="drawer-pending">連線中…</p>
      </div>
    </section>`;
  document.body.appendChild(wrap);

  const frame = wrap.querySelector(".drawer__frame");
  const pending = wrap.querySelector(".drawer__pending");
  frame.addEventListener("load", () => {
    // load 事件不等於「連上終端了」。view 若在抽屜開著的期間被回收（idle 回收、
    // reconciler 清掉），nginx 的 auth_request 會失敗 → 302 到 `/`，而 `/` 是 Flask 的
    // 回應、帶著 `X-Frame-Options: DENY`，瀏覽器拒絕在框內算繪——但 load 照樣觸發。
    // 只看 load 的話「連線中…」會消失、換成一片白，什麼線索都沒有。
    // iframe 同源，讀得到它現在停在哪；不在 /session/ 底下就是被導走了。
    let inside = true;
    try {
      inside = frame.contentWindow.location.pathname.startsWith("/session/");
    } catch { inside = false; }       // 讀不到＝已經不是同源，同樣不是我們的終端
    if (!inside) {
      pending.hidden = false;
      pending.textContent = "這個終端已經結束（session 可能被回收或終止了）。關掉這個抽屜再開一次。";
      return;
    }
    pending.hidden = true;
    attachSizeSync();
  });

  /* ── 讓容器裡的 TTY 跟著抽屜的大小走 ────────────────────────────────────────
   * ttyd **有**把視窗大小送出去，只是送給它自己的子程序——而那個子程序是
   * `docker attach`，它不會把大小轉給容器裡的 TTY。所以預設情況是：xterm 依 iframe
   * 排到 181 欄，容器裡的 TUI 仍照建立時的 140 欄畫，右邊空一大塊
   * （實測 2026-07-26：改視窗把 xterm 從 162 欄變成 240 欄，PTY 全程都是 140×40）。
   *
   * 補的這一步是：把 xterm 量到的欄列數用 /resize 送去，docker 改容器 TTY 的大小、
   * 核心送 SIGWINCH，TUI 自己重繪（驗過：181×54 送出去之後畫面最長那行就是 181 字元）。
   * xterm 的 Terminal 物件 ttyd 掛在 window.term 上，而 iframe 同源，父頁面讀得到。
   *
   * ⚠ 尺寸是**整個 session 共用**的——容器只有一個 TTY。人在這裡把視窗拉寬，
   *   其他在看同一場的人看到的版面就跟著變。這是「同一個 session 多個觀看者」的必然，
   *   不是可以繞開的實作細節。
   */
  /* 尺寸同步的診斷開關。平時完全安靜；要查「送出去的欄列數為什麼跟畫面對不上」時：
   *     localStorage.setItem("claude-pty:debug-size", "1")   // 關掉：removeItem
   * 之後每一次「量到的尺寸變化」與「真的送出去的值」都會印在 console，帶時間軸。
   * 這類問題的本質是時序（fit 落在我們量完之後），靠肉眼看畫面永遠問不出來——2026-07-27
   * 就是靠比對「送出 36×150」與「畫面上 112×42」才定位到的。 */
  const sizeDebug = (() => {
    let on = false;
    try { on = localStorage.getItem("claude-pty:debug-size") === "1"; } catch { /* 隱私模式 */ }
    const t0 = performance.now();
    return on ? (...a) => console.log(`[size +${Math.round(performance.now() - t0)}ms]`, ...a)
              : () => {};
  })();

  /* ── 畫布與 CSS 尺寸脫節時，字會被畫成錯的大小 ──────────────────────────────
   * xterm 的 WebGL 算繪器把畫布的 backing store 開成「CSS 尺寸 × devicePixelRatio」，
   * 但字要畫多大是另外從 `dimensions` 算的。兩邊的 dpr 只要對不上，畫出來的字就整體
   * 縮放錯誤——實測 2026-07-27：抽屜一開，畫布是 1728×1248 而 CSS 只有 864×624，
   * devicePixelRatio 是 1，於是每個字只佔顯示上的一半，看起來就是「字太小、版面不對」。
   * 而且是**從 ttyd 自己的第一次算繪就已經如此**（font 13 時畫布 1554×1230 / CSS 777），
   * 不是我們改字級造成的。
   *
   * 使用者回報的「每次都要手動按一下 +/- px，大小才會是對的」就是在修這件事。
   *
   * ⚠ 這四種都試過，**沒有一種有用**（同一個 session 各重開一次量的）：
   *     `_renderService.handleDevicePixelRatioChange()`、`term.clearTextureAtlas()`、
   *     往 iframe 丟 resize 事件——畫布完全不動；
   *     `term.resize()`——畫布跟著變（1728→1704）但 2 倍的比例原封不動。
   *   只有**真的改變字級**會讓 xterm 重新量字並重建畫布。所以這裡就照使用者手動做的
   *   那件事做：+1 再還原。同值指派 xterm 會直接忽略，一定要先跳開再回來。
   *
   * 兩次指派同步做完（實測 5ms），中間不重新 fit，欄列數不變（72×26 前後相同），
   * 所以不會多送一次 /resize，也不會動到使用者存下來的字級。
   */
  const healGlyphScale = (term) => {
    const doc = frame.contentDocument;
    const size = term.options?.fontSize;
    if (!doc || !size) return;
    const dpr = frame.contentWindow.devicePixelRatio || 1;
    const broken = [...doc.querySelectorAll(".xterm canvas")].some((c) => {
      const cssW = parseFloat(c.style.width);
      // 還沒排版好的畫布不算壞——那是「還沒到」，不是「錯了」
      return cssW > 0 && c.width > 0 && Math.abs(c.width - cssW * dpr) > 1;
    });
    if (!broken) return;
    sizeDebug("畫布與 CSS 尺寸對不上，重新量一次字級", size);
    // try/finally：中間那一步若拋了，字級不可以停在 +1——那會是使用者永遠改不回來的
    // 大小，比原本的 bug 還糟。
    try { term.options.fontSize = size + 1; } finally { term.options.fontSize = size; }
  };

  let sizeTimer = null;
  // 這一輪要不要順便叫 TUI 重畫。做成「黏著的旗標」而不是參數：debounce 會把多次呼叫
  // 併成一次送出，若開啟時那次帶著 redraw 卻被後續的 onResize 併掉，重繪就悄悄不見了。
  let wantRedraw = false;
  const syncSize = ({ redraw = false } = {}) => {
    wantRedraw = wantRedraw || redraw;
    // 拖視窗、連按字級都會連續觸發，debounce 之後只送最後一次。
    // ⚠ 尺寸要在**送出的當下**才讀，不能在排程時就抓走：連按 8 次縮小時 onResize 會
    //   一路回報中途值，抓走的那個會蓋掉最終值——實測 xterm 已經是 254×82、PTY 卻停在
    //   158×43，畫面右邊一大塊 TUI 不會畫（2026-07-26）。
    clearTimeout(sizeTimer);
    sizeTimer = setTimeout(() => {
      const term = frame.contentWindow?.term;
      if (!term) return;
      // 修在讀尺寸**之前**：萬一哪天重量字級也改了欄列數，要送出去的是修好之後的值。
      // 掛在這裡而不是另外開一個計時器或監聽器，是因為這條路本來就涵蓋了全部的時機：
      // 開抽屜必定走一次，之後改字級、拖視窗、瀏覽器縮放、把視窗拖到另一個 dpi 的螢幕
      // ——每一種都會改變 cell 大小 → xterm 重新 fit → onResize → 走到這裡。
      healGlyphScale(term);
      const { cols, rows } = term;
      const body = { rows, cols, redraw: wantRedraw };
      sizeDebug("送出", `${cols}x${rows}`, "redraw=", wantRedraw,
                "iframe=", `${frame.clientWidth}x${frame.clientHeight}`,
                "fontSize=", term.options?.fontSize);
      // ⚠ 旗標要等**送出成功**才清。抽屜剛開的那一刻 session 可能還在 creating，正是這
      //   一發最容易失敗的時候；先清掉的話重繪就永遠不會再有第二次機會，而失敗本身被
      //   下面的 .catch 靜靜吞掉——那就違背了它做成「黏著旗標」的整個理由。
      //
      // ⚠ 曾經想改成「尺寸也不再變才清」來對付「第一發送的是還沒 fit 的舊尺寸」——
      //   **那是錯的、而且不必要**（2026-07-27，被 e2e_drawer 當場打紅）：
      //     不必要——第二發若是**不同**尺寸，docker resize 本來就會產生真的 SIGWINCH，
      //             TUI 自己就重畫了，不需要旗標。
      //     錯的——拖視窗期間每一發的尺寸都不同，那個條件永遠不成立，於是每一發都重複
      //           要求重繪。真正沒有 SIGWINCH 的是「尺寸沒變」那一種，而那一種伺服端
      //           自己判斷得出來（見 sessions.resize 的 `unchanged`），不必前端猜。
      // 路徑片段用 encodeURIComponent（不是 esc——那是 HTML 逸出，用在網址上是錯的編碼）
      api(`/api/sessions/${encodeURIComponent(sid)}/resize`, { method: "POST", body })
        .then(() => { wantRedraw = false; })
        .catch(() => {});     // 純視覺，失敗不打擾使用者（session 可能剛好結束了）
    }, 300);
  };
  // ⚠ `load` 觸發時 ttyd 自己的 JS 還沒跑完，window.term 還不存在——直接取會拿到
  //   undefined 然後就再也不試了（實測：PTY 全程停在 80×24）。所以要等它出現。
  let sizePolls = 0;
  /* 字級：存起來，下次開抽屜（或開別的 session）沿用——每次都要重調很煩。
     改完要讓 xterm 重新 fit：ttyd 綁的是 window resize，所以往 iframe 丟一個 resize 事件
     即可，它會重算欄列數，接著 term.onResize 把新尺寸同步給容器的 TTY（見上）。 */
  const FONT_KEY = "claude-pty:term-font";
  // 夾在 8–32：再小讀不到、再大一行放不了幾個字，TUI 的版面會整個垮掉。
  const FONT_MIN = 8, FONT_MAX = 32;
  const fontValue = wrap.querySelector(".drawer__zoom-value");
  const showFont = (size) => {
    fontValue.textContent = `${size}px`;
    // 到界時把該側停用——不然按了沒反應，使用者不知道是壞了還是到頂了
    wrap.querySelector('[data-act="font-"]').disabled = size <= FONT_MIN;
    wrap.querySelector('[data-act="font+"]').disabled = size >= FONT_MAX;
  };
  const applyFont = (size) => {
    const term = frame.contentWindow?.term;
    if (!term) return;
    term.options.fontSize = size;
    localStorage.setItem(FONT_KEY, String(size));
    showFont(size);
    frame.contentWindow.dispatchEvent(new Event("resize"));
  };
  const bumpFont = (delta) => {
    const term = frame.contentWindow?.term;
    if (!term) return;
    const next = Math.min(FONT_MAX, Math.max(FONT_MIN, (term.options.fontSize || 14) + delta));
    if (next !== term.options.fontSize) applyFont(next);
  };

  const attachSizeSync = () => {
    const term = frame.contentWindow?.term;
    if (!term) {
      // 5 秒還沒出現就放棄：多半是 ttyd 換版不再掛 window.term，那就退回原本的行為
      // （終端仍可用，只是尺寸不跟著抽屜走），不要無限輪詢下去。
      if (++sizePolls < 50 && !closing) setTimeout(attachSizeSync, 100);
      return;
    }
    // ⚠ onResize 必須**先**註冊，再套字級。這行原本放在最後，於是 applyFont() 觸發的
    //   那一次 fit——也就是最關鍵的第一次——沒有人在聽：PTY 停在建立時的 140×40，
    //   而 xterm 已經照存下來的字級排成別的行數。字級偏大時 xterm 的列數比 PTY 少，
    //   TUI 照 40 列畫，下面幾列就落在看不見的地方，要手動按一次縮放才會正常
    //   （使用者回報「大多時候都是錯的」，2026-07-26）。
    sizeDebug("接上 onResize；此刻 term=", `${term.cols}x${term.rows}`,
              "iframe=", `${frame.clientWidth}x${frame.clientHeight}`);
    term.onResize((d) => {             // 值在 syncSize 裡才讀，見上
      sizeDebug("xterm 自己 fit 成", `${d.cols}x${d.rows}`,
                "iframe=", `${frame.clientWidth}x${frame.clientHeight}`);
      syncSize();
    });
    // localStorage 的值是使用者可以手改的，而且舊版本可能存過別的範圍——一律夾回界內，
    // 不是「合法才用」：存了 999 的話直接忽略會讓他永遠回不到自己調過的大小。
    const raw = parseInt(localStorage.getItem(FONT_KEY) || "", 10);
    const saved = Number.isFinite(raw)
      ? Math.min(FONT_MAX, Math.max(FONT_MIN, raw)) : null;
    if (saved !== null && saved !== term.options.fontSize) {
      applyFont(saved);
    } else {
      showFont(term.options.fontSize);
      /* ⚠ 字級不用改，**還是要逼一次 fit**——不然開啟時送出去的是「別的字級的」格數。
       *
       * ttyd 的第一次排版是用**它自己的**預設字級算的，之後才把字級套成使用者存的那個
       * 值，而**套字級不會順便重新 fit**。於是 `term.options.fontSize` 已經是 18，
       * `term.cols/rows` 還停在 13px 排出來的數字——兩者不同步，而 syncSize 讀的是後者。
       *
       * 實測 2026-07-31（iframe 904×650、存 18px）：開抽屜送出 112×42，那是 13px 的格數
       * （112×7.80≈874 塞得進 904）；但畫面用 18px 畫，`.xterm-screen` 撐成 1165×840，
       * 超出框 29%，TUI 下半截落在看不見的地方。正解是 84×32，而使用者得手動按一下
       * +/- px 才會回正——那正是「打開通常大小是錯的」。
       *
       * ⚠ `applyFont` 是**唯一**會 dispatch resize 去叫 ttyd 重新 fit 的地方，所以走進
       *   這個 else 就整條路都跳過了。2026-07-26 修過同型的一次（「onResize 必須先註冊」
       *   那段），但那次只補到 applyFont **有跑**的情況；「存的字級剛好等於目前字級」
       *   這條路上根本沒有 fit 可以被聽到，於是同一個症狀留到現在。
       * 送出的旗標由下面那發 syncSize({redraw:true}) 負責：這裡觸發的 fit 若真的改了
       * 欄列數，onResize 會排一發不帶 redraw 的，兩發會被 debounce 併成一發、旗標取聯集。 */
      frame.contentWindow.dispatchEvent(new Event("resize"));
    }
    // 不論上面有沒有觸發 fit，都以「目前的實際尺寸」對齊一次。syncSize 有 debounce，
    // 與 fit 造成的那次會合併成一發，不會多送。
    //
    // ⚠ 開啟時**一定**帶 redraw。尺寸剛好與上次相同是常態（同一個視窗、同一個字級），
    //   那種情況 docker resize 不會產生 SIGWINCH，TUI 於是沿用它上次畫的版面——而那個
    //   版面可能是別的尺寸留下的，看起來就是「下面的內容跑到看不見的地方」，要手動按
    //   一下縮放才會好（使用者回報）。後續因拖視窗/改字級觸發的同步不需要，那些本來
    //   就有真正的尺寸變化。
    syncSize({ redraw: true });
  };
  frame.src = path;

  // 先讓瀏覽器把「在畫面外」的起始狀態畫過一幀，再打開關——同一幀內設定 src 與
  // data-open 的話，transform 沒有起點可以過渡，抽屜會直接出現而不是滑進來。
  // 存下 handle：連點兩次時第二次會先 close() 第一個，那時這幀還沒跑，不取消的話
  // 它會把 data-open 設回去，已經在關閉中的抽屜又滑進來一次。
  const raf = requestAnimationFrame(() => wrap.dataset.open = "1");

  // 限定在工具列裡找：抽屜掛著 aria-modal="true"，螢幕閱讀器會把背景整片藏起來，
  // 焦點若還留在背景那顆「終端」按鈕上，鍵盤使用者等於在一個被宣告不存在的頁面裡按 Tab。
  wrap.querySelector('.drawer__tools [data-act="close"]').focus();
  // aria-modal 只影響螢幕閱讀器的虛擬游標，**不影響 Tab 順序**：背景的按鈕與連結
  // 全都還在 Tab 序裡，卻被 scrim 蓋住點不到。inert 才是同時處理兩者的那一個。
  const shell = document.querySelector(".shell");
  if (shell) shell.inert = true;

  // ⚠ 這裡**刻意沒有** Esc 關閉。ttyd 一載入就把焦點搶進終端（實測 activeElement 是
  // IFRAME），母頁面根本收不到 keydown——而那是對的：Esc 是 TUI 自己的鍵，Claude Code
  // 用它中斷。做成「還沒點進終端時 Esc 有效、點進去之後失效」比一律不接管更糟。
  // 代價要講明：進了終端之後鍵盤沒有回頭路（xterm 連 Tab 都吃掉），只能用滑鼠關。
  // ⚠ 宣告在 `close` **之前**：close 會清掉它，而兩者的距離夠遠，靠「呼叫時機剛好在
  //   宣告之後」撐著遲早會被下一個人改壞（那會是 TDZ 例外，而它發生在關閉抽屜時）。
  let hintTimer = null;
  let closing = false;
  const close = () => {
    if (closing) return;
    closing = true;
    cancelAnimationFrame(raf);
    clearTimeout(sizeTimer);      // 抽屜都關了就別再送尺寸
    clearInterval(hintTimer);     // 輪播同理——不清的話它會一直跑到頁面關掉為止
    delete wrap.dataset.open;
    if (openDrawer && openDrawer.el === wrap) openDrawer = null;
    if (shell) shell.inert = false;
    const panel = wrap.querySelector(".drawer__panel");
    // 動畫跑完才移除；prefers-reduced-motion 下 transition 被關掉、transitionend
    // 永遠不會來，所以另外掛一個 timeout 保底（少了它抽屜會永遠留在 DOM 上）。
    const finish = () => {
      clearTimeout(timer);
      panel.removeEventListener("transitionend", onEnd);
      wrap.remove();
    };
    // ⚠ 必須濾 target 與 propertyName：transitionend 會冒泡，而工具列那兩顆按鈕各有
    //   120ms 的 background 過渡。用滑鼠按下關閉鍵時，面板一滑走游標就離開了按鈕，
    //   hover-out 的過渡比面板的 240ms transform 早到——實測 179ms 就收到 .icon-btn 的
    //   background-color，抽屜在 182ms 被拆掉，滑到 75% 就整個消失（2026-07-26 實測）。
    const onEnd = (e) => {
      if (e.target === panel && e.propertyName === "transform") finish();
    };
    const timer = setTimeout(finish, 400);
    panel.addEventListener("transitionend", onEnd);
    restoreFocus();
  };

  /* 提示輪播。⚠ 只在**不只一條**時才轉——一條也在轉的話那不是輪播，是閃爍。 */
  const hintBox = wrap.querySelector(".drawer__hints");
  const hints = [...(hintBox?.querySelectorAll(".drawer__hint") || [])];
  if (hints.length > 1) {
    let at = 0;
    const show = (i) => hints.forEach((h, n) => {
      h.dataset.on = String(n === i);
      // ⚠ 沒露臉的那幾條要退出 Tab 序與無障礙樹。它們在畫面上是透明的，留著的話
      //   鍵盤使用者會 Tab 到一個看不見的複製鍵，螢幕閱讀器也會把三條一起唸出來。
      h.inert = n !== i;
      h.setAttribute("aria-hidden", String(n !== i));
    });
    show(0);
    const stop = () => { clearInterval(hintTimer); hintTimer = null; };
    const start = () => {
      stop();
      // ⚠ 抽屜關閉中就不要再起計時器。目前**不可達**（現有的關閉路徑上，pointerleave／
      //   focusout 都在 close 之前送達，我把每條都推過），但只要日後多一條關閉路徑
      //   （把 Esc 加回來、session 結束自動關），close 之後才到的 pointerleave 就會
      //   重啟一個掛在已經 detached 的 DOM 上的 interval，而且沒有人會再清它。
      //   一行的保險，換掉一整類「要靠事件順序才成立」的推理。
      if (closing) return;
      // 6 秒：短到三條都輪得到，長到讀得完一句。
      hintTimer = setInterval(() => show(at = (at + 1) % hints.length), 6000);
    };
    start();
    /* 滑鼠停著或鍵盤聚焦時暫停：裡面有可點的複製鍵，會自己跑掉的點擊目標很惡劣。
     *
     * ⚠ 兩個條件要**分開記**，不可以 enter→stop / leave→start 這樣直接對接。
     *   點過複製鍵之後焦點會留在它上面，這時把滑鼠移開就會恢復輪播——而輪播會把沒露臉
     *   的那幾條設成 `inert`，於是**使用者正聚焦的那顆按鈕變成 inert，焦點被瀏覽器收走**。
     *   代價是「點完就把滑鼠移開」時輪播會停著不動，直到焦點離開這一區為止。
     *   兩者相比，靜止的提示遠好過焦點在使用者腳下消失。 */
    let hovering = false;
    let focused = false;
    const settle = () => ((hovering || focused) ? stop() : start());
    hintBox.addEventListener("pointerenter", () => { hovering = true; settle(); });
    hintBox.addEventListener("pointerleave", () => { hovering = false; settle(); });
    hintBox.addEventListener("focusin", () => { focused = true; settle(); });
    hintBox.addEventListener("focusout", () => { focused = false; settle(); });
  }

  wrap.addEventListener("click", async (e) => {
    const act = e.target.closest("[data-act]")?.dataset.act;
    if (act === "close" || act === "scrim") close();
    if (act === "font+") bumpFont(1);
    if (act === "font-") bumpFont(-1);
    if (act === "copy-persist") {
      const btn = e.target.closest("[data-act='copy-persist']");
      const path = btn?.dataset.copy || "";
      try {
        await navigator.clipboard.writeText(path);
        // ⚠ toast 掛在 document.body（見 toast()），與抽屜同層而 z-index 100 > 90，
        //   所以蓋得住抽屜。抽屜把 `.shell` 設成 inert，但 toast 不在 .shell 裡，
        //   仍然點得到（e2e_drawer 有一條在守這件事）。
        toast("已複製路徑", "success", { body: path, duration: 3000 });
      } catch {
        // 非 HTTPS 或權限被拒時 clipboard API 不可用——把路徑講出來讓人自己選，
        // 總比一句「複製失敗」然後什麼都不能做好（同 account.html 既有的做法）。
        toast("無法自動複製", "warning",
              { body: `請手動輸入：${path}`, duration: 10000 });
      }
      return;
    }
    if (act === "pop") {
      window.open(path, "_blank", "noopener");
      toast("已在新分頁開啟", "info",
            { body: "抽屜刻意不關：先斷線再讓新分頁連，ttyd 會在中間就退出" });
    }
  });

  openDrawer = { el: wrap, close };
  return openDrawer;
}

/* ── 對話框：取代原生 confirm() / prompt()（外觀不受控、且被瀏覽器擋在頁面之外）── */
function dialog({ title, body, confirmText = "確定", danger = false, input = null,
                 confirmIcon = null, pre = null, preNoWrap = false, viewOnly = false,
                 wide = false }) {
  return new Promise((resolve) => {
    const wrap = document.createElement("div");
    wrap.className = "modal";
    wrap.innerHTML = `
      <div class="modal__box${wide ? " modal__box--screen" : ""}" role="dialog" aria-modal="true">
        <h2 class="modal__title">${esc(title)}</h2>
        <div class="modal__body">${esc(body || "")}</div>
        ${pre !== null ? `<pre class="modal__pre${preNoWrap ? " modal__pre--nowrap" : ""}"
             id="modal-pre"></pre>` : ""}
        ${input ? `<div class="field"><input class="input" id="modal-input"
             type="${esc(input.type || "text")}" maxlength="${esc(String(input.maxLength || 200))}"
             value="${esc(input.value || "")}" placeholder="${esc(input.placeholder || "")}"></div>` : ""}
        <div class="modal__actions">
          ${viewOnly ? "" : `<button class="btn" data-act="cancel">
            <i class="fa-solid fa-xmark"></i> 取消</button>`}
          <button class="btn ${danger ? "btn--danger" : "btn--primary"}" data-act="ok">
            <i class="fa-solid ${esc(confirmIcon || (danger ? "fa-circle-stop" : "fa-check"))}"></i>
            ${esc(viewOnly ? "關閉" : confirmText)}</button>
        </div>
      </div>`;
    // pre 用 textContent：它裝的是使用者原本的 prompt 原文，可能含任何字元
    if (pre !== null) wrap.querySelector("#modal-pre").textContent = pre;
    document.body.appendChild(wrap);
    const field = wrap.querySelector("#modal-input");
    // 對話框裡的密碼欄（管理員重設他人密碼）也要能看一眼——它是動態產生的，
    // 不在頁面載入時那次掃描的範圍內
    enhancePasswordFields(wrap);
    if (field) field.select(); else wrap.querySelector('[data-act="ok"]').focus();
    // allowEmpty：讓「清空」成為有效答案（例如取消命名），而不是被當成按了取消
    const answer = () => (input?.allowEmpty ? field.value : (field.value || null));

    // 注音／日文等 IME 選字時也會送出 Enter；那一下是「確認選字」不是「送出表單」。
    // isComposing 在部分瀏覽器於 compositionend 的同一次 keydown 已為 false，故自行記狀態。
    let composing = false;
    field?.addEventListener("compositionstart", () => { composing = true; });
    field?.addEventListener("compositionend", () => { composing = false; });

    const done = (v) => { wrap.remove(); document.removeEventListener("keydown", onKey); resolve(v); };
    const onKey = (e) => {
      if (e.key === "Escape" && !composing) done(null);
      if (e.key === "Enter" && field && !composing && !e.isComposing) done(answer());
    };
    document.addEventListener("keydown", onKey);
    wrap.addEventListener("click", (e) => {
      if (e.target === wrap) return done(null);
      const act = e.target.closest("button[data-act]")?.dataset.act;
      if (act === "cancel") done(null);
      if (act === "ok") done(field ? answer() : true);
    });
  });
}

/* ── 主題：JSON → CSS custom properties ────────────────────────────────────────
 * 主題檔只描述語意色（surface / text / border / accent / signal），不碰版面。
 * 新增主題＝在 static/themes/ 放一個 JSON 並加進 THEMES，不需改任何 CSS。
 */
const THEMES = [
  { id: "instrument", name: "Instrument", mode: "dark", icon: "fa-solid fa-gauge-high" },
  { id: "daylight", name: "Daylight", mode: "light", icon: "fa-solid fa-sun" },
  { id: "vellum", name: "Vellum", mode: "dark", icon: "fa-solid fa-mug-hot" },
];
const THEME_STORAGE_KEY = "claude-pty:theme";
// ⚠ SYNC：base.html 的 <head> inline script 用同一組 key 做首屏套色
const THEME_VARS_KEY = "claude-pty:theme-vars:";

/** 實際套用主題色（不含動畫）。 */
/** 取一個主題的色票。**先讀 localStorage 的快取**，沒有才 fetch。
 *
 * ⚠ 快取本來就一直在寫（下面 persistTheme），只是從來沒有人讀——所以每切一次主題都在
 *   重新跑一趟網路。它同時是換頁時 <head> inline script 同步套用的資料來源。
 */
async function loadThemeColors(id) {
  if (id === "instrument") return null;      // 預設主題＝CSS 內建值，沒有色票要套
  const cached = localStorage.getItem(THEME_VARS_KEY + id);
  if (cached) {
    try { return JSON.parse(cached); } catch { /* 壞掉就當沒有，往下重抓 */ }
  }
  const res = await fetch(`/static/themes/${encodeURIComponent(id)}.json`);
  if (!res.ok) return null;
  const theme = await res.json();
  return theme.colors || {};
}

/** 把色票套到 :root。**純同步、只碰 style**——這是 View Transition 的 callback 要執行的
 *  唯一工作，多一件事都會延長畫面凍結的時間（見 applyTheme）。 */
function paintTheme(id, colors) {
  const root = document.documentElement;
  if (id === "instrument") {
    for (const prop of Array.from(root.style)) {
      if (prop.startsWith("--color-")) root.style.removeProperty(prop);
    }
    return;
  }
  for (const [key, value] of Object.entries(colors || {})) {
    root.style.setProperty(`--color-${key}`, value);
  }
}

/** 記住選擇與色票。localStorage 是**同步磁碟 I/O**，實測佔掉整段 callback 的一大半
 *  ——所以它必須在過渡之外做。 */
function persistTheme(id, colors) {
  if (colors) localStorage.setItem(THEME_VARS_KEY + id, JSON.stringify(colors));
  localStorage.setItem(THEME_STORAGE_KEY, id);
}

/** 保留原本的介面給不做過渡的呼叫端（初始化、prefers-reduced-motion）。 */
async function setThemeVars(id) {
  const colors = await loadThemeColors(id);
  paintTheme(id, colors);
  persistTheme(id, colors);
}

/**
 * 套用主題，並以 View Transition 做「從點擊處擴散的同心圓」過渡。
 * 圓心取自使用者點下的座標，半徑算到視窗最遠角，看起來像漣漪推過整個畫面。
 * 瀏覽器不支援、或使用者偏好減少動態時，直接套用（無動畫），功能不受影響。
 */
async function applyTheme(id, origin) {
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (!document.startViewTransition || reduce || !origin) {
    await setThemeVars(id);
    return;
  }
  /* ⚠ **過渡的 callback 一定要是同步的、而且只做改樣式這一件事。**
   *
   * startViewTransition 的順序是：拍下舊畫面 → 執行 callback 並**等它的 promise** →
   * 拍下新畫面 → 才跑動畫。等待期間整個頁面是一張凍結的靜態圖，而那是主執行緒上的
   * 停頓——**一定看得見**，就是使用者回報的「同心圓還沒開始，畫面先卡住一下」。
   *
   * 原本 callback 裡有三件事，實測共 18–22ms：fetch 色票、逐一設 40 個 custom
   * property、以及 localStorage.setItem（**同步磁碟 I/O**）。三件裡只有「設 property」
   * 是過渡真正要捕捉的變化，另外兩件都搬到這裡、在過渡之外先做完。
   *
   * ⚠ 曾經為了「動畫中的掉幀」把這整套換成合成器處理的圓形疊層（只動 transform），
   *   量到的主執行緒長幀從 2.8 個降到 0，但**視覺上兩者都不卡**——rAF 量的是主執行緒
   *   節奏，而 View Transition 的動畫是合成器在推的，主執行緒停頓時畫面照樣在動。
   *   疊層版的代價是擴散過程只有純色（沒有真實畫面），所以換回這一版。
   *   要再動這裡之前：**先確認你量的東西與使用者看到的東西是同一個**。
   */
  const colors = await loadThemeColors(id);
  persistTheme(id, colors);
  const { x, y } = origin;
  // 半徑＝圓心到四個角的最大距離，確保漣漪能覆蓋整個視窗
  const radius = Math.hypot(
    Math.max(x, window.innerWidth - x),
    Math.max(y, window.innerHeight - y),
  );
  const transition = document.startViewTransition(() => paintTheme(id, colors));
  try {
    await transition.ready;
    document.documentElement.animate(
      { clipPath: [`circle(0px at ${x}px ${y}px)`, `circle(${radius}px at ${x}px ${y}px)`] },
      { duration: 520, easing: "cubic-bezier(0.4, 0, 0.2, 1)",
        pseudoElement: "::view-transition-new(root)" },
    );
  } catch {
    /* 過渡被中斷（如連續切換）：主題已套用，忽略即可 */
  }
}

/** 「還有多久」／「多久以前」，語意化到分鐘。
 *  ⚠ 與 relTime() 分開：那支只講過去（「3 分鐘前」），這裡兩個方向都要，而且**要到分鐘**
 *    ——「1 小時後」對一個 89 分鐘後才重置的區間是誤導，人會據此決定現在要不要繼續跑。 */
function relWhen(iso) {
  const ms = new Date(iso).getTime() - Date.now();
  if (Number.isNaN(ms)) return "";
  const future = ms > 0;
  let m = Math.round(Math.abs(ms) / 60000);
  if (m < 1) return future ? "即將重置" : "剛剛";
  const d = Math.floor(m / 1440); m -= d * 1440;
  const h = Math.floor(m / 60); m -= h * 60;
  const parts = [];
  if (d) parts.push(`${d} 天`);
  if (h) parts.push(`${h} 小時`);
  // 天數夠多時分鐘是雜訊；只有在一天之內才需要精確到分
  if (m && !d) parts.push(`${m} 分鐘`);
  // ⚠ 用空白接，不要直接串起來——「3 天11 小時」會黏成一團（實測看到的就是這樣）
  return parts.join(" ") + (future ? "後" : "前");
}

/** 重置券還剩多久到期 → 緊急度。回 "" 表示還早，不必特別標。
 *
 *  ⚠ 券**會過期而且過期就沒了**，所以「哪一張要先用掉」是看這一塊的唯一理由。只給一句
 *    「3 天後到期」等於要人自己讀字比大小——真正會被漏掉的那張（今天就到期）長得跟
 *    還有一個月的那張一模一樣。這裡把剩餘時間換成看得見的三段。
 *
 *  以**剩餘整天數**分桶（floor）：0 天（含已過期）＝紅框＋脈動、1–2 天＝紅框、
 *  3 天＝橘框、4 天以上＝原樣。負數落在第一桶是刻意的：拿到手上這份快照時剛過期的券，
 *  該是最醒目的那張，而不是靜靜掉回原樣。 */
function ticketExpiry(iso) {
  if (!iso) return "";
  const ms = new Date(iso).getTime() - Date.now();
  if (Number.isNaN(ms)) return "";
  const days = Math.floor(ms / 86400000);
  if (days < 1) return "critical";
  if (days < 3) return "urgent";
  if (days < 4) return "soon";
  return "";
}

function initTheme() {
  const saved = localStorage.getItem(THEME_STORAGE_KEY) || "instrument";
  const mount = document.getElementById("theme-picker");
  if (mount) {
    // 每個主題標示亮/暗，選單裡一眼看得出來
    const opts = THEMES.map((t) => ({
      value: t.id, label: t.name, icon: t.icon,
      hint: t.mode === "light" ? "亮色" : "暗色",
    }));
    createPicker(mount, opts, saved);
    // navbar 上沒有「主題」這個文字標籤（圖示 + 目前主題名已經說完了），所以名稱只能
    // 從這裡來。⚠ 要掛在**按鈕與清單**上，不是外層的 mount——mount 沒有 role，
    // ARIA 名稱掛在無角色的一般元素上不會被輔助技術採用（等於沒寫）。
    mount.querySelector("button")?.setAttribute("aria-label", "介面主題");
    mount.querySelector('[role="listbox"]')?.setAttribute("aria-label", "介面主題");
    mount.addEventListener("change", (e) => applyTheme(e.detail.value, e.detail.origin));
  }
  if (saved !== "instrument") setThemeVars(saved);   // 初次載入不做動畫
}

/* ── 分段控制的滑動 ────────────────────────────────────────────────────────────
 * Sessions ↔ 帳號 是真的換頁（整份 HTML 重來），沒有「同一個 DOM 從甲移到乙」這回事。
 *
 * **thumb 的位置由 CSS 決定**（`.navseg[data-active]` → `--navseg-i`，見 app.css），
 * 所以頁面第一幀就已經在正確的格子上。這裡只做一件事：如果上一頁停在另一格，就先把
 * thumb 拉回那一格（無過渡），下一影格再放手讓它滑回 CSS 給的位置。
 *
 * 這個順序很重要——它決定了失敗時的樣子。JS 沒跑、跑得晚、或 sessionStorage 被清掉，
 * 結果都只是「沒有動畫」；thumb 不會有一瞬間不在正確位置。先前反過來做（JS 量寬度才
 * 定位），每次換頁都會看到它冒出來閃一下（2026-07-25 使用者回報）。
 */
/* ── 密碼欄位的「看一眼」 ──────────────────────────────────────────────────────
 * 密碼是盲打的，而這裡的欄位有兩種都很容易打錯的情境：登入時的長密碼、以及「新密碼／
 * 再輸入一次」那組（打錯的代價是換一個自己不知道的密碼）。
 *
 * 做成自動掃描而不是在模板各加一顆按鈕：欄位散在 login / account 兩個模板共五處，還有
 * dialog 動態產生的那一個（管理員重設密碼）。逐一手寫的話，下次新增欄位一定會漏。
 */
function attachPasswordToggle(input) {
  if (input.dataset.pwToggle) return;          // 同一個欄位不要包兩次
  input.dataset.pwToggle = "1";

  const wrap = document.createElement("span");
  wrap.className = "pw";
  input.parentNode.insertBefore(wrap, input);
  wrap.appendChild(input);

  // ⚠ 切成 text 之後就不再是「密碼欄位」了，瀏覽器的保護跟著消失：拼字檢查會生效，
  //   而部分瀏覽器與擴充套件會把 text 欄位的內容送到遠端做檢查（spell-jacking，
  //   「顯示密碼」正是最常被點名的觸發點）。這三個屬性的成本是零，一律關掉。
  input.spellcheck = false;
  input.setAttribute("autocorrect", "off");
  input.setAttribute("autocapitalize", "off");

  const btn = document.createElement("button");
  btn.type = "button";                          // ⚠ 不設的話在 <form> 裡預設是 submit
  btn.className = "pw__toggle";
  // ⚠ 刻意**留在 Tab 順序裡**。曾經設 tabIndex = -1（想讓打完密碼按 Tab 直接到下一格），
  //   但那讓純鍵盤與螢幕閱讀器使用者完全沒有辦法用這個功能——而「看一眼自己打對沒」
  //   本來就是一項無障礙輔助，把鍵盤路徑拿掉等於只留給最不需要它的人。按鈕在 DOM 上
  //   就緊接著 input，Tab 到它是符合預期的位置，不是意外（review 2026-07-25）。
  btn.innerHTML = '<i class="fa-solid fa-eye"></i>';
  // 名稱固定、狀態交給 aria-pressed。兩個都隨狀態變的話，螢幕閱讀器會唸成
  // 「隱藏密碼，已按下」——同一件事講兩次，而且聽起來像互相矛盾。
  btn.setAttribute("aria-label", "顯示密碼");
  const paint = () => {
    const shown = input.type === "text";
    btn.innerHTML = `<i class="fa-solid fa-eye${shown ? "-slash" : ""}"></i>`;
    btn.setAttribute("aria-pressed", String(shown));
  };
  paint();
  btn.addEventListener("click", () => {
    // 切換 type 會讓游標跳到字尾，先記下位置再還原——不然看一眼密碼就得重新找插入點
    const { selectionStart: a, selectionEnd: b } = input;
    input.type = input.type === "password" ? "text" : "password";
    paint();
    input.focus();
    try { input.setSelectionRange(a, b); } catch { /* 某些瀏覽器在 type 切換後不給設 */ }
  });
  wrap.appendChild(btn);
  input._pwRepaint = paint;                     // 供 resetPasswordFields 用
}

function enhancePasswordFields(root = document) {
  root.querySelectorAll('input[type="password"], input[data-pw-toggle]')
      .forEach(attachPasswordToggle);
}

/** 把密碼欄位收回遮蔽狀態並清空。
 *
 * ⚠ 清 value 而不收回 type 是會出事的：管理員為了確認沒打錯而按了眼睛，送出後表單
 *   清空但欄位還是 text，**下一個人的密碼就全程明文顯示在螢幕上**——而他並沒有再按
 *   一次眼睛，畫面上也沒有任何提示。在這個「開帳號給誰就等於把共用憑證交給誰」的
 *   系統裡，旁邊有人或正在螢幕分享並不罕見（review 2026-07-25）。
 *   所以「清空密碼欄」這件事只有一個正確做法，就是呼叫這個函式。
 */
function resetPasswordFields(...inputs) {
  for (const input of inputs) {
    if (!input) continue;
    input.value = "";
    input.type = "password";
    input._pwRepaint?.();                       // 圖示換回「眼睛」、aria-pressed 歸位
  }
}

const NAVSEG_STORAGE_KEY = "claude-pty:navseg";
const NAVSEG_ORDER = ["sessions", "account"];

function initNavSeg() {
  const seg = document.querySelector(".navseg");
  const thumb = seg && seg.querySelector(".navseg__thumb");
  if (!thumb) return;
  const active = seg.dataset.active;
  const previous = sessionStorage.getItem(NAVSEG_STORAGE_KEY);
  sessionStorage.setItem(NAVSEG_STORAGE_KEY, active);

  if (previous === active || NAVSEG_ORDER.indexOf(previous) < 0) return;   // 首次進站或原地重整
  // 開了「減少動態」就整段不做。單純把 transition 關掉是不夠的——下面仍會把 thumb 挪到
  // 上一格再挪回來，沒有過渡的話那一幀就是直接跳過去，正好是這整段在避免的閃爍。
  if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) return;

  // 起點：把 data-active 暫時改成上一頁那一格。**不自己算 translateX** ——位置的算式
  // 只該有一份（在 CSS 的 --navseg-i），JS 複製一份的話，日後改格寬或加第三格時
  // 它會靜靜地算錯。
  seg.dataset.active = previous;
  requestAnimationFrame(() => {
    thumb.dataset.animate = "1";
    seg.dataset.active = active;                  // 交還給 CSS＝滑到目前這一格
  });
}

/* 到期時刻排版成「2026-08-22 12:22（27 天後）」。
 *
 * ⚠ 這件事只能在瀏覽器做。控制平面跑在容器裡、時區是 UTC，它排出來的時間不屬於任何人；
 *   所以伺服端只送 epoch 毫秒（credentials_state 的 *_at），時區與語系由這裡決定。
 * 相對時間的單位隨距離換檔：剩三小時卻寫「0 天後」等於沒講。 */
function credWhen(ms) {
  const d = new Date(ms);
  const abs = d.toLocaleString("zh-TW", {
    year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", hour12: false,
  });
  const diff = ms - Date.now();
  const rtf = new Intl.RelativeTimeFormat("zh-TW", { numeric: "auto" });
  const min = diff / 60000;
  const rel = Math.abs(min) < 60 ? rtf.format(Math.round(min), "minute")
            : Math.abs(min) < 1440 ? rtf.format(Math.round(min / 60), "hour")
            : rtf.format(Math.round(min / 1440), "day");
  return `${abs}（${rel}）`;
}

/* 憑證狀態。伺服端在頁面裡先塞了一份（#cred-data），之後由列表的
 * 15 秒輪詢覆蓋——這樣「已經在跑的 session 憑證到期」不必重新整理也看得到，那正是
 * 2026-07-26 那次事故裡一直沒有人發現的那一段。 */
let CRED = {};

/* 招牌上的憑證徽章：顯示**目前選中的那個 agent** 的憑證狀態。
 * 一顆膠囊跟著選單走，而不是並排兩顆——招牌的水平空間是有限的（見斷點那段的實測），
 * 而你在看的當下只關心正要開的那一個。
 * 圖示與顏色都在 CSS（.cred 的 data-state），這裡只搬狀態值。 */
function setCredCli(cli) {
  const el = document.getElementById("cred-badge");
  const d = CRED[cli];
  if (!el || !d) return;         // 沒有這個 agent 的資料就維持現狀，不要清成空白
  // 「換了 agent」與「同一個 agent 的狀態被輪詢更新」是兩件事，只有前者要翻頁動畫：
  // 後者每 15 秒來一次，跟著飛的話招牌會自己動起來，而且多半什麼也沒變。
  const switched = el.dataset.cli !== cli;
  el.dataset.cli = cli;
  if (switched && !prefersReducedMotion()) swapCred(el, d);
  else paintCred(el, d);
}

/** 把一份憑證狀態畫上徽章。**畫什麼只有這一份**——過渡只決定「什麼時候畫」，
 *  兩邊各寫一份遲早分岔。`el` 可以是離線的複本（見 credWidth）。 */
function paintCred(el, d) {
  el.dataset.state = d.state;
  const mark = el.querySelector(".cred__brand");
  // 只在品牌真的換了才重畫（innerHTML 每次都寫會讓 SVG 一直重建）。
  if (mark && mark.dataset.brand !== d.brand) {
    mark.dataset.brand = d.brand;
    mark.innerHTML = brandIcon(d.brand, "cred__brand-svg");
  }
  const label = el.querySelector(".cred__label");
  // ⚠ 只守這一行：元素是 role="status"（aria-live=polite），**文字**變動會讓螢幕閱讀器
  //   再念一次，即使寫進去的字串一模一樣——每 15 秒念一次憑證狀態。
  //   反過來說，data-* 與 aria-hidden 的品牌標誌不會被念，那些不能一起擋掉：
  //   先前把它們也放進同一個 return 條件，結果首次載入（值與伺服端一致）品牌標誌
  //   永遠不會被注入。
  if (label.textContent !== d.label) label.textContent = d.label;
  if (el.id === "cred-badge") refreshCredTip();   // 離線複本沒有 tooltip 要更新
}

const prefersReducedMotion = () =>
  Boolean(window.matchMedia?.("(prefers-reduced-motion: reduce)").matches);

/** 這份狀態畫上去會有多寬。**不可以**先寫進真的徽章再量：那顆是 role="status"，
 *  文字每改一次螢幕閱讀器就念一次，為了量寬度讓人多聽一遍不划算。
 *  複本是同步建立、量完立刻移除，所以重複的 id 不會被任何人看到。 */
function credWidth(el, d) {
  const ghost = el.cloneNode(true);
  ghost.removeAttribute("id");
  for (const n of ghost.querySelectorAll("[id]")) n.removeAttribute("id");
  paintCred(ghost, d);
  ghost.style.cssText += ";position:fixed;left:-9999px;top:0;width:auto;visibility:hidden";
  el.parentNode.appendChild(ghost);
  const w = ghost.getBoundingClientRect().width;
  ghost.remove();
  return w;
}

// 與 CSS 的 .cred[data-swap] 是同一組數字：淡出 140ms → 換內容 → 淡入 180ms。
const CRED_OUT_MS = 140;
const CRED_IN_MS = 180;
// 世代編號。連續切換（在選單上按住 ↑↓）時只有最後一次算數，否則前一輪的計時器會在
// 後一輪畫完之後才醒來，把寬度解鎖或把 data-swap 刪掉，動畫斷在一半。
let CRED_SWAP_GEN = 0;

/** 翻頁式換 agent：舊內容往上淡出、寬度同時 morph 到新內容的寬度、新內容從下方浮上來。
 *  寬度先動的理由見 app.css 的 `.cred[data-swap]` 那段（膠囊不能裁切，字會探出去）。 */
function swapCred(el, d) {
  const gen = ++CRED_SWAP_GEN;
  // 起點用實際佔位寬度；終點在**還沒動任何東西**之前先量好，量完才開始跑。
  el.style.width = `${el.getBoundingClientRect().width}px`;
  const to = credWidth(el, d);
  el.dataset.swap = "out";
  requestAnimationFrame(() => {              // 讓起點寬度先落定一幀，否則沒有可過渡的差值
    if (gen !== CRED_SWAP_GEN) return;
    el.style.width = `${to}px`;
  });
  setTimeout(() => {
    if (gen !== CRED_SWAP_GEN) return;
    paintCred(el, d);
    el.dataset.swap = "in";
    setTimeout(() => {
      if (gen !== CRED_SWAP_GEN) return;
      delete el.dataset.swap;
      // 寬度交還給內容：文字會被輪詢改寫（「已設定」↔「未設定憑證」），
      // 留著寫死的 px 會讓它從此對不上自己的內容。
      el.style.width = "";
    }, CRED_IN_MS);
  }, CRED_OUT_MS);
}

/* 列表輪詢回來的兩份狀態。沿用目前選中的 agent，不要跳回預設。 */
function renderCredBadge(all) {
  if (!all) return;              // 欄位缺席就維持伺服端繪好的那份
  CRED = all;
  // data-cli 由伺服端以 config.DEFAULT_CLI 種下（見 _masthead.html），這裡不再寫一次
  // 預設值——那會變成第二個真相來源，兩邊哪天分岔了也不會有人發現。
  const el = document.getElementById("cred-badge");
  if (el) setCredCli(el.dataset.cli);
}

/* 依目前 agent 的 detail + stamps 重組 data-tip。
 * 掛在 mouseenter/focus 上（見 DOMContentLoaded）：相對時間會隨時間走鐘，而 tooltip
 * 只在浮出來那一刻被看到——在那一刻算，比每 15 秒重算一次省事也準。
 * stamps 的標題由伺服端給（存取權杖／續期權杖各是哪個欄位是伺服端的知識），
 * 這裡不寫死任何一種。 */
function refreshCredTip() {
  const el = document.getElementById("cred-badge");
  if (!el) return;
  const d = CRED[el.dataset.cli];
  const lines = [d ? d.detail : el.dataset.tip];
  for (const s of (d && d.stamps) || []) lines.push(`${s.label} ${credWhen(s.at)}`);
  const next = lines.filter(Boolean).join("\n");
  // ⚠ 沒變就不要寫，理由與上面 label 那道守衛相同：這顆是 role="status"，而 tooltip 的
  //   文字是 `::after { content: attr(data-tip) }`——瀏覽器會把生成內容放進 a11y tree，
  //   無條件重寫等於可能讓螢幕閱讀器再念一次。同一條防線不該只套一半。
  if (el.dataset.tip !== next) el.dataset.tip = next;
}

document.addEventListener("DOMContentLoaded", () => {
  initTheme();
  initNavSeg();
  enhancePasswordFields();
  const cred = document.getElementById("cred-badge");
  if (cred) {
    // 伺服端已經把憑證狀態塞進頁面（見 _masthead.html），不必再打一趟 API
    try { CRED = JSON.parse(document.getElementById("cred-data").textContent); }
    catch { CRED = {}; }                      // 壞了就退回伺服端繪好的那份靜態徽章
    setCredCli(cred.dataset.cli);             // 補上品牌標誌（伺服端只留空的插槽）
    refreshCredTip();                         // 先組一份，滑鼠可能在 JS 載入前就停在上面
    cred.addEventListener("mouseenter", refreshCredTip);
    cred.addEventListener("focus", refreshCredTip);
  }
  /* 頁尾的打包時間：伺服端只送 ISO，格式化與「多久以前」都在瀏覽器做——控制平面跑在
     容器裡、時區是 UTC，排出來的時間不屬於任何人（同 credTipText 的決定）。 */
  for (const t of document.querySelectorAll(".footer__at")) {
    const iso = t.getAttribute("datetime");
    const d = iso ? new Date(iso) : null;
    if (!d || Number.isNaN(d.getTime())) continue;   // 解不出來就留空，不要印 Invalid Date
    t.textContent = absTime(iso);
  }
  for (const r of document.querySelectorAll(".footer__rel")) {
    const iso = r.dataset.for;
    const d = iso ? new Date(iso) : null;
    if (!d || Number.isNaN(d.getTime())) continue;
    r.textContent = `（${relTime(iso)}）`;
  }
  initAccountMenu();
});

/** 登出。設定選單與任何其他入口共用同一份——這條路徑有它自己的失敗處理，複製一份遲早會漂。 */
async function doLogout() {
  try {
    await api("/api/auth/logout", { method: "POST" });
  } catch (ex) {
    // 登出失敗也要照樣離開：cookie 可能早就失效了（那正是常見的失敗原因），
    // 把人留在一個進不去任何頁面的畫面上更糟。
    toastAfterNav("已離開控制台", "warning", `伺服器回報：${ex.message}`);
    location.href = "/login";
    return;
  }
  // 換頁會清掉當下的 toast，所以寄放給登入頁顯示（見 toastAfterNav）
  toastAfterNav("已登出", "success", "工作階段已結束，session 本身仍在背景執行");
  location.href = "/login";
}

/* ── 設定對話框：終端程式（ttyd） ────────────────────────────────────────────
 * 原本是 session 頁上一塊展開的面板，兩個問題：
 *   · 它與篩選共用 `.filters__grid`（auto-fit 的多欄格線），而這裡只有兩格——於是
 *     右邊永遠空一大塊，而空白在**設定**這種面板上讀起來像「有東西沒載出來」。
 *   · 它只存在於 session 頁。設定是跟著**身分**走的東西，換頁不該消失。
 * 改成從身分下拉叫出的對話框：單欄、寬度自己說了算，而且每一頁都在。
 */
function settingsModal() {
  const wrap = document.createElement("div");
  wrap.className = "modal";
  wrap.dataset.testid = "settings-modal";
  wrap.innerHTML = `
    <div class="modal__box modal__box--wide" role="dialog" aria-modal="true"
         aria-labelledby="settings-modal-title">
      <h2 class="modal__title" id="settings-modal-title">
        <i class="fa-solid fa-sliders"></i> 設定</h2>
      <div class="settings">
        <section class="settings__row">
          <div class="settings__head">
            <span class="settings__label">終端程式</span>
            <span class="settings__note">只影響之後開的終端，已經在跑的不會被換掉</span>
          </div>
          <div id="pick-ttyd" class="settings__control"></div>
        </section>
      </div>
      <div class="modal__actions">
        <button class="btn" data-act="close"><i class="fa-solid fa-xmark"></i> 關閉</button>
      </div>
    </div>`;
  document.body.appendChild(wrap);

  /* ⚠ Esc 要掛在 **document** 上、不是 wrap 上，而且**必須設初始焦點**。
   *   這個對話框是從身分下拉點開的，選單關掉之後焦點掉回 `<body>`——焦點不在 wrap 裡，
   *   掛在 wrap 上的 keydown 就永遠收不到，Esc 形同不存在（實測：開啟後 activeElement
   *   是 BODY、按 Esc 沒反應；點進輸入框之後才關得掉）。
   *   同檔的 `dialog()` 兩件事都做對了，這裡比照它——包含「離開時把監聽器拿掉」。
   * 初始焦點放關閉鍵（唯一一定聚焦得上的控件——picker 要等 /api/prefs 回來才建）。 */
  const onKey = (e) => { if (e.key === "Escape") close(); };
  const close = () => {
    document.removeEventListener("keydown", onKey);
    wrap.remove();
    document.getElementById("account-btn")?.focus();   // 焦點回它來的地方
  };
  document.addEventListener("keydown", onKey);
  wrap.addEventListener("click", (e) => {
    if (e.target === wrap || e.target.closest('[data-act="close"]')) close();
  });
  wrap.querySelector('[data-act="close"]').focus();

  api("/api/prefs").then((d) => {
    const opts = d.ttyd_choices.map((c) => ({ value: c.value, label: c.label }));
    // 下拉本身就顯示著現值，不另外印「目前：Rust」——那是同一件事寫兩次
    const picker = createPicker(wrap.querySelector("#pick-ttyd"), opts, d.ttyd_bin);
    wrap.querySelector("#pick-ttyd").addEventListener("change", async () => {
      try {
        const saved = await api("/api/prefs",
                                { method: "PATCH", body: { ttyd_bin: picker.value } });
        const label = (opts.find((o) => o.value === saved.ttyd_bin) || {}).label
                      || saved.ttyd_bin;
        toast(`之後開的終端會用 ${label} 版；已經開著的不受影響`);
      } catch (ex) {
        picker.value = d.ttyd_bin;    // 存不進去就轉回真實值，不要留假象
        toast(`設定沒存成功：${ex.message}`, "error");
      }
    });
  }).catch((ex) => {
    toast(`讀取設定失敗：${ex.message}`, "error");
  });

  return wrap;
}

/* ── 身分下拉：設定 / 登出 ─────────────────────────────────────────────────────
 * 與用量面板同一套疊放與關閉規則（fixed 座標、捕獲階段的外點關閉、Esc、resize 重算）。
 *
 * ⚠ 鍵盤要能用。這是 `role="menu"`，螢幕閱讀器與鍵盤使用者會預期 ↑↓ 能移動、Esc 能關、
 *   關掉之後焦點回到觸發鍵——少了最後那一項，鍵盤使用者關掉選單後焦點會掉回 <body>，
 *   得從頭 Tab 一次。
 */
function initAccountMenu() {
  const btn = document.getElementById("account-btn");
  const menu = document.getElementById("account-menu");
  if (!btn || !menu) return;

  let open = false;
  const items = () => [...menu.querySelectorAll(".menu__item")];
  const setOpen = (on, { focusBack = false } = {}) => {
    open = on;
    menu.hidden = !on;
    btn.setAttribute("aria-expanded", String(on));
    if (on) anchorPanel(btn, menu, { matchWidth: true });
    else if (focusBack) btn.focus();
  };
  setOpen(false);

  btn.addEventListener("click", () => setOpen(!open));
  // 鍵盤從按鈕直接往下：↓ 開啟並停在第一項（原生 menu button 的慣例）
  btn.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      setOpen(true);
      (e.key === "ArrowDown" ? items()[0] : items().at(-1))?.focus();
    }
  });

  menu.addEventListener("click", (e) => {
    const act = e.target.closest("[data-act]")?.dataset.act;
    if (!act) return;
    setOpen(false);
    if (act === "logout") doLogout();
    if (act === "settings") settingsModal();
  });
  menu.addEventListener("keydown", (e) => {
    const list = items();
    const i = list.indexOf(document.activeElement);
    if (e.key === "ArrowDown") { e.preventDefault(); list[(i + 1) % list.length].focus(); }
    if (e.key === "ArrowUp") { e.preventDefault(); list[(i - 1 + list.length) % list.length].focus(); }
    if (e.key === "Escape") { e.preventDefault(); setOpen(false, { focusBack: true }); }
  });

  // ⚠ 捕獲階段：面板內容若被重畫，冒泡時 contains() 會對已經被換掉的節點回 false
  //   而讓選單自己關掉（picker 與用量面板踩過同一個坑）。
  document.addEventListener("click", (e) => {
    if (open && !menu.contains(e.target) && !btn.contains(e.target)) setOpen(false);
  }, true);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && open) setOpen(false, { focusBack: true });
  });
  window.addEventListener("resize", () => { if (open) anchorPanel(btn, menu, { matchWidth: true }); });
}
