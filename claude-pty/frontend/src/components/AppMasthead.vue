<script lang="ts">
/**
 * 上一次招牌停在哪一格。thumb 的滑動全靠它（見下面 `paintedSeg`）。
 *
 * ⚠ **為什麼在這個非 setup 的區塊裡**：`<script setup>` 的內容會被編譯成 `setup()` 的函式
 *   本體，寫在那裡的 `let` 是**每個元件實例各自一份**。而招牌隨換頁重新掛載，於是那份紀錄
 *   每次都是新的 null，等於「永遠沒有上一格」，動畫一次都不會跑。第一版就是這樣寫的，
 *   單元測試當場抓到（2026-08-26）。只有非 setup 的 `<script>` 才是真的模組層級，跨掛載
 *   活著。
 * ⚠ 舊版用 sessionStorage 記，是因為它每次換頁都整份 HTML 重來、JS 狀態一併清空。這一版
 *   **刻意不用 sessionStorage**：那份記錄會活過整頁重載，於是「直接開 /account」也會被當成
 *   「從 / 滑過來」而動一下，正是 2026-07-25 那次事故要避免的首幀動畫（golden 在同一個
 *   browser context 裡跨場景也會因此飄）。模組變數在重載時歸零，冷載入自然就沒有動畫。
 */
let lastSeg: string | null = null;
</script>

<script setup lang="ts">
/*
 * 招牌。對照舊版 `server/templates/_masthead.html` 逐項搬過來，包含它的每一條理由：
 *
 *   · 分段控制而不是兩個裸連結：這兩個是**同一組互斥的去處**。
 *   · 憑證徽章做成連結而不是 span：它在未設定時是紅的、在抱怨一件事，而**能解決那件事
 *     的地方只有帳號頁**。role="status" 讓輪詢改寫時螢幕閱讀器會念出來。
 *   · 身分常駐、對身分能做的事（設定、登出）收進下拉。整個膠囊就是那顆按鈕。
 *
 * 一處與舊版不同：憑證徽章的翻頁動畫（舊版 swapCred）拿掉。它只在**換 agent** 時才跑，
 * 而這套東西只驅動 claude 一種 CLI，`switched` 恆為 false，留著等於留一段永遠不執行的
 * 程式碼。
 *
 * ⚠ **thumb 的滑動則是逐條照抄舊版的 initNavSeg，一步都不能省。** 這裡的註解一度寫著
 *   「SPA 換頁不再整份 HTML 重來，招牌的 DOM 一直是同一份，`data-active` 一改 CSS 自己就
 *   跑」，那句話**是錯的**：`AppShell`（招牌的家）掛在每一個 view 裡面，換頁時整個招牌
 *   連同 thumb 都是**新節點**（2026-08-26 用瀏覽器量到：換頁前後的 thumb 不是同一個 DOM
 *   節點）。新節點一出生就在目的地那一格，沒有「值變了」可言，所以不會有任何過渡，
 *   而使用者看到的就是瞬移。真正要做的兩件事見下面 `paintedSeg` 與 `onMounted`。
 */
import { computed, onBeforeUnmount, onMounted, ref, useTemplateRef, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import { anchorPanel } from "@/lib/anchor";
import { applyTheme, initTheme, prefersReducedMotion, THEMES } from "@/lib/theme";
import { toastAfterNav, toastError } from "@/lib/toast";
import { useSiteStore } from "@/stores/site";

import BrandMark from "./BrandMark.vue";
import SitePicker, { type PickerOption, type PickerOrigin } from "./SitePicker.vue";

const emit = defineEmits<{ settings: [] }>();

const store = useSiteStore();
const route = useRoute();
const router = useRouter();

const activeSeg = computed(() => (route.path === "/account" ? "account" : "sessions"));

/**
 * **第一幀畫在哪一格**。上一頁停在別格就先畫在那裡，下一影格才交還給 `activeSeg`，那一次
 * 屬性變化就是使用者看到的滑動（`--navseg-i` 換值 → `transform` 換值 → CSS 過渡）。
 *
 * ⚠ 不自己算 `translateX`：位置的算式只該有一份，在 CSS 的 `--navseg-i`。JS 複製一份的話，
 *   日後改格寬或加第三格時它會靜靜地算錯（舊版 initNavSeg 的原話）。
 * ⚠ `aria-current` 仍然跟著 `activeSeg`：那是「現在在哪一頁」的事實，一影格都不該說錯。
 *   會暫時說謊的只有 thumb 的落點，而它 `aria-hidden`。
 */
const animateFrom =
  lastSeg !== null && lastSeg !== activeSeg.value && !prefersReducedMotion() ? lastSeg : null;
const paintedSeg = ref(animateFrom ?? activeSeg.value);
/* 招牌若哪天不再隨換頁重掛（例如 AppShell 被提到 App.vue），這條就是接手的那一半：
   `data-active` 仍然跟著路由走，而 `data-animate` 早在掛載後那一影格就掛上了。
   ⚠ **這裡不可以寫 `lastSeg`。** 換頁時舊招牌還沒被拆掉，它的 `activeSeg` 會先變成新的
     那一格、這條 watch 先跑，`lastSeg` 於是在新招牌 setup 之前就被改成了目的地，
     `animateFrom` 一律算成 null，動畫一次都不會跑。用真瀏覽器量到的：`transform` 直接
     從 0 跳到 130px，`getAnimations()` 是空的、一個 transitionrun 都沒有（2026-08-26）。
     「上一格是哪一格」只由 `onMounted` 寫，那是每個招牌各自報到一次的地方。 */
watch(activeSeg, (v) => {
  paintedSeg.value = v;
});

const cred = computed(() => store.credentials[store.meta.defaultCli]);

const theme = ref("instrument");
const themeOptions: PickerOption[] = THEMES.map((t) => ({
  value: t.id,
  label: t.name,
  icon: t.icon,
  hint: t.mode === "light" ? "亮色" : "暗色",
}));

const navThumb = useTemplateRef<HTMLElement>("navThumb");

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
    // ⚠ **要離開就得先把身分清掉。** 只 push 的話守衛看到 `store.user` 還在，會判他仍然
    //   登入著並把這次導覽彈回 `/`，「照樣離開」這句話就不成立了（與 401 那條路同一個
    //   坑，見 lib/unauthorized）。失敗原因是 401 時全域處理器已經清過一次，這裡再清一次
    //   是安全的；而 500 那種失敗只有這裡清得到。
    store.dropIdentity();
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
  lastSeg = activeSeg.value;
  /* 從上一格滑到這一格。**兩個動作要在同一個影格裡、而且在第一次繪製之後**：
   *
   *   · `data-animate`：CSS 只在這個屬性存在時才給 `.navseg__thumb` 一條 transition
   *     （見 app.css）。它若在第一次繪製時就在，thumb 會從 `translateX(0)` 滑進來，
   *     正是 2026-07-25 那次「每次換頁閃一下」的事故。
   *   · `paintedSeg`：交還給真正的那一格。這一行才是「滑」本身。
   *
   * ⚠ 順序無所謂但**必須同一個影格**：屬性是同步寫的，`paintedSeg` 是 Vue 的 ref、下一個
   *   microtask 才 patch 進 DOM，兩者都落在這一影格的樣式計算之前。過渡是拿變更後的樣式
   *   判斷的，所以 transition 有、transform 也變了，於是它跑。
   * ⚠ `animateFrom` 是 null（冷載入、或原地重掛同一格）時這一段等於什麼都沒做：
   *   `paintedSeg` 本來就已經是 `activeSeg`。`data-animate` 照掛不誤，讓上面那條
   *   `watch(activeSeg)` 的路徑也有 transition 可用。
   * ⚠ 開了「減少動態」就整段不做（與舊版 initNavSeg 的處置一致）：`animateFrom` 已經是
   *   null，thumb 第一幀就在該在的位置，這裡再連 transition 都不給。 */
  if (!prefersReducedMotion()) {
    requestAnimationFrame(() => {
      const el = navThumb.value;
      if (!el) return;
      /* ⚠ **先讀一次版面**。過渡是拿「變更前樣式」與「變更後樣式」比出來的，而變更前樣式
         要有一次樣式計算把它定下來。換頁這條路上，招牌是在同一個 task 裡插進 DOM 的
         （router 的導覽解決之後 Vue 就掛），下一件事就是這個 rAF，中間**一次繪製都沒
         有**，於是瀏覽器眼中 thumb 從來沒有在上一格待過，`transform` 只是換了個初值，
         沒有東西可以過渡。實測（2026-08-26，chromium）：transform 直接 0 → 130px，
         `getAnimations()` 空的、一個 transitionrun 都沒有。
         讀 `offsetWidth` 會強迫當場算一次版面，上一格那個位置就成了變更前樣式。 */
      void el.offsetWidth;
      el.dataset.animate = "1";
      paintedSeg.value = activeSeg.value;
    });
  }
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
      <!-- ⚠ `data-active` 綁的是 `paintedSeg` 不是 `activeSeg`：thumb 的落點在換頁的第一影格
           要停在**上一格**，下一影格才滑過來（見上面）。下面兩顆連結的 `aria-current` 仍然
           綁 `activeSeg`：「現在在哪一頁」一影格都不該說錯。 -->
      <span class="navseg" role="group" aria-label="主要區域" :data-active="paintedSeg">
        <span ref="navThumb" class="navseg__thumb" aria-hidden="true"></span>
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
