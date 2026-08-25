<script setup lang="ts">
/* ── 自訂下拉 ──────────────────────────────────────────────────────────────────
 * 取代原生 <select>：外觀跨平台不受控、且塞不進圖示。
 * 保留原生語意：role=listbox/option、鍵盤（↑↓/Enter/Esc/Home/End）、aria-selected。
 *
 * 這是舊版 `createPicker` 的 Vue 版。DOM 結構、class、`data-testid` 一律照舊
 * （`<mountId>` / `-button` / `-menu` / `-opt-<value>` / `-search`），因為 e2e 與
 * aria golden 直接拿舊版那份來驗。
 *
 * `search`：給選項多到要用找的那種（例如使用者清單）。選項少的時候不要開——多一格
 * 空輸入框只是噪音。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, useTemplateRef, watch } from "vue";

import { anchorPanel } from "@/lib/anchor";

import BrandMark from "./BrandMark.vue";

export interface PickerOption {
  value: string;
  label: string;
  icon?: string;
  hint?: string;
  brand?: string;
}

export interface PickerOrigin {
  x: number;
  y: number;
}

const props = withDefaults(
  defineProps<{
    /** 掛載點的 id：testid 全部由它衍生（與舊版 `mount.id` 同一個來源）。 */
    id: string;
    options: PickerOption[];
    modelValue: string;
    search?: boolean;
    /** ⚠ **刻意沒有預設值。** 舊版的 `data-loading` 只在有人動過 `.disabled` 的 picker 上
     *  才存在（模型與思考深度那兩顆，等目錄載入時鎖起來），其餘的掛載點連這個屬性都沒有。
     *  給了預設 false 的話每一顆都會長出 `data-loading=""`，golden 的 DOM 就對不上。 */
    disabled?: boolean;
    /** picker 外面沒有文字標籤時（招牌上的主題）用它命名按鈕與清單。 */
    ariaLabel?: string;
    /** 掛載點的標籤名。舊版是就地改造模板裡既有的元素，而招牌上那一個是 `<span>`。 */
    tag?: string;
  }>(),
  { search: false, disabled: undefined, ariaLabel: "", tag: "div" },
);

const emit = defineEmits<{
  "update:modelValue": [value: string];
  change: [detail: { value: string; origin: PickerOrigin | null }];
}>();

const mount = useTemplateRef<HTMLElement>("mount");
const button = useTemplateRef<HTMLButtonElement>("button");
const menu = useTemplateRef<HTMLElement>("menu");
/* ⚠ 搜尋框**不用 template ref**：它現在住在 `v-for` 裡，而 v-for 裡的 ref 收成的是
   **陣列**——`searchInput.value?.focus()` 於是變成「對一個陣列呼叫 focus」，執行期直接
   拋 TypeError（改成 v-for 之後被 vitest 的 unhandled error 當場抓到）。
   從選單元素查一次乾淨得多，也不必去記住那個陣列語意。 */
const searchEl = (): HTMLInputElement | null =>
  menu.value?.querySelector<HTMLInputElement>('input[type="search"]') ?? null;

const open = ref(false);
const active = ref(0);
const query = ref("");
/* ⚠ 起始是 `undefined` 而不是 "down"：`data-drop` 是 `anchorPanel` 在**展開時**才寫上去的
   （見 lib/anchor），沒展開過的 picker 身上根本沒有這個屬性。 */
const drop = ref<"up" | "down" | undefined>(undefined);
/* ⚠ 選單的內容一旦畫過就**留著**，收合只是 `hidden`。舊版 `renderMenu()` 只在 open() 裡
   呼叫，關閉時不清 innerHTML——所以「沒展開過是空的 `<ul>`、展開過就一直有內容」是它的
   實際形狀，兩種狀態都要照抄。整段 v-if 掉的話，關起來之後 DOM 會比舊版少一截。 */
const hasOpened = ref(false);

/* 展開之後才有內容；沒展開過的 `<ul>` 是空的（見 hasOpened）。用陣列而不是布林，
   是為了讓樣板只有 `v-for`——理由見樣板裡那段註解。 */
const shownOptions = computed(() => (hasOpened.value ? visible.value : []));
const searchRows = computed(() => (hasOpened.value && props.search ? [0] : []));
const emptyRows = computed(() => (hasOpened.value && !visible.value.length ? [0] : []));

// 舊版只有被鎖過的 picker 身上才有 `data-loading`（見 disabled 這個 prop 的說明）。
const loadingAttr = computed(() =>
  props.disabled === undefined ? undefined : props.disabled ? "1" : "",
);

const current = computed(
  () => props.options.find((o) => o.value === props.modelValue) ?? props.options[0],
);

/** 目前查詢字串下看得到的選項。不分大小寫，比對 label。 */
const visible = computed(() => {
  const q = query.value.trim().toLowerCase();
  return q ? props.options.filter((o) => o.label.toLowerCase().includes(q)) : props.options;
});

const place = (): void => {
  if (!button.value || !menu.value) return;
  anchorPanel(button.value, menu.value, { mount: mount.value, matchWidth: true });
  drop.value = mount.value?.dataset.drop as "up" | "down" | undefined;
};

function onScroll(e: Event): void {
  if (menu.value?.contains(e.target as Node)) return; // 捲的是選單自己，不必動
  const r = button.value?.getBoundingClientRect();
  if (!r) return;
  if (r.bottom < 0 || r.top > globalThis.innerHeight) close();
  else place();
}

function onDocClick(e: MouseEvent): void {
  if (!mount.value?.contains(e.target as Node)) close();
}

function openMenu(): void {
  /* 停用中就不開。⚠ CSS 的 `pointer-events: none` 只擋得住滑鼠——鍵盤使用者照樣
     Tab 得進來、按 Enter 展開、改得動值，而伺服端稍後會靜靜把那個值丟掉。
     停用必須同時對兩種輸入方式成立。 */
  if (props.disabled || mount.value?.closest('[data-disabled="1"]')) return;
  open.value = true;
  hasOpened.value = true;
  query.value = ""; // 每次重新展開都從完整清單開始，不要留著上次打的字
  active.value = Math.max(
    0,
    props.options.findIndex((o) => o.value === props.modelValue),
  );
  void nextTick(() => {
    place();
    if (props.search) {
      const el = searchEl();
      el?.focus();
      const len = el?.value.length ?? 0;
      el?.setSelectionRange(len, len);
    }
  });
  /* 選單是 fixed 定位的，頁面一捲它就會留在原地、跟按鈕脫節。
     ⚠ **跟著重新定位，不要關掉**：選單自己就是可捲的，而且瀏覽器把元素捲進視野的
     捲動也算——關掉的話游標剛移過去就關了。capture 才收得到內層捲動容器的事件。 */
  globalThis.addEventListener("scroll", onScroll, { capture: true });
  globalThis.addEventListener("resize", close);
}

function close(): void {
  open.value = false;
  globalThis.removeEventListener("scroll", onScroll, { capture: true });
  globalThis.removeEventListener("resize", close);
}

function pick(value: string, origin: PickerOrigin | null): void {
  close();
  if (value !== props.modelValue) emit("update:modelValue", value);
  // detail 帶上點擊座標：主題切換的同心圓過渡需要圓心
  emit("change", { value, origin });
}

function onButtonKeydown(e: KeyboardEvent): void {
  if (["ArrowDown", "ArrowUp", "Enter", " "].includes(e.key) && !open.value) {
    e.preventDefault();
    openMenu();
    return;
  }
  if (!open.value) return;
  // ⚠ 一律走 visible()：開了搜尋之後「畫面上第 3 個」與「options 的第 3 個」不是同一個。
  const shown = visible.value;
  if (e.key === "Escape") {
    e.preventDefault();
    close();
  } else if (!shown.length) {
    /* 找不到任何項目時方向鍵沒有東西可選 */
  } else if (e.key === "ArrowDown") {
    e.preventDefault();
    active.value = (active.value + 1) % shown.length;
  } else if (e.key === "ArrowUp") {
    e.preventDefault();
    active.value = (active.value - 1 + shown.length) % shown.length;
  } else if (e.key === "Home") {
    e.preventDefault();
    active.value = 0;
  } else if (e.key === "End") {
    e.preventDefault();
    active.value = shown.length - 1;
  } else if (e.key === "Enter") {
    e.preventDefault();
    const r = button.value?.getBoundingClientRect(); // 鍵盤操作：以按鈕中心為圓心
    const origin = r ? { x: r.left + r.width / 2, y: r.top + r.height / 2 } : null;
    pick(shown[Math.min(active.value, shown.length - 1)].value, origin);
  }
}

/** 在輸入框裡打字時，方向鍵/Enter 交給按鈕的 keydown 處理，其餘按鍵不要往上冒泡
 *  ——否則每一個字元都會被當成 picker 的鍵盤指令。 */
function onSearchKeydown(e: KeyboardEvent): void {
  if (["ArrowDown", "ArrowUp", "Enter", "Escape", "Home", "End"].includes(e.key)) {
    onButtonKeydown(e);
    e.preventDefault();
  }
  e.stopPropagation();
}

// 清單換了一批時把 active 歸零：舊索引指的很可能是另一個東西。
watch(
  () => props.options,
  () => {
    active.value = 0;
  },
);

// 鎖起來的當下要順手關掉展開的選單——不然它會停在畫面上，點了沒反應。
watch(
  () => props.disabled,
  (on) => {
    if (on && open.value) close();
  },
);

onMounted(() => document.addEventListener("click", onDocClick));
onBeforeUnmount(() => {
  document.removeEventListener("click", onDocClick);
  close();
});

defineExpose({ open, close });
</script>

<template>
  <component
    :is="tag"
    ref="mount"
    :id="id"
    class="picker"
    :data-testid="id"
    :data-drop="drop"
    :data-loading="loadingAttr"
  >
    <button
      ref="button"
      type="button"
      class="picker__button"
      aria-haspopup="listbox"
      :aria-expanded="open ? 'true' : 'false'"
      :data-testid="`${id}-button`"
      :aria-label="ariaLabel || undefined"
      :disabled="disabled"
      @click="open ? close() : openMenu()"
      @keydown="onButtonKeydown"
    >
      <BrandMark v-if="current?.brand" :name="current.brand" />
      <i v-else class="picker__icon" :class="current?.icon || 'fa-solid fa-circle'"></i>
      <span>{{ current?.label }}</span>
      <i class="picker__caret fa-solid fa-chevron-down"></i>
    </button>
    <ul
      ref="menu"
      class="picker__menu"
      role="listbox"
      :hidden="!open"
      :data-testid="`${id}-menu`"
      :aria-label="ariaLabel || undefined"
    >
      <!-- ⚠ 三個 `v-for` 而不是 `v-if`：沒有 v-else 的 v-if 會在 DOM 上留一個空的註解節點
           當錨點，而舊版那個 ul 在沒展開過時是**完全空的**。v-for 的 Fragment 錨點是空白
           文字節點，outerHTML 看不到，所以兩邊逐字一樣。
           （⚠ 這段註解裡刻意不寫出那個註解節點的字面形狀：HTML 註解遇到第一個結束序列就
             收掉，寫進去會把這段註解截斷、整個樣板解析失敗。我剛踩過。） -->
      <li v-for="_ in searchRows" :key="'search'" class="picker__search">
        <input
          v-model="query"
          class="input input--sm"
          type="search"
          :data-testid="`${id}-search`"
          placeholder="輸入名稱篩選"
          aria-label="篩選選項"
          @input="active = 0"
          @keydown="onSearchKeydown"
        />
      </li>
      <li
        v-for="(o, i) in shownOptions"
        :key="o.value"
        class="picker__option"
        role="option"
        :data-value="o.value"
        :data-testid="`${id}-opt-${o.value || 'any'}`"
        :aria-selected="o.value === modelValue"
        :data-active="i === active"
        @click="pick(o.value, { x: $event.clientX, y: $event.clientY })"
      >
        <BrandMark v-if="o.brand" :name="o.brand" />
        <i v-else class="picker__icon" :class="o.icon || 'fa-solid fa-circle'"></i>
        <span>{{ o.label }}</span>
        <!-- v-for 而不是 v-if：沒有 hint 的選項不該多一個註解錨點（理由同上面那段）。 -->
        <span v-for="h in o.hint ? [o.hint] : []" :key="h" class="picker__hint">{{ h }}</span>
      </li>
      <li v-for="_ in emptyRows" :key="'empty'" class="picker__empty">找不到符合的項目</li>
    </ul>
  </component>
</template>
