<script setup lang="ts">
/*
 * 整個 SPA 的外框。
 *
 * 頁尾放在 router-view 之外、與它同層——對照舊版 `base.html`：頁尾是 body 層級的東西，
 * 三個頁面（登入 / session / 帳號）都有，「我看到的是不是最新的」在哪一頁都會問。
 * toast 與對話框同樣掛在最外層（實際上是 Teleport 到 body）。
 */
import AppFooter from "@/components/AppFooter.vue";
import DialogHost from "@/components/DialogHost.vue";
import ToastStack from "@/components/ToastStack.vue";
</script>

<template>
  <RouterView />
  <AppFooter />
  <ToastStack />
  <DialogHost />
</template>
