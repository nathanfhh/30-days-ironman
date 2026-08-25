import { onMounted, onUpdated, type Ref } from "vue";

/* chip 的寬度是固定的（見 app.css：整欄對齊本身就是資訊），所以長字串會被切掉。
 * **只有真的被切到的才掛 tooltip**：每一顆都掛的話，滑過 `high` 也會彈出一個只是重複
 * 顯示同樣文字的框——那是雜訊，也讓「有提示＝這裡有你看不到的東西」這個訊號失效。
 *
 * ⚠ 只有排版過才量得出來（scrollWidth/clientWidth），所以每次 render 完都要跑一次。
 *   視窗縮放不另外掛監聽：列表本來就每 15 秒重畫一次，最多慢那一輪。
 * ⚠ [量的那一層, 掛 tooltip 的那一層] **一定是兩層**：tooltip 是 ::after，掛在有
 *   overflow:hidden 的元素上會被連著裁掉（見 app.css 的 .manifest__id-text）。
 */
const PAIRS: [string, string][] = [
  [".chip__text", ".chip"],
  [".manifest__id-text", ".manifest__id"],
];

export function markClipped(root: HTMLElement): void {
  for (const [inner, outer] of PAIRS) {
    for (const el of root.querySelectorAll<HTMLElement>(inner)) {
      // +1 是給次像素捨入的餘裕：等寬時瀏覽器偶爾會回報差 0.5px，那不是被切到
      const clipped = el.scrollWidth > el.clientWidth + 1;
      const host = el.closest<HTMLElement>(outer);
      if (!host) continue;
      host.classList.toggle("tip", clipped);
      if (clipped) host.dataset.tip = el.textContent ?? "";
      else delete host.dataset.tip;
    }
  }
}

export function useClipTips(root: Ref<HTMLElement | null>): void {
  const run = (): void => {
    if (root.value) markClipped(root.value);
  };
  onMounted(run);
  onUpdated(run);
}
