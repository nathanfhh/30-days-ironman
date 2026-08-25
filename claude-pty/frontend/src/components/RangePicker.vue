<script setup lang="ts">
/* ── 日期區間選擇器 ────────────────────────────────────────────────────────────
 *
 * 這裡原本是兩個 `<input type="datetime-local">`。原生控制項在「挑一段區間」這件事上
 * 很難用：看不到月曆所以挑不出「上週三到這週五」這種相對關係、兩個欄位彼此無關、
 * 而且一個月要按幾十次上下鍵。做法與 element-plus 的 datetimerange 同形。
 *
 * ⚠ 刻意**不放**「最近 7 天」那類快捷。左邊那格「時間範圍」下拉已經是相對區間了，
 *   這裡再放一份語意近乎相同、行為卻不同（絕對 vs 相對）的按鈕，只會讓人不知道
 *   兩者差在哪。這個面板的職責就是「指定一段明確的區間」。
 *
 * ⚠ 舊版有一整段在講「不能靠重新產生 HTML 塗選取狀態」（滑過日期時重建 innerHTML，
 *   游標底下的按鈕被換掉，mousedown 與 mouseup 落在不同節點，瀏覽器**不會**產生
 *   click）。Vue 這一版天生沒有那個問題：節點靠 key 復用，改的只是 class 與屬性。
 *   舊版另外寫進 `data-edge` / `data-in` 的用意（class 給 CSS、屬性給自動化測試）照舊。
 */
import { computed, onBeforeUnmount, onMounted, ref, useTemplateRef } from "vue";

import { anchorPanel } from "@/lib/anchor";
import {
  RP_DOW,
  rpAddMonths,
  rpCells,
  rpClampView,
  rpFuture,
  rpHm,
  rpIso,
  rpMaxView,
  rpMonth,
  rpParse,
  rpSameDay,
  rpYmd,
  rpDay,
} from "@/lib/range";

export interface RangeValue {
  from: string;
  to: string;
}

const props = defineProps<{ modelValue: RangeValue }>();
const emit = defineEmits<{
  "update:modelValue": [value: RangeValue];
  change: [value: RangeValue];
}>();

const mount = useTemplateRef<HTMLElement>("mount");
const trigger = useTemplateRef<HTMLButtonElement>("trigger");
const panel = useTemplateRef<HTMLElement>("panel");

// committed（已經送出去查詢的）從 props 來；draft 是面板裡正在編輯的值。
// 分開才做得到「按確定才生效」，也才能在取消展開時把改到一半的東西丟掉。
const dFrom = ref<Date | null>(null);
const dTo = ref<Date | null>(null);
const view = ref<Date>(rpMonth(new Date()));
const picking = ref<"from" | "to">("from");
const hover = ref<Date | null>(null);
const open = ref(false);
const drop = ref<"up" | "down">("down");

const committedFrom = computed(() => rpParse(props.modelValue.from));
const committedTo = computed(() => rpParse(props.modelValue.to));

const triggerLabel = computed(() => {
  const from = committedFrom.value;
  const to = committedTo.value;
  if (!from && !to) return "點此指定區間";
  return `${from ? `${rpYmd(from)} ${rpHm(from)}` : "不限"} → ${to ? `${rpYmd(to)} ${rpHm(to)}` : "不限"}`;
});
const triggerEmpty = computed(() => (committedFrom.value || committedTo.value ? 0 : 1));

/** 目前要拿來畫「範圍」的兩端：終點還沒定時用游標懸停的那天預覽。 */
const spanEnds = computed<[Date | null, Date | null]>(() => {
  const a = dFrom.value;
  const b = dTo.value ?? (picking.value === "to" ? hover.value : null);
  if (!a || !b) return [null, null];
  return a <= b ? [a, b] : [b, a];
});

const hint = computed(() => {
  if (!dFrom.value && !dTo.value) return "點一下選起點，再點一下選終點";
  if (picking.value === "to" && !dTo.value) return "再點一下選終點（選反了會自動對調）";
  return "可再點一下重新選，或直接改上方的日期與時間";
});

const today = rpYmd(new Date());
const atMax = computed(() => rpMonth(view.value).getTime() >= rpMaxView().getTime());

interface Cell {
  key: string;
  ymd: string;
  day: number;
  other: boolean;
  disabled: boolean;
  isToday: boolean;
  edge: boolean;
  inside: boolean;
}

function cellsOf(v: Date): Cell[] {
  const [lo, hi] = spanEnds.value;
  const now = new Date();
  return rpCells(v).map((d) => {
    const edge = rpSameDay(d, dFrom.value) || rpSameDay(d, dTo.value);
    const inside = !edge && !!(lo && hi) && rpDay(d) > rpDay(lo) && rpDay(d) < rpDay(hi);
    return {
      key: `${v.getFullYear()}-${v.getMonth()}-${rpYmd(d)}`,
      ymd: rpYmd(d),
      day: d.getDate(),
      other: d.getMonth() !== v.getMonth(),
      disabled: rpFuture(d),
      isToday: rpSameDay(d, now),
      edge,
      inside,
    };
  });
}

const leftCells = computed(() => cellsOf(view.value));
const rightCells = computed(() => cellsOf(rpAddMonths(view.value, 1)));
const rightView = computed(() => rpAddMonths(view.value, 1));

const editFrom = computed(() => ({
  date: dFrom.value ? rpYmd(dFrom.value) : "",
  time: dFrom.value ? rpHm(dFrom.value) : "",
}));
const editTo = computed(() => ({
  date: dTo.value ? rpYmd(dTo.value) : "",
  time: dTo.value ? rpHm(dTo.value) : "",
}));

/* 點某一天。時間部分沿用已經設過的；沒設過就給「整天」的兩端——挑 7/19 到 7/26 時
   多數人要的是那兩天的全部，而不是 00:00 到 00:00（會少掉最後一天）。 */
function pickDay(ymd: string): void {
  const [y, m, d] = ymd.split("-").map(Number);
  const keep = (base: Date | null, hh: number, mm: number): Date =>
    new Date(y, m - 1, d, base ? base.getHours() : hh, base ? base.getMinutes() : mm, 0, 0);
  if (picking.value === "from" || (dFrom.value && dTo.value)) {
    dFrom.value = keep(dFrom.value, 0, 0);
    dTo.value = null;
    picking.value = "to";
  } else {
    dTo.value = keep(dTo.value, 23, 59);
    // 選反了就對調，不要丟掉他的第二次點擊、也不要跳錯誤訊息
    if (dFrom.value && dTo.value < dFrom.value) {
      const a = dFrom.value;
      dFrom.value = new Date(
        dTo.value.getFullYear(),
        dTo.value.getMonth(),
        dTo.value.getDate(),
        0,
        0,
        0,
        0,
      );
      dTo.value = new Date(a.getFullYear(), a.getMonth(), a.getDate(), 23, 59, 0, 0);
    }
    picking.value = "from";
  }
  hover.value = null;
}

/** 上方輸入框改動：日期與時間分開兩欄，任一欄改了都要組回同一個 Date。 */
function editField(which: "from" | "to", kind: "date" | "time", value: string): void {
  const cur = which === "from" ? dFrom.value : dTo.value;
  let next: Date | null = null;
  if (kind === "date") {
    if (value) {
      const [y, m, d] = value.split("-").map(Number);
      next = new Date(
        y,
        m - 1,
        d,
        cur ? cur.getHours() : which === "from" ? 0 : 23,
        cur ? cur.getMinutes() : which === "from" ? 0 : 59,
        0,
        0,
      );
    }
  } else {
    // 只有時間、沒有日期時無從組出一個時刻——先挑日期再說，不要自作主張補今天
    if (!cur || !value) return;
    const [hh, mm] = value.split(":").map(Number);
    next = new Date(cur.getFullYear(), cur.getMonth(), cur.getDate(), hh, mm, 0, 0);
  }
  // max 屬性只擋得住原生的日期選擇器，直接打字仍然送得進未來的日期
  if (next && rpFuture(next)) next = new Date();
  if (which === "from") dFrom.value = next;
  else dTo.value = next;
  picking.value = dFrom.value && !dTo.value ? "to" : "from";
  if (next) view.value = rpClampView(rpMonth(next));
}

const place = (): void => {
  if (!trigger.value || !panel.value) return;
  anchorPanel(trigger.value, panel.value, { mount: mount.value });
  drop.value = (mount.value?.dataset.drop as "up" | "down") ?? "down";
};

// 與 picker 同一套：捲動時**重新定位而不是關掉**（理由見 SitePicker.onScroll）
function onScroll(e: Event): void {
  if (panel.value?.contains(e.target as Node)) return;
  const r = trigger.value?.getBoundingClientRect();
  if (!r) return;
  if (r.bottom < 0 || r.top > globalThis.innerHeight) close();
  else place();
}

function openPanel(): void {
  if (mount.value?.closest('[data-disabled="1"]')) return;
  open.value = true;
  dFrom.value = committedFrom.value;
  dTo.value = committedTo.value;
  picking.value = "from";
  hover.value = null;
  // 展開時對齊到已選區間的月份；沒選過就讓「本月」落在右邊那一格
  view.value = rpClampView(rpMonth(committedFrom.value ?? rpAddMonths(new Date(), -1)));
  void Promise.resolve().then(place);
  globalThis.addEventListener("scroll", onScroll, { capture: true });
  globalThis.addEventListener("resize", close);
}

function close(): void {
  open.value = false;
  globalThis.removeEventListener("scroll", onScroll, { capture: true });
  globalThis.removeEventListener("resize", close);
}

const currentValue = (): RangeValue => ({
  from: dFrom.value ? rpIso(dFrom.value) : "",
  to: dTo.value ? rpIso(dTo.value) : "",
});

function commit(): void {
  const v = currentValue();
  close();
  emit("update:modelValue", v);
  emit("change", v);
}

function clear(): void {
  dFrom.value = null;
  dTo.value = null;
  picking.value = "from";
  commit();
}

function moveView(delta: number): void {
  view.value = rpClampView(rpAddMonths(view.value, delta));
}

/** 懸停預覽：只在「已定起點、還沒定終點」時有意義。 */
function onHover(ymd: string): void {
  if (picking.value !== "to" || !dFrom.value) return;
  const [y, m, d] = ymd.split("-").map(Number);
  const next = new Date(y, m - 1, d);
  if (rpSameDay(next, hover.value)) return;
  hover.value = next;
}

/* 點面板外面就收起來。
 * ⚠ 舊版特別註明**必須用捕獲階段**，因為冒泡時面板裡被點的節點已經被 innerHTML 重建掉，
 *   `contains()` 於是回 false、面板每點一天就自己關一次。Vue 版沒有那次重建，但捕獲
 *   階段一樣正確、而且對「日後有人加回重建」是免疫的，所以照舊。 */
function onDocClick(e: MouseEvent): void {
  if (open.value && !mount.value?.contains(e.target as Node)) close();
}

function onTriggerKeydown(e: KeyboardEvent): void {
  if (["Enter", " ", "ArrowDown"].includes(e.key) && !open.value) {
    e.preventDefault();
    openPanel();
  }
}

function onPanelKeydown(e: KeyboardEvent): void {
  if (e.key === "Escape") {
    e.preventDefault();
    close();
    trigger.value?.focus();
  }
}

onMounted(() => document.addEventListener("click", onDocClick, true));
onBeforeUnmount(() => {
  document.removeEventListener("click", onDocClick, true);
  close();
});
</script>

<template>
  <div
    ref="mount"
    id="pick-range"
    class="picker rangepick"
    data-testid="pick-range"
    :data-drop="drop"
  >
    <button
      ref="trigger"
      type="button"
      class="picker__button rangepick__trigger"
      data-testid="range-trigger"
      aria-haspopup="dialog"
      :aria-expanded="open ? 'true' : 'false'"
      @click="open ? close() : openPanel()"
      @keydown="onTriggerKeydown"
    >
      <i class="picker__icon fa-solid fa-calendar-days"></i>
      <span class="rangepick__value" :data-empty="triggerEmpty">{{ triggerLabel }}</span>
      <i class="picker__caret fa-solid fa-chevron-down"></i>
    </button>
    <div
      ref="panel"
      class="rangepick__panel"
      role="dialog"
      aria-label="選擇時間範圍"
      data-testid="range-panel"
      :hidden="!open"
      @keydown="onPanelKeydown"
    >
      <template v-if="open">
        <div class="rangepick__heads">
          <label class="rangepick__head">
            <input
              class="input input--sm"
              type="date"
              data-edit="from-date"
              :max="today"
              data-testid="range-from-date"
              :value="editFrom.date"
              @change="editField('from', 'date', ($event.target as HTMLInputElement).value)"
            />
            <input
              class="input input--sm"
              type="time"
              data-edit="from-time"
              data-testid="range-from-time"
              :value="editFrom.time"
              @change="editField('from', 'time', ($event.target as HTMLInputElement).value)"
            />
          </label>
          <i class="rangepick__arrow fa-solid fa-arrow-right-long" aria-hidden="true"></i>
          <label class="rangepick__head">
            <input
              class="input input--sm"
              type="date"
              data-edit="to-date"
              :max="today"
              data-testid="range-to-date"
              :value="editTo.date"
              @change="editField('to', 'date', ($event.target as HTMLInputElement).value)"
            />
            <input
              class="input input--sm"
              type="time"
              data-edit="to-time"
              data-testid="range-to-time"
              :value="editTo.time"
              @change="editField('to', 'time', ($event.target as HTMLInputElement).value)"
            />
          </label>
        </div>
        <div class="rangepick__cals">
          <!-- 上一月/下一月只放在對應的那一側：兩個月曆是連動的（右邊永遠是左邊 +1），
               兩側都放前後鍵會讓人以為可以各自獨立翻。 -->
          <div class="rangepick__cal" data-testid="range-cal">
            <div class="rangepick__calhead">
              <span class="rangepick__navs">
                <button
                  type="button"
                  class="rangepick__nav"
                  aria-label="上一年"
                  data-testid="range-prev-year"
                  @click="moveView(-12)"
                >
                  <i class="fa-solid fa-angles-left"></i>
                </button>
                <button
                  type="button"
                  class="rangepick__nav"
                  aria-label="上個月"
                  data-testid="range-prev-month"
                  @click="moveView(-1)"
                >
                  <i class="fa-solid fa-angle-left"></i>
                </button>
              </span>
              <span class="rangepick__month">
                {{ view.getFullYear() }} 年 {{ view.getMonth() + 1 }} 月
              </span>
              <span class="rangepick__navs"></span>
            </div>
            <div class="rangepick__dow">
              <span v-for="d in RP_DOW" :key="d">{{ d }}</span>
            </div>
            <div class="rangepick__grid">
              <button
                v-for="c in leftCells"
                :key="c.key"
                type="button"
                class="rangepick__day"
                :class="{
                  'is-other': c.other,
                  'is-disabled': c.disabled,
                  'is-today': c.isToday,
                  'is-edge': c.edge,
                  'is-in': c.inside,
                }"
                :data-day="c.ymd"
                :disabled="c.disabled"
                :data-testid="c.other ? 'range-day-other' : 'range-day'"
                :data-edge="String(c.edge)"
                :data-in="String(c.inside)"
                tabindex="-1"
                @click="pickDay(c.ymd)"
                @mouseover="onHover(c.ymd)"
              >
                {{ c.day }}
              </button>
            </div>
          </div>
          <div class="rangepick__cal" data-testid="range-cal">
            <div class="rangepick__calhead">
              <span class="rangepick__navs"></span>
              <span class="rangepick__month">
                {{ rightView.getFullYear() }} 年 {{ rightView.getMonth() + 1 }} 月
              </span>
              <span class="rangepick__navs">
                <button
                  type="button"
                  class="rangepick__nav"
                  aria-label="下個月"
                  data-testid="range-next-month"
                  :disabled="atMax"
                  @click="moveView(1)"
                >
                  <i class="fa-solid fa-angle-right"></i>
                </button>
                <button
                  type="button"
                  class="rangepick__nav"
                  aria-label="下一年"
                  data-testid="range-next-year"
                  :disabled="atMax"
                  @click="moveView(12)"
                >
                  <i class="fa-solid fa-angles-right"></i>
                </button>
              </span>
            </div>
            <div class="rangepick__dow">
              <span v-for="d in RP_DOW" :key="d">{{ d }}</span>
            </div>
            <div class="rangepick__grid">
              <button
                v-for="c in rightCells"
                :key="c.key"
                type="button"
                class="rangepick__day"
                :class="{
                  'is-other': c.other,
                  'is-disabled': c.disabled,
                  'is-today': c.isToday,
                  'is-edge': c.edge,
                  'is-in': c.inside,
                }"
                :data-day="c.ymd"
                :disabled="c.disabled"
                :data-testid="c.other ? 'range-day-other' : 'range-day'"
                :data-edge="String(c.edge)"
                :data-in="String(c.inside)"
                tabindex="-1"
                @click="pickDay(c.ymd)"
                @mouseover="onHover(c.ymd)"
              >
                {{ c.day }}
              </button>
            </div>
          </div>
        </div>
        <div class="rangepick__foot">
          <span class="rangepick__hint">{{ hint }}</span>
          <button
            type="button"
            class="btn"
            data-act="clear"
            data-testid="range-clear"
            @click="clear"
          >
            <i class="fa-solid fa-eraser"></i> 清除
          </button>
          <button
            type="button"
            class="btn btn--primary"
            data-act="ok"
            data-testid="range-ok"
            @click="commit"
          >
            <i class="fa-solid fa-check"></i> 確定
          </button>
        </div>
      </template>
    </div>
  </div>
</template>
