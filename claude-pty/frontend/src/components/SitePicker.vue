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
    disabled?: boolean;
    /** picker 外面沒有文字標籤時（招牌上的主題）用它命名按鈕與清單。 */
    ariaLabel?: string;
  }>(),
  { search: false, disabled: false, ariaLabel: "" },
);

const emit = defineEmits<{
  "update:modelValue": [value: string];
  change: [detail: { value: string; origin: PickerOrigin | null }];
}>();

const mount = useTemplateRef<HTMLElement>("mount");
const button = useTemplateRef<HTMLButtonElement>("button");
const menu = useTemplateRef<HTMLElement>("menu");
const searchInput = useTemplateRef<HTMLInputElement>("searchInput");

const open = ref(false);
const active = ref(0);
const query = ref("");
const drop = ref<"up" | "down">("down");

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
  drop.value = (mount.value?.dataset.drop as "up" | "down") ?? "down";
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
  query.value = ""; // 每次重新展開都從完整清單開始，不要留著上次打的字
  active.value = Math.max(
    0,
    props.options.findIndex((o) => o.value === props.modelValue),
  );
  void nextTick(() => {
    place();
    if (props.search) {
      searchInput.value?.focus();
      const len = searchInput.value?.value.length ?? 0;
      searchInput.value?.setSelectionRange(len, len);
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
  <div
    ref="mount"
    :id="id"
    class="picker"
    :data-testid="id"
    :data-drop="drop"
    :data-loading="disabled ? '1' : ''"
  >
    <button
      ref="button"
      type="button"
      class="picker__button"
      :data-testid="`${id}-button`"
      :aria-label="ariaLabel || undefined"
      aria-haspopup="listbox"
      :aria-expanded="open ? 'true' : 'false'"
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
      :aria-label="ariaLabel || undefined"
      :data-testid="`${id}-menu`"
      :hidden="!open"
    >
      <template v-if="open">
        <li v-if="search" class="picker__search">
          <input
            ref="searchInput"
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
          v-for="(o, i) in visible"
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
          <span v-if="o.hint" class="picker__hint">{{ o.hint }}</span>
        </li>
        <li v-if="!visible.length" class="picker__empty">找不到符合的項目</li>
      </template>
    </ul>
  </div>
</template>
