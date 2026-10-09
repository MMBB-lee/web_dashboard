<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import { backendMode, refreshBackendStatus } from '../../app/data'

const route = useRoute()
const now = ref(new Date())
let timer: number | undefined
onMounted(() => { timer = window.setInterval(() => { now.value = new Date() }, 1000) })
onUnmounted(() => { if (timer) window.clearInterval(timer) })

const clock = computed(() => new Intl.DateTimeFormat('zh-CN', {
  timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
  hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
}).format(now.value))
const modeLabel = computed(() => ({ checking: '正在连接', demo: '演示数据', live: '真实接口', auth: '需要登录' })[backendMode.value])
const nav = [
  { to: '/', label: '全域总览', index: '01' },
  { to: '/scenic', label: '景区客流', index: '02' },
  { to: '/holidays', label: '假期专题', index: '03' },
]
</script>

<template>
  <div class="app-shell">
    <div class="workspace">
      <header class="topbar">
        <div class="brand"><div class="brand-mark">皖</div><span>安徽文旅交通</span></div>
        <div class="topbar-title"><span>安徽文旅交通客流观察与预测</span><small>ANHUI TOURISM & TRANSPORT DASHBOARD</small></div>
        <div class="topbar-right">
          <span :class="['connection', backendMode]"><span class="connection-dot" />{{ modeLabel }}</span>
          <time class="clock">{{ clock }}</time>
          <button class="icon-button" title="重新检查接口" aria-label="重新检查接口" @click="refreshBackendStatus(true)">↻</button>
        </div>
      </header>
      <nav class="nav-list" aria-label="主导航">
        <RouterLink v-for="item in nav" :key="item.to" :to="item.to" :class="['nav-item', { active: route.path === item.to }]">
          <span class="nav-index">{{ item.index }}</span><span>{{ item.label }}</span>
        </RouterLink>
        <span class="nav-caption">数据驾驶舱 / A 端</span>
      </nav>
      <main class="main-content">
        <div v-if="backendMode === 'demo'" class="demo-banner" role="status">
          <strong>演示模式</strong><span>下方数值为界面演示数据，非官方统计；后端和登录服务可用后会自动切换为真实接口。</span>
        </div>
        <slot />
      </main>
    </div>
  </div>
</template>
