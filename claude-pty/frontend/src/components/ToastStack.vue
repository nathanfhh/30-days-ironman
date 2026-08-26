<script setup lang="ts">
/*
 * toast 的 DOM。掛在 <body> 底下（Teleport）。
 *
 * ⚠ **一則都沒有的時候整個堆疊不存在**，不是留一個空的 div。舊版是第一則 toast 才
 *   `document.body.appendChild`，而 golden 的 DOM 快照記 id——常駐的話每一個沒有 toast 的
 *   場景都會多一行 `div id=toast-stack`。
 *   （4a 的快審意見是「常駐」，理由是 aria-live 區域從第一幀就在比較保險。但那是在 golden
 *   還沒有 DOM 快照之前給的；現在規格明確說了舊版長什麼樣，以規格為準。真要改成常駐，
 *   該連 app.js 一起改並重錄 golden。）
 *
 * 原本掛在 <body> 底下（Teleport）——與抽屜同層而 z-index 較高，所以蓋得住
 * 抽屜；抽屜把 `.shell` 設成 inert，但 toast 不在 `.shell` 裡，仍然點得到
 * （e2e_drawer 有一條在守這件事）。
 *
 * 倒數就是 `.toast__bar` 那個 CSS animation 本身，不另外開計時器：hover 暫停只要一行
 * `animation-play-state: paused` 就成立，畫面上的進度條與真正的剩餘時間永遠一致。
 */
import { nextTick, watch } from "vue";

import { dismissToast, toasts, TOAST_LEVELS, type ToastItem } from "@/lib/toast";

// 下一影格才加 shown，讓進場過渡有起始狀態可以過渡（同一影格內設會被合併掉）
// ⚠ `immediate: true` 不是裝飾：main.ts 在 `app.mount()` **之前**就 `drainPendingToast()`，
//   取出來的那一則在這個元件掛上時已經躺在 toasts 裡。watch 只看之後的變化的話，那一則
//   永遠拿不到 shown，CSS 讓它停在 opacity 0 直到倒數結束自己消失，畫面上從頭到尾沒有人
//   看到它（Copilot review 2026-08-26 抓到的）。
//   ⚠ 當時舉的例子是「登出後跳回登入頁的那則『已登出』」，那個例子已經不存在了（登出改成
//     SPA 內換頁 ＋ 直接 `toast()`，2026-08-26）。守的性質沒變，只是現在寄放區恆空：這一行
//     守的是**任何在掛載之前就進佇列的 toast**，而 drainPendingToast() 仍然跑在 mount 之前。
watch(
  () => toasts.length,
  () => {
    void nextTick(() => {
      requestAnimationFrame(() => {
        for (const t of toasts) t.shown = true;
      });
    });
  },
  { immediate: true },
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
