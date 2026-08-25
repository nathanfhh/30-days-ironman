/* 把浮層放到觸發元件的下方（或上方）。picker、日期區間選擇器與身分下拉共用。
 *
 * 逐條移植自舊版 app.js 的 `anchorPanel`，理由一併搬過來（它們都是實測換來的）：
 *
 * ⚠ 用 `position: fixed` 而不是相對按鈕的 absolute：這些浮層會出現在有
 *   `overflow-y: auto` 的容器裡（modal 的 .modal__scroll），absolute 的浮層會被那個
 *   容器裁掉——展開後只看得到頂端一小條。
 * ⚠ fixed 的定位基準不一定是視窗：祖先只要有 backdrop-filter / transform / filter
 *   就會變成它的 containing block，而招牌與對話框遮罩兩者都有 backdrop-filter。
 *   所以座標**先歸零、量出實際落點、再回推差值**——不去猜基準是誰。
 */
export interface AnchorOptions {
  mount?: HTMLElement | null;
  matchWidth?: boolean;
}

export function anchorPanel(
  anchor: HTMLElement,
  panel: HTMLElement,
  { mount = null, matchWidth = false }: AnchorOptions = {},
): void {
  const r = anchor.getBoundingClientRect();
  // fixed 之後 `min-width:100%` 是對視窗算的，要的話得自己補上觸發元件的寬度
  if (matchWidth) panel.style.minWidth = `${r.width}px`;
  panel.style.top = "0px";
  panel.style.left = "0px";
  const zero = panel.getBoundingClientRect();
  const { height: h, width: w } = zero;
  // 下方空間不足就往上開。固定往下開的話，位在畫面底部的浮層一展開就整片跑到視窗外。
  const up = globalThis.innerHeight - r.bottom < h + 16 && r.top > h + 16;
  if (mount) mount.dataset.drop = up ? "up" : "down";
  // 靠右緣的浮層展開後可能超出視窗，往左收回來（留 8px 邊距）
  const wantLeft = Math.max(8, Math.min(r.left, globalThis.innerWidth - w - 8));
  /* ⚠ 垂直也要夾。翻上翻下只在「其中一側塞得下」時有解——浮層比可用高度還高時，
   *   兩側都不夠，於是它就整片露到畫面外面去。夾回可視範圍，超高的部分交給面板自己捲。 */
  const wantTop = up ? r.top - h - 6 : r.bottom + 6;
  const maxTop = Math.max(8, globalThis.innerHeight - h - 8);
  panel.style.top = `${Math.max(8, Math.min(wantTop, maxTop)) - zero.top}px`;
  panel.style.left = `${wantLeft - zero.left}px`;
}
