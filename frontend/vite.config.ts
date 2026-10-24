/// <reference types="vitest/config" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: './src/test/setup.ts',
    include: ['src/**/*.test.{ts,tsx}'],
  },
  server: {
    proxy: {
      // 前端 /api 请求代理到后端（docs/03-前端/01：dev server 代理 /api 与 /ws 到后端）
      '/api': {
        target: 'http://localhost:8070',
        changeOrigin: true,
      },
      // WebSocket 代理：前端 /ws/chat 连到后端（缺此配置 WS 会连不上）
      '/ws': {
        target: 'http://localhost:8070',
        changeOrigin: true,
        ws: true,
      },
    },
  },
  build: {
    rollupOptions: {
      output: {
        // 拆大依赖 chunk，避免单 chunk >500kB
        manualChunks(id) {
          if (id.includes('node_modules/react') || id.includes('node_modules/react-router')) return 'react'
          if (id.includes('node_modules/antd') || id.includes('node_modules/@ant-design')) return 'antd'
          if (id.includes('node_modules/echarts')) return 'echarts'
        },
      },
    },
  },
})
