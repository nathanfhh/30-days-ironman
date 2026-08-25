import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createMemoryHistory, createRouter, type Router } from "vue-router";

import AppFooter from "@/components/AppFooter.vue";
import ManifestList from "@/components/ManifestList.vue";
import SitePicker from "@/components/SitePicker.vue";
import SiteSwitch from "@/components/SiteSwitch.vue";
import RangePicker from "@/components/RangePicker.vue";
import FilterBar from "@/components/FilterBar.vue";
import type { SessionRow } from "@/lib/sessions";
import { useSiteStore } from "@/stores/site";

const testRouter = (): Router =>
  createRouter({
    history: createMemoryHistory(),
    routes: [{ path: "/", component: { template: "<div />" } }],
  });

const row = (over: Partial<SessionRow> = {}): SessionRow => ({
  id: "sid00000001",
  state: "running",
  ready: true,
  container: "claude-pty-sid00000001",
  created_at: new Date(Date.now() - 60_000).toISOString(),
  ready_at: new Date(Date.now() - 55_000).toISOString(),
  state_checked_at: new Date().toISOString(),
  profile: { cli: "claude", model: "opus", effort: "high", network: "restricted" },
  ...over,
});

describe("SitePicker", () => {
  const options = [
    { value: "", label: "不限", icon: "fa-solid fa-asterisk" },
    { value: "1", label: "有錄製", icon: "fa-solid fa-circle-check" },
    { value: "0", label: "沒錄製", icon: "fa-solid fa-circle-minus" },
  ];

  it("testid 全部由掛載點的 id 衍生（e2e 與 golden 直接吃這一組）", () => {
    const w = mount(SitePicker, { props: { id: "pick-fcap", options, modelValue: "" } });
    expect(w.find('[data-testid="pick-fcap"]').exists()).toBe(true);
    expect(w.find('[data-testid="pick-fcap-button"]').exists()).toBe(true);
    expect(w.find('[data-testid="pick-fcap-menu"]').exists()).toBe(true);
    // 掛載點自己的 id 也要留著：CSS 有規則直接掛在 #theme-picker 這種 id 上
    expect(w.find("#pick-fcap").exists()).toBe(true);
  });

  it("空字串那一格的 testid 用 -opt-any（值本身不能當 id）", async () => {
    const w = mount(SitePicker, { props: { id: "pick-fcap", options, modelValue: "" } });
    await w.find('[data-testid="pick-fcap-button"]').trigger("click");
    expect(w.find('[data-testid="pick-fcap-opt-any"]').exists()).toBe(true);
    expect(w.find('[data-testid="pick-fcap-opt-1"]').exists()).toBe(true);
  });

  it("選了就關起來，並把值與座標一起送出去（主題的同心圓需要圓心）", async () => {
    const w = mount(SitePicker, { props: { id: "p", options, modelValue: "" } });
    await w.find('[data-testid="p-button"]').trigger("click");
    expect(w.find('[data-testid="p-menu"]').attributes("hidden")).toBeUndefined();
    await w.find('[data-testid="p-opt-1"]').trigger("click");
    expect(w.emitted("update:modelValue")).toEqual([["1"]]);
    expect(w.emitted("change")![0][0]).toMatchObject({ value: "1" });
    expect(w.find('[data-testid="p-menu"]').attributes("hidden")).toBeDefined();
  });

  it("鍵盤：↓ 展開、再 ↓ 移動、Enter 選取", async () => {
    const w = mount(SitePicker, { props: { id: "p", options, modelValue: "" } });
    const btn = w.find('[data-testid="p-button"]');
    await btn.trigger("keydown", { key: "ArrowDown" });
    await btn.trigger("keydown", { key: "ArrowDown" });
    await btn.trigger("keydown", { key: "Enter" });
    expect(w.emitted("update:modelValue")).toEqual([["1"]]);
  });

  it("停用中不展開——鍵盤這條路也要擋（pointer-events 只擋滑鼠）", async () => {
    const w = mount(SitePicker, { props: { id: "p", options, modelValue: "", disabled: true } });
    await w.find('[data-testid="p-button"]').trigger("keydown", { key: "ArrowDown" });
    expect(w.find('[data-testid="p-menu"]').attributes("hidden")).toBeDefined();
  });

  it("搜尋框只在 search 時出現，並就地過濾", async () => {
    const w = mount(SitePicker, { props: { id: "p", options, modelValue: "", search: true } });
    await w.find('[data-testid="p-button"]').trigger("click");
    const input = w.find('[data-testid="p-search"]');
    expect(input.exists()).toBe(true);
    await input.setValue("錄製");
    expect(w.findAll(".picker__option")).toHaveLength(2);
    await input.setValue("不會有這個");
    expect(w.find(".picker__empty").exists()).toBe(true);
  });
});

describe("SiteSwitch", () => {
  it("off/on 是值不是布林，狀態同步到外層與 aria-checked", async () => {
    const w = mount(SiteSwitch, {
      props: {
        id: "pick-network",
        modelValue: "restricted",
        off: "restricted",
        on: "unrestricted",
        offLabel: "限制（白名單）",
        onLabel: "完全開放",
        name: "網路能力",
      },
    });
    expect(w.find('[data-testid="pick-network"]').attributes("data-on")).toBe("false");
    expect(w.find('[data-testid="pick-network-control"]').attributes("aria-checked")).toBe("false");
    // 沒有 aria-label 的話螢幕閱讀器只會唸「switch，已勾選」
    expect(w.find('[data-testid="pick-network-control"]').attributes("aria-label")).toBe(
      "網路能力",
    );
    await w.find('[data-testid="pick-network-control"]').trigger("click");
    expect(w.emitted("update:modelValue")).toEqual([["unrestricted"]]);
  });

  it("標籤也可點（命中區大一點），方向鍵是指定而非切換", async () => {
    const w = mount(SiteSwitch, {
      props: { id: "s", modelValue: "0", off: "0", on: "1", offLabel: "關", onLabel: "開" },
    });
    await w.find('[data-testid="s-label"]').trigger("click");
    expect(w.emitted("update:modelValue")).toEqual([["1"]]);
    // 已經是 off 了，再按 ← 不該再送一次
    await w.find('[data-testid="s-control"]').trigger("keydown", { key: "ArrowLeft" });
    expect(w.emitted("update:modelValue")).toHaveLength(1);
  });
});

describe("ManifestList", () => {
  const base = {
    offset: 0,
    historical: false,
    isAdmin: false,
    gitlabEnabled: false,
  };

  it("空清單的措辭兩張表不同（一張是「去開一個」，一張是回顧）", () => {
    const live = mount(ManifestList, { props: { ...base, rows: [] } });
    expect(live.find('[data-testid="manifest-empty"]').text()).toContain("用上面的表單開一個");
    const past = mount(ManifestList, { props: { ...base, rows: [], historical: true } });
    expect(past.find('[data-testid="manifest-empty"]').text()).toBe("還沒有結束的 Session。");
  });

  it("載入中與讀取失敗是兩種畫面，不可以互相冒充", () => {
    const loading = mount(ManifestList, { props: { ...base, rows: [], loading: true } });
    expect(loading.find('[data-testid="manifest-empty"]').text()).toBe("載入中…");
    const failed = mount(ManifestList, { props: { ...base, rows: [], error: "500 boom" } });
    expect(failed.text()).toContain("讀取失敗：500 boom");
    expect(failed.find('[data-testid="manifest-empty"]').exists()).toBe(false);
  });

  it("執行中那張表有表頭、每列有動作鍵，序號跨頁連續", () => {
    const w = mount(ManifestList, {
      props: { ...base, rows: [row(), row({ id: "sid2" })], offset: 20 },
    });
    expect(w.find('[data-testid="manifest-head"]').exists()).toBe(true);
    expect(w.findAll('[data-testid="session-row"]')).toHaveLength(2);
    expect(w.findAll(".manifest__index span:last-child").map((e) => e.text())).toEqual([
      "21",
      "22",
    ]);
    expect(w.find('[data-testid="row-open-sid00000001"]').exists()).toBe(true);
    expect(w.find('[data-testid="checked-sid00000001"]').exists()).toBe(true);
  });

  it("沒取名字就用 sid 並套等寬字體", () => {
    const w = mount(ManifestList, {
      props: { ...base, rows: [row(), row({ id: "b", display_name: "重構" })] },
    });
    const titles = w.findAll('[data-testid="session-title"]');
    expect(titles[0].classes()).toContain("mono-id");
    expect(titles[1].classes()).not.toContain("mono-id");
    expect(titles[1].text()).toBe("重構");
  });

  it("歷史那張表是唯讀的：沒有改名、沒有終端、沒有終止", () => {
    const w = mount(ManifestList, {
      props: {
        ...base,
        historical: true,
        rows: [row({ ended_at: new Date().toISOString(), ended_reason: "terminated" })],
      },
    });
    expect(w.find('[data-act="rename"]').exists()).toBe(false);
    expect(w.find('[data-act="open"]').exists()).toBe(false);
    expect(w.find('[data-act="kill"]').exists()).toBe(false);
    expect(w.find('[data-testid="session-status"]').text()).toContain("使用者終止");
  });

  it("三顆動作各自往上送，帶著那一列", async () => {
    const w = mount(ManifestList, { props: { ...base, rows: [row()] } });
    await w.find('[data-act="rename"]').trigger("click");
    await w.find('[data-act="open"]').trigger("click");
    await w.find('[data-act="kill"]').trigger("click");
    expect(w.emitted("rename")![0][0]).toMatchObject({ id: "sid00000001" });
    expect(w.emitted("open")).toHaveLength(1);
    expect(w.emitted("kill")).toHaveLength(1);
  });
});

describe("FilterBar", () => {
  let router: Router;

  beforeEach(async () => {
    setActivePinia(createPinia());
    router = testRouter();
    await router.push("/");
    await router.isReady();
  });

  const mountBar = (open = true) =>
    mount(FilterBar, { props: { open }, global: { plugins: [router] } });

  it("五個欄位標籤都有 tooltip（篩的是什麼要講得出來）", () => {
    const w = mountBar();
    expect(w.findAll('[data-testid="filter-field-label"]')).toHaveLength(5);
    for (const el of w.findAll('[data-testid="filter-field-label"]')) {
      expect(el.attributes("data-tip")).toBeTruthy();
    }
  });

  it("收合時整塊退出 Tab 序，展開時屬性要**整個消失**（inert 存在即生效）", async () => {
    const w = mountBar(false);
    expect(w.find("#filter-shell").attributes("data-open")).toBe("0");
    expect(w.find('[data-testid="filter-bar"]').attributes("inert")).toBeDefined();
    await w.setProps({ open: true });
    // ⚠ `inert="false"` 仍然是 inert。這一條守的就是那個。
    expect(w.find('[data-testid="filter-bar"]').attributes("inert")).toBeUndefined();
    expect(w.find("#filter-shell").attributes("data-open")).toBe("1");
  });

  it("🔴 首載（沒點過那顆鍵）收著的時候也要 inert——階段 4 照抄舊版那個洞已經拆掉", () => {
    const w = mountBar(false);
    expect(w.find("#filter-shell").attributes("data-open")).toBe("0");
    // 舊版只在 setFiltersOpen() 裡設 inert，所以剛進站時鍵盤 Tab 得進一塊看不見的區域
    expect(w.find('[data-testid="filter-bar"]').attributes("inert")).toBeDefined();
  });

  it("選一個條件就寫進網址並通知上層重抓", async () => {
    const w = mountBar();
    await w.find('[data-testid="pick-fnet-button"]').trigger("click");
    await w.find('[data-testid="pick-fnet-opt-restricted"]').trigger("click");
    await flushPromises();
    expect(router.currentRoute.value.query.network).toBe("restricted");
    expect(w.emitted("changed")).toHaveLength(1);
    expect(w.find('[data-testid="filter-summary"]').text()).toBe("1 個條件生效中");
    expect(w.find('[data-testid="filter-clear"]').attributes("disabled")).toBeUndefined();
  });

  it("「不限」是把那一格從網址拿掉，不是送一個 all", async () => {
    await router.replace({ path: "/", query: { network: "restricted" } });
    const w = mountBar();
    await w.find('[data-testid="pick-fnet-button"]').trigger("click");
    await w.find('[data-testid="pick-fnet-opt-any"]').trigger("click");
    await flushPromises();
    expect(router.currentRoute.value.query.network).toBeUndefined();
  });

  it("選了自訂範圍：起迄那一格展開，而且 since=custom 不可以進網址", async () => {
    const w = mountBar();
    expect(w.find('[data-testid="filter-range"]').attributes("hidden")).toBeDefined();
    await w.find('[data-testid="pick-since-button"]').trigger("click");
    await w.find('[data-testid="pick-since-opt-custom"]').trigger("click");
    await flushPromises();
    expect(w.find('[data-testid="filter-range"]').attributes("hidden")).toBeUndefined();
    // 後端把 since 當天數解析，"custom" 會 400
    expect(router.currentRoute.value.query.since).toBeUndefined();
  });

  it("帶著 from/to 進來時那一格要自己展開（不然畫面說不限、清單卻是篩過的）", async () => {
    await router.replace({ path: "/", query: { from: "2026-08-01T00:00:00+08:00" } });
    const w = mountBar();
    expect(w.find('[data-testid="filter-range"]').attributes("hidden")).toBeUndefined();
    expect(w.find('[data-testid="pick-since-button"]').text()).toContain("自訂範圍");
  });

  it("時間範圍與自訂區間互斥：選了天數就把 from/to 清掉", async () => {
    await router.replace({
      path: "/",
      query: { from: "2026-08-01T00:00:00+08:00", to: "2026-08-02T00:00:00+08:00" },
    });
    const w = mountBar();
    await w.find('[data-testid="pick-since-button"]').trigger("click");
    await w.find('[data-testid="pick-since-opt-7"]').trigger("click");
    await flushPromises();
    expect(router.currentRoute.value.query).toEqual({ since: "7" });
  });

  it("清除全部條件只動篩選那幾個，分頁狀態留著", async () => {
    await router.replace({ path: "/", query: { tab: "past", since: "7", capture: "1" } });
    const w = mountBar();
    await w.find('[data-testid="filter-clear"]').trigger("click");
    await flushPromises();
    expect(router.currentRoute.value.query).toEqual({ tab: "past" });
    expect(w.find('[data-testid="filter-summary"]').text()).toBe("沒有套用任何條件");
  });
});

describe("RangePicker", () => {
  it("點兩天就是一段區間，按確定才送出（半截的不觸發查詢）", async () => {
    const w = mount(RangePicker, { props: { modelValue: { from: "", to: "" } } });
    await w.find('[data-testid="range-trigger"]').trigger("click");
    await flushPromises();
    const days = w.findAll('[data-testid="range-day"]:not([disabled])');
    expect(days.length).toBeGreaterThan(1);
    await days[0].trigger("click");
    // 只點了一天：還沒有 change
    expect(w.emitted("change")).toBeUndefined();
    await days[1].trigger("click");
    expect(w.emitted("change")).toBeUndefined();
    await w.find('[data-testid="range-ok"]').trigger("click");
    const v = w.emitted("change")![0][0] as { from: string; to: string };
    // 帶時區偏移的 ISO——後端只收帶時區的，不猜是哪一區的牆上時間
    expect(v.from).toMatch(/^\d{4}-\d{2}-\d{2}T00:00:00[+-]\d{2}:\d{2}$/);
    expect(v.to).toMatch(/^\d{4}-\d{2}-\d{2}T23:59:00[+-]\d{2}:\d{2}$/);
  });

  it("未來的日期不給選（挑了也查不到東西，只會讓人以為壞了）", async () => {
    const w = mount(RangePicker, { props: { modelValue: { from: "", to: "" } } });
    await w.find('[data-testid="range-trigger"]').trigger("click");
    await flushPromises();
    // 右邊那格是本月，本月未來的那幾天一定是 disabled
    const disabled = w.findAll(".rangepick__day[disabled]");
    expect(disabled.length).toBeGreaterThan(0);
    // 左邊最多到上個月，所以「下個月」在起始視圖就已經到頂
    expect(w.find('[data-testid="range-next-month"]').attributes("disabled")).toBeDefined();
  });

  it("清除是一個有效的答案，會送出兩端皆空", async () => {
    const w = mount(RangePicker, {
      props: { modelValue: { from: "2026-08-01T00:00:00+08:00", to: "" } },
    });
    await w.find('[data-testid="range-trigger"]').trigger("click");
    await flushPromises();
    await w.find('[data-testid="range-clear"]').trigger("click");
    expect(w.emitted("change")![0][0]).toEqual({ from: "", to: "" });
  });
});

describe("stores/site", () => {
  beforeEach(() => setActivePinia(createPinia()));

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
  const bootstrapBody = (user: unknown) => ({
    user,
    default_cli: "claude",
    credentials: {},
    limits: { name_max: 25, username_max: 32, min_password_length: 8 },
    gitlab: { enabled: false, host: null, proxy_error: null },
    persist_dir: "/home/nathan/persistent-data",
    build: BUILD,
  });

  /** 依路徑回應的假後端，並記下打了哪些路徑。 */
  function fakeApi(routes: Record<string, { status?: number; body?: unknown }>): string[] {
    const seen: string[] = [];
    globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input).split("?")[0];
      seen.push(path);
      const r = routes[path];
      if (!r) return new Response("{}", { status: 404 });
      return new Response(JSON.stringify(r.body ?? {}), {
        status: r.status ?? 200,
        headers: { "Content-Type": "application/json" },
      });
    }) as typeof fetch;
    return seen;
  }

  it("loadIdentity 失敗時把身分清掉，但仍然標記問過了", async () => {
    fakeApi({ "/api/account/bootstrap": { status: 500 } });
    const store = useSiteStore();
    expect(await store.loadIdentity()).toBeNull();
    expect(store.identityLoaded).toBe(true);
  });

  it("🔴 冷載入只打 /api/account/bootstrap，身分從那條回應來", async () => {
    const seen = fakeApi({
      "/api/account/bootstrap": {
        body: bootstrapBody({ id: 1, username: "alice", is_admin: true }),
      },
    });
    const store = useSiteStore();
    const u = await store.loadIdentity();
    expect(u?.username).toBe("alice");
    expect(store.user?.is_admin).toBe(true);
    // ⚠ 這一條才是重點：**不可以**再多問一次 /api/auth/me
    expect(seen).toEqual(["/api/account/bootstrap"]);
    // 同一發也要把 meta 填好（它本來就是「這個帳號的處境」那條）
    expect(store.meta.nameMax).toBe(25);
    expect(store.meta.defaultCli).toBe("claude");
  });

  it("🔴 loadAccountMeta 也要把身分收下（存完 PAT 之後 gitlab_pat_configured 會變）", async () => {
    fakeApi({
      "/api/account/bootstrap": {
        body: bootstrapBody({
          id: 1,
          username: "alice",
          is_admin: false,
          gitlab_pat_configured: true,
        }),
      },
    });
    const store = useSiteStore();
    await store.loadAccountMeta();
    expect(store.user?.gitlab_pat_configured).toBe(true);
  });

  it("adoptIdentity：登入的回應本身就帶身分，不必再問一次", () => {
    const store = useSiteStore();
    store.adoptIdentity({ id: 3, username: "carol", is_admin: false });
    expect(store.user?.username).toBe("carol");
    expect(store.identityLoaded).toBe(true);
  });

  it("憑證欄位缺席時維持現狀，不要清成空白", () => {
    const store = useSiteStore();
    store.setCredentials({
      claude: {
        cli: "claude",
        brand: "anthropic",
        ok: true,
        state: "ok",
        label: "已設定",
        detail: "d",
      },
    });
    store.setCredentials(undefined);
    expect(store.credentials.claude.label).toBe("已設定");
  });

  it("兩個環境事實寫回 <html>，抽屜的讀法一字不變", () => {
    const store = useSiteStore();
    store.meta.behindProxy = true;
    store.meta.persistDir = "/home/nathan/persistent-data";
    store.applyMetaToRoot();
    expect(document.documentElement.dataset.behindProxy).toBe("1");
    expect(document.documentElement.dataset.persistDir).toBe("/home/nathan/persistent-data");
  });

  it("🔴 公開那一條只填得起兩個欄位：版號與主機路徑登入前不得取得（裁示 L4）", async () => {
    const seen = fakeApi({
      "/api/bootstrap": { body: { behind_proxy: true, login_art: "/static/images/a.webp" } },
    });
    const store = useSiteStore();
    await store.loadPublicMeta();
    expect(seen).toEqual(["/api/bootstrap"]);
    expect(store.meta.behindProxy).toBe(true);
    expect(store.meta.loginArt).toBe("/static/images/a.webp");
    // ⚠ 這三條才是這一題：公開那條**沒有**這些值，所以它們必須停在預設值。
    expect(store.meta.persistDir).toBe("");
    expect(store.meta.buildModules).toEqual([]);
    expect(store.meta.buildBuiltAt).toBeNull();
  });

  it("🔴 版號與主機路徑跟著 /api/account/bootstrap 回來，並補寫 <html>", async () => {
    document.documentElement.dataset.persistDir = "";
    fakeApi({
      "/api/account/bootstrap": {
        body: bootstrapBody({ id: 1, username: "alice", is_admin: false }),
      },
    });
    const store = useSiteStore();
    await store.loadAccountMeta();
    expect(store.meta.buildModules[0].version).toBe("0.2.0");
    expect(store.meta.buildBuiltAt).toBe("2026-08-20T10:00:00+00:00");
    expect(store.meta.persistDir).toBe("/home/nathan/persistent-data");
    // ⚠ 抽屜是 runtime 才建的，讀的是 <html> 上那個屬性。這一條回來之後沒有補寫的話，
    //   抽屜標題列會少一整行，而且不會有任何錯誤。
    expect(document.documentElement.dataset.persistDir).toBe("/home/nathan/persistent-data");
  });

  it("🔴 登出要把登入後才拿到的 meta 一起清掉（SPA 換頁，store 不會自己沒）", async () => {
    fakeApi({
      "/api/account/bootstrap": {
        body: bootstrapBody({ id: 1, username: "alice", is_admin: false }),
      },
      "/api/auth/logout": { body: {} },
    });
    const store = useSiteStore();
    await store.loadAccountMeta();
    store.meta.behindProxy = true;
    store.meta.loginArt = "/static/images/a.webp";
    await store.logout();
    expect(store.user).toBeNull();
    expect(store.meta.buildModules).toEqual([]);
    expect(store.meta.buildBuiltAt).toBeNull();
    expect(store.meta.persistDir).toBe("");
    expect(document.documentElement.dataset.persistDir).toBe("");
    // ⚠ 公開的那兩個留著：它們本來就不需要身分，清掉只會讓登入頁少一張插畫。
    expect(store.meta.behindProxy).toBe(true);
    expect(store.meta.loginArt).toBe("/static/images/a.webp");
  });
});

describe("AppFooter", () => {
  beforeEach(() => setActivePinia(createPinia()));

  it("🔴 沒有 build 資訊時整段不畫（登入頁沒有頁尾）", () => {
    const w = mount(AppFooter);
    // ⚠ 不是「畫一個空的頁尾」：`.footer` 有 border-top，空的話畫面上會浮出一條沒有內容
    //   的橫線，而 legacy 的登入頁從來沒有那條線。
    expect(w.find('[data-testid="footer"]').exists()).toBe(false);
    expect(w.html()).toBe("<!--v-if-->");
  });

  it("有 build 資訊就照舊畫（登入後）", () => {
    const store = useSiteStore();
    store.meta.buildModules = [
      {
        name: "claude-pty",
        version: "0.2.0",
        commit: "abc1234",
        built_at: "2026-08-20T10:00:00+00:00",
        detail: "控制平面本體。",
      },
    ];
    store.meta.buildBuiltAt = "2026-08-20T10:00:00+00:00";
    const w = mount(AppFooter);
    expect(w.find('[data-testid="footer"]').exists()).toBe(true);
    expect(w.find('[data-testid="footer-mod"]').text()).toContain("0.2.0");
    expect(w.find(".footer__sha").text()).toBe("abc1234");
    expect(w.find(".footer__built").exists()).toBe(true);
  });

  it("🟡 只有 built_at、沒有模組時仍然畫：那是「答不出來」不是「不給」", () => {
    const store = useSiteStore();
    store.meta.buildBuiltAt = "2026-08-20T10:00:00+00:00";
    const w = mount(AppFooter);
    expect(w.find('[data-testid="footer"]').exists()).toBe(true);
    expect(w.find(".footer__built").exists()).toBe(true);
  });
});
