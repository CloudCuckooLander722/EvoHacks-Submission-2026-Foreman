/// <reference types="vitest/config" />
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// §D: the site calls `${VITE_API_BASE}/runs/...` (default /api); in dev Vite forwards that
// to Nathan's API on :8000, which serves the same paths without the /api prefix.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
  test: { environment: 'node' },
})
