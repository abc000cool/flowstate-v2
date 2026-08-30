# FlowState Dashboard

Dark mission-control frontend for FlowState v2 (Vite + React 18 + TypeScript,
hand-rolled CSS design system — no Tailwind).

## Commands

Run everything from `frontend/`. This machine needs the local npm cache flag:

```sh
npm install --cache .npm-cache
npm run dev       # dev server; proxies /api and /healthz -> localhost:8000
npm run build     # tsc -b (strict) + vite build -> dist/
npm run preview   # serve the production build
npm test          # vitest (colormap anchors, axis formatters, client auth, run-detail smoke)
```

## Demo / mock mode

- `VITE_MOCK=1 npm run dev` forces the in-memory backend in `src/mocks/`
  (deterministic fixtures incl. a synthetic backward-propagating wave field).
- Without the flag, the app polls `/healthz`; when the API is unreachable it
  auto-falls back to the same demo data and shows the amber
  "API offline — showing demo data" banner.

## API settings

Base URL (default `/api/v1`) and `X-API-Key` (default `flowstate-local-dev`, matching docker-compose.yml)
live in localStorage; change them from the Settings drawer in the left rail.

## Map of the code

- `src/styles/tokens.css` — design tokens (colors, 8px grid, fonts)
- `src/lib/colormap.ts` — heatmap ramps (documented anchor stops, unit-tested)
- `src/lib/format.ts` — typed min/km/km-per-h axis + readout helpers
- `src/api/client.ts` — typed fetch client for the v2 API contract
- `src/mocks/` — mock backend + synthetic space-time field generators
- `src/components/HeatmapCanvas.tsx` — canvas space-time diagram w/ crosshair
- `src/views/` — Scenarios / Runs / Run detail / Sweeps / Reports
