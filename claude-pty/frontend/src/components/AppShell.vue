<script setup lang="ts">
/*
 * 登入之後每一頁共用的外框：招牌 + 內容 + 設定對話框。
 *
 * 舊版是 Jinja 的 `{% include "_masthead.html" %}`，而**設定對話框只存在於 session 頁**
 * ——它是從身分下拉叫出來的，那個下拉每一頁都有，對話框卻只有一頁掛得起來。
 * 收進這個外框之後兩件事都跟著身分走，每一頁都在（這正是當初把設定搬進下拉的理由）。
 */
import { ref } from "vue";

import AppMasthead from "./AppMasthead.vue";
import SettingsModal from "./SettingsModal.vue";

const settingsOpen = ref(false);
</script>

<template>
  <div class="shell" data-testid="shell">
    <AppMasthead @settings="settingsOpen = true" />
    <slot />
    <SettingsModal v-if="settingsOpen" @close="settingsOpen = false" />
  </div>
</template>
