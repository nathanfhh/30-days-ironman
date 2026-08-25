<script setup lang="ts">
/*
 * 建立表單。逐條移植自舊版 `sessions.html`，每一段的理由一併搬過來。
 *
 * profile 面向排成兩列，分法是語意的、不是為了排版：
 *   第一列 AI Agent / 模型 / 思考深度——「要跑的是什麼」。
 *   第二列 網路能力 / 流量錄製 / Telemetry——「在什麼環境裡跑」，三個各自獨立。
 */
import { computed, onMounted, ref } from "vue";

import { api } from "@/api/client";
import { lsJson, lsSet } from "@/lib/storage";
import { dismissToast, toast, toastError } from "@/lib/toast";
import { useSiteStore } from "@/stores/site";

import SitePicker, { type PickerOption } from "./SitePicker.vue";
import SiteSwitch from "./SiteSwitch.vue";

const emit = defineEmits<{ created: [] }>();

const store = useSiteStore();

interface CatalogModel {
  slug: string;
  display_name: string;
  efforts: string[];
  default_effort: string;
  deprecated?: boolean;
}
interface CatalogEntry {
  models: CatalogModel[];
  default_model: string;
  source: string;
  fetched_at: string | null;
}
type Catalog = Record<string, CatalogEntry>;

const EFFORT_ICON: Record<string, string> = {
  low: "fa-solid fa-gauge-simple",
  medium: "fa-solid fa-gauge-simple-high",
  high: "fa-solid fa-gauge",
  xhigh: "fa-solid fa-gauge-high",
  max: "fa-solid fa-fire",
};
const MODEL_ICON: Record<string, string> = {
  opus: "fa-solid fa-gem",
  sonnet: "fa-solid fa-feather",
  fable: "fa-solid fa-wand-magic-sparkles",
  haiku: "fa-solid fa-leaf",
};
// claude 那幾顆的補充說明（純呈現，不進 API——後端只負責「哪些值合法」）。
// 四個字各佔一個軸，不要兩顆講同一件事：最強＝能力、快＝速度、新＝世代、省＝成本。
const MODEL_HINT: Record<string, string> = { opus: "最強", sonnet: "快", fable: "新", haiku: "省" };

/* 記住上次選的模型與深度。退路是後端給的 default_model，不是「清單的第一個」。
 * ⚠ 這份記憶**跨重整活著**（存 localStorage）：開 session 的人多半連續開好幾場同樣的
 *   設定。存的是「選過什麼」而不是「該用什麼」——取用時一律先確認它還在當前清單裡。 */
const PICK_KEY = "claude-pty:model-pick";
interface LastPick {
  claude?: { model?: string; effort?: string };
}
const lastPick = lsJson<LastPick>(PICK_KEY, {});

const catalog = ref<Catalog | null>(null);
// 目錄還在路上時，模型與思考深度**還不是使用者的選擇**——初值是寫死的預設。
const catalogLoading = ref(true);
const modelSource = ref("");
const modelSourceTone = ref("");

// AI Agent：目前只有 Claude。畫成選單而不是一行字，是因為它是「這一場跑的是誰」的答案，
// 而那一格在版面上撐著第一列的主詞。值不參與 payload——這裡是顯示，不是設定來源。
const cliOptions: PickerOption[] = [
  { value: "claude", label: "Claude", icon: "fa-solid fa-robot" },
];
const cli = ref("claude");

// 模型與思考深度：這裡寫死的只是目錄還沒載到前的暫時內容，載到後整份換掉。
const modelOptions = ref<PickerOption[]>([
  { value: "opus", label: "Opus", icon: MODEL_ICON.opus, hint: "最強" },
  { value: "sonnet", label: "Sonnet", icon: MODEL_ICON.sonnet, hint: "快" },
  { value: "fable", label: "Fable", icon: MODEL_ICON.fable, hint: "新" },
]);
const model = ref("opus");

// ⚠ label 一律是值本身，不做首字大寫：effort 是機器值（`--effort xhigh`），
//   選單裡看到什麼就該是能貼進指令的那個字。
const effortOptions = ref<PickerOption[]>(
  ["low", "medium", "high", "xhigh", "max"].map((e) => ({
    value: e,
    label: e,
    icon: EFFORT_ICON[e],
  })),
);
const effort = ref("high");

// 這三個都只有兩種狀態，用開關而非下拉。off 一律是**安全 / 低副作用**的那一端，
// 所以「全關」＝限制出網、不錄製、不送 telemetry，正是預設值。
const network = ref("restricted");
const capture = ref("0");
const telemetry = ref("0");
// ⚠ off 一樣是安全那端：fd 不讓憑證進環境變數。on 是**逃生口**，不是「另一種風格」。
const tokenDelivery = ref("fd");

const name = ref("");
const submitting = ref(false);

const currentModel = computed(() =>
  catalog.value?.claude?.models.find((m) => m.slug === model.value),
);

// 沒貼 CLI 憑證的話這一場一定開不起來（後端會擋）。按鈕做成**看起來停用、但按得下去**：
// 真的 disabled 的話它不發事件，使用者只會看到一顆按不動的按鈕、沒有人告訴他為什麼。
const credOk = computed(() => store.credentials[store.meta.defaultCli]?.ok !== false);

/** 目前選的與這顆模型的預設不同時，掛一個點提示——並講出預設是什麼，否則「非預設」
 *  這三個字本身不構成資訊（不知道基準在哪）。 */
const effortNote = computed(() => {
  const m = currentModel.value;
  if (!m) return "";
  return effort.value !== m.default_effort ? `● 非預設（此模型預設 ${m.default_effort}）` : "";
});

function remember(): void {
  lastPick.claude = { model: model.value, effort: effort.value };
  // 寫失敗不可以影響表單：無痕視窗、容量滿、或使用者關掉了儲存都會丟例外，而那些情況下
  // 「記不住」是可以接受的降級，「建不了 session」不是。
  lsSet(PICK_KEY, JSON.stringify(lastPick));
}

/** @param keepCurrent 目前畫面上的值是不是「使用者剛剛選的」。
 *
 *  ⚠ 只有**在同一個 CLI 裡換模型**時才是 true。其餘情況（初次載入）畫面上那個值只是
 *    寫死的預設，不是任何人的選擇——把它當成選擇的話，記憶失效時會停在 `high` 而不是
 *    這顆模型的預設，「沒有那個選項就跳回預設」等於沒有生效。 */
function rebuildEffort({ keepCurrent = false } = {}): void {
  const m = currentModel.value;
  if (!m) return;
  // 優先序：使用者剛選的（僅換模型時）→ 記憶裡的 → **這顆模型**的預設。
  const remembered = lastPick.claude?.effort;
  const keep =
    keepCurrent && m.efforts.includes(effort.value)
      ? effort.value
      : remembered && m.efforts.includes(remembered)
        ? remembered
        : m.default_effort;
  // 標出這顆模型的預設：每顆不一樣，不標的話使用者無從判斷「我把它調高/調低了多少」。
  effortOptions.value = m.efforts.map((e) => ({
    value: e,
    label: e,
    icon: EFFORT_ICON[e] || "fa-solid fa-gauge",
    hint: e === m.default_effort ? "預設" : "",
  }));
  effort.value = keep;
}

function rebuildModels(): void {
  const cat = catalog.value?.claude;
  if (!cat) return; // 目錄還沒載到：維持現況
  const models = cat.models ?? [];
  const remembered = lastPick.claude?.model;
  const cur =
    models.find((m) => m.slug === remembered) ??
    models.find((m) => m.slug === cat.default_model) ??
    models[0];
  modelOptions.value = models.map((m) => ({
    value: m.slug,
    label: m.display_name,
    icon: MODEL_ICON[m.slug] || "fa-solid fa-microchip",
    // 上游已預告要淘汰的——選之前看得到
    hint: m.deprecated ? "即將淘汰" : (MODEL_HINT[m.slug] ?? ""),
  }));
  if (cur) model.value = cur.slug;
  rebuildEffort();
  // 目錄的來源要誠實講：離線後備是「猜的」，過期的是「真的但舊」——兩者都不該看起來
  // 像剛抓的（同列表那顆 state_checked_at 的紀律，ADR 0012）。
  modelSource.value =
    cat.source === "fallback"
      ? "離線後備清單，可能過時"
      : cat.source === "stale"
        ? `目錄取自 ${cat.fetched_at ?? "未知時間"}，尚未更新`
        : "";
  modelSourceTone.value = cat.source === "fallback" ? "warn" : "";
}

function onModelChange(): void {
  // 先重建（effort 清單依模型而定），再記下來。keepCurrent：換模型時人剛調好的深度
  // 只要新模型也支援就留著，不要無故跳回預設。
  rebuildEffort({ keepCurrent: true });
  remember();
}

async function submit(): Promise<void> {
  if (!credOk.value) {
    toast("還沒設定 CLI 憑證", "warning", {
      body: "到帳號管理頁貼上 claude setup-token 的輸出，才開得了 session",
    });
    return;
  }
  submitting.value = true;
  // 建立要等 container 起來（restricted 遇上 trivy DB 過期可能數十秒）。先講一聲，
  // 免得使用者以為按鈕沒反應而重按。duration 拉長：它要陪整段等待。
  const pending = toast("正在建立 session…", "info", {
    body: "容器啟動需要一點時間，請稍候",
    duration: 60000,
  });
  try {
    const s = await api<{ id: string; display_name?: string | null }>("/api/sessions", {
      method: "POST",
      body: {
        name: name.value || null,
        profile: {
          cli: "claude",
          network: network.value,
          capture: capture.value === "1",
          telemetry: telemetry.value === "1",
          model: model.value,
          effort: effort.value,
          token_delivery: tokenDelivery.value,
        },
      },
    });
    toast(`已建立 ${s.display_name || s.id}`, "success", { body: "就緒後點「終端」即可開始互動" });
    name.value = "";
    emit("created");
  } catch (ex) {
    toastError("建立 session", ex);
  } finally {
    // 不論成敗，「建立中」都該讓位給結果
    if (pending) dismissToast(pending.id);
    submitting.value = false;
  }
}

onMounted(async () => {
  /* ⚠ 只鎖到「目錄有結果」為止，**成功與失敗都要解鎖**（所以是 finally 不是 then）。
     目錄載不到不可以讓表單壞掉：畫面維持寫死的預設值，照樣建得起 session（後端也會
     擋住不合法的組合）。漏掉失敗那條的話，一個外部依賴掛掉就等於整張表單永遠按不下去。 */
  try {
    catalog.value = await api<Catalog>("/api/catalog");
    rebuildModels();
  } catch (ex) {
    modelSource.value = `模型清單讀取失敗：${ex instanceof Error ? ex.message : String(ex)}`;
    modelSourceTone.value = "warn";
  } finally {
    catalogLoading.value = false;
  }
});
</script>

<template>
  <section class="panel" id="create-panel">
    <h2 class="panel__title">開一個新 Session</h2>
    <form id="create-form" @submit.prevent="submit">
      <div class="form-row" style="--form-col-min: 20rem">
        <div class="field">
          <span class="label">AI Agent</span>
          <SitePicker id="pick-cli" v-model="cli" :options="cliOptions" />
        </div>
        <div class="field">
          <!-- 清單來自 /api/catalog；要有地方講「這份是哪來的」——離線後備是猜的、
               過期的是真的但舊，兩者都不該看起來像剛抓的。 -->
          <span class="label">模型</span>
          <SitePicker
            id="pick-model"
            v-model="model"
            :options="modelOptions"
            :disabled="catalogLoading"
            @change="onModelChange"
          />
          <span
            class="field__hint"
            id="model-source"
            data-testid="model-source"
            :data-tone="modelSourceTone"
            >{{ modelSource }}</span
          >
        </div>
        <div class="field">
          <!-- 每顆模型的預設深度不同。選了非預設值時要看得出來——不然「我到底有沒有
               動過它」只能靠記憶。 -->
          <span class="label">思考深度</span>
          <SitePicker
            id="pick-effort"
            v-model="effort"
            :options="effortOptions"
            :disabled="catalogLoading"
            @change="remember"
          />
          <span
            class="field__hint"
            id="effort-note"
            data-testid="effort-note"
            :data-tone="effortNote ? 'accent' : ''"
            >{{ effortNote }}</span
          >
        </div>
      </div>
      <div class="form-row" style="--form-col-min: 20rem">
        <div class="field">
          <span class="label">網路能力</span>
          <SiteSwitch
            id="pick-network"
            v-model="network"
            off="restricted"
            on="unrestricted"
            name="網路能力"
            off-label="限制（白名單）"
            on-label="完全開放"
            off-icon="fa-shield-halved"
            on-icon="fa-globe"
            hint="可任意連外"
          />
        </div>
        <div class="field">
          <span class="label">流量錄製</span>
          <SiteSwitch
            id="pick-capture"
            v-model="capture"
            off="0"
            on="1"
            name="流量錄製"
            off-label="不錄製"
            on-label="錄製流量"
            off-icon="fa-video-slash"
            on-icon="fa-video"
            hint="mitmproxy"
          />
        </div>
        <div class="field">
          <span class="label">Telemetry</span>
          <SiteSwitch
            id="pick-telemetry"
            v-model="telemetry"
            off="0"
            on="1"
            name="Telemetry"
            off-label="不送"
            on-label="送 Jaeger"
            off-icon="fa-chart-line"
            on-icon="fa-chart-line"
            hint="探不通就不送"
          />
        </div>
      </div>
      <div class="field">
        <label class="label" for="name">名稱</label>
        <input
          id="name"
          v-model="name"
          class="input"
          :maxlength="store.meta.nameMax"
          placeholder="例：重構登入流程"
        />
      </div>
      <!-- 憑證交付：**這不是偏好題，是逃生口。** 預設那條（檔案描述符）依賴一個官方沒有
           寫進文件的機制，所以要留一個當場切得回去的開關。hint 必須把**什麼時候該切**
           講出來，不能只說兩者差在哪。 -->
      <div class="field">
        <span class="label">憑證交付</span>
        <SiteSwitch
          id="pick-token-delivery"
          v-model="tokenDelivery"
          off="fd"
          on="env"
          name="憑證交付"
          off-label="檔案描述符"
          on-label="環境變數"
          off-icon="fa-shield-halved"
          on-icon="fa-rotate-left"
          hint="官方文件寫過的退路"
        />
        <span class="field__hint">
          預設不讓憑證進容器的環境變數。此法官方無文件、實測可用；若新 session
          開始要求登入，改用環境變數。
        </span>
      </div>
      <div class="form-actions form-actions--center">
        <button
          class="btn btn--primary"
          :class="{ 'btn--inert': !credOk }"
          type="submit"
          id="create-btn"
          :disabled="catalogLoading || submitting"
        >
          <template v-if="catalogLoading">
            <i class="fa-solid fa-spinner fa-spin"></i> 載入模型清單…
          </template>
          <template v-else-if="submitting">
            <i class="fa-solid fa-spinner fa-spin"></i> 建立中…
          </template>
          <template v-else> <i class="fa-solid fa-plus"></i> 建立 Session </template>
        </button>
      </div>
    </form>
  </section>
</template>
