/// <reference types="vitest/config" />
import { fileURLToPath, URL } from "node:url";

import vue from "@vitejs/plugin-vue";
import { defineConfig } from "vite";

/*
 * ⚠ build 的產物直接落在 `server/static/dist/`（見 outDir）。
 *
 * 理由：dev 與 e2e 由 in-thread 的 Flask serve 這份 dist（管兩版的 `CLAUDE_PTY_UI` 切換器
 * 已隨 legacy 於 2026-08-26 一起移除，現在只有一種 UI），
 * 而 Flask 的 static 根就是 `server/static/`——產物放在別處的話，那條路要另外開一個
 * 只在 dev 才存在的路由，而「只在測試環境存在的路徑」正是最容易與 prod 分岔的東西。
 * prod 由 nginx 直出同一份（node 階段 build → COPY 進 nginx image，見 deploy/Dockerfile）。
 *
 * ⚠ dist **不進版控**（claude-pty/.gitignore 只加了那一行路徑）。
 */
export default defineConfig({
  plugins: [vue()],
  // 產物由 nginx 掛在網站根（/assets/…），dev 由 Flask serve 同樣的路徑。
  base: "/",
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  server: {
    fs: {
      /*
       * ⚠ `app.css` 與 vendor 資源住在 `server/static/`，也就是 Vite root 的外面。
       *   dev server 預設不給讀 root 之外的檔案，所以要放行上一層。
       *   **一律引用原檔、不複製**：階段 4 的前提是 CSS 一字不改（見計畫），複製一份
       *   等於同一份樣式有兩個真相，而截圖 golden 分不出「樣式改了」與「複本沒跟上」。
       */
      allow: [".."],
    },
  },
  build: {
    outDir: fileURLToPath(new URL("../server/static/dist", import.meta.url)),
    // outDir 在 root 外面，Vite 預設不敢清；這裡是我們自己的產物目錄，清掉是對的。
    emptyOutDir: true,
    sourcemap: false,
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./vitest.setup.ts"],
    include: ["src/**/*.spec.ts"],
    coverage: {
      provider: "v8",
      reporter: ["text", "lcov"],
      include: ["src/**/*.{ts,vue}"],
      exclude: ["src/main.ts", "src/**/*.spec.ts"],
      thresholds: {
        /* 計畫的決定 8 訂的是 70%，那是「至少要有」的底線。實際已經到 94%，門檻跟著收緊到
           90%：留在 70 的話，之後刪掉一整塊測試也還是綠的，那條線就等於沒有在守什麼。 */
        lines: 90,
      },
    },
  },
});
