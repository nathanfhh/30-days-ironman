/* 篩選條件的形狀。畫面與網址之間只有這一份對照表，兩邊各寫一份遲早會漂。 */

export const FILTER_KEYS = ["since", "from", "to", "network", "capture", "telemetry"] as const;
export type FilterKey = (typeof FILTER_KEYS)[number];

/** 「不限」一律用**空字串**，不是省略也不是 "all"：後端把空字串與缺席都當成不限
 *  （見 app._tri_bool），而空字串讓 picker 有一個實際可選的值。 */
export const ANY = "";
/** 時間範圍那格的「自訂區間」選項。⚠ 它**不是**送給後端的值——後端只收 `since=<天數>`
 *  或 `from`/`to`。它只活在 picker 的顯示狀態裡。 */
export const CUSTOM = "custom";

export type QueryLike = Record<string, unknown>;

export function queryString(query: QueryLike, key: string): string {
  const v = query[key];
  return typeof v === "string" ? v : "";
}

/**
 * 生效中的條件有幾個。
 *
 * ⚠ from 與 to 合計算一個——畫面上它們就是「時間範圍」那一格，填了起迄兩欄卻跳成 2，
 *   會讓人以為多套了一個看不見的條件。
 * ⚠ **這裡是這條規則唯一的實作。** 後端曾經有一份 `Filters.active()`，docstring 還寫著
 *   「畫面上的『篩選 · N』靠它」——但畫面從來沒打過它，那份已經刪了。要改算法就改這裡。
 */
export function activeFilterKeys(query: QueryLike): FilterKey[] {
  return FILTER_KEYS.filter((k) => k !== "to" && queryString(query, k) !== "");
}

/** 真的要送去後端的那幾個。空字串＝不限＝不必送。 */
export function filterParams(query: QueryLike): Record<string, string> {
  const out: Record<string, string> = {};
  for (const k of FILTER_KEYS) {
    const v = queryString(query, k);
    if (v) out[k] = v;
  }
  return out;
}
