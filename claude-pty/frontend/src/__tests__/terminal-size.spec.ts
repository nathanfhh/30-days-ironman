import { flushPromises } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { defineComponent, ref } from "vue";

import { FONT_KEY, useTerminalSize, type TermLike } from "@/composables/useTerminalSize";

/*
 * 尺寸同步的兩道閘（階段 1.5 的成果）。這一支守的是**時序**，而時序正是唯一一種
 * 「看畫面永遠看不出來」的 bug：送出去的欄列數與畫面對不上時，沒有錯誤、沒有跡象。
 *
 * 替身只做兩件事：一個假的 `window.term`（xterm 的 Terminal），與一個假的
 * `getAnimations()`（抽屜的滑入過渡）。ResizeObserver 用 vitest.setup 裡那個 no-op 替身，
 * 所以「盒子安靜了」這一道靠 `lastBoxAt` 的初始值成立——那正是它在真環境裡的起點。
 */

interface Harness {
  term: TermLike & { fire: (cols: number, rows: number) => void };
  frame: HTMLIFrameElement;
  panel: HTMLElement;
  anims: Animation[];
}

function makeHarness(fontSize = 14): Harness {
  let onResizeCb: ((d: { cols: number; rows: number }) => void) | null = null;
  const term = {
    cols: 80,
    rows: 24,
    options: { fontSize },
    onResize(cb: (d: { cols: number; rows: number }) => void) {
      onResizeCb = cb;
    },
    fire(cols: number, rows: number) {
      term.cols = cols;
      term.rows = rows;
      onResizeCb?.({ cols, rows });
    },
  };
  const frame = document.createElement("iframe");
  // contentWindow 在 jsdom 裡是唯讀的 getter，直接蓋掉一個帶 term 的替身
  Object.defineProperty(frame, "contentWindow", {
    value: { term, devicePixelRatio: 1, dispatchEvent: () => true },
    configurable: true,
  });
  Object.defineProperty(frame, "contentDocument", { value: null, configurable: true });
  const panel = document.createElement("div");
  const anims: Animation[] = [];
  (panel as HTMLElement & { getAnimations: () => Animation[] }).getAnimations = () => anims;
  return { term, frame, panel, anims };
}

/** 掛一個只做 useTerminalSize 的宿主元件，拿到它回傳的介面。
 *  （composable 用了 onBeforeUnmount，所以一定要有元件實例才呼叫得了。） */
function mountSize(harness: Harness) {
  let api!: ReturnType<typeof useTerminalSize>;
  const closing = ref(false);
  const Host = defineComponent({
    setup() {
      api = useTerminalSize({
        sid: "sid1",
        frame: ref(harness.frame),
        panel: ref(harness.panel),
        closing,
      });
      return () => null;
    },
  });
  const wrapper = mount(Host);
  mounted.push(wrapper);
  return { api, closing, wrapper };
}

/* ⚠ 每一支測完都要拆掉。composable 會排 debounce 與遞迴的 `boxSettled` 輪詢——留著的話它們
   會在**下一支**測試裡醒來、打到那一支剛換上的 fetch 替身，於是計數莫名其妙變成 3。
   （第一次寫的時候就是這樣紅的，而錯的看起來是被算到的那一支。） */
const mounted: { unmount: () => void }[] = [];

const resizeCalls = (): { url: string; body: unknown }[] =>
  (globalThis.fetch as unknown as { mock: { calls: [string, RequestInit][] } }).mock.calls
    .filter(([url]) => String(url).includes("/resize"))
    .map(([url, init]) => ({ url: String(url), body: JSON.parse(String(init.body)) }));

describe("useTerminalSize", () => {
  afterEach(() => {
    for (const w of mounted.splice(0)) w.unmount();
  });

  beforeEach(() => {
    vi.useRealTimers();
    localStorage.clear();
    globalThis.fetch = vi.fn(async () => new Response(null, { status: 204 })) as typeof fetch;
  });

  it("開啟時送一發，而且**一定帶 redraw**（尺寸與上次相同時 docker 不會產生 SIGWINCH）", async () => {
    const h = makeHarness();
    const { api } = mountSize(h);
    api.attach();
    await new Promise((r) => setTimeout(r, 400));
    await flushPromises();
    const calls = resizeCalls();
    expect(calls).toHaveLength(1);
    expect(calls[0].body).toMatchObject({ cols: 80, rows: 24, redraw: true });
    expect(calls[0].url).toContain("/api/sessions/sid1/resize");
  });

  it("🔴 抽屜還在滑入就不送：等它的 finished 才送（第一道閘）", async () => {
    const h = makeHarness();
    let release!: () => void;
    const finished = new Promise<void>((r) => (release = r));
    // 一個「還在跑」的滑入過渡
    h.anims.push({ transitionProperty: "transform", finished } as unknown as Animation);
    const { api } = mountSize(h);
    api.attach();
    await new Promise((r) => setTimeout(r, 400));
    await flushPromises();
    expect(resizeCalls()).toHaveLength(0); // 還在滑，一發都不能送
    release();
    await flushPromises();
    await new Promise((r) => setTimeout(r, 20));
    await flushPromises();
    expect(resizeCalls()).toHaveLength(1);
  });

  it("🔴 只等「滑入」那一個過渡：無限動畫不可以把 /resize 永遠卡住", async () => {
    const h = makeHarness();
    // 一條永遠不會結束的裝飾動畫（呼吸燈、無限旋轉的圖示都算）。
    // 它沒有 transitionProperty，所以不該被等。
    h.anims.push({
      animationName: "pulse",
      finished: new Promise<void>(() => {}),
    } as unknown as Animation);
    const { api } = mountSize(h);
    api.attach();
    await new Promise((r) => setTimeout(r, 400));
    await flushPromises();
    expect(resizeCalls()).toHaveLength(1);
  });

  it("🔴 後來的那一發作廢先前排的（token）：連按時只送最後一次", async () => {
    const h = makeHarness();
    const { api } = mountSize(h);
    api.attach();
    // debounce 還沒到就再排一發，並把尺寸改掉
    api.syncSize();
    h.term.cols = 200;
    h.term.rows = 50;
    api.syncSize();
    await new Promise((r) => setTimeout(r, 400));
    await flushPromises();
    const calls = resizeCalls();
    expect(calls).toHaveLength(1);
    // ⚠ 尺寸是**送出的當下**才讀的，不是排程時抓走的——所以是最終值
    expect(calls[0].body).toMatchObject({ cols: 200, rows: 50 });
  });

  it("抽屜關了就不再送（排程中的那一發要當場作廢）", async () => {
    const h = makeHarness();
    const { api, closing } = mountSize(h);
    api.attach();
    closing.value = true;
    await new Promise((r) => setTimeout(r, 400));
    await flushPromises();
    expect(resizeCalls()).toHaveLength(0);
  });

  it("字級夾在 8–32 並存起來；到界之後不再變", async () => {
    const h = makeHarness(9);
    const { api } = mountSize(h);
    api.attach();
    api.bumpFont(-1);
    expect(h.term.options!.fontSize).toBe(8);
    expect(localStorage.getItem(FONT_KEY)).toBe("8");
    api.bumpFont(-1); // 已經到底，不該再動
    expect(h.term.options!.fontSize).toBe(8);
    api.bumpFont(1);
    expect(h.term.options!.fontSize).toBe(9);
  });

  it("存過的字級會被套回來，而且**一律夾回界內**（手改成 999 不該讓人回不去）", async () => {
    localStorage.setItem(FONT_KEY, "999");
    const h = makeHarness(14);
    const { api } = mountSize(h);
    api.attach();
    expect(h.term.options!.fontSize).toBe(32);
    expect(api.fontSize.value).toBe(32);
  });

  it("xterm 自己 fit 之後會補送一發（值在送出當下才讀）", async () => {
    const h = makeHarness();
    const { api } = mountSize(h);
    api.attach();
    await new Promise((r) => setTimeout(r, 400));
    await flushPromises();
    expect(resizeCalls()).toHaveLength(1);
    h.term.fire(120, 40);
    await new Promise((r) => setTimeout(r, 400));
    await flushPromises();
    const calls = resizeCalls();
    expect(calls).toHaveLength(2);
    expect(calls[1].body).toMatchObject({ cols: 120, rows: 40 });
    // ⚠ 第二發**不帶 redraw**：尺寸真的變了，docker resize 自己會產生 SIGWINCH
    expect(calls[1].body).toMatchObject({ redraw: false });
  });

  it("term 還沒出現就先不接，出現之後再接上（ttyd 的 JS 比 load 事件晚）", async () => {
    const h = makeHarness();
    const hidden = h.frame.contentWindow as unknown as { term?: TermLike };
    const saved = hidden.term;
    delete hidden.term;
    const { api } = mountSize(h);
    api.attach();
    await new Promise((r) => setTimeout(r, 400));
    await flushPromises();
    expect(resizeCalls()).toHaveLength(0);
    hidden.term = saved;
    await new Promise((r) => setTimeout(r, 500));
    await flushPromises();
    expect(resizeCalls().length).toBeGreaterThan(0);
  });
});
