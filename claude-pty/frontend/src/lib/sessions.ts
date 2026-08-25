import { absTime, relTime } from "./time";

/* session 列表的純資料函式。全部逐條移植自舊版 `sessions.html` 的內嵌腳本，
 * 抽成模組是為了單獨測得到——那些判斷（三態 telemetry、GitLab 的兩個事實、新鮮度）
 * 正是「畫錯了會說謊」的地方，不該只能靠瀏覽器測試守。 */

export interface SessionProfile {
  cli?: string;
  model?: string;
  effort?: string;
  network?: string;
  capture?: boolean;
  telemetry?: boolean;
  telemetry_active?: boolean;
}

export interface SessionRow {
  id: string;
  display_name?: string | null;
  container?: string | null;
  state: string;
  ready?: boolean;
  owner?: string | null;
  profile?: SessionProfile;
  created_at: string;
  ready_at?: string | null;
  last_active_at?: string | null;
  ended_at?: string | null;
  ended_reason?: string | null;
  state_checked_at?: string | null;
  gitlab_proxy?: boolean | null;
  gitlab_pat_set?: boolean | null;
}

export const END_REASON: Record<string, string> = {
  terminated: "使用者終止",
  exited: "自行結束",
  gone: "container 消失",
  idle: "閒置回收",
};

export const CLI_BRAND: Record<string, string> = { claude: "anthropic" };

// 每個 profile 面向都固定出一個 chip（開與關都出），關的狀態才看得出來是「刻意關掉」
// 而不是「這版沒有這個功能」。文案與建立表單的選項一致。
const NET_CHIP: Record<string, [string, string, string]> = {
  restricted: ["限制", "fa-solid fa-shield-halved", "accent"],
  unrestricted: ["開放", "fa-solid fa-globe", ""],
};

/** 一般的文字 chip（擁有者 / CLI / 模型 / 思考深度）。 */
export interface TextChip {
  text: string;
  tone: string;
  icon?: string;
  brand?: string;
}

/** 純圖示的標記 chip。`kind` 是給自動化測試的識別欄位——tone 是共用的、圖示 class 又是
 *  Font Awesome 的實作細節，兩者都不足以回答「這顆是哪一種標記」。 */
export interface MarkChip {
  title: string;
  icon: string;
  tone: string;
  kind: string;
}

export interface ChipSet {
  lead: TextChip[];
  marks: MarkChip[];
  tail: TextChip[];
}

export interface ChipContext {
  isAdmin: boolean;
  gitlabEnabled: boolean;
  historical?: boolean;
}

export function chipsOf(s: SessionRow, ctx: ChipContext): ChipSet {
  const p = s.profile ?? {};
  const historical = ctx.historical ?? false;
  const lead: TextChip[] = [];
  // 一般使用者只看得到自己的 session，每一列都掛自己的名字只是浪費空間
  if (ctx.isAdmin && s.owner)
    lead.push({ text: s.owner, tone: "owner", icon: "fa-solid fa-user chip__icon" });
  if (p.cli) lead.push({ text: p.cli, tone: `cli-${p.cli}`, brand: CLI_BRAND[p.cli] });

  /* 模型與思考深度用文字而非圖示：「opus / high」本身就是要讀的資訊。
     ⚠ 它們接在 telemetry **後面**，與新增表單的欄位順序一致。同一組設定在兩個地方
       若是兩種順序，對照時得重新找一次。 */
  const tail: TextChip[] = [];
  if (p.model) {
    tail.push({ text: p.model, tone: "model", icon: "fa-solid fa-gem chip__icon" });
    if (p.effort)
      tail.push({ text: p.effort, tone: "effort", icon: "fa-solid fa-gauge-high chip__icon" });
  }

  const marks: MarkChip[] = [];
  if (p.network) {
    const [label, icon, tone] = NET_CHIP[p.network] ?? [p.network, "fa-solid fa-question", ""];
    marks.push({ title: `網路：${label}`, icon, tone, kind: "network" });
  }
  marks.push(
    p.capture
      ? { title: "流量錄製：開", icon: "fa-solid fa-circle-dot", tone: "accent", kind: "capture" }
      : { title: "流量錄製：關", icon: "fa-solid fa-circle-dot", tone: "off", kind: "capture" },
  );
  /* ⚠ 這個座標的用途是**事後比對**，所以不准說謊。控制平面在建立 session 時就探過
       Jaeger 通不通，把結果記在 telemetry_active。三態要分得出來——把「沒開成」畫成
       「送」會讓人以為有 trace 在收、查的時候才發現沒有，那比不顯示更糟。
       舊列沒有 telemetry_active 這個鍵（undefined）：誠實的措辭是「已要求」。 */
  if (!p.telemetry) {
    marks.push({
      title: "Telemetry：不送",
      icon: "fa-solid fa-chart-line",
      tone: "off",
      kind: "telemetry",
    });
  } else if (p.telemetry_active === true) {
    marks.push({
      title: "Telemetry：送 Jaeger（建立時探測可達）",
      icon: "fa-solid fa-chart-line",
      tone: "accent",
      kind: "telemetry",
    });
  } else if (p.telemetry_active === false) {
    marks.push({
      title: "Telemetry：要求了但沒開成（建立時 Jaeger 探不到，已降級不送）",
      icon: "fa-solid fa-chart-line",
      tone: "warn",
      kind: "telemetry",
    });
  } else {
    marks.push({
      title: "Telemetry：已要求送 Jaeger（實際是否送出以容器 log 為準）",
      icon: "fa-solid fa-chart-line",
      tone: "accent",
      kind: "telemetry",
    });
  }

  /* GitLab 代理。**這一顆要讀兩個事實，只看任何一個都會說謊**：
   *   · s.gitlab_proxy   ＝這場**當初**有沒有接上代理的網路（不可變）。
   *   · s.gitlab_pat_set ＝擁有者**現在**還有沒有 token。
   * ⚠ **null／undefined 一律不畫。** 那是這個欄位上線前建立的舊列，事實是「不知道」。
   * ⚠ 歷史那張表**只有一個事實**：session 都結束了，沒有「現在能不能用」可言。 */
  const proxied = s.gitlab_proxy;
  if (ctx.gitlabEnabled && proxied !== null && proxied !== undefined) {
    if (!proxied) {
      marks.push({
        title: historical
          ? "GitLab：期間未啟用"
          : "GitLab：本場沒有——開場時沒接上代理網路，事後補 token 也救不了這一場（網路要在容器啟動前接），要用就開新的一場",
        icon: "fa-brands fa-gitlab",
        tone: "off",
        kind: "gitlab",
      });
    } else if (historical) {
      marks.push({
        title: "GitLab：期間曾啟用",
        icon: "fa-brands fa-gitlab",
        tone: "accent",
        kind: "gitlab",
      });
    } else if (s.gitlab_pat_set) {
      marks.push({
        title: "GitLab：本場可用",
        icon: "fa-brands fa-gitlab",
        tone: "accent",
        kind: "gitlab",
      });
    } else {
      marks.push({
        title:
          "GitLab：本場當初接上了代理網路，但你現在沒有設 token——git 與 API 都會失敗。到帳號頁填回去，一個對帳週期內會自己恢復",
        icon: "fa-brands fa-gitlab",
        tone: "warn",
        kind: "gitlab",
      });
    }
  }

  return { lead, marks, tail };
}

// 啟動耗時的合理上限。超過就值得注意——最常見的原因是 restricted profile 沒命中
// trivy DB 快取，得重抓約 1GB 的 DB（實測 36 秒 vs 命中時的 1 秒）。
export const SLOW_BOOT_SECONDS = 10;

// 狀態新鮮度：超過這麼久沒跟 dockerd 求證就標出來。對帳每 30 秒一輪，取它的 4 倍
// ——連錯兩輪還可以說是巧合，連錯四輪就是 reconciler 出事了。
export const STALE_STATE_SECONDS = 120;

export interface Freshness {
  text: string;
  tip: string;
  stale: "0" | "1";
}

/** 這一列的狀態是幾點跟 dockerd 求證來的（ADR 0012）。 */
export function freshness(iso: string | null | undefined, nowIso: string): Freshness {
  // ⚠ 沒問到過**不可以**顯示成「剛剛確認」。NULL 的意思是「從來沒問到」——把它畫成
  //   新鮮的，等於用最有信心的樣子說謊。
  if (!iso) {
    return { text: "未確認", stale: "1", tip: "還沒有向 dockerd 求證過這一列的狀態" };
  }
  const sec = (new Date(nowIso).getTime() - new Date(iso).getTime()) / 1000;
  const stale = isFinite(sec) && sec > STALE_STATE_SECONDS ? "1" : "0";
  return {
    text: `${relTime(iso)}確認`,
    stale,
    tip: `狀態於 ${absTime(iso)} 向 dockerd 求證${stale === "1" ? "；已經超過兩分鐘沒更新，對帳可能卡住了" : ""}`,
  };
}

export interface LiveState {
  /** container 起來了但 driver 還沒 */
  booting: boolean;
  lamp: string;
  lampTitle: string;
  state: [string, string, string];
}

/** 就緒是「CLI 真的可用了」而非「container 在跑」，值得自己一欄。ready 的依據是 log 裡的
 *  標記，container 結束後標記仍在——所以不在跑的一律顯示結束狀態。 */
export function liveState(s: SessionRow): LiveState {
  const booting = s.state === "running" && !s.ready;
  let state: [string, string, string] = ["ready", "就緒", "fa-solid fa-circle-check"];
  if (s.state !== "running") {
    state = ["ended", s.state === "exited" ? "已結束" : s.state, "fa-solid fa-circle-xmark"];
  } else if (booting) {
    state = ["booting", "啟動中", "fa-solid fa-spinner fa-spin"];
  }
  return {
    booting,
    lamp: booting ? "creating" : s.state,
    lampTitle: booting ? `${s.state} · 啟動中` : s.state,
    state,
  };
}
