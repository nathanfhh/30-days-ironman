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
 *   · `/api/bootstrap`（公開）——未登入者今天也看得到的東西：`<html>` 的兩個屬性、頁尾
 *     版本、登入頁的插畫。登入頁需要它們，所以它不能被關在 401 後面。
 *   · `/api/account/bootstrap`（需登入）——「這個帳號的處境」：憑證狀態、長度限制、GitLab。
 *
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
  persist_dir: string;
  build: { modules: BuildModule[]; built_at: string | null };
  login_art: string | null;
}

interface AccountBootstrap {
  /** 這一版才有：形狀與 `/api/auth/me` 的 `user` 相同，是同一個來源。
   *  舊的後端沒有這個欄位，`loadIdentity()` 會退回去問 `/api/auth/me` 並喊一聲（見那裡）。 */
  user?: User;
  default_cli: string;
  credentials: Credentials;
  limits: { name_max: number; username_max: number; min_password_length: number };
  gitlab: { enabled: boolean; host: string | null; proxy_error: string | null };
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

  /** 我是誰。401 由 api() 統一導回登入頁，這裡只負責把身分放好。
   *
   * ⚠ 拿到身分就順手把 `/api/account/bootstrap` 也帶回來：招牌在 sessions 與 account
   *   兩頁都有，兩頁都需要憑證狀態與長度限制。分開讓呼叫端各自記得打，遲早會有一頁忘記。
   * ⚠ **一個 app 生命週期只會問一次**（`identityLoaded` 擋著），跨路由不重打；cookie 失效
   *   時 api() 收到 401 會統一導回登入頁，那條路才是重新確認身分的入口。 */
  async function loadIdentity(): Promise<User | null> {
    try {
      const d = await fetchAccountMeta();
      if (d.user) {
        user.value = d.user;
        return d.user;
      }
      /* ⚠ 舊的後端還沒有 `user` 這個欄位。**降級是安全的，但不可以是無聲的**（同
         `config.UI` 那條不認得的值的處置）：退回去問 `/api/auth/me`，並且喊一聲說明為什麼
         多了這一發——不喊的話，症狀會是「golden 的網路序列莫名其妙多一行」，而肇因是兩條
         線的合併順序，那要查很久。
         ⚠ **這條相容路徑有明確的死期**：`/api/account/bootstrap` 帶 `user` 之後它就是死的，
           階段 5 拆舊時連同這段註解一起刪。 */
      console.warn(
        "[claude-pty] /api/account/bootstrap 沒有 user 欄位（後端是舊版？），" +
          "退回 /api/auth/me。網路序列會多一發，那是預期中的降級。",
      );
      const me = await api<{ user: User }>("/api/auth/me");
      user.value = me.user;
      return me.user;
    } catch {
      user.value = null;
      return null;
    } finally {
      identityLoaded.value = true;
    }
  }

  /**
   * 公開的那一條。**進站就打**，不等身分——登入頁的頁尾與插畫需要它。
   *
   * ⚠ 失敗不可以擋住畫面：這幾個值全是「畫得更完整」用的，拿不到就退回預設（頁尾留白、
   *   沒有插畫），登入與列表照樣能用。舊版模板拿不到 `build_info()` 時的行為也是留白。
   */
  async function loadPublicMeta(): Promise<void> {
    try {
      const d = await api<PublicBootstrap>("/api/bootstrap");
      meta.value = {
        ...meta.value,
        behindProxy: d.behind_proxy,
        persistDir: d.persist_dir,
        buildModules: d.build.modules,
        buildBuiltAt: d.build.built_at,
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
  async function fetchAccountMeta(): Promise<AccountBootstrap> {
    const d = await api<AccountBootstrap>("/api/account/bootstrap");
    meta.value = {
      ...meta.value,
      defaultCli: d.default_cli,
      nameMax: d.limits.name_max,
      usernameMax: d.limits.username_max,
      minPasswordLength: d.limits.min_password_length,
      gitlabEnabled: d.gitlab.enabled,
      gitlabHost: d.gitlab.host,
      gitlabProxyError: d.gitlab.proxy_error,
    };
    setCredentials(d.credentials);
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

  async function logout(): Promise<void> {
    await api("/api/auth/logout", { method: "POST" });
    user.value = null;
    credentials.value = {};
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
