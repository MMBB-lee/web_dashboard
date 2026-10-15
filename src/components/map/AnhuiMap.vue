<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref, watch } from 'vue'
import { createAnhuiMap } from './three-map'
import { loadMapBoundaries } from './map-data'
import type { MapEntity, MapOptions, MapController } from './map-types'
import './map.css'

const props = withDefaults(defineProps<{
  entities: MapEntity[]
  scenicId?: string
  railId?: string
  airportId?: string
  demo?: boolean
}>(), { demo: false })
const emit = defineEmits<{ select: [entity: MapEntity] }>()
const mapHost = ref<HTMLElement | null>(null)
const loading = ref(true)
const loadError = ref('')
let controller: MapController | null = null
let disposed = false
let attempt = 0

function mapOptions(): MapOptions {
  return {
    entities: props.entities, scenicId: props.scenicId, railId: props.railId,
    airportId: props.airportId, demo: props.demo,
    onSelect: entity => emit('select', entity),
  }
}

async function initialize() {
  const request = ++attempt
  loading.value = true
  loadError.value = ''
  try {
    const data = await loadMapBoundaries(import.meta.env.BASE_URL)
    if (disposed || request !== attempt || !mapHost.value) return
    controller?.dispose()
    controller = null
    controller = createAnhuiMap(mapHost.value, mapOptions(), data)
  } catch (cause) {
    if (disposed || request !== attempt) return
    mapHost.value?.replaceChildren()
    loadError.value = cause instanceof Error ? cause.message : '暂时无法创建 3D 地图。'
  } finally {
    if (!disposed && request === attempt) loading.value = false
  }
}

onMounted(() => { void initialize() })
watch(() => [props.entities, props.scenicId, props.railId, props.airportId, props.demo], () => {
  controller?.setData(mapOptions())
}, { deep: true })
onBeforeUnmount(() => {
  disposed = true
  attempt++
  controller?.dispose()
  controller = null
})
</script>

<template>
  <div class="map-stage three-map-stage" aria-label="安徽省三维行政区地图" :aria-busy="loading">
    <div ref="mapHost" class="three-map-host" />
    <div v-if="loading" class="three-map-status" role="status">正在加载安徽省立体地图…</div>
    <div v-else-if="loadError" class="three-map-status error" role="alert">
      <strong>地图加载失败</strong>
      <span>{{ loadError }}</span>
      <button type="button" @click="initialize">重新加载地图</button>
    </div>
  </div>
</template>
