import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Дев-сервер прокидывает /api на локальный FastAPI — так браузер сидит на одном
// origin и никуда наружу не лезет.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: process.env.UMIR_API ?? 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
  },
})
