/*
 * ⚠ `app.css` 由這裡 import，**引用的是 `server/static/css/app.css` 原檔**。
 *   階段 4 的前提是 CSS 一字不改（見計畫）：複製一份等於同一份樣式有兩個真相，而截圖
 *   golden 分不出「樣式改了」與「複本沒跟上」。Vite 會把它打包成帶雜湊的 /assets/*.css。
 *   字體與 Font Awesome 仍由 `/static/vendor/…` 供應（見 index.html）——那些是原檔，
 *   不該被雜湊改名。
 */
import "../../server/static/css/app.css";

import { createPinia } from "pinia";
import { createApp } from "vue";

import { setUnauthorizedHandler } from "@/api/client";
import App from "@/App.vue";
import { drainPendingToast } from "@/lib/toast";
import { router } from "@/router";
import { useSiteStore } from "@/stores/site";

const app = createApp(App);
// ⚠ pinia 要在 router 之前裝：router 的守衛第一件事就是 `useSiteStore()`。
app.use(createPinia());
app.use(router);

// 401 改走 SPA 導向（舊版是整頁 location.href）。語意不變：cookie 過期就回登入頁。
setUnauthorizedHandler(() => {
  void router.push("/login");
});

// 伺服端環境事實（behind_proxy / persist_dir / 頁尾版本…）。目前只是把預設值放好，
// 階段 3 的端點一上線就在 store 裡換掉。
useSiteStore().loadMeta();

// 上一頁寄放的通知（登出、與 legacy 版互跳）
drainPendingToast();

app.mount("#app");
