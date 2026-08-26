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
 */
export function toastError(action: string, err: unknown): ToastItem | undefined {
  if (err instanceof ApiError && err.status === 401) return undefined;
  const message = err instanceof Error ? err.message : String(err);
  return toast(`${action}失敗`, "danger", { body: message });
}

/* ── 跨頁通知 ─────────────────────────────────────────────────────────────────
 * SPA 之內換頁不會清掉 toast，但**離開 SPA 的那幾條路仍在**（登出後回登入頁、legacy
 * 與 vue 兩版並存期間互相跳轉）。所以這一份寄放機制原樣保留。
 * 用 sessionStorage 而非 localStorage：訊息屬於「這個分頁的這一次操作」。
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
