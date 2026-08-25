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
    { path: "/", name: "sessions", component: () => import("@/views/SessionsView.vue") },
    { path: "/login", name: "login", component: LoginView },
    { path: "/account", name: "account", component: () => import("@/views/AccountView.vue") },
  ],
});

router.beforeEach(async (to: RouteLocationNormalized) => {
  const store = useSiteStore();
  if (!store.identityLoaded) await store.loadIdentity();
  if (to.path === "/login") {
    // 已登入者不該停在登入頁——與「未登入訪問管理頁 → 導向 /login」互為對稱
    return store.user ? { path: "/" } : true;
  }
  return store.user ? true : { path: "/login" };
});
