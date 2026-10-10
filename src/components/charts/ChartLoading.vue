<script setup lang="ts">
defineProps<{ kind: 'line' | 'bar' | 'donut'; label?: string }>()
</script>

<template>
  <div class="chart-loading chart-canvas" role="status" aria-live="polite" :aria-label="label ?? '正在读取图表数据'">
    <svg v-if="kind === 'donut'" class="donut-loading" viewBox="0 0 120 120" aria-hidden="true">
      <circle class="donut-track" cx="60" cy="60" r="43" />
      <circle class="donut-progress" cx="60" cy="60" r="43" />
    </svg>
    <div v-else-if="kind === 'bar'" class="bar-loading" aria-hidden="true">
      <span v-for="index in 5" :key="index" :style="{ '--bar-width': `${48 + index * 8}%`, '--bar-delay': `${-120 * index}ms` }" />
    </div>
    <svg v-else class="line-loading" viewBox="0 0 320 130" preserveAspectRatio="none" aria-hidden="true">
      <path class="line-grid" d="M0 25H320 M0 65H320 M0 105H320" />
      <path class="line-path" d="M0 100 C28 94 40 37 76 55 S122 105 151 76 S191 22 219 52 S271 87 320 20" />
    </svg>
    <span class="loading-label">{{ label ?? '正在读取图表数据…' }}</span>
  </div>
</template>

<style scoped>
.chart-loading { display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 15px; min-height: 0; overflow: hidden; color: #8fdfff; background: radial-gradient(circle at center, rgba(0, 212, 255, .07), transparent 60%); }
.loading-label { font-size: 12px; letter-spacing: .08em; }
.donut-loading { width: min(34%, 150px); min-width: 90px; aspect-ratio: 1; overflow: visible; }
.donut-loading circle { fill: none; stroke-width: 11; }
.donut-track { stroke: rgba(117, 167, 210, .16); }
.donut-progress { stroke: var(--accent); stroke-linecap: round; stroke-dasharray: 95 270; transform-origin: 60px 60px; animation: donut-spin 1.6s linear infinite; filter: drop-shadow(0 0 6px rgba(0, 212, 255, .5)); }
.bar-loading { width: min(68%, 290px); display: grid; gap: 12px; }
.bar-loading span { display: block; width: var(--bar-width); height: 10px; background: linear-gradient(90deg, rgba(0, 212, 255, .16), var(--accent)); transform-origin: left center; animation: bar-grow 1.15s ease-in-out infinite alternate; animation-delay: var(--bar-delay); }
.line-loading { width: min(76%, 380px); height: min(36%, 145px); min-height: 76px; overflow: visible; }
.line-grid { fill: none; stroke: rgba(117, 167, 210, .16); stroke-width: 1; }
.line-path { fill: none; stroke: var(--accent); stroke-width: 3; stroke-linecap: round; stroke-dasharray: 430; stroke-dashoffset: 430; animation: line-draw 1.65s ease-in-out infinite; filter: drop-shadow(0 0 5px rgba(0, 212, 255, .55)); }
@keyframes donut-spin { to { transform: rotate(360deg); } }
@keyframes bar-grow { from { transform: scaleX(.28); opacity: .55; } to { transform: scaleX(1); opacity: 1; } }
@keyframes line-draw { 0% { stroke-dashoffset: 430; opacity: .35; } 65% { stroke-dashoffset: 0; opacity: 1; } 100% { stroke-dashoffset: 0; opacity: .35; } }
@media (prefers-reduced-motion: reduce) { .donut-progress, .bar-loading span, .line-path { animation: none; } .line-path { stroke-dashoffset: 0; } }
</style>
