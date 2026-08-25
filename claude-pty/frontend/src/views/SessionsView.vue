<script setup lang="ts">
/*
 * Session 列表頁。對照舊版 `server/templates/sessions.html`。
 *
 * 目前在哪個頁籤由**網址**決定，不是只存在記憶體裡——這樣重新整理、加到書籤、或把連結
 * 貼給別人，看到的都是同一個畫面。切換頁籤與改條件都用 `router.replace`（不該在瀏覽器
 * 的上一頁堆一疊）。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import { api, ApiError } from "@/api/client";
import AppShell from "@/components/AppShell.vue";
import CreatePanel from "@/components/CreatePanel.vue";
import FilterBar from "@/components/FilterBar.vue";
import ManifestList from "@/components/ManifestList.vue";
import TerminalDrawer from "@/components/TerminalDrawer.vue";
import { activeFilterKeys, filterParams } from "@/lib/filters";
import type { Credentials } from "@/stores/site";
import { dialog } from "@/lib/dialog";
import type { SessionRow } from "@/lib/sessions";
import { toast, toastError } from "@/lib/toast";
import { useSiteStore } from "@/stores/site";

const store = useSiteStore();
const route = useRoute();
const router = useRouter();

interface ListResponse {
  sessions: SessionRow[];
  total: number;
  limit: number;
  offset: number;
  credentials?: Credentials;
}

const rows = ref<SessionRow[]>([]);
const total = ref(0);
const offset = ref(0);
const pageSize = ref<number | null>(null); // 由後端回應決定，前端不自作主張
const loading = ref(true);
const error = ref<string | null>(null);
const swapping = ref(false);
const listUpdated = ref("");
const filtersOpen = ref(false);
const drawer = ref<{ sid: string; label: string; path: string; flavor: string | null } | null>(
  null,
);

const showHistory = computed(() => route.query.tab === "past");
const activeCount = computed(() => activeFilterKeys(route.query).length);

/* 自動刷新會整段重繪列表。若使用者的指標正停在列表上，那多半是「正要按某一列的動作鍵」
   ——重繪會把那顆按鈕連同 DOM 節點一起換掉，點擊就落空了。這種失敗完全無聲：使用者只
   覺得「我明明點了啊」。定時刷新是為了對齊漂移的狀態，晚 15 秒完全無所謂，讓路給人。 */
function pointerOnList(): boolean {
  const el = document.getElementById("manifest");
  if (!el) return false;
  return el.matches(":hover") || el.contains(document.activeElement);
}

async function refresh(retried = false, auto = false): Promise<void> {
  if (auto && pointerOnList()) return; // 下一輪再說
  try {
    const qs = new URLSearchParams({ offset: String(offset.value) });
    if (pageSize.value) qs.set("limit", String(pageSize.value)); // 第一次不帶，讓後端說了算
    for (const [k, v] of Object.entries(filterParams(route.query))) qs.set(k, v);
    const data = await api<ListResponse>(
      `/api/sessions${showHistory.value ? "/history" : ""}?${qs}`,
    );
    pageSize.value = data.limit;
    // 終止或對帳清掉整頁後，停在空白的尾頁很怪；退到最後一頁重抓（只退一次，避免遞迴）
    if (!data.sessions.length && data.offset > 0 && data.total > 0 && !retried) {
      offset.value = Math.floor((data.total - 1) / data.limit) * data.limit;
      return await refresh(true);
    }
    offset.value = data.offset;
    rows.value = data.sessions;
    total.value = data.total;
    error.value = null;
    // 招牌上的憑證徽章搭列表的順風車更新（兩支列表端點都會帶 credentials）。
    // 沒有它的話，憑證是在「這一頁開著的期間」到期的，畫面就永遠停在載入當下的狀態。
    store.setCredentials(data.credentials);
    // 列表每 15 秒才對齊一次，標上取得時刻才知道眼前這份有多新
    listUpdated.value = `擷取於 ${new Date().toLocaleTimeString("zh-TW", { hour12: false })}`;
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
    // 自動刷新每 15 秒一次，失敗時列表區的文字可能在畫面外——再發一則 toast，
    // 免得使用者一直看著一份沒在更新的舊資料卻不知道
    if (!auto) toastError("列表讀取", e);
  } finally {
    loading.value = false;
  }
}

const pagerHidden = computed(() => total.value <= (pageSize.value ?? 0) && offset.value === 0);
const pageFrom = computed(() => (total.value ? offset.value + 1 : 0));
const pageTo = computed(() => offset.value + rows.value.length);

function prevPage(): void {
  offset.value = Math.max(0, offset.value - (pageSize.value ?? 0));
  void refresh();
}
function nextPage(): void {
  offset.value += pageSize.value ?? 0;
  void refresh();
}

async function selectTab(past: boolean): Promise<void> {
  if (showHistory.value === past) return; // 點目前這一頁不該重整、不該把捲軸拉回去
  // 先淡出再換內容：直接替換內容會硬生生閃一下。等一個 transition 的時間（與
  // --transition-fast 對齊）就夠，不必等 transitionend——那在分頁被切到背景時不會觸發。
  swapping.value = true;
  await new Promise((r) => setTimeout(r, 120));
  const query = { ...route.query };
  if (past) query.tab = "past";
  else delete query.tab; // 執行中是預設值，不必留在網址裡
  await router.replace({ path: route.path, query });
  offset.value = 0;
  await refresh();
  swapping.value = false; // 內容就位了才淡回來
}

// tabs 的鍵盤約定：左右方向鍵在頁籤之間移動
function onTabKeydown(e: KeyboardEvent): void {
  if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
  e.preventDefault();
  const id = e.key === "ArrowRight" ? "tab-past" : "tab-live";
  document.getElementById(id)?.focus();
  void selectTab(id === "tab-past");
}

/* 這一整類失敗都在說同一件事：**畫面上這一列已經不是現在的真相**。
 *   404 → 那場已經結束並被歸檔；409 → 登錄還在但 container 已經不在。
 * 不論使用者按的是哪一顆按鈕，這時列表都該立刻重新拉一次。
 * ⚠ 放在**共用的處理**而不是各別的 action 裡：新增一顆按鈕的人不必記得補這件事。 */
async function handleRowError(action: string, ex: unknown): Promise<void> {
  toastError(action, ex);
  if (ex instanceof ApiError && (ex.status === 404 || ex.status === 409)) {
    await refresh().catch(() => {});
  }
}

async function onRename(row: SessionRow): Promise<void> {
  try {
    const name = await dialog({
      title: "重新命名",
      body: `給這個 session 一個好認的名字（最多 ${store.meta.nameMax} 字，留空則顯示 ID）。`,
      confirmText: "儲存",
      confirmIcon: "fa-pen",
      input: {
        value: row.display_name ?? "",
        allowEmpty: true,
        maxLength: store.meta.nameMax,
        placeholder: "例：重構登入流程",
      },
    });
    if (name === null) {
      toast("已取消重新命名", "info");
      return;
    }
    const renamed = await api<{ display_name?: string | null }>(`/api/sessions/${row.id}`, {
      method: "PATCH",
      body: { name },
    });
    toast("已更名", "success", {
      body: name
        ? `這個 session 現在叫「${renamed.display_name}」`
        : `已取消命名，改回顯示 ID ${row.id}`,
    });
    await refresh();
  } catch (ex) {
    await handleRowError("操作", ex);
  }
}

async function onKill(row: SessionRow): Promise<void> {
  // 取了名字之後只報 id 等於要人自己去對照；名字與 container 都寫出來才確認得了
  const label = row.display_name || row.id;
  try {
    const ok = await dialog({
      title: "終止 Session",
      body:
        `將移除「${label}」的 container（${row.container || row.id}）。` +
        `對話不會消失，之後仍可用 /resume 續接。`,
      confirmText: "終止",
      danger: true,
      confirmIcon: "fa-circle-stop",
    });
    if (!ok) {
      toast("已取消", "info", { body: `${label} 仍在執行` });
      return;
    }
    await api(`/api/sessions/${row.id}`, { method: "DELETE" });
    toast(`已終止 ${label}`, "warning", { body: "對話沒有消失，之後仍可用 /resume 續接" });
    await refresh();
  } catch (ex) {
    await handleRowError("操作", ex);
  }
}

async function onOpen(row: SessionRow, e: MouseEvent): Promise<void> {
  try {
    // 先 POST 起 view：失敗（沒有可用 port、session 已不在）在這裡就會拋出，
    // 比讓 iframe 靜靜地被導回首頁清楚得多。
    const view = await api<{ path: string; direct_url: string; ttyd_flavor?: string | null }>(
      `/api/sessions/${row.id}/view`,
      { method: "POST" },
    );
    // 按住 ⌘/Ctrl 仍然開新分頁——這顆鍵本來就是開分頁的，習慣不該被拿走
    const wantsTab = e.metaKey || e.ctrlKey;
    // 直連 Flask（未走 nginx）時只有跨 origin 的 direct_url，會被本站 CSP 擋在 iframe 外，
    // 所以那個模式一律開分頁。
    if (store.meta.behindProxy && !wantsTab) {
      drawer.value = {
        sid: row.id,
        label: row.display_name || row.id,
        path: view.path,
        flavor: view.ttyd_flavor ?? null,
      };
    } else {
      // ⚠ 帶 noopener 時 window.open **一定**回傳 null（規範如此），不代表被攔截。
      //   真被攔截時瀏覽器自己會顯示提示，那是它的職責。
      globalThis.open(store.meta.behindProxy ? view.path : view.direct_url, "_blank", "noopener");
      toast("已開啟終端", "info", { body: "在新分頁中；若沒跳出請檢查瀏覽器的彈出視窗攔截設定" });
    }
  } catch (ex) {
    await handleRowError("操作", ex);
  }
}

function onCreated(): void {
  offset.value = 0; // 新的排在最前面（created_at desc），翻回第一頁才看得到
  void refresh();
}

function onFiltersChanged(): void {
  // 換條件就回第一頁：留在第 3 頁而結果只剩 1 頁的話會看到空白，那不是「沒有資料」
  offset.value = 0;
  void refresh();
}

let timer: ReturnType<typeof setInterval> | null = null;

onMounted(() => {
  // 一進來就有條件（從書籤或分享的連結進來）的話，把篩選列展開——收合著會讓人
  // 以為看到的是全部
  if (activeCount.value) filtersOpen.value = true;
  void refresh();
  // 狀態會漂移（container 自行結束），定期對齊。auto=true 讓它在使用者正操作列表時讓路。
  timer = setInterval(() => void refresh(false, true), 15000);
});

onBeforeUnmount(() => {
  if (timer) clearInterval(timer);
});

// 看歷史時把建立表單收起來：那是「現在要開一個」的動作，跟「回顧過去」不同語境
watch(showHistory, () => {
  offset.value = 0;
});
</script>

<template>
  <AppShell>
    <!-- ⚠ 看歷史時把建立表單**收起來**（`hidden`），不是把它從 DOM 上拿掉。舊版就是
         `document.getElementById("create-panel").hidden = past`——用 v-if 的話 `#create-panel`
         在歷史那一頁**整個不存在**，而 e2e 與 aria golden 是拿舊版那份來比的。
         順帶：元件一直掛著，`/api/catalog` 也就只打一次，與舊版（整頁的 script 一律跑一次）
         同一個行為。 -->
    <CreatePanel :hidden="showHistory" @created="onCreated" />

    <section style="margin-top: var(--space-6)">
      <div class="section-head">
        <!-- 執行中與已結束是同一份清單的兩種篩選，不是兩個功能——tabs 表達的是「切換視角」，
             比一顆按下去才知道對面有什麼的按鈕誠實。
             篩選鍵貼著頁籤放：兩者是同一件事的兩個維度，而且它控制的面板就在正下方。 -->
        <div class="section-head__lead">
          <div class="tabs" role="tablist" aria-label="Session 檢視">
            <button
              class="tabs__tab"
              role="tab"
              id="tab-live"
              :aria-selected="showHistory ? 'false' : 'true'"
              data-testid="tab-live"
              @click="selectTab(false)"
              @keydown="onTabKeydown"
            >
              <i class="fa-solid fa-play"></i> 執行中
            </button>
            <button
              class="tabs__tab"
              role="tab"
              id="tab-past"
              :aria-selected="showHistory ? 'true' : 'false'"
              data-testid="tab-past"
              @click="selectTab(true)"
              @keydown="onTabKeydown"
            >
              <i class="fa-solid fa-clock-rotate-left"></i> 已結束
            </button>
          </div>
          <!-- 收合時仍要看得出「你正在看的是被篩過的資料」——那是最容易誤判的地方，
               所以生效數字直接掛在按鈕上，而不是只在展開後才看得到。 -->
          <button
            class="btn"
            id="filter-toggle"
            :aria-expanded="filtersOpen ? 'true' : 'false'"
            aria-controls="filter-bar"
            data-testid="filter-toggle"
            :data-active="activeCount ? '1' : ''"
            @click="filtersOpen = !filtersOpen"
          >
            <i class="fa-solid fa-filter"></i> 篩選<span id="filter-count">{{
              activeCount ? ` · ${activeCount}` : ""
            }}</span>
          </button>
        </div>
        <div class="section-head__actions">
          <span class="section-head__note" id="list-updated">{{ listUpdated }}</span>
          <button class="btn" id="refresh-btn" @click="refresh()">
            <i class="fa-solid fa-rotate"></i> 重新整理
          </button>
        </div>
      </div>

      <FilterBar :open="filtersOpen" @changed="onFiltersChanged" />

      <ManifestList
        :rows="rows"
        :offset="offset"
        :historical="showHistory"
        :is-admin="Boolean(store.user?.is_admin)"
        :gitlab-enabled="store.meta.gitlabEnabled"
        :error="error"
        :loading="loading"
        :swapping="swapping"
        @rename="onRename"
        @open="onOpen"
        @kill="onKill"
      />

      <!-- 只有一頁就不佔版面 -->
      <div class="pager" id="pager" :hidden="pagerHidden">
        <button class="btn" id="prev-btn" :disabled="offset <= 0" @click="prevPage">
          <i class="fa-solid fa-chevron-left"></i> 上一頁
        </button>
        <span class="pager__status" id="pager-status" data-testid="pager-status">
          <b>{{ pageFrom }}</b
          >–<b>{{ pageTo }}</b> / 共 <b>{{ total }}</b> 筆
        </span>
        <button class="btn" id="next-btn" :disabled="pageTo >= total" @click="nextPage">
          下一頁 <i class="fa-solid fa-chevron-right"></i>
        </button>
      </div>
    </section>

    <TerminalDrawer
      v-if="drawer"
      :sid="drawer.sid"
      :label="drawer.label"
      :path="drawer.path"
      :flavor="drawer.flavor"
      @close="drawer = null"
    />
  </AppShell>
</template>
