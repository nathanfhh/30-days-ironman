<script setup lang="ts">
/*
 * 帳號清單（管理員限定）。
 *
 * ⚠ 這裡刻意**只有「重設密碼」一顆動作鈕**。帳號的退場就是它：改掉密碼＝cookie 全滅＋
 *   他登不回來＋**嘗試**收掉當下追蹤得到的終端。
 *   ⚠ 這不等於「停用帳號」：他的容器繼續跑、他存的憑證與 per-user proxy 也還在，而收終端
 *     有可能失敗（後端會回報）。
 *   沒有刪除（ADR 0010：刪除會 cascade 掉 session 登錄、斷稽核鏈），也沒有事後提權／降權
 *   ——權限在建立時決定，設錯就再建一個對的。
 * ⚠ 分頁的樣式與 session 列表共用同一個 `.pager`，不另立一套：同一個系統裡「翻頁」應該
 *   長得一樣、按鍵在同一個位置。
 */
import { computed, ref } from "vue";

import { api } from "@/api/client";
import { dialog } from "@/lib/dialog";
import { relTime } from "@/lib/time";
import { toast, toastError } from "@/lib/toast";
import { useSiteStore } from "@/stores/site";

interface RosterUser {
  id: number;
  username: string;
  is_admin: boolean;
  created_at: string;
}

interface RosterResponse {
  users: RosterUser[];
  total: number;
  limit: number;
  offset: number;
}

const store = useSiteStore();

const users = ref<RosterUser[]>([]);
const total = ref(0);
const offset = ref(0);
const pageSize = ref<number | null>(null); // 由後端回應決定，前端不自作主張
const error = ref<string | null>(null);

const pagerHidden = computed(
  () => error.value !== null || (total.value <= (pageSize.value ?? 0) && offset.value === 0),
);
const pageFrom = computed(() => (total.value ? offset.value + 1 : 0));
const pageTo = computed(() => offset.value + users.value.length);

async function load(): Promise<void> {
  try {
    const data = await api<RosterResponse>(`/api/users?offset=${offset.value}`);
    // ⚠ 以後端回報的 offset 為準，不是送出去的那個。超出範圍時後端會回一頁空的，
    //   拿本地變數去算頁碼會顯示成「11–10 / 共 3 筆」。
    offset.value = data.offset;
    pageSize.value = data.limit;
    total.value = data.total;
    users.value = data.users;
    error.value = null;
  } catch (ex) {
    error.value = ex instanceof Error ? ex.message : String(ex);
    toastError("帳號清單讀取", ex);
  }
}

/** 把 offset 移到 username 所在的那一頁。查不到就不動（清單照樣會重畫）。 */
async function gotoUserPage(username: string): Promise<void> {
  if (!pageSize.value) return;
  try {
    // /options 回的是同一組排序的完整名單，所以名字的索引就是它在清單裡的位置
    const { users: all } = await api<{ users: { username: string }[] }>("/api/users/options");
    const i = all.findIndex((u) => u.username === username);
    if (i >= 0) offset.value = Math.floor(i / pageSize.value) * pageSize.value;
  } catch {
    /* 翻不過去就留在原頁，不值得為此讓建立帳號看起來失敗 */
  }
}

function prev(): void {
  offset.value = Math.max(0, offset.value - (pageSize.value ?? 0));
  void load();
}
function next(): void {
  offset.value += pageSize.value ?? 0;
  void load();
}

async function resetPassword(u: RosterUser): Promise<void> {
  try {
    const pw = await dialog({
      title: "重設密碼",
      body: `為 ${u.username} 設定新密碼（至少 ${store.meta.minPasswordLength} 字元）`,
      confirmText: "重設",
      confirmIcon: "fa-key",
      input: { type: "password", placeholder: "新密碼" },
    });
    if (!pw) {
      toast("已取消", "info", { body: `${u.username} 的密碼未變動` });
      return;
    }
    const r = await api<{ views_failed?: boolean } | null>(`/api/users/${u.id}/password`, {
      method: "POST",
      body: { new_password: pw },
    });
    // ⚠ 這條是「讓某個人退場」最常走的路，收不乾淨更不能報成功。
    if (r && r.views_failed) {
      // 這條不跳頁，所以可以 hover 暫停補讀；但訊息比 success 長，時間要給足。
      toast(`已重設 ${u.username} 的密碼，但終端沒有收乾淨`, "warning", {
        body:
          "他的登入已經失效，但有終端沒收掉；那些連線在收掉之前仍然可以打字。" +
          "請再按一次，或直接終止他那幾場 session。",
        duration: 9000,
      });
    } else {
      // ⚠ 範圍限定在「這次追蹤到的」，不要寫成「開著的終端都失效了」。
      toast(`已重設 ${u.username} 的密碼`, "success", {
        body: "他既有的登入已立刻失效，這次追蹤到的互動終端也都收掉了",
      });
    }
  } catch (ex) {
    toastError("操作", ex);
  }
}

/** 建完帳號之後：清單依名字排序又分頁，新帳號很可能不在目前這一頁——建完卻看不到他，
 *  看起來就跟沒建成一樣。翻到他所在的那一頁再重畫。 */
async function afterCreate(username: string): Promise<void> {
  await gotoUserPage(username);
  await load();
}

const when = (iso: string): string => iso.slice(0, 16).replace("T", " ");

defineExpose({ load, afterCreate });
</script>

<template>
  <section class="panel">
    <h2 class="panel__title">帳號清單</h2>
    <table class="roster" data-testid="roster-table">
      <thead>
        <tr>
          <th>ID</th>
          <th>使用者名稱</th>
          <th>權限</th>
          <th>建立時間</th>
          <th></th>
        </tr>
      </thead>
      <tbody id="roster-body" data-testid="roster">
        <!-- prettier-ignore -->
        <tr v-if="error"><td colspan="5">讀取失敗：{{ error }}</td></tr>
        <!-- prettier-ignore -->
        <tr v-else-if="!users.length"><td colspan="5">載入中…</td></tr>
        <tr v-for="u in users" v-else :key="u.id">
          <td class="mono-id">{{ u.id }}</td>
          <td>
            <!-- prettier-ignore -->
            <span class="roster__name" data-testid="roster-name" :title="u.username">{{ u.username }}</span>
          </td>
          <td>
            <span v-if="u.is_admin" class="chip" data-tone="accent">admin</span>
            <span v-else class="chip">user</span>
          </td>
          <!-- 絕對時間回答「是哪一天」，相對時間回答「多久以前」——找特定帳號時用前者，
               判斷「這是不是剛剛新增的」用後者，兩個問題都常問，所以兩個都給。 -->
          <td class="roster__when">
            <span>{{ when(u.created_at) }}</span>
            <span class="roster__ago">{{ relTime(u.created_at) }}</span>
          </td>
          <!-- ⚠ `display:flex` 要掛在 td **裡面**的 div，不能掛在 td 自己身上：td 一旦變成
               flex container 就不再是 table-cell，那一列的底線會在這一格前面斷掉。 -->
          <td>
            <div class="row-actions">
              <button
                class="btn"
                data-act="reset"
                :data-id="u.id"
                :data-name="u.username"
                @click="resetPassword(u)"
              >
                <i class="fa-solid fa-key"></i> 重設密碼
              </button>
            </div>
          </td>
        </tr>
      </tbody>
    </table>
    <!-- 只有一頁時整條收起來 -->
    <div class="pager" id="roster-pager" data-testid="roster-pager" :hidden="pagerHidden">
      <button
        class="btn"
        id="roster-prev"
        data-testid="roster-prev"
        :disabled="offset <= 0"
        @click="prev"
      >
        <i class="fa-solid fa-chevron-left"></i> 上一頁
      </button>
      <!-- prettier-ignore -->
      <span class="pager__status" id="roster-status" data-testid="roster-status"><b>{{ pageFrom }}</b>–<b>{{ pageTo }}</b> / 共 <b>{{ total }}</b> 筆</span>
      <button
        class="btn"
        id="roster-next"
        data-testid="roster-next"
        :disabled="pageTo >= total"
        @click="next"
      >
        下一頁 <i class="fa-solid fa-chevron-right"></i>
      </button>
    </div>
  </section>
</template>
