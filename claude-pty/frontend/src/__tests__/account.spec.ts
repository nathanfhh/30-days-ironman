import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createMemoryHistory, createRouter, type Router } from "vue-router";

import AccountView from "@/views/AccountView.vue";
import App from "@/App.vue";
import LoginView from "@/views/LoginView.vue";
import { toasts } from "@/lib/toast";
import { useSiteStore } from "@/stores/site";

/* 帳號頁。這一支守的是**每一塊在不同狀態下畫成什麼**：一般使用者與管理員看到的東西不一樣、
 * 憑證設過沒、GitLab 代理起不起得來、ttyd 那張表的三態。golden 只錄了兩個定格
 * （account-user / account-admin），其餘狀態只有這裡問得到。 */

interface Route {
  status?: number;
  body?: unknown;
}
type Routes = Record<string, Route | ((init?: RequestInit) => Route)>;

const calls: { url: string; method: string; body?: unknown }[] = [];

function installFetch(routes: Routes): void {
  globalThis.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const path = url.split("?")[0];
    calls.push({
      url,
      method: init?.method ?? "GET",
      body: init?.body ? JSON.parse(String(init.body)) : undefined,
    });
    const entry = routes[path];
    if (!entry) throw new Error(`測試沒有登記這條路徑：${path}`);
    const r = typeof entry === "function" ? entry(init) : entry;
    const status = r.status ?? 200;
    if (status === 204) return new Response(null, { status });
    return new Response(JSON.stringify(r.body ?? {}), {
      status,
      headers: { "Content-Type": "application/json" },
    });
  }) as typeof fetch;
}

const CREDENTIALS = (ok: boolean) => ({
  claude: {
    cli: "claude",
    brand: "anthropic",
    ok,
    state: ok ? "ok" : "bad",
    label: ok ? "Claude 憑證已設定" : "Claude 未設定憑證",
    detail: "d",
  },
});

const USERS = {
  users: [
    { id: 1, username: "alice", is_admin: true, created_at: "2026-08-20T10:00:00+00:00" },
    { id: 2, username: "bob", is_admin: false, created_at: "2026-08-21T10:00:00+00:00" },
  ],
  total: 2,
  limit: 10,
  offset: 0,
};

const INSPECT = {
  psutil: true,
  views: [
    {
      owner: "alice",
      session_id: "sid1",
      session_name: "重構",
      port: 41000,
      pid: 4242,
      alive: true,
      ttyd_bin: "ttyd",
      created_at: "2026-08-25T03:00:00+00:00",
      proc: { listening: ["0.0.0.0:41000"], clients: 1, bin: "ttyd" },
    },
  ],
  orphans: [],
};

let router: Router;
const mounted: VueWrapper[] = [];

beforeEach(async () => {
  calls.length = 0;
  toasts.splice(0, toasts.length);
  localStorage.clear();
  setActivePinia(createPinia());
  vi.useRealTimers();
  router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/", component: { template: "<div />" } },
      { path: "/login", component: LoginView },
      { path: "/account", component: AccountView },
    ],
  });
});

afterEach(() => {
  for (const w of mounted.splice(0)) w.unmount();
  document.body.innerHTML = "";
});

async function mountAccount(
  isAdmin: boolean,
  meta: Partial<ReturnType<typeof useSiteStore>["meta"]> = {},
) {
  const store = useSiteStore();
  store.user = { id: 1, username: "alice", is_admin: isAdmin };
  Object.assign(store.meta, meta);
  await router.push("/account");
  await router.isReady();
  const w = mount(App, { global: { plugins: [router] }, attachTo: document.body });
  mounted.push(w);
  await flushPromises();
  return w;
}

describe("AccountView：一般使用者", () => {
  beforeEach(() => {
    installFetch({
      "/api/users/me/token": { status: 204 },
    });
  });

  it("管理員那三塊整塊不畫（不是把按鈕停用）", async () => {
    const store = useSiteStore();
    store.setCredentials(CREDENTIALS(false));
    const w = await mountAccount(false);
    expect(w.find('[data-testid="token-state"]').exists()).toBe(true);
    expect(w.find('[data-testid="pw-form"]').exists()).toBe(true);
    // ⚠ 後端那幾條 API 有 @admin_only，但區塊本身若渲染出來，一般使用者會看到一張永遠
    //   載入失敗的表格，而且知道有這個東西存在
    expect(w.find('[data-testid="roster-table"]').exists()).toBe(false);
    expect(w.find('[data-testid="ttyd-views"]').exists()).toBe(false);
    expect(w.find("#user-form").exists()).toBe(false);
    // 也不該去打那幾條 API
    expect(calls.some((c) => c.url.startsWith("/api/users?"))).toBe(false);
    expect(calls.some((c) => c.url === "/api/ttyd/inspect")).toBe(false);
  });

  it("憑證未設定：chip 紅、清除鍵收起來、placeholder 是提示而不是遮罩", async () => {
    const store = useSiteStore();
    store.setCredentials(CREDENTIALS(false));
    const w = await mountAccount(false);
    expect(w.find('[data-testid="token-state"] .chip').attributes("data-tone")).toBe("error");
    expect(w.find('[data-testid="token-clear"]').attributes("hidden")).toBeDefined();
    expect(w.find('[data-testid="cli-token"]').attributes("placeholder")).toContain("貼上");
  });

  it("憑證已設定：chip 綠、清除鍵出現、placeholder 換成遮罩（欄位永遠是空的）", async () => {
    const store = useSiteStore();
    store.setCredentials(CREDENTIALS(true));
    const w = await mountAccount(false);
    expect(w.find('[data-testid="token-state"] .chip').attributes("data-tone")).toBe("ok");
    expect(w.find('[data-testid="token-clear"]').attributes("hidden")).toBeUndefined();
    expect(w.find('[data-testid="cli-token"]').attributes("placeholder")).toContain("已設定");
    expect((w.find('[data-testid="cli-token"]').element as HTMLInputElement).value).toBe("");
  });

  it("儲存鍵要等真的貼了東西才可按", async () => {
    const store = useSiteStore();
    store.setCredentials(CREDENTIALS(false));
    const w = await mountAccount(false);
    expect(w.find('[data-testid="token-save"]').attributes("disabled")).toBeDefined();
    await w.find('[data-testid="cli-token"]').setValue("  ");
    expect(w.find('[data-testid="token-save"]').attributes("disabled")).toBeDefined();
    await w.find('[data-testid="cli-token"]').setValue("sk-abc");
    expect(w.find('[data-testid="token-save"]').attributes("disabled")).toBeUndefined();
  });

  it("改密碼：長度與一致性都要過，提示只在開始輸入之後才出現", async () => {
    const w = await mountAccount(false, { minPasswordLength: 8 });
    const hint = w.find('[data-testid="pw-hint"]');
    // 一進頁面不該滿江紅
    expect(hint.attributes("hidden")).toBeDefined();
    await w.find('[data-testid="old-pw"]').setValue("old");
    await w.find('[data-testid="new-pw"]').setValue("short");
    expect(hint.attributes("hidden")).toBeUndefined();
    expect(hint.text()).toBe("至少 8 字元");
    await w.find('[data-testid="new-pw"]').setValue("longenough");
    await w.find('[data-testid="confirm-pw"]').setValue("longenoug");
    expect(hint.text()).toBe("兩次輸入不一致");
    expect(w.find('[data-testid="pw-btn"]').attributes("disabled")).toBeDefined();
    await w.find('[data-testid="confirm-pw"]').setValue("longenough");
    expect(hint.attributes("hidden")).toBeDefined();
    expect(w.find('[data-testid="pw-btn"]').attributes("disabled")).toBeUndefined();
  });

  it("🔴 改密碼收不乾淨時**不可以**報成功", async () => {
    installFetch({ "/api/users/me/password": { body: { views_failed: true } } });
    const w = await mountAccount(false, { minPasswordLength: 8 });
    await w.find('[data-testid="old-pw"]').setValue("oldpassword");
    await w.find('[data-testid="new-pw"]').setValue("newpassword");
    await w.find('[data-testid="confirm-pw"]').setValue("newpassword");
    await w.find('[data-testid="pw-form"]').trigger("submit");
    await flushPromises();
    const t = toasts.at(-1)!;
    expect(t.level).toBe("warning");
    expect(t.title).toContain("沒有收乾淨");
    // 表單要先清空：跳轉之前再按一次會拿到 401，把這則警告洗掉
    expect((w.find('[data-testid="old-pw"]').element as HTMLInputElement).value).toBe("");
  });

  it("GitLab 那一塊只有功能開著才畫", async () => {
    const w = await mountAccount(false);
    expect(w.find("#pat-form").exists()).toBe(false);
    const w2 = await mountAccount(false, { gitlabEnabled: true, gitlabHost: "gitlab.test" });
    expect(w2.find("#pat-form").exists()).toBe(true);
    expect(w2.text()).toContain("gitlab.test");
  });

  it("GitLab 代理起不來時，把 nginx 那句話排在最上面", async () => {
    const w = await mountAccount(false, {
      gitlabEnabled: true,
      gitlabHost: "gitlab.test",
      gitlabProxyError: "host not found in upstream",
    });
    expect(w.find("#pat-state .chip").text()).toBe("代理起不來");
    expect(w.text()).toContain("host not found in upstream");
  });
});

describe("AccountView：管理員", () => {
  beforeEach(() => {
    installFetch({
      "/api/users": { body: USERS },
      "/api/ttyd/inspect": { body: INSPECT },
    });
  });

  it("三塊都畫，而且進頁就把清單與 ttyd 實況抓回來", async () => {
    const w = await mountAccount(true);
    expect(w.find("#user-form").exists()).toBe(true);
    expect(w.find('[data-testid="roster-table"]').exists()).toBe(true);
    expect(w.findAll('[data-testid="roster-name"]').map((e) => e.text())).toEqual(["alice", "bob"]);
    expect(w.find('[data-testid="ttyd-views"]').text()).toContain("41000");
    expect(calls.some((c) => c.url.startsWith("/api/users?"))).toBe(true);
    expect(calls.some((c) => c.url === "/api/ttyd/inspect")).toBe(true);
  });

  it("權限說明依所選角色而變，粗體是自己拆的（不進 v-html）", async () => {
    const w = await mountAccount(true);
    const lede = w.find("#role-lede");
    expect(lede.attributes("data-tone")).toBe("info");
    expect(lede.text()).toContain("只看得見");
    await w.find('[data-testid="pick-role-button"]').trigger("click");
    await w.find('[data-testid="pick-role-opt-1"]').trigger("click");
    await flushPromises();
    expect(lede.attributes("data-tone")).toBe("warn");
    expect(lede.findAll("strong").map((e) => e.text())).toContain("所有人");
  });

  it("只有一頁時分頁列收起來；讀取失敗時整張表換成講得出原因的一列", async () => {
    const w = await mountAccount(true);
    expect(w.find('[data-testid="roster-pager"]').attributes("hidden")).toBeDefined();

    installFetch({
      "/api/users": { status: 500, body: { error: "資料庫鎖住了" } },
      "/api/ttyd/inspect": { body: INSPECT },
    });
    const w2 = await mountAccount(true);
    expect(w2.find('[data-testid="roster"]').text()).toContain("資料庫鎖住了");
    expect(toasts.some((t) => t.title === "帳號清單讀取失敗")).toBe(true);
  });

  it("重設密碼要先問過；按取消就什麼都不做", async () => {
    const w = await mountAccount(true);
    await w.find('[data-act="reset"]').trigger("click");
    await flushPromises();
    const modal = document.querySelector('[data-testid="modal"]')!;
    expect(modal.querySelector('[data-testid="modal-title"]')!.textContent).toBe("重設密碼");
    // 對話框裡的密碼欄也要有「看一眼」（舊版是 enhancePasswordFields(wrap) 包的）
    expect(modal.querySelector(".pw__toggle")).not.toBeNull();
    (modal.querySelector('[data-act="cancel"]') as HTMLButtonElement).click();
    await flushPromises();
    expect(calls.some((c) => c.url.includes("/password"))).toBe(false);
    expect(toasts.some((t) => t.title === "已取消")).toBe(true);
  });

  it("🔴 重設密碼收不乾淨時也不可以報成功", async () => {
    installFetch({
      "/api/users": { body: USERS },
      "/api/ttyd/inspect": { body: INSPECT },
      "/api/users/2/password": { body: { views_failed: true } },
    });
    const w = await mountAccount(true);
    await w.findAll('[data-act="reset"]')[1].trigger("click");
    await flushPromises();
    const input = document.querySelector("#modal-input") as HTMLInputElement;
    input.value = "newpassword";
    input.dispatchEvent(new Event("input"));
    await flushPromises();
    (document.querySelector('[data-act="ok"]') as HTMLButtonElement).click();
    await flushPromises();
    const t = toasts.at(-1)!;
    expect(t.level).toBe("warning");
    expect(t.title).toContain("沒有收乾淨");
  });

  it("建立帳號成功後清空三個欄位（權限也要回到一般使用者）", async () => {
    installFetch({
      "/api/users": (init) => (init?.method === "POST" ? { body: {} } : { body: USERS }),
      "/api/users/options": { body: { users: [{ username: "alice" }, { username: "bob" }] } },
      "/api/ttyd/inspect": { body: INSPECT },
    });
    const w = await mountAccount(true);
    await w.find('[data-testid="new-user"]').setValue("carol");
    await w.find('[data-testid="new-user-pw"]').setValue("password1");
    await w.find('[data-testid="pick-role-button"]').trigger("click");
    await w.find('[data-testid="pick-role-opt-1"]').trigger("click");
    await w.find("#user-form").trigger("submit");
    await flushPromises();
    const post = calls.find((c) => c.method === "POST")!;
    expect(post.body).toMatchObject({ username: "carol", password: "password1", is_admin: true });
    expect((w.find('[data-testid="new-user"]').element as HTMLInputElement).value).toBe("");
    // ⚠ 權限沒回到預設的話，下一筆很容易誤建成管理員
    expect(w.find('[data-testid="pick-role-button"]').text()).toContain("一般使用者");
  });

  it("ttyd 實況：pid 還沒寫回是「建立中」不是紅的，port 對得上就只說相符", async () => {
    installFetch({
      "/api/users": { body: USERS },
      "/api/ttyd/inspect": {
        body: {
          psutil: false,
          views: [
            { ...INSPECT.views[0], alive: null, pid: null },
            {
              ...INSPECT.views[0],
              session_id: "sid2",
              port: 41001,
              proc: { listening: ["0.0.0.0:41999"], clients: 0, bin: "ttyd" },
            },
          ],
          orphans: [{ pid: 999, proc: { listening: ["0.0.0.0:42000"], bin: "ttyd" } }],
        },
      },
    });
    const w = await mountAccount(true);
    const body = w.find('[data-testid="ttyd-views"]');
    // 「不知道」與「壞了」要長得不一樣
    expect(body.text()).toContain("建立中");
    expect(body.findAll(".chip").some((c) => c.text() === "對不上")).toBe(true);
    // 沒有 psutil 一定要喊，不然空的孤兒區看起來像「掃過了，很乾淨」
    expect(w.find("#ttyd-nopsutil").attributes("hidden")).toBeUndefined();
    expect(w.find('[data-testid="ttyd-orphans"]').text()).toContain("1 個孤兒程序");
  });

  it("ttyd 實況讀不到時要換成講得出原因的一列，不留著過期的資料", async () => {
    const w = await mountAccount(true);
    expect(w.find('[data-testid="ttyd-views"]').text()).toContain("41000");
    installFetch({
      "/api/users": { body: USERS },
      "/api/ttyd/inspect": { status: 500, body: { error: "boom" } },
    });
    await w.find('[data-testid="ttyd-refresh"]').trigger("click");
    await flushPromises();
    expect(w.find('[data-testid="ttyd-views"]').text()).toContain("讀不到");
    expect(w.find('[data-testid="ttyd-views"]').text()).not.toContain("41000");
  });
});
