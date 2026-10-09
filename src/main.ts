import { createApp } from 'vue'
import type { App as VueApp } from 'vue'
import PrimeVue from 'primevue/config'
import type { RouteRecordRaw, Router } from 'vue-router'
import App from './App.vue'
import { registerInteractionRoutes, router } from './app/router'
import { installRouteGuard } from './app/route_guard'
import { interactionsAvailable } from './app/data'
import 'primeicons/primeicons.css'
import './styles/tokens.css'
import './styles/global.css'

type InteractionPackage = {
  createInteractions: () => { installGuard: (router: Router) => void; install: (app: VueApp) => void }
  interactionRoutes: RouteRecordRaw[]
  interactionTheme: unknown
}

async function bootstrap(): Promise<void> {
  const app = createApp(App)
  if (interactionsAvailable) {
    const loadInteractions = Object.values(import.meta.glob('../../web_interactions/src/index.ts'))[0]
    if (!loadInteractions) throw new Error('web_interactions 入口文件不存在')
    const interactionsModule = await loadInteractions() as InteractionPackage
    import.meta.glob('../../web_interactions/src/styles.css', { eager: true })
    const interactions = interactionsModule.createInteractions()
    registerInteractionRoutes(router, interactionsModule.interactionRoutes)
    interactions.installGuard(router)
    app.use(PrimeVue, { theme: { preset: interactionsModule.interactionTheme, options: { darkModeSelector: '.app-dark' } } })
    app.use(interactions)
  }
  installRouteGuard(router)
  app.use(router).mount('#app')
}

void bootstrap()
