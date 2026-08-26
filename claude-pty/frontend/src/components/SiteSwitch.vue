<script setup lang="ts">
/* ── 開關：二選一的選項不該用下拉 ──────────────────────────────────────────────
   下拉是「從 N 個裡挑一個」的元件；只有開與關兩種狀態時，它讓使用者多按一次、多讀
   一份選單，卻沒有多給任何資訊。開關把狀態直接畫在畫面上。

   off/on 是**值**而非布林：網路能力的兩端是 restricted / unrestricted，不是 true/false。

   ⚠ DOM 只建一次，之後只改 class 與文字（Vue 的 class binding 本來就是就地改）。
     整段重建的話沒有起始狀態可以過渡，CSS 上明明寫了 transition，畫面卻是瞬間跳過去。
   ⚠ 文字**一律**填入，靠 CSS 淡入淡出；清成空字串的話它是瞬間消失，過渡就沒了。
*/
const props = withDefaults(
  defineProps<{
    id: string;
    modelValue: string;
    off: string;
    on: string;
    offLabel: string;
    onLabel: string;
    offIcon?: string;
    onIcon?: string;
    hint?: string;
    /** 沒有這個名字，螢幕閱讀器只會唸出「switch，已勾選」——聽的人無從得知這是網路
     *  能力、流量錄製還是 telemetry。旁邊那行狀態文字對它來說只是不相干的兄弟節點。 */
    name?: string;
  }>(),
  { offIcon: "fa-circle", onIcon: "fa-circle", hint: "", name: "" },
);

const emit = defineEmits<{ "update:modelValue": [value: string]; change: [value: string] }>();

const isOn = (): boolean => props.modelValue === props.on;

function toggle(): void {
  const next = isOn() ? props.off : props.on;
  emit("update:modelValue", next);
  emit("change", next);
}

function onKeydown(e: KeyboardEvent): void {
  // role=switch 的鍵盤約定：空白/Enter 切換；方向鍵是「明確指定開或關」而非切換
  if (e.key === " " || e.key === "Enter") {
    e.preventDefault();
    toggle();
  } else if (e.key === "ArrowRight" && !isOn()) {
    e.preventDefault();
    toggle();
  } else if (e.key === "ArrowLeft" && isOn()) {
    e.preventDefault();
    toggle();
  }
}
</script>

<template>
  <div :id="id" class="switch" :data-testid="id" :data-on="String(isOn())">
    <button
      type="button"
      class="switch__control"
      :data-testid="`${id}-control`"
      role="switch"
      :aria-label="name || id || '開關'"
      :aria-checked="isOn() ? 'true' : 'false'"
      @click="toggle"
      @keydown="onKeydown"
    >
      <span class="switch__track">
        <span class="switch__thumb">
          <i class="switch__icon fa-solid" :class="isOn() ? onIcon : offIcon"></i>
        </span>
      </span>
    </button>
    <!-- 標籤也可點：命中區大一點，不必瞄準那顆小圓 -->
    <span class="switch__label" :data-testid="`${id}-label`" @click="toggle">
      <span class="switch__text">{{ isOn() ? onLabel : offLabel }}</span>
      <span class="switch__hint">{{ hint }}</span>
    </span>
  </div>
</template>
