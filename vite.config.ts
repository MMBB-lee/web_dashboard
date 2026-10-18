import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { createRequire } from 'node:module'
import vue from '@vitejs/plugin-vue'
import { defineConfig, normalizePath } from 'vite'

const workspace = fileURLToPath(new URL('.', import.meta.url))
const dashboard = path.join(workspace, 'web_dashboard')
const host = path.join(workspace, 'web_interactions/src/host')
// pnpm's global virtual store can put font assets outside the workspace.
const iconFonts = path.join(path.dirname(createRequire(import.meta.url).resolve('primeicons/primeicons.css', { paths: [host] })), 'fonts')
const entry = normalizePath(path.join(dashboard, 'src/main.ts'))
const data = normalizePath(path.join(dashboard, 'src/app/data'))

// 整合适配只在仓库根入口启用；A 目录仍可独立构建。
export default defineConfig({
  root: dashboard,
  plugins: [{
    name: 'anhui-workspace-host', enforce: 'pre',
    resolveId(source, importer) {
      if (!importer || !source.startsWith('.')) return
      const resolved = normalizePath(path.resolve(path.dirname(importer.split('?')[0]), source)).replace(/\.ts$/, '')
      if (resolved === data) return path.join(host, 'dashboard-data.ts')
    },
    load(id) {
      if (normalizePath(id.split('?')[0]) === entry) return `import ${JSON.stringify(normalizePath(path.join(host, 'main.ts')))}`
    },
  }, vue()],
  define: { __WEB_INTERACTIONS_AVAILABLE__: 'true' },
  resolve: {
    dedupe: ['vue', 'vue-router', 'primevue', '@primeuix/themes', 'echarts'],
    alias: { '@': path.join(dashboard, 'src') },
  },
  server: {
    host: '127.0.0.1',
    fs: { allow: [workspace, iconFonts] },
    proxy: { '/api/v1': { target: process.env.API_PROXY_TARGET ?? 'http://127.0.0.1:8000', changeOrigin: true } },
  },
})
