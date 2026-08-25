/**
 * 送出中要 disable，而且要 finally。
 *
 * 舊版 account.html 的四個表單原本都是 `await api(...)` 直接接 toast，沒有任何 in-flight
 * 狀態。最尖的是改密碼：伺服端 argon2 本來就慢，成功後又等一段時間才跳頁，那段時間內再
 * 按一次，第二發帶的是**已經失效的舊密碼**，畫面上會同時出現成功 toast 與「原密碼錯誤」。
 *
 * ⚠ 一定要 `finally`：失敗時沒有解鎖的話，使用者改完錯誤還得重新整理才能再送一次。
 *
 * Vue 版把「那顆按鈕是誰」交給呼叫端的 ref，不像舊版去 querySelector——按鈕就在同一個
 * 元件裡，繞一圈去 DOM 找只是把一個編譯期看得到的關係變成執行期才知道。
 */
import type { Ref } from "vue";

export function submitting(flag: Ref<boolean>, fn: () => Promise<void>): () => Promise<void> {
  return async () => {
    if (flag.value) return;
    flag.value = true;
    try {
      await fn();
    } finally {
      flag.value = false;
    }
  };
}
