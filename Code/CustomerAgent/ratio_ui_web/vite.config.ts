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
      // ── CustomerAgent live/replay routing ──────────────────────────────
      //
      // The deployed Container App
      //   https://ca-ratio-customeragent-dev.graywater-ed11bb19.centralus
      //     .azurecontainerapps.io
      // exposes the new continuous-polling endpoints (/api/run,
      // /api/run/services) but does NOT mount the trace-replay routes
      // (/api/traces/*). Those still live in the local backend
      // (Code/CustomerAgent/src/server/traces_api.py) and are what the
      // XCV detail view streams from.
      //
      // Order matters: more-specific paths must be listed BEFORE the
      // catch-all. Vite/http-proxy picks the FIRST matching prefix.
      '/cha-live-api/api/traces': {
        target: 'http://127.0.0.1:8503',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/cha-live-api/, ''),
      },
      '/cha-live-api': {
        target: 'https://ca-ratio-customeragent-dev.graywater-ed11bb19.centralus.azurecontainerapps.io',
        changeOrigin: true,
        secure: true,
        rewrite: (path) => path.replace(/^\/cha-live-api/, ''),
      },
    },
  },
});
