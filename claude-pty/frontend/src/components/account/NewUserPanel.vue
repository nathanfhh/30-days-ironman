<script setup lang="ts">
/*
 * 新增使用者（管理員限定）。
 *
 * ⚠ 說明**依所選角色而變**：「這個帳號建出來能做什麼」正是按下建立之前唯一要判斷的事。
 *   寫成一段涵蓋兩種角色的固定文字，讀的人得自己從中挑出與當下選擇相關的半句。
 * ⚠ 成功後**明確清空**，不依賴 form.reset()：權限 picker 是自訂元件，reset 管不到它，
 *   於是「帳號密碼清了、權限還停在上一次的選擇」——下一筆很容易誤建成管理員。
 * ⚠ 密碼欄要連「顯示密碼」的狀態一起收回去（PasswordInput 的 reset()）：只清值的話，
 *   上一次按過眼睛的狀態會留著，下一個帳號的密碼就明文了。
 */
import { computed, ref, useTemplateRef } from "vue";

import { api } from "@/api/client";
import PasswordInput from "@/components/PasswordInput.vue";
import SitePicker, { type PickerOption } from "@/components/SitePicker.vue";
import { submitting } from "@/lib/submitting";
import { toast, toastError } from "@/lib/toast";
import { useSiteStore } from "@/stores/site";

const emit = defineEmits<{ created: [username: string] }>();

const store = useSiteStore();

const username = ref("");
const password = ref("");
const role = ref("0");
const busy = ref(false);
const pwField = useTemplateRef<InstanceType<typeof PasswordInput>>("pwField");

const roleOptions: PickerOption[] = [
  { value: "0", label: "一般使用者", icon: "fa-solid fa-user" },
  { value: "1", label: "管理員", icon: "fa-solid fa-user-shield" },
];

/* 每個角色實際拿到什麼——照程式的行為寫，不是照「應該差不多是這樣」寫。 */
const ROLE_NOTE: Record<string, [string, string]> = {
  "0": [
    "只看得見、也只能操作自己開的 session。讀別人的一律回「未知 session」，" +
      "連那筆存不存在都不洩漏。可以自己改密碼。",
    "info",
  ],
  "1": [
    "看得見並可終止**所有人**的 session，還能新增帳號、代改任何人的密碼。" +
      "代改就是接管：對方的登入狀態全部失效、登不回來，系統並且**嘗試**切斷" +
      "他目前追蹤得到的互動終端（可能失敗，失敗會告訴你）。權限一旦給出去" +
      "只能靠對方自律，事後**沒有**降權或停用可以收回。",
    "warn",
  ],
};

const note = computed(() => ROLE_NOTE[role.value] ?? ROLE_NOTE["0"]);

/* 只有 `**粗體**` 這一種標記，自己拆即可——為了一個強調引進 markdown 解析器，是把一行字
   的問題換成一個相依套件的問題。
   ⚠ 拆成片段用 v-for 畫，不走 v-html：舊版是 `esc(text)` 之後才 replace，這一版連那一步
     都不必——Vue 的插值本來就會逸出。 */
const noteParts = computed(() =>
  note.value[0].split(/\*\*(.+?)\*\*/).map((text, i) => ({ text, strong: i % 2 === 1, key: i })),
);

const create = submitting(busy, async () => {
  // 先取出來：清空之後就讀不到了，而通知訊息要用它
  const name = username.value;
  const isAdminRole = role.value === "1";
  try {
    await api("/api/users", {
      method: "POST",
      body: { username: name, password: password.value, is_admin: isAdminRole },
    });
    toast(`已建立帳號 ${name}`, "success", {
      body: isAdminRole ? "權限：管理員（可管理帳號與所有 session）" : "權限：一般使用者",
    });
    username.value = "";
    pwField.value?.reset();
    role.value = "0";
    emit("created", name);
  } catch (ex) {
    toastError("建立帳號", ex);
  }
});
</script>

<template>
  <section class="panel">
    <h2 class="panel__title">新增使用者</h2>
    <p class="panel__lede" id="role-lede" :data-tone="note[1]">
      <template v-for="p in noteParts" :key="p.key">
        <strong v-if="p.strong">{{ p.text }}</strong>
        <template v-else>{{ p.text }}</template>
      </template>
    </p>
    <form id="user-form" @submit.prevent="create">
      <div class="form-row" style="--form-col-min: 11rem">
        <div class="field">
          <label class="label" for="new-user">使用者名稱</label>
          <!-- maxlength 只是先擋一手（貼上超長字串時當場截斷），真正的把關在後端
               `_clean_username`——瀏覽器的限制繞得過去，而帳號建錯了不能刪。 -->
          <input
            class="input"
            id="new-user"
            v-model="username"
            data-testid="new-user"
            autocomplete="off"
            required
            :maxlength="store.meta.usernameMax"
            :title="`最長 ${store.meta.usernameMax} 字元，不可含空白或換行`"
          />
        </div>
        <div class="field">
          <label class="label" for="new-user-pw">密碼</label>
          <PasswordInput
            ref="pwField"
            id="new-user-pw"
            v-model="password"
            testid="new-user-pw"
            autocomplete="new-password"
            :required="true"
          />
        </div>
        <div class="field">
          <span class="label">權限</span>
          <SitePicker id="pick-role" v-model="role" :options="roleOptions" />
        </div>
      </div>
      <div class="form-actions">
        <button class="btn btn--primary" type="submit" :disabled="busy">
          <i class="fa-solid fa-user-plus"></i> 建立帳號
        </button>
      </div>
    </form>
  </section>
</template>
