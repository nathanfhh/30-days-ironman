<script setup lang="ts">
/* 一列的 chip 群：擁有者 / CLI → 網路 · 錄製 · telemetry → 模型 / 思考深度。
 * 順序與判斷全在 `lib/sessions.ts` 的 `chipsOf`（那裡有每一顆的理由）。
 *
 * ⚠ 文字要自己包一層 span。chip 是 inline-flex，裸的文字節點會落進匿名 flex item，
 *   而 `text-overflow: ellipsis` 對它無效——原本 CSS 寫了 ellipsis 卻只看到硬切，
 *   就是這個原因（長模型名會被切掉尾巴）。
 */
import { computed } from "vue";

import { chipsOf, type ChipContext, type SessionRow } from "@/lib/sessions";

import BrandMark from "./BrandMark.vue";

const props = defineProps<{ session: SessionRow; ctx: ChipContext }>();

const chips = computed(() => chipsOf(props.session, props.ctx));
</script>

<template>
  <div class="chips manifest__chips-cell" data-testid="chips-cell">
    <span
      v-for="c in chips.lead"
      :key="`lead-${c.tone}-${c.text}`"
      class="chip"
      data-testid="chip"
      :data-tone="c.tone || undefined"
    >
      <BrandMark v-if="c.brand" :name="c.brand" cls="chip__logo" />
      <i v-else-if="c.icon" :class="c.icon"></i>
      <span class="chip__text" data-testid="chip-text">{{ c.text }}</span>
    </span>
    <span
      v-for="m in chips.marks"
      :key="`mark-${m.kind}`"
      class="chip chip--mark tip"
      data-testid="chip-mark"
      :data-kind="m.kind"
      :data-tone="m.tone"
      :data-tip="m.title"
      :aria-label="m.title"
    >
      <i :class="m.icon"></i>
    </span>
    <span
      v-for="c in chips.tail"
      :key="`tail-${c.tone}-${c.text}`"
      class="chip"
      data-testid="chip"
      :data-tone="c.tone || undefined"
    >
      <i v-if="c.icon" :class="c.icon"></i>
      <span class="chip__text" data-testid="chip-text">{{ c.text }}</span>
    </span>
  </div>
</template>
