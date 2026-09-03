import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The dashboard talks to the API through /api, proxied in dev so the browser
// never deals with CORS and the same relative URLs work in production behind
// a single reverse proxy.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    host: true,
    proxy: {
      '/api': {
        target: process.env.VITE_API_PROXY ?? 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: true,
    rollupOptions: {
      output: {
        // Charts and the data layer change far less often than app code, so
        // splitting them keeps the cacheable vendor chunks stable.
        manualChunks: {
          react: ['react', 'react-dom', 'react-router-dom'],
          query: ['@tanstack/react-query'],
          charts: ['recharts'],
        },
      },
    },
  },
})
