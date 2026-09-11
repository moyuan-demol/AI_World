import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Dev server proxies /api to the FastAPI backend, so the browser only ever
// talks to one origin (no CORS surprises, no API key in the bundle).
export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: 5173,
    // 允许通过内网穿透域名访问（默认只允许 localhost / IP，否则返回 403）
    allowedHosts: ['.lhr.life', '.trycloudflare.com', '.loca.lt', '.ngrok-free.app', '.ngrok.io'],
    proxy: {
      '/api': {
        target: process.env.VITE_API_TARGET || 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
  },
})
