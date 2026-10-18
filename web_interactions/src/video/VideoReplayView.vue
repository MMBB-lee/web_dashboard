<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import Button from 'primevue/button'
import Select from 'primevue/select'
import Tag from 'primevue/tag'
import Message from 'primevue/message'
import StatePanel from '../components/StatePanel.vue'
import TrendChart from '../components/TrendChart.vue'
import { useInteractions } from '../module'
import { ApiError, errorMessage, query } from '../api/client'
import type { CameraForecast, CountGrain, Counts, CountingTask, VideoItem } from '../types/api'
import { elapsed, normalizeCounts } from './counting'
import { dateText, numberText } from '../utils'

const { client, options, auth } = useInteractions()
const selectedAnalysisIds = ref<Record<string, string>>({ ...options.analysisIds })
const cameraNames = ref<Record<string, string>>({ ...options.cameraNames })
const task = ref<CountingTask | null>(null), starting = ref(false), taskError = ref(''), sampleInterval = ref(5)
const taskBusy = computed(() => starting.value || !!task.value && ['queued','processing','running'].includes(task.value.status))
const taskLabels: Record<string,string> = { queued: '等待计数', processing: '正在计数', running: '正在计数', ready: '计数完成', failed: '计数失败' }
const canStart = computed(() => auth.state.session?.user.role === 'admin')
const videos = ref<VideoItem[]>([]), videoId = ref(''), counts = ref<Counts | null>(null), forecast = ref<CameraForecast | null>(null)
const loading = ref(true), error = ref(''), countLoading = ref(false), countError = ref(''), analysisNotReady = ref(false)
const forecastError = ref(''), predicting = ref(false)
const position = ref(7200), grain = ref<CountGrain>('5min'), horizon = ref(30)
const player = ref<HTMLVideoElement>(), localUrl = ref(''), fileInput = ref<HTMLInputElement>(), playbackError = ref('')
const video = computed(() => videos.value.find(v => v.id === videoId.value))
const isLocal = computed(() => video.value?.origin === 'local_folder')
const isRelative = computed(() => counts.value?.time_basis === 'relative' || isLocal.value)
const mediaDuration = ref(0)
const duration = computed(() => video.value?.duration_seconds ?? (video.value?.recorded_start_at && video.value?.recorded_end_at ? Math.max(0, (Date.parse(video.value.recorded_end_at) - Date.parse(video.value.recorded_start_at)) / 1000) : mediaDuration.value))
const cutoff = computed(() => video.value?.recorded_start_at ? chinaTime(Date.parse(video.value.recorded_start_at) + position.value * 1000) : '')
function videoName(item?: VideoItem) { return item?.title || (item?.camera_id ? cameraNames.value[item.camera_id] || item.camera_id : '本地录像') }
const completeCounts = computed(() => counts.value?.points.filter(p => isRelative.value ? p.offset_seconds! <= position.value : Date.parse(p.period_end) <= Date.parse(cutoff.value)) ?? [])
const latestCount = computed(() => completeCounts.value.at(-1))
const qualityLabels: Record<string, string> = { good: '有效估计', degraded: '部分缺帧', unavailable: '无有效帧', demo: '模拟数据', verified: '已核验', valid: '有效', ok: '有效', missing: '缺失', occluded: '遮挡', low_quality: '质量不足', invalid: '无效', unverified: '待核验' }
const countQuality = computed(() => latestCount.value ? qualityLabels[latestCount.value.quality_status] || latestCount.value.quality_status : '暂无计数')
const roiPoints = computed(() => counts.value?.roi.map(point => point.join(',')).join(' ') ?? '')
const hasInterval = computed(() => forecast.value?.status === 'ready' && forecast.value.forecast_points.some(p => p.lower_bound != null && p.upper_bound != null))
const forecastNotice = computed(() => !forecast.value || forecast.value.status === 'ready' ? '' : forecast.value.reason || (forecast.value.status === 'quality_insufficient' ? '计数质量不足，暂停预测' : '有效历史不足或回测未通过，暂停预测'))
const playbackSrc = computed(() => options.mode === 'demo' ? localUrl.value : video.value ? `/api/v1/videos/${encodeURIComponent(video.value.id)}/playback${video.value.file_digest ? `?v=${video.value.file_digest}` : ''}` : '')
let generation = 0, forecastVersion = 0
let folderTimer: ReturnType<typeof setInterval> | undefined, taskTimer: ReturnType<typeof setInterval> | undefined
const pendingTaskRequests = new Set<string>()

function chinaTime(time: number) { return new Date(time + 8 * 3600000).toISOString().slice(0, 19) + '+08:00' }
function positionLabel(seconds: number) { return elapsed(seconds) }
function clearForecast() { ++forecastVersion; forecast.value = null; forecastError.value = ''; predicting.value = false }
function releaseLocalPreview() { if (localUrl.value) URL.revokeObjectURL(localUrl.value); localUrl.value = ''; if (fileInput.value) fileInput.value.value = '' }
async function loadVideos(silent = false) {
  if (!silent) loading.value = true
  error.value = ''
  try {
    if (options.mode === 'demo') {
      const demo = await import('../../dev/fixtures')
      selectedAnalysisIds.value = { ...demo.demoAnalysisIds, ...selectedAnalysisIds.value }
      cameraNames.value = { ...cameraNames.value, ...demo.demoCameraNames }
    }
    const previousDigest = video.value?.file_digest
    videos.value = (await client.get<VideoItem[]>('/videos?page_size=100')).data
    const previous = videoId.value
    if (!videos.value.some(item => item.id === videoId.value)) videoId.value = videos.value[0]?.id ?? ''
    if (previous !== videoId.value || previousDigest !== video.value?.file_digest || !silent) await selectVideo()
  } catch (e) { error.value = errorMessage(e) }
  finally { loading.value = false }
}
async function selectVideo() {
  ++generation; task.value = null; counts.value = null; countError.value = ''; clearForecast(); taskError.value = ''; starting.value = false
  player.value?.pause(); releaseLocalPreview(); playbackError.value = ''
  mediaDuration.value = 0
  position.value = isLocal.value ? 0 : Math.min(7200, duration.value)
  if (isLocal.value) grain.value = 'frame'
  if (options.mode === 'api' && videoId.value) await loadTask()
  else await loadCounts()
}
async function loadTask() {
  const id = videoId.value
  if (!id || pendingTaskRequests.has(id)) return
  pendingTaskRequests.add(id)
  try {
    const current = (await client.get<CountingTask | null>(`/videos/${encodeURIComponent(id)}/counting`)).data
    if (id !== videoId.value) return
    taskError.value = ''
    task.value = current
    if (current?.sample_interval_seconds) sampleInterval.value = current.sample_interval_seconds
    if (current?.status === 'ready') {
      const changed = selectedAnalysisIds.value[id] !== current.analysis_id
      selectedAnalysisIds.value[id] = current.analysis_id
      if (changed || !counts.value) await loadCounts()
    } else if (current?.status === 'failed') taskError.value = current.error_message || '计数失败，请重试'
    else if (!current) { delete selectedAnalysisIds.value[id]; await loadCounts() }
  } catch(e) { if (id === videoId.value) taskError.value = errorMessage(e) }
  finally { pendingTaskRequests.delete(id) }
}
async function startCounting() {
  const id = videoId.value
  if (!id || taskBusy.value) return
  starting.value = true; taskError.value = ''; task.value = null; counts.value = null; clearForecast(); player.value?.pause()
  try {
    const created = (await client.post<CountingTask>(`/videos/${encodeURIComponent(id)}/counting`, {
      count_model: 'p2pnet', weights_version: 'leader-SHTechA-50604773',
      roi: { points: [[0,0],[1,0],[1,1],[0,1]] }, sample_interval_seconds: sampleInterval.value,
      aggregation_minutes: 5, inference_engine: 'pytorch', device: 'CPU',
    })).data
    if (id !== videoId.value) return
    task.value = created; selectedAnalysisIds.value[id] = created.analysis_id
    counts.value = null; clearForecast()
    if (created.status === 'ready') await loadCounts()
  } catch(e) { if (id === videoId.value) taskError.value = errorMessage(e) }
  finally { if (id === videoId.value) starting.value = false }
}
async function loadCounts() {
  const current = ++generation; clearForecast(); counts.value = null; countError.value = ''; analysisNotReady.value = false; countLoading.value = true
  const analysisId = selectedAnalysisIds.value[videoId.value]
  if (!analysisId) { countLoading.value = false; return }
  const requestedGrain = grain.value
  try {
    const response = await client.get<Counts>(`/videos/${encodeURIComponent(videoId.value)}/counts?${query({ analysis_id: analysisId, grain: requestedGrain })}`)
    const result = normalizeCounts(response.data, requestedGrain, options.mode === 'demo')
    if (current === generation) counts.value = result
  } catch (e) {
    if (current === generation) { countError.value = errorMessage(e); analysisNotReady.value = e instanceof ApiError && e.status === 409 && e.code === 'analysis_not_ready' }
  } finally { if (current === generation) countLoading.value = false }
}
function seek() { if (options.mode === 'api' && player.value && Number.isFinite(player.value.duration)) player.value.currentTime = Math.min(position.value, player.value.duration) }
function loadedMetadata() { mediaDuration.value = Number.isFinite(player.value?.duration) ? player.value!.duration : 0; seek() }
function updateTime() { if (options.mode === 'api' && player.value) position.value = Math.min(player.value.currentTime, duration.value) }
async function predict() {
  const analysisId = selectedAnalysisIds.value[videoId.value]
  if (!analysisId || !counts.value || countLoading.value) return
  player.value?.pause()
  const current = ++forecastVersion; forecast.value = null; forecastError.value = ''; predicting.value = true
  const requestedCutoff = cutoff.value
  try {
    const response = await client.post<CameraForecast>(`/videos/${encodeURIComponent(videoId.value)}/short-forecast`, { analysis_id: analysisId, cutoff_at: requestedCutoff, horizon_minutes: horizon.value, step_minutes: 5 })
    const result = response.data
    if (result.metric !== 'camera_visible_people' || !['ready', 'data_insufficient', 'quality_insufficient'].includes(result.status) || !Array.isArray(result.forecast_points)) throw new Error('预测接口尚未采用画面可见人数契约，请联系后端确认')
    if (result.status === 'ready' && result.forecast_points.some(p => !p || p.metric !== 'camera_visible_people' || p.unit !== '人' || p.grain !== '5min' || p.is_forecast !== true || !Number.isFinite(Date.parse(p.period_start)) || !Number.isFinite(Date.parse(p.period_end)) || Date.parse(p.period_start) > Date.parse(p.period_end) || Date.parse(p.period_end) <= Date.parse(requestedCutoff) || (p.value !== null && (!Number.isFinite(p.value) || p.value < 0)) || [p.lower_bound, p.upper_bound].some(bound => bound != null && (!Number.isFinite(bound) || bound < 0)) || (p.lower_bound != null && p.upper_bound != null && p.lower_bound > p.upper_bound))) throw new Error('预测接口返回了不符合约定的未来人数')
    if (current === forecastVersion) forecast.value = result.status === 'ready' ? result : { ...result, forecast_points: [] }
  } catch (e) { if (current === forecastVersion) forecastError.value = errorMessage(e) }
  finally { if (current === forecastVersion) predicting.value = false }
}
function localPreview(event: Event) {
  const file = (event.target as HTMLInputElement).files?.[0]
  if (!file) return
  if (!file.type.startsWith('video/')) { playbackError.value = '请选择浏览器可播放的视频文件'; return }
  if (localUrl.value) URL.revokeObjectURL(localUrl.value)
  localUrl.value = URL.createObjectURL(file); playbackError.value = ''
}
watch([horizon, position], clearForecast, { flush: 'sync' })
watch(grain, loadCounts)
onMounted(() => {
  void loadVideos()
  if (options.mode === 'api') {
    folderTimer = setInterval(() => { if (!loading.value && !taskBusy.value) void loadVideos(true) }, 15000)
    taskTimer = setInterval(() => { if (taskBusy.value && !starting.value) void loadTask() }, 1000)
  }
})
onBeforeUnmount(() => { ++generation; ++forecastVersion; clearInterval(folderTimer); clearInterval(taskTimer); releaseLocalPreview() })
</script>
<template>
  <section class="b-ui page-stack video-page">
    <header class="page-heading"><div><div class="eyebrow">RECORDED VIDEO ANALYSIS</div><h1>录像回放</h1><p>查看固定画面区域估计可见人数、计数质量与未来画面人数预测。</p></div><Button label="刷新视频目录" icon="pi pi-refresh" outlined :loading="loading" @click="loadVideos()" /><Tag value="录像回放 · 非实时" severity="warn" /></header>
    <div class="panel filters">
      <label class="field grow">录像片段<Select :pt="{ overlay: { class: 'b-ui b-select-overlay' } }" v-model="videoId" :options="videos.map(v => ({ value: v.id, label: `${videoName(v)}${v.recorded_start_at ? ` · ${dateText(v.recorded_start_at)}` : ''}` }))" option-label="label" option-value="value" aria-label="录像片段" @change="selectVideo" /></label>
      <label class="field">计数粒度<Select :pt="{ overlay: { class: 'b-ui b-select-overlay' } }" v-model="grain" :options="[{ label: '5 分钟中位数', value: '5min' }, { label: '逐帧估计', value: 'frame' }]" option-label="label" option-value="value" aria-label="计数粒度" /></label>
      <div class="recording-meta"><span>{{ video?.recorded_start_at ? '录制时间' : '视频时长' }}</span><strong>{{ video?.recorded_start_at ? `${dateText(video.recorded_start_at, true)} — ${dateText(video.recorded_end_at, true)}` : positionLabel(duration) }}</strong></div>
    </div>
    <div v-if="options.mode === 'api' && video" class="panel filters counting-controls">
      <div class="counting-intro"><h2>P2PNet 画面人数计数</h2><p class="muted small">统计区域：全画面 · PyTorch CPU</p></div>
      <label class="field">抽帧间隔<Select v-model="sampleInterval" :options="[1,5,10,30,60].map(value => ({ label: `${value} 秒`, value }))" option-label="label" option-value="value" :disabled="taskBusy" aria-label="抽帧间隔" :pt="{ overlay: { class: 'b-ui b-select-overlay' } }" /></label>
      <div class="counting-actions">
        <Button :label="task?.status === 'failed' ? '重试计数' : '开始计数'" icon="pi pi-users" :loading="taskBusy" :disabled="!canStart || taskBusy" @click="startCounting" />
        <template v-if="task"><Tag :value="taskLabels[task.status] || task.status" :severity="task.status === 'failed' ? 'danger' : task.status === 'ready' ? 'success' : 'info'" /><span class="small muted counting-sample-total">{{ task.processed_samples }} / {{ task.total_samples }} 个抽样帧</span><progress v-if="taskBusy" :value="task.progress" max="100" aria-label="计数进度" /></template>
      </div>
      <p v-if="!canStart" class="muted small">请使用管理员账号启动计数。</p>
      <p v-if="taskError" class="counting-error" role="alert">{{ taskError }}</p>
    </div>
    <StatePanel v-if="loading || error" :kind="loading ? 'loading' : 'error'" :message="error" @retry="loadVideos" />
    <StatePanel v-else-if="!video" kind="empty" message="将视频文件放入项目 videos 文件夹，然后点击“刷新视频目录”。页面也会每15秒自动检查。" />
    <template v-else>
      <div class="video-grid">
        <article class="panel replay-panel">
          <div class="panel-heading"><h2>{{ videoName(video) }}</h2><span class="small muted"><i class="pi pi-video" /> 录像回放</span></div>
          <video v-if="playbackSrc" ref="player" :key="playbackSrc" :src="playbackSrc" controls preload="metadata" class="video-player" @loadedmetadata="loadedMetadata" @timeupdate="updateTime" @error="playbackError = '录像暂时无法播放，请检查视频编码、文件完整性或登录状态，建议使用H.264编码的MP4'" />
          <div v-else class="video-placeholder"><div class="gate-blueprint" aria-hidden="true"><span /><span /><span /><i class="pi pi-video" /></div><h3>授权录像待接入</h3><p>当前展示画面人数与时间轴的交互演示</p></div>
          <div v-if="options.mode === 'demo'" class="local-preview-controls"><Button label="选择本地录像预览" icon="pi pi-folder-open" severity="secondary" outlined @click="fileInput?.click()" /><input ref="fileInput" type="file" accept="video/*" class="sr-only" aria-label="选择本地录像" @change="localPreview" /><span class="small muted">文件仅在本机播放，不上传；演示计数、ROI 与该文件无关联，演示时间轴独立。</span></div>
          <Message v-if="playbackError" severity="error">{{ playbackError }}</Message>
          <div class="replay-timeline"><label for="replay-position"><span>回放位置</span><strong>{{ positionLabel(position) }} <span class="muted">/ {{ positionLabel(duration) }}</span></strong></label><input id="replay-position" v-model.number="position" type="range" min="0" :max="duration" step="1" @input="seek" /><div><span>{{ video.recorded_start_at?.slice(11,16) || '00:00:00' }}</span><span>{{ cutoff ? `${cutoff.slice(11,19)} · 统计截止位置` : '本地视频回放' }}</span><span>{{ video.recorded_end_at?.slice(11,16) || positionLabel(duration) }}</span></div></div>
          <p v-if="isLocal && video.source_note" class="small muted local-source">{{ video.source_note }}</p>
        </article>
        <aside class="panel counter-panel">
          <span class="eyebrow">VISIBLE PEOPLE</span><h2>固定画面区域估计可见人数</h2>
          <p class="muted small">{{ grain === '5min' ? '截止位置前最近完整窗口的有效帧中位数' : '截止位置前最近抽样帧的估计人数' }}</p>
          <div class="count-item"><span><i class="pi pi-users" />{{ grain === '5min' ? '最近窗口人数' : '最近帧人数' }}</span><strong>{{ numberText(latestCount?.value) }}<small>人</small></strong></div>
          <div class="model-details">
            <div><span>{{ isRelative ? '录像位置' : '计数时间' }}</span><strong>{{ isRelative ? latestCount?.period_end || '—' : latestCount?.period_end.slice(11,19) || '—' }}</strong></div>
            <div><span>计数质量</span><strong>{{ countQuality }}</strong></div>
            <div v-if="grain === '5min'"><span>有效抽样帧</span><strong>{{ latestCount?.sample_count ?? '—' }} 帧</strong></div>
            <div><span>人工核验{{ options.mode === 'demo' ? '（模拟）' : '' }}</span><strong>{{ counts?.manual_validation ? `${counts.manual_validation.sample_count} 帧 · MAE ${numberText(counts.manual_validation.mae)} 人` : '尚无核验记录' }}</strong></div>
          </div>
          <p class="notice">每点表示画面人数估计，不能相加为通行人次，也不代表全景区在园人数。</p>
        </aside>
      </div>
      <StatePanel v-if="countLoading" kind="loading" message="正在读取所选粒度的画面人数。" />
      <div v-else-if="analysisNotReady" class="panel"><Message severity="warn">{{ countError }}</Message><Button label="重新加载" severity="secondary" @click="loadCounts" /></div>
      <StatePanel v-else-if="countError" kind="error" :message="countError" @retry="loadCounts" />
      <Message v-else-if="!counts" severity="info">{{ taskBusy ? '正在使用 P2PNet 分析录像，完成后自动显示计数结果。' : '点击“开始计数”，系统会自动分析并保存结果。' }}</Message>
        <article class="panel count-metadata">
          <div class="roi-summary"><svg viewBox="0 0 1 1" preserveAspectRatio="none" role="img" aria-label="固定统计区域 ROI 归一化坐标示意"><polygon v-if="roiPoints" :points="roiPoints" /></svg><div><h2>固定统计区域 ROI</h2><p class="muted small">归一化区域示意（非录像截图）</p><code>{{ counts?.roi.map(point => `(${point.join(', ')})`).join(' · ') || '全画面 · 计数完成后显示模型信息' }}</code></div></div>
          <dl class="analysis-details">
            <div><dt>计数模型</dt><dd>{{ counts?.count_model === 'p2pnet' ? 'P2PNet' : counts?.count_model === 'dm_count' ? 'DM-Count' : 'P2PNet' }}</dd></div>
            <div><dt>权重版本</dt><dd>{{ counts?.weights_version || '—' }}</dd></div>
            <div><dt>推理引擎 / 设备</dt><dd>{{ counts?.inference_engine || '—' }} / {{ counts?.device || '—' }}</dd></div>
            <div v-if="counts?.inference_engine === 'openvino'"><dt>OpenVINO 版本</dt><dd>{{ counts.openvino_version || '未提供' }}</dd></div>
            <div><dt>抽帧间隔</dt><dd>{{ counts?.sample_interval_seconds != null ? `${counts.sample_interval_seconds} 秒` : '—' }}</dd></div>
            <div><dt>聚合口径</dt><dd>5 分钟有效帧中位数</dd></div>
          </dl>
          <p v-if="options.mode === 'demo'" class="muted small">模拟计数与人工核验示例 · 未执行真实 DM-Count 推理或模型回测。</p>
        </article>
        <article class="panel video-trend-panel">
          <div class="panel-heading"><div><h2>画面人数趋势与短时预测</h2><p>预测使用截止位置之前的有效 5 分钟计数，与当前查看粒度无关。</p></div><div class="compact-controls">
            <Select :pt="{ overlay: { class: 'b-ui b-select-overlay' } }" v-model="horizon" :options="[15,30,60].map(value => ({ label: `未来 ${value} 分钟`, value }))" option-label="label" option-value="value" aria-label="预测时长" />
            <Button label="短时预测" icon="pi pi-chart-line" :loading="predicting" :disabled="!counts || countLoading || !cutoff" @click="predict" />
          </div></div>
          <div class="chart-legend"><span><i class="legend-line" />实际计数（模型估计）</span><span><i class="legend-line forecast" />未来画面人数预测</span><span v-if="hasInterval"><i class="legend-band" />预测区间</span></div>
          <Message v-if="forecastError" severity="error">{{ forecastError }}</Message>
          <Message v-if="forecastNotice" severity="warn">{{ forecastNotice }}</Message>
          <TrendChart v-if="completeCounts.length" :points="completeCounts" :forecast="forecast?.status === 'ready' ? forecast.forecast_points : []" short :relative="isRelative" label="画面可见人数" history-label="计数估计" unit="人" />
          <StatePanel v-else kind="empty" :message="counts ? grain === '5min' && duration < 300 ? '录像不足5分钟，请选择“逐帧估计”查看计数。' : '当前回放位置之前没有完整计数点。' : '开始计数后自动显示画面人数趋势。'" />
          <div class="chart-footer"><span>单位：人 · {{ grain === '5min' ? '5 分钟中位数' : '逐帧估计' }} · {{ completeCounts.length }} 个计数点</span><span v-if="forecast">{{ forecast.model_version }} · 输入历史 {{ numberText(forecast.input_window_minutes) }} 分钟 · 截止 {{ forecast.training_cutoff?.slice(11,19) }}</span></div>
          <p v-if="isRelative" class="small muted forecast-guidance">横轴为录像经过时间。短时预测需要足够的连续计数历史；当前录像不足时保留计数结果。</p>
          <p v-if="forecast?.validation_error" class="forecast-validation small muted">分跨度回测误差{{ options.mode === 'demo' ? '（模拟示例）' : '' }}：<span v-for="(validation, minutes) in forecast.validation_error" :key="minutes">{{ minutes }} 分钟 MAE {{ numberText(validation?.mae) }} 人；</span></p>
          <p class="small muted forecast-guidance">15 / 30 分钟预测分别以约 60 / 120 分钟有效历史为起步条件；60 分钟需更长连续数据及回测通过，时长不保证准确率。</p>
        </article>
    </template>
  </section>
</template>
<style scoped>
.local-source { margin-top: 12px; line-height: 1.6; overflow-wrap: anywhere; }
.b-ui .counting-controls { align-items: flex-end; flex-wrap: wrap; }
.counting-intro { align-self: center; }
.counting-actions { display: flex; align-items: center; flex-wrap: wrap; gap: 12px; min-width: 0; }
.counting-sample-total { white-space: nowrap; }
.counting-controls h2 { font-size: 16px; }
.counting-controls progress { width: 180px; max-width: 100%; accent-color: #00d4ff; }
.counting-error { flex-basis: 100%; color: #ffb84d; }
</style>
