<script setup lang="ts">
/** 列表裡的時間欄：顯示「多久以前」，hover/focus 給出原始時刻。
 *
 * 相對時間掃一眼就懂，但要對照 log、要跟別人講「就是那個時間點」時它沒有用；
 * 兩者不必二選一——把精確值收進 tooltip 就好。列表上每一個時間欄都該用這個。
 * ⚠ `tip--right`：這些欄位靠列的右半邊，置中的 tooltip 會頂出視窗右緣。
 * ⚠ **不加 tabindex**。一列有 2–3 個時間欄，一頁 20 列就是多 40–60 個 tab 停留點，
 *   鍵盤使用者要一路按過去才到得了「開啟 / 終止」。這些是純資訊、不可操作的欄位。
 */
import { absTime, relTime } from "@/lib/time";

withDefaults(defineProps<{ iso?: string | null; cls?: string }>(), { iso: null, cls: "metric" });
</script>

<template>
  <span v-if="!iso" :class="[cls, 'metric__none']">—</span>
  <span v-else :class="[cls, 'tip', 'tip--right']" :data-tip="absTime(iso)">{{
    relTime(iso)
  }}</span>
</template>
