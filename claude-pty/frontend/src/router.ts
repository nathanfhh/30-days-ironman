import { createRouter, createWebHistory, type RouteLocationNormalized } from "vue-router";

import { useSiteStore } from "@/stores/site";
import LoginView from "@/views/LoginView.vue";

/*
 * 三條路由，與舊版三個頁面一一對應（`/`、`/login`、`/account`）。網址一個字都不換：
 * 書籤、e2e 的 `page.goto`、nginx 的 try_files 都吃同一組。
 *
 * ⚠ 進頁的守衛只做一件事：**還不知道自己是誰的時候先問一次**（`/api/account/bootstrap`，
 *   身分與這個帳號的處境同一條回應），401 就去登入頁。這是 SPA 版的 authn gate；真正的
 *   gate 仍然在後端（每一支 `/api/*` 都過），前端這一層只是省掉「先畫一頁再被踢走」的
 *   閃動，不是安全邊界。
 */
export const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: "/",
      name: "sessions",
      component: () => import("@/views/SessionsView.vue"),
      meta: { title: "Sessions · claude-pty" },
    },
    { path: "/login", name: "login", component: LoginView, meta: { title: "登入 · claude-pty" } },
    {
      path: "/account",
      name: "account",
      component: () => import("@/views/AccountView.vue"),
      meta: { title: "帳號 · claude-pty" },
    },
  ],
});

/* 分頁標題。舊版是每個模板自己的 `{% block title %}`（見 base.html），SPA 沒有那一步，
 * 所以掛在路由上——**三個字串逐字照舊**（`Sessions · claude-pty` 的 S 是大寫、分隔號是
 * 全形間隔號 U+00B7 前後各一個空格）。
 * ⚠ 掛 afterEach 不是 beforeEach：標題該在**到了**那一頁之後才換。導覽被守衛擋下來時
 *   （未登入被送回 /login）標題若已經先改成目的地，畫面與分頁標籤會各說各話。 */
router.afterEach((to) => {
  const title = to.meta.title;
  if (typeof title === "string") document.title = title;
});

/**
 * 進頁的守衛。**抽成具名的匯出函式而不是就地寫在 `beforeEach` 裡**：它是這個 SPA 對
 * 「你是誰、該去哪」唯一的判斷，值得單獨測——而測試那邊自己建的 router 掛得上同一支，
 * 驗到的就是正式版跑的那一份，不是照抄一份會漂走的複本。
 */
export async function authGuard(to: RouteLocationNormalized) {
  const store = useSiteStore();
  /* 問一次就記著（`identityLoaded` 擋著）。cookie 中途失效時，api() 收到 401 會走
   * `createUnauthorizedHandler`：它先把身分清掉（`dropIdentity`，`identityLoaded` 一併翻回
   * false）再導回登入頁，所以那一次導覽會回到這裡**重新**探測一遍。那才是重新確認身分的
   * 入口；而它成立的前提就是那面旗被翻回去了：沒翻的話這裡不重探、`store.user` 還在，
   * 下面那條會把人彈回 `/`（2026-08-26 修，見 stores/site 的 dropIdentity）。
   *
   * ⚠ **登入頁也要問。** 曾經在這裡跳過它，理由是「那一頁本來就是給沒登入的人看的，那一發
   *   必定 401」——而那個理由漏掉了一種人：**已經登入、然後直接冷載入 `/login` 的**。
   *   舊版對他是伺服端一句 302 回 `/`；跳過探測的話，他會停在登入表單前面。
   *   而伺服端那句 302 在正式部署上**不會發生**：nginx 直接把 `index.html` 從磁碟吐出來
   *   （`deploy/nginx-ui/vue/ui.conf` 的 `location = /login`），根本不經過 Flask。
   *   代價是沒登入的人在登入頁上多一趟往返、拿一個 401。那一發的 401 是**答案**不是錯誤，
   *   所以探測不走全域的「導回登入頁」（見 store 的 `probe`）——否則會在一個正在前往
   *   `/login` 的導覽中間再開一次導覽。
   * ⚠ 這裡是 `await`，所以探測沒回來之前**不會有任何頁面被畫出來**：已登入者看到的是一瞬間
   *   的空白然後直接到 `/`，不是「先看到登入表單再被彈走」。 */
  if (!store.identityLoaded) await store.loadIdentity();
  if (to.path === "/login") {
    // 已登入者不該停在登入頁——與「未登入訪問管理頁 → 導向 /login」互為對稱
    return store.user ? { path: "/" } : true;
  }
  return store.user ? true : { path: "/login" };
}

router.beforeEach(authGuard);
