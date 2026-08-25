<script setup lang="ts">
/*
 * 招牌。對照舊版 `server/templates/_masthead.html` 逐項搬過來，包含它的每一條理由：
 *
 *   · 分段控制而不是兩個裸連結：這兩個是**同一組互斥的去處**。
 *   · 憑證徽章做成連結而不是 span：它在未設定時是紅的、在抱怨一件事，而**能解決那件事
 *     的地方只有帳號頁**。role="status" 讓輪詢改寫時螢幕閱讀器會念出來。
 *   · 身分常駐、對身分能做的事（設定、登出）收進下拉。整個膠囊就是那顆按鈕。
 *
 * 兩處與舊版不同，都是 SPA 帶來的化簡：
 *   1. thumb 的滑動不再需要「記住上一頁停在哪」（舊版 initNavSeg）。換頁不再整份 HTML
 *      重來，招牌的 DOM 一直是同一份，`data-active` 一改 CSS 的 transition 自己就跑。
 *   2. 憑證徽章的翻頁動畫（舊版 swapCred）拿掉：它只在**換 agent** 時才跑，而這套東西
 *      只驅動 claude 一種 CLI，`switched` 恆為 false——留著等於留一段永遠不執行的程式碼。
 */
import { computed, onBeforeUnmount, onMounted, ref, useTemplateRef } from "vue";
import { useRoute, useRouter } from "vue-router";

import { anchorPanel } from "@/lib/anchor";
import { applyTheme, initTheme, THEMES } from "@/lib/theme";
import { toastAfterNav, toastError } from "@/lib/toast";
import { useSiteStore } from "@/stores/site";

import BrandMark from "./BrandMark.vue";
import SitePicker, { type PickerOption, type PickerOrigin } from "./SitePicker.vue";

const emit = defineEmits<{ settings: [] }>();

const store = useSiteStore();
const route = useRoute();
const router = useRouter();

const activeSeg = computed(() => (route.path === "/account" ? "account" : "sessions"));

const cred = computed(() => store.credentials[store.meta.defaultCli]);

const theme = ref("instrument");
const themeOptions: PickerOption[] = THEMES.map((t) => ({
  value: t.id,
  label: t.name,
  icon: t.icon,
  hint: t.mode === "light" ? "亮色" : "暗色",
}));

const menuOpen = ref(false);
const accountBtn = useTemplateRef<HTMLButtonElement>("accountBtn");
const accountMenu = useTemplateRef<HTMLElement>("accountMenu");

function setOpen(on: boolean, { focusBack = false } = {}): void {
  menuOpen.value = on;
  if (on) {
    void Promise.resolve().then(() => {
      if (accountBtn.value && accountMenu.value) {
        anchorPanel(accountBtn.value, accountMenu.value, { matchWidth: true });
      }
    });
  } else if (focusBack) {
    accountBtn.value?.focus();
  }
}

const items = (): HTMLButtonElement[] =>
  [...(accountMenu.value?.querySelectorAll(".menu__item") ?? [])] as HTMLButtonElement[];

function onBtnKeydown(e: KeyboardEvent): void {
  // 鍵盤從按鈕直接往下：↓ 開啟並停在第一項（原生 menu button 的慣例）
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault();
    setOpen(true);
    void Promise.resolve().then(() => {
      const list = items();
      (e.key === "ArrowDown" ? list[0] : list.at(-1))?.focus();
    });
  }
}

function onMenuKeydown(e: KeyboardEvent): void {
  const list = items();
  const i = list.indexOf(document.activeElement as HTMLButtonElement);
  if (e.key === "ArrowDown") {
    e.preventDefault();
    list[(i + 1) % list.length]?.focus();
  }
  if (e.key === "ArrowUp") {
    e.preventDefault();
    list[(i - 1 + list.length) % list.length]?.focus();
  }
  if (e.key === "Escape") {
    e.preventDefault();
    setOpen(false, { focusBack: true });
  }
}

/** 登出。設定選單與任何其他入口共用同一份——這條路徑有它自己的失敗處理。 */
async function doLogout(): Promise<void> {
  try {
    await store.logout();
  } catch (ex) {
    // 登出失敗也要照樣離開：cookie 可能早就失效了（那正是常見的失敗原因），
    // 把人留在一個進不去任何頁面的畫面上更糟。
    toastError("登出", ex);
    await router.push("/login");
    return;
  }
  toastAfterNav("已登出", "success", "工作階段已結束，session 本身仍在背景執行");
  await router.push("/login");
}

function onMenuClick(act: "settings" | "logout"): void {
  setOpen(false);
  if (act === "logout") void doLogout();
  if (act === "settings") emit("settings");
}

// ⚠ 捕獲階段：面板內容若被重畫，冒泡時 contains() 會對已經被換掉的節點回 false
//   而讓選單自己關掉（picker 踩過同一個坑）。
function onDocClick(e: MouseEvent): void {
  if (!menuOpen.value) return;
  const t = e.target as Node;
  if (!accountMenu.value?.contains(t) && !accountBtn.value?.contains(t)) setOpen(false);
}

function onDocKeydown(e: KeyboardEvent): void {
  if (e.key === "Escape" && menuOpen.value) setOpen(false, { focusBack: true });
}

function onResize(): void {
  if (menuOpen.value && accountBtn.value && accountMenu.value) {
    anchorPanel(accountBtn.value, accountMenu.value, { matchWidth: true });
  }
}

function onThemeChange(detail: { value: string; origin: PickerOrigin | null }): void {
  void applyTheme(detail.value, detail.origin);
}

onMounted(async () => {
  theme.value = await initTheme();
  document.addEventListener("click", onDocClick, true);
  document.addEventListener("keydown", onDocKeydown);
  globalThis.addEventListener("resize", onResize);
});

onBeforeUnmount(() => {
  document.removeEventListener("click", onDocClick, true);
  document.removeEventListener("keydown", onDocKeydown);
  globalThis.removeEventListener("resize", onResize);
});
</script>

<template>
  <header class="masthead" data-testid="masthead">
    <div class="masthead__brand">
      <span class="masthead__title">claude<em>-pty</em></span>
      <span class="masthead__kicker">控制平台</span>
      <span class="navseg" role="group" aria-label="主要區域" :data-active="activeSeg">
        <span class="navseg__thumb" aria-hidden="true"></span>
        <!-- 兩格的字數刻意相當（四個中文字），等寬才不會看起來是硬撐出來的 -->
        <RouterLink
          to="/"
          data-seg="sessions"
          :aria-current="activeSeg === 'sessions' ? 'page' : undefined"
        >
          <i class="fa-solid fa-terminal"></i>工作階段
        </RouterLink>
        <RouterLink
          to="/account"
          data-seg="account"
          :aria-current="activeSeg === 'account' ? 'page' : undefined"
        >
          <i class="fa-solid fa-id-badge"></i>帳號管理
        </RouterLink>
      </span>
    </div>
    <nav class="masthead__nav">
      <!-- 憑證徽章：這個人設定 CLI 憑證了沒。沒設的話新 session 一開場就停在登入提示，
           所以狀態常駐在招牌上、紅著提醒。圖示與顏色全由 CSS 依 data-state 決定——
           這裡只負責給 state / label / detail 三個值，不挑圖示。 -->
      <RouterLink
        v-if="cred"
        id="cred-badge"
        class="cred tip tip--wide tip--left"
        data-testid="cred-badge"
        role="status"
        to="/account"
        :data-cli="store.meta.defaultCli"
        :data-state="cred.state"
        :data-tip="cred.detail"
      >
        <span class="cred__brand" aria-hidden="true">
          <BrandMark :name="cred.brand" cls="cred__brand-svg" />
        </span>
        <span class="cred__label">{{ cred.label }}</span>
      </RouterLink>
      <!-- 不標「主題」二字：picker 自己的按鈕上就有調色盤圖示與目前主題名。
           無障礙靠 aria-label（掛在按鈕與清單上，不是外層——外層沒有 role）。 -->
      <SitePicker
        id="theme-picker"
        v-model="theme"
        :options="themeOptions"
        aria-label="介面主題"
        @change="onThemeChange"
      />
      <span class="masthead__account">
        <button
          ref="accountBtn"
          class="whoami"
          id="account-btn"
          type="button"
          aria-haspopup="menu"
          :aria-expanded="menuOpen ? 'true' : 'false'"
          aria-controls="account-menu"
          data-testid="account-btn"
          @click="setOpen(!menuOpen)"
          @keydown="onBtnKeydown"
        >
          <span class="whoami__avatar" aria-hidden="true">
            {{ (store.user?.username ?? "").slice(0, 1).toUpperCase() }}
          </span>
          <span class="whoami__name">{{ store.user?.username }}</span>
          <!-- 不掛 tooltip：「admin」這個字自己就說完了 -->
          <span v-if="store.user?.is_admin" class="whoami__badge">
            <i class="fa-solid fa-user-shield"></i>admin
          </span>
          <i class="fa-solid fa-chevron-down whoami__caret" aria-hidden="true"></i>
        </button>
        <div
          ref="accountMenu"
          class="menu"
          id="account-menu"
          role="menu"
          :hidden="!menuOpen"
          data-testid="account-menu"
          @keydown="onMenuKeydown"
        >
          <button
            class="menu__item"
            type="button"
            role="menuitem"
            data-act="settings"
            data-testid="menu-settings"
            @click="onMenuClick('settings')"
          >
            <i class="fa-solid fa-sliders"></i>設定
          </button>
          <!-- 登出單獨一區：它結束整個工作階段，與上面那個不是同一種份量。 -->
          <div class="menu__sep" role="separator"></div>
          <button
            class="menu__item menu__item--danger"
            type="button"
            role="menuitem"
            data-act="logout"
            data-testid="menu-logout"
            @click="onMenuClick('logout')"
          >
            <i class="fa-solid fa-arrow-right-from-bracket"></i>登出
          </button>
        </div>
      </span>
    </nav>
  </header>
</template>
