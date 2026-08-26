<script setup lang="ts">
/*
 * 帳號管理頁。對照舊版 `server/templates/account.html`（651 行）。
 *
 * 拆成六個面板各自一個元件，順序與舊版逐塊對應：
 *   CLI 憑證 → GitLab 憑證（功能開著才畫）→ 變更我的密碼 →〔管理員〕新增使用者 →
 *   帳號清單 → ttyd 實況
 *
 * ⚠ 管理員那三塊要**整塊 gate**，不是把裡面的按鈕停用：後端那幾條 API 有 `@admin_only`，
 *   但區塊本身若對一般使用者也渲染，他會看到一張永遠載入失敗的表格，而且知道有這個東西
 *   存在（`test_template_contract` 守的就是這條）。
 */
import { onMounted, useTemplateRef } from "vue";

import AppShell from "@/components/AppShell.vue";
import CliTokenPanel from "@/components/account/CliTokenPanel.vue";
import GitlabPatPanel from "@/components/account/GitlabPatPanel.vue";
import NewUserPanel from "@/components/account/NewUserPanel.vue";
import PasswordPanel from "@/components/account/PasswordPanel.vue";
import RosterPanel from "@/components/account/RosterPanel.vue";
import TtydPanel from "@/components/account/TtydPanel.vue";
import { useSiteStore } from "@/stores/site";

const store = useSiteStore();

const roster = useTemplateRef<InstanceType<typeof RosterPanel>>("roster");
const ttyd = useTemplateRef<InstanceType<typeof TtydPanel>>("ttyd");

onMounted(() => {
  if (store.user?.is_admin) {
    void roster.value?.load();
    void ttyd.value?.load();
  }
});
</script>

<template>
  <AppShell>
    <CliTokenPanel />
    <GitlabPatPanel v-if="store.meta.gitlabEnabled" />
    <PasswordPanel />
    <template v-if="store.user?.is_admin">
      <NewUserPanel @created="roster?.afterCreate($event)" />
      <RosterPanel ref="roster" />
      <TtydPanel ref="ttyd" />
    </template>
  </AppShell>
</template>
