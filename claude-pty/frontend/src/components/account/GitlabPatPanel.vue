<script setup lang="ts">
/*
 * GitLab 憑證。與 CLI 憑證那一塊同一套做法：狀態在進頁時就算好（不會先閃一個預設值），
 * 存／清之後重抓 `/api/account/bootstrap` 讓三處狀態同源重畫。
 *
 * ⚠ 空字串＝清除，所以清除也走 PUT，**沒有 DELETE 端點**（見 app.set_own_gitlab_pat）。
 * ⚠ 整塊只在 `gitlab.enabled` 時畫（呼叫端 gate）：功能關掉時每一場的代理都不存在，
 *   畫出來等於對著使用者講一件這台機器上沒有的事。
 */
import { computed, ref } from "vue";

import { api } from "@/api/client";
import PasswordInput from "@/components/PasswordInput.vue";
import { submitting } from "@/lib/submitting";
import { toast, toastError } from "@/lib/toast";
import { useSiteStore } from "@/stores/site";

const store = useSiteStore();

const pat = ref("");
const busy = ref(false);

const proxyError = computed(() => store.meta.gitlabProxyError);
const patSet = computed(() => store.user?.gitlab_pat_configured === true);
const placeholder = computed(() =>
  patSet.value
    ? "••••••••••••••••••••（已設定，貼新的可覆寫）"
    : "貼上 GitLab 的 Personal Access Token",
);

const putPat = (value: string): Promise<unknown> =>
  api("/api/users/me/gitlab-pat", { method: "PUT", body: { pat: value } });

/** 存或清之後把狀態重新抓一次。
 *
 * ⚠ 舊版是 `setTimeout(() => location.reload(), 900)`——徽章、chip、按鈕三處狀態同源重畫，
 *   在一個每頁都要重新跑 Jinja 的架構下那是最可靠的做法。SPA 不必付那個代價：
 *   `/api/account/bootstrap` 一發就把憑證、限制、身分全帶回來，那三處都是照它畫的。
 * ⚠ 欄位要**自己清**。舊版靠整頁重載順便清掉，不重載就得明寫——不清的話畫面會停在
 *   「已經存進去了，但輸入框裡還留著剛剛那把 PAT」，而那是最不該留在畫面上的東西。
 */
async function refresh(): Promise<void> {
  pat.value = "";
  await store.loadAccountMeta();
}

const save = submitting(busy, async () => {
  try {
    await putPat(pat.value);
    toast("GitLab 憑證已儲存", "success", {
      body: "已接上代理的 session 會在一個對帳週期內改用新的；還沒接上的要開新的一場",
    });
    await refresh();
  } catch (ex) {
    toastError("儲存 GitLab 憑證", ex);
  }
});

async function clear(): Promise<void> {
  try {
    await putPat("");
    toast("GitLab 憑證已清除", "warning", {
      body: "你的代理會被收掉，所有 session 當場失去 GitLab；再填一把回去它們會恢復",
    });
    await refresh();
  } catch (ex) {
    toastError("清除 GitLab 憑證", ex);
  }
}
</script>

<template>
  <section class="panel">
    <div class="section-head" style="margin-bottom: var(--space-2)">
      <h2 class="panel__title" style="margin: 0">GitLab 憑證</h2>
      <span id="pat-state">
        <span v-if="proxyError" class="chip" data-tone="error">代理起不來</span>
        <span v-else-if="patSet" class="chip" data-tone="ok">已設定</span>
        <span v-else class="chip" data-tone="error">未設定</span>
      </span>
    </div>
    <!-- ⚠ 代理連續起不來時，把 nginx 自己說的那句話端出來，而且**排在最上面**。
         它回答的是「我明明設好了，為什麼不能用」——而這一頁其他所有東西（chip 說已設定、
         欄位有遮罩）都在說「一切正常」。訊息是容器 log 的最後一行，不含設定檔內容。 -->
    <p v-if="proxyError" class="panel__lede" data-tone="warn">
      <strong>你的代理起不來，所以 GitLab 現在不通。</strong>
      這多半是<strong>部署設定</strong>的問題，不是你的 token——先看這一句再決定要不要重貼：
      <!-- 機器訊息與人話之間要斷行：它常常長到換兩行，接著又緊跟建議。 -->
      <br /><code>{{ proxyError }}</code
      ><br />
      出現 <code>host not found in upstream</code> 就是部署的
      <code>CLAUDE_PTY_GITLAB_HOST</code> 設錯或解不到，請找管理者。
      修好之後會自動恢復，這則訊息也會自己消失。
    </p>
    <p class="panel__lede">
      你自己的 <strong>{{ store.meta.gitlabHost }}</strong> Personal Access Token（需要
      <code>api</code>
      scope）。你開的 session 會接上一顆<strong>只屬於你</strong>的代理，由它蓋章——
      <strong>token 不會進入 session 容器</strong>，裡面的 AI 用得到你的 GitLab，
      卻讀不到這把鑰匙。clone 用正規網址就好（<code>https://…</code> 與 <code>git@…</code>
      都會自動改寫成走代理）。請用專門建立、scope 最小、有到期日的 token。
    </p>
    <form id="pat-form" @submit.prevent="save">
      <div class="form-row">
        <div class="field" style="flex: 1">
          <label class="label" for="gitlab-pat">Personal Access Token</label>
          <!-- 同 CLI 憑證那一格：走 PasswordInput（舊版由 enhancePasswordFields 包） -->
          <PasswordInput
            id="gitlab-pat"
            v-model="pat"
            autocomplete="off"
            :placeholder="placeholder"
          />
        </div>
      </div>
      <div class="form-actions">
        <button
          class="btn btn--primary"
          type="submit"
          id="pat-save"
          :disabled="!pat.trim() || busy"
        >
          <i class="fa-solid fa-key"></i> 儲存
        </button>
        <button class="btn" type="button" id="pat-clear" :hidden="!patSet" @click="clear">
          <i class="fa-solid fa-eraser"></i> 清除
        </button>
      </div>
    </form>
    <!-- ⚠ 這段講的是**輪替語意**，是這個面板最容易被誤解的一件事。分界線在「那一場開場時
         有沒有接上代理網路」，不在「帳號現在有沒有 PAT」（ADR 0016）。
         ⚠ `margin-top` 要明寫：`.panel__lede` 帶負的上邊距（它是設計來緊貼在標題底下的），
            直接用在表單後面會把這一塊拉上去疊在儲存／清除按鈕上。 -->
    <p class="panel__lede" data-tone="warn" style="margin-top: var(--space-4)">
      換一把新的：已接上代理的 session 會在一個對帳週期內改用新的。清除：它們當場失去
      GitLab，<strong>再填回去會恢復</strong>。所以——<strong
        >輪替 token 不等於隔離一場你 不信任的 session；要隔離那場，就終止那場。</strong
      >
      設定 token 之前開的 session 沒接上代理，事後補不上，要開新的一場。
    </p>
  </section>
</template>
