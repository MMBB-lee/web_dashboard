<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useInteractions } from '../module'
import { errorMessage, safeSourceUrl } from '../api/client'

const { client, auth } = useInteractions()
interface Document { id: string; title: string; publisher: string; index_status: string; active_index_version: string | null; source_url: string; valid_until: string | null }
const documents = ref<Document[]>([]), error = ref(''), notice = ref(''), busy = ref(false), loading = ref(false)
const page = ref(1), total = ref(0)
const title = ref(''), publisher = ref(''), sourceUrl = ref(''), permission = ref(''), published = ref('')
const validFrom = ref(''), validUntil = ref(''), file = ref<File | null>(null)
const admin = computed(() => auth.state.session?.user.role === 'admin')
const database = ref<{ documents: number; ready: number; pending: number; failed: number; records: number } | null>(null)
const labels: Record<string, string> = { pending: '等待索引', building: '构建中', ready: '可检索', failed: '失败' }
let timer: ReturnType<typeof setInterval> | undefined, alive = true
async function refresh() {
  loading.value = true
  try {
    const [result, status] = await Promise.all([client.get<Document[]>(`/knowledge/documents?page=${page.value}&page_size=100`), client.get<NonNullable<typeof database.value>>('/knowledge/database/status')])
    if (!alive) return
    documents.value = result.data; total.value = result.meta.total ?? result.data.length; database.value = status.data; error.value = ''
  } catch (e) { if (alive) error.value = errorMessage(e) }
  finally { loading.value = false }
}
async function updateDatabase() {
  if (busy.value) return
  busy.value = true; error.value = ''; notice.value = ''
  try {
    const result = await client.post<{ created: number; updated: number; unchanged: number; queued: number }>('/knowledge/database/sync', {})
    const value = result.data
    notice.value = `数据库同步完成：新增 ${value.created} 篇、更新 ${value.updated} 篇、不变 ${value.unchanged} 篇。${value.queued ? `后台正在为 ${value.queued} 篇资料建立索引。` : '已是最新内容。'}`
    page.value = 1; await refresh()
  } catch (e) { error.value = errorMessage(e) }
  finally { busy.value = false }
}
async function reindex(ids: string[]) {
  if (!ids.length || busy.value) return
  busy.value = true; error.value = ''; notice.value = ''
  try {
    const result = await client.post<{ job_id: string }>('/knowledge/reindex', { document_ids: ids, reason: '管理员从知识库管理入口更新索引' })
    notice.value = `重建任务已提交，等待后台 Worker 处理。任务：${result.data.job_id}`
    await refresh()
  } catch (e) { error.value = errorMessage(e) }
  finally { busy.value = false }
}
function choose(event: Event) { file.value = (event.target as HTMLInputElement).files?.[0] ?? null }
async function upload() {
  if (!file.value || busy.value) return
  if (file.value.size > 50 * 1024 * 1024) { error.value = '文件不能超过 50MB'; return }
  const form = new FormData()
  form.set('file', file.value); form.set('title', title.value); form.set('publisher', publisher.value)
  form.set('source_url', sourceUrl.value); form.set('permission_note', permission.value)
  form.set('published_at', `${published.value}T00:00:00+08:00`)
  if (validFrom.value) form.set('valid_from', `${validFrom.value}T00:00:00+08:00`)
  if (validUntil.value) form.set('valid_until', `${validUntil.value}T23:59:59+08:00`)
  busy.value = true; error.value = ''; notice.value = ''
  try {
    const result = await client.post<{ job_id: string }>('/knowledge/documents', form)
    notice.value = `资料已登记，等待后台 Worker 建立索引。任务：${result.data.job_id}`
    page.value = 1; await refresh()
  } catch (e) { error.value = errorMessage(e) }
  finally { busy.value = false }
}
onMounted(() => { void refresh(); timer = setInterval(() => { if (!busy.value && !loading.value) void refresh() }, 5000) })
onUnmounted(() => { alive = false; clearInterval(timer) })
</script>

<template>
  <section class="knowledge-manager">
    <p>资料状态为“可检索”后即可用于项目问答。数据库同步会自动建立索引，历史资料保留适用时间。</p>
    <p v-if="error" role="alert" class="error">{{ error }}</p>
    <p v-if="notice" role="status">{{ notice }}</p>
    <div class="database-sync"><div><strong>数据库知识</strong><p>将新增或变更的地点、客流、假期及事件资料同步到知识库。</p><p v-if="database">共 {{ database.documents }} 篇 · 可检索 {{ database.ready }} 篇 · 处理中 {{ database.pending }} 篇<span v-if="database.failed"> · 失败 {{ database.failed }} 篇</span></p></div><button v-if="admin" :disabled="busy" @click="updateDatabase">{{ busy ? '同步中…' : '更新数据库' }}</button></div>
    <div class="actions"><button :disabled="loading" @click="refresh">刷新状态</button><button v-if="admin" :disabled="busy || !documents.length" @click="reindex(documents.map(d => d.id))">重建本页索引</button><span>共 {{ total }} 篇资料</span></div>
    <div class="document-list"><article v-for="doc in documents" :key="doc.id"><strong>{{ doc.title }}</strong><span>{{ doc.publisher }} · {{ labels[doc.index_status] ?? doc.index_status }} {{ doc.active_index_version ?? '' }}</span><span v-if="doc.valid_until">有效期至 {{ doc.valid_until.slice(0, 10) }}</span><div class="actions"><a v-if="safeSourceUrl(doc.source_url)" :href="safeSourceUrl(doc.source_url)" target="_blank" rel="noopener noreferrer">查看来源</a><button v-if="admin" :disabled="busy" @click="reindex([doc.id])">重建索引</button></div></article></div>
    <div class="actions" v-if="total > 100"><button :disabled="page === 1 || loading" @click="page--; refresh()">上一页</button><span>第 {{ page }} 页</span><button :disabled="page * 100 >= total || loading" @click="page++; refresh()">下一页</button></div>
    <form v-if="admin" @submit.prevent="upload">
      <h3>上传或补充知识资料</h3>
      <label>文档文件（PDF / TXT / Markdown，最大 50MB）<input type="file" accept=".pdf,.txt,.md,.markdown" required @change="choose" /></label>
      <label>标题<input v-model="title" required maxlength="200" /></label>
      <label>发布单位<input v-model="publisher" required maxlength="200" /></label>
      <label>来源链接<input v-model="sourceUrl" type="url" required maxlength="500" /></label>
      <label>发布日期<input v-model="published" type="date" required /></label>
      <label>生效日期（可选）<input v-model="validFrom" type="date" /></label>
      <label>失效日期（可选）<input v-model="validUntil" type="date" :min="validFrom" /></label>
      <label>使用权限说明<textarea v-model="permission" required maxlength="500" placeholder="说明资料的公开来源或使用授权" /></label>
      <button type="submit" :disabled="busy || !file">{{ busy ? '处理中…' : '上传并建立索引' }}</button>
    </form>
    <p v-else>当前账号可查看资料状态，管理员账号可以上传资料、重建索引。</p>
  </section>
</template>

<style scoped>
.knowledge-manager { display:grid; gap:14px; color:var(--b-text, #cce8ff); }.knowledge-manager p { line-height:1.6; }.actions { display:flex; flex-wrap:wrap; align-items:center; gap:12px; }.document-list { display:grid; gap:10px; max-height:36vh; overflow:auto; }.document-list article { display:grid; gap:5px; padding:12px; border:1px solid #31526e; }.document-list span { font-size:12px; }.knowledge-manager form { display:grid; gap:12px; }.knowledge-manager label { display:grid; gap:6px; }.knowledge-manager input, .knowledge-manager textarea { width:100%; padding:8px; color:inherit; background:#102a46; border:1px solid #426583; }.knowledge-manager button { padding:7px 12px; color:#cceeff; background:#153a59; border:1px solid #477e9f; cursor:pointer; }.knowledge-manager button:disabled { opacity:.5; cursor:default; }.knowledge-manager a { color:#5edcff; }.error { color:#ffbba7; }
.database-sync { display:flex; align-items:center; justify-content:space-between; gap:14px; padding:14px; border:1px solid #31526e; background:#102a46; }.database-sync p { margin-top:5px; font-size:12px; }.database-sync button { flex:none; }
</style>
