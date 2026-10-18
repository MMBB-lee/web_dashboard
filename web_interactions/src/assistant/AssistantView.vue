<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import Button from 'primevue/button'
import Textarea from 'primevue/textarea'
import Tag from 'primevue/tag'
import Drawer from 'primevue/drawer'
import { useInteractions } from '../module'
import { useEntityNavigation } from '../entity-navigation'
const { openEntity, navigationError, navigating } = useEntityNavigation()
import { errorMessage, safeSourceUrl } from '../api/client'
import type { Answer, Citation, KnowledgeDocument } from '../types/api'
import { dateText } from '../utils'
import StatePanel from '../components/StatePanel.vue'
import KnowledgeManager from '../components/KnowledgeManager.vue'
const knowledgeOpen = ref(false)
const { client, options } = useInteractions(), route = useRoute()
const input = ref(''), pending = ref(false), conversationId = ref<string>(), bottom = ref<HTMLElement>()
const messages = ref<{ question: string; response?: Answer; error?: string; streaming?: boolean }[]>([])
let activeRequest: AbortController | undefined
let scrollFrame: number | undefined
onBeforeUnmount(() => { activeRequest?.abort(); if (scrollFrame != null) cancelAnimationFrame(scrollFrame) })
function scrollAnswer() {
  if (scrollFrame != null) return
  scrollFrame = requestAnimationFrame(() => {
    scrollFrame = undefined
    void nextTick().then(() => bottom.value?.scrollIntoView({ block: 'nearest' }))
  })
}
const document = ref<KnowledgeDocument | null>(null), documentId = ref<string | null>(null), documentError = ref(''), documentLoading = ref(false)
const suggestions = ['合肥南站的月度客流有什么变化？', '铁路发送量和机场吞吐量有什么区别？', '现在景区有多少人？']
let documentVersion = 0
async function send(retryIndex?: number) {
  const question = typeof retryIndex === 'number' ? messages.value[retryIndex]?.question ?? '' : input.value.trim()
  if (!question || question.length > 1000 || pending.value) return
  const index = typeof retryIndex === 'number' ? retryIndex : messages.value.push({ question }) - 1
  messages.value[index]!.error = undefined; messages.value[index]!.response = undefined
  messages.value[index]!.streaming = true; input.value = ''; pending.value = true
  activeRequest = new AbortController()
  await nextTick(); bottom.value?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  try {
    const body = { message: question, ...(conversationId.value ? { conversation_id: conversationId.value } : {}), ...(typeof route.query.entity_id === 'string' ? { context: { entity_ids: [route.query.entity_id] } } : {}) }
    const answer = options.mode === 'demo'
      ? (await client.post<Answer>('/assistant/chat', body)).data
      : await client.stream<Answer>('/assistant/chat/stream', body, {
        onStart(id) {
          conversationId.value = id
          messages.value[index]!.response = { conversation_id: id, answer: '', status: 'answered', citations: [], used_metric_queries: [], limitations: [] }
        },
        onDelta(text) {
          const response = messages.value[index]!.response
          if (response) response.answer += text
          scrollAnswer()
        },
      }, activeRequest.signal)
    messages.value[index]!.response = answer; conversationId.value = answer.conversation_id
  } catch (e) { messages.value[index]!.error = errorMessage(e) }
  finally { messages.value[index]!.streaming = false; pending.value = false; activeRequest = undefined; await nextTick(); bottom.value?.scrollIntoView({ behavior: 'smooth', block: 'end' }) }
}
async function openDocument(citation: Citation) {
  const current = ++documentVersion; documentId.value = citation.id; documentLoading.value = true; documentError.value = ''; document.value = null
  try { const response = await client.get<KnowledgeDocument>(`/knowledge/documents/${encodeURIComponent(citation.id)}`); if (current === documentVersion) document.value = response.data }
  catch (e) { if (current === documentVersion) documentError.value = errorMessage(e) }
  finally { if (current === documentVersion) documentLoading.value = false }
}
onMounted(() => { if (typeof route.query.question === 'string') input.value = route.query.question.slice(0, 1000) })
</script>
<template>
  <section class="b-ui page-stack assistant-page">
    <p v-if="navigationError" role="alert">{{ navigationError }}</p>
    <header class="page-heading"><div><div class="eyebrow">DATA ASSISTANT</div><h1>智能问答</h1><p>支持日常交流与项目问答，项目数据结合数据库和资料回答。</p></div><Button v-if="options.mode !== 'demo'" label="知识库管理" icon="pi pi-book" outlined @click="knowledgeOpen = true" /><Button label="新对话" icon="pi pi-plus" outlined severity="secondary" :disabled="pending || !messages.length" @click="messages = []; conversationId = undefined; input = ''" /></header>
    <div class="panel chat-panel">
      <div v-if="!messages.length" class="chat-welcome"><div class="assistant-symbol"><i class="pi pi-sparkles" /></div><span class="eyebrow">数据查询 · 资料检索</span><h2>文旅交通数据助手</h2><p>查询交通趋势、理解统计口径，或追溯事件来源。<br />每一个可靠的回答，都应该有迹可循。</p><div class="suggestions"><button v-for="(question, i) in suggestions" :key="question" @click="input = question"><i :class="['pi', ['pi-chart-line', 'pi-book', 'pi-question-circle'][i]]" /><span>{{ question }}</span><i class="pi pi-arrow-up-right" /></button></div></div>
      <div v-else class="chat-messages" aria-live="polite">
        <article v-for="(message, index) in messages" :key="index" class="exchange"><div class="user-message"><span class="avatar small-avatar">你</span><p>{{ message.question }}</p></div><div class="answer-message"><span class="avatar assistant-avatar"><i class="pi pi-sparkles" /></span><div class="answer-body"><div class="answer-label">皖行助手 <span>{{ options.mode === 'demo' ? '预设演示回答' : '智能对话 · 数据检索' }}</span></div><template v-if="message.response"><Tag v-if="message.response.status === 'insufficient_evidence'" value="证据不足" severity="warn" /><p class="answer-text">{{ message.response.answer }}</p><div v-if="message.response.metric_result" class="query-context"><div v-for="(item, i) in [message.response.metric_result!]" :key="i"><i class="pi pi-calendar" />数据时间 {{ item.period_start }} — {{ item.period_end }}</div></div><div v-if="message.response.citations.length" class="citations"><span class="small muted">参考来源</span><div v-for="(citation, i) in message.response.citations" :key="`${citation.id}-${i}`" class="citation"><span class="citation-index">{{ i + 1 }}</span><div><strong>{{ citation.title }}</strong><span>{{ dateText(citation.date) }}</span></div><button v-if="citation.type === 'document'" class="text-link" @click="openDocument(citation)">查看资料</button><RouterLink v-else-if="citation.type === 'event'" :to="{ name: 'b-events', query: { id: citation.id } }">查看事件</RouterLink><a v-else-if="safeSourceUrl(citation.url)" :href="safeSourceUrl(citation.url)" target="_blank" rel="noopener noreferrer">查看原文</a></div></div><ul v-if="message.response.limitations.length" class="limitations"><li v-for="item in message.response.limitations" :key="item">{{ item }}</li></ul></template><div v-if="message.error" role="alert"><p>{{ message.error }}</p><Button label="重试此问题" text icon="pi pi-refresh" :disabled="pending" @click="send(index)" /></div><p v-else-if="message.streaming" class="thinking"><i class="pi pi-spin pi-spinner" />{{ message.response?.answer ? '正在生成…' : '正在整理回答与引用…' }}</p></div></div></article><div ref="bottom" /></div>
      <form class="chat-composer" @submit.prevent="send()"><label class="sr-only" for="question">输入问题</label><Textarea id="question" v-model="input" placeholder="例如：合肥南站最近几个月的发送量有什么变化？" rows="2" :maxlength="1000" :disabled="pending" @keydown.ctrl.enter.prevent="send()" /><div class="composer-footer"><span><i class="pi pi-paperclip" />通用交流 · 项目数据检索 <span class="char-count">{{ input.length }}/1000</span></span><Button v-if="pending && options.mode !== 'demo'" type="button" label="停止生成" icon="pi pi-stop" severity="secondary" @click="activeRequest?.abort()" /><Button type="submit" label="发送" icon="pi pi-arrow-up" :loading="pending" :disabled="!input.trim() || pending" /></div></form>
    </div><p class="chat-footnote">{{ options.mode === 'demo' ? '当前为演示模式：回答与引用均为预设示例，未连接真实模型。' : '回答受数据覆盖范围与资料时效限制，请结合引用核验。' }}</p>
    <Drawer :visible="!!documentId" header="引用资料" position="right" class="b-ui b-drawer" @update:visible="value => { if (!value) documentId = null }"><StatePanel v-if="documentLoading || documentError" :kind="documentLoading ? 'loading' : 'error'" :message="documentError" @retry="documentId && openDocument({ id: documentId, type: 'document', title: '', date: '' })" /><template v-else-if="document"><h2>{{ document.title }}</h2><p class="muted">{{ document.publisher }} · {{ dateText(document.published_at) }}</p><p v-if="document.valid_until && Date.parse(document.valid_until) < Date.now()" class="notice">资料适用期已结束，请注意时效。</p><p v-for="(snippet, i) in document.allowed_snippets" :key="i" class="detail-body">{{ snippet }}</p><p>{{ document.permission_note }}</p><a v-if="safeSourceUrl(document.source_url)" :href="safeSourceUrl(document.source_url)" target="_blank" rel="noopener noreferrer">查看来源原文</a></template></Drawer>
    <Drawer v-model:visible="knowledgeOpen" header="知识库管理" position="right" class="b-ui b-drawer" :style="{ width: 'min(760px, 95vw)' }"><KnowledgeManager v-if="knowledgeOpen" /></Drawer>
  </section>
</template>
