# FlowState embed

A self-contained, static web page that replays **real** Eclipse SUMO + IDM
ring-road simulations (the CI-gated `ring_sugiyama` benchmark, CLAUDE.md
§3.2.1) and shows the observed I-24 westbound day. It has no backend: the
page fetches a committed data pack and interpolates between the engine's
0.5 s samples. It is built to be iframed into the public website.

* **Ring simulation tab.** 18 / 22 / 26 vehicles on the 230 m ring, 0 / 1 / 2
  vehicles running FollowerStopper, switched on from the start or after five
  minutes, three seeds each (45 runs). Top-down ring, a time-space diagram
  (each point coloured by the speed of the nearest vehicle behind it, with
  every trajectory drawn over it), live readouts, per-minute speed spread
  against the uncontrolled run with the same seed, a last-5-minutes table, and
  a provenance line (SUMO version, config hash, seed).
* **I-24, observed tab.** The 30 Nov 2022 westbound space-time mean-speed
  field from I-24 MOTION (60 s × 100 m bins), hatched where no vehicle was
  tracked, with a readout row (hover, or focus the plot and use the arrow keys).

Every number shown is computed from the trajectories in `public/data/`; the
page contains no hand-typed results.

## Design

The page follows the "Paper & Signal" brief (`docs/design/DASHBOARD_DESIGN.md`):
its tokens are copied into `src/tokens.css` (light and dark; the embed does not
import from `frontend/`), and every speed colouring uses the §7.1 "FlowState
speed" stops (`src/colormap.ts`) with a km/h + mph legend. The I-24 field uses
the absolute 0–120 km/h scale with the 40 km/h wave-threshold notch. Ring
speeds are far lower (22 vehicles share 230 m), so the ring views use 0 to the
95th-percentile speed of the uncontrolled run with the same ring and seed; a
run and its uncontrolled twin therefore share one scale, and the legend prints
it. No web fonts are shipped (system stacks). The theme follows
`prefers-color-scheme`; visitors who prefer reduced motion start paused.

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

The public site serves this build at `/demo/` (the `flowstate-site` repository
holds a copy of `dist/` as `demo/`; its README says how to refresh it) and
iframes it on Research → Science:

```html
<iframe
  src="/demo/?embed=1&theme=dark"
  title="FlowState ring-road simulation replay"
  width="100%" height="900" style="border:0;border-radius:12px"
  loading="lazy"></iframe>
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
| `embed` | `1` hides the brand line (the host page carries its own heading) | off |
| `theme` | `light` or `dark` pins the theme | follows `prefers-color-scheme` |
| `run` | a run id from `data/index.json`, e.g. `n22_av1_t300_s42` | 22 vehicles, 1 controlled, on after 5 min, seed 42 |
| `n`, `av`, `t`, `seed` | grid values (ignored when `run` is given) | as above |
| `rate` | `1`, `5`, `20`, `60` (playback speed) | `20` |
| `autoplay` | `0` to start paused (and stay paused when another run is picked) | plays once the ring is on screen, unless the visitor prefers reduced motion |
| `tab` | `ring` or `observed` | `ring` |

Keyboard: space plays/pauses, arrow keys seek 5 s, Home rewinds; on the I-24
plot, arrow keys move the readout one bin (Shift: ten), Home/End jump to the
time edges. The page posts its height after every layout change, and a path
without its trailing slash (`/demo`) redirects to `/demo/` so the relative
asset paths resolve.

## What it will not do

It cannot run new simulations in the browser and does not pretend to: the
engine is SUMO, server-side. Parameter combinations outside the committed
grid need a regenerated pack. Nothing in the page claims validation; the
corridor-level validation record lives in `docs/I24_VALIDATION.md`.
