import { createRouter, createWebHistory, type RouteLocationNormalized } from "vue-router";

import { useSiteStore } from "@/stores/site";
import LoginView from "@/views/LoginView.vue";

/*
 * 三條路由，與舊版三個頁面一一對應（`/`、`/login`、`/account`）。網址一個字都不換：
 * 書籤、e2e 的 `page.goto`、nginx 的 try_files 都吃同一組。
 *
 * ⚠ 進頁的守衛只做一件事：**還不知道自己是誰的時候先問一次 `/api/auth/me`**，401 就去
 *   登入頁。這是 SPA 版的 authn gate；真正的 gate 仍然在後端（每一支 `/api/*` 都過），
 *   前端這一層只是省掉「先畫一頁再被踢走」的閃動，不是安全邊界。
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

router.beforeEach(async (to: RouteLocationNormalized) => {
  const store = useSiteStore();
  /* ⚠ **登入頁不問「我是誰」。** 那一頁本來就是給沒登入的人看的，那一發必定 401——
   *   白花一趟往返、在 console 留一行紅字，而且 401 的統一處理是「導回登入頁」，
   *   在登入頁上等於什麼都沒做。
   *   「已登入者不該停在登入頁」不必靠它：伺服器在吐這個殼**之前**就會先導走
   *   （見 web.login_page），而 SPA 內部從別的頁走過來時身分已經在記憶體裡。 */
  if (to.path === "/login") {
    return store.user ? { path: "/" } : true;
  }
  // 一個 app 生命週期問一次就夠（`identityLoaded` 擋著）。cookie 中途失效時，
  // api() 收到 401 會把人導回登入頁，那才是重新確認身分的入口。
  if (!store.identityLoaded) await store.loadIdentity();
  return store.user ? true : { path: "/login" };
});
