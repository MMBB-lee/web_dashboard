<script setup lang="ts">
import { computed } from 'vue'
import { formatValue } from '../../app/format'
import type { Entity } from '../../app/types'

type MapEntity = Entity & { latest_value?: number | null; latest_metric?: string }
const props = defineProps<{ entities: MapEntity[] }>()
const emit = defineEmits<{ select: [entity: MapEntity] }>()
const positioned = computed(() => props.entities.filter((entity) => entity.longitude != null && entity.latitude != null).map((entity) => ({
  ...entity,
  left: `${Math.min(89, Math.max(11, 12 + ((entity.longitude! - 115.5) / 4.3) * 76))}%`,
  top: `${Math.min(86, Math.max(14, 12 + ((34.7 - entity.latitude!) / 5.4) * 72))}%`,
})))
</script>

<template>
  <div class="map-stage">
    <div class="map-grid" />
    <svg class="map-shape" viewBox="0 0 600 420" aria-hidden="true">
      <defs>
        <linearGradient id="map-fill" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#103a69" stop-opacity=".74" /><stop offset="1" stop-color="#0b2850" stop-opacity=".24" /></linearGradient>
        <filter id="map-glow"><feGaussianBlur stdDeviation="7" /></filter>
      </defs>
      <path class="map-glow" d="M256 20 L316 45 L325 82 L388 100 L401 146 L451 172 L426 217 L467 250 L442 293 L402 310 L386 368 L330 398 L291 367 L253 383 L218 352 L157 349 L135 307 L151 260 L105 228 L128 177 L166 148 L181 99 L223 86 Z" />
      <path class="map-outline" d="M256 20 L316 45 L325 82 L388 100 L401 146 L451 172 L426 217 L467 250 L442 293 L402 310 L386 368 L330 398 L291 367 L253 383 L218 352 L157 349 L135 307 L151 260 L105 228 L128 177 L166 148 L181 99 L223 86 Z" />
      <path d="M165 149 C260 155 318 217 426 217 M151 260 C244 237 317 280 442 293 M223 86 C246 175 247 258 253 383" stroke="rgba(74,160,216,.24)" stroke-width="1" fill="none" stroke-dasharray="5 7" />
    </svg>
    <div class="map-coordinate north">N 34°</div><div class="map-coordinate south">N 29°</div>
    <button v-for="entity in positioned" :key="entity.id" type="button" :class="['map-marker', entity.type]" :style="{ left: entity.left, top: entity.top }" :title="`${entity.name} · ${entity.latest_value == null ? '暂无数值' : formatValue(entity.latest_value) + ' 人次'}`" @click="emit('select', entity)">
      <span class="marker-dot" /><span class="marker-label">{{ entity.name }}</span>
    </button>
    <div class="map-legend"><span><i class="legend-dot scenic" />景区</span><span><i class="legend-dot rail_station" />铁路站</span><span><i class="legend-dot airport" />机场</span></div>
    <div class="map-disclaimer">对象位置示意 · 非行政边界地图</div>
  </div>
</template>

<style scoped>
.map-stage { position: relative; height: 390px; overflow: hidden; border: 1px solid rgba(105, 158, 210, .13); background: radial-gradient(circle at 52% 52%, rgba(25, 89, 151, .23), transparent 53%), #091d3b; }
.map-grid { position: absolute; inset: 0; background-image: linear-gradient(rgba(56, 125, 182, .1) 1px, transparent 1px), linear-gradient(90deg, rgba(56, 125, 182, .1) 1px, transparent 1px); background-size: 30px 30px; mask-image: radial-gradient(ellipse at center, black, transparent 82%); }
.map-shape { position: absolute; width: min(72%, 550px); height: 94%; top: 2%; left: 50%; transform: translateX(-50%); }
.map-glow { fill: none; stroke: rgba(0, 212, 255, .58); stroke-width: 9; filter: url(#map-glow); }
.map-outline { fill: url(#map-fill); stroke: rgba(80, 200, 244, .72); stroke-width: 2; }
.map-coordinate { position: absolute; left: 17px; color: #6e9fc5; font: 12px Consolas, monospace; }
.map-coordinate.north { top: 16px; }.map-coordinate.south { bottom: 46px; }
.map-marker { position: absolute; display: flex; align-items: center; gap: 7px; padding: 5px 7px; transform: translate(-8px, -50%); border: 0; background: rgba(6, 20, 42, .75); color: #e6f7ff; font-size: clamp(12px, .7vw, 14px); white-space: nowrap; z-index: 2; }
.map-marker:hover { background: rgba(9, 44, 79, .96); outline: 1px solid rgba(0,212,255,.5); }
.marker-dot { position: relative; display: block; width: 9px; height: 9px; flex: none; border: 2px solid #00d4ff; background: #072c4e; box-shadow: 0 0 12px #00d4ff; }
.marker-dot::after { content: ''; position: absolute; inset: -7px; border: 1px solid currentColor; color: #00d4ff; opacity: .32; animation: radar 2.8s infinite; }
.map-marker.rail_station .marker-dot { border-color: #00f2a9; box-shadow: 0 0 10px #00f2a9; }.map-marker.rail_station .marker-dot::after { color: #00f2a9; }
.map-marker.airport .marker-dot { border-color: #ffb84d; box-shadow: 0 0 10px #ffb84d; }.map-marker.airport .marker-dot::after { color: #ffb84d; }
.map-legend { position: absolute; left: 15px; bottom: 14px; display: flex; flex-wrap: wrap; gap: 13px; color: #9cb8d3; font-size: 12px; }
.map-legend span { display: flex; align-items: center; gap: 5px; }.legend-dot { display: block; width: 6px; height: 6px; background: #00d4ff; }.legend-dot.rail_station { background: #00f2a9; }.legend-dot.airport { background: #ffb84d; }
.map-disclaimer { position: absolute; right: 13px; bottom: 14px; color: #8aaecd; font-size: 11px; }
@keyframes radar { 50% { transform: scale(1.35); opacity: .08; } }
@media (max-width: 640px) { .map-stage { height: 330px; }.map-marker { font-size: 11px; }.map-disclaimer { bottom: 35px; } }
</style>
