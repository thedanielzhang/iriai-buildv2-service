import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 51235,
    allowedHosts: true,
    proxy: {
      '/api': 'http://localhost:51234',
    },
  },
})
