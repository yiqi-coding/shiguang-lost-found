import { fileURLToPath, URL } from 'node:url'
import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'

/**
 * Vite 配置
 * - 别名 @ -> src
 * - 开发环境把 /api 代理到本地后端（VITE_PROXY_TARGET，默认 http://127.0.0.1:8000）
 * - 生产构建输出到 dist，并按需生成 sourcemap
 */
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), 'VITE_')
  const isProduction = mode === 'production'

  return {
    plugins: [vue()],
    resolve: {
      alias: {
        '@': fileURLToPath(new URL('./src', import.meta.url)),
      },
    },
    server: {
      host: '127.0.0.1',
      port: 5173,
      open: false,
      proxy: {
        // 前端统一以 /api/v1 为 baseURL，开发期由 Vite 转发到后端服务，避免跨域配置
        '/api': {
          target: env.VITE_PROXY_TARGET || 'http://127.0.0.1:8000',
          changeOrigin: true,
        },
      },
    },
    build: {
      outDir: 'dist',
      assetsDir: 'assets',
      sourcemap: !isProduction,
      chunkSizeWarningLimit: 1500,
      rollupOptions: {
        output: {
          // 骨架阶段先做粗粒度分包，避免首屏 JS 过大
          manualChunks: {
            vue: ['vue', 'vue-router', 'pinia'],
            elementPlus: ['element-plus', '@element-plus/icons-vue'],
          },
        },
      },
    },
  }
})
