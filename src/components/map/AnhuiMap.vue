<script setup lang="ts">
import { onMounted, onUnmounted, ref, watch } from 'vue'
import AMapLoader from '@amap/amap-jsapi-loader'
import { formatValue } from '../../app/format'
import type { Entity } from '../../app/types'

type MapEntity = Entity & {
  latest_value?: number | null
  latest_metric?: string
}

type DistrictNode = {
  adcode?: string
  name?: string
  center?: unknown
  districtList?: DistrictNode[]
}

const COUNTY_LABEL_ZOOM = 8.5

const props = defineProps<{ entities: MapEntity[]; scenicId?: string; railId?: string; airportId?: string }>()
const emit = defineEmits<{ select: [entity: MapEntity] }>()

const mapElement = ref<HTMLElement | null>(null)
const loadError = ref('')
const hasProvince = ref(false)
const adminCount = ref({ cities: 0, counties: 0 })
let AMap: any = null
let map: any = null
let markers: any[] = []
let flowOverlays: any[] = []
let movingFlows: Array<{
  marker: any
  path: number[][]
  trailGlow: any
  trailCore: any
  duration: number
  offset: number
}> = []
let flowFrame = 0
let lastFlowPaint = 0
let flowPaused = false
let flowResumeTimer = 0
let provincePolygons: any[] = []
let adminMarkers: any[] = []
let provinceView: { zoom: number; center: any } | null = null
let dragEnabled: boolean | null = null
let resizeObserver: ResizeObserver | null = null
let resizeFrame = 0
let disposed = false

function drawMarkers() {
  if (!AMap || !map) return

  if (markers.length) map.remove(markers)

  markers = props.entities
    .filter(entity =>
      entity.longitude != null &&
      entity.latitude != null &&
      Number.isFinite(entity.longitude) &&
      Number.isFinite(entity.latitude),
    )
    .map(entity => {
      // 用 DOM 节点和 textContent，避免把地点名称拼进 HTML 字符串。
      const content = document.createElement('div')
      const selected = entity.id === props.scenicId || entity.id === props.railId || entity.id === props.airportId
      content.className = `entity-pin ${entity.type}${selected ? ' selected' : ''}`
      content.tabIndex = 0
      content.setAttribute('role', 'button')

      const dot = document.createElement('span')
      dot.className = 'pin-dot'
      dot.setAttribute('aria-hidden', 'true')

      const valueText = entity.latest_value == null
        ? '暂无数值'
        : `${formatValue(entity.latest_value)} 人次`
      content.setAttribute('aria-label', `${entity.name}，${valueText}`)

      const tooltip = document.createElement('span')
      tooltip.className = 'pin-tooltip'
      tooltip.setAttribute('aria-hidden', 'true')

      const name = document.createElement('strong')
      name.textContent = entity.name
      const value = document.createElement('span')
      value.textContent = valueText
      tooltip.append(name, value)
      content.append(dot, tooltip)

      const marker = new AMap.Marker({
        position: [entity.longitude!, entity.latitude!],
        anchor: 'center',
        content,
        zIndex: 30,
      })

      marker.on('click', () => emit('select', entity))
      content.addEventListener('keydown', event => {
        if (event.key !== 'Enter' && event.key !== ' ') return
        event.preventDefault()
        event.stopPropagation()
        emit('select', entity)
      })
      return marker
    })

  if (markers.length) map.add(markers)
}

function clearFlows() {
  if (flowFrame) cancelAnimationFrame(flowFrame)
  flowFrame = 0
  lastFlowPaint = 0
  if (map && flowOverlays.length) map.remove(flowOverlays)
  flowOverlays = []
  movingFlows = []
}

function curvedPath(from: MapEntity, to: MapEntity, side: number): number[][] {
  const start = [from.longitude!, from.latitude!]
  const end = [to.longitude!, to.latitude!]
  const dx = end[0] - start[0]
  const dy = end[1] - start[1]
  const distance = Math.hypot(dx, dy)
  if (distance < 0.001) return []
  const bend = Math.min(.38, Math.max(.12, distance * .27)) * side
  const control = [(start[0] + end[0]) / 2 - dy / distance * bend, (start[1] + end[1]) / 2 + dx / distance * bend]
  return Array.from({ length: 49 }, (_, index) => {
    const t = index / 48
    const inverse = 1 - t
    return [
      inverse * inverse * start[0] + 2 * inverse * t * control[0] + t * t * end[0],
      inverse * inverse * start[1] + 2 * inverse * t * control[1] + t * t * end[1],
    ]
  })
}

function animateFlows(time: number) {
  if (flowPaused || !map) {
    flowFrame = 0
    return
  }
  // 地图的线段更新比 DOM 动画重，约 30 帧即可保持流动感。
  if (time - lastFlowPaint < 32) {
    flowFrame = requestAnimationFrame(animateFlows)
    return
  }
  lastFlowPaint = time
  for (const flow of movingFlows) {
    const position = ((time / flow.duration + flow.offset) % 1) * (flow.path.length - 1)
    const index = Math.floor(position)
    const fraction = position - index
    const current = flow.path[index]
    const next = flow.path[Math.min(index + 1, flow.path.length - 1)]
    const head = [
      current[0] + (next[0] - current[0]) * fraction,
      current[1] + (next[1] - current[1]) * fraction,
    ]
    const trail = flow.path.slice(Math.max(0, index - 8), index + 1)
    trail.push(head)
    flow.trailGlow.setPath(trail)
    flow.trailCore.setPath(trail)
    flow.marker.setPosition(head)
  }
  flowFrame = requestAnimationFrame(animateFlows)
}

function drawFlows() {
  clearFlows()
  if (!AMap || !map) return
  const scenic = props.entities.find(entity => entity.id === props.scenicId)
  if (scenic?.longitude == null || scenic.latitude == null) return

  for (const [id, kind, side, duration, offset] of [
    [props.railId, 'rail_station', 1, 3800, 0],
    [props.airportId, 'airport', -1, 4600, .48],
  ] as const) {
    const origin = props.entities.find(entity => entity.id === id)
    if (origin?.longitude == null || origin.latitude == null) continue
    const path = curvedPath(origin, scenic, side)
    if (!path.length) continue
    const color = kind === 'rail_station' ? '#00f2a9' : '#ffb84d'
    const glow = new AMap.Polyline({
      path, strokeColor: color, strokeOpacity: .17, strokeWeight: 6,
      zIndex: 20, bubble: true,
    })
    const line = new AMap.Polyline({
      path, strokeColor: color, strokeOpacity: .7, strokeWeight: 1.5,
      zIndex: 21, bubble: true,
    })
    const trailGlow = new AMap.Polyline({
      path: [], strokeColor: color, strokeOpacity: .35, strokeWeight: 8,
      zIndex: 22, bubble: true,
    })
    const trailCore = new AMap.Polyline({
      path: [], strokeColor: color, strokeOpacity: 1, strokeWeight: 3,
      zIndex: 23, bubble: true,
    })
    const particle = document.createElement('span')
    particle.className = `flow-particle ${kind}`
    particle.setAttribute('aria-hidden', 'true')
    const marker = new AMap.Marker({ position: path[0], anchor: 'center', content: particle, zIndex: 45 })
    flowOverlays.push(glow, line, trailGlow, trailCore, marker)
    movingFlows.push({ marker, path, trailGlow, trailCore, duration, offset })
  }
  if (!flowOverlays.length) return
  map.add(flowOverlays)
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
    for (const flow of movingFlows) flow.marker.setPosition(flow.path[Math.floor(flow.path.length / 2)])
  } else if (!flowPaused) flowFrame = requestAnimationFrame(animateFlows)
}

function pauseFlowAnimation() {
  flowPaused = true
  if (flowFrame) cancelAnimationFrame(flowFrame)
  flowFrame = 0
}

function resumeFlowAnimation() {
  flowPaused = false
  if (movingFlows.length && !flowFrame && !window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
    lastFlowPaint = 0
    flowFrame = requestAnimationFrame(animateFlows)
  }
}

function addAdminLabel(name: string, position: unknown, kind: 'city' | 'county'): void {
  if (!AMap || !map || !position) return

  const content = document.createElement('span')
  content.className = `admin-label ${kind}`
  content.textContent = name

  adminMarkers.push(new AMap.Marker({
    position,
    anchor: 'center',
    content,
    zIndex: kind === 'city' ? 13 : 12,
    zooms: kind === 'city' ? [2, COUNTY_LABEL_ZOOM - 0.01] : [COUNTY_LABEL_ZOOM, 20],
  }))
}

function drawAdministrativeAreas(province: DistrictNode): void {
  if (!AMap || !map) return

  const cities = (province.districtList ?? []).filter(city => city.name && city.adcode && city.center)
  const seenCounties = new Set<string>()
  for (const city of cities) {
    const counties = (city.districtList ?? []).filter(county => county.name && county.adcode && county.center && county.name !== '市辖区')
    for (const county of counties) {
      if (seenCounties.has(county.adcode!)) continue
      seenCounties.add(county.adcode!)
      addAdminLabel(county.name!, county.center, 'county')
    }
    addAdminLabel(city.name!, city.center, 'city')
  }

  if (adminMarkers.length) map.add(adminMarkers)
  adminCount.value = { cities: cities.length, counties: seenCounties.size }

  if (AMap.DistrictLayer?.Province) {
    const cityBorders = new AMap.DistrictLayer.Province({ adcode: ['340000'], depth: 1, zIndex: 3 })
    cityBorders.setStyles({
      'fill': '',
      'stroke-width': 1,
      'province-stroke': '',
      'city-stroke': 'rgba(102, 172, 215, .58)',
    })
    map.add(cityBorders)

    const countyBorders = new AMap.DistrictLayer.Province({
      adcode: ['340000'], depth: 2, zIndex: 4, zooms: [COUNTY_LABEL_ZOOM, 20],
    })
    countyBorders.setStyles({
      'fill': '',
      'stroke-width': 1,
      'province-stroke': '',
      'city-stroke': '',
      'county-stroke': 'rgba(117, 159, 195, .42)',
    })
    map.add(countyBorders)
  }
}

function showWholeProvince() {
  if (!map || !provinceView) return
  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
  if (flowResumeTimer) clearTimeout(flowResumeTimer)
  if (reduceMotion) {
    map.setZoomAndCenter(provinceView.zoom, provinceView.center, true)
  } else {
    pauseFlowAnimation()
    map.setZoomAndCenter(provinceView.zoom, provinceView.center, false, 480)
    flowResumeTimer = window.setTimeout(() => {
      flowResumeTimer = 0
      resumeFlowAnimation()
    }, 560)
  }
  syncDragState()
}

function syncDragState() {
  if (!map || !provinceView) return
  const shouldEnable = map.getZoom() > provinceView.zoom + 0.001
  if (dragEnabled === shouldEnable) return
  dragEnabled = shouldEnable
  map.setStatus({ dragEnable: shouldEnable })
}

function fitAndLockProvince() {
  if (!map || !provincePolygons.length) return

  // 先按当前容器尺寸完整展示安徽，再把这幅画面固定为最小视图。
  provinceView = null
  map.clearLimitBounds()
  map.setZooms([2, 20])
  map.setFitView(provincePolygons, true, [14, 14, 14, 14])

  provinceView = { zoom: map.getZoom(), center: map.getCenter() }
  map.setZooms([provinceView.zoom, 20])
  // 当前 JS API 返回的 Bounds 可直接用作限制拖动的四个边界。
  map.setLimitBounds(map.getBounds())
  syncDragState()
}

onMounted(async () => {
  const key = import.meta.env.VITE_AMAP_KEY
  const securityCode = import.meta.env.VITE_AMAP_SECURITY_CODE

  if (!key || !securityCode) {
    loadError.value = '请先在 .env.local 配置高德 Key 和安全密钥。'
    return
  }
  if (!mapElement.value) return

  try {
    // 必须在加载 JS API 之前设置。
    ;(window as Window & {
      _AMapSecurityConfig?: { securityJsCode: string }
    })._AMapSecurityConfig = { securityJsCode: securityCode }

    AMap = await AMapLoader.load({
      key,
      version: '2.0',
      plugins: ['AMap.DistrictSearch', 'AMap.DistrictLayer'],
    })
    if (disposed || !mapElement.value) return

    map = new AMap.Map(mapElement.value, {
      // 地图掩模仅在 3D 视图生效；俯视角保持二维的视觉效果。
      viewMode: '3D',
      pitch: 0,
      pitchEnable: false,
      rotateEnable: false,
      mapStyle: 'amap://styles/darkblue',
      center: [117.3, 31.8],
      zoom: 7,
      showLabel: false,
      dragEnable: false,
      zoomEnable: true,
      scrollWheel: true,
      resizeEnable: true,
    })

    map.on('zoomchange', syncDragState)
    resizeObserver = new ResizeObserver(() => {
      if (!provinceView || disposed) return
      cancelAnimationFrame(resizeFrame)
      resizeFrame = requestAnimationFrame(fitAndLockProvince)
    })
    resizeObserver.observe(mapElement.value)

    // 省外只显示省级轮廓；此图层不受安徽底图掩模影响。
    const provinceBorders = new AMap.DistrictLayer.Country({
      SOC: 'CHN',
      depth: 2,
      zIndex: 1,
      rejectMapMask: true,
    })
    provinceBorders.setStyles({
      'fill': '',
      'nation-stroke': '',
      'province-stroke': 'rgba(112, 163, 205, 0.42)',
      'city-stroke': '',
    })
    map.add(provinceBorders)

    drawMarkers()
    drawFlows()

    const district = new AMap.DistrictSearch({
      level: 'province',
      subdistrict: 2,
      extensions: 'all',
    })

    district.search('安徽省', (status: string, result: any) => {
      if (disposed || !map) return
      const boundaries = status === 'complete'
        ? result.districtList?.[0]?.boundaries ?? []
        : []

      if (!boundaries.length) {
        loadError.value = '安徽省边界加载失败，请检查 Key、密钥和网络。'
        return
      }

      // 高德示例的掩模格式为 [ [行政区边界路径], ... ]。
      map.setMask(boundaries.map((path: any) => [path]))

      provincePolygons = boundaries.map((path: any) =>
        new AMap.Polygon({
          path,
          strokeColor: '#00d4ff',
          strokeWeight: 2,
          fillColor: '#103a69',
          fillOpacity: 0.18,
          bubble: true,
        }),
      )

      map.add(provincePolygons)
      hasProvince.value = true
      fitAndLockProvince()
      drawAdministrativeAreas(result.districtList[0])
    })
  } catch {
    loadError.value = '高德地图加载失败，请检查 Key、密钥和网络。'
  }
})

watch(() => [props.entities, props.scenicId, props.railId, props.airportId], () => {
  drawMarkers()
  drawFlows()
}, { deep: true })

onUnmounted(() => {
  disposed = true
  resizeObserver?.disconnect()
  cancelAnimationFrame(resizeFrame)
  if (flowResumeTimer) clearTimeout(flowResumeTimer)
  clearFlows()
  map?.destroy()
  map = null
})
</script>

<template>
  <div class="map-stage">
    <div ref="mapElement" class="map-canvas" />

    <div class="map-legend">
      <span><i class="legend-dot scenic" />景区</span>
      <span><i class="legend-dot rail_station" />铁路站</span>
      <span><i class="legend-dot airport" />机场</span>
    </div>

    <div v-if="adminCount.cities" class="admin-hint">
      {{ adminCount.cities }} 个地级市 · {{ adminCount.counties }} 个区县
    </div>

    <button
      v-if="hasProvince"
      class="reset-button"
      type="button"
      @click="showWholeProvince"
    >
      返回全省
    </button>

    <div v-if="loadError" class="map-error" role="status">
      {{ loadError }}
    </div>
  </div>
</template>

<style scoped>
.map-stage {
  position: relative;
  flex: 1;
  min-height: 390px;
  min-width: 0;
  overflow: hidden;
  border: 1px solid rgba(105, 158, 210, .13);
  background: #091d3b;
}

.map-canvas {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  /* 高德会给地图容器注入网格背景；掩模外保持纯深蓝。 */
  background-color: #091d3b !important;
  background-image: none !important;
}

/* 高德署名是一张图片：文字转白，再覆盖还原左侧的原始彩色图标。 */
.map-canvas :deep(.amap-logo img) {
  filter: brightness(0) invert(1);
}
.map-canvas :deep(.amap-logo::after) {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  width: 21px;
  height: 20px;
  background: url('https://webapi.amap.com/theme/v2.0/logo@2x.png') left top / 73px 20px no-repeat;
  pointer-events: none;
}

.map-legend {
  position: absolute;
  z-index: 2;
  top: 12px;
  left: 12px;
  display: flex;
  gap: 12px;
  padding: 7px 10px;
  background: rgba(6, 20, 42, .82);
  color: #c9e4f6;
  font-size: 12px;
  pointer-events: none;
}

.map-legend span {
  display: flex;
  align-items: center;
  gap: 5px;
}

.legend-dot {
  width: 7px;
  height: 7px;
  background: #00d4ff;
}
.legend-dot.rail_station { background: #00f2a9; }
.legend-dot.airport { background: #ffb84d; }

.admin-hint {
  position: absolute;
  z-index: 2;
  top: 50px;
  left: 12px;
  padding: 5px 8px;
  background: rgba(6, 20, 42, .78);
  color: #8fb5d2;
  font-size: 11px;
  pointer-events: none;
}

:deep(.admin-label) {
  display: inline-block;
  color: #d5eaff;
  font-size: 12px;
  font-weight: 600;
  line-height: 1.25;
  white-space: nowrap;
  text-shadow: 0 1px 4px #06152c, 0 0 5px #06152c;
  pointer-events: none;
}

:deep(.admin-label.city) {
  color: #8faec8;
  font-size: 10px;
  font-weight: 400;
  text-shadow: 0 1px 3px #06152c;
}

:deep(.admin-label.county) {
  color: #a9c8df;
  font-size: 11px;
  font-weight: 400;
}

.map-canvas :deep(.amap-marker:has(.admin-label)) {
  pointer-events: none !important;
}

.reset-button {
  position: absolute;
  z-index: 2;
  top: 12px;
  right: 12px;
  min-height: 27px;
  padding: 4px 10px;
  border: 1px solid #417da5;
  border-radius: 999px;
  background: rgba(6, 20, 42, .86);
  color: #e6f7ff;
  font-size: 12px;
  line-height: 1.2;
  cursor: pointer;
}

.map-error {
  position: absolute;
  z-index: 2;
  top: 49px;
  left: 12px;
  max-width: calc(100% - 24px);
  padding: 8px 10px;
  background: rgba(88, 31, 31, .92);
  color: white;
  font-size: 12px;
}

:deep(.entity-pin) {
  position: relative;
  display: grid;
  place-items: center;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: transparent;
  cursor: pointer;
}

:deep(.entity-pin:focus-visible) {
  outline: 1px solid #e6f7ff;
  outline-offset: 2px;
}

:deep(.pin-dot) {
  width: 9px;
  height: 9px;
  border-radius: 50%;
  background: #00d4ff;
  box-shadow: 0 0 8px #00d4ff;
}
:deep(.entity-pin.rail_station .pin-dot) {
  background: #00f2a9;
  box-shadow: 0 0 8px #00f2a9;
}
:deep(.entity-pin.airport .pin-dot) {
  background: #ffb84d;
  box-shadow: 0 0 8px #ffb84d;
}
:deep(.entity-pin.selected .pin-dot) {
  width: 12px;
  height: 12px;
  outline: 1px solid rgba(255, 255, 255, .8);
  outline-offset: 3px;
}
:deep(.entity-pin.selected::before) {
  content: '';
  position: absolute;
  width: 18px;
  height: 18px;
  border: 1px solid #00d4ff;
  border-radius: 50%;
  box-shadow: 0 0 10px #00d4ff;
  pointer-events: none;
  animation: selected-pin-pulse 2.4s ease-out infinite;
}
:deep(.entity-pin.selected.rail_station::before) {
  border-color: #00f2a9;
  box-shadow: 0 0 10px #00f2a9;
}
:deep(.entity-pin.selected.airport::before) {
  border-color: #ffb84d;
  box-shadow: 0 0 10px #ffb84d;
}
@keyframes selected-pin-pulse {
  0% { opacity: .85; transform: scale(.8); }
  100% { opacity: 0; transform: scale(2.2); }
}
:deep(.flow-particle) {
  display: block;
  width: 8px;
  height: 8px;
  border: 1px solid white;
  border-radius: 50%;
  background: #eafff7;
  box-shadow: 0 0 5px 2px #00f2a9, 0 0 13px 5px #00f2a9;
  pointer-events: none;
}
:deep(.flow-particle.airport) {
  background: #fff7e8;
  box-shadow: 0 0 5px 2px #ffb84d, 0 0 13px 5px #ffb84d;
}

:deep(.pin-tooltip) {
  position: absolute;
  bottom: calc(100% + 6px);
  left: 50%;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
  padding: 6px 9px;
  border: 1px solid rgba(0, 212, 255, .5);
  background: rgba(6, 20, 42, .96);
  color: #e6f7ff;
  font-size: 12px;
  white-space: nowrap;
  pointer-events: none;
  opacity: 0;
  visibility: hidden;
  transform: translateX(-50%);
  transition: opacity .12s ease, visibility .12s ease;
}

:deep(.pin-tooltip > span) {
  color: #9fc5df;
}

:deep(.entity-pin:hover .pin-tooltip),
:deep(.entity-pin:focus-visible .pin-tooltip) {
  opacity: 1;
  visibility: visible;
}

.map-canvas :deep(.amap-marker:has(.entity-pin):hover),
.map-canvas :deep(.amap-marker:has(.entity-pin):focus-within) {
  z-index: 1000 !important;
}

@media (max-width: 640px) {
  .map-stage { min-height: 330px; }
  .map-legend { gap: 7px; font-size: 11px; }
}
@media (prefers-reduced-motion: reduce) {
  :deep(.entity-pin.selected::before) { animation: none; opacity: .5; }
}
</style>
