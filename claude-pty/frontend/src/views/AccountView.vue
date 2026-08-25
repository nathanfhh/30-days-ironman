<script setup lang="ts">
/*
 * 帳號管理頁：**階段 4 前半只有殼**。
 *
 * 為什麼不現在做：舊版 `account.html` 有 651 行、**28 處 Jinja 注入**（是三個模板裡最多的），
 * 而那 28 處正是計畫階段 3 要改成 API 的東西。在那些端點出現之前搬過來，等於在前端重寫一份
 * 猜出來的伺服端狀態——那是「描述≠實作」的反面教材，也會讓階段 3 完成時整頁重做一次。
 *
 * 這一頁要搬的區塊（都在舊版模板裡）：改密碼、CLI 憑證（setup-token）、GitLab PAT、
 * ttyd 檢視（views／orphans）、管理員的帳號名冊與新增帳號。
 */
import AppShell from "@/components/AppShell.vue";
</script>

<template>
  <AppShell>
    <section class="panel">
      <h2 class="panel__title">帳號管理</h2>
      <!-- prettier-ignore -->
      <p class="empty">
        這一頁還沒搬到這一版（階段 4 後半，要等階段 3 的 endpoint）。目前請切回舊介面
        （`CLAUDE_PTY_UI=legacy`）使用。</p>
    </section>
  </AppShell>
</template>
