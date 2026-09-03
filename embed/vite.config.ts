import { defineConfig } from 'vitest/config';

// base './' so the built page works from any path (root of a Render static
// site, a sub-folder, or behind a reverse proxy) without rebuilding.
export default defineConfig({
  base: './',
  build: { target: 'es2020', sourcemap: false, assetsInlineLimit: 0 },
  server: { port: 5174 },
  preview: { port: 4174 },
  test: { include: ['tests/**/*.test.ts'] },
});
