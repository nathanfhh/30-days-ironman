<script setup lang="ts">
/*
 * ttyd 實況（管理員限定）。回答三個「DB 與 `docker ps` 都答不了」的問題：那顆行程在聽哪個
 * port、現在有幾個人連著、有沒有誰在跑卻不在 DB 裡。
 *
 * ⚠ 這一節是**唯讀**的，沒有任何動作鈕。它的用途是對帳，不是操作——要收掉哪一場走既有的
 *   終止那條路，那條有完整的擁有者檢查。
 * ⚠ 沒有 psutil 時**一定要喊**。不喊的話下面那個空的孤兒區看起來就像「掃過了，很乾淨」，
 *   而這一節整個存在的理由就是不讓「沒查到」被讀成「沒問題」。
 */
import { computed, ref } from "vue";

import { api } from "@/api/client";
import { relTime } from "@/lib/time";
import { toastError } from "@/lib/toast";

interface Proc {
  listening?: string[];
  clients?: number;
  bin?: string;
  started_at?: string | null;
}

interface ViewRow {
  owner?: string | null;
  session_id: string;
  session_name?: string | null;
  port: number;
  pid: number | null;
  alive: boolean | null;
  ttyd_bin?: string | null;
  created_at: string;
  proc?: Proc | null;
}

interface Orphan {
  pid: number;
  proc?: Proc | null;
}

interface Inspect {
  views: ViewRow[];
  orphans: Orphan[];
  psutil?: boolean;
}

const data = ref<Inspect | null>(null);
const failed = ref(false);

/* 「不知道」與「壞了」要長得不一樣。`alive === null` 是 pid 還沒寫回 DB 的那個窗口
   （開終端當下的例行狀態），把它畫成紅色就是每開一次終端就假警報一次。 */
type Cell = { kind: "chip" | "mono"; text: string; tone?: string; title?: string };

function aliveCell(row: ViewRow): Cell {
  if (row.alive === null) {
    return { kind: "chip", text: "建立中", title: "pid 還沒寫回 DB（開啟中），不是已結束" };
  }
  if (row.alive === false) {
    return {
      kind: "chip",
      text: "已結束",
      tone: "danger",
      title: "DB 有列、程序已結束。它佔著 uq_views_port，這場下次開終端會拿不到 port",
    };
  }
  return { kind: "mono", text: String(row.pid) };
}

/* ⚠ 這一格是這一節的重點：DB 說 port 是 X，那顆行程實際在聽的是不是 X。
   相同就只印 port，不同（或問不到）才出聲——每一列都掛一個綠勾等於沒有訊號。 */
type Listen = { kind: "ago" | "chip"; text: string; tone?: string; title?: string };

function listenCell(row: ViewRow): Listen {
  const proc = row.proc;
  if (!proc || !("listening" in proc) || proc.listening === undefined) {
    return row.alive === false
      ? { kind: "ago", text: "—" }
      : { kind: "chip", text: "不知道", title: "問不到那個行程的 socket" };
  }
  const ports = proc.listening.map((a) => Number(a.split(":").pop()));
  if (ports.includes(row.port)) {
    return {
      kind: "ago",
      text: "相符",
      title: `它在聽 ${proc.listening.join("、")}，與 DB 記的 ${row.port} 相同`,
    };
  }
  return {
    kind: "chip",
    text: "對不上",
    tone: "danger",
    title: `DB 記的是 ${row.port}，它實際在聽 ${proc.listening.join("、") || "（沒有在聽）"}`,
  };
}

const when = (iso: string): string => iso.slice(0, 16).replace("T", " ");

/* 一列一個 view model：`aliveCell()` / `listenCell()` 只算一次，樣板也就不必為了拿 title
   而對聯集型別做窄化（同一個表達式在樣板裡呼叫三次，TypeScript 每次都要重新窄化一遍，
   而且那三次真的會各算一遍）。 */
const rows = computed(() =>
  (data.value?.views ?? []).map((r) => ({
    key: `${r.session_id}-${r.port}`,
    owner: r.owner || "?",
    ownerTitle: r.owner || "",
    session: r.session_name || r.session_id,
    port: r.port,
    alive: aliveCell(r),
    listen: listenCell(r),
    clients:
      r.proc && "clients" in r.proc && r.proc.clients !== undefined ? String(r.proc.clients) : null,
    bin: r.proc?.bin || r.ttyd_bin || "?",
    at: when(r.created_at),
    ago: relTime(r.created_at),
  })),
);

async function load(): Promise<void> {
  try {
    data.value = await api<Inspect>("/api/ttyd/inspect");
    failed.value = false;
  } catch (ex) {
    // ⚠ 失敗要把表格換成講得出原因的一列，不能留著上一次的資料。留著的話畫面看起來仍然
    //   正常，而它顯示的是過期的世界——那正是這一節要抓的那種假象。
    data.value = null;
    failed.value = true;
    toastError("讀取 ttyd 實況", ex);
  }
}

defineExpose({ load });
</script>

<template>
  <section class="panel">
    <div class="section-head" style="margin-bottom: var(--space-2)">
      <h2 class="panel__title" style="margin: 0">ttyd 實況</h2>
      <button class="btn" id="ttyd-refresh" data-testid="ttyd-refresh" @click="load">
        <i class="fa-solid fa-rotate"></i> 重新整理
      </button>
    </div>
    <p class="panel__lede">
      「在聽」與「連線」是去問那顆行程自己的 socket 得到的，不是 DB 記的。<br />
      「在聽」把<strong>它實際綁的 port</strong> 跟<strong>左邊 DB 記的 port</strong>
      對起來：相符代表兩邊講的是同一件事；<br />
      對不上代表 DB 記錯了——照 DB 那個 port 連不到終端，而它真正占著的那個沒有人在管。
    </p>
    <p class="panel__lede" data-tone="warn" id="ttyd-nopsutil" :hidden="data?.psutil !== false">
      這個容器裡沒有 psutil，只讀得到 DB 那一半。<strong>下面沒有列出孤兒，不代表沒有孤兒。</strong>
    </p>
    <table class="roster">
      <thead>
        <tr>
          <th>擁有者 / session</th>
          <th>port</th>
          <th>pid</th>
          <th>在聽</th>
          <th>連線</th>
          <th>ttyd</th>
          <th>開啟於</th>
        </tr>
      </thead>
      <tbody id="ttyd-body" data-testid="ttyd-views">
        <!-- prettier-ignore -->
        <tr v-if="failed"><td colspan="7">讀不到（見下方訊息）</td></tr>
        <!-- prettier-ignore -->
        <tr v-else-if="!data"><td colspan="7">載入中…</td></tr>
        <!-- prettier-ignore -->
        <tr v-else-if="!data.views.length"><td colspan="7">目前沒有開著的終端</td></tr>
        <tr v-for="r in rows" v-else :key="r.key">
          <td>
            <!-- prettier-ignore -->
            <span class="roster__name" :title="r.ownerTitle">{{ r.owner }}</span>
            <span class="roster__ago">{{ r.session }}</span>
          </td>
          <td class="mono-id">{{ r.port }}</td>
          <td>
            <!-- prettier-ignore -->
            <span v-if="r.alive.kind === 'mono'" class="mono-id">{{ r.alive.text }}</span>
            <!-- prettier-ignore -->
            <span v-else class="chip" :data-tone="r.alive.tone" :title="r.alive.title">{{ r.alive.text }}</span>
          </td>
          <td>
            <!-- prettier-ignore -->
            <span v-if="r.listen.kind === 'ago'" class="roster__ago" :title="r.listen.title">{{ r.listen.text }}</span>
            <!-- prettier-ignore -->
            <span v-else class="chip" :data-tone="r.listen.tone" :title="r.listen.title">{{ r.listen.text }}</span>
          </td>
          <td>
            <template v-if="r.clients !== null">{{ r.clients }}</template>
            <span v-else class="roster__ago">—</span>
          </td>
          <td>{{ r.bin }}</td>
          <td class="roster__when">
            <span>{{ r.at }}</span>
            <span class="roster__ago">{{ r.ago }}</span>
          </td>
        </tr>
      </tbody>
    </table>
    <!-- 孤兒：程序在跑、DB 沒有對應的列。沒有任何自動機制找得到它們，所以這裡是唯一的出口。 -->
    <div id="ttyd-orphans" data-testid="ttyd-orphans">
      <template v-if="data?.orphans.length">
        <p class="panel__lede" data-tone="warn" style="margin-top: var(--space-4)">
          <strong>{{ data.orphans.length }} 個孤兒程序</strong>：在跑，但 DB 沒有對應的列。
          沒有任何自動清理找得到它們，而它們佔著 port。
        </p>
        <table class="roster">
          <thead>
            <tr>
              <th>pid</th>
              <th>在聽</th>
              <th>ttyd</th>
              <th>起於</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="o in data.orphans" :key="o.pid">
              <td class="mono-id">{{ o.pid }}</td>
              <td>{{ (o.proc?.listening ?? []).join("、") || "不知道" }}</td>
              <td>{{ o.proc?.bin || "?" }}</td>
              <td>{{ o.proc?.started_at ? relTime(o.proc.started_at) : "—" }}</td>
            </tr>
          </tbody>
        </table>
      </template>
    </div>
  </section>
</template>
