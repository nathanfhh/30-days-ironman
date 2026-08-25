import { onBeforeUnmount, ref, type Ref } from "vue";

import { api } from "@/api/client";
import { lsGet, lsSet } from "@/lib/storage";

/* ── 讓容器裡的 TTY 跟著抽屜的大小走 ──────────────────────────────────────────
 *
 * ttyd **有**把視窗大小送出去，只是送給它自己的子程序——而那個子程序是 `docker attach`，
 * 它不會把大小轉給容器裡的 TTY。所以預設情況是：xterm 依 iframe 排到 181 欄，容器裡的
 * TUI 仍照建立時的 140 欄畫，右邊空一大塊（實測 2026-07-26）。
 *
 * 補的這一步是：把 xterm 量到的欄列數用 /resize 送去，docker 改容器 TTY 的大小、核心送
 * SIGWINCH，TUI 自己重繪。xterm 的 Terminal 物件 ttyd 掛在 `window.term` 上，而 iframe
 * 同源，父頁面讀得到。
 *
 * ⚠ 尺寸是**整個 session 共用**的——容器只有一個 TTY。人在這裡把視窗拉寬，其他在看同一場
 *   的人看到的版面就跟著變。這是「同一個 session 多個觀看者」的必然。
 *
 * 這一份是階段 1.5 的成果（`app.js` 的 `syncSize` / `attachSizeSync`）逐條搬過來，包含
 * 那兩道閘與 token。**Vue 帶來的好處是生命週期有地方掛**（onBeforeUnmount 拆觀察者），
 * 不是 `nextTick`——nextTick 只保證 DOM patch 刷完，不保證 layout 穩、transition 結束、
 * iframe 內的 JS 跑完，等的東西根本不對。
 */

/** 字級夾在 8–32：再小讀不到、再大一行放不了幾個字，TUI 的版面會整個垮掉。 */
export const FONT_MIN = 8;
export const FONT_MAX = 32;
export const FONT_KEY = "claude-pty:term-font";

/** 盒子連續這麼久沒再變，才算停定。 */
export const SETTLE_MS = 150;

/** xterm 的 Terminal，只取我們真的會碰的那幾個成員。 */
export interface TermLike {
  cols: number;
  rows: number;
  options?: { fontSize?: number };
  onResize: (cb: (d: { cols: number; rows: number }) => void) => void;
}

/* 尺寸同步的診斷開關。平時完全安靜；要查「送出去的欄列數為什麼跟畫面對不上」時：
 *     localStorage.setItem("claude-pty:debug-size", "1")   // 關掉：removeItem
 * 這類問題的本質是時序，靠肉眼看畫面永遠問不出來。 */
function makeDebug(): (...a: unknown[]) => void {
  const on = lsGet("claude-pty:debug-size") === "1";
  const t0 = performance.now();
  return on
    ? (...a: unknown[]) => console.log(`[size +${Math.round(performance.now() - t0)}ms]`, ...a)
    : () => {};
}

/* ── 畫布與 CSS 尺寸脫節時，字會被畫成錯的大小 ──────────────────────────────
 * xterm 的 WebGL 算繪器把畫布的 backing store 開成「CSS 尺寸 × devicePixelRatio」，但字要
 * 畫多大是另外從 `dimensions` 算的。兩邊的 dpr 只要對不上，畫出來的字就整體縮放錯誤——
 * 實測 2026-07-27：抽屜一開，畫布是 1728×1248 而 CSS 只有 864×624，於是每個字只佔顯示上
 * 的一半。而且是**從 ttyd 自己的第一次算繪就已經如此**，不是我們改字級造成的。
 *
 * ⚠ 這四種都試過，**沒有一種有用**：`handleDevicePixelRatioChange()`、
 *   `clearTextureAtlas()`、往 iframe 丟 resize 事件（畫布完全不動）；`term.resize()`
 *   （畫布跟著變但 2 倍的比例原封不動）。只有**真的改變字級**會讓 xterm 重新量字並重建
 *   畫布。所以這裡就照使用者手動做的那件事做：+1 再還原。同值指派 xterm 會直接忽略。
 */
function healGlyphScale(
  frame: HTMLIFrameElement,
  term: TermLike,
  debug: (...a: unknown[]) => void,
): void {
  const doc = frame.contentDocument;
  const size = term.options?.fontSize;
  if (!doc || !size) return;
  const dpr = frame.contentWindow?.devicePixelRatio || 1;
  const broken = [...doc.querySelectorAll<HTMLCanvasElement>(".xterm canvas")].some((c) => {
    const cssW = parseFloat(c.style.width);
    // 還沒排版好的畫布不算壞——那是「還沒到」，不是「錯了」
    return cssW > 0 && c.width > 0 && Math.abs(c.width - cssW * dpr) > 1;
  });
  if (!broken) return;
  debug("畫布與 CSS 尺寸對不上，重新量一次字級", size);
  // try/finally：中間那一步若拋了，字級不可以停在 +1——那會是使用者永遠改不回來的大小。
  try {
    term.options!.fontSize = size + 1;
  } finally {
    term.options!.fontSize = size;
  }
}

/**
 * ⚠ 只等「滑入」那一個過渡，其餘一律忽略。
 *
 * `getAnimations()` 回的是這個元素上**所有**的動畫。哪天有人在面板上加一個
 * `animation: … infinite`（呼吸燈、載入中的脈動、無限旋轉的圖示都算），它的 `finished`
 * **永遠不會 resolve**，於是 /resize 從此再也送不出去。症狀會是「PTY 尺寸完全不同步了」，
 * 而肇因是一條看起來與尺寸毫不相干的 CSS 裝飾。
 */
function isSlide(a: Animation): boolean {
  const t = (a as Animation & { transitionProperty?: string }).transitionProperty;
  const Ctor = (globalThis as { CSSTransition?: unknown }).CSSTransition;
  return t === "transform" && (typeof Ctor === "undefined" || a instanceof (Ctor as never));
}

export interface TerminalSizeOptions {
  sid: string;
  frame: Ref<HTMLIFrameElement | null>;
  panel: Ref<HTMLElement | null>;
  /** 抽屜正在關（或已經關了）。所有排程中的動作都要當場作廢。 */
  closing: Ref<boolean>;
}

export function useTerminalSize({ sid, frame, panel, closing }: TerminalSizeOptions) {
  const debug = makeDebug();
  /** 目前的字級，畫在工具列上（到界時該側的按鈕會 disabled）。 */
  const fontSize = ref<number | null>(null);

  let sizeTimer: ReturnType<typeof setTimeout> | null = null;
  let sizeToken = 0;
  let frameRO: ResizeObserver | null = null;
  let sizePolls = 0;
  /** 這一輪要不要順便叫 TUI 重畫。做成「黏著的旗標」而不是參數：debounce 會把多次呼叫併成
   *  一次送出，若開啟時那次帶著 redraw 卻被後續的 onResize 併掉，重繪就悄悄不見了。 */
  let wantRedraw = false;
  /** 盒子最後一次變動的時刻。由下面的 ResizeObserver 維護。 */
  let lastBoxAt = 0;

  const term = (): TermLike | null =>
    (frame.value?.contentWindow as (Window & { term?: TermLike }) | null)?.term ?? null;

  /* 第一道閘：抽屜的滑入過渡結束。
     ⚠ 要 .catch：動畫被取消時 finished 會 reject（連點兩次、抽屜當場被關掉），而
       「不必再等了」正是那時候該有的結論，不是一個未處理的例外。 */
  const drawerSettled = (): Promise<unknown> => {
    const anims = (panel.value?.getAnimations?.() ?? []).filter(isSlide);
    return Promise.all(anims.map((a) => a.finished.catch(() => {})));
  };

  /* 第二道閘：iframe 的盒子連續 SETTLE_MS 沒有再變。fit 只有在盒子變的時候才會重跑，
     盒子還在動就送，送出去的是中途的格數，而**之後沒有任何人會再送一次**。

     ⚠ 為什麼不是「開啟後固定等 400ms」：那個數字在快的機器上是白等、在慢的機器上還是
       太早，而且它與 CSS 裡的 240ms 是兩份會各自漂走的常數。等事實不會漂。 */
  const boxSettled = (): Promise<void> =>
    new Promise((resolve) => {
      const tick = (): void => {
        const quiet = performance.now() - lastBoxAt;
        if (closing.value || quiet >= SETTLE_MS) resolve();
        else setTimeout(tick, SETTLE_MS - quiet);
      };
      tick();
    });

  function sendSize(): void {
    const t = term();
    const el = frame.value;
    if (!t || !el) return;
    // 修在讀尺寸**之前**：萬一哪天重量字級也改了欄列數，要送出去的是修好之後的值。
    healGlyphScale(el, t, debug);
    const { cols, rows } = t;
    const body = { rows, cols, redraw: wantRedraw };
    debug(
      "送出",
      `${cols}x${rows}`,
      "redraw=",
      wantRedraw,
      "iframe=",
      `${el.clientWidth}x${el.clientHeight}`,
    );
    /* ⚠ 旗標要等**送出成功**才清。抽屜剛開的那一刻 session 可能還在 creating，正是這一發
       最容易失敗的時候；先清掉的話重繪就永遠不會再有第二次機會，而失敗本身被下面的
       .catch 靜靜吞掉——那就違背了它做成「黏著旗標」的整個理由。
       路徑片段用 encodeURIComponent（不是 HTML 逸出，那用在網址上是錯的編碼）。 */
    api(`/api/sessions/${encodeURIComponent(sid)}/resize`, { method: "POST", body })
      .then(() => {
        wantRedraw = false;
      })
      .catch(() => {}); // 純視覺，失敗不打擾使用者（session 可能剛好結束了）
  }

  /**
   * 排一發尺寸同步。
   *
   * ⚠ 尺寸要在**送出的當下**才讀，不能在排程時就抓走：連按 8 次縮小時 onResize 會一路回報
   *   中途值，抓走的那個會蓋掉最終值——實測 xterm 已經是 254×82、PTY 卻停在 158×43。
   * ⚠ token：等兩道閘的期間又有人排了新的一發，這一發就作廢。沒有它的話「先排的後到」
   *   會用舊尺寸蓋掉新的。
   */
  function syncSize({ redraw = false } = {}): void {
    wantRedraw = wantRedraw || redraw;
    if (sizeTimer) clearTimeout(sizeTimer);
    const token = ++sizeToken;
    sizeTimer = setTimeout(() => {
      void Promise.all([drawerSettled(), boxSettled()]).then(() => {
        if (token !== sizeToken || closing.value) return;
        sendSize();
      });
    }, 300);
  }

  function applyFont(size: number): void {
    const t = term();
    if (!t?.options) return;
    t.options.fontSize = size;
    lsSet(FONT_KEY, String(size));
    fontSize.value = size;
    // 改完要讓 xterm 重新 fit：ttyd 綁的是 window resize，所以往 iframe 丟一個 resize
    // 事件即可，它會重算欄列數，接著 term.onResize 把新尺寸同步給容器的 TTY。
    frame.value?.contentWindow?.dispatchEvent(new Event("resize"));
  }

  function bumpFont(delta: number): void {
    const t = term();
    if (!t?.options) return;
    const next = Math.min(FONT_MAX, Math.max(FONT_MIN, (t.options.fontSize || 14) + delta));
    if (next !== t.options.fontSize) applyFont(next);
  }

  /**
   * iframe 載入完成後接上。
   *
   * ⚠ `load` 觸發時 ttyd 自己的 JS 還沒跑完，`window.term` 還不存在——直接取會拿到
   *   undefined 然後就再也不試了（實測：PTY 全程停在 80×24）。所以要等它出現，
   *   5 秒還沒出現就放棄（多半是 ttyd 換版不再掛 window.term），不要無限輪詢下去。
   */
  function attach(): void {
    const t = term();
    if (!t) {
      if (++sizePolls < 50 && !closing.value) setTimeout(attach, 100);
      return;
    }
    /* ⚠ onResize 必須**先**註冊，再套字級。這行原本放在最後，於是 applyFont() 觸發的那一次
       fit——也就是最關鍵的第一次——沒有人在聽：PTY 停在建立時的 140×40，而 xterm 已經照
       存下來的字級排成別的行數（使用者回報「大多時候都是錯的」）。 */
    debug("接上 onResize；此刻 term=", `${t.cols}x${t.rows}`);

    /* 盒子一變就逼 xterm 重新 fit，並記下變動的時刻（boxSettled 要用）。
     * ⚠ 為什麼需要它：**ttyd 綁的只有 `window.resize`**，沒有任何人在看 iframe 這個元素的
     *   盒子。Chromium 目前在元素尺寸改變時也會在 iframe 內補發一次 resize（實測過），
     *   但那是實作行為不是規格保證，而這條路壞掉的症狀是「PTY 靜靜停在舊格數」：沒有錯誤、
     *   沒有跡象，只有畫面不對。
     * ⚠ observe() 會立刻回呼一次（帶當下尺寸），那正好把 lastBoxAt 初始化成「現在」。 */
    lastBoxAt = performance.now();
    let lastBox = "";
    frameRO = new ResizeObserver(() => {
      const el = frame.value;
      if (!el) return;
      const box = `${el.clientWidth}x${el.clientHeight}`;
      if (box === lastBox) return; // 只在真的變了才算，避免自己觸發自己
      lastBox = box;
      lastBoxAt = performance.now();
      debug("iframe 盒子變成", box);
      el.contentWindow?.dispatchEvent(new Event("resize"));
    });
    if (frame.value) frameRO.observe(frame.value);

    t.onResize((d) => {
      debug("xterm 自己 fit 成", `${d.cols}x${d.rows}`);
      syncSize();
    });

    /* localStorage 的值是使用者可以手改的，而且舊版本可能存過別的範圍——一律夾回界內，
       不是「合法才用」：存了 999 的話直接忽略會讓他永遠回不到自己調過的大小。 */
    const rawFont = parseInt(lsGet(FONT_KEY) || "", 10);
    const saved = Number.isFinite(rawFont) ? Math.min(FONT_MAX, Math.max(FONT_MIN, rawFont)) : null;
    if (saved !== null && saved !== t.options?.fontSize) {
      applyFont(saved);
    } else {
      fontSize.value = t.options?.fontSize ?? null;
      /* ⚠ 字級不用改，**還是要逼一次 fit**——不然開啟時送出去的是「別的字級的」格數。
       * ttyd 的第一次排版是用它自己的預設字級算的，之後才把字級套成使用者存的值，而
       * **套字級不會順便重新 fit**。於是 `options.fontSize` 已經是 18、`cols/rows` 還停在
       * 13px 排出來的數字，而送出去讀的是後者（實測 2026-07-31：送出 112×42，正解是 84×32，
       * 使用者得手動按一下 +/- 才會回正）。
       * ⚠ `applyFont` 是**唯一**會 dispatch resize 去叫 ttyd 重新 fit 的地方，所以走進這個
       *   else 就整條路都跳過了——那正是「存的字級剛好等於目前字級」這條路上的洞。 */
      frame.value?.contentWindow?.dispatchEvent(new Event("resize"));
    }

    /* 不論上面有沒有觸發 fit，都以「目前的實際尺寸」對齊一次。
       ⚠ 開啟時**一定**帶 redraw。尺寸剛好與上次相同是常態（同一個視窗、同一個字級），那種
         情況 docker resize 不會產生 SIGWINCH，TUI 於是沿用它上次畫的版面——而那個版面可能
         是別的尺寸留下的，看起來就是「下面的內容跑到看不見的地方」。 */
    syncSize({ redraw: true });
  }

  function stop(): void {
    if (sizeTimer) clearTimeout(sizeTimer); // 抽屜都關了就別再送尺寸
    sizeTimer = null;
    frameRO?.disconnect(); // 盒子的觀察者同理（節點馬上就要被移除了）
    frameRO = null;
  }

  // 這才是 Vue 帶來的好處：拆除有地方掛，不必靠呼叫端記得在每一條關閉路徑上清一次。
  onBeforeUnmount(stop);

  return { fontSize, attach, stop, bumpFont, syncSize, FONT_MIN, FONT_MAX };
}
