import type { Router } from 'vue-router'
import { backendMode, refreshBackendStatus } from './data'

export function installRouteGuard(router: Router): void {
  router.beforeEach(async (to) => {
    if (to.meta.public) return true
    await refreshBackendStatus()
    if (backendMode.value === 'auth') {
      return { path: '/login', query: { redirect: to.fullPath } }
    }
    return true
  })
}
