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

/* ⚠ 這裡曾經有一組「跨頁通知」（`toastAfterNav` ／ `drainPendingToast`）：把一則訊息寄在
 *   sessionStorage，等下一次整份 app 重新載入時取出來顯示。2026-08-30 拆掉，因為它的
 *   兩個呼叫端先後消失、而且都不是意外：legacy 與 vue 並存期間的互跳隨 legacy 於
 *   2026-08-26 拆除；登出那則「已登出」是 SPA 內換頁，寄放的話會躺到下一次整頁重載才在
 *   無關的時機冒出來，同日改成直接 `toast()`。全站唯一還在的整頁跳轉是改完密碼那條
 *   （PasswordPanel 的 `location.href`），而它選擇先顯示夠久再跳，也用不到寄放。
 *   要再用的時候重寫是十行；留著的成本是一組沒有人呼叫的匯出、一支測試，以及三處
 *   「為什麼這裡不用它」的註解。
 */
