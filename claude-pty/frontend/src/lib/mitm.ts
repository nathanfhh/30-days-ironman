import { api } from "@/api/client";

interface MitmRelay {
  path: string;
}

/**
 * 先在 user gesture 內開空白分頁，再用明確的 POST 建立 relay。
 *
 * GET `/session/<sid>/mitm/` 會經 nginx auth_request；它只能查既有 relay，不能再負責建立。
 * 因此不能先 POST 完再 `window.open()`，否則瀏覽器可能把 async callback 判成 popup。
 * 空白分頁一開就解除 opener，POST 失敗則關閉它，避免留下可誤解的空白頁。
 */
export async function openMitm(sid: string): Promise<void> {
  const tab = globalThis.open("about:blank", "_blank");
  if (tab) {
    try {
      tab.opener = null;
    } catch {
      // 某些瀏覽器的 WindowProxy 不允許設定 opener；POST 仍可繼續，導向使用 replace。
    }
  }

  try {
    const relay = await api<MitmRelay>(`/api/sessions/${encodeURIComponent(sid)}/mitm`, {
      method: "POST",
    });
    if (tab) {
      tab.location.replace(relay.path);
    } else {
      // popup 被瀏覽器擋下時仍嘗試正常開啟，呼叫端的錯誤提示會提醒使用者檢查 popup 設定。
      globalThis.open(relay.path, "_blank", "noopener");
    }
  } catch (error) {
    tab?.close();
    throw error;
  }
}
