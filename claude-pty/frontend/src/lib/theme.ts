import { lsGet, lsSet } from "./storage";

/* ── 主題：JSON → CSS custom properties ────────────────────────────────────────
 * 主題檔只描述語意色（surface / text / border / accent / signal），不碰版面。
 * 新增主題＝在 static/themes/ 放一個 JSON 並加進 THEMES，不需改任何 CSS。
 * 這一份是舊版 app.js 同名函式的逐條移植，**行為與 key 都不變**（兩版並存期間共用
 * 同一份 localStorage 快取，改 key 會讓換版時閃一次預設主題）。
 */
export interface ThemeDef {
  id: string;
  name: string;
  mode: "dark" | "light";
  icon: string;
}

export const THEMES: ThemeDef[] = [
  { id: "instrument", name: "Instrument", mode: "dark", icon: "fa-solid fa-gauge-high" },
  { id: "daylight", name: "Daylight", mode: "light", icon: "fa-solid fa-sun" },
  { id: "vellum", name: "Vellum", mode: "dark", icon: "fa-solid fa-mug-hot" },
];

export const THEME_STORAGE_KEY = "claude-pty:theme";
// ⚠ SYNC：index.html 的 <head> inline script 用同一組 key 做首屏套色
export const THEME_VARS_KEY = "claude-pty:theme-vars:";

export type ThemeColors = Record<string, string>;

/** 取一個主題的色票。**先讀 localStorage 的快取**，沒有才 fetch。 */
export async function loadThemeColors(id: string): Promise<ThemeColors | null> {
  if (id === "instrument") return null; // 預設主題＝CSS 內建值，沒有色票要套
  const cached = lsGet(THEME_VARS_KEY + id);
  if (cached) {
    try {
      return JSON.parse(cached) as ThemeColors;
    } catch {
      /* 壞掉就當沒有，往下重抓 */
    }
  }
  const res = await fetch(`/static/themes/${encodeURIComponent(id)}.json`);
  if (!res.ok) return null;
  const theme = (await res.json()) as { colors?: ThemeColors };
  return theme.colors ?? {};
}

/** 把色票套到 :root。**純同步、只碰 style**——這是 View Transition 的 callback 要執行的
 *  唯一工作，多一件事都會延長畫面凍結的時間（見 applyTheme）。 */
export function paintTheme(id: string, colors: ThemeColors | null): void {
  const root = document.documentElement;
  if (id === "instrument") {
    for (const prop of Array.from(root.style)) {
      if (prop.startsWith("--color-")) root.style.removeProperty(prop);
    }
    delete root.dataset.theme;
    return;
  }
  root.dataset.theme = id;
  for (const [key, value] of Object.entries(colors ?? {})) {
    root.style.setProperty(`--color-${key}`, value);
  }
}

/** 記住選擇與色票。localStorage 是**同步磁碟 I/O**，所以它必須在過渡之外做。 */
export function persistTheme(id: string, colors: ThemeColors | null): void {
  if (colors) lsSet(THEME_VARS_KEY + id, JSON.stringify(colors));
  lsSet(THEME_STORAGE_KEY, id);
}

/** 不做過渡的套用（初始化、prefers-reduced-motion）。 */
export async function setThemeVars(id: string): Promise<void> {
  const colors = await loadThemeColors(id);
  paintTheme(id, colors);
  persistTheme(id, colors);
}

export const prefersReducedMotion = (): boolean =>
  Boolean(globalThis.matchMedia?.("(prefers-reduced-motion: reduce)").matches);

export interface Origin {
  x: number;
  y: number;
}

/**
 * 套用主題，並以 View Transition 做「從點擊處擴散的同心圓」過渡。
 *
 * ⚠ **過渡的 callback 一定要是同步的、而且只做改樣式這一件事。** startViewTransition
 *   會等 callback 的 promise，等待期間整個頁面是一張凍結的靜態圖——那是主執行緒上的
 *   停頓，一定看得見。所以 fetch 色票與 localStorage 都搬到過渡之外先做完。
 */
export async function applyTheme(id: string, origin?: Origin | null): Promise<void> {
  const startViewTransition = (
    document as Document & {
      startViewTransition?: (cb: () => void) => { ready: Promise<void> };
    }
  ).startViewTransition;
  if (!startViewTransition || prefersReducedMotion() || !origin) {
    await setThemeVars(id);
    return;
  }
  const colors = await loadThemeColors(id);
  persistTheme(id, colors);
  const { x, y } = origin;
  // 半徑＝圓心到四個角的最大距離，確保漣漪能覆蓋整個視窗
  const radius = Math.hypot(
    Math.max(x, globalThis.innerWidth - x),
    Math.max(y, globalThis.innerHeight - y),
  );
  const transition = startViewTransition.call(document, () => paintTheme(id, colors));
  try {
    await transition.ready;
    document.documentElement.animate(
      {
        clipPath: [`circle(0px at ${x}px ${y}px)`, `circle(${radius}px at ${x}px ${y}px)`],
      },
      {
        duration: 520,
        easing: "cubic-bezier(0.4, 0, 0.2, 1)",
        pseudoElement: "::view-transition-new(root)",
      },
    );
  } catch {
    /* 過渡被中斷（如連續切換）：主題已套用，忽略即可 */
  }
}

/** 頁面起來時把存下來的主題套上去（不做動畫）。 */
export async function initTheme(): Promise<string> {
  const saved = lsGet(THEME_STORAGE_KEY) || "instrument";
  if (saved !== "instrument") await setThemeVars(saved);
  return saved;
}
