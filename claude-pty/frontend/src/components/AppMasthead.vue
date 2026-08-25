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
import { computed, onBeforeUnmount, onMounted, ref, useTemplateRef, watch } from "vue";
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
watch(() => store.credentials, syncCredData, { deep: true });

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

/* ⚠ `#cred-data` 是舊版 `_masthead.html` 的 `<script type="application/json">`：伺服端把
 *   憑證狀態塞在頁面裡，`app.js` 進站時讀它，之後才由列表輪詢覆蓋。
 *
 *   這一版**沒有任何讀者**（值走 `/api/account/bootstrap` 與列表的順風車），節點留著純粹
 *   是為了 DOM 與舊版一致。Vue 的模板編譯器不吐 `<script>`，所以只能自己建。
 *   ⚠ 這是一個明知沒有用途的相容節點，階段 5 拆舊時**要連同模板那一行一起刪**。
 */
let credDataEl: HTMLScriptElement | null = null;

function syncCredData(): void {
  if (!credDataEl) return;
  credDataEl.textContent = JSON.stringify(store.credentials);
}

onMounted(async () => {
  const nav = document.querySelector(".masthead__nav");
  if (nav && !document.getElementById("cred-data")) {
    credDataEl = document.createElement("script");
    credDataEl.type = "application/json";
    credDataEl.id = "cred-data";
    // 舊版的位置：`.masthead__nav` 的第一個子節點（在憑證徽章之前）
    nav.insertBefore(credDataEl, nav.firstChild);
    syncCredData();
  }
  theme.value = await initTheme();
  document.addEventListener("click", onDocClick, true);
  document.addEventListener("keydown", onDocKeydown);
  globalThis.addEventListener("resize", onResize);
});

onBeforeUnmount(() => {
  credDataEl?.remove();
  credDataEl = null;
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
        <!-- ⚠ 用 `custom` 自己畫 `<a>`，不要讓 RouterLink 代勞：它會自動掛
             `router-link-active` / `router-link-exact-active` 兩個 class，而舊版那兩顆連結
             只有 `data-seg` 與 `aria-current`。多出來的 class 會讓 golden 的 DOM 對不上，
             而且 `.navseg a` 的樣式規則本來就沒有預期它們存在。
             這樣寫仍然是 SPA 導覽（navigate 保留了修飾鍵開新分頁的行為）。 -->
        <RouterLink to="/" custom v-slot="{ href, navigate }">
          <!-- prettier-ignore -->
          <a
            :href="href"
            data-seg="sessions"
            :aria-current="activeSeg === 'sessions' ? 'page' : undefined"
            @click="navigate"
          >
            <i class="fa-solid fa-terminal"></i>工作階段</a>
        </RouterLink>
        <RouterLink to="/account" custom v-slot="{ href, navigate }">
          <!-- prettier-ignore -->
          <a
            :href="href"
            data-seg="account"
            :aria-current="activeSeg === 'account' ? 'page' : undefined"
            @click="navigate"
          >
            <i class="fa-solid fa-id-badge"></i>帳號管理</a>
        </RouterLink>
      </span>
    </div>
    <nav class="masthead__nav">
      <!-- 憑證徽章：這個人設定 CLI 憑證了沒。沒設的話新 session 一開場就停在登入提示，
           所以狀態常駐在招牌上、紅著提醒。圖示與顏色全由 CSS 依 data-state 決定——
           這裡只負責給 state / label / detail 三個值，不挑圖示。 -->
      <!-- ⚠ **首幀就要在**，不 v-if。舊版是伺服端渲染的，這顆膠囊從第一次繪製就佔著位置；
           等 /api/account/bootstrap 回來才長出來的話，招牌會先窄一截再撐開。
           class 也固定寫死（`cred tip tip--wide tip--left`）——舊版沒有任何條件式 class。 -->
      <RouterLink to="/account" custom v-slot="{ href, navigate }">
        <!-- 屬性順序照舊版模板（class 在 id 之前）：順序對 HTML 沒有語意，但逐字比對
             DOM 時它是唯一還會亮的差異，留著只會讓真的差異被雜訊蓋住。 -->
        <a
          class="cred tip tip--wide tip--left"
          id="cred-badge"
          data-testid="cred-badge"
          role="status"
          :href="href"
          :data-cli="store.meta.defaultCli"
          :data-state="cred?.state"
          :data-tip="cred?.detail"
          @click="navigate"
        >
          <!-- `data-brand` 是舊版 paintCred() 拿來判斷「品牌換了才重畫 SVG」的欄位。
               這一版不需要那個最佳化（Vue 自己會 diff），但屬性照留：它是 DOM 的一部分。 -->
          <span class="cred__brand" aria-hidden="true" :data-brand="cred?.brand">
            <BrandMark v-if="cred" :name="cred.brand" cls="cred__brand-svg" />
          </span>
          <span class="cred__label">{{ cred?.label }}</span>
        </a>
      </RouterLink>
      <!-- 不標「主題」二字：picker 自己的按鈕上就有調色盤圖示與目前主題名。
           無障礙靠 aria-label（掛在按鈕與清單上，不是外層——外層沒有 role）。 -->
      <!-- ⚠ 掛載點是 `<span>` 不是 `<div>`：舊版是 `<span id="theme-picker">`，被
           `createPicker` 就地改成 `.picker`。`.masthead__nav` 是 flex，兩者的排版結果一樣，
           但 golden 比的是 DOM。 -->
      <SitePicker
        id="theme-picker"
        tag="span"
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
            {{ (store.user?.username ?? "").slice(0, 1).toUpperCase() }}</span
          >
          <span class="whoami__name">{{ store.user?.username }}</span>
          <!-- 不掛 tooltip：「admin」這個字自己就說完了 -->
          <!-- prettier-ignore -->
          <span v-if="store.user?.is_admin" class="whoami__badge">
            <i class="fa-solid fa-user-shield"></i>admin</span>
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
          <!-- prettier-ignore -->
          <button
            class="menu__item"
            type="button"
            role="menuitem"
            data-act="settings"
            data-testid="menu-settings"
            @click="onMenuClick('settings')"
          >
            <i class="fa-solid fa-sliders"></i>設定</button>
          <!-- 登出單獨一區：它結束整個工作階段，與上面那個不是同一種份量。 -->
          <div class="menu__sep" role="separator"></div>
          <!-- prettier-ignore -->
          <button
            class="menu__item menu__item--danger"
            type="button"
            role="menuitem"
            data-act="logout"
            data-testid="menu-logout"
            @click="onMenuClick('logout')"
          >
            <i class="fa-solid fa-arrow-right-from-bracket"></i>登出</button>
        </div>
      </span>
    </nav>
  </header>
</template>
