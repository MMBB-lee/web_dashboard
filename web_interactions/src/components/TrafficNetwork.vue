<script setup lang="ts">
import { computed } from 'vue'
import type { Entity } from '../types/api'
import { boundaryPath, locateTransportEntities, mapHeight, mapWidth } from '../transport/geography'
import { useInteractions } from '../module'
const props = defineProps<{ entities: Entity[]; activeId: string; latestValue?: number | null; latestPeriodEnd?: string; latestGrain?: string }>()
const emit = defineEmits<{ select: [entity: Entity] }>()
const { options } = useInteractions()
const locations = computed(() => locateTransportEntities(props.entities))
const nodes = computed(() => locations.value.nodes)
const mapEntities = computed(() => nodes.value.map(node => ({
  ...node.entity, longitude: node.longitude, latitude: node.latitude,
  latest_value: node.entity.id === props.activeId ? props.latestValue ?? node.entity.latest_value ?? null : node.entity.latest_value ?? null,
  latest_period_end: node.entity.id === props.activeId ? props.latestPeriodEnd ?? node.entity.latest_period_end : node.entity.latest_period_end,
  latest_grain: node.entity.id === props.activeId ? props.latestGrain ?? node.entity.latest_grain : node.entity.latest_grain,
})))
const railId = computed(() => mapEntities.value.find(entity => entity.id === props.activeId && entity.type === 'rail_station')?.id
  ?? mapEntities.value.find(entity => entity.type === 'rail_station')?.id)
const airportId = computed(() => mapEntities.value.find(entity => entity.id === props.activeId && entity.type === 'airport')?.id
  ?? mapEntities.value.find(entity => entity.type === 'airport')?.id)
</script>

<template>
  <div class="network-visual" :class="{ 'three-transport': options.transportMap }">
    <component v-if="options.transportMap" :is="options.transportMap" :entities="mapEntities" :rail-id="railId" :airport-id="airportId" :demo="options.mode === 'demo'" @select="emit('select', $event)" />
    <svg v-else :viewBox="`0 0 ${mapWidth} ${mapHeight}`" role="img" aria-label="安徽交通枢纽地理分布图，北向上，位置按经纬度投影">
      <defs>
        <filter id="network-glow"><feGaussianBlur stdDeviation="5" result="blur"/><feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
      </defs>
      <path class="network-boundary" :d="boundaryPath" fill-rule="evenodd" />
      <g class="network-north" transform="translate(705 58)" aria-hidden="true"><path d="M0 22V-15M-7 -3L0 -17L7 -3"/><text x="0" y="-28" text-anchor="middle">北 N</text></g>
      <g v-for="node in nodes" :key="node.entity.id" :data-entity-id="node.entity.id" :data-longitude="node.longitude" :data-latitude="node.latitude" :transform="`translate(${node.x} ${node.y})`" :class="['network-node', { active: node.entity.id === activeId }]">
        <title>{{ node.entity.name }} · {{ node.longitude.toFixed(4) }}°E，{{ node.latitude.toFixed(4) }}°N</title>
        <path class="node-leader" :d="`M0 0L${node.labelX / 2} ${node.labelY}L${node.labelX} ${node.labelY}`" />
        <circle class="network-ripple" r="18"/><circle class="network-core" r="6" filter="url(#network-glow)"/><circle class="network-center" r="2.5"/>
        <text :x="node.labelX" :y="node.labelY - 5" :text-anchor="node.labelAnchor">{{ node.entity.name }}</text><text :x="node.labelX" :y="node.labelY + 12" :text-anchor="node.labelAnchor" class="node-type">{{ node.entity.type === 'airport' ? '机场 · 旅客吞吐量' : '铁路站 · 发送量' }}</text>
      </g>
    </svg>
    <p v-if="locations.unlocated.length" class="network-unlocated" role="status">未定位：{{ locations.unlocated.map(entity => entity.name).join('、') }}（缺少有效坐标）</p>
    <div v-if="!options.transportMap" class="network-caption"><span><i class="network-key active" />当前对象</span><span><i class="network-key" />交通枢纽</span><span>近似站址 · 北向上</span></div>
  </div>
</template>

<style scoped>
.network-visual.three-transport { display: flex; flex-direction: column; min-height: 330px; }
.network-visual {
  position: relative;
  width: 100%;
  height: 100%;
  overflow: hidden;
  background:
    radial-gradient(circle at 50% 45%, rgba(0, 173, 255, .1), transparent 42%),
    linear-gradient(rgba(0, 212, 255, .025) 1px, transparent 1px),
    linear-gradient(90deg, rgba(0, 212, 255, .025) 1px, transparent 1px);
  background-size: auto, 34px 34px, 34px 34px;
}

svg { width: 100%; height: calc(100% - 28px); display: block; }

.network-boundary {
  fill: rgba(7, 47, 93, .34);
  stroke: #00bfe8;
  stroke-width: 2;
  stroke-linejoin: round;
  filter: drop-shadow(0 0 10px rgba(0, 196, 255, .22));
}

.network-north path, .node-leader {
  fill: none;
  stroke: rgba(112, 195, 230, .55);
  stroke-width: 1;
}
.network-north text { fill: #82b5d2; font-size: 13px; }
.network-unlocated { position: absolute; left: 15px; bottom: 30px; max-width: calc(100% - 30px); margin: 0; color: #ffb64c; font-size: 10px; }

.network-node text {
  fill: #a8c9e7;
  font-size: 15px;
  font-family: "Microsoft YaHei", sans-serif;
}

.network-node .node-type { fill: #5d83aa; font-size: 11px; }
.network-core { fill: #0aa9dc; stroke: #86edff; stroke-width: 2; }
.network-center { fill: #eaffff; }
.network-ripple { fill: none; stroke: rgba(0, 212, 255, .24); stroke-width: 2; transform-origin: center; animation: node-pulse 2.5s ease-out infinite; }
.network-node.active .network-core { fill: #ffb64c; stroke: #fff1c1; }
.network-node.active .network-ripple { stroke: rgba(255, 182, 76, .55); }
.network-node.active text { fill: #f5fbff; font-weight: 650; }

.network-caption {
  position: absolute;
  right: 15px;
  bottom: 9px;
  display: flex;
  gap: 18px;
  color: #5e84aa;
  font-size: 10px;
}

.network-caption span { display: inline-flex; align-items: center; gap: 5px; }
.network-key { width: 7px; height: 7px; border-radius: 50%; background: #00bde8; box-shadow: 0 0 7px rgba(0, 212, 255, .7); }
.network-key.active { background: #ffb64c; box-shadow: 0 0 7px rgba(255, 182, 76, .7); }

@keyframes node-pulse { 0% { transform: scale(.45); opacity: .9; } 80%, 100% { transform: scale(1.25); opacity: 0; } }

@media (prefers-reduced-motion: reduce) {
  .network-ripple { animation: none; }
}
</style>
