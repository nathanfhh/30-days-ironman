"""ttyd 的替身：一份極簡的 HTML，模的是父頁面真正會碰到的那幾件事。

抽出來成一個獨立模組，是因為它現在有**兩個**使用者：`e2e_drawer.py`（驗抽屜的尺寸同步）
與 `golden_scenes.py`（錄抽屜開啟的 golden master）。複製兩份的話它們會各自漂走，而漂走
之後兩邊驗的就不是同一個終端了，卻沒有任何東西會紅。

⚠ 檔名沒有 `test_` 前綴是刻意的：`run-all.sh` 的 glob 撿的是 `tests/test_*.py` 與
  `tests/e2e_*.py`，這個檔案不是測試、被撿走只會空跑（同 `fake_gitlab.py`）。

只做父頁面真正會碰到的那幾件事。字寬/行高取等寬字的常見比例，讓「改字級」真的會改
變欄列數——測「送出的是哪一個尺寸」需要這個因果關係，寫死的數字驗不出東西。

畫布那一段模的是 xterm 真正的行為（2026-07-27 在真的 ttyd 上量過每一條）：
* backing store 只在**重新量字**時才依當下的 dpr 重建；
* 同值指派 fontSize 會被忽略，所以「先跳開再回來」是唯一叫得動它的方式；
* 改字級**不會**順便重新 fit（欄列數不變）——fit 是 window resize 才跑的。
`__scale` 是「建立畫布時用的 dpr」，把它設成 dpr 的兩倍就重現了使用者遇到的畫面：
字被畫成一半大小，要手動按一下 +/- px 才會對。
"""

STUB = """<!doctype html><meta charset="utf-8"><body style="margin:0;background:#111">
<div class="xterm"><div class="xterm-screen">
<canvas class="xterm-link-layer"></canvas><canvas></canvas></div></div>
<script>
const CB = [];
let font = 14;
window.__remeasures = 0;                 // 重新量字的次數（驗「沒事別亂動」）
window.term = {
  options: {
    get fontSize() { return font; },
    set fontSize(v) { if (v === font) return; font = v; remeasure(); },
  },
  cols: 0, rows: 0,
  onResize(cb) { CB.push(cb); },
};
function paint(scale) {
  document.querySelectorAll(".xterm canvas").forEach((c) => {
    c.style.width = window.innerWidth + "px";
    c.style.height = window.innerHeight + "px";
    c.width  = Math.round(window.innerWidth  * scale);
    c.height = Math.round(window.innerHeight * scale);
  });
}
function remeasure() { window.__remeasures++; paint(window.devicePixelRatio || 1); }
function fit() {
  const cols = Math.max(2, Math.floor(window.innerWidth  / (font * 0.6)));
  const rows = Math.max(1, Math.floor(window.innerHeight / (font * 1.2)));
  paint(window.devicePixelRatio || 1);   // fit 之後畫布一定是對的
  if (cols === window.term.cols && rows === window.term.rows) return;
  window.term.cols = cols; window.term.rows = rows;
  CB.forEach((cb) => cb({ cols, rows }));
}
window.addEventListener("resize", fit);
fit();
paint(__SCALE__);                        // 起始狀態：畫布與 CSS 尺寸脫節
__FONT_AFTER__                           // 見下方 stub_font_after 的說明（預設空字串）
</script>"""
