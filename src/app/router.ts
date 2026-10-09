import { defineComponent, h } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import type { RouteRecordRaw, Router } from 'vue-router'
import DashboardView from '../features/dashboard/DashboardView.vue'
import ScenicView from '../features/scenic/ScenicView.vue'
import HolidayView from '../features/holidays/HolidayView.vue'

const LoginPending = defineComponent({
  name: 'LoginPending',
  setup() {
    return () => h('section', { class: 'panel login-pending' }, [
      h('span', { class: 'eyebrow' }, '身份验证'),
      h('h1', '后端已启动，请先登录'),
      h('p', 'B 负责的登录页面尚未交付。接入 web_interactions 的路由后，这里将显示正式登录页。'),
      h('button', { class: 'button-primary', onClick: () => window.location.reload() }, '重新检查登录状态'),
    ])
  },
})

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'dashboard', component: DashboardView, meta: { title: '全域总览' } },
    { path: '/scenic', name: 'scenic', component: ScenicView, meta: { title: '景区客流' } },
    { path: '/holidays', name: 'holidays', component: HolidayView, meta: { title: '假期专题' } },
    { path: '/login', name: 'login-placeholder', component: LoginPending, meta: { title: '登录', public: true } },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
  scrollBehavior: () => ({ top: 0 }),
})

// B 的 web_interactions 包交付后，由应用入口传入其导出路由。
// 登录路由替换占位页；其他路由保持由 B 所属目录维护。
export function registerInteractionRoutes(target: Router, routes: RouteRecordRaw[]): void {
  if (routes.some((route) => route.path === '/login')) target.removeRoute('login-placeholder')
  for (const route of routes) target.addRoute(route)
}
