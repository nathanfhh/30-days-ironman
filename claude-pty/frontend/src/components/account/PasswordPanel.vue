<script setup lang="ts">
/*
 * 變更我的密碼。兩次輸入一致且達長度下限才讓按鈕可按（後端仍會自己驗一次）。
 *
 * ⚠ 改密碼＝這個帳號的登入狀態**全部失效，包含這一台**。所以不 reset 表單、不留在原頁——
 *   直接送回登入頁。留在這裡的話下一個動作一定是 401，那比直接跳轉更難懂。
 * ⚠ **不要無條件宣稱終端都收掉了。** 後端在收不乾淨時回 200 加實情（不是 204），而那正是
 *   這一整條線在買的東西——在這裡丟掉的話，畫面又變回「看起來都好」。
 * ⚠ 失敗那條要**多留時間讓人讀完**再跳（7 秒）：1.2 秒足夠讀「請重新登入」，不足以讀完
 *   「有終端沒收掉、那些連線還可以打字」。
 * ⚠ **先把表單清空。** 送出鈕在 finally 會被還原成可按，而密碼改掉的那一刻本頁 cookie
 *   已經失效——在跳轉之前再按一次，api() 收到 401 會立刻導回登入頁，把剛剛那個警告洗掉。
 */
import { computed, ref } from "vue";

import { api } from "@/api/client";
import PasswordInput from "@/components/PasswordInput.vue";
import { submitting } from "@/lib/submitting";
import { toast, toastError } from "@/lib/toast";
import { useSiteStore } from "@/stores/site";

const store = useSiteStore();

const oldPw = ref("");
const newPw = ref("");
const confirmPw = ref("");
const busy = ref(false);

const minPw = computed(() => store.meta.minPasswordLength);

/** 只在使用者已經開始輸入時才報錯，不然一進頁面就滿江紅。 */
const hint = computed(() => {
  if (newPw.value && newPw.value.length < minPw.value) return `至少 ${minPw.value} 字元`;
  if (confirmPw.value && newPw.value !== confirmPw.value) return "兩次輸入不一致";
  return "";
});

const canSubmit = computed(
  () =>
    Boolean(oldPw.value) && newPw.value.length >= minPw.value && newPw.value === confirmPw.value,
);

const save = submitting(busy, async () => {
  try {
    const r = await api<{ views_failed?: boolean } | null>("/api/users/me/password", {
      method: "POST",
      body: { old_password: oldPw.value, new_password: newPw.value },
    });
    oldPw.value = "";
    newPw.value = "";
    confirmPw.value = "";
    let wait = 1200;
    if (r && r.views_failed) {
      wait = 7000;
      toast("密碼已更改，但終端沒有收乾淨", "warning", {
        body:
          "所有裝置的登入已失效，但有終端沒收掉；那些連線在收掉之前仍然可以打字。" +
          "重新登入之後把它們關掉，或直接終止那幾場 session。",
        duration: wait,
        pausable: false,
      });
    } else {
      // ⚠ 「這次追蹤到的」不是客套，是這句話唯一站得住的範圍：後端收的是它當下列得出來
      //   的那些 view。這裡不可以升級成「所有終端」。
      toast("密碼已更改，請重新登入", "success", {
        body: "所有裝置的登入已失效，這次追蹤到的互動終端也都收掉了；容器不受影響，重新登入即可接回",
      });
    }
    setTimeout(() => {
      globalThis.location.href = "/login";
    }, wait);
  } catch (ex) {
    toastError("更新密碼", ex);
  }
});
</script>

<template>
  <section class="panel">
    <h2 class="panel__title">變更我的密碼</h2>
    <form id="pw-form" data-testid="pw-form" @submit.prevent="save">
      <div class="form-row">
        <div class="field">
          <label class="label" for="old-pw">目前密碼</label>
          <PasswordInput
            id="old-pw"
            v-model="oldPw"
            testid="old-pw"
            autocomplete="current-password"
            :required="true"
          />
        </div>
        <div class="field">
          <label class="label" for="new-pw">新密碼（至少 {{ minPw }} 字元）</label>
          <PasswordInput
            id="new-pw"
            v-model="newPw"
            testid="new-pw"
            autocomplete="new-password"
            :required="true"
          />
        </div>
        <div class="field">
          <label class="label" for="confirm-pw">再輸入一次新密碼</label>
          <PasswordInput
            id="confirm-pw"
            v-model="confirmPw"
            testid="confirm-pw"
            autocomplete="new-password"
            :required="true"
          />
          <!-- prettier-ignore -->
          <span class="field__hint" id="pw-hint" data-testid="pw-hint" :hidden="!hint" data-tone="error">{{ hint }}</span>
        </div>
      </div>
      <div class="form-actions">
        <button
          class="btn btn--primary"
          type="submit"
          id="pw-btn"
          data-testid="pw-btn"
          :disabled="!canSubmit || busy"
        >
          <i class="fa-solid fa-key"></i> 更新密碼
        </button>
      </div>
    </form>
  </section>
</template>
