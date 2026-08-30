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
import { createUnauthorizedHandler } from "@/lib/unauthorized";
import { router } from "@/router";
import { useSiteStore } from "@/stores/site";

const app = createApp(App);
// ⚠ pinia 要在 router 之前裝：router 的守衛第一件事就是 `useSiteStore()`。
app.use(createPinia());
app.use(router);

// 401 改走 SPA 導向（舊版是整頁 location.href）。語意不變：cookie 過期就回登入頁。
// ⚠ 但「push 一下」**不等於**回得去：SPA 換頁時 store 是活的，身分沒清掉的話守衛會判他
//   仍然登入著，把這次導覽原地彈回原頁（2026-08-26 Nathan 實測回報）。清身分、只導一次、
//   發哪一則 toast 這三件事都在 `lib/unauthorized`，那裡有完整的理由；放在那邊也才進得了
//   單元測試的覆蓋範圍：這個檔被 coverage 排除（見 vite.config.ts）。
setUnauthorizedHandler(createUnauthorizedHandler(router));

// 公開的伺服端事實（behind_proxy / 登入頁插畫）。**不等身分**：登入頁的插畫需要它，
// 而那一頁本來就是未登入的人在看。
// ⚠ 版號與主機路徑**不在這一發裡**（2026-08-26 裁示 L4：登入前不得取得），它們跟著
//   `/api/account/bootstrap` 一起回來。
// 不 await：它失敗或慢都不該擋住第一次繪製（拿不到就留白，見 store 的說明）。
void useSiteStore().loadPublicMeta();

// 上一頁寄放的通知。
app.mount("#app");
