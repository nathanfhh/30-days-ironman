<script setup lang="ts">
/* ── 設定對話框：終端程式（ttyd） ────────────────────────────────────────────
 * 原本是 session 頁上一塊展開的面板，兩個問題：它與篩選共用多欄格線而這裡只有兩格
 * （右邊永遠空一大塊，在**設定**這種面板上讀起來像「有東西沒載出來」），而且它只存在
 * 於 session 頁——設定是跟著**身分**走的東西，換頁不該消失。
 *
 * ⚠ Esc 要掛在 **document** 上、不是 wrap 上，而且**必須設初始焦點**：這個對話框是從
 *   身分下拉點開的，選單關掉之後焦點掉回 `<body>`，掛在 wrap 上的 keydown 永遠收不到。
 *   初始焦點放關閉鍵（唯一一定聚焦得上的控件——picker 要等 /api/prefs 回來才建）。
 */
import { onBeforeUnmount, onMounted, ref, useTemplateRef } from "vue";

import { api } from "@/api/client";
import { toast } from "@/lib/toast";

import SitePicker, { type PickerOption } from "./SitePicker.vue";

const emit = defineEmits<{ close: [] }>();

interface Prefs {
  ttyd_bin: string;
  ttyd_choices: { value: string; label: string }[];
}

const options = ref<PickerOption[]>([]);
const value = ref("");
/* 最後一次**確定存進去**的值，存不進去時要轉回它。
 *
 * ⚠ 不可以在 `save()` 裡從 `value` 取「改之前是什麼」。SitePicker 是先
 *   `emit("update:modelValue")` 再 `emit("change")`，而 v-model 的處理器是同步跑的，所以
 *   進到 `save()` 的那一刻 `value` 已經是新值了：舊版那句 `const before = value.value`
 *   記下來的是新值，於是 catch 裡的還原等於什麼都沒做。症狀是畫面停在「Rust 版」、
 *   下一場卻開出 C 版，兩者不一致而且沒有任何跡象（單元測試當場抓到的）。
 */
const committed = ref("");
const loaded = ref(false);
const closeBtn = useTemplateRef<HTMLButtonElement>("closeBtn");

function onKey(e: KeyboardEvent): void {
  if (e.key === "Escape") emit("close");
}

async function save(next: string): Promise<void> {
  try {
    const saved = await api<Prefs>("/api/prefs", { method: "PATCH", body: { ttyd_bin: next } });
    // 以伺服器回的為準（它才知道最後存進去的是什麼），畫面與事實才不會分岔
    committed.value = saved.ttyd_bin;
    value.value = saved.ttyd_bin;
    const label = options.value.find((o) => o.value === saved.ttyd_bin)?.label ?? saved.ttyd_bin;
    toast(
      `新開的 session 會用 ${label} 版；已經開著的那一場，要把終端分頁全部關掉、下次再開才會換`,
    );
  } catch (ex) {
    value.value = committed.value; // 存不進去就轉回真實值，不要留假象
    toast(`設定沒存成功：${ex instanceof Error ? ex.message : String(ex)}`, "error");
  }
}

onMounted(async () => {
  document.addEventListener("keydown", onKey);
  closeBtn.value?.focus();
  try {
    const d = await api<Prefs>("/api/prefs");
    options.value = d.ttyd_choices.map((c) => ({ value: c.value, label: c.label }));
    value.value = d.ttyd_bin;
    committed.value = d.ttyd_bin;
    loaded.value = true;
  } catch (ex) {
    toast(`讀取設定失敗：${ex instanceof Error ? ex.message : String(ex)}`, "error");
  }
});

onBeforeUnmount(() => document.removeEventListener("keydown", onKey));
</script>

<template>
  <Teleport to="body">
    <div
      class="modal"
      data-testid="settings-modal"
      @click="$event.target === $event.currentTarget && emit('close')"
    >
      <div
        class="modal__box modal__box--wide"
        data-testid="modal-box"
        role="dialog"
        aria-modal="true"
        aria-labelledby="settings-modal-title"
      >
        <!-- prettier-ignore -->
        <h2 class="modal__title" data-testid="modal-title" id="settings-modal-title">
          <i class="fa-solid fa-sliders"></i> 設定</h2>
        <div class="settings">
          <section class="settings__row" data-testid="settings-row">
            <div class="settings__head">
              <span class="settings__label" data-testid="settings-label">終端程式</span>
              <span class="settings__note" data-testid="settings-note">
                新開的 session
                立刻套用；已經開著的那一場，要把終端分頁全部關掉、下次再開才會換</span
              >
            </div>
            <!-- 下拉本身就顯示著現值，不另外印「目前：Rust」——那是同一件事寫兩次 -->
            <SitePicker
              v-if="loaded"
              id="pick-ttyd"
              v-model="value"
              :options="options"
              @change="save($event.value)"
            />
            <!-- prettier-ignore -->
            <p
              class="settings__note"
              data-testid="settings-note"
              style="margin: var(--space-2) 0 0"
            >
              兩顆有一個實質差異：網頁標題 Rust 版由伺服器端決定，C 版是在瀏覽器端蓋掉的。
              在意這一點就選 Rust 版。</p>
          </section>
        </div>
        <div class="modal__actions">
          <!-- prettier-ignore -->
          <button ref="closeBtn" class="btn" data-act="close" @click="emit('close')">
            <i class="fa-solid fa-xmark"></i> 關閉</button>
        </div>
      </div>
    </div>
  </Teleport>
</template>
