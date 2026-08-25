import { defineStore } from "pinia";
import { ref } from "vue";

import { api } from "@/api/client";

/*
 * 全站唯一一個 store：**「這一次連線的身分與環境」**。
 *
 * 為什麼只有一個：Pinia 的價值在這個 app 裡只有一處成立——身分與憑證狀態被招牌、
 * 建立表單、列表三個互不相鄰的地方讀，而它們的更新來源是同一個（列表輪詢順風車帶回來
 * 的 `credentials`）。其餘狀態（清單、篩選、表單）都只有一個擁有者，放進 store 只會
 * 讓「誰改了它」變難回答。
 */

export interface User {
  id: number;
  username: string;
  is_admin: boolean;
  [key: string]: unknown;
}

export interface CredentialState {
  cli: string;
  brand: string;
  ok: boolean;
  state: string;
  label: string;
  detail: string;
}

export type Credentials = Record<string, CredentialState>;

export interface BuildModule {
  name: string;
  version: string | null;
  commit: string | null;
  detail: string;
  built_at: string | null;
}

/**
 * 伺服端環境事實。來源是階段 3 開的兩條 bootstrap endpoint，**分界線是 gate 不是頁面**：
 *
 *   · `/api/bootstrap`（公開）：登入頁自己要畫對的東西：`<html data-behind-proxy>`
 *     與登入頁的插畫。登入頁需要它們，所以它不能被關在 401 後面。
 *   · `/api/account/bootstrap`（需登入）：先證明你是誰才給的：憑證狀態、長度限制、
 *     GitLab，以及 **2026-08-26（裁示 L4）搬過去的版號與主機路徑**。
 *
 * ⚠ `persistDir` / `buildModules` / `buildBuiltAt` 這三個欄位**在登入之前一定是預設值**
 *   （空字串、空陣列、null），這不是「還沒載入」，是「不給」。取用它們的地方要能在那個
 *   狀態下畫得成立：抽屜那段有 `v-if`（而抽屜本來就只在登入後存在），頁尾則整段不畫。
 * ⚠ `buildBuiltAt` 是**整包**的屬性、不屬於任何一個模組，所以獨立一個欄位而不是讀
 *   `buildModules[0].built_at`。端點的 docstring 特地把它提到最外層並寫明理由：留在列裡
 *   的話遲早會有人把它畫成「claude-pty 這一列的時間」。
 */
export interface SiteMeta {
  behindProxy: boolean;
  persistDir: string;
  buildModules: BuildModule[];
  /** 整包的建置時間（`build.built_at`），不是任何一個模組的。 */
  buildBuiltAt: string | null;
  /** 登入頁插畫的**完整網址**（伺服端每次呼叫重挑一張），沒有圖就 null。 */
  loginArt: string | null;
  defaultCli: string;
  nameMax: number;
  usernameMax: number;
  minPasswordLength: number;
  gitlabEnabled: boolean;
  gitlabHost: string | null;
  gitlabProxyError: string | null;
}

const META_DEFAULTS: SiteMeta = {
  behindProxy: false,
  persistDir: "",
  buildModules: [],
  buildBuiltAt: null,
  loginArt: null,
  defaultCli: "claude",
  nameMax: 25,
  usernameMax: 32,
  minPasswordLength: 8,
  gitlabEnabled: false,
  gitlabHost: null,
  gitlabProxyError: null,
};

interface PublicBootstrap {
  behind_proxy: boolean;
  login_art: string | null;
}

interface AccountBootstrap {
  /** 形狀與 `/api/auth/me` 的 `user` 相同，是同一個來源（見 server/app.py 的
   *  `account_bootstrap`）。冷載入靠它，所以這一頁不必再問一次「我是誰」。 */
  user: User;
  default_cli: string;
  credentials: Credentials;
  limits: { name_max: number; username_max: number; min_password_length: number };
  gitlab: { enabled: boolean; host: string | null; proxy_error: string | null };
  /** 宿主機上「寫了會留著」的那個目錄。**登入後才給**（裁示 L4）。 */
  persist_dir: string;
  /** 頁尾那一排。**登入後才給**（裁示 L4）。 */
  build: { modules: BuildModule[]; built_at: string | null };
}

export const useSiteStore = defineStore("site", () => {
  const user = ref<User | null>(null);
  const credentials = ref<Credentials>({});
  const meta = ref<SiteMeta>({ ...META_DEFAULTS });
  /** 進頁那一發 `/api/auth/me` 有沒有回來過。router 的守衛靠它決定要不要等。 */
  const identityLoaded = ref(false);

  /**
   * 登入成功之後，直接用回應裡的身分——**不要再打一次 `/api/auth/me`**。
   *
   * `POST /api/auth/login` 回的就是 `{user: …}`（見 server/app.py 的 login），那份與
   * `/api/auth/me` 是同一個來源。再問一次除了多一趟往返之外，還讓「登入」這個動作在網路
   * 序列上多一發 golden 對不到的呼叫。
   */
  function adoptIdentity(u: User): void {
    user.value = u;
    identityLoaded.value = true;
  }

  /** 我是誰。**冷載入時這是唯一的那一發**——身分與這個帳號的處境同一條回應。
   *
   * ⚠ **一個 app 生命週期只會問一次**（`identityLoaded` 擋著），跨路由不重打；cookie 失效
   *   時 api() 收到 401 會統一導回登入頁，那條路才是重新確認身分的入口。 */
  async function loadIdentity(): Promise<User | null> {
    try {
      await fetchAccountMeta({ probe: true });
      return user.value;
    } catch {
      user.value = null;
      return null;
    } finally {
      identityLoaded.value = true;
    }
  }

  /**
   * 公開的那一條。**進站就打**，不等身分：登入頁的插畫需要它。
   *
   * ⚠ **它只填得起兩個欄位。** 版號與主機路徑在 2026-08-26（裁示 L4）搬進要登入的那條，
   *   所以這裡不再碰 `persistDir` / `buildModules` / `buildBuiltAt`，那三個要等
   *   `fetchAccountMeta`。未登入時它們**就是**預設值，而那是規格不是載入中。
   * ⚠ 失敗不可以擋住畫面：這兩個值都是「畫得更完整」用的，拿不到就退回預設（沒有插畫、
   *   `data-behind-proxy` 當 0），登入照樣能用。
   */
  async function loadPublicMeta(): Promise<void> {
    try {
      const d = await api<PublicBootstrap>("/api/bootstrap");
      meta.value = {
        ...meta.value,
        behindProxy: d.behind_proxy,
        loginArt: d.login_art,
      };
    } catch {
      /* 留白，不猜值——猜一個看起來合理的版本號比空白糟得多 */
    }
    applyMetaToRoot();
  }

  /**
   * 需要登入的那一條。打一次、把 meta 與憑證都填好，並把回應交回去。
   *
   * ⚠ **冷載入一個要登入的頁面時，這是唯一的那一發。** 身分（`user`）也在這條回應裡——
   *   它本來就是「這個帳號的處境」那條端點，把 who am I 併進來之後，冷載入從三趟往返
   *   （bootstrap ＋ auth/me ＋ account/bootstrap）變成兩趟。
   */
  async function fetchAccountMeta({ probe = false } = {}): Promise<AccountBootstrap> {
    // probe：這一發是「我是誰」的探測，401 由呼叫端自己解讀（見 api 的 handleUnauthorized）
    const d = await api<AccountBootstrap>("/api/account/bootstrap", {
      handleUnauthorized: !probe,
    });
    meta.value = {
      ...meta.value,
      defaultCli: d.default_cli,
      nameMax: d.limits.name_max,
      usernameMax: d.limits.username_max,
      minPasswordLength: d.limits.min_password_length,
      gitlabEnabled: d.gitlab.enabled,
      gitlabHost: d.gitlab.host,
      gitlabProxyError: d.gitlab.proxy_error,
      persistDir: d.persist_dir,
      buildModules: d.build.modules,
      buildBuiltAt: d.build.built_at,
    };
    /* ⚠ `data-persist-dir` 是這一條回來之後才寫得上去的（裁示 L4 之後它是登入後的事實）。
       抽屜是 runtime 才建的、而且只存在於登入後的頁面，所以「進站那一發寫一次」不夠，
       這裡要再寫一次。漏掉的話抽屜標題列會少一整行，而且不會有任何錯誤。 */
    applyMetaToRoot();
    setCredentials(d.credentials);
    /* 身分也在這一條回應裡。順手收下，呼叫端就不必記得「存完 PAT 之後 user 的
       `gitlab_pat_configured` 也變了」——那種要靠人記得的事遲早會漏掉一處。 */
    user.value = d.user;
    return d;
  }

  /** 只要 meta 不要身分的呼叫端（登入成功之後——身分已經從登入的回應收下了）。 */
  async function loadAccountMeta(): Promise<void> {
    try {
      await fetchAccountMeta();
    } catch {
      /* 拿不到就用預設，畫面不至於壞掉 */
    }
  }

  /**
   * 把兩個環境事實寫回 `<html>` 的 data 屬性。
   * 舊版是伺服端在 base.html 直接印上去的，而抽屜（runtime 才建）就是從那裡讀——
   * 讀法維持一字不變，換掉的只有「誰寫進去」。
   */
  function applyMetaToRoot(): void {
    const root = document.documentElement;
    root.dataset.behindProxy = meta.value.behindProxy ? "1" : "0";
    root.dataset.persistDir = meta.value.persistDir;
  }

  /** 列表輪詢順風車帶回來的憑證狀態。欄位缺席就維持現狀，不要清成空白。 */
  function setCredentials(all: Credentials | undefined | null): void {
    if (!all) return;
    credentials.value = all;
  }

  /**
   * 登出。**登入後才拿得到的 meta 要一起清掉**，不只是身分與憑證。
   *
   * ⚠ 登出是 SPA 內的換頁（`router.push("/login")`），不是整頁跳轉：store 活著，meta 裡
   *   那份版號與主機路徑會原封不動跟著人回到登入頁，頁尾就照樣印出來。那正是裁示 L4 要
   *   收的東西，只是換了一條路徑洩漏。所以這裡把「要登入才給的那些」還原成預設值，
   *   公開那兩個（`behindProxy` / `loginArt`）留著：它們本來就不需要身分。
   */
  async function logout(): Promise<void> {
    await api("/api/auth/logout", { method: "POST" });
    user.value = null;
    credentials.value = {};
    meta.value = {
      ...META_DEFAULTS,
      behindProxy: meta.value.behindProxy,
      loginArt: meta.value.loginArt,
    };
    applyMetaToRoot();
  }

  return {
    user,
    credentials,
    meta,
    identityLoaded,
    loadIdentity,
    adoptIdentity,
    loadPublicMeta,
    loadAccountMeta,
    applyMetaToRoot,
    setCredentials,
    logout,
  };
});
