<script setup lang="ts">
/*
 * CLI 憑證。對照舊版 `account.html` 的第一個 panel。
 *
 * 狀態 chip 讀的是招牌那份憑證狀態（同一個真相，不另打一趟 API）——舊版讀 `#cred-data`，
 * 這一版讀 store，來源都是 `credentials_state`。
 *
 * ⚠ 欄位**永遠是空的**（存進去不吐回來），所以「設過沒」只能靠 placeholder 講，不然兩種
 *   狀態在這一格裡長得一模一樣。
 * ⚠ 存／清之後重抓 `/api/account/bootstrap`：徽章、chip、按鈕三處都是照那一份畫的，
 *   一發就全部對齊（階段 4 為了與舊版一致是整頁重載，舊版拆掉之後不必再付那個代價）。
 */
import { computed, ref } from "vue";

import { api } from "@/api/client";
import PasswordInput from "@/components/PasswordInput.vue";
import { submitting } from "@/lib/submitting";
import { toast, toastError } from "@/lib/toast";
import { useSiteStore } from "@/stores/site";

const store = useSiteStore();

const token = ref("");
const busy = ref(false);

const credOk = computed(() => store.credentials[store.meta.defaultCli]?.ok === true);
const placeholder = computed(() =>
  credOk.value ? "••••••••••••••••••••（已設定，貼新的可覆寫）" : "貼上 claude setup-token 的輸出",
);

/** 存或清之後把狀態重新抓一次。
 *
 * ⚠ 舊版是 `setTimeout(() => location.reload(), 900)`——徽章、chip、按鈕三處狀態同源重畫，
 *   在一個每頁都要重新跑 Jinja 的架構下那是最可靠的做法。SPA 不必付那個代價：
 *   `/api/account/bootstrap` 一發就把憑證、限制、身分全帶回來，那三處都是照它畫的。
 * ⚠ 欄位要**自己清**。舊版靠整頁重載順便清掉，不重載就得明寫——不清的話畫面會停在
 *   「已經存進去了，但輸入框裡還留著剛剛那把 token」，而那是最不該留在畫面上的東西。
 */
async function refresh(): Promise<void> {
  token.value = "";
  await store.loadAccountMeta();
}

const save = submitting(busy, async () => {
  try {
    await api("/api/users/me/token", { method: "PUT", body: { token: token.value } });
    toast("憑證已儲存", "success", { body: "之後開的 session 會用它登入" });
    await refresh();
  } catch (ex) {
    toastError("儲存憑證", ex);
  }
});

async function clear(): Promise<void> {
  try {
    await api("/api/users/me/token", { method: "DELETE" });
    toast("憑證已清除", "warning", {
      body: "之後開新 session 會被擋下，重新貼上即可；已在跑的不受影響",
    });
    await refresh();
  } catch (ex) {
    toastError("清除憑證", ex);
  }
}
</script>

<template>
  <section class="panel">
    <div class="section-head" style="margin-bottom: var(--space-2)">
      <h2 class="panel__title" style="margin: 0">CLI 憑證</h2>
      <span id="token-state" data-testid="token-state">
        <span v-if="credOk" class="chip" data-tone="ok">已設定</span>
        <span v-else class="chip" data-tone="error">未設定</span>
      </span>
    </div>
    <!-- ⚠ `</code>` 與那個全形逗號之間**不可以有空白**。prettier 會把 `</code>` 斷到行尾，
         而下一行以逗號開頭的文字節點不是「只有空白」，所以 Vue 的 condense 只把換行摺成
         **一個空白**、不會拿掉——畫面上就是 `setup-token ，把輸出`，整段跟著位移，
         帳號頁的截圖因此紅（0.44%、6754 個強差異像素）。
         `</code
      >` 這個寫法是 prettier 自己用來避免多出空白的那一招，照它做。 -->
    <p class="panel__lede">
      在 host 上執行
      <code data-copy title="點一下複製">claude setup-token</code>，把輸出貼進來。之後開的 session
      用它 登入；<strong>token 過期不會有預告</strong>——症狀是新開的 session 停在登入提示，
      遇到就重跑一次指令、把新的貼回這裡（已在跑的 session 不受影響）。
    </p>
    <form id="token-form" @submit.prevent="save">
      <div class="form-row">
        <div class="field" style="flex: 1">
          <label class="label" for="cli-token">setup-token</label>
          <!-- type=password：貼進來的是憑證，畫面上不該讀得出來（旁邊有人、截圖、錄影）。
               走 PasswordInput：舊版是 `enhancePasswordFields()` 掃過去包起來的，包完會多一個
               `.pw` 外框與一顆「看一眼」按鈕，而那顆按鈕在 aria 樹裡看得到。 -->
          <PasswordInput
            id="cli-token"
            v-model="token"
            testid="cli-token"
            autocomplete="off"
            :placeholder="placeholder"
          />
        </div>
      </div>
      <div class="form-actions">
        <button
          class="btn btn--primary"
          type="submit"
          id="token-save"
          data-testid="token-save"
          :disabled="!token.trim() || busy"
        >
          <i class="fa-solid fa-id-card"></i> 儲存
        </button>
        <button
          class="btn"
          type="button"
          id="token-clear"
          data-testid="token-clear"
          :hidden="!credOk"
          @click="clear"
        >
          <i class="fa-solid fa-eraser"></i> 清除
        </button>
      </div>
    </form>
  </section>
</template>
