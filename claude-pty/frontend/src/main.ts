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

// 公開的伺服端事實（behind_proxy / persist_dir / 頁尾版本 / 登入頁插畫）。**不等身分**：
// 登入頁的頁尾與插畫需要它，而那一頁本來就是未登入的人在看。
// 不 await：它失敗或慢都不該擋住第一次繪製（拿不到就留白，見 store 的說明）。
void useSiteStore().loadPublicMeta();

// 上一頁寄放的通知。SPA 之內換頁不會清掉 toast，但**離開 SPA 的那條路還在**：登出之後是
// 整頁跳轉回登入頁，當下發的那一則得有人接手（見 lib/toast 的 toastAfterNav）。
drainPendingToast();

app.mount("#app");
