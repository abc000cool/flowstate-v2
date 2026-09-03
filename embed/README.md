# FlowState embed

A self-contained, static web page that replays **real** Eclipse SUMO + IDM
ring-road simulations (the CI-gated `ring_sugiyama` benchmark, CLAUDE.md
§3.2.1) and shows the observed I-24 westbound day. It has no backend: the
page fetches a committed data pack and interpolates between the engine's
0.5 s samples. It is built to be iframed into the public website.

* **Ring benchmark tab.** 18 / 22 / 26 vehicles on the 230 m ring, 0 / 1 / 2
  vehicles running FollowerStopper, switched on from the start or after five
  minutes, three seeds each (45 runs). Top-down ring, time-space diagram,
  live readouts, per-minute speed spread against the uncontrolled baseline
  with the same seed, and a provenance line (SUMO version, config hash, seed).
* **I-24, observed tab.** The 30 Nov 2022 westbound space-time mean-speed
  field from I-24 MOTION (60 s × 100 m bins) with a hover readout.

Every number shown is computed from the trajectories in `public/data/`; the
page contains no hand-typed results.

## Run locally

```sh
cd embed
npm ci
npm run dev        # http://localhost:5174
npm test           # unit tests + data-pack integrity
npm run build      # dist/ (typecheck + vite build)
npm run preview    # serve dist/ at http://localhost:4174
```

## Regenerate the data pack

From the repository root (SUMO must be installed in the workspace; the 45
ring runs take ~10 minutes on one core, a few hundred MB of RAM):

```sh
uv run --no-sync python scripts/website_sim_pack.py
```

Layout and encoding are documented in that script's docstring and checked by
`tests/integrity.test.ts`.

## Deploy

The build output is a plain static site (`dist/`, ~5 MB, almost all of it
trajectory data). Any static host works.

**Render (recommended, zero config).** The repository root has a
`render.yaml` Blueprint. In the Render dashboard choose *New → Blueprint*,
connect the repository, and deploy; the service is named `flowstate-sim`.
Manual alternative: *New → Static Site*, root directory `embed`, build
command `npm ci && npm run build`, publish directory `dist`.

**Fly.io.** From this directory:

```sh
fly launch --copy-config --yes   # first time; uses fly.toml + Dockerfile
fly deploy                        # afterwards
```

The image is nginx serving `dist/` on port 8080 with gzip, long-lived caching
for hashed assets, a day for the data pack, and `frame-ancestors *` so the
page may be embedded anywhere.

**Anything else** (Netlify, Cloudflare Pages, GitHub Pages, S3): publish
`dist/` as-is; the page uses relative asset paths and works from a sub-folder.

## Embed on the website

```html
<iframe
  src="https://flowstate-sim.onrender.com/?embed=1&run=n22_av1_t300_s42"
  title="FlowState ring simulation"
  width="100%" height="720" style="border:0;border-radius:12px"
  loading="lazy" allow="fullscreen"></iframe>
```

The page posts its rendered height to the parent
(`{type: 'flowstate-embed', height}`) so the host can size the frame:

```js
window.addEventListener('message', (e) => {
  if (e.data?.type === 'flowstate-embed') iframe.style.height = `${e.data.height}px`;
});
```

URL parameters:

| Parameter | Values | Default |
|---|---|---|
| `embed` | `1` hides the tagline and footer prose | off |
| `run` | a run id from `data/index.json`, e.g. `n22_av1_t300_s42` | 22 vehicles, 1 controlled, on after 5 min, seed 42 |
| `n`, `av`, `t`, `seed` | grid values (ignored when `run` is given) | as above |
| `rate` | `1`, `5`, `20`, `60` (playback speed) | `20` |
| `autoplay` | `0` to start paused | plays, unless the visitor prefers reduced motion |
| `tab` | `ring` or `observed` | `ring` |

Keyboard: space plays/pauses, arrow keys seek 5 s, Home rewinds.

## What it will not do

It cannot run new simulations in the browser and does not pretend to: the
engine is SUMO, server-side. Parameter combinations outside the committed
grid need a regenerated pack. Nothing in the page claims validation; the
corridor-level validation record lives in `docs/I24_VALIDATION.md`.
