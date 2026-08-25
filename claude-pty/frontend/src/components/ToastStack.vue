<script setup lang="ts">
/*
 * toast 的 DOM。掛在 <body> 底下（Teleport）——與抽屜同層而 z-index 較高，所以蓋得住
 * 抽屜；抽屜把 `.shell` 設成 inert，但 toast 不在 `.shell` 裡，仍然點得到
 * （e2e_drawer 有一條在守這件事）。
 *
 * 倒數就是 `.toast__bar` 那個 CSS animation 本身，不另外開計時器：hover 暫停只要一行
 * `animation-play-state: paused` 就成立，畫面上的進度條與真正的剩餘時間永遠一致。
 */
import { nextTick, watch } from "vue";

import { dismissToast, toasts, TOAST_LEVELS, type ToastItem } from "@/lib/toast";

// 下一影格才加 shown，讓進場過渡有起始狀態可以過渡（同一影格內設會被合併掉）
watch(
  () => toasts.length,
  () => {
    void nextTick(() => {
      requestAnimationFrame(() => {
        for (const t of toasts) t.shown = true;
      });
    });
  },
);

function close(item: ToastItem): void {
  if (item.closing) return; // 進度條跑完與手動關閉可能同時發生
  item.closing = true;
  // 等離場過渡跑完再移除；transitionend 沒來（分頁在背景、動畫被略過）也要收，
  // 否則節點會永遠留著。
  setTimeout(() => dismissToast(item.id), 400);
}

function onTransitionEnd(item: ToastItem): void {
  if (item.closing) dismissToast(item.id);
}
</script>

<template>
  <Teleport to="body">
    <div v-if="toasts.length" id="toast-stack" class="toast-stack" aria-live="polite">
      <div
        v-for="t in toasts"
        :key="t.id"
        class="toast"
        data-testid="toast"
        :data-level="t.level"
        :data-pausable="t.pausable ? undefined : '0'"
        :data-shown="t.shown ? '1' : undefined"
        :data-closing="t.closing ? '1' : undefined"
        @transitionend="onTransitionEnd(t)"
      >
        <i class="toast__icon fa-solid" :class="TOAST_LEVELS[t.level]"></i>
        <div class="toast__body">
          <!-- ⚠ 一律走文字插值（Vue 預設就會逸出）：標題與內文都可能含後端回傳或使用者
               輸入的字串。舊版是 textContent，同一條紀律。 -->
          <div class="toast__title" data-testid="toast-title">{{ t.title }}</div>
          <div class="toast__desc" data-testid="toast-desc" :hidden="!t.body">{{ t.body }}</div>
        </div>
        <button
          class="toast__close"
          type="button"
          aria-label="關閉"
          data-testid="toast-close"
          @click="close(t)"
        >
          <i class="fa-solid fa-xmark"></i>
        </button>
        <span
          class="toast__bar"
          data-testid="toast-bar"
          :style="{ animationDuration: `${t.duration}ms` }"
          @animationend="close(t)"
        ></span>
      </div>
    </div>
  </Teleport>
</template>
