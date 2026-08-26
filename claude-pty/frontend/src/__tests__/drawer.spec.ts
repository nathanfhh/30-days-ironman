import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { nextTick } from "vue";

import { setUnauthorizedHandler } from "@/api/client";
import TerminalDrawer from "@/components/TerminalDrawer.vue";
import { toasts } from "@/lib/toast";
import { useSiteStore } from "@/stores/site";

/* ── 終端抽屜 ──────────────────────────────────────────────────────────────────
 * views.spec 那一支從 session 列表把抽屜叫出來，守的是「開哪一場、iframe 指到哪」。
 * 這一支直接掛元件，守的是**抽屜自己**那幾條看不見的路：iframe 載入之後到底算不算接上、
 * 在終端裡貼一張圖會發生什麼、提示為什麼會停下來、以及關閉那一段動畫沒跑完就把節點
 * 拆掉的話會怎樣。這些狀態在真瀏覽器裡都要人手動製造，而且錯了不會有任何錯誤訊息。
 */

const mounted: VueWrapper[] = [];
let clipboardWrite = vi.fn(async () => {});
let fetchMock = vi.fn();

interface FakeTerm {
  cols: number;
  rows: number;
  options: { fontSize: number };
  onResize: (cb: (d: { cols: number; rows: number }) => void) => void;
}

interface FakeFrame {
  frame: HTMLIFrameElement;
  term: FakeTerm;
  /** 模擬「在終端裡直接 ⌘V」：抽屜把 paste 監聽掛進 iframe 的文件裡，父頁面收不到。 */
  pasteInside: (files: File[]) => { prevented: boolean };
}

/** 一定找得到，找不到就當場說清楚是哪一個選擇器。 */
function el<T extends Element>(sel: string): T {
  const found = document.querySelector<T>(sel);
  if (!found) throw new Error(`畫面上找不到 ${sel}`);
  return found;
}

function mountDrawer(over: { flavor?: string | null } = {}): VueWrapper {
  const w = mount(TerminalDrawer, {
    props: { sid: "sid1", label: "重構", path: "/session/sid1/", flavor: null, ...over },
    attachTo: document.body,
  });
  mounted.push(w);
  return w;
}

/**
 * 給抽屜的 iframe 裝一個同源替身。
 *
 * `pathname` 就是這一支要問的那件事：`load` 只代表「有東西載完了」，不代表載進來的是
 * 我們的終端：view 被回收時 nginx 會 302 到 `/`，load 照樣觸發。
 * `crossOrigin: true` 則模擬連讀都讀不到的情況（真的跨了 origin）。
 */
function fakeFrame(
  pathname = "/session/sid1/",
  { crossOrigin = false, docThrows = false } = {},
): FakeFrame {
  const frame = el<HTMLIFrameElement>('[data-testid="drawer-frame"]');
  const pasteHandlers: ((e: unknown) => void)[] = [];
  const term: FakeTerm = {
    cols: 80,
    rows: 24,
    options: { fontSize: 14 },
    onResize: () => {},
  };
  const win = { location: { pathname }, term, devicePixelRatio: 1, dispatchEvent: () => true };
  Object.defineProperty(frame, "contentWindow", {
    configurable: true,
    get: () => {
      if (crossOrigin) throw new Error("Blocked a frame with origin from accessing a cross-origin");
      return win;
    },
  });
  Object.defineProperty(frame, "contentDocument", {
    configurable: true,
    get: () => {
      if (docThrows) throw new Error("SecurityError");
      return crossOrigin
        ? null
        : {
            addEventListener: (type: string, cb: (e: unknown) => void) => {
              if (type === "paste") pasteHandlers.push(cb);
            },
            querySelectorAll: () => [],
          };
    },
  });
  return {
    frame,
    term,
    pasteInside: (files: File[]) => {
      let prevented = false;
      for (const cb of pasteHandlers) {
        cb({
          clipboardData: { files },
          preventDefault: () => {
            prevented = true;
          },
          stopPropagation: () => {},
        });
      }
      return { prevented };
    },
  };
}

/** 把 iframe 裝好並送出 load，回到「抽屜認為自己接上了」的狀態。 */
async function loadFrame(pathname = "/session/sid1/", opts = {}): Promise<FakeFrame> {
  const f = fakeFrame(pathname, opts);
  f.frame.dispatchEvent(new Event("load"));
  await flushPromises();
  return f;
}

/** 上傳打出去的那幾發（尺寸同步也走 fetch，不濾掉會混在一起）。 */
const uploadCalls = (): RequestInit[] =>
  fetchMock.mock.calls
    .filter(([url]) => String(url).includes("/upload"))
    .map(([, init]) => init as RequestInit);

/** 面板的 transitionend。⚠ jsdom 沒有 TransitionEvent，propertyName 要自己掛上去。 */
function transitionEnd(target: Element, propertyName: string): void {
  const ev = new Event("transitionend", { bubbles: true });
  Object.defineProperty(ev, "propertyName", { value: propertyName });
  target.dispatchEvent(ev);
}

const pending = (): HTMLElement => el<HTMLElement>('[data-testid="drawer-pending"]');

describe("TerminalDrawer", () => {
  beforeEach(() => {
    // 每條測試從乾淨的 401 處理器起跑；預設值是 `location.href`，在 jsdom 裡只是雜訊
    setUnauthorizedHandler(() => {});
    setActivePinia(createPinia());
    toasts.splice(0, toasts.length);
    localStorage.clear();
    vi.useRealTimers();
    clipboardWrite = vi.fn(async () => {});
    Object.defineProperty(globalThis.navigator, "clipboard", {
      configurable: true,
      value: { writeText: clipboardWrite },
    });
    fetchMock = vi.fn(
      async () =>
        new Response(JSON.stringify({ path: "/home/nathan/persistent-data/a.png" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
    );
    globalThis.fetch = fetchMock as unknown as typeof fetch;
  });

  afterEach(() => {
    for (const w of mounted.splice(0)) w.unmount();
    document.body.innerHTML = "";
    vi.useRealTimers();
  });

  it("iframe 載入之後還要確認它停在 /session/ 底下，才算真的接上了", async () => {
    mountDrawer();
    await flushPromises();
    expect(pending().hasAttribute("hidden")).toBe(false);
    expect(pending().textContent).toContain("連線中");
    await loadFrame("/session/sid1/");
    // 接上了才收起「連線中…」，收得太早的話畫面上會是一片白，什麼線索都沒有
    expect(pending().hasAttribute("hidden")).toBe(true);
  });

  it("🔴 被導走時 load 照樣會觸發：那時要留下一句講得出原因的話，不是一片白", async () => {
    mountDrawer();
    await flushPromises();
    await loadFrame("/");
    expect(pending().hasAttribute("hidden")).toBe(false);
    expect(pending().textContent).toContain("這個終端已經結束");
  });

  it("連 iframe 的位置都讀不到（非同源）也走同一條路，不是拋出去", async () => {
    mountDrawer();
    await flushPromises();
    await loadFrame("/session/sid1/", { crossOrigin: true });
    expect(pending().textContent).toContain("這個終端已經結束");
  });

  it("同源檢查過了、文件卻讀不到時不能拋：拋出去的話 load 之後的每一步都不會發生", async () => {
    mountDrawer();
    await flushPromises();
    await loadFrame("/session/sid1/", { docThrows: true });
    // 前面那一段已經判定是我們的終端，所以「連線中」照樣要收起來
    expect(pending().hasAttribute("hidden")).toBe(true);
  });

  it("在終端裡貼一張圖：攔下來上傳，路徑進剪貼簿", async () => {
    mountDrawer();
    await flushPromises();
    const f = await loadFrame();
    const { prevented } = f.pasteInside([new File(["x"], "shot.png", { type: "image/png" })]);
    await flushPromises();
    // 檔案終端吃不了，不攔的話 xterm 會收到一坨 base64
    expect(prevented).toBe(true);
    expect(uploadCalls()).toHaveLength(1);
    const init = uploadCalls()[0];
    expect(init.method).toBe("POST");
    expect(init.body).toBeInstanceOf(FormData);
    // form 設不了這個標頭，所以它就是後端的反 CSRF 閘門
    expect((init.headers as Record<string, string>)["X-Requested-With"]).toBe("fetch");
    // ⚠ 不可以自己設 Content-Type：boundary 是瀏覽器組的，設了就弄丟了
    expect(init.headers).not.toHaveProperty("Content-Type");
    expect(clipboardWrite).toHaveBeenCalledWith("/home/nathan/persistent-data/a.png");
    expect(toasts.at(-1)!.title).toBe("已上傳，路徑在剪貼簿");
  });

  it("貼上純文字放行給 xterm（那是正常的貼字，不是要上傳）", async () => {
    mountDrawer();
    await flushPromises();
    const f = await loadFrame();
    const { prevented } = f.pasteInside([]);
    await flushPromises();
    expect(prevented).toBe(false);
    expect(uploadCalls()).toHaveLength(0);
  });

  it("上傳失敗要把後端說的原因端出來，不是只說一句失敗", async () => {
    fetchMock.mockImplementation(
      async () =>
        new Response(JSON.stringify({ error: "副檔名不在白名單" }), {
          status: 400,
          headers: { "Content-Type": "application/json" },
        }),
    );
    mountDrawer();
    await flushPromises();
    const f = await loadFrame();
    f.pasteInside([new File(["x"], "a.exe")]);
    await flushPromises();
    expect(toasts.at(-1)!.title).toBe("上傳失敗");
    expect(toasts.at(-1)!.body).toBe("副檔名不在白名單");
  });

  it("🔴 上傳收到 401 走全站那條路：導回登入頁，而且不發「上傳失敗」", async () => {
    /* 上傳不能走 `api()`（multipart 的 boundary 要交給瀏覽器組），但「401 一律導回登入頁」
       是全站的規格、不是 `api()` 這個函式的性質。少了那一段，cookie 中途失效時使用者會
       拿到一句「上傳失敗／未登入」，然後繼續留在一個什麼都做不了的畫面上
       （fable 快審 2026-08-26）。 */
    const onUnauthorized = vi.fn();
    setUnauthorizedHandler(onUnauthorized);
    fetchMock.mockImplementation(
      async () =>
        new Response(JSON.stringify({ error: "未登入" }), {
          status: 401,
          headers: { "Content-Type": "application/json" },
        }),
    );
    mountDrawer();
    await flushPromises();
    const f = await loadFrame();
    toasts.splice(0, toasts.length); // 只看上傳這一段發了什麼
    f.pasteInside([new File(["x"], "a.png")]);
    await flushPromises();
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
    // 該讀的是全域那則「登入已失效」，不是「上傳失敗」。剩下的只有「上傳中…」那一則。
    expect(toasts.map((t) => t.title)).not.toContain("上傳失敗");
    expect(toasts.map((t) => t.title)).toEqual(["上傳中…"]);
  });

  it("剪貼簿不可用時把路徑講出來，讓人自己輸入（總比一句「複製失敗」好）", async () => {
    clipboardWrite.mockRejectedValue(new Error("denied") as never);
    const store = useSiteStore();
    store.meta.persistDir = "/home/nathan/persistent-data";
    mountDrawer();
    await flushPromises();
    el<HTMLButtonElement>('[data-act="copy-persist"]').click();
    await flushPromises();
    expect(toasts.at(-1)!.title).toBe("無法自動複製");
    expect(toasts.at(-1)!.body).toContain("/home/nathan/persistent-data");
  });

  it("提示上的複製鍵只複製路徑本身", async () => {
    const store = useSiteStore();
    store.meta.persistDir = "/home/nathan/persistent-data";
    mountDrawer();
    await flushPromises();
    el<HTMLButtonElement>('[data-act="copy-persist"]').click();
    await flushPromises();
    expect(clipboardWrite).toHaveBeenCalledWith("/home/nathan/persistent-data");
    expect(clipboardWrite).toHaveBeenCalledTimes(1);
    expect(toasts.at(-1)!.title).toBe("已複製路徑");
  });

  it("選檔上傳：送出去之後欄位要清掉，同一個檔連傳兩次才會再觸發 change", async () => {
    mountDrawer();
    await flushPromises();
    const input = el<HTMLInputElement>('[data-testid="drawer-file"]');
    Object.defineProperty(input, "files", {
      configurable: true,
      value: [new File(["x"], "a.png", { type: "image/png" })],
    });
    input.dispatchEvent(new Event("change"));
    await flushPromises();
    expect(uploadCalls()).toHaveLength(1);
    expect(input.value).toBe("");
  });

  it("選檔對話框按了取消（沒有檔）就什麼都不做", async () => {
    mountDrawer();
    await flushPromises();
    const input = el<HTMLInputElement>('[data-testid="drawer-file"]');
    input.dispatchEvent(new Event("change"));
    await flushPromises();
    expect(uploadCalls()).toHaveLength(0);
    expect(toasts).toHaveLength(0);
  });

  it("開新分頁**刻意不關抽屜**：兩邊同時連著才是安全的交接", async () => {
    const open = vi.fn();
    vi.stubGlobal("open", open);
    const w = mountDrawer();
    await flushPromises();
    el<HTMLButtonElement>('[data-act="pop"]').click();
    await flushPromises();
    expect(open).toHaveBeenCalledWith("/session/sid1/", "_blank", "noopener");
    expect(w.emitted("close")).toBeUndefined();
    expect(document.querySelector('[data-testid="drawer"]')).not.toBeNull();
    vi.unstubAllGlobals();
  });

  it("兩條提示時才輪播，而且輪不到的那一條要退出 Tab 序與無障礙樹", async () => {
    vi.useFakeTimers();
    const store = useSiteStore();
    store.meta.persistDir = "/home/nathan/persistent-data";
    mountDrawer();
    await nextTick();
    const persist = el<HTMLElement>('[data-testid="drawer-persist"]');
    const mouse = el<HTMLElement>('[data-testid="drawer-mouse"]');
    expect(persist.dataset.on).toBe("true");
    expect(mouse.dataset.on).toBe("false");
    // 沒露臉的那一條在畫面上是透明的，留在 Tab 序裡等於有一顆看不見的按鈕
    expect(mouse.hasAttribute("inert")).toBe(true);
    expect(mouse.getAttribute("aria-hidden")).toBe("true");
    vi.advanceTimersByTime(6000);
    await nextTick();
    expect(persist.dataset.on).toBe("false");
    expect(mouse.dataset.on).toBe("true");
    expect(persist.hasAttribute("inert")).toBe(true);
  });

  it("只有一條提示時不轉（一條也在轉的話那不是輪播，是閃爍）", async () => {
    vi.useFakeTimers();
    mountDrawer();
    await nextTick();
    expect(document.querySelector('[data-testid="drawer-persist"]')).toBeNull();
    const mouse = el<HTMLElement>('[data-testid="drawer-mouse"]');
    expect(mouse.dataset.on).toBe("true");
    vi.advanceTimersByTime(30_000);
    await nextTick();
    // 唯一那一條永遠露臉，也永遠不該被設成 inert
    expect(mouse.dataset.on).toBe("true");
    expect(mouse.hasAttribute("inert")).toBe(false);
  });

  it("🔴 焦點還在提示區裡就不准恢復輪播：不然使用者按著的那顆鍵會變成 inert", async () => {
    vi.useFakeTimers();
    const store = useSiteStore();
    store.meta.persistDir = "/home/nathan/persistent-data";
    mountDrawer();
    await nextTick();
    const hints = el<HTMLElement>('[data-testid="drawer-hints"]');
    const persist = el<HTMLElement>('[data-testid="drawer-persist"]');
    hints.dispatchEvent(new Event("pointerenter"));
    hints.dispatchEvent(new Event("focusin"));
    vi.advanceTimersByTime(12_000);
    await nextTick();
    expect(persist.dataset.on).toBe("true"); // hover＋focus 都在，停住不動
    // 只把滑鼠移開、焦點還在（點完複製鍵就是這個狀態），仍然不可以恢復
    hints.dispatchEvent(new Event("pointerleave"));
    vi.advanceTimersByTime(12_000);
    await nextTick();
    expect(persist.dataset.on).toBe("true");
    // 焦點也離開了才恢復
    hints.dispatchEvent(new Event("focusout"));
    vi.advanceTimersByTime(6000);
    await nextTick();
    expect(persist.dataset.on).toBe("false");
  });

  it("🔴 關閉要等滑走的那一個過渡，別的元素的過渡不算數", async () => {
    const w = mountDrawer();
    await flushPromises();
    const panel = el<HTMLElement>(".drawer__panel");
    el<HTMLButtonElement>('[data-testid="drawer-close"]').click();
    await nextTick();
    expect(el<HTMLElement>('[data-testid="drawer"]').dataset.open).toBeUndefined();
    // ⚠ 工具列那幾顆按鈕各有 120ms 的 background 過渡，而且 transitionend 會冒泡：
    //   不濾的話抽屜會在滑到 75% 時整個消失
    transitionEnd(el<HTMLElement>('[data-testid="drawer-close"]'), "background-color");
    expect(w.emitted("close")).toBeUndefined();
    transitionEnd(panel, "opacity");
    expect(w.emitted("close")).toBeUndefined();
    transitionEnd(panel, "transform");
    expect(w.emitted("close")).toHaveLength(1);
  });

  it("🔴 prefers-reduced-motion 下 transitionend 永遠不會來，400ms 保底要接住", async () => {
    vi.useFakeTimers();
    const w = mountDrawer();
    await nextTick();
    el<HTMLButtonElement>('[data-testid="drawer-close"]').click();
    vi.advanceTimersByTime(399);
    expect(w.emitted("close")).toBeUndefined();
    vi.advanceTimersByTime(1);
    expect(w.emitted("close")).toHaveLength(1);
  });

  it("過渡到了就把保底的計時器收掉，close 只會送一次", async () => {
    vi.useFakeTimers();
    const w = mountDrawer();
    await nextTick();
    el<HTMLButtonElement>('[data-testid="drawer-close"]').click();
    transitionEnd(el<HTMLElement>(".drawer__panel"), "transform");
    vi.advanceTimersByTime(1000);
    expect(w.emitted("close")).toHaveLength(1);
  });

  it("遮罩也能關；關到一半再按一次不會關第二遍", async () => {
    vi.useFakeTimers();
    const w = mountDrawer();
    await nextTick();
    el<HTMLElement>('[data-act="scrim"]').click();
    el<HTMLElement>('[data-act="scrim"]').click();
    el<HTMLButtonElement>('[data-testid="drawer-close"]').click();
    vi.advanceTimersByTime(1000);
    expect(w.emitted("close")).toHaveLength(1);
  });

  it("字級鍵直接調 iframe 裡的 term，畫面上要看得到現在是幾 px", async () => {
    mountDrawer();
    await flushPromises();
    const f = await loadFrame();
    expect(el<HTMLElement>('[data-testid="drawer-font-value"]').textContent).toBe("14px");
    el<HTMLButtonElement>('[data-testid="drawer-font-inc"]').click();
    await nextTick();
    expect(f.term.options.fontSize).toBe(15);
    expect(el<HTMLElement>('[data-testid="drawer-font-value"]').textContent).toBe("15px");
    el<HTMLButtonElement>('[data-testid="drawer-font-dec"]').click();
    await nextTick();
    expect(f.term.options.fontSize).toBe(14);
  });

  it("哪一顆 ttyd 在服務要寫出來；舊的 view 記錄沒有這個值就不畫", async () => {
    mountDrawer({ flavor: "Rust" });
    await flushPromises();
    expect(el<HTMLElement>('[data-testid="drawer-bin"]').textContent).toBe("Rust");
    for (const w of mounted.splice(0)) w.unmount();
    document.body.innerHTML = "";
    mountDrawer({ flavor: null });
    await flushPromises();
    expect(document.querySelector('[data-testid="drawer-bin"]')).toBeNull();
  });
});
