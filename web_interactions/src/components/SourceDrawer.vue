<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import Drawer from 'primevue/drawer'
import StatePanel from './StatePanel.vue'
import { useInteractions } from '../module'
import { errorMessage, safeSourceUrl } from '../api/client'
import type { Source } from '../types/api'
import { dateText } from '../utils'
const props = defineProps<{ sourceId: string | null }>()
const emit = defineEmits<{ close: [] }>()
const { client } = useInteractions()
const source = ref<Source | null>(null), error = ref(''), loading = ref(false)
const isProjectSource = computed(() => !!source.value && /\/api\/project-data\/export\.csv(?:\?|$)/.test(source.value.url))
const statusText = computed(() => ({ verified: '已核验', pending_review: '待核验', unverified: '未核验', rejected: '未通过核验', needs_review: '待复核', not_applicable: '不适用' }[source.value?.verification_status ?? ''] ?? '未标注'))
const coverageText = computed(() => {
  const start = source.value?.coverage_start, end = source.value?.coverage_end
  if (!start && !end) return '未提供统计范围'
  return start === end ? dateText(start) : `${dateText(start)} 至 ${dateText(end)}`
})
const usageText = computed(() => isProjectSource.value
  ? '本资料汇集公开统计、估算及模拟数据，具体来源和处理方式可在数据清单中查看。'
  : source.value?.usage_note)
let version = 0
async function load() {
  const current = ++version; source.value = null; error.value = ''
  if (!props.sourceId) return
  loading.value = true
  try { const response = await client.get<Source>(`/sources/${encodeURIComponent(props.sourceId)}`); if (current === version) source.value = response.data }
  catch (e) { if (current === version) error.value = errorMessage(e) }
  finally { if (current === version) loading.value = false }
}
watch(() => props.sourceId, load, { immediate: true })
</script>
<template>
  <Drawer :visible="!!sourceId" position="right" header="数据来源" class="b-ui b-drawer" @update:visible="value => { if (!value) emit('close') }">
    <StatePanel v-if="loading || error" :kind="loading ? 'loading' : 'error'" :message="error" @retry="load" />
    <div v-else-if="source" class="source-content">
      <div class="source-heading"><span class="eyebrow">来源详情</span><h2>{{ source.title }}</h2><p class="muted">{{ source.publisher }}</p></div>
      <dl class="detail-list">
        <template v-if="isProjectSource"><dt>资料类型</dt><dd>项目整理数据</dd></template>
        <template v-else><dt>发布时间</dt><dd>{{ dateText(source.published_at, true) }}</dd></template>
        <dt>{{ isProjectSource ? '最近更新' : '采集时间' }}</dt><dd>{{ dateText(source.retrieved_at, true) }}</dd>
        <dt>统计范围</dt><dd>{{ coverageText }}</dd>
        <dt>核验状态</dt><dd><span class="source-status">{{ statusText }}</span></dd>
      </dl>
      <p v-if="usageText" class="notice">{{ usageText }}</p>
      <a v-if="safeSourceUrl(source.url)" class="text-link source-link" :href="safeSourceUrl(source.url)" target="_blank" rel="noopener noreferrer">{{ isProjectSource ? '查看完整数据清单' : '查看原始来源' }} <i class="pi pi-arrow-up-right" /></a>
    </div>
  </Drawer>
</template>

<style scoped>
.source-content { padding: 16px 0; }
.source-heading h2 { overflow-wrap: anywhere; }
.source-content .detail-list { line-height: 1.7; row-gap: 18px; }
.source-status { display: inline-block; padding: 1px 8px; border: 1px solid var(--b-border); background: rgba(0, 212, 255, .05); }
.source-content .notice { line-height: 1.85; overflow-wrap: anywhere; }
.source-link { display: inline-flex; align-items: center; gap: 5px; margin-top: 18px; }
</style>
