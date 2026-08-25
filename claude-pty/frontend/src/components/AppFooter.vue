<script setup lang="ts">
/*
 * 頁尾：線上跑的到底是哪一版。三個頁面（登入 / session / 帳號）都有——「我看到的是不是
 * 最新的」在哪一頁都會問。
 *
 * ⚠ 值一律由 server 端算好（舊版是 `build_info()`，見 server/version.py）。問不到就留白
 *   並在 tooltip 講原因：猜一個看起來合理的值比空白糟得多，空白會讓人去查、錯的值會讓人
 *   停止查。
 * ⚠ TODO(階段 3)：`build_info()` 目前**只有 Jinja 拿得到**，SPA 沒有對應端點，所以這一列
 *   現在是空的。這正是「空白會讓人去查」的情況，而且它是設計上的暫時狀態，不是意外——
 *   端點一上線，`stores/site` 的 `loadMeta()` 填 `buildModules` 即可，這個元件不必改。
 * ⚠ 相對時間由前端補（伺服端跑在 UTC，排出來的時間不屬於任何人）。
 */
import { computed } from "vue";

import { absTime, relTime } from "@/lib/time";
import { useSiteStore } from "@/stores/site";

const store = useSiteStore();

const modules = computed(() => store.meta.buildModules);
// 建置時間單獨一行：它是整包的屬性，不屬於任何一個模組。
const built = computed(() => modules.value[0]?.built_at ?? null);
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
