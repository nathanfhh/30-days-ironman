/*
 * localStorage / sessionStorage 的安全存取。
 *
 * Safari 全面封鎖 cookie、企業政策、無痕模式下，`localStorage` 的**存取本身**就會
 * throw——不是回 null。舊版 app.js 為此吃過一次虧（初始化第一行拋出，同一個 handler
 * 裡後面全部不執行，而且沒有任何錯誤訊息），所以這裡照樣一律包起來。
 */

export function lsGet(key: string, fallback: string | null = null): string | null {
  try {
    return localStorage.getItem(key) ?? fallback;
  } catch {
    return fallback;
  }
}

export function lsSet(key: string, value: string): boolean {
  try {
    localStorage.setItem(key, value);
    return true;
  } catch {
    return false;
  }
}

export function lsDel(key: string): boolean {
  try {
    localStorage.removeItem(key);
    return true;
  } catch {
    return false;
  }
}

/** 讀一份 JSON。壞掉、不是物件、或存取失敗都回 fallback——存過壞資料不該讓畫面壞掉。 */
export function lsJson<T>(key: string, fallback: T): T {
  const raw = lsGet(key);
  if (!raw) return fallback;
  try {
    const v = JSON.parse(raw) as unknown;
    return v && typeof v === "object" && !Array.isArray(v) ? (v as T) : fallback;
  } catch {
    return fallback;
  }
}
