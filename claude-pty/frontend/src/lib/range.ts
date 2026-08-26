/* 日期區間選擇器的純函式。抽出來是為了單獨測得到——元件裡剩下的就只有 DOM 與事件。
 * 全部逐條移植自舊版 app.js 的 `_rp*` 系列，名字刻意保持一致，方便兩版對照。 */

export const RP_DOW = ["日", "一", "二", "三", "四", "五", "六"];

export const rpPad = (n: number): string => String(Math.floor(Math.abs(n))).padStart(2, "0");
export const rpDay = (d: Date): Date => new Date(d.getFullYear(), d.getMonth(), d.getDate());
export const rpMonth = (d: Date): Date => new Date(d.getFullYear(), d.getMonth(), 1);
export const rpAddMonths = (d: Date, n: number): Date =>
  new Date(d.getFullYear(), d.getMonth() + n, 1);
export const rpYmd = (d: Date): string =>
  `${d.getFullYear()}-${rpPad(d.getMonth() + 1)}-${rpPad(d.getDate())}`;
export const rpHm = (d: Date): string => `${rpPad(d.getHours())}:${rpPad(d.getMinutes())}`;
export const rpSameDay = (a: Date | null, b: Date | null): boolean =>
  !!(a && b) && rpDay(a).getTime() === rpDay(b).getTime();

/** 帶時區偏移的 ISO。後端只收帶時區的（見 app._iso_or_none），不猜是哪一區的牆上時間。 */
export function rpIso(d: Date): string {
  const off = -d.getTimezoneOffset();
  return `${rpYmd(d)}T${rpHm(d)}:00${off >= 0 ? "+" : "-"}${rpPad(off / 60)}:${rpPad(off % 60)}`;
}

/** 一個月曆格子要畫的 42 天（含前後月補格）。從當月 1 號回推到那一週的星期日。 */
export function rpCells(view: Date): Date[] {
  const first = rpMonth(view);
  const start = new Date(first);
  start.setDate(1 - first.getDay());
  return Array.from({ length: 42 }, (_, i) => {
    const d = new Date(start);
    d.setDate(start.getDate() + i);
    return d;
  });
}

/** 今天（當地時間的 00:00）。未來的日期不給選——這個區間篩的是**已經發生過的**
 *  session（建立時間／結束時間），挑未來查不到任何東西，只會讓人以為是壞了。 */
export const rpToday = (): Date => rpDay(new Date());
export const rpFuture = (d: Date): boolean => rpDay(d) > rpToday();

/* 左邊那個月最多只能到「上個月」，因為右邊永遠是它 +1——這樣右邊剛好停在本月，
   不會出現一整面全部反灰的未來月份。 */
export const rpMaxView = (): Date => rpAddMonths(rpMonth(new Date()), -1);
export const rpClampView = (v: Date): Date => (v > rpMaxView() ? rpMaxView() : v);

export function rpParse(s: string | null | undefined): Date | null {
  if (!s) return null;
  const d = new Date(s);
  return Number.isNaN(d.getTime()) ? null : d;
}
