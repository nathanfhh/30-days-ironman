<script setup lang="ts">
/*
 * 登入頁。對照舊版 `server/templates/login.html`。
 *
 * ⚠ 送出鈕要等兩欄都填才可按——省掉一次注定失敗的往返。
 *   這裡**刻意不檢查密碼長度**：長度是「建立/變更密碼」時的政策，不是登入條件。把它搬到
 *   登入頁的話，只要哪天調高了 MIN_PASSWORD_LENGTH，所有在舊政策下建立的帳號就再也按不下
 *   這顆按鈕——而後端明明還驗得過他們的密碼。
 *
 * ⚠ TODO(階段 3)：左下角的插畫由伺服端每次隨機挑一張（`web.LOGIN_ART`），SPA 拿不到那份
 *   清單，所以現在不畫。舊版的模板本來就有 `{% if art %}` 這條分支，少了圖版面仍然成立。
 */
import { computed, onMounted, ref, useTemplateRef } from "vue";
import { useRouter } from "vue-router";

import { api } from "@/api/client";
import PasswordInput from "@/components/PasswordInput.vue";
import { toast } from "@/lib/toast";
import { useSiteStore } from "@/stores/site";

const router = useRouter();
const store = useSiteStore();

const username = ref("");
const password = ref("");
const error = ref("");
const submitting = ref(false);
const userField = useTemplateRef<HTMLInputElement>("userField");

const canSubmit = computed(() => Boolean(username.value.trim() && password.value));

async function submit(): Promise<void> {
  error.value = "";
  submitting.value = true;
  try {
    await api("/api/auth/login", {
      method: "POST",
      body: { username: username.value, password: password.value },
    });
    await store.loadIdentity();
    toast(`歡迎回來，${username.value.trim()}`, "success", { body: "已進入控制台" });
    await router.push("/");
  } catch (ex) {
    error.value = ex instanceof Error ? ex.message : String(ex);
  } finally {
    submitting.value = false;
  }
}

// 瀏覽器自動填入時值已經在，不能只等 input 事件——Vue 的 v-model 會在 mount 後同步，
// 這裡只負責把焦點放好（舊版靠 autofocus 屬性）。
onMounted(() => userField.value?.focus());
</script>

<template>
  <main class="gate">
    <aside class="gate__aside">
      <div class="gate__brand">
        <p class="gate__kicker">控制平台</p>
        <h1 class="gate__mark" data-testid="brand-mark">claude<em>-pty</em></h1>
      </div>
    </aside>

    <section class="gate__main">
      <form class="gate__form" id="login-form" autocomplete="on" @submit.prevent="submit">
        <!-- 標題就該長得像標題：顯示字體 + 一道強調色短線，而不是跟欄位標籤同一個字級 -->
        <h2 class="gate__heading">身分驗證</h2>

        <div
          class="notice"
          data-tone="error"
          id="login-error"
          data-testid="login-error"
          :hidden="!error"
        >
          {{ error }}
        </div>

        <div class="field">
          <label class="label label--field" for="username">使用者名稱</label>
          <input
            ref="userField"
            id="username"
            v-model="username"
            class="input"
            name="username"
            data-testid="login-username"
            autocomplete="username"
            required
          />
        </div>
        <div class="field">
          <label class="label label--field" for="password">密碼</label>
          <PasswordInput
            id="password"
            v-model="password"
            name="password"
            testid="login-password"
            autocomplete="current-password"
            :required="true"
          />
        </div>

        <button
          class="btn btn--primary"
          type="submit"
          id="login-btn"
          :disabled="!canSubmit || submitting"
          style="width: 100%; padding: var(--space-3)"
        >
          <i class="fa-solid fa-right-to-bracket"></i> 進入控制台
        </button>
      </form>
    </section>
  </main>
</template>
