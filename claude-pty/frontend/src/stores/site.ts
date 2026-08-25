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

/*
 * ⚠ TODO(階段 3)：以下這幾項今天是 Jinja 注入的伺服端事實，**還沒有對應的 API**
 *   （計畫的階段 3 就是在做這件事：50 處注入改 endpoint）。SPA 拿不到伺服端渲染，
 *   所以在那支端點出現之前先用這裡的預設值，並且**只有一個地方要改**：
 *   `loadMeta()` 換成打那支端點即可。
 *
 *   目前受影響的畫面（已在日誌與回報中列出）：
 *     · `name_max`      建立表單與改名對話框的長度上限（config.NAME_MAX）
 *     · `gitlab_enabled` 列表 GitLab 標記的 gate（config.gitlab_enabled()）
 *     · `behind_proxy`  終端要開抽屜還是新分頁（config.BEHIND_PROXY）
 *     · `persist_dir`   抽屜標題列的「哪個目錄寫了會留著」（config.DATA_BIND）
 *     · `build_info`    頁尾的版本與 commit（server/version.py）
 *     · 登入頁的插畫（`web.LOGIN_ART` 每次隨機挑一張）
 */
export interface SiteMeta {
  behindProxy: boolean;
  persistDir: string;
  nameMax: number;
  gitlabEnabled: boolean;
  defaultCli: string;
  buildModules: BuildModule[];
  loginArt: string | null;
}

const META_DEFAULTS: SiteMeta = {
  behindProxy: false,
  persistDir: "",
  // ⚠ 暫時值。真值是 config.NAME_MAX（目前 25）；階段 3 之後由 API 給，
  //   在那之前這裡寫死一份**只影響前端的提示與 maxlength**，後端仍然會擋。
  nameMax: 25,
  gitlabEnabled: false,
  defaultCli: "claude",
  buildModules: [],
  loginArt: null,
};

export const useSiteStore = defineStore("site", () => {
  const user = ref<User | null>(null);
  const credentials = ref<Credentials>({});
  const meta = ref<SiteMeta>({ ...META_DEFAULTS });
  /** 進頁那一發 `/api/auth/me` 有沒有回來過。router 的守衛靠它決定要不要等。 */
  const identityLoaded = ref(false);

  /** 我是誰。401 由 api() 統一導回登入頁，這裡只負責把身分放好。 */
  async function loadIdentity(): Promise<User | null> {
    try {
      const d = await api<{ user: User }>("/api/auth/me");
      user.value = d.user;
      return d.user;
    } catch {
      user.value = null;
      return null;
    } finally {
      identityLoaded.value = true;
    }
  }

  /**
   * 伺服端環境事實。**現在沒有這支 API**（見上方 TODO），所以它只是把預設值放好；
   * 階段 3 的端點一上線就在這裡換掉，其餘畫面一個字都不必動。
   */
  function loadMeta(): void {
    meta.value = { ...META_DEFAULTS };
    applyMetaToRoot();
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
    loadMeta,
    applyMetaToRoot,
    setCredentials,
    logout,
  };
});
