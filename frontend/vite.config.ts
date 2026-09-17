import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

/** Where the dev server proxies `/api` and `/healthz`.
 *
 * Defaults to the local stack's port; `FLOWSTATE_API_ORIGIN=http://127.0.0.1:8010
 * npm run dev` points the dashboard at another instance without a hand-written
 * config file (a browser walkthrough against a scratch API needed exactly that).
 * A cross-origin target must also be listed in the API's CORS origins. */
// the config runs in Node; @types/node is not a dependency of the dashboard
declare const process: { env: Record<string, string | undefined> };
const apiOrigin = process.env.FLOWSTATE_API_ORIGIN ?? 'http://localhost:8000';

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': { target: apiOrigin, changeOrigin: true },
      '/healthz': { target: apiOrigin, changeOrigin: true },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    css: false,
  },
});
