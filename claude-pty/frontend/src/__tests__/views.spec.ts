import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createMemoryHistory, createRouter, type Router } from "vue-router";

import App from "@/App.vue";
import LoginView from "@/views/LoginView.vue";
import SessionsView from "@/views/SessionsView.vue";
import { applyTheme, paintTheme, persistTheme, setThemeVars, THEME_STORAGE_KEY } from "@/lib/theme";
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

const listBody = (rows: Record<string, unknown>[], total = rows.length) => ({
  sessions: rows,
  total,
  limit: 10,
  offset: 0,
  credentials: CREDENTIALS,
});

function makeRouter(): Router {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/", component: SessionsView },
      { path: "/login", component: LoginView },
      { path: "/account", component: { template: "<div />" } },
    ],
  });
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
    installFetch({
      "/api/auth/login": { body: { user: { id: 1, username: "alice", is_admin: false } } },
      "/api/auth/me": { body: { user: { id: 1, username: "alice", is_admin: false } } },
    });
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

  it("送出成功就進控制台，並把身分載回來", async () => {
    const w = await mountAt("/login");
    await w.find('[data-testid="login-username"]').setValue("alice");
    await w.find('[data-testid="login-password"]').setValue("s3cret");
    await w.find("#login-form").trigger("submit");
    await flushPromises();
    expect(calls[0]).toMatchObject({
      url: "/api/auth/login",
      method: "POST",
      body: { username: "alice", password: "s3cret" },
    });
    expect(useSiteStore().user?.username).toBe("alice");
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

  const setUser = (): void => {
    const store = useSiteStore();
    store.user = { id: 1, username: "alice", is_admin: true };
    store.loadMeta();
  };

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

  it("走 nginx 時開抽屜（目前是殼，但路要對）", async () => {
    setUser();
    useSiteStore().meta.behindProxy = true;
    const w = await mountAt("/");
    installFetch({
      "/api/sessions": { body: listBody([sessionRow()]) },
      "/api/sessions/sid1/view": {
        body: { path: "/session/sid1/", direct_url: "http://127.0.0.1:41000/" },
      },
      "/api/catalog": { body: CATALOG },
    });
    await w.find('[data-act="open"]').trigger("click");
    await flushPromises();
    const drawer = document.querySelector('[data-testid="drawer"]')!;
    expect(drawer.getAttribute("data-sid")).toBe("sid1");
    (document.querySelector('[data-testid="drawer-close"]') as HTMLButtonElement).click();
    await flushPromises();
    expect(document.querySelector('[data-testid="drawer"]')).toBeNull();
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
