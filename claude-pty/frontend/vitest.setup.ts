/*
 * jsdom 沒有實作的幾個瀏覽器 API，元件在 mount 時就會用到。
 * 一律補**最小**的替身：補得比實際多的話，測試會對著一個真實瀏覽器沒有的行為變綠。
 */
if (!("matchMedia" in globalThis.window)) {
  Object.defineProperty(globalThis.window, "matchMedia", {
    writable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }),
  });
}

// picker / 選單的定位（anchorPanel）會呼叫它；jsdom 一律回 0，那對「有沒有定位」的斷言
// 已經夠用，我們不在單元測試裡驗座標（那是 e2e 與截圖 golden 的工作）。
if (!globalThis.ResizeObserver) {
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
}

if (!globalThis.requestAnimationFrame) {
  globalThis.requestAnimationFrame = ((cb: FrameRequestCallback) =>
    setTimeout(() => cb(0), 0) as unknown as number) as typeof requestAnimationFrame;
  globalThis.cancelAnimationFrame = ((id: number) =>
    clearTimeout(id)) as typeof cancelAnimationFrame;
}
