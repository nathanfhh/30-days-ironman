import { reactive } from "vue";

/* ── 對話框：取代原生 confirm() / prompt()（外觀不受控、且被瀏覽器擋在頁面之外）──
 *
 * 介面刻意與舊版 app.js 的 `dialog()` 一模一樣（回傳 Promise，取消是 null），
 * 呼叫端一個字都不必改寫。DOM 在 `components/DialogHost.vue`。
 */

export interface DialogInput {
  value?: string;
  placeholder?: string;
  maxLength?: number;
  type?: string;
  /** 讓「清空」成為有效答案（例如取消命名），而不是被當成按了取消。 */
  allowEmpty?: boolean;
}

export interface DialogOptions {
  title: string;
  body?: string;
  confirmText?: string;
  confirmIcon?: string | null;
  danger?: boolean;
  input?: DialogInput | null;
  pre?: string | null;
  preNoWrap?: boolean;
  viewOnly?: boolean;
  wide?: boolean;
}

export type DialogAnswer = string | boolean | null;

export interface DialogEntry extends DialogOptions {
  id: number;
  draft: string;
  resolve: (v: DialogAnswer) => void;
}

export const dialogs = reactive<DialogEntry[]>([]);

let seq = 0;

export function dialog(options: DialogOptions): Promise<DialogAnswer> {
  return new Promise((resolve) => {
    dialogs.push({
      confirmText: "確定",
      danger: false,
      input: null,
      pre: null,
      preNoWrap: false,
      viewOnly: false,
      wide: false,
      confirmIcon: null,
      body: "",
      ...options,
      id: ++seq,
      draft: options.input?.value ?? "",
      resolve,
    });
  });
}

export function settleDialog(id: number, answer: DialogAnswer): void {
  const i = dialogs.findIndex((d) => d.id === id);
  if (i < 0) return;
  const [entry] = dialogs.splice(i, 1);
  entry.resolve(answer);
}
