<script setup lang="ts">
/* ── 密碼欄位的「看一眼」 ──────────────────────────────────────────────────────
 * 密碼是盲打的，而這裡的欄位有兩種都很容易打錯的情境：登入時的長密碼、以及「新密碼／
 * 再輸入一次」那組（打錯的代價是換一個自己不知道的密碼）。
 *
 * 舊版是「掃描頁面上所有 input[type=password] 再包一層」，因為欄位散在兩個模板共五處
 * 加上動態產生的一個。Vue 版改成一個元件——漏不掉，因為密碼欄位就是它。DOM 與舊版
 * 包完之後一模一樣（`.pw` > input + `.pw__toggle`）。
 */
import { ref, useTemplateRef } from "vue";

defineProps<{
  id: string;
  testid?: string;
  autocomplete?: string;
  required?: boolean;
  placeholder?: string;
  name?: string;
}>();

const model = defineModel<string>({ required: true });

const shown = ref(false);
const input = useTemplateRef<HTMLInputElement>("input");

function toggle(): void {
  // 切換 type 會讓游標跳到字尾，先記下位置再還原——不然看一眼密碼就得重新找插入點
  const el = input.value;
  const a = el?.selectionStart ?? null;
  const b = el?.selectionEnd ?? null;
  shown.value = !shown.value;
  void Promise.resolve().then(() => {
    el?.focus();
    try {
      if (a !== null && b !== null) el?.setSelectionRange(a, b);
    } catch {
      /* 某些瀏覽器在 type 切換後不給設 */
    }
  });
}

/** 把欄位收回遮蔽狀態並清空。
 *
 * ⚠ 清 value 而不收回 type 是會出事的：管理員為了確認沒打錯而按了眼睛，送出後表單
 *   清空但欄位還是 text，**下一個人的密碼就全程明文顯示在螢幕上**。 */
function reset(): void {
  model.value = "";
  shown.value = false;
}

defineExpose({ reset });
</script>

<template>
  <span class="pw">
    <!-- ⚠ 切成 text 之後就不再是「密碼欄位」，瀏覽器的保護跟著消失：拼字檢查會生效，
         而部分瀏覽器與擴充套件會把 text 欄位的內容送到遠端做檢查（spell-jacking）。
         這三個屬性的成本是零，一律關掉。 -->
    <input
      ref="input"
      v-model="model"
      class="input"
      :id="id"
      :name="name"
      :type="shown ? 'text' : 'password'"
      :data-testid="testid"
      :autocomplete="autocomplete"
      :required="required"
      :placeholder="placeholder"
      :spellcheck="false"
      autocorrect="off"
      autocapitalize="off"
    />
    <!-- ⚠ 刻意**留在 Tab 順序裡**：純鍵盤與螢幕閱讀器使用者才是最需要「看一眼自己打對沒」
         的人。名稱固定、狀態交給 aria-pressed（兩個都變的話會被唸成「隱藏密碼，已按下」）。 -->
    <button
      type="button"
      class="pw__toggle"
      aria-label="顯示密碼"
      :aria-pressed="shown ? 'true' : 'false'"
      @click="toggle"
    >
      <i class="fa-solid" :class="shown ? 'fa-eye-slash' : 'fa-eye'"></i>
    </button>
  </span>
</template>
