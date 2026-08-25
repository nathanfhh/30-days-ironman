<script setup lang="ts">
/* ── 終端抽屜：**階段 4 前半只有殼** ───────────────────────────────────────────
 *
 * 這個檔案現在只做兩件事：把介面（props / emits）釘下來，以及在被打開時**明確講出
 * 它還沒接上**——而不是給一個看起來像終端、實際上什麼都不會發生的畫面。
 *
 * 後半要搬進來的東西（都在舊版 `app.js` 的 `terminalDrawer` 裡，每一段都帶著實測理由）：
 *   · iframe 掛 `/session/<sid>/`（**不是** POST /view 回的 direct_url——那是另一個
 *     origin，會被本站 CSP 擋掉）。
 *   · `useTerminalSize` composable：ResizeObserver 掛 `.drawer__frame` 驅動 fit，
 *     `POST /resize` 只在尺寸穩定且抽屜動畫結束後送最後一次帶 redraw（階段 1.5 的成果）。
 *   · 字級（localStorage）、貼圖上傳、提示輪播、`.shell` 設 inert、關閉時焦點歸位。
 *
 * ⚠ 目前實際上打不開：`behindProxy` 還沒有 API 可問（見 stores/site 的 TODO(階段 3)），
 *   預設 false，而 false 這條路本來就是「開新分頁」——與舊版直連模式的行為一致。
 */
defineProps<{
  sid: string;
  label: string;
  path: string;
  flavor?: string | null;
}>();

const emit = defineEmits<{ close: [] }>();
</script>

<template>
  <Teleport to="body">
    <div class="drawer" data-testid="drawer" :data-sid="sid" data-open="1">
      <div class="drawer__scrim" data-act="scrim" @click="emit('close')"></div>
      <section
        class="drawer__panel"
        role="dialog"
        aria-modal="true"
        :aria-label="`終端：${label || sid}`"
      >
        <header class="drawer__bar">
          <div class="drawer__id">
            <i class="fa-solid fa-terminal" aria-hidden="true"></i>
            <span class="drawer__title">{{ label || sid }}</span>
            <code class="drawer__sid">{{ sid }}</code>
          </div>
          <div class="drawer__tools">
            <button
              class="icon-btn"
              data-act="close"
              data-testid="drawer-close"
              aria-label="關閉終端"
              title="關閉"
              @click="emit('close')"
            >
              <i class="fa-solid fa-xmark"></i>
            </button>
          </div>
        </header>
        <div class="drawer__body">
          <!-- prettier-ignore -->
          <p class="drawer__pending" data-testid="drawer-pending">
            終端抽屜還沒搬到這一版（階段 4 後半）。這一場的終端請用「新分頁」開：{{ path }}</p>
        </div>
      </section>
    </div>
  </Teleport>
</template>
