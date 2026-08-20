import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

const apiTarget = process.env.VITE_API_URL || 'http://localhost:8080'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 3082,
    proxy: {
      '/api/comworker/events/stream': {
        target: apiTarget,
        configure: (proxy: any) => {
          proxy.on('proxyRes', (proxyRes: any) => {
            proxyRes.headers['cache-control'] = 'no-cache'
            proxyRes.headers['x-accel-buffering'] = 'no'
          })
        },
      },
      '/api/comworker/terminal/ws': {
        target: apiTarget,
        ws: true,
      },
      '/api': apiTarget,
    },
  },
})
