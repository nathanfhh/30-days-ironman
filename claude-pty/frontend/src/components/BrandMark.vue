<script setup lang="ts">
/* ── 品牌標誌（內嵌 SVG）────────────────────────────────────────────────────────
 * Font Awesome 沒有 Anthropic 的圖示，且品牌標誌不該相依外部 CDN。
 * 一律以 fill="currentColor" 繪製：顏色由 CSS 的 color 繼承，因此**深色/淺色主題
 * 自動適配、不需要各準備一份**（換主題只改 --color-* 變數，標誌跟著變）。
 * path 與舊版 app.js 的 BRAND_PATHS 同一份。
 */
const BRAND_PATHS: Record<string, string> = {
  anthropic:
    "M17.3041 3.541h-3.6718l6.696 16.918H24Zm-10.6082 0L0 20.459h3.7442l1.3693-3.5527h7." +
    "0052l1.3693 3.5528h3.7442L10.5363 3.5409Zm-.3712 10.2232 2.2914-5.9456 2.2914 5.9456Z",
};

const props = withDefaults(defineProps<{ name: string; cls?: string }>(), {
  cls: "picker__icon",
});

const path = (): string | undefined => BRAND_PATHS[props.name];
</script>

<template>
  <svg
    v-if="path()"
    :class="cls"
    viewBox="0 0 24 24"
    width="1.05em"
    height="1.05em"
    fill="currentColor"
    aria-hidden="true"
  >
    <path :d="path()" />
  </svg>
</template>
