/**
 * 控制平面 API 的封裝。行為對照舊版 `static/js/app.js` 的 `api()`，兩處差異都是刻意的：
 *
 *   1. 401 不再 `location.href = "/login"`（那是整頁重載），改成呼叫一個可注入的處理器，
 *      由 router 做 SPA 導向。**語意不變**：cookie 過期就回登入頁。
 *   2. 錯誤物件帶 `status`，與舊版相同（呼叫端要分辨 404/409，不可以拿中文訊息做判斷）。
 */

export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

type Unauthorized = () => void;

// 預設仍然是整頁跳轉：main.ts 會在 router 準備好之後換成 SPA 導向。
// 這樣「還沒掛上 router 就收到 401」也有正確行為，而不是靜靜地什麼都不做。
let onUnauthorized: Unauthorized = () => {
  globalThis.location.href = "/login";
};

export function setUnauthorizedHandler(fn: Unauthorized): void {
  onUnauthorized = fn;
}

export interface ApiOptions {
  method?: string;
  body?: unknown;
}

export async function api<T = unknown>(
  path: string,
  { method = "GET", body }: ApiOptions = {},
): Promise<T> {
  const res = await fetch(path, {
    method,
    /*
     * ⚠ `X-Requested-With` **無條件送**，不是只在有 body 時送。沒有 body 的變更請求
     *   （DELETE /api/sessions/<sid> 等）就是靠它通過後端的 CSRF 閘門
     *   （見 server/app.py 的 `_require_json_for_writes`）。這個標頭不在 CORS 安全列表
     *   裡，所以 no-cors 送不出去、`<form>` 也設不了。
     */
    headers: body
      ? { "Content-Type": "application/json", "X-Requested-With": "fetch" }
      : { "X-Requested-With": "fetch" },
    body: body === undefined ? undefined : JSON.stringify(body),
    credentials: "same-origin",
  });

  if (res.status === 401) {
    onUnauthorized();
    throw new ApiError("未登入", 401);
  }
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try {
      const data = (await res.json()) as { error?: string } | null;
      if (data && data.error) msg = data.error;
    } catch {
      /* 非 JSON 回應就沿用狀態碼 */
    }
    throw new ApiError(msg, res.status);
  }
  return (res.status === 204 ? null : await res.json()) as T;
}
