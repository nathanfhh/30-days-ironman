import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createMemoryHistory, createRouter, type Router } from "vue-router";

import App from "@/App.vue";
import SettingsModal from "@/components/SettingsModal.vue";
import { authGuard } from "@/router";
import LoginView from "@/views/LoginView.vue";
import SessionsView from "@/views/SessionsView.vue";
import {
  applyTheme,
  initTheme,
  paintTheme,
  persistTheme,
  setThemeVars,
  THEME_STORAGE_KEY,
  THEME_VARS_KEY,
} from "@/lib/theme";
import { toasts } from "@/lib/toast";
import { useSiteStore } from "@/stores/site";

/* ── 假的控制平面 ──────────────────────────────────────────────────────────────
 * 用「路徑 → 回應」的表，而不是逐個測試各自 mock 一次 fetch：這樣一支測試想改的只有
 * **它在乎的那一條**，其餘照舊；而且沒被登記的路徑會直接讓測試紅掉，不會靜靜地回一個
 * 空物件讓斷言變成在驗空氣。 */
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

const sessionRow = (over: Record<string, unknown> = {}): Record<string, unknown> => ({
  id: "sid1",
  state: "running",
  ready: true,
  container: "claude-pty-sid1",
  created_at: new Date(Date.now() - 60_000).toISOString(),
  ready_at: new Date(Date.now() - 58_000).toISOString(),
  last_active_at: new Date().toISOString(),
  state_checked_at: new Date().toISOString(),
  profile: { cli: "claude", model: "opus", effort: "high", network: "restricted" },
  ...over,
});

const CREDENTIALS = {
  claude: {
    cli: "claude",
    brand: "anthropic",
    ok: true,
    state: "ok",
    label: "Claude 憑證已設定",
    detail: "token 過期不會有預告",
  },
};

const CATALOG = {
  claude: {
    models: [
      {
        slug: "opus",
        display_name: "Opus",
        efforts: ["low", "high", "max"],
        default_effort: "high",
      },
      { slug: "haiku", display_name: "Haiku", efforts: ["low", "medium"], default_effort: "low" },
    ],
    default_model: "opus",
    source: "static",
    fetched_at: null,
  },
};

/** 頁尾那一排。**登入後才給**（2026-08-26 裁示 L4）。 */
const BUILD = {
  modules: [
    {
      name: "claude-pty",
      version: "0.2.0",
      commit: "abc1234",
      built_at: "2026-08-20T10:00:00+00:00",
      detail: "控制平面本體。",
    },
  ],
  built_at: "2026-08-20T10:00:00+00:00",
};

/** 一份完整的 `/api/account/bootstrap` 回應。 */
const accountBootstrapBody = (user: unknown) => ({
  user,
  default_cli: "claude",
  credentials: CREDENTIALS,
  limits: { name_max: 25, username_max: 32, min_password_length: 8 },
  gitlab: { enabled: false, host: null, proxy_error: null },
  persist_dir: "/home/nathan/persistent-data",
  build: BUILD,
});

const listBody = (rows: Record<string, unknown>[], total = rows.length) => ({
  sessions: rows,
  total,
  limit: 10,
  offset: 0,
  credentials: CREDENTIALS,
});

function makeRouter(): Router {
  const r = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/", component: SessionsView },
      { path: "/login", component: LoginView },
      { path: "/account", component: { template: "<div />" } },
    ],
  });
  // ⚠ 掛的是正式版那一支守衛，不是照抄一份：照抄的複本遲早與正式版漂走，而漂走的那天
  //   測試仍然全綠。
  r.beforeEach(authGuard);
  return r;
}

let router: Router;
const mounted: VueWrapper[] = [];

beforeEach(async () => {
  calls.length = 0;
  toasts.splice(0, toasts.length);
  localStorage.clear();
  setActivePinia(createPinia());
  vi.useRealTimers();
  router = makeRouter();
});

afterEach(() => {
  for (const w of mounted.splice(0)) w.unmount();
  document.body.innerHTML = "";
});

/* 一律掛整個 `App`，不是單掛那一頁：toast、對話框、頁尾都住在 App 這一層（而且是
 * Teleport 到 body 的），單掛 view 的話「按了終止會跳出確認框」這種斷言會對著一個
 * 根本沒被渲染的元件測，而且是**靜靜地**測不到。 */
async function mountAt(path: string): Promise<VueWrapper> {
  await router.push(path);
  await router.isReady();
  const w = mount(App, { global: { plugins: [router] }, attachTo: document.body });
  mounted.push(w);
  await flushPromises();
  return w;
}

describe("LoginView", () => {
  beforeEach(() => {
    /* 假後端要**照真的那樣有狀態**：還沒登入時 `/api/account/bootstrap` 是 401，登入之後
       才回身分。無條件回身分的話，守衛會把每一支登入頁的測試都導去 `/`，而那正是這一版
       新加的行為——測試得分得出「沒登入停在這裡」與「已登入被導走」。
       ⚠ 表裡**沒有 `/api/auth/me`**：多打一發的話這張表就會炸，那是刻意的守衛。 */
    let loggedIn = false;
    installFetch({
      "/api/auth/login": () => {
        loggedIn = true;
        return { body: { user: { id: 1, username: "alice", is_admin: false } } };
      },
      "/api/account/bootstrap": () =>
        loggedIn
          ? { body: accountBootstrapBody({ id: 1, username: "alice", is_admin: false }) }
          : { status: 401, body: { error: "未登入" } },
    });
  });

  it("🔴 已登入者冷載入 /login 要被導回 /（舊版是伺服端一句 302）", async () => {
    installFetch({
      "/api/account/bootstrap": {
        body: accountBootstrapBody({ id: 1, username: "alice", is_admin: false }),
      },
      "/api/catalog": { body: CATALOG },
      "/api/sessions": { body: listBody([]) },
    });
    await mountAt("/login");
    // ⚠ 正式部署由 nginx 直接吐 index.html，Flask 那句 302 根本不會跑到——前端不接的話，
    //   已經登入的人會停在登入表單前面。
    expect(router.currentRoute.value.path).toBe("/");
  });

  it("沒登入時停在登入頁（探測的 401 是答案，不是錯誤）", async () => {
    installFetch({ "/api/account/bootstrap": { status: 401, body: { error: "未登入" } } });
    const w = await mountAt("/login");
    expect(router.currentRoute.value.path).toBe("/login");
    expect(w.find('[data-testid="login-username"]').exists()).toBe(true);
  });

  it("兩欄都填才按得下去（省掉一次注定失敗的往返）", async () => {
    const w = await mountAt("/login");
    const btn = w.find("#login-btn");
    expect(btn.attributes("disabled")).toBeDefined();
    await w.find('[data-testid="login-username"]').setValue("alice");
    expect(btn.attributes("disabled")).toBeDefined();
    await w.find('[data-testid="login-password"]').setValue("s3cret");
    expect(btn.attributes("disabled")).toBeUndefined();
  });

  it("**刻意不檢查密碼長度**：那是建立密碼的政策，不是登入條件", async () => {
    const w = await mountAt("/login");
    await w.find('[data-testid="login-username"]').setValue("a");
    await w.find('[data-testid="login-password"]').setValue("1");
    expect(w.find("#login-btn").attributes("disabled")).toBeUndefined();
  });

  it("🔴 登入頁沒有頁尾：版號登入後才給（裁示 L4）", async () => {
    const w = await mountAt("/login");
    expect(router.currentRoute.value.path).toBe("/login");
    // ⚠ 查的是**整段不在**，不是「頁尾在但裡面空的」。空的頁尾會留下 `.footer` 的
    //   border-top，那是一條浮在登入表單下方、legacy 從來沒有過的橫線。
    expect(w.find('[data-testid="footer"]').exists()).toBe(false);
    expect(w.findAll('[data-testid="footer-mod"]')).toHaveLength(0);
    // ⚠ 反向：不是因為前端沒去問，是因為那條端點回 401。前端仍然照打不誤。
    expect(calls.some((c) => c.url === "/api/account/bootstrap")).toBe(true);
    // ⚠ 而且公開那條**一個版本字樣都沒被要到**：store 裡是預設值。
    const store = useSiteStore();
    expect(store.meta.buildModules).toEqual([]);
    expect(store.meta.buildBuiltAt).toBeNull();
    expect(store.meta.persistDir).toBe("");
  });

  it("🔴 登入成功之後頁尾才出現（同一個 App，換的是有沒有那條回應）", async () => {
    const w = await mountAt("/login");
    expect(w.find('[data-testid="footer"]').exists()).toBe(false);
    await w.find('[data-testid="login-username"]').setValue("alice");
    await w.find('[data-testid="login-password"]').setValue("s3cret");
    await w.find("#login-form").trigger("submit");
    await flushPromises();
    expect(w.find('[data-testid="footer"]').exists()).toBe(true);
    expect(w.find('[data-testid="footer-mod"]').text()).toContain("0.2.0");
  });

  it("送出成功就進控制台，並把身分載回來", async () => {
    const w = await mountAt("/login");
    await w.find('[data-testid="login-username"]').setValue("alice");
    await w.find('[data-testid="login-password"]').setValue("s3cret");
    await w.find("#login-form").trigger("submit");
    await flushPromises();
    // ⚠ 不是 `calls[0]`：守衛在進登入頁時已經探測過一次（那一發是 401，見上面的假後端）。
    expect(calls.find((c) => c.url === "/api/auth/login")).toMatchObject({
      method: "POST",
      body: { username: "alice", password: "s3cret" },
    });
    expect(useSiteStore().user?.username).toBe("alice");
    // ⚠ 身分來自登入的回應本身，不再多問一次
    expect(calls.some((c) => c.url === "/api/auth/me")).toBe(false);
    expect(router.currentRoute.value.path).toBe("/");
    expect(toasts[0].title).toContain("歡迎回來");
  });

  it("失敗時把後端原文畫在 notice 上，畫面留在原地", async () => {
    // ⚠ 密碼錯是 **400**（`auth.AuthError` 的處理器），不是 401。401 的意思是「cookie 沒了」，
    //   那一條由 api() 統一接走並導回登入頁——在登入頁上把它當成密碼錯會是另一回事。
    installFetch({ "/api/auth/login": { status: 400, body: { error: "帳號或密碼不正確" } } });
    const w = await mountAt("/login");
    const notice = w.find('[data-testid="login-error"]');
    expect(notice.attributes("hidden")).toBeDefined();
    await w.find('[data-testid="login-username"]').setValue("alice");
    await w.find('[data-testid="login-password"]').setValue("bad");
    await w.find("#login-form").trigger("submit");
    await flushPromises();
    expect(notice.attributes("hidden")).toBeUndefined();
    expect(notice.text()).toBe("帳號或密碼不正確");
    expect(router.currentRoute.value.path).toBe("/login");
  });

  it("密碼可以看一眼；切回去時 type 要真的變回 password", async () => {
    const w = await mountAt("/login");
    const field = w.find('[data-testid="login-password"]');
    expect(field.attributes("type")).toBe("password");
    await w.find(".pw__toggle").trigger("click");
    expect(w.find('[data-testid="login-password"]').attributes("type")).toBe("text");
    expect(w.find(".pw__toggle").attributes("aria-pressed")).toBe("true");
    await w.find(".pw__toggle").trigger("click");
    expect(w.find('[data-testid="login-password"]').attributes("type")).toBe("password");
  });
});

const setUser = (): void => {
  const store = useSiteStore();
  // adoptIdentity 而不是直接指派：它同時標記「已經問過了」，守衛才不會再探測一次
  store.adoptIdentity({ id: 1, username: "alice", is_admin: true });
  // 兩條 bootstrap 由 installFetch 的假表供應；這裡直接把預設值放好即可
  store.applyMetaToRoot();
};

describe("SessionsView", () => {
  beforeEach(() => {
    installFetch({
      "/api/auth/me": { body: { user: { id: 1, username: "alice", is_admin: true } } },
      "/api/catalog": { body: CATALOG },
      "/api/sessions": { body: listBody([sessionRow()]) },
      "/api/sessions/history": { body: listBody([], 0) },
      "/api/prefs": {
        body: {
          ttyd_bin: "ttyd",
          ttyd_choices: [
            { value: "ttyd", label: "C" },
            { value: "ttyd-rust", label: "Rust" },
          ],
        },
      },
    });
  });

  it("進頁就畫出招牌、建立表單與清單", async () => {
    setUser();
    const w = await mountAt("/");
    expect(w.find('[data-testid="shell"]').exists()).toBe(true);
    expect(w.find('[data-testid="masthead"]').exists()).toBe(true);
    expect(w.find('[data-testid="account-btn"]').text()).toContain("alice");
    expect(w.find("#create-panel").exists()).toBe(true);
    expect(w.findAll('[data-testid="session-row"]')).toHaveLength(1);
    // 憑證徽章搭列表的順風車更新
    expect(w.find('[data-testid="cred-badge"]').attributes("data-state")).toBe("ok");
  });

  it("admin 看得到每一列的擁有者 chip", async () => {
    installFetch({
      "/api/auth/me": { body: { user: { id: 1, username: "alice", is_admin: true } } },
      "/api/catalog": { body: CATALOG },
      "/api/sessions": { body: listBody([sessionRow({ owner: "bob" })]) },
    });
    setUser();
    const w = await mountAt("/");
    expect(w.findAll('[data-testid="chip-text"]').map((e) => e.text())).toContain("bob");
  });

  it("模型清單載到之前表單是鎖著的，而且**成敗都要解鎖**", async () => {
    setUser();
    const w = await mountAt("/");
    expect(w.find("#create-btn").attributes("disabled")).toBeUndefined();
    expect(w.find("#create-btn").text()).toContain("建立 Session");
    // 目錄回來之後，模型選單換成後端那一份，思考深度跟著這顆模型重建
    await w.find('[data-testid="pick-model-button"]').trigger("click");
    expect(w.findAll(".picker__option").map((e) => e.text())).toEqual(
      expect.arrayContaining([expect.stringContaining("Opus"), expect.stringContaining("Haiku")]),
    );
  });

  it("目錄讀不到也不能讓表單壞掉（一個外部依賴掛掉≠整張表單按不下去）", async () => {
    installFetch({
      "/api/catalog": { status: 500, body: { error: "boom" } },
      "/api/sessions": { body: listBody([]) },
    });
    setUser();
    const w = await mountAt("/");
    expect(w.find("#create-btn").attributes("disabled")).toBeUndefined();
    expect(w.find('[data-testid="model-source"]').text()).toContain("模型清單讀取失敗");
  });

  it("換模型時思考深度只在新模型撐不住時才跳回預設", async () => {
    setUser();
    const w = await mountAt("/");
    // opus 預設 high，haiku 只有 low/medium
    expect(w.find('[data-testid="pick-effort-button"]').text()).toContain("high");
    await w.find('[data-testid="pick-model-button"]').trigger("click");
    await w.find('[data-testid="pick-model-opt-haiku"]').trigger("click");
    await flushPromises();
    expect(w.find('[data-testid="pick-effort-button"]').text()).toContain("low");
  });

  it("建立送出的是 profile 的六個面向，成功後回第一頁重抓", async () => {
    setUser();
    const w = await mountAt("/");
    installFetch({
      "/api/sessions": (init) =>
        init?.method === "POST"
          ? { body: { id: "new1", display_name: null } }
          : { body: listBody([sessionRow({ id: "new1" })]) },
      "/api/catalog": { body: CATALOG },
    });
    await w.find("#create-form").trigger("submit");
    await flushPromises();
    const post = calls.find((c) => c.method === "POST")!;
    expect(post.body).toMatchObject({
      profile: {
        cli: "claude",
        network: "restricted",
        capture: false,
        telemetry: false,
        model: "opus",
        effort: "high",
        token_delivery: "fd",
      },
    });
    expect(toasts.some((t) => t.title.startsWith("已建立"))).toBe(true);
  });

  it("切到已結束：打 history、建立表單收起來、網址記住", async () => {
    setUser();
    const w = await mountAt("/");
    await w.find('[data-testid="tab-past"]').trigger("click");
    await new Promise((r) => setTimeout(r, 150)); // 淡出那 120ms
    await flushPromises();
    expect(calls.some((c) => c.url.startsWith("/api/sessions/history"))).toBe(true);
    expect(router.currentRoute.value.query.tab).toBe("past");
    // ⚠ 是 `hidden` 不是「不存在」：舊版就是這樣收起來的，而 golden 拿舊版那份來比
    expect(w.find("#create-panel").attributes("hidden")).toBeDefined();
    expect(w.find('[data-testid="tab-past"]').attributes("aria-selected")).toBe("true");
  });

  it("列表讀取失敗會把原因畫在清單區，並發一則 toast", async () => {
    installFetch({
      "/api/catalog": { body: CATALOG },
      "/api/sessions": { status: 500, body: { error: "資料庫鎖住了" } },
    });
    setUser();
    const w = await mountAt("/");
    expect(w.find(".empty").text()).toContain("資料庫鎖住了");
    expect(toasts.some((t) => t.title === "列表讀取失敗")).toBe(true);
  });

  it("終止要先問過；按取消就什麼都不做", async () => {
    setUser();
    const w = await mountAt("/");
    await w.find('[data-act="kill"]').trigger("click");
    await flushPromises();
    const modal = document.querySelector('[data-testid="modal"]')!;
    expect(modal.querySelector('[data-testid="modal-title"]')!.textContent).toBe("終止 Session");
    // 名字與 container 都要寫出來，不然使用者確認不了自己在殺哪一場
    expect(modal.querySelector('[data-testid="modal-body"]')!.textContent).toContain(
      "claude-pty-sid1",
    );
    (modal.querySelector('[data-act="cancel"]') as HTMLButtonElement).click();
    await flushPromises();
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);
    expect(toasts.some((t) => t.title === "已取消")).toBe(true);
  });

  it("確認終止就送 DELETE，並告訴使用者對話沒有消失", async () => {
    setUser();
    const w = await mountAt("/");
    installFetch({
      "/api/sessions": { body: listBody([]) },
      "/api/sessions/sid1": { status: 204 },
      "/api/catalog": { body: CATALOG },
    });
    await w.find('[data-act="kill"]').trigger("click");
    await flushPromises();
    (document.querySelector('[data-act="ok"]') as HTMLButtonElement).click();
    await flushPromises();
    expect(calls.some((c) => c.url === "/api/sessions/sid1" && c.method === "DELETE")).toBe(true);
    expect(toasts.some((t) => t.body.includes("/resume"))).toBe(true);
  });

  it("改名：留空是有效答案（改回顯示 ID），不是按了取消", async () => {
    setUser();
    const w = await mountAt("/");
    installFetch({
      "/api/sessions": { body: listBody([sessionRow()]) },
      "/api/sessions/sid1": { body: { display_name: null } },
      "/api/catalog": { body: CATALOG },
    });
    await w.find('[data-act="rename"]').trigger("click");
    await flushPromises();
    (document.querySelector('[data-act="ok"]') as HTMLButtonElement).click();
    await flushPromises();
    const patch = calls.find((c) => c.method === "PATCH")!;
    expect(patch.body).toEqual({ name: "" });
    expect(toasts.some((t) => t.body.includes("改回顯示 ID"))).toBe(true);
  });

  it("開終端：沒走 nginx 時開新分頁而不是抽屜（跨 origin 會被 CSP 擋在 iframe 外）", async () => {
    setUser();
    const w = await mountAt("/");
    installFetch({
      "/api/sessions": { body: listBody([sessionRow()]) },
      "/api/sessions/sid1/view": {
        body: { path: "/session/sid1/", direct_url: "http://127.0.0.1:41000/" },
      },
      "/api/catalog": { body: CATALOG },
    });
    const open = vi.fn();
    vi.stubGlobal("open", open);
    await w.find('[data-act="open"]').trigger("click");
    await flushPromises();
    expect(open).toHaveBeenCalledWith("http://127.0.0.1:41000/", "_blank", "noopener");
    expect(document.querySelector('[data-testid="drawer"]')).toBeNull();
    vi.unstubAllGlobals();
  });

  it("走 nginx 時開抽屜：iframe 指到單一入口那條路徑，不是跨 origin 的直連網址", async () => {
    setUser();
    useSiteStore().meta.behindProxy = true;
    const w = await mountAt("/");
    installFetch({
      "/api/sessions": { body: listBody([sessionRow()]) },
      "/api/sessions/sid1/view": {
        body: {
          path: "/session/sid1/",
          direct_url: "http://127.0.0.1:41000/",
          ttyd_flavor: "Rust",
        },
      },
      "/api/catalog": { body: CATALOG },
    });
    await w.find('[data-act="open"]').trigger("click");
    await flushPromises();
    const drawer = document.querySelector('[data-testid="drawer"]')!;
    expect(drawer.getAttribute("data-sid")).toBe("sid1");
    // ⚠ **一定是 `path` 不是 `direct_url`**：後者是另一個 origin，會被本站 CSP 擋在 iframe 外
    const frame = document.querySelector('[data-testid="drawer-frame"]') as HTMLIFrameElement;
    expect(frame.getAttribute("src")).toBe("/session/sid1/");
    // 哪一顆 ttyd 在服務：出問題時第一個要問的問題
    expect(document.querySelector('[data-testid="drawer-bin"]')!.textContent).toBe("Rust");
    // 背景要退出 Tab 序（aria-modal 只影響螢幕閱讀器的虛擬游標，不影響 Tab 順序）
    expect((document.querySelector(".shell") as HTMLElement).inert).toBe(true);
    (document.querySelector('[data-testid="drawer-close"]') as HTMLButtonElement).click();
    await new Promise((r) => setTimeout(r, 600));
    await new Promise((r) => setTimeout(r, 50));
    await flushPromises();
    await flushPromises();
    expect(document.querySelector('[data-testid="drawer"]')).toBeNull();
    expect((document.querySelector(".shell") as HTMLElement).inert).toBe(false);
  });

  it("那一列已經作古（404）時，操作失敗之後要立刻重拉清單", async () => {
    setUser();
    const w = await mountAt("/");
    const before = calls.filter((c) => c.url.startsWith("/api/sessions?")).length;
    installFetch({
      "/api/sessions": { body: listBody([]) },
      "/api/sessions/sid1/view": { status: 404, body: { error: "未知 session" } },
      "/api/catalog": { body: CATALOG },
    });
    await w.find('[data-act="open"]').trigger("click");
    await flushPromises();
    expect(calls.filter((c) => c.url.startsWith("/api/sessions?")).length).toBeGreaterThan(before);
  });

  it("身分下拉：設定叫得出對話框，登出會離開", async () => {
    setUser();
    const w = await mountAt("/");
    await w.find('[data-testid="account-btn"]').trigger("click");
    await flushPromises();
    await w.find('[data-testid="menu-settings"]').trigger("click");
    await flushPromises();
    expect(document.querySelector('[data-testid="settings-modal"]')).not.toBeNull();
    expect(calls.some((c) => c.url === "/api/prefs")).toBe(true);
    (
      document.querySelector(
        '[data-testid="settings-modal"] [data-act="close"]',
      ) as HTMLButtonElement
    ).click();
    await flushPromises();
    expect(document.querySelector('[data-testid="settings-modal"]')).toBeNull();

    installFetch({ "/api/auth/logout": { status: 204 } });
    await w.find('[data-testid="account-btn"]').trigger("click");
    await w.find('[data-testid="menu-logout"]').trigger("click");
    await flushPromises();
    expect(router.currentRoute.value.path).toBe("/login");
  });

  it("分頁列只有真的不只一頁才出現", async () => {
    installFetch({
      "/api/catalog": { body: CATALOG },
      "/api/sessions": { body: { ...listBody([sessionRow()], 25), limit: 10 } },
    });
    setUser();
    const w = await mountAt("/");
    expect(w.find("#pager").attributes("hidden")).toBeUndefined();
    expect(w.find('[data-testid="pager-status"]').text()).toContain("共 25 筆");
    expect(w.find("#prev-btn").attributes("disabled")).toBeDefined();
  });
});

describe("lib/theme", () => {
  it("預設主題＝把覆寫全部拿掉，不是再塗一層", async () => {
    document.documentElement.style.setProperty("--color-surface", "#fff");
    document.documentElement.dataset.theme = "daylight";
    paintTheme("instrument", null);
    expect(document.documentElement.style.getPropertyValue("--color-surface")).toBe("");
    expect(document.documentElement.dataset.theme).toBeUndefined();
  });

  it("套色票會寫進 :root，並標上目前是哪一個主題", () => {
    paintTheme("vellum", { surface: "#111", text: "#eee" });
    expect(document.documentElement.dataset.theme).toBe("vellum");
    expect(document.documentElement.style.getPropertyValue("--color-text")).toBe("#eee");
    paintTheme("instrument", null);
  });

  it("色票快取讀得回來，就不必再跑一趟網路", async () => {
    localStorage.clear();
    persistTheme("vellum", { surface: "#111" });
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe("vellum");
    const fetchSpy = vi.fn();
    globalThis.fetch = fetchSpy as unknown as typeof fetch;
    await setThemeVars("vellum");
    expect(fetchSpy).not.toHaveBeenCalled();
    paintTheme("instrument", null);
  });

  it("沒有 View Transition（或沒有圓心）就直接套用，功能不受影響", async () => {
    localStorage.clear();
    globalThis.fetch = vi.fn(
      async () =>
        new Response(JSON.stringify({ colors: { accent: "#f0a" } }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
    ) as typeof fetch;
    await applyTheme("daylight", null);
    expect(document.documentElement.style.getPropertyValue("--color-accent")).toBe("#f0a");
    paintTheme("instrument", null);
  });
});

/* ── 設定對話框 ────────────────────────────────────────────────────────────────
 * 上面那一支從身分下拉把它叫出來，守的是「叫得出來、關得掉」。這裡直接掛元件，守的是
 * 讀不到偏好時畫成什麼、換一顆 ttyd 那條 PATCH，以及**存不進去時值要轉回真實值**：
 * 留著假象的話畫面會說「已經是 Rust 版了」，而下一場開出來還是 C 版。
 */
const PREFS = {
  ttyd_bin: "ttyd",
  ttyd_choices: [
    { value: "ttyd", label: "C 版" },
    { value: "ttyd-rs", label: "Rust 版" },
  ],
};

function mountSettings(): VueWrapper {
  const w = mount(SettingsModal, { attachTo: document.body });
  mounted.push(w);
  return w;
}

/** ⚠ 對話框是 Teleport 到 body 的，wrapper.find() 看不到它，一律回文件裡找。 */
function inDoc<T extends Element>(selector: string): T {
  const found = document.querySelector<T>(selector);
  if (!found) throw new Error(`畫面上找不到 ${selector}`);
  return found;
}

const clickIn = async (selector: string): Promise<void> => {
  inDoc<HTMLElement>(selector).click();
  await flushPromises();
};

const pressEscape = (): void => {
  document.body.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
};

describe("SettingsModal", () => {
  it("🔴 Esc 掛在 document 上：對話框是從下拉點開的，焦點根本不在它裡面", async () => {
    installFetch({ "/api/prefs": { body: PREFS } });
    const w = mountSettings();
    await flushPromises();
    // 初始焦點放關閉鍵：picker 要等 /api/prefs 回來才建，它是唯一一定聚焦得上的控件
    expect(document.activeElement).toBe(inDoc('[data-testid="settings-modal"] [data-act="close"]'));
    document.body.dispatchEvent(new KeyboardEvent("keydown", { key: "a", bubbles: true }));
    expect(w.emitted("close")).toBeUndefined();
    pressEscape();
    expect(w.emitted("close")).toHaveLength(1);
  });

  it("拆掉之後 Esc 就不該再被接走（監聽掛在 document 上，不拆會留一輩子）", async () => {
    installFetch({ "/api/prefs": { body: PREFS } });
    const w = mountSettings();
    await flushPromises();
    w.unmount();
    mounted.splice(mounted.indexOf(w), 1);
    pressEscape();
    expect(w.emitted("close")).toBeUndefined();
  });

  it("點遮罩關、點盒子裡面不關", async () => {
    installFetch({ "/api/prefs": { body: PREFS } });
    const w = mountSettings();
    await flushPromises();
    await clickIn('[data-testid="modal-box"]');
    expect(w.emitted("close")).toBeUndefined();
    await clickIn('[data-testid="settings-modal"]');
    expect(w.emitted("close")).toHaveLength(1);
  });

  it("讀不到偏好時說得出原因，而且不畫一個沒有選項的下拉", async () => {
    installFetch({ "/api/prefs": { status: 500, body: { error: "boom" } } });
    mountSettings();
    await flushPromises();
    expect(toasts.at(-1)!.title).toContain("讀取設定失敗");
    expect(document.querySelector("#pick-ttyd")).toBeNull();
    // 對話框本身照畫，不然點了設定像是完全沒有反應
    expect(document.querySelector('[data-testid="settings-modal"]')).not.toBeNull();
  });

  it("換一顆 ttyd：PATCH 出去，並講明「已經開著的那一場不會換」", async () => {
    installFetch({
      "/api/prefs": (init) =>
        init?.method === "PATCH" ? { body: { ...PREFS, ttyd_bin: "ttyd-rs" } } : { body: PREFS },
    });
    mountSettings();
    await flushPromises();
    await clickIn('[data-testid="pick-ttyd-button"]');
    await clickIn('[data-testid="pick-ttyd-opt-ttyd-rs"]');
    const patch = calls.find((c) => c.url === "/api/prefs" && c.method === "PATCH")!;
    expect(patch.body).toEqual({ ttyd_bin: "ttyd-rs" });
    // 講的是 label 不是 value：畫面上從來沒出現過 "ttyd-rs" 這個字
    expect(toasts.at(-1)!.title).toContain("Rust 版");
    expect(toasts.at(-1)!.title).toContain("終端分頁全部關掉");
  });

  it("存不進去要說是哪一句話卡住了，而且不可以報成功", async () => {
    installFetch({
      "/api/prefs": (init) =>
        init?.method === "PATCH"
          ? { status: 500, body: { error: "唯讀的設定檔" } }
          : { body: PREFS },
    });
    mountSettings();
    await flushPromises();
    await clickIn('[data-testid="pick-ttyd-button"]');
    await clickIn('[data-testid="pick-ttyd-opt-ttyd-rs"]');
    expect(toasts.at(-1)!.title).toContain("設定沒存成功");
    expect(toasts.at(-1)!.title).toContain("唯讀的設定檔");
    expect(toasts.at(-1)!.level).toBe("danger");
  });

  /* 🐛 **目前是紅的，而且它抓到的是真的壞掉**（用 it.fails 標著：哪天修好了這一支會改成
   *    「預期失敗卻通過」而炸開，那時把 `.fails` 拿掉即可）。
   *
   *    `save()` 第一行的 `const before = value.value` 想記下「改之前是什麼」，但 SitePicker
   *    是先 `emit("update:modelValue")` 再 `emit("change")`，而 v-model 的處理器是同步跑的，
   *    所以 save 讀到的 `before` 已經是**新值**，catch 裡那句「轉回真實值」等於什麼都沒做。
   *    後果：畫面停在「Rust 版」，而下一場開出來的是 C 版，兩者不一致且沒有任何跡象。
   *    修法是把改前的值留在 change 之外（例如在 SitePicker 的 change 事件裡一併帶上舊值，
   *    或改用 `@change` 單向、不掛 v-model），那是行為變更，不在這一輪的範圍內。 */
  it.fails("🔴 存不進去就把值轉回真實值，不要留一個假象", async () => {
    installFetch({
      "/api/prefs": (init) =>
        init?.method === "PATCH"
          ? { status: 500, body: { error: "唯讀的設定檔" } }
          : { body: PREFS },
    });
    mountSettings();
    await flushPromises();
    const button = (): string => inDoc('[data-testid="pick-ttyd-button"]').textContent ?? "";
    expect(button()).toContain("C 版");
    await clickIn('[data-testid="pick-ttyd-button"]');
    await clickIn('[data-testid="pick-ttyd-opt-ttyd-rs"]');
    // 畫面說 Rust、下一場卻開出 C 版，是這一塊最糟的結局
    expect(button()).toContain("C 版");
  });
});

/* ── 主題的過渡與快取 ─────────────────────────────────────────────────────────
 * 上面那一組守的是「顏色有沒有塗上去」。這裡守的是**過渡本身**：色票與 localStorage
 * 必須在過渡之外先做完（startViewTransition 會等 callback 的 promise，等待期間整頁是
 * 一張凍結的靜態圖），以及三條退路（沒有 View Transition、使用者要求減少動態、過渡
 * 被中斷）都不可以讓主題換不成。
 */
/* ⚠ jsdom 沒有 View Transition，而 DOM 的型別宣告要求 `startViewTransition` 回傳一個完整的
   `ViewTransition`（finished / updateCallbackDone / skipTransition …）。替身只需要 `ready`，
   所以轉型集中在這兩支上，測試本文不再散落 `as unknown as`。 */
type StartViewTransition = (cb: () => void) => { ready: Promise<void> };

function stubViewTransition(start: StartViewTransition): void {
  (document as unknown as { startViewTransition?: StartViewTransition }).startViewTransition =
    start;
}

/** 只有 `ready` 的動畫替身：真正要問的是「有沒有拿正確的 clipPath 去動畫」。 */
function stubAnimate(): ReturnType<typeof vi.fn> {
  const animate = vi.fn((_frames: unknown, _opts: unknown) => ({}) as Animation);
  document.documentElement.animate = animate as unknown as HTMLElement["animate"];
  return animate;
}

const jsonResponse = (body: unknown, status = 200): Response =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

const serveColors = (colors: Record<string, string>): ReturnType<typeof vi.fn> => {
  const spy = vi.fn(async () => jsonResponse({ colors }));
  globalThis.fetch = spy as unknown as typeof fetch;
  return spy;
};

/** 把測試裝上去的替身全部拆乾淨，並把畫面還原成預設主題。 */
function resetTheme(): void {
  Reflect.deleteProperty(document, "startViewTransition");
  Reflect.deleteProperty(document.documentElement, "animate");
  vi.unstubAllGlobals();
  paintTheme("instrument", null);
}

describe("lib/theme：過渡與退路", () => {
  beforeEach(() => localStorage.clear());
  afterEach(resetTheme);

  it("色票快取壞掉就當作沒有、重抓一次，不是讓整個主題壞掉", async () => {
    localStorage.setItem(THEME_VARS_KEY + "vellum", "{{{");
    const spy = serveColors({ surface: "#111" });
    await setThemeVars("vellum");
    expect(spy).toHaveBeenCalledOnce();
    expect(document.documentElement.style.getPropertyValue("--color-surface")).toBe("#111");
  });

  it("主題檔抓不到就不套色票，但仍然標上是哪一個主題", async () => {
    globalThis.fetch = vi.fn(async () => new Response("nope", { status: 404 })) as typeof fetch;
    await setThemeVars("daylight");
    expect(document.documentElement.dataset.theme).toBe("daylight");
    expect(document.documentElement.style.getPropertyValue("--color-accent")).toBe("");
  });

  it("🔴 使用者要求減少動態時不做同心圓，主題照樣要換成功", async () => {
    serveColors({ accent: "#0af" });
    vi.stubGlobal("matchMedia", () => ({ matches: true }));
    const start = vi.fn((cb: () => void) => {
      cb();
      return { ready: Promise.resolve() };
    });
    stubViewTransition(start);
    await applyTheme("daylight", { x: 10, y: 20 });
    expect(start).not.toHaveBeenCalled();
    expect(document.documentElement.style.getPropertyValue("--color-accent")).toBe("#0af");
  });

  it("🔴 過渡的 callback 只做改樣式那一件事，色票與 localStorage 都在它之外先做完", async () => {
    serveColors({ accent: "#f0a" });
    const animate = stubAnimate();
    let paintedInside = "";
    let storedBefore: string | null = null;
    stubViewTransition((cb) => {
      storedBefore = localStorage.getItem(THEME_STORAGE_KEY);
      cb();
      paintedInside = document.documentElement.style.getPropertyValue("--color-accent");
      return { ready: Promise.resolve() };
    });
    await applyTheme("daylight", { x: 10, y: 20 });
    expect(paintedInside).toBe("#f0a");
    // ⚠ 進到 callback 之前就已經存好了：localStorage 是同步磁碟 I/O，放進去等於延長凍結
    expect(storedBefore).toBe("daylight");
    const [frames, opts] = animate.mock.calls[0] as [
      { clipPath: string[] },
      KeyframeAnimationOptions,
    ];
    // 漣漪從點擊處長出來，半徑要夠遠才蓋得滿整個視窗
    expect(frames.clipPath[0]).toBe("circle(0px at 10px 20px)");
    expect(frames.clipPath[1]).toMatch(/^circle\(\d+(\.\d+)?px at 10px 20px\)$/);
    expect(opts.pseudoElement).toBe("::view-transition-new(root)");
  });

  it("過渡被中斷（連按兩下換主題）不是錯誤：顏色已經換好了", async () => {
    serveColors({ accent: "#f0a" });
    stubAnimate();
    stubViewTransition((cb) => {
      cb();
      return { ready: Promise.reject(new Error("aborted")) };
    });
    await expect(applyTheme("daylight", { x: 1, y: 1 })).resolves.toBeUndefined();
    expect(document.documentElement.style.getPropertyValue("--color-accent")).toBe("#f0a");
  });

  it("進站時把存下來的主題套回去；預設主題連一趟網路都不必跑", async () => {
    const spy = serveColors({ surface: "#111" });
    expect(await initTheme()).toBe("instrument");
    expect(spy).not.toHaveBeenCalled();
    persistTheme("vellum", { surface: "#111" });
    expect(await initTheme()).toBe("vellum");
    expect(document.documentElement.dataset.theme).toBe("vellum");
    // 快取讀得回來，所以還是不必跑網路
    expect(spy).not.toHaveBeenCalled();
  });
});
