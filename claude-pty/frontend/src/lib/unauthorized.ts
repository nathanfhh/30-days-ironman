import type { Router } from "vue-router";

import { toast } from "@/lib/toast";
import { useSiteStore } from "@/stores/site";

/**
 * 收到 401 之後要做的事（`api/client` 的 `setUnauthorizedHandler` 吃這一支）。
 *
 * ⚠ **抽成具名的工廠，不就地寫在 main.ts 裡。** 兩個理由，與 router 把 `authGuard` 抽出來
 *   是同一個：`main.ts` 不在單元測試的覆蓋範圍內（見 vite.config.ts 的 coverage.exclude），
 *   寫在那裡等於這段沒有人守；而在測試裡照抄一份的複本遲早與正式版漂走，漂走的那天測試
 *   仍然全綠。這樣寫，測試掛上去的就是正式版跑的那一份。
 *
 * 做三件事，順序有意義：
 *
 *   1. **先把身分清掉**（`dropIdentity`）。不清的話 router 的守衛會看到 `store.user` 還在，
 *      於是 `to.path === "/login"` 那條判他「已登入」並回 `{ path: "/" }`，把這次導覽原地
 *      彈回去，畫面上就是「只跳了一則 toast，人還在原頁」（2026-08-26 Nathan 實測回報）。
 *      `identityLoaded` 也跟著翻回 false，下一次守衛才會重新探測。
 *   2. **只導一次**。cookie 一失效，當下所有在飛的請求會**同時**拿到 401（列表輪詢、憑證
 *      徽章、帳號頁那幾條），每一發都 push 的話 vue-router 會對重複導覽出 warning，而且
 *      後一發會把前一發打斷。同一輪靠旗子擋，導覽結束之後遲到的那幾發靠「已經在登入頁上
 *      就不做」那一關擋（見下面），兩道各擋一種，少哪一道都會多一則重複的通知。
 *   3. 講一句話，而且**只有這一句**。各呼叫端那些「◯◯失敗／未登入」由 `toastError` 統一
 *      吞掉（見 lib/toast），否則畫面上會堆著三四則毫無資訊的字，真正該讀的那則被埋在裡面。
 *
 * ⚠ toast 用 `toast()` 不是 `toastAfterNav()`。後者是寄在 sessionStorage 給**下一次整份
 *   app 重新載入**的人接（main.ts 的 `drainPendingToast`），而這裡是 SPA 之內換頁，
 *   main.ts 不會再跑一次，寄過去的話那則通知要到不知道多久以後才會冒出來。SPA 內換頁不會
 *   清掉畫面上的 toast，直接發就是對的。
 */
export function createUnauthorizedHandler(router: Router): () => void {
  let redirecting = false;
  return () => {
    /* 已經在登入頁上了就整段不做，**連身分都不清**。兩個理由：
     *
     *   · **遲到的 401**。導覽完成之後才回來的那幾發（輪詢在被導走的前一刻剛送出、
     *     AccountView 最慢的那條）不該再發一則一模一樣的通知、再 push 一次同一個路由。
     *     旗子擋不到它們：那時 push 已經 resolve、旗子早就放下了。
     *   · **剛登入成功的那個窗口**。登入的回應收下身分（`adoptIdentity`）到 `push("/")`
     *     完成之間，人還站在 `/login` 上。這中間若有別的請求 401（例如上一輪還沒回來的
     *     那一發），清身分會把剛收下的那份洗掉，使用者按了「進入控制台」卻留在原地。
     *
     * ⚠ 旗子仍然要留著：**同一輪的並行 401** 那時 push 還沒 resolve、`currentRoute`
     *   還停在來源頁，過得了上面這一關，靠旗子擋。 */
    if (router.currentRoute.value.path === "/login") return;
    useSiteStore().dropIdentity();
    if (redirecting) return;
    redirecting = true;
    toast("登入已失效，請重新登入", "warning", {
      body: "這個帳號的登入狀態已經在伺服器上被作廢（例如在另一個分頁改了密碼）。",
    });
    void router.push("/login").finally(() => {
      redirecting = false;
    });
  };
}
