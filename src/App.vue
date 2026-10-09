<script setup lang="ts">
import { onMounted, onUnmounted, watch } from 'vue'
import { RouterView, useRoute, useRouter } from 'vue-router'
import DashboardShell from './components/layout/DashboardShell.vue'
import { backendMode, refreshBackendStatus } from './app/data'

const route = useRoute()
const router = useRouter()
let poller: number | undefined

onMounted(() => {
  void refreshBackendStatus(true)
  poller = window.setInterval(() => { void refreshBackendStatus(true) }, 15_000)
})
onUnmounted(() => { if (poller) window.clearInterval(poller) })

watch(backendMode, (mode) => {
  if (mode === 'auth' && route.path !== '/login') {
    void router.replace({ path: '/login', query: { redirect: route.fullPath } })
  }
})
</script>

<template>
  <RouterView v-if="route.path === '/login'" />
  <DashboardShell v-else><RouterView /></DashboardShell>
</template>
