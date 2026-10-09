import { createApp, watch } from 'vue'
import type { App as VueApp } from 'vue'
import PrimeVue from 'primevue/config'
import type { RouteRecordRaw, Router } from 'vue-router'
import App from './App.vue'
import { registerInteractionRoutes, router } from './app/router'
import { installRouteGuard } from './app/route_guard'
import { backendMode, interactionsAvailable, refreshBackendStatus } from './app/data'
import 'primeicons/primeicons.css'
import './styles/tokens.css'
import './styles/global.css'

type InteractionPackage = {
  createInteractions: (options: {
    mode: 'api' | 'demo'
    fetcher?: (input: string, init?: RequestInit) => Promise<Response>
    analysisIds?: Record<string, string>
    cameraNames?: Record<string, string>
    regions?: { label: string; value: string }[]
  }) => { installGuard: (router: Router) => void; install: (app: VueApp) => void }
  interactionRoutes: RouteRecordRaw[]
  interactionTheme: unknown
}

async function bootstrap(): Promise<void> {
  const app = createApp(App)
  const initialMode = await refreshBackendStatus(true)
  if (interactionsAvailable) {
    const loadInteractions = Object.values(import.meta.glob('../../web_interactions/src/index.ts'))[0]
    if (!loadInteractions) throw new Error('web_interactions 入口文件不存在')
    const interactionsModule = await loadInteractions() as InteractionPackage
    import.meta.glob('../../web_interactions/src/styles.css', { eager: true })
    const mode = initialMode === 'demo' ? 'demo' : 'api'
    const options: Parameters<InteractionPackage['createInteractions']>[0] = {
      mode,
      regions: [{ label: '合肥市', value: '340100' }, { label: '黄山市', value: '341000' }],
    }
    if (mode === 'demo') {
      const loadDemoApi = Object.values(import.meta.glob('../../web_interactions/dev/mock.ts'))[0]
      const loadFixtures = Object.values(import.meta.glob('../../web_interactions/dev/fixtures.ts'))[0]
      if (!loadDemoApi || !loadFixtures) throw new Error('web_interactions 演示接口文件不存在')
      const [{ createDemoApi }, { analysisId, videos }] = await Promise.all([
        loadDemoApi() as Promise<{ createDemoApi: (scenario?: string) => (input: string, init?: RequestInit) => Promise<Response> }>,
        loadFixtures() as Promise<{ analysisId: string; videos: { id: string }[] }>,
      ])
      options.fetcher = createDemoApi(new URLSearchParams(location.search).get('scenario') ?? 'normal')
      if (videos[0]) options.analysisIds = { [videos[0].id]: analysisId }
      options.cameraNames = { 'demo-east-gate': '黄山 · 演示东入口' }
    }
    const interactions = interactionsModule.createInteractions(options)
    registerInteractionRoutes(router, interactionsModule.interactionRoutes)
    interactions.installGuard(router)
    app.use(PrimeVue, { theme: { preset: interactionsModule.interactionTheme, options: { darkModeSelector: '.app-dark' } } })
    app.use(interactions)
    watch(backendMode, (status) => {
      if (status !== 'checking' && (status === 'demo') !== (mode === 'demo')) window.location.reload()
    })
  }
  installRouteGuard(router)
  app.use(router).mount('#app')
}

void bootstrap()
