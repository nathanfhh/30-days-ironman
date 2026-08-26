<script setup lang="ts">
/*
 * 頁尾：線上跑的到底是哪一版。**登入之後**的每一頁都有：「我看到的是不是最新的」在哪
 * 一頁都會問。
 *
 * ⚠ **登入頁沒有頁尾**（2026-08-26 裁示 L4：版號與主機路徑登入前不得取得）。值來自
 *   `/api/account/bootstrap`，那條要登入；未登入時 `buildModules` 是空陣列，這裡整段
 *   不畫。
 *
 *   為什麼是「整段不畫」而不是「留一條只有品牌的頁尾」：legacy 的頁尾裡**只有**這些版本
 *   膠囊，沒有品牌、沒有其他內容（見 `server/templates/base.html`，2026-08-26 刪）。
 *   擺一個品牌上去是**新增**一個舊版沒有的東西；而只留空殼也不行：`.footer` 有
 *   `border-top`，空的話登入表單下方會浮出一條沒有內容的橫線，那同樣是舊版沒有的樣子。
 *   兩個選項裡「不畫」才是**沒有多出東西**的那一個。
 *
 * ⚠ 值一律由 server 端算好（見 server/version.py）。**問得到但答不出來**時仍然留白並在
 *   tooltip 講原因（`版本未知` / `commit 未知`）：猜一個看起來合理的值比空白糟得多，
 *   空白會讓人去查、錯的值會讓人停止查。那與「未登入所以不給」是兩件事：後者連頁尾
 *   都沒有，不會有人以為系統答不出自己的版本。
 * ⚠ 相對時間由前端補（伺服端跑在 UTC，排出來的時間不屬於任何人）。
 */
import { computed } from "vue";

import { absTime, relTime } from "@/lib/time";
import { useSiteStore } from "@/stores/site";

const store = useSiteStore();

const modules = computed(() => store.meta.buildModules);
/* 建置時間單獨一行：它是**整包**的屬性，不屬於任何一個模組。
   ⚠ 讀 `build.built_at` 而不是 `modules[0].built_at`：`/api/account/bootstrap` 特地把它提到
     最外層，docstring 也寫明理由：留在列裡的話遲早會有人把它畫成「claude-pty 這一列的時間」。 */
const built = computed(() => store.meta.buildBuiltAt);
/* 有沒有東西可畫。⚠ 兩個都看：`built` 是可以單獨缺席的（build arg 沒給就是 null），
   那時仍然要畫出那一排膠囊。只有兩個都沒有才代表「這一頁不給」。 */
const hasBuild = computed(() => modules.value.length > 0 || Boolean(built.value));
</script>

<template>
  <footer v-if="hasBuild" class="footer" data-testid="footer">
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
      <!-- ⚠ `</time>` 與 `<span>` 之間那一個空白是**有意義的**：舊版模板兩者之間是換行，
           渲染出來就是一個空白，整行因此寬 215px。Vue 的 `whitespace: 'condense'` 會把
           「只有空白＋換行」的文字節點整個摺掉，少了它整行變成 211px——十五場截圖全紅，
           而且紅的位置在頁尾、看起來像是別的東西壞了。用 `{{ " " }}` 明寫，它不會被摺。 -->
      建置於 <time class="footer__at" :datetime="built">{{ absTime(built) }}</time
      >{{ " " }}<span class="footer__rel" :data-for="built">（{{ relTime(built) }}）</span>
    </div>
  </footer>
</template>
