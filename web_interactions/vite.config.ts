import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'
import path from 'node:path'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'

const workspace = fileURLToPath(new URL('..', import.meta.url))
const iconFonts = path.join(path.dirname(createRequire(import.meta.url).resolve('primeicons/primeicons.css')), 'fonts')

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  return {
    plugins: [vue()],
    server: {
      port: 5173, strictPort: true,
      fs: { allow: [workspace, iconFonts] },
      proxy: { '/api/v1': { target: env.API_PROXY_TARGET || 'http://127.0.0.1:8000', changeOrigin: false } },
    },
    build: { outDir: 'dist' },
  }
})
