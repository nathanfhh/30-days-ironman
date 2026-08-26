/**
 * 「多久以前」。⚠ 這是全站唯一一份說法——session 列表與帳號清單都用它，兩邊各寫一份
 * 遲早會漂移成「3 分鐘前」與「3 分前」。刻意不引入 dayjs 之類的相依：為了四個分支背
 * 一整包函式庫不划算（舊版 app.js 的同一個決定，理由沿用）。
 */
export function relTime(iso: string): string {
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  if (diff < 60) return `${Math.floor(diff)} 秒前`;
  if (diff < 3600) return `${Math.floor(diff / 60)} 分鐘前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小時前`;
  return `${Math.floor(diff / 86400)} 天前`;
}

/** 絕對時刻，使用者本地時區。伺服端一律送 UTC ISO，格式化只在這裡做。 */
export function absTime(iso: string): string {
  return new Date(iso).toLocaleString("zh-TW", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

/** 把秒數說成人看得懂的長度。用於啟動耗時與使用時長，兩者量級差很多（秒 ↔ 小時）。 */
export function span(
  fromIso: string | null | undefined,
  toIso: string | null | undefined,
): string | null {
  if (!fromIso || !toIso) return null;
  const sec = (new Date(toIso).getTime() - new Date(fromIso).getTime()) / 1000;
  if (!isFinite(sec) || sec < 0) return null;
  if (sec < 60) return `${sec < 10 ? sec.toFixed(1) : Math.round(sec)} 秒`;
  if (sec < 3600) return `${Math.floor(sec / 60)} 分 ${Math.round(sec % 60)} 秒`;
  return `${Math.floor(sec / 3600)} 小時 ${Math.round((sec % 3600) / 60)} 分`;
}
