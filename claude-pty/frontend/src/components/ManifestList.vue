<script setup lang="ts">
/*
 * 清單本體。兩張表（執行中／已結束）共用這個元件，因為它們是**同一份清單的兩種篩選**，
 * 不是兩個功能。
 *
 * ⚠ 表頭與資料列共用 `.manifest__row` 的格線定義，欄位才會對齊——另外寫一份
 *   grid-template-columns 遲早會與資料列漂移（欄位一改就得記得改兩個地方）。
 * ⚠ 歷史紀錄是唯讀的：容器早就不在了，沒有終端可開、也沒有東西好改名。
 */
import { computed, useTemplateRef } from "vue";

import { useClipTips } from "@/composables/useClipTips";
import {
  END_REASON,
  freshness,
  liveState,
  SLOW_BOOT_SECONDS,
  type SessionRow,
} from "@/lib/sessions";
import { span } from "@/lib/time";

import MetricTime from "./MetricTime.vue";
import SessionChips from "./SessionChips.vue";

const props = defineProps<{
  rows: SessionRow[];
  offset: number;
  historical: boolean;
  isAdmin: boolean;
  gitlabEnabled: boolean;
  /** 讀取失敗時直接把原因畫在清單區（舊版是 `讀取失敗：<訊息>`）。 */
  error?: string | null;
  loading?: boolean;
  swapping?: boolean;
}>();

const emit = defineEmits<{
  rename: [row: SessionRow];
  open: [row: SessionRow, event: MouseEvent];
  kill: [row: SessionRow];
}>();

const root = useTemplateRef<HTMLElement>("root");
useClipTips(root);

/* 執行中的「已跑」以此刻為終點；`nowIso` 在每次重繪取一次，同一批列用同一個時間點，
   不會出現「第一列 3 分 01 秒、最後一列 3 分 02 秒」這種抖動。 */
const nowIso = computed(() => {
  void props.rows; // rows 一換就重新取一次
  return new Date().toISOString();
});

const chipCtx = computed(() => ({
  isAdmin: props.isAdmin,
  gitlabEnabled: props.gitlabEnabled,
  historical: props.historical,
}));

const seq = (i: number): string => String(props.offset + i + 1).padStart(2, "0");

const titleOf = (s: SessionRow): string => s.display_name || s.id;
const titleClass = (s: SessionRow): string =>
  s.display_name ? "manifest__id" : "manifest__id mono-id";

interface Boot {
  took: string | null;
  slow: boolean;
}

function boot(s: SessionRow): Boot {
  const took = span(s.created_at, s.ready_at);
  if (!took) return { took: null, slow: false };
  const sec = (new Date(s.ready_at as string).getTime() - new Date(s.created_at).getTime()) / 1000;
  return { took, slow: sec > SLOW_BOOT_SECONDS };
}

const emptyText = computed(() =>
  props.historical ? "還沒有結束的 Session。" : "目前沒有 Session。用上面的表單開一個。",
);
</script>

<template>
  <div
    ref="root"
    class="manifest"
    id="manifest"
    data-testid="manifest"
    :data-swapping="swapping ? '1' : undefined"
  >
    <div v-if="error" class="empty">讀取失敗：{{ error }}</div>
    <div v-else-if="loading" class="empty" data-testid="manifest-empty">載入中…</div>
    <div v-else-if="!rows.length" class="empty" data-testid="manifest-empty">{{ emptyText }}</div>
    <template v-else-if="historical">
      <div
        class="manifest__row manifest__row--past manifest__row--head"
        data-testid="manifest-head"
        aria-hidden="true"
      >
        <div>#</div>
        <div>名稱 / Container</div>
        <div>結束原因</div>
        <div>設定</div>
        <div>耗時</div>
        <div>時間</div>
      </div>
      <article
        v-for="(s, idx) in rows"
        :key="s.id"
        class="manifest__row manifest__row--past"
        data-testid="session-row"
        :style="{ animationDelay: `${idx * 40}ms` }"
      >
        <div class="manifest__index">
          <span class="lamp" data-testid="session-lamp" data-state="exited"></span>
          <span>{{ seq(idx) }}</span>
        </div>
        <div>
          <div class="manifest__name">
            <span :class="titleClass(s)" data-testid="session-title">
              <span class="manifest__id-text" data-testid="session-title-text">{{
                titleOf(s)
              }}</span>
            </span>
          </div>
          <div class="manifest__meta tip" :data-tip="s.container || ''">
            <span class="manifest__meta-text">{{ s.container || "" }}</span>
          </div>
        </div>
        <div class="manifest__status" data-testid="session-status" data-state="ended">
          <i class="fa-solid fa-clock-rotate-left"></i
          >{{ END_REASON[s.ended_reason ?? ""] || s.ended_reason || "" }}
        </div>
        <SessionChips :session="s" :ctx="chipCtx" />
        <div class="manifest__metrics">
          <span class="metric__label">啟動</span>
          <span v-if="!boot(s).took" class="metric__none">—</span>
          <span
            v-else
            class="metric"
            :class="{ 'metric--slow': boot(s).slow }"
            :data-tip="boot(s).slow ? '偏慢；多半是 trivy DB 沒命中快取' : undefined"
            >{{ boot(s).took }}</span
          >
          <span class="metric__label">時長</span>
          <span class="metric">{{ span(s.created_at, s.ended_at) || "—" }}</span>
        </div>
        <div class="manifest__metrics">
          <span class="metric__label">建立</span>
          <MetricTime :iso="s.created_at" />
          <span class="metric__label">結束</span>
          <MetricTime :iso="s.ended_at" />
        </div>
      </article>
    </template>
    <template v-else>
      <div class="manifest__row manifest__row--head" data-testid="manifest-head" aria-hidden="true">
        <div>#</div>
        <div>名稱 / Container</div>
        <div>狀態</div>
        <div>設定</div>
        <div>耗時</div>
        <div>時間</div>
        <div>操作</div>
      </div>
      <article
        v-for="(s, idx) in rows"
        :key="s.id"
        class="manifest__row"
        data-testid="session-row"
        :style="{ animationDelay: `${idx * 40}ms` }"
      >
        <div class="manifest__index">
          <span
            class="lamp"
            data-testid="session-lamp"
            :data-state="liveState(s).lamp"
            :title="liveState(s).lampTitle"
          ></span>
          <span>{{ seq(idx) }}</span>
        </div>
        <div>
          <div class="manifest__name">
            <!-- 有取名字就以名字為主標題；沒取名就沿用 sid（等寬字體）。
                 次要行放 container 名稱——可直接複製去 docker exec/logs。 -->
            <span :class="titleClass(s)" data-testid="session-title">
              <span class="manifest__id-text" data-testid="session-title-text">{{
                titleOf(s)
              }}</span>
            </span>
            <button
              class="icon-btn tip"
              data-act="rename"
              :data-id="s.id"
              :data-name="s.display_name || ''"
              data-tip="重新命名"
              aria-label="重新命名"
              @click="emit('rename', s)"
            >
              <i class="fa-solid fa-pen"></i>
            </button>
          </div>
          <div class="manifest__meta tip" :data-tip="s.container || ''">
            <span class="manifest__meta-text">{{ s.container || "" }}</span>
          </div>
        </div>
        <div
          class="manifest__status"
          data-testid="session-status"
          :data-state="liveState(s).state[0]"
        >
          <span class="manifest__state"
            ><i :class="liveState(s).state[2]"></i>{{ liveState(s).state[1] }}</span
          >
          <!-- 這一列的狀態是**幾點跟 dockerd 求證來的**（ADR 0012）。列表本身完全不打
               docker，所以「看起來是即時的」會是謊。舊了還會自己標紅。 -->
          <span
            class="manifest__checked tip tip--right"
            :data-stale="freshness(s.state_checked_at, nowIso).stale"
            :data-testid="`checked-${s.id}`"
            :data-tip="freshness(s.state_checked_at, nowIso).tip"
            >{{ freshness(s.state_checked_at, nowIso).text }}</span
          >
        </div>
        <SessionChips :session="s" :ctx="chipCtx" />
        <div class="manifest__metrics">
          <span class="metric__label">啟動</span>
          <span v-if="!boot(s).took" class="metric__none">—</span>
          <span
            v-else
            class="metric"
            :class="{ 'metric--slow': boot(s).slow }"
            :data-tip="boot(s).slow ? '偏慢；多半是 trivy DB 沒命中快取' : undefined"
            >{{ boot(s).took }}</span
          >
          <span class="metric__label">已跑</span>
          <span class="metric">{{ span(s.created_at, nowIso) || "—" }}</span>
        </div>
        <div class="manifest__metrics">
          <span class="metric__label">建立</span>
          <MetricTime :iso="s.created_at" />
          <span
            class="metric__label tip"
            data-tip="最後一次有人對它操作（開終端／改尺寸）。Claude 自己在跑不會更新這個時間"
            >操作</span
          >
          <MetricTime :iso="s.last_active_at" />
        </div>
        <div class="manifest__actions">
          <button
            class="btn"
            data-act="open"
            :data-id="s.id"
            :data-label="titleOf(s)"
            :data-testid="`row-open-${s.id}`"
            title="開啟終端（按住 ⌘/Ctrl 改開新分頁）"
            @click="emit('open', s, $event)"
          >
            <i class="fa-solid fa-terminal"></i> 終端
          </button>
          <button
            class="btn btn--danger"
            data-act="kill"
            :data-id="s.id"
            :data-label="titleOf(s)"
            :data-container="s.container || ''"
            @click="emit('kill', s)"
          >
            <i class="fa-solid fa-circle-stop"></i> 終止
          </button>
        </div>
      </article>
    </template>
  </div>
</template>
