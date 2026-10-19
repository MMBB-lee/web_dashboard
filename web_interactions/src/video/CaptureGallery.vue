<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import Button from 'primevue/button'
import Select from 'primevue/select'
import Message from 'primevue/message'
import { useInteractions } from '../module'
import { errorMessage, query } from '../api/client'
import type { CaptureFolder, CaptureImage } from '../types/api'

const props = defineProps<{ analysisId?: string; taskStatus?: string }>()
const { client } = useInteractions()
const folders = ref<CaptureFolder[]>([]), images = ref<CaptureImage[]>([])
const folderId = ref(''), imageName = ref(''), loadingFolders = ref(false), loadingImages = ref(false)
const error = ref(''), imageError = ref(false)
const selectedImage = computed(() => images.value.find(image => image.name === imageName.value))
const imageIndex = computed(() => images.value.findIndex(image => image.name === imageName.value))
const selectedFolder = computed(() => folders.value.find(folder => folder.id === folderId.value))
let folderVersion = 0, imageVersion = 0, timer: ReturnType<typeof setInterval> | undefined

async function loadImages() {
  const current = ++imageVersion
  images.value = []; imageName.value = ''; imageError.value = false; error.value = ''
  if (!folderId.value) { loadingImages.value = false; return }
  loadingImages.value = true
  try {
    const result = (await client.get<CaptureImage[]>(`/videos/capture-images?${query({ folder: folderId.value })}`)).data
    if (current !== imageVersion) return
    images.value = result; imageName.value = result[0]?.name ?? ''
  } catch (cause) { if (current === imageVersion) error.value = errorMessage(cause) }
  finally { if (current === imageVersion) loadingImages.value = false }
}

async function refresh(preferCurrent = false) {
  const current = ++folderVersion
  loadingFolders.value = true; error.value = ''
  try {
    const result = (await client.get<CaptureFolder[]>('/videos/capture-folders')).data
    if (current !== folderVersion) return
    const previous = folderId.value
    const previousCount = selectedFolder.value?.image_count
    folders.value = result
    const matching = result.find(folder => folder.analysis_id === props.analysisId)
    if (preferCurrent && matching) folderId.value = matching.id
    else if (!result.some(folder => folder.id === folderId.value)) folderId.value = matching?.id ?? result[0]?.id ?? ''
    if (previous !== folderId.value || previousCount !== selectedFolder.value?.image_count || preferCurrent) {
      const selected = imageName.value
      await loadImages()
      if (previous === folderId.value && images.value.some(image => image.name === selected)) imageName.value = selected
    }
  } catch (cause) { if (current === folderVersion) error.value = errorMessage(cause) }
  finally { if (current === folderVersion) loadingFolders.value = false }
}

function selectImage(step: number) { imageName.value = images.value[imageIndex.value + step]?.name ?? imageName.value }
watch(imageName, () => { imageError.value = false })
watch(() => props.analysisId, () => { void refresh(true) })
watch(() => props.taskStatus, (status, previous) => { if (status === 'ready' && previous !== 'ready') void refresh(true) })
onMounted(() => {
  void refresh(true)
  timer = setInterval(() => { if (!loadingFolders.value && !loadingImages.value) void refresh() }, 15000)
})
onBeforeUnmount(() => { ++folderVersion; ++imageVersion; clearInterval(timer) })
</script>

<template>
  <article class="panel capture-gallery">
    <div class="panel-heading"><div><h2>检测图片</h2><p>查看录像抽帧检测结果，图片保留人头标记与对应人数。</p></div><Button label="刷新图片目录" icon="pi pi-refresh" outlined :loading="loadingFolders" @click="refresh(true)" /></div>
    <div class="capture-controls">
      <label class="field capture-folder">检测文件夹<Select v-model="folderId" :options="folders.map(folder => ({ value: folder.id, label: `${folder.name} · ${folder.image_count} 张` }))" option-label="label" option-value="value" placeholder="选择检测时间" aria-label="检测文件夹" :disabled="loadingFolders || !folders.length" :pt="{ overlay: { class: 'b-ui b-select-overlay' } }" @change="loadImages" /></label>
      <label class="field capture-file">检测图片<Select v-model="imageName" :options="images.map(image => ({ value: image.name, label: image.name }))" option-label="label" option-value="value" placeholder="选择图片" aria-label="检测图片" :disabled="loadingImages || !images.length" :pt="{ overlay: { class: 'b-ui b-select-overlay' } }" /></label>
      <div class="capture-navigation"><Button icon="pi pi-chevron-left" label="上一张" outlined :disabled="imageIndex <= 0 || loadingImages" @click="selectImage(-1)" /><Button icon="pi pi-chevron-right" label="下一张" outlined :disabled="imageIndex < 0 || imageIndex >= images.length - 1 || loadingImages" @click="selectImage(1)" /></div>
    </div>
    <Message v-if="error" severity="error">{{ error }}</Message>
    <p v-else-if="loadingImages" class="muted capture-empty" role="status">正在读取检测图片…</p>
    <template v-else-if="selectedImage">
      <Message v-if="imageError" severity="warn">图片暂时无法读取，可能已移除或登录已失效，请刷新图片目录后重试。</Message>
      <figure v-else class="capture-preview"><img :key="`${folderId}/${selectedImage.name}`" :src="selectedImage.url" :alt="`检测图片：${selectedImage.name}`" @error="imageError = true" /><figcaption><span>{{ selectedImage.name }}</span><span>第 {{ imageIndex + 1 }} / {{ images.length }} 张</span></figcaption></figure>
    </template>
    <p v-else class="muted capture-empty" role="status">{{ loadingFolders ? '正在读取检测文件夹…' : folders.length ? '当前文件夹暂无检测图片，计数过程中可刷新查看。' : '暂无检测图片，开始计数后会自动保存并显示。' }}</p>
    <p class="small muted capture-note">按北京时间保存，每个检测文件夹对应一次分析。{{ selectedFolder?.legacy ? '当前为历史目录，原有截图仍可查看。' : '' }}</p>
  </article>
</template>

<style scoped>
.capture-gallery { min-width: 0; }
.capture-controls { display: flex; flex-wrap: wrap; align-items: flex-end; gap: 16px; margin: 20px 0; }
.capture-folder, .capture-file { flex: 1 1 260px; min-width: 0; }
.capture-controls :deep(.p-select) { width: 100%; min-width: 0; }
.capture-controls :deep(.p-select-label) { overflow: hidden; text-overflow: ellipsis; }
.capture-navigation { display: flex; gap: 10px; flex-wrap: wrap; }
.capture-preview { margin: 0; }
.capture-preview img { display: block; width: 100%; max-height: 560px; object-fit: contain; background: #061222; border: 1px solid #214768; }
.capture-preview figcaption { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 8px; padding-top: 12px; color: #a2bfda; font-size: 13px; overflow-wrap: anywhere; }
.capture-empty { padding: 28px 0; text-align: center; }
.capture-note { margin-top: 12px; }
@media (max-width: 680px) { .capture-gallery .panel-heading { align-items: flex-start; flex-direction: column; gap: 12px; }.capture-navigation { width: 100%; }.capture-preview img { max-height: 340px; } }
</style>
