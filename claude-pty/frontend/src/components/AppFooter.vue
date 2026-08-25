<script setup lang="ts">
/*
 * 頁尾：線上跑的到底是哪一版。三個頁面（登入 / session / 帳號）都有——「我看到的是不是
 * 最新的」在哪一頁都會問。
 *
 * ⚠ 值一律由 server 端算好（舊版是 `build_info()`，見 server/version.py）。問不到就留白
 *   並在 tooltip 講原因：猜一個看起來合理的值比空白糟得多，空白會讓人去查、錯的值會讓人
 *   停止查。
 * 值來自 `/api/bootstrap`（公開，登入頁也要）。拿不到就留白——那正是上面那句話的兌現。
 * ⚠ 相對時間由前端補（伺服端跑在 UTC，排出來的時間不屬於任何人）。
 */
import { computed } from "vue";

import { absTime, relTime } from "@/lib/time";
import { useSiteStore } from "@/stores/site";

const store = useSiteStore();

const modules = computed(() => store.meta.buildModules);
/* 建置時間單獨一行：它是**整包**的屬性，不屬於任何一個模組。
   ⚠ 讀 `build.built_at` 而不是 `modules[0].built_at`——`/api/bootstrap` 特地把它提到最外層，
     docstring 也寫明理由：留在列裡的話遲早會有人把它畫成「claude-pty 這一列的時間」。 */
const built = computed(() => store.meta.buildBuiltAt);
</script>

<template>
  <footer class="footer" data-testid="footer">
    <div class="footer__row">
      <span
        v-for="(m, i) in modules"
        :key="m.name"
        class="footer__mod tip"
        :class="{ 'tip--left': i === 0 }"
        data-testid="footer-mod"
        :data-tip="m.detail"
      >
        <span class="footer__name">{{ m.name }}</span>
        <span v-if="m.version" class="footer__ver">{{ m.version }}</span>
        <span v-else class="footer__unknown">版本未知</span>
        <code v-if="m.commit" class="footer__sha">{{ m.commit }}</code>
        <span v-else-if="i === 0" class="footer__unknown">commit 未知</span>
      </span>
    </div>
    <div v-if="built" class="footer__built">
      建置於 <time class="footer__at" :datetime="built">{{ absTime(built) }}</time>
      <span class="footer__rel" :data-for="built">（{{ relTime(built) }}）</span>
    </div>
  </footer>
</template>
