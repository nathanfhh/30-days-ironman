import { reactive } from "vue";

import { ApiError } from "@/api/client";

/* ── 通知 toast ────────────────────────────────────────────────────────────────
 * 右上角堆疊，附倒數進度條，滑鼠移上去暫停。
 *
 * **倒數就是進度條那個 CSS animation 本身**，不另外開 setTimeout（舊版 app.js 的決定，
 * 原樣沿用）。這樣「hover 暫停」只要一行 `animation-play-state: paused` 就成立，而且
 * 畫面上的進度條與真正的剩餘時間永遠一致。關閉時機由 animationend 決定。
 *
 * 這個模組只管**有哪些 toast**；DOM 與動畫在 `components/ToastStack.vue`。
 */

export const TOAST_LEVELS: Record<string, string> = {
  info: "fa-circle-info",
  success: "fa-circle-check",
  warning: "fa-triangle-exclamation",
  danger: "fa-circle-exclamation",
  // 舊呼叫端用的字眼，映射過去（一次改完所有呼叫點反而容易漏）
  ok: "fa-circle-check",
  error: "fa-circle-exclamation",
};
const TOAST_ALIAS: Record<string, string> = { ok: "success", error: "danger" };

export interface ToastItem {
  id: number;
  title: string;
  body: string;
  level: string;
  duration: number;
  pausable: boolean;
  shown: boolean;
  closing: boolean;
}

export const toasts = reactive<ToastItem[]>([]);

let seq = 0;

export interface ToastOptions {
  body?: string;
  duration?: number;
  /**
   * 進度條是否可被 hover 暫停。倒數代表「時間到就會發生某件事」時必須傳 false——
   * 那個動作由別的計時器決定，暫停進度條只會讓畫面與事實不符。
   */
  pausable?: boolean;
}

export function toast(
  title: string,
  level = "info",
  { body = "", duration = 5000, pausable = true }: ToastOptions = {},
): ToastItem | undefined {
  if (!title) return undefined;
  const kind = TOAST_ALIAS[level] || (TOAST_LEVELS[level] ? level : "info");
  const item: ToastItem = {
    id: ++seq,
    title,
    body,
    level: kind,
    duration,
    pausable,
    shown: false,
    closing: false,
  };
  toasts.push(item);
  return item;
}

/** 收掉一則。重複呼叫是安全的（進度條跑完與手動關閉可能同時發生）。 */
export function dismissToast(id: number): void {
  const i = toasts.findIndex((t) => t.id === id);
  if (i < 0) return;
  toasts.splice(i, 1);
}

/**
 * 錯誤的統一呈現：標題講「哪個動作失敗」，內文放後端原文。
 *
 * ⚠ **401 一個字都不講**（回 undefined）。cookie 在伺服端被作廢的那一刻，當下所有在飛的
 *   請求會同時拿到 401，而它們各自的呼叫端都會走到這裡：畫面上會一次堆出「列表讀取失敗／
 *   未登入」「讀取 ttyd 實況失敗／未登入」這種對使用者毫無資訊的字，而**唯一該讀的那一則**
 *   （lib/unauthorized 發的「登入已失效，請重新登入」）被埋在裡面。
 *   401 不是這個動作失敗，是登入狀態沒了，那件事有它自己的一則通知與一次導覽。
 * ⚠ 擋在這裡而不是各個 catch 裡：新增一顆按鈕的人不必記得補這件事（同 SessionsView 的
 *   `handleRowError` 那條理由）。真的想在 401 時說點什麼的呼叫端，自己 `toast()` 即可。
 * ⚠ `opts` 是為了讓「本來就手寫 toast」的呼叫端搬得過來而不必改掉它們的 duration
 *   （抽屜的上傳失敗刻意留 8 秒，那則要讀的是後端說的原因）。`body` 不開放覆寫：它就是
 *   後端原文，這個函式存在的理由之一就是不讓呼叫端各自改寫它。
 */
export function toastError(
  action: string,
  err: unknown,
  opts: Omit<ToastOptions, "body"> = {},
): ToastItem | undefined {
  if (err instanceof ApiError && err.status === 401) return undefined;
  const message = err instanceof Error ? err.message : String(err);
  return toast(`${action}失敗`, "danger", { body: message, ...opts });
}

/* ── 跨頁通知 ─────────────────────────────────────────────────────────────────
 * 寄一則給「下一次整份 app 重新載入」的人（`main.ts` 進站時 `drainPendingToast()` 去取）。
 * 用 sessionStorage 而非 localStorage：訊息屬於「這個分頁的這一次操作」。
 *
 * ⚠ **現在沒有任何生產呼叫端。** 原本的兩個都不在了：legacy 與 vue 並存期間的互跳隨
 *   legacy 於 2026-08-26 拆掉；登出那則「已登出」曾經寄放在這裡，但登出是 SPA 內換頁、
 *   `main.ts` 不會再跑一次，於是那則通知一直躺著等到下一次整頁重載才在一個無關的時機
 *   冒出來（2026-08-26 修，改成直接 `toast()`）。唯一還在的整頁跳轉是改完密碼那條
 *   （PasswordPanel 的 `location.href`），而它選擇**先顯示夠久再跳**，也用不到寄放。
 *   留著機制而不是刪掉，是因為「離開 SPA 的跳轉」這個形狀還在；要不要拆是另一個決定。
 */
const PENDING_TOAST_KEY = "claude-pty:pending-toast";

export function toastAfterNav(title: string, level = "info", body = ""): void {
  try {
    sessionStorage.setItem(PENDING_TOAST_KEY, JSON.stringify({ title, level, body }));
  } catch {
    /* 無痕模式等情境下 storage 不可用——不顯示通知即可，別擋住流程 */
  }
}

/** 取出上一頁寄放的通知並顯示。只該出現一次，所以先移除再顯示。 */
export function drainPendingToast(): void {
  let raw: string | null = null;
  try {
    raw = sessionStorage.getItem(PENDING_TOAST_KEY);
    if (raw) sessionStorage.removeItem(PENDING_TOAST_KEY);
  } catch {
    return;
  }
  if (!raw) return;
  try {
    const t = JSON.parse(raw) as { title: string; level?: string; body?: string };
    toast(t.title, t.level ?? "info", { body: t.body ?? "" });
  } catch {
    /* 內容壞掉就當作沒有 */
  }
}
