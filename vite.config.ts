import { fileURLToPath, URL } from 'node:url'
import { existsSync } from 'node:fs'
import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

const interactionsEntry = fileURLToPath(new URL('../web_interactions/src/index.ts', import.meta.url))

export default defineConfig({
  plugins: [vue()],
  define: {
    __WEB_INTERACTIONS_AVAILABLE__: JSON.stringify(existsSync(interactionsEntry)),
  },
  resolve: {
    dedupe: ['vue', 'vue-router', 'primevue', '@primeuix/themes', 'echarts'],
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    fs: { allow: [fileURLToPath(new URL('..', import.meta.url))] },
    proxy: {
      '/api/v1': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})
