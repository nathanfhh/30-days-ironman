<script setup lang="ts">
/* `dialog()` 的 DOM。結構、class 與 testid 對照舊版 app.js 的同名函式。 */
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";

import { dialogs, settleDialog, type DialogEntry } from "@/lib/dialog";

const inputs = ref<Record<number, HTMLInputElement | null>>({});

const answer = (d: DialogEntry): string | boolean | null => {
  if (!d.input) return true;
  // allowEmpty：讓「清空」成為有效答案（例如取消命名），而不是被當成按了取消
  return d.input.allowEmpty ? d.draft : d.draft || null;
};

const top = (): DialogEntry | undefined => dialogs.at(-1);

/* 注音／日文等 IME 選字時也會送出 Enter；那一下是「確認選字」不是「送出表單」。
   isComposing 在部分瀏覽器於 compositionend 的同一次 keydown 已為 false，故自行記狀態。 */
const composing = ref(false);

function onKey(e: KeyboardEvent): void {
  const d = top();
  if (!d) return;
  if (e.key === "Escape" && !composing.value) settleDialog(d.id, null);
  if (e.key === "Enter" && d.input && !composing.value && !e.isComposing)
    settleDialog(d.id, answer(d));
}

onMounted(() => document.addEventListener("keydown", onKey));
onBeforeUnmount(() => document.removeEventListener("keydown", onKey));

// 有輸入框就選取它的內容（改名時多半是要整個換掉），否則把焦點放在確定鍵上。
watch(
  () => dialogs.length,
  () => {
    const d = top();
    if (!d) return;
    void nextTick(() => {
      if (d.input) inputs.value[d.id]?.select();
    });
  },
);
</script>

<template>
  <Teleport to="body">
    <div
      v-for="d in dialogs"
      :key="d.id"
      class="modal"
      data-testid="modal"
      @click="$event.target === $event.currentTarget && settleDialog(d.id, null)"
    >
      <div
        class="modal__box"
        :class="{ 'modal__box--screen': d.wide }"
        data-testid="modal-box"
        role="dialog"
        aria-modal="true"
      >
        <h2 class="modal__title" data-testid="modal-title">{{ d.title }}</h2>
        <div class="modal__body" data-testid="modal-body">{{ d.body }}</div>
        <!-- pre 走文字插值：它裝的是使用者原本的 prompt 原文，可能含任何字元 -->
        <pre
          v-if="d.pre !== null"
          id="modal-pre"
          class="modal__pre"
          :class="{ 'modal__pre--nowrap': d.preNoWrap }"
          >{{ d.pre }}</pre>
        <div v-if="d.input" class="field">
          <input
            :ref="(el) => (inputs[d.id] = el as HTMLInputElement | null)"
            v-model="d.draft"
            id="modal-input"
            class="input"
            :type="d.input.type || 'text'"
            :maxlength="d.input.maxLength || 200"
            :placeholder="d.input.placeholder || ''"
            @compositionstart="composing = true"
            @compositionend="composing = false"
          />
        </div>
        <div class="modal__actions">
          <!-- prettier-ignore -->
          <button
            v-if="!d.viewOnly"
            class="btn"
            data-act="cancel"
            @click="settleDialog(d.id, null)"
          >
            <i class="fa-solid fa-xmark"></i> 取消</button>
          <button
            class="btn"
            :class="d.danger ? 'btn--danger' : 'btn--primary'"
            data-act="ok"
            @click="settleDialog(d.id, answer(d))"
          >
            <!-- prettier-ignore -->
            <i
              class="fa-solid"
              :class="d.confirmIcon || (d.danger ? 'fa-circle-stop' : 'fa-check')"
            ></i>
            {{ d.viewOnly ? "關閉" : d.confirmText }}
          </button>
        </div>
      </div>
    </div>
  </Teleport>
</template>
