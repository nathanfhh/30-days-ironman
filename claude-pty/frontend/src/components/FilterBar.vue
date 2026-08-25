<script setup lang="ts">
/* ── 篩選 ──────────────────────────────────────────────────────────────────────
 * 條件的唯一真相在**網址**，不是這幾個 picker 的內部狀態。理由：重新整理、把連結貼給
 * 別人，看到的都該是同一份清單（分頁狀態 ?tab 早就是這樣做的）。picker 只是網址的
 * 編輯器——每次變動就重寫網址再重抓。
 *
 * ⚠ 用 `router.replace` 不是 `push`，所以**「上一頁」不會回到前一組條件**——這與切換
 *   頁籤的處理一致（改條件不該在瀏覽器的上一頁堆一疊）。要支援上一頁的話得改成 push
 *   並補一個 popstate handler，那是另一個決定；別在註解裡宣稱它已經成立。
 *
 * 「不限」一律用**空字串**，不是省略也不是 "all"：後端把空字串與缺席都當成不限
 * （見 app._tri_bool），而空字串讓 picker 有一個實際可選的值。
 *
 * ⚠ 展開/收合走外層 shell 的 `data-open`，**不是** `hidden`：`hidden` 等於
 *   display:none，而 display 不可過渡。高度的過渡靠 shell 的 grid-template-rows。
 * ⚠ 外框（padding / border / 背景）必須在 `.filters__box` 這一層——`.filters` 是純粹的
 *   裁切層，外框留在它上面的話收起來仍佔 34px，而那 34px 也讓 `is_hidden()` 判定為
 *   「還看得見」。
 */
import { computed, ref } from "vue";
import { useRoute, useRouter } from "vue-router";

import { activeFilterKeys, ANY, CUSTOM, FILTER_KEYS, queryString } from "@/lib/filters";

import RangePicker, { type RangeValue } from "./RangePicker.vue";
import SitePicker, { type PickerOption } from "./SitePicker.vue";

const props = defineProps<{
  open: boolean;
  /** 使用者有沒有真的動過那顆篩選鍵。決定 `inert` 要不要寫（見下方）。 */
  toggled: boolean;
}>();
const emit = defineEmits<{ changed: [] }>();

const route = useRoute();
const router = useRouter();

const q = (key: string): string => queryString(route.query, key);

const range = computed<RangeValue>(() => ({ from: q("from"), to: q("to") }));

/* ⚠ 「這一格現在停在自訂範圍」是**畫面狀態，不進網址**。`since=custom` 送給後端會被
 *   當成天數解析（400），而使用者可能只是先選了自訂、還沒挑區間。
 *   帶著 from/to 進來（書籤／別人分享的連結）時它必須自己成立——少了那一段，畫面會說
 *   「不限」而清單卻是篩過的。 */
const rangeOpen = ref(Boolean(q("from") || q("to")));
const sinceValue = computed(() => (q("from") || q("to") || rangeOpen.value ? CUSTOM : q("since")));
const showRange = computed(() => sinceValue.value === CUSTOM);

const activeKeys = computed(() => activeFilterKeys(route.query));

const summary = computed(() =>
  activeKeys.value.length ? `${activeKeys.value.length} 個條件生效中` : "沒有套用任何條件",
);

type Query = Record<string, string>;

function currentQuery(): Query {
  const out: Query = {};
  for (const [k, v] of Object.entries(route.query)) {
    if (typeof v === "string" && v) out[k] = v;
  }
  return out;
}

async function apply(next: Query): Promise<void> {
  await router.replace({ path: route.path, query: next });
  emit("changed");
}

function setFilter(key: string, value: string): void {
  const next = currentQuery();
  if (value === ANY) delete next[key];
  else next[key] = value;
  void apply(next);
}

/* 時間範圍那格是**一格兩種語意**：預設值走 `since=<天數>`，自訂走 `from`/`to`。
 * 兩者不可並存（後端會回 400——「一週內」又「從三月到四月」沒有誠實的解釋），
 * 所以切換時一定要把另一邊清掉。這是唯一需要特別處理的一格。 */
function setTimeFilter(value: string, custom?: RangeValue): void {
  const next = currentQuery();
  delete next.since;
  delete next.from;
  delete next.to;
  rangeOpen.value = value === CUSTOM;
  if (value === CUSTOM) {
    // rangePicker 回的已經是帶時區偏移的 ISO——後端只收帶時區的，不猜是哪一區的牆上時間。
    const v = custom ?? range.value;
    if (v.from) next.from = v.from;
    if (v.to) next.to = v.to;
  } else if (value !== ANY) {
    next.since = value;
  }
  void apply(next);
}

function clearFilters(): void {
  const next = currentQuery();
  for (const k of FILTER_KEYS) delete next[k];
  rangeOpen.value = false;
  void apply(next);
}

// 三態的中間那一格一律叫「不限」，而且排在第一個：它是預設，也是「回到沒有條件」
const tri = (yes: string, no: string): PickerOption[] => [
  { value: ANY, label: "不限", icon: "fa-solid fa-asterisk" },
  { value: "1", label: yes, icon: "fa-solid fa-circle-check" },
  { value: "0", label: no, icon: "fa-solid fa-circle-minus" },
];

// 時間範圍：預設幾個常用的，最後一格是自訂區間——它可以拉得要多寬有多寬，所以排在最後。
const SINCE_OPTIONS: PickerOption[] = [
  { value: ANY, label: "不限", icon: "fa-solid fa-asterisk" },
  { value: "1", label: "一天內", icon: "fa-solid fa-clock" },
  { value: "7", label: "一週內", icon: "fa-solid fa-calendar-week" },
  { value: "30", label: "一個月內", icon: "fa-solid fa-calendar-days" },
  { value: CUSTOM, label: "自訂範圍", icon: "fa-solid fa-sliders" },
];

const NET_OPTIONS: PickerOption[] = [
  { value: ANY, label: "不限", icon: "fa-solid fa-asterisk" },
  { value: "restricted", label: "限制（白名單）", icon: "fa-solid fa-shield-halved" },
  { value: "unrestricted", label: "開放", icon: "fa-solid fa-globe" },
];

const CAP_OPTIONS = tri("有錄製", "沒錄製");
const TEL_OPTIONS = tri("有送", "沒送");

/* 收合時整塊要退出 Tab 序：grid 收成 0fr 只是視覺上不見，裡面的 picker 與輸入框
   仍然聚焦得到，鍵盤使用者會 Tab 進一塊看不見的區域。

   ⚠ 展開時要回傳 `undefined` 而**不是** false。`inert` 不在 Vue 認得的布林屬性清單裡
     （itemscope/allowfullscreen/formnovalidate/ismap/nomodule/novalidate/readonly），
     所以 `:inert="false"` 會照字面渲染成 `inert="false"`——而 HTML 的規則是**屬性存在
     就是 inert**，值是什麼都一樣。症狀是篩選列展開了卻整塊點不到，而 DOM 看起來是對的。
     單元測試抓到的（2026-08-25）。 */
/* ⚠ 展開時要回傳 `undefined` 而**不是** false：`inert` 不在 Vue 認得的布林屬性清單裡，
     `:inert="false"` 會照字面渲染成 `inert="false"`，而 HTML 的規則是**屬性存在就是
     inert**。症狀是篩選列展開了卻整塊點不到，而 DOM 看起來是對的（單元測試抓到的）。
   ⚠ **首次載入也不寫。** 舊版的 `filterBar.inert` 只在 `setFiltersOpen()` 裡設，而那支
     只有點了篩選鍵才會跑——剛進站、篩選列收著的時候舊版身上**沒有**這個屬性。無條件寫
     的話 golden 的第一幀就對不上。
     （那確實是舊版的一個小洞：沒點過就 Tab 得進收合的區域。但那是舊版的行為，1:1 這個
     階段不在這裡改；要修是階段 5 拆舊之後、而且該連舊版一起修。） */
const inert = computed(() => (props.toggled && !props.open ? true : undefined));
</script>

<template>
  <div class="filters-shell" id="filter-shell" :data-open="open ? '1' : '0'">
    <div class="filters" id="filter-bar" data-testid="filter-bar" :inert="inert">
      <div class="filters__box">
        <!-- 排序：先是「這場是誰開的、跑什麼、怎麼跑」，時間範圍**擺最後**。
             這樣選了「自訂範圍」時，起迄兩格就緊接在它後面流下去。 -->
        <div class="filters__grid" data-testid="filters-grid">
          <div class="field">
            <span
              class="label tip tip--wide"
              data-testid="filter-field-label"
              data-tip="限制＝只放行白名單（entrypoint 會套 iptables）；開放＝容器可以連任何地方。"
              >網路能力</span
            >
            <SitePicker
              id="pick-fnet"
              :model-value="q('network')"
              :options="NET_OPTIONS"
              @change="setFilter('network', $event.value)"
            />
          </div>
          <div class="field">
            <span
              class="label tip tip--wide"
              data-testid="filter-field-label"
              data-tip="這場有沒有把出網流量錄下來（經 mitm 落到 host）。"
              >流量錄製</span
            >
            <SitePicker
              id="pick-fcap"
              :model-value="q('capture')"
              :options="CAP_OPTIONS"
              @change="setFilter('capture', $event.value)"
            />
          </div>
          <div class="field">
            <span
              class="label tip tip--wide"
              data-testid="filter-field-label"
              data-tip="這場有沒有要求把 OpenTelemetry 送到 Jaeger。容器起來後會先探 Jaeger 通不通，探不通就不送——這個條件篩的是要求，不是結果。"
              >Telemetry</span
            >
            <SitePicker
              id="pick-ftel"
              :model-value="q('telemetry')"
              :options="TEL_OPTIONS"
              @change="setFilter('telemetry', $event.value)"
            />
          </div>
          <div class="field">
            <span
              class="label tip tip--wide"
              data-testid="filter-field-label"
              data-tip="從現在往回算。「執行中」比的是建立時間，「已結束」比的是結束時間——在已結束的清單裡，「一週內」問的是「一週內結束的」。選「自訂範圍」後面會接出起迄兩格。"
              >時間範圍</span
            >
            <SitePicker
              id="pick-since"
              :model-value="sinceValue"
              :options="SINCE_OPTIONS"
              @change="setTimeFilter($event.value)"
            />
          </div>
          <!-- 自訂區間的兩個欄位只有選了「自訂範圍」才出現——平時佔著一整列很吵 -->
          <div
            class="field range-field"
            id="field-range"
            :hidden="!showRange"
            data-testid="filter-range"
          >
            <span
              class="label tip tip--wide"
              data-testid="filter-field-label"
              data-tip="指定一段明確的區間。展開後左右兩個月並排：點一下選起點、再點一下選終點，中間滑過會預覽整段；也可以直接改上方的日期與時間。任一端留空＝那一端不限。"
              >起迄</span
            >
            <!-- 按下「確定」才會走到這裡（半截的區間不該觸發查詢） -->
            <RangePicker :model-value="range" @change="setTimeFilter('custom', $event)" />
          </div>
        </div>
        <div class="filters__foot">
          <span class="section-head__note" id="filter-summary" data-testid="filter-summary">{{
            summary
          }}</span>
          <!-- prettier-ignore -->
          <button
            class="btn btn--sm"
            id="filter-clear"
            :disabled="activeKeys.length === 0"
            data-testid="filter-clear"
            @click="clearFilters"
          >
            <i class="fa-solid fa-eraser"></i> 清除全部條件</button>
        </div>
      </div>
    </div>
  </div>
</template>
