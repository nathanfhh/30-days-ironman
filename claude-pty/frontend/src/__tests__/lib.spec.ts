import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, api, setUnauthorizedHandler } from "@/api/client";
import { activeFilterKeys, filterParams } from "@/lib/filters";
import { chipsOf, freshness, liveState, type SessionRow } from "@/lib/sessions";
import { lsDel, lsGet, lsJson, lsSet } from "@/lib/storage";
import { absTime, relTime, span } from "@/lib/time";
import { dismissToast, toast, toastError, toasts } from "@/lib/toast";

const row = (over: Partial<SessionRow> = {}): SessionRow => ({
  id: "abc123",
  state: "running",
  ready: true,
  created_at: new Date().toISOString(),
  ...over,
});

const ago = (sec: number): string => new Date(Date.now() - sec * 1000).toISOString();

const mark = (s: SessionRow, gitlabEnabled: boolean, historical = false) =>
  chipsOf(s, { isAdmin: false, gitlabEnabled, historical }).marks.find((m) => m.kind === "gitlab");

describe("lib/time", () => {
  it("相對時間分四段，秒／分／時／天", () => {
    expect(relTime(ago(5))).toBe("5 秒前");
    expect(relTime(ago(120))).toBe("2 分鐘前");
    expect(relTime(ago(7200))).toBe("2 小時前");
    expect(relTime(ago(86400 * 3))).toBe("3 天前");
  });

  it("絕對時刻是本地時區、24 小時制", () => {
    const s = absTime("2026-08-25T01:02:03Z");
    expect(s).toMatch(/2026/);
    expect(s).not.toMatch(/[AP]M/);
  });

  it("span：不足一分講秒、跨小時講小時；缺任何一端回 null", () => {
    expect(span("2026-08-25T00:00:00Z", "2026-08-25T00:00:05Z")).toBe("5.0 秒");
    expect(span("2026-08-25T00:00:00Z", "2026-08-25T00:02:30Z")).toBe("2 分 30 秒");
    expect(span("2026-08-25T00:00:00Z", "2026-08-25T02:30:00Z")).toBe("2 小時 30 分");
    expect(span(null, "2026-08-25T00:00:00Z")).toBeNull();
    // 終點早於起點是資料壞了，不是「負的長度」——不畫比畫一個負數好
    expect(span("2026-08-25T01:00:00Z", "2026-08-25T00:00:00Z")).toBeNull();
  });
});

describe("lib/storage", () => {
  beforeEach(() => localStorage.clear());

  it("讀寫與預設值", () => {
    expect(lsGet("nope", "fallback")).toBe("fallback");
    expect(lsSet("k", "v")).toBe(true);
    expect(lsGet("k")).toBe("v");
  });

  it("存過壞 JSON 或非物件都回 fallback（不讓畫面壞掉）", () => {
    lsSet("j", "{{{");
    expect(lsJson("j", { a: 1 })).toEqual({ a: 1 });
    lsSet("j", "[1,2]");
    expect(lsJson("j", { a: 1 })).toEqual({ a: 1 });
    lsSet("j", '{"a":2}');
    expect(lsJson("j", { a: 1 })).toEqual({ a: 2 });
  });

  it("storage 存取本身丟例外時也不能炸（無痕模式）", () => {
    const spy = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    expect(lsGet("k", "fallback")).toBe("fallback");
    spy.mockRestore();
  });

  it("🔴 寫入被擋下時回 false 而不是拋出去（呼叫端據此決定要不要退而求其次）", () => {
    const spy = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("QuotaExceededError");
    });
    expect(lsSet("k", "v")).toBe(false);
    spy.mockRestore();
    // 拋出去的話呼叫它的那一段後面全部不執行，而且畫面上沒有任何跡象
    expect(lsSet("k", "v")).toBe(true);
  });

  it("刪除同樣包起來：刪不掉不是理由讓整個初始化中斷", () => {
    lsSet("k", "v");
    expect(lsDel("k")).toBe(true);
    expect(lsGet("k")).toBeNull();
    // 不存在的鍵也算刪成功（removeItem 本來就是冪等的）
    expect(lsDel("nope")).toBe(true);
    const spy = vi.spyOn(Storage.prototype, "removeItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    expect(lsDel("k")).toBe(false);
    spy.mockRestore();
  });
});

describe("lib/filters", () => {
  it("from 與 to 合計一個條件（畫面上它們就是同一格）", () => {
    expect(activeFilterKeys({ from: "a", to: "b" })).toEqual(["from"]);
    expect(activeFilterKeys({ since: "7", network: "restricted" })).toEqual(["since", "network"]);
    expect(activeFilterKeys({ capture: "" })).toEqual([]);
  });

  it("空字串＝不限＝不必送給後端", () => {
    expect(filterParams({ since: "7", capture: "", tab: "past" })).toEqual({ since: "7" });
  });
});

describe("lib/sessions", () => {
  it("一般使用者看不到 owner chip，admin 看得到", () => {
    const s = row({ owner: "alice", profile: { cli: "claude" } });
    expect(chipsOf(s, { isAdmin: false, gitlabEnabled: false }).lead.map((c) => c.text)).toEqual([
      "claude",
    ]);
    expect(chipsOf(s, { isAdmin: true, gitlabEnabled: false }).lead.map((c) => c.text)).toEqual([
      "alice",
      "claude",
    ]);
  });

  it("模型與思考深度排在標記後面（與建立表單同一個順序）", () => {
    const s = row({ profile: { cli: "claude", model: "opus", effort: "high" } });
    const set = chipsOf(s, { isAdmin: false, gitlabEnabled: false });
    expect(set.tail.map((c) => c.text)).toEqual(["opus", "high"]);
    // 沒有模型就不該冒出一顆孤零零的 effort
    const noModel = chipsOf(row({ profile: { effort: "high" } }), {
      isAdmin: false,
      gitlabEnabled: false,
    });
    expect(noModel.tail).toEqual([]);
  });

  it("telemetry 是三態：不送／送成了／要求了但沒開成", () => {
    const tone = (profile: SessionRow["profile"]): string =>
      chipsOf(row({ profile }), { isAdmin: false, gitlabEnabled: false }).marks.find(
        (m) => m.kind === "telemetry",
      )!.tone;
    expect(tone({ telemetry: false })).toBe("off");
    expect(tone({ telemetry: true, telemetry_active: true })).toBe("accent");
    expect(tone({ telemetry: true, telemetry_active: false })).toBe("warn");
    // 舊列沒有這個鍵：誠實的措辭是「已要求」，不假裝知道結果
    expect(tone({ telemetry: true })).toBe("accent");
  });

  it("GitLab 標記：功能關掉不畫、null 不畫、接上了但沒 token 是 warn", () => {
    expect(mark(row({ gitlab_proxy: true }), false)).toBeUndefined();
    expect(mark(row({ gitlab_proxy: null }), true)).toBeUndefined();
    expect(mark(row({}), true)).toBeUndefined();
    expect(mark(row({ gitlab_proxy: true, gitlab_pat_set: true }), true)?.tone).toBe("accent");
    expect(mark(row({ gitlab_proxy: true, gitlab_pat_set: false }), true)?.tone).toBe("warn");
    expect(mark(row({ gitlab_proxy: false }), true)?.tone).toBe("off");
    // 歷史那張表只有一個事實，不看 token
    expect(mark(row({ gitlab_proxy: true }), true, true)?.title).toBe("GitLab：期間曾啟用");
  });

  it("沒問到過的狀態是「未確認」而不是「剛剛確認」", () => {
    const now = new Date().toISOString();
    expect(freshness(null, now)).toMatchObject({ text: "未確認", stale: "1" });
    expect(freshness(now, now).stale).toBe("0");
    const old = new Date(Date.now() - 300_000).toISOString();
    expect(freshness(old, now).stale).toBe("1");
    expect(freshness(old, now).tip).toContain("對帳可能卡住了");
  });

  it("running 但還沒 ready ＝啟動中；不在跑的一律顯示結束狀態", () => {
    expect(liveState(row({ state: "running", ready: false })).state[0]).toBe("booting");
    expect(liveState(row({ state: "running", ready: false })).lamp).toBe("creating");
    expect(liveState(row({ state: "running", ready: true })).state[0]).toBe("ready");
    expect(liveState(row({ state: "exited" })).state[1]).toBe("已結束");
  });
});

describe("lib/toast", () => {
  beforeEach(() => {
    toasts.splice(0, toasts.length);
    sessionStorage.clear();
  });

  it("舊呼叫端的字眼會被映射（ok→success、error→danger）", () => {
    toast("a", "ok");
    toast("b", "error");
    toast("c", "不存在的等級");
    expect(toasts.map((t) => t.level)).toEqual(["success", "danger", "info"]);
  });

  it("沒有標題就不發（空的通知只是一塊會動的空白）", () => {
    expect(toast("")).toBeUndefined();
    expect(toasts).toHaveLength(0);
  });

  it("重複收掉同一則是安全的", () => {
    const t = toast("x")!;
    dismissToast(t.id);
    dismissToast(t.id);
    expect(toasts).toHaveLength(0);
  });

  it("🔴 toastError 對 401 一個字都不講（那一則由全域處理器統一發）", () => {
    // cookie 被作廢的那一刻，所有在飛的請求會同時拿到 401；每個呼叫端各講一次的話，
    // 唯一該讀的那一則（「登入已失效」）會被埋在一堆「◯◯失敗／未登入」裡。
    expect(toastError("列表讀取", new ApiError("未登入", 401))).toBeUndefined();
    expect(toasts).toHaveLength(0);
  });

  it("401 以外照講，狀態碼不影響（403 是真的失敗，使用者要看到）", () => {
    toastError("操作", new ApiError("需要管理員權限", 403));
    toastError("讀取", new Error("連不上"));
    expect(toasts.map((t) => t.title)).toEqual(["操作失敗", "讀取失敗"]);
    expect(toasts[0]!.body).toBe("需要管理員權限");
  });
});

const respond = (init: { status: number; body?: unknown; statusText?: string }): void => {
  globalThis.fetch = vi.fn(async () =>
    init.status === 204
      ? new Response(null, { status: 204 })
      : new Response(JSON.stringify(init.body ?? {}), {
          status: init.status,
          statusText: init.statusText ?? "",
          headers: { "Content-Type": "application/json" },
        }),
  ) as typeof fetch;
};

describe("api/client", () => {
  beforeEach(() => {
    setUnauthorizedHandler(() => {});
  });

  it("沒有 body 的變更請求也要帶 X-Requested-With（CSRF 閘門靠它）", async () => {
    respond({ status: 204 });
    await api("/api/sessions/x", { method: "DELETE" });
    const init = (globalThis.fetch as unknown as { mock: { calls: [string, RequestInit][] } }).mock
      .calls[0][1];
    expect((init.headers as Record<string, string>)["X-Requested-With"]).toBe("fetch");
    expect(init.headers).not.toHaveProperty("Content-Type");
  });

  it("204 回 null，不去 parse 一個空 body", async () => {
    respond({ status: 204 });
    await expect(api("/api/x", { method: "POST", body: {} })).resolves.toBeNull();
  });

  it("401 走注入的處理器（SPA 導向），並拋出", async () => {
    respond({ status: 401 });
    const seen = vi.fn();
    setUnauthorizedHandler(seen);
    await expect(api("/api/x")).rejects.toThrow("未登入");
    expect(seen).toHaveBeenCalledOnce();
  });

  it("錯誤帶著狀態碼走：呼叫端要分辨 409，不可以拿中文訊息做判斷", async () => {
    respond({ status: 409, body: { error: "時機不對" } });
    await expect(api("/api/x")).rejects.toBeInstanceOf(ApiError);
    await api("/api/x").catch((e: ApiError) => {
      expect(e.status).toBe(409);
      expect(e.message).toBe("時機不對");
    });
  });

  it("非 JSON 的失敗回應沿用狀態碼當訊息", async () => {
    globalThis.fetch = vi.fn(
      async () => new Response("<html>oops</html>", { status: 502, statusText: "Bad Gateway" }),
    ) as typeof fetch;
    await expect(api("/api/x")).rejects.toThrow("502 Bad Gateway");
  });
});
