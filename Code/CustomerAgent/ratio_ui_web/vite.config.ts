import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: 3010,
    strictPort: true,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      '/sr-api': {
        target: 'http://127.0.0.1:8006',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/sr-api/, ''),
      },
      '/fuse-api': {
        target: 'http://127.0.0.1:8008',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/fuse-api/, ''),
      },
      '/customer-agent-api': {
        // Customer Agent FastAPI server (see Code/scripts/start_all.ps1).
        // Port 8503 matches `python -m uvicorn server.app:app --port 8503`.
        target: 'http://127.0.0.1:8503',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/customer-agent-api/, ''),
      },
      '/cha-live-api': {
        target: 'http://127.0.0.1:8503',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/cha-live-api/, ''),
      },
    },
  },
});
