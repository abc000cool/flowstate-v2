# FlowState dashboard: design brief ("Paper & Signal")

Status: **approved for implementation**. Date: 2026-10-07. Scope: `frontend/` only.
No API, schema, config-hash or golden changes are in scope.

This brief is written to be implemented verbatim. Every value below (colors, sizes,
contrast ratios, colormap stops) was computed, not eyeballed: WCAG 2.x contrast and
OKLab/OKLCH math, CVD checks with the Machado–Oliveira–Fernandes (2009) severity-1.0
simulation, and the categorical/ordinal palettes run through the data-viz palette
validator. When this document and a habit disagree, this document wins. When this
document and `CLAUDE.md` disagree, `CLAUDE.md` wins.

---

## 0. TL;DR

* **Aesthetic.** *Paper & Signal* combines warm paper-and-ink neutrals, one signal blue, and
  data colors that mean something only in the data. It aims for Linear/Vercel-grade
  restraint, Datadog/Grafana/Blueprint-grade density, and Stripe-grade discipline about
  loading, empty and error states. The page should look like the FHWA-style report it
  produces, not like a game console.
* **Themes.** The light and dark themes are both first-class. The page follows
  `prefers-color-scheme`, and a System/Light/Dark control in Settings can override it.
  The old single dark "mission control" theme is retired.
* **Type.** Inter in sentence case for everything a person reads. JetBrains Mono only for
  machine values (IDs, hashes, seeds, paths, numbers in tables and axes). The
  letter-spaced monospace capitals are gone from labels, buttons, nav, titles and headers.
* **Data viz.** The speed heatmap gets a CVD-safe colormap: deep red when stopped, pale
  neutral at 60 km/h, blue at free flow. The legend reads in km/h and mph and marks the
  40 km/h wave threshold. Bins with no data are hatched, so they can't be mistaken for
  stopped traffic (today they are ΔE 4.5 apart, which is indistinguishable). The sweep
  matrix uses binned blue/red, not green/red. Metric tiles show `mean ± half-width` with
  a dot strip of the replicates.
* **Build.** Plain CSS custom properties and plain global CSS files. No CSS modules, no
  Tailwind, no CSS-in-JS. **No new npm dependencies.** About 26 Lucide icons are vendored
  into one file (ISC licence). Three engineers can work in parallel on disjoint files
  after a half-day groundwork step (§12).

---

## 1. Who this is for, and what is wrong today

### 1.1 Users and context

| Who | Where they sit | What they need from the screen |
|---|---|---|
| DOT / MPO traffic engineer | Agency laptop or 1080p office monitor, often light-lit room, Windows + Edge/Chrome, long sessions | Read a space-time speed field like a PeMS contour plot; compare runs; trust every number; find the report |
| Consultancy modeler | Large monitor, many runs and sweeps | Density: many rows, many cells, fast scanning, keyboard |
| Reviewer / manager | Screenshots, PDFs, a projector in a meeting | Calm, legible, prints and projects well, honest labels |

Two facts shape the bar:

* **Our buyers are public entities.** Under the DOJ ADA Title II rule (published
  2024-04-24), state and local governments' web content must meet **WCAG 2.1 AA**. The
  2026 interim final rule moved the compliance dates to 2027-04-26 for entities of 50,000
  or more people (Frisco is one) and 2028-04-26 for smaller ones. Their procurement
  teams increasingly ask vendors for the same, so we design to AA in both themes from
  the start.
* **The product's credibility is its honesty** (`CLAUDE.md` §0). We keep provenance
  labels, CIs, seeds, the screening-tier warnings and verbatim failure reasons, and make
  them visually *first-class*.

### 1.2 Audit of the current dashboard

The audit covers `frontend/src` as of commit `571b9e4`.

| Area | What is wrong (evidence) |
|---|---|
| **Hierarchy** | Every label is 9–13 px letter-spaced uppercase mono: view titles (`.view-title`, 13 px muted), panel titles (11 px), field labels (10 px), buttons, nav, chips and table headers. The page title is among the *smallest, faintest* text on the page, so nothing leads. |
| **Contrast** | `--faint #5a6375` is 3.20:1 on `--bg` and 3.02:1 on `--panel`, which fails AA. It is used for table headers, hashes, empty states and axis titles. Tags are 9 px. |
| **Consistency** | There are 77 inline `style={{…}}` objects in views and components, plus 4 `<label>&nbsp;</label>` alignment hacks. Hard-coded hex appears outside tokens: 8 in `app.css`, 7 in `HeatmapCanvas.tsx` (canvas colors) and 4 in `bits.tsx` (SVG thumbnails). None of these can follow a theme. |
| **Dark-only** | `color-scheme: dark` is hard-coded in `tokens.css` and `index.html`. There is no light theme, no OS preference and no toggle. Screenshots and printouts look like a console. |
| **Heatmap legibility** | (1) Null bins are painted `NULL_BIN_RGB [11,14,20]` while 0 km/h is `[10,16,42]`: **ΔE 4.5, contrast 1.03**, so *no data and stopped traffic look the same*. (2) Jammed traffic is near-background navy, so jams read as holes. (3) The legend is a 120 px bar with only "0 – 120 km/h", with no ticks, no mph and no threshold. (4) The readout floats over the top-right of the plot. (5) The plot resizes on `window` resize only, so it won't follow a sidebar collapse. |
| **Sweep matrix** | Deltas blend toward ok-green or danger-red: status colors used as data, and **ΔE 3.0 under deuteranopia** at 20 % saturation (6.4 at maximum). Weak improvements and regressions look the same to about 6 % of men. |
| **Replicate strips** | `StripChart` plots replicate *index* on x, an axis with no meaning, and duplicates the metric cards in a second grid. |
| **States** | Empty states are one mono line. Loading is text ("loading field…") or nothing at all (the runs table is blank until the first poll). If `getRunHeatmap` fails, the panel says "loading field…" **forever**, because the error only goes to a toast. |
| **Navigation** | Nav items are ungrouped and "First run" is first. There is no breadcrumb (Run detail uses a "← runs" link styled as a title). A UTC clock re-renders the shell every second and adds noise. |
| **Keyboard / a11y** | Runs table rows navigate on `onClick` and aren't focusable. Sweep cells are hover-only. The focus ring is a `box-shadow`, which disappears in Windows forced-colors mode. Critical explanations live only in `title` tooltips. `ConfirmDialog` has no focus trap and doesn't restore focus. |

What is **good and stays**: honesty labels (SEEDED, DEMO, MACRO SCREENING, UNDERPOWERED,
NO DATA, LOCAL/SERVER), confirm-before-compute dialogs with cost facts, verbatim
failure reasons, demo data that is never laundered into evidence, and mono for machine
values.

---

## 2. Inspirations: what we take from each product

The list follows the owner's instruction to "copy stuff from actual tech companies". We
take *rules*, not looks. See §13 for what we refuse to copy.

| Product | What we take (concrete, transferable) | Sources |
|---|---|---|
| **Linear** (2024 redesign; 2026 refresh) | Themes are *generated* from a few inputs in a perceptual space (LCH: base, accent, contrast) instead of 98 hand-set variables, so we derive every surface from one 12-step scale. The navigation sidebar is **dimmer** than content: "Don't compete for attention you haven't earned." "Structure should be felt not seen": fewer separators, softer dividers, smaller icons, warmer neutral grays. Headers and view controls are consistent across every page. A ⌘/Ctrl-K command menu (we add it as an optional P2). | [How we redesigned the Linear UI (part II)](https://linear.app/now/how-we-redesigned-the-linear-ui), [A calmer interface for a product in motion](https://linear.app/now/behind-the-latest-design-refresh), [UI refresh changelog 2026-03-12](https://linear.app/changelog/2026-03-12-ui-refresh) |
| **Vercel Geist** | Scale semantics: steps 100–300 are backgrounds (default/hover/active), 400–600 borders (default/hover/active), 700–800 high-contrast backgrounds, 900–1000 text and icons. Background 1 is the default and Background 2 is used *sparingly*. Material radii: base/small 6 px, menu/modal 12 px, fullscreen 16 px, with shadow growing with lift. The type system separates **label** (single-line UI, sized to marry with icons) from **copy** (multi-line, taller leading), with a tabular label for numbers. | [Geist colors](https://vercel.com/geist/colors), [Geist typography](https://vercel.com/geist/typography), [Geist materials](https://vercel.com/geist/materials) |
| **Stripe** | Accessible color is built in a perceptual space (CIELAB) so that hues share one lightness curve; in their system, colors "at least five levels apart" are guaranteed small-text contrast. Empty-state copy: the title states what is missing, with a period; the description is under 14 words and says when data appears; the action echoes the title. Render order is **loading → error → empty → content**, and loading, error, empty and content all share the same fixed height (no layout jump). Toasts are short and temporary; banners are persistent and require action. At most three charts per row, and the headline value sits above the chart. | [Designing accessible color systems](https://stripe.com/blog/accessible-color-systems), [Empty state](https://docs.stripe.com/stripe-apps/patterns/empty-state), [Communicating state](https://docs.stripe.com/stripe-apps/patterns/communicating-state), [Chart layout](https://docs.stripe.com/stripe-apps/patterns/chart-layout) |
| **Datadog (DRUIDS)** | "Filtering a list of data, scoping to a range of time, or inspecting a graph", once learned, must work the same everywhere. We have one hover/inspect behavior for every chart (crosshair + readout row) and one filter position (above what it scopes). Code is the source of truth for tokens. | [DRUIDS, the design system that powers Datadog](https://www.datadoghq.com/blog/engineering/druids-the-design-system-that-powers-datadog/) |
| **Grafana** | Dense, operational defaults: Inter at a 14 px base, 12 px small, radius 4/6/10 px, component heights of 3/4/6 grid units (24/32/48 px on its 8 px grid), panel header height of 5 units, a hover overlay at 8 % opacity, and border weak/medium/strong as three tiers. We keep the three border tiers and the 32 px default control, but use **opaque** scale steps instead of Grafana's alpha text (`0.65`), because opaque steps give predictable, provable contrast. | [createTypography.ts](https://github.com/grafana/grafana/blob/main/packages/grafana-data/src/themes/createTypography.ts), [createColors.ts](https://github.com/grafana/grafana/blob/main/packages/grafana-data/src/themes/createColors.ts), [createShape.ts](https://github.com/grafana/grafana/blob/main/packages/grafana-data/src/themes/createShape.ts), [createComponents.ts](https://github.com/grafana/grafana/blob/main/packages/grafana-data/src/themes/createComponents.ts) |
| **Palantir Blueprint** | Engineering-grade density on a **4 px spacing unit**. Controls are 30/24/40 px, fonts 14/12/16 px, icons 16/20 px, radius 4 px, and transitions 100 ms on `cubic-bezier(0.4, 1, 0.75, 0.9)`. We adopt the 4 px unit, the 16 px icon grid and ~100 ms micro-transitions. | [`_variables.scss`](https://github.com/palantir/blueprint/blob/develop/packages/core/src/common/_variables.scss) |
| **kepler.gl** (open-source core of the geospatial-temporal family) | Time playback is a lightweight strip *under* the view, with a distribution histogram, speed control and a "Showing … / Reset" bar when zoomed; keyboard panning uses modifier keys. We take the keyboard-scrubbing and the "zoomed, Reset" conventions for the heatmap. We **reject** a map-first layout (Mapbox/Foursquare Studio), because our primary view is a space-time diagram, not a map. | [kepler.gl time playback](https://docs.kepler.gl/docs/user-guides/h-playback) |
| **Observable Framework / Hex** | The card pattern: title at 15 px/500, a muted subtitle at 400 weight, padding 1 rem, grid gap 1 rem. Columns step 2→3→4 at 640/720/1080 px using **container queries**, so cards respond to their container, not the window. From Hex we take the "story" view: an analysis read top-to-bottom in the order it was computed. That is Run detail's layout. | [card.css](https://github.com/observablehq/framework/blob/main/src/style/card.css), [grid.css](https://github.com/observablehq/framework/blob/main/src/style/grid.css), [Hex: Building a Builder](https://hex.tech/blog/building-a-builder/) |
| **IBM Carbon** | Data tables come in five sizes (24/32/40/48/64 px), with 16 px column padding, semibold headers and zebra only as an option. Categorical colors are applied **in strict order**. The alert palette is separate from the categorical palette. Sequential ramps get lighter-larger in dark themes. Legend hover dims other series to 30 % and click isolates. Use discrete classes, not a decorative gradient, where a sequential palette is meant. | [Color palettes](https://carbondesignsystem.com/data-visualization/color-palettes/), [Data table style](https://carbondesignsystem.com/components/data-table/style/), [Legends](https://carbondesignsystem.com/data-visualization/legends/) |
| **Radix Colors** | The 12-step scale: 1–2 app backgrounds, 3–5 component backgrounds (normal/hover/active), 6–8 borders (subtle/interactive/strong and focus), 9–10 solids, 11–12 text (steps 11/12 hold APCA Lc 60/90 on step 2). **We adopt Radix *Sand*, *Green*, *Amber* and *Red* values verbatim (MIT).** | [Understanding the scale](https://www.radix-ui.com/colors/docs/palette-composition/understanding-the-scale), [radix-ui/colors (MIT)](https://github.com/radix-ui/colors) |
| **Science of color maps** | Use perceptually ordered, CVD-safe colormaps, never rainbow or red-green. This is the basis for the speed and density ramps in §7. | [Crameri, Shephard & Heron (2020), *The misuse of colour in science communication*, Nat. Commun. 11:5444](https://www.nature.com/articles/s41467-020-19160-7) |

Internal consistency sources: `scripts/m3_analyze_sweep.py` (report-figure palette:
controller colors, `SEQ_BLUES`, print neutrals) and `docs/WEBSITE_BRIEF.md` §2 (soft
warm text, muted warm ink, "jam-red through amber to free-flow blue"). The dashboard now
matches both.

---

## 3. The aesthetic: "Paper & Signal"

**One sentence:** a precise analytical instrument printed on warm paper, where the only
loud things are the data and the one blue that says "you can act here".

* **Paper.** Warm, low-chroma neutrals (Radix Sand) in both themes. Light is warm
  off-white paper with near-black ink; dark is warm charcoal with soft white ink. This
  matches the report figures (`INK2 #52514e`, `GRID #e8e7e2`) and the website's warm text,
  so a screenshot, a PDF page and the dashboard read as one product.
* **Signal.** There is one accent, *FlowState blue*, taken from the report-figure ramp
  (`#256abf` solid, `#1f63b8` text). It is used for primary actions, links, focus, the
  selected state and the "running" status, and nothing else. In data, blue means free
  flow and improvement. These are the same "things are moving" semantics, so the brand
  and the data agree.
* **Status.** Green, amber and red mean status only: done/PASS, caution/SEEDED, and
  failed/FAIL. They always come with an icon and a word, never color alone.
* **Data.** Data colors appear only inside plots, matrices and legends: the speed and
  density colormaps, the controller series, and the sweep diverging bins. Text never
  wears a data color.
* **Structure.** Hairline borders, no shadows on surfaces, shadows only on floating
  layers, an 8 px card radius, a 4 px spacing grid, and dense 13 px tables with 36 px rows.

**Why this suits traffic engineers.** They live in PDFs, plan sheets and PeMS contour
plots, so paper neutrals and a red-means-jam colormap are their native language. They
work long sessions on ordinary hardware, sometimes on projectors, so high contrast and
calm surfaces win over glow. They must trust numbers, so CIs, seeds and provenance are
typographically first-class, and FAIL looks as deliberate as PASS. Their employers are
bound to WCAG 2.1 AA (§1.1).

---

## 4. Design principles

1. **The data is the loudest thing on the screen.** Chrome is neutral, hairline and still.
   Color in chrome means *state*; color in a chart means *data*. No glow, gradients, neon
   or ticking clocks.
2. **Honesty is a visual feature.** Provenance (SEEDED, DEMO, MACRO SCREENING, LOCAL),
   uncertainty (CI on every headline number, UNDERPOWERED below n = 20) and failure (the
   service's own words) get first-class components, and FAIL gets the same typographic
   weight as PASS.
3. **Say what the server said.** Use API vocabulary verbatim (`done`, `failed`,
   `report_refused`), and never invent a number on the client (`CLAUDE.md` §7.4).
4. **Dense but calm.** 13 px tables, 32 px controls, 36 px rows, 4 px grid. Group with
   whitespace and one hairline, never boxes inside boxes.
5. **One way to do each thing.** There is one button set, one table, one status pill, one
   callout and one page header. Page actions always sit top-right. Every view renders
   loading → error → empty → content in that order, at a stable height.
6. **Accessible by default, in both themes.** AA text contrast, 3:1 for UI boundaries and
   focus, everything reachable by keyboard, an outline-based focus ring that survives
   forced colors, reduced motion honored, and CVD-safe data colors.
7. **Print-literate.** The dashboard looks like the report it generates: the same
   neutrals, the same data colors and the same units (km/h with mph alongside).

---

## 5. Tokens (ready to paste)

Replace `frontend/src/styles/tokens.css` with the block below in full. It contains:

* theme-invariant tokens (type, space, radius, size, motion, z-index);
* the light theme on `:root`;
* the dark theme, declared **twice with identical bodies**, once for the OS preference
  (unless the user pinned light) and once for an explicit `data-theme="dark"`;
* legacy aliases so that unmigrated CSS keeps rendering during the migration. Delete
  these in step 5 (§12).

```css
/* =====================================================================
   FlowState design tokens: "Paper & Signal"
   Source of truth: docs/design/DASHBOARD_DESIGN.md §5.

   Light theme lives on :root. The dark theme is declared twice and the two
   bodies MUST stay identical (src/test/tokens.test.ts checks it):
     1. OS dark preference, unless the user pinned light;
     2. the user pinned dark (Settings → Appearance).

   Neutral (Sand), green, amber and red scale values: Radix Colors
   (MIT, https://github.com/radix-ui/colors). Blue: FlowState's own ramp,
   shared with the report figures (scripts/m3_analyze_sweep.py SEQ_BLUES).
   Categorical series: the validated data-viz reference palette, the same
   hexes as CONTROLLER_COLORS in the report figures.
   ===================================================================== */

/* ---------- theme-invariant ---------- */
:root {
  /* type families */
  --font-sans: 'Inter', system-ui, -apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
  --font-mono: 'JetBrains Mono', ui-monospace, 'SF Mono', Menlo, Consolas, monospace;

  /* type scale: size / line-height pairs */
  --text-11: 11px;  --leading-11: 16px;
  --text-12: 12px;  --leading-12: 16px;
  --text-13: 13px;  --leading-13: 20px;
  --text-14: 14px;  --leading-14: 20px;  --leading-14-prose: 22px;
  --text-16: 16px;  --leading-16: 24px;
  --text-20: 20px;  --leading-20: 28px;
  --text-24: 24px;  --leading-24: 32px;
  --text-32: 32px;  --leading-32: 40px;

  --weight-regular: 400;
  --weight-medium: 500;
  --weight-semibold: 600;

  --tracking-tight: -0.017em;   /* Inter 20px and up */
  --tracking-snug: -0.011em;    /* Inter 16px */
  --tracking-normal: -0.006em;  /* Inter 13–14px */
  --tracking-none: 0;           /* Inter 11–12px; ALL mono */
  --tracking-caps: 0.04em;      /* 11px uppercase eyebrows, badges */

  /* spacing: 4px base */
  --space-0-5: 2px;
  --space-1: 4px;
  --space-1-5: 6px;
  --space-2: 8px;
  --space-3: 12px;
  --space-4: 16px;
  --space-5: 20px;
  --space-6: 24px;
  --space-8: 32px;
  --space-10: 40px;
  --space-12: 48px;
  --space-16: 64px;

  /* radii */
  --radius-xs: 4px;     /* badges, checkboxes, sweep cells, kbd */
  --radius-sm: 6px;     /* buttons, inputs, nav items, tooltips, segmented */
  --radius-md: 8px;     /* panels, cards, callouts, toasts */
  --radius-lg: 12px;    /* dialogs, menus, command palette */
  --radius-full: 9999px;/* status pills, dots, progress */

  /* sizes */
  --control-h-sm: 28px;
  --control-h-md: 32px;
  --control-h-lg: 36px;
  --row-h: 36px;
  --row-h-compact: 28px;
  --icon-sm: 14px;
  --icon-md: 16px;
  --icon-lg: 20px;
  --sidebar-w: 232px;
  --sidebar-w-collapsed: 56px;
  --topbar-h: 48px;
  --content-max: 1440px;
  --content-max-narrow: 960px;
  --prose-max: 72ch;

  /* motion */
  --duration-1: 80ms;    /* color/background hover */
  --duration-2: 120ms;   /* button press, toggles, segmented */
  --duration-3: 180ms;   /* tooltip/popover/toast enter */
  --duration-4: 240ms;   /* dialog/drawer enter */
  --duration-progress: 400ms;
  --ease-standard: cubic-bezier(0.2, 0, 0, 1);
  --ease-enter: cubic-bezier(0.16, 1, 0.3, 1);
  --ease-exit: cubic-bezier(0.4, 0, 1, 1);

  /* z-index */
  --z-base: 0;
  --z-sticky: 10;     /* sticky table headers / first columns */
  --z-sidebar: 20;
  --z-topbar: 30;
  --z-dropdown: 40;
  --z-overlay: 80;    /* scrims */
  --z-modal: 90;      /* dialogs, drawers, command palette */
  --z-toast: 100;
  --z-tooltip: 110;   /* above dialogs: tooltips can live inside them */

  /* focus */
  --focus-width: 2px;
  --focus-offset: 2px;
}

@media (prefers-reduced-motion: reduce) {
  :root {
    --duration-1: 0ms;
    --duration-2: 0ms;
    --duration-3: 0ms;
    --duration-4: 0ms;
    --duration-progress: 0ms;
  }
}

/* ---------- light theme (default) ---------- */
:root {
  color-scheme: light;

  /* neutral scale: Radix Sand (light) */
  --gray-1: #fdfdfc;
  --gray-2: #f9f9f8;
  --gray-3: #f1f0ef;
  --gray-4: #e9e8e6;
  --gray-5: #e2e1de;
  --gray-6: #dad9d6;
  --gray-7: #cfceca;
  --gray-8: #bcbbb5;
  --gray-9: #8d8d86;
  --gray-10: #82827c;
  --gray-11: #63635e;
  --gray-12: #21201c;

  /* surfaces */
  --bg-canvas: var(--gray-2);   /* page behind everything, sidebar */
  --bg-surface: #ffffff;        /* panels, cards, tables, topbar */
  --bg-raised: #ffffff;         /* menus, popovers, dialogs, drawers, toasts (+shadow) */
  --bg-subtle: var(--gray-2);   /* table header, wells, code, fact lists */
  --bg-hover: var(--gray-3);
  --bg-active: var(--gray-4);   /* pressed, selected row, active nav item */
  --bg-input: #ffffff;
  --bg-inverse: var(--gray-12); /* simple tooltips, kbd */
  --bg-overlay: rgb(33 32 28 / 0.32);

  /* text */
  --text-primary: var(--gray-12);
  --text-secondary: var(--gray-11);
  --text-disabled: var(--gray-9);
  --text-inverse: var(--gray-1);
  --text-on-accent: #ffffff;

  /* borders */
  --border-subtle: var(--gray-4);   /* row dividers, inner separators */
  --border-default: var(--gray-6);  /* panels, cards, sidebar edge, topbar */
  --border-strong: var(--gray-7);   /* secondary buttons, segmented controls */
  --border-input: var(--gray-9);    /* inputs/selects/file: >= 3:1 vs surface */

  /* accent: FlowState blue */
  --accent-solid: #256abf;
  --accent-solid-hover: #1c5cab;
  --accent-solid-active: #184f95;
  --accent-text: #1f63b8;
  --accent-subtle: #eef3fa;
  --accent-border: #9ec5f4;
  --focus-ring: #256abf;

  /* semantic (status only) */
  --success-text: #1b744d;
  --success-subtle: #e6f6eb;
  --success-border: #adddc0;
  --success-solid: #30a46c;
  --warning-text: #975800;
  --warning-subtle: #fefbe9;
  --warning-border: #f3d673;
  --warning-solid: #ffc53d;
  --danger-text: #c0262b;
  --danger-subtle: #feebec;
  --danger-border: #fdbdbe;
  --danger-solid: #ce2c31;
  --info-text: var(--accent-text);
  --info-subtle: var(--accent-subtle);
  --info-border: var(--accent-border);

  /* progress */
  --progress-track: var(--gray-4);
  --progress-running: var(--accent-solid);
  --progress-done: var(--success-text);
  --progress-failed: var(--danger-text);

  /* shadows: floating layers only, never on surfaces */
  --shadow-sm: 0 1px 2px rgb(33 32 28 / 0.06);
  --shadow-md: 0 1px 3px rgb(33 32 28 / 0.06), 0 6px 16px rgb(33 32 28 / 0.08);
  --shadow-lg: 0 4px 12px rgb(33 32 28 / 0.08), 0 24px 56px rgb(33 32 28 / 0.18);

  /* select chevron (stroke = --text-secondary) */
  --icon-chevron: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6' viewBox='0 0 10 6'%3E%3Cpath d='M1 1l4 4 4-4' fill='none' stroke='%2363635e' stroke-width='1.5' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E");

  /* data-viz chrome (plots read these at paint time) */
  --viz-surface: var(--bg-surface);
  --viz-frame: var(--border-default);
  --viz-grid: var(--gray-4);
  --viz-axis: var(--gray-8);
  --viz-tick-text: var(--text-secondary);
  --viz-crosshair: var(--gray-12);
  --viz-crosshair-halo: rgb(255 255 255 / 0.8);
  --viz-null-bg: var(--gray-3);
  --viz-null-hatch: var(--gray-7);
  --viz-ci-opacity: 0.14;

  /* categorical series: validated, light, surface #ffffff (§7.3) */
  --viz-baseline: #8d8d86;   /* "baseline (no controlled vehicles)": neutral, not a slot */
  --viz-series-1: #2a78d6;   /* follower_stopper */
  --viz-series-2: #eb6834;   /* pi_saturation */
  --viz-series-3: #1baf7a;   /* jad */
  --viz-series-4: #eda100;   /* vsl / infrastructure */
  --viz-series-5: #e87ba4;   /* pi_meanfrac (superseded, labelled) */

  /* ordinal (compliance / penetration as series): validated --ordinal */
  --viz-ord-1: #86b6ef;
  --viz-ord-2: #5598e7;
  --viz-ord-3: #256abf;
  --viz-ord-4: #104281;

  /* sweep matrix diverging bins (§7.4) */
  --viz-div-neutral: var(--gray-3);
  --viz-div-better-1: #cde2fb;
  --viz-div-better-2: #9ec5f4;
  --viz-div-better-3: #5598e7;
  --viz-div-better-4: #256abf;
  --viz-div-worse-1: #fcd5d0;
  --viz-div-worse-2: #f6aba2;
  --viz-div-worse-3: #e7685e;
  --viz-div-worse-4: #bd2e2b;
  --viz-div-ink-1-3: var(--gray-12);  /* text on neutral and classes 1–3 */
  --viz-div-ink-4: #ffffff;           /* text on class 4 */
}

/* ---------- dark theme: OS preference (unless pinned light) ---------- */
@media (prefers-color-scheme: dark) {
  :root:not([data-theme='light']) {
    color-scheme: dark;

    --gray-1: #111110;
    --gray-2: #191918;
    --gray-3: #222221;
    --gray-4: #2a2a28;
    --gray-5: #31312e;
    --gray-6: #3b3a37;
    --gray-7: #494844;
    --gray-8: #62605b;
    --gray-9: #6f6d66;
    --gray-10: #7c7b74;
    --gray-11: #b5b3ad;
    --gray-12: #eeeeec;

    --bg-canvas: var(--gray-1);
    --bg-surface: var(--gray-2);
    --bg-raised: var(--gray-3);
    --bg-subtle: var(--gray-1);
    --bg-hover: var(--gray-3);
    --bg-active: var(--gray-4);
    --bg-input: var(--gray-1);
    --bg-inverse: var(--gray-12);
    --bg-overlay: rgb(0 0 0 / 0.6);

    --text-primary: var(--gray-12);
    --text-secondary: var(--gray-11);
    --text-disabled: var(--gray-9);
    --text-inverse: var(--gray-1);
    --text-on-accent: #ffffff;

    --border-subtle: var(--gray-4);
    --border-default: var(--gray-6);
    --border-strong: var(--gray-7);
    --border-input: var(--gray-9);

    --accent-solid: #256abf;
    --accent-solid-hover: #2f6fc4;
    --accent-solid-active: #1c5cab;
    --accent-text: #86b6ef;
    --accent-subtle: #1b2939;
    --accent-border: #265085;
    --focus-ring: #5598e7;

    --success-text: #3dd68c;
    --success-subtle: #132d21;
    --success-border: #20573e;
    --success-solid: #30a46c;
    --warning-text: #ffca16;
    --warning-subtle: #302008;
    --warning-border: #5c3d05;
    --warning-solid: #ffc53d;
    --danger-text: #ff9592;
    --danger-subtle: #3b1219;
    --danger-border: #72232d;
    --danger-solid: #ce2c31;
    --info-text: var(--accent-text);
    --info-subtle: var(--accent-subtle);
    --info-border: var(--accent-border);

    --progress-track: var(--gray-5);
    --progress-running: var(--accent-text);
    --progress-done: var(--success-text);
    --progress-failed: var(--danger-text);

    --shadow-sm: 0 1px 2px rgb(0 0 0 / 0.4);
    --shadow-md: 0 1px 3px rgb(0 0 0 / 0.4), 0 8px 24px rgb(0 0 0 / 0.5);
    --shadow-lg: 0 4px 12px rgb(0 0 0 / 0.4), 0 24px 64px rgb(0 0 0 / 0.6);

    --icon-chevron: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6' viewBox='0 0 10 6'%3E%3Cpath d='M1 1l4 4 4-4' fill='none' stroke='%23b5b3ad' stroke-width='1.5' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E");

    --viz-surface: var(--bg-surface);
    --viz-frame: var(--border-default);
    --viz-grid: var(--gray-4);
    --viz-axis: var(--gray-7);
    --viz-tick-text: var(--text-secondary);
    --viz-crosshair: var(--gray-12);
    --viz-crosshair-halo: rgb(0 0 0 / 0.7);
    --viz-null-bg: var(--gray-3);
    --viz-null-hatch: var(--gray-6);
    --viz-ci-opacity: 0.22;

    --viz-baseline: #7c7b74;
    --viz-series-1: #3987e5;
    --viz-series-2: #d95926;
    --viz-series-3: #199e70;
    --viz-series-4: #c98500;
    --viz-series-5: #d55181;

    --viz-ord-1: #256abf;
    --viz-ord-2: #3987e5;
    --viz-ord-3: #6da7ec;
    --viz-ord-4: #b7d3f6;

    --viz-div-neutral: var(--gray-4);
    --viz-div-better-1: #1f3654;
    --viz-div-better-2: #204b7f;
    --viz-div-better-3: #215da5;
    --viz-div-better-4: #7aaeef;
    --viz-div-worse-1: #502824;
    --viz-div-worse-2: #77312b;
    --viz-div-worse-3: #993a33;
    --viz-div-worse-4: #e88f85;
    --viz-div-ink-1-3: var(--gray-12);
    --viz-div-ink-4: var(--gray-1);
  }
}

/* ---------- dark theme: pinned (body identical to the block above) ---------- */
:root[data-theme='dark'] {
  color-scheme: dark;

  --gray-1: #111110;
  --gray-2: #191918;
  --gray-3: #222221;
  --gray-4: #2a2a28;
  --gray-5: #31312e;
  --gray-6: #3b3a37;
  --gray-7: #494844;
  --gray-8: #62605b;
  --gray-9: #6f6d66;
  --gray-10: #7c7b74;
  --gray-11: #b5b3ad;
  --gray-12: #eeeeec;

  --bg-canvas: var(--gray-1);
  --bg-surface: var(--gray-2);
  --bg-raised: var(--gray-3);
  --bg-subtle: var(--gray-1);
  --bg-hover: var(--gray-3);
  --bg-active: var(--gray-4);
  --bg-input: var(--gray-1);
  --bg-inverse: var(--gray-12);
  --bg-overlay: rgb(0 0 0 / 0.6);

  --text-primary: var(--gray-12);
  --text-secondary: var(--gray-11);
  --text-disabled: var(--gray-9);
  --text-inverse: var(--gray-1);
  --text-on-accent: #ffffff;

  --border-subtle: var(--gray-4);
  --border-default: var(--gray-6);
  --border-strong: var(--gray-7);
  --border-input: var(--gray-9);

  --accent-solid: #256abf;
  --accent-solid-hover: #2f6fc4;
  --accent-solid-active: #1c5cab;
  --accent-text: #86b6ef;
  --accent-subtle: #1b2939;
  --accent-border: #265085;
  --focus-ring: #5598e7;

  --success-text: #3dd68c;
  --success-subtle: #132d21;
  --success-border: #20573e;
  --success-solid: #30a46c;
  --warning-text: #ffca16;
  --warning-subtle: #302008;
  --warning-border: #5c3d05;
  --warning-solid: #ffc53d;
  --danger-text: #ff9592;
  --danger-subtle: #3b1219;
  --danger-border: #72232d;
  --danger-solid: #ce2c31;
  --info-text: var(--accent-text);
  --info-subtle: var(--accent-subtle);
  --info-border: var(--accent-border);

  --progress-track: var(--gray-5);
  --progress-running: var(--accent-text);
  --progress-done: var(--success-text);
  --progress-failed: var(--danger-text);

  --shadow-sm: 0 1px 2px rgb(0 0 0 / 0.4);
  --shadow-md: 0 1px 3px rgb(0 0 0 / 0.4), 0 8px 24px rgb(0 0 0 / 0.5);
  --shadow-lg: 0 4px 12px rgb(0 0 0 / 0.4), 0 24px 64px rgb(0 0 0 / 0.6);

  --icon-chevron: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6' viewBox='0 0 10 6'%3E%3Cpath d='M1 1l4 4 4-4' fill='none' stroke='%23b5b3ad' stroke-width='1.5' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E");

  --viz-surface: var(--bg-surface);
  --viz-frame: var(--border-default);
  --viz-grid: var(--gray-4);
  --viz-axis: var(--gray-7);
  --viz-tick-text: var(--text-secondary);
  --viz-crosshair: var(--gray-12);
  --viz-crosshair-halo: rgb(0 0 0 / 0.7);
  --viz-null-bg: var(--gray-3);
  --viz-null-hatch: var(--gray-6);
  --viz-ci-opacity: 0.22;

  --viz-baseline: #7c7b74;
  --viz-series-1: #3987e5;
  --viz-series-2: #d95926;
  --viz-series-3: #199e70;
  --viz-series-4: #c98500;
  --viz-series-5: #d55181;

  --viz-ord-1: #256abf;
  --viz-ord-2: #3987e5;
  --viz-ord-3: #6da7ec;
  --viz-ord-4: #b7d3f6;

  --viz-div-neutral: var(--gray-4);
  --viz-div-better-1: #1f3654;
  --viz-div-better-2: #204b7f;
  --viz-div-better-3: #215da5;
  --viz-div-better-4: #7aaeef;
  --viz-div-worse-1: #502824;
  --viz-div-worse-2: #77312b;
  --viz-div-worse-3: #993a33;
  --viz-div-worse-4: #e88f85;
  --viz-div-ink-1-3: var(--gray-12);
  --viz-div-ink-4: var(--gray-1);
}

/* ---------- legacy aliases: DELETE in step 5 (§12) ---------- */
:root {
  --bg: var(--bg-canvas);
  --panel: var(--bg-surface);
  --panel-edge: var(--border-default);
  --panel-raised: var(--bg-subtle);
  --panel-inset: var(--bg-subtle);
  --text: var(--text-primary);
  --muted: var(--text-secondary);
  --faint: var(--text-secondary);   /* upgraded: the old --faint failed AA */
  --accent: var(--accent-text);
  --accent-dim: var(--accent-subtle);
  --amber: var(--warning-text);
  --amber-dim: var(--warning-subtle);
  --danger: var(--danger-text);
  --danger-dim: var(--danger-subtle);
  --ok: var(--success-text);
  --ok-dim: var(--success-subtle);
  --s1: var(--space-2);
  --s2: var(--space-4);
  --s3: var(--space-6);
  --s4: var(--space-8);
  --s5: var(--space-10);
  --s6: var(--space-12);
  --r1: var(--radius-xs);
  --r2: var(--radius-sm);
  --r3: var(--radius-md);
  --font-ui: var(--font-sans);
  --card-shadow: none;
  --ring: 0 0 0 var(--focus-width) var(--focus-ring);
}
```

### 5.1 Theme mechanics

* **Pre-paint.** `index.html` sets `data-theme` before React mounts, so there is no flash
  of the wrong theme. Replace the `<meta name="color-scheme">` and the inline `<style>`
  with:

  ```html
  <meta name="color-scheme" content="light dark" />
  <title>FlowState</title>
  <script>
    try {
      var t = localStorage.getItem('flowstate.theme');
      if (t === 'light' || t === 'dark') document.documentElement.dataset.theme = t;
    } catch (e) {}
  </script>
  <style>
    html { background: #f9f9f8; }
    @media (prefers-color-scheme: dark) { html:not([data-theme='light']) { background: #111110; } }
    html[data-theme='dark'] { background: #111110; }
  </style>
  ```

* **`src/lib/theme.ts`** (new) exports:

  ```ts
  export type ThemePref = 'system' | 'light' | 'dark';
  export type Theme = 'light' | 'dark';
  export const THEME_STORAGE_KEY = 'flowstate.theme';
  export const THEME_EVENT = 'flowstate:themechange';
  export function getThemePref(): ThemePref;          // localStorage, try/catch, default 'system'
  export function setThemePref(p: ThemePref): void;   // write storage; set/remove documentElement.dataset.theme; dispatch THEME_EVENT
  export function resolvedTheme(): Theme;             // data-theme if set, else matchMedia('(prefers-color-scheme: dark)')
  export function useResolvedTheme(): Theme;          // useSyncExternalStore over matchMedia 'change' + THEME_EVENT
  export function readToken(name: string, el?: Element): string; // getComputedStyle(el ?? documentElement).getPropertyValue(name).trim()
  ```

  **jsdom has no `window.matchMedia`.** Guard every call with
  `typeof window.matchMedia === 'function'` and treat a missing one as light.
  Otherwise every test that renders `Layout` will throw.
* **Canvas and SVG never hard-code color.** `HeatmapCanvas` reads `--viz-*` through
  `readToken` at paint time and lists `useResolvedTheme()` in its paint-effect
  dependencies, so a theme flip repaints. SVG uses `var(--…)` in `fill`/`stroke`
  attributes directly.
* **Data colormaps are theme-invariant** (§7.1). Chrome around them (frame, ticks,
  crosshair, null hatch) follows the theme.

### 5.2 Typography usage

Load Inter 400/500/600 and JetBrains Mono 400/500. **Drop the JetBrains Mono 700
import** from `main.tsx`, because nothing uses it any more. Body: `font: 400 14px/20px
var(--font-sans)`, `letter-spacing: var(--tracking-normal)`, `-webkit-font-smoothing:
antialiased`, `text-rendering: optimizeLegibility`.

| Role | Family | Size / line | Weight | Tracking | Case | Color |
|---|---|---|---|---|---|---|
| Page title (h1) | Inter | 20 / 28 | 600 | tight | Sentence | primary |
| Section title (h2) | Inter | 16 / 24 | 600 | snug | Sentence | primary |
| Panel title (h3) | Inter | 14 / 20 | 600 | normal | Sentence | primary |
| Body / prose | Inter | 14 / 22 | 400 | normal | Sentence | primary; max 72ch |
| UI text: buttons, nav, inputs, table text cells | Inter | 13 / 20 | 400 (buttons, nav: 500) | normal | Sentence | primary/secondary |
| Field label | Inter | 12 / 16 | 500 | none | Sentence | **primary** |
| Helper text, captions, table headers, meta | Inter | 12 / 16 | 400 (headers 500) | none | Sentence | secondary |
| Eyebrow (metric label), badge | Inter | 11 / 16 | 500 | caps | As authored (already uppercase in source) | secondary / badge color |
| Metric tile value | Inter | 24 / 32 | 600 | tight | n/a | primary; **proportional** figures |
| Machine values: IDs, hashes, seeds, paths, numbers in tables, CI line, readouts | JetBrains Mono | 12 / 20 (11 / 16 in compact contexts) | 400 (run id in title: 500) | none | n/a | primary/secondary |
| Axis ticks | JetBrains Mono | 11 / 16 | 400 | none | n/a | `--viz-tick-text` |

Rules:

* **No CSS `text-transform: uppercase` anywhere.** Text that is uppercase in the source
  (badges, `METRIC_DEFS` labels, `'API LINK'`) stays as authored. The comment in
  `lib/metrics.ts` already forbids uppercasing because σ becomes Σ.
* Mono is never letter-spaced and is never bold.
* Numbers that align in a column use mono. A standalone large number (metric tile value)
  uses Inter with proportional figures.
* Verify after install that Inter's `tnum` is present in the `@fontsource/inter` build
  (in devtools, `font-variant-numeric: tabular-nums` makes "1111" and "0000" equal
  width). If it is missing, the few Inter places that request `tabular-nums` (legend
  ticks, slider values) switch to mono.

---

## 6. Layout

### 6.1 App shell

```
┌──────────────────┬─────────────────────────────────────────────────────────────────┐
│ ▨ FlowState       │ Runs / run-a41d09                    ⌖ i24_replica      ◐     │ topbar 48
│                  ├─────────────────────────────────────────────────────────────────┤
│ Set up           │ ⚠ API offline — showing demo data, not results from this server │ banner (only when needed)
│  ⌖ Onboard corridor├─────────────────────────────────────────────────────────────────┤
│  ◫ Scenarios     │                                                                 │
│ Simulate         │   Page title  [meta]                     [Secondary] [Primary]  │
│  ∿ Runs          │   One-line description of the page.                             │
│  ▦ Sweeps        │                                                                 │
│ Report           │   ┌ panel ───────────────────────────────────────────────────┐  │
│  ▤ Reports       │   │                                                           │  │
│                  │   └───────────────────────────────────────────────────────────┘  │
│                  │                                                                 │
│ ──────────────── │                                                                 │
│ ▷ First run      │                                                                 │
│ ● API LINK       │                                                                 │
│ ⚙ Settings       │                                                                 │
└──────────────────┴─────────────────────────────────────────────────────────────────┘
   sidebar 232            main column: scrolls; page max-width 1440, padding 24/24/48
```

* **Grid.** `.app { display: grid; grid-template-columns: var(--sidebar-w) minmax(0,1fr);
  height: 100dvh; }`. The main column is `grid-template-rows: var(--topbar-h) auto
  minmax(0,1fr)` (topbar, banners, scrolling content).
* **Sidebar** (`<aside class="sidebar">`).
  * Background is `--bg-canvas` with a right border of 1 px `--border-default`.
    Padding is `12px 8px`.
  * Brand row (40 px). The mark is a 20 px rounded square, `fill: currentColor`
    (`--text-primary`), with three parallel diagonal stripes in `--bg-surface`
    (`M4 8 L9 13  M7 5 L14 12  M11 4 L16 9`, stroke 2, round caps): wave bands
    sloping down-right, as they do in our space-time plots. Next to it is the wordmark
    "FlowState" in Inter 15 px/600, tracking `-0.01em`.
  * Group labels, sentence case 12/16 500 secondary, 16 px above each group: **"Set up"**
    (Onboard corridor, Scenarios), **"Simulate"** (Runs, Sweeps), **"Report"** (Reports).
    The footer holds "First run", the connection status line, and the Settings button.
  * Nav item. Height 32, padding `0 10px`, radius `--radius-sm`, gap 10. Icon 16 px in
    `--text-secondary`; label 13/20 500 in `--text-secondary`. Hover:
    `--bg-hover` + `--text-primary`. Active (`aria-current="page"`, which NavLink sets):
    `--bg-active` + `--text-primary`, icon `--text-primary`. **No accent bar and no
    accent color.** The sidebar stays dimmer than the content (Linear).
  * Status line. The status strings stay as authored (`API LINK`, `KEY REJECTED`,
    `DEMO DATA`, `API OFFLINE`, `PROBING…`), at 11/16 500, tracking caps, secondary.
    The 8 px dot is `--success-solid` (link), `--danger-text` (down/rejected) or
    `--warning-solid` (demo/probing). Pulse only in the down state and only without
    reduced motion. Keep `title="Live /health probe, every 5 s"`.
* **Topbar** (`<header class="topbar">`). 48 px, `--bg-surface`, bottom border
  `--border-default`, padding `0 24px`.
  * Left: a **breadcrumb** (`<nav aria-label="Breadcrumb">`, 13 px). The section name is
    in secondary as a link, then a `/` separator, then the current item in primary 500
    with `aria-current="page"`. On Run detail the current item is the run id in mono.
  * Right: the **active-corridor chip**. It is a pill with a map-pin icon and the name
    in mono 12 px; empty reads "No active corridor" in secondary. The chip reads
    `useAppState().corridor`. Don't change when views set it: `onboard.test.tsx` probes
    the context value through its own `data-testid="active"` component. Then the
    **theme toggle**: an icon button
    that cycles System → Light → Dark, with `aria-label="Theme: System"` and so on.
  * Remove the UTC clock.
* **Banners.** These are full-width strips between the topbar and content, styled as
  edge-to-edge callouts (no radius, bottom border in the tone's border color, 16 px icon,
  13 px text, padding `8px 24px`).
  * The auth banner is danger tone and **keeps `role="alert"`, its text, and the
    `Retry now` / `Open Settings` buttons** (small secondary).
  * The offline banner is warning tone, with no role.
* **Content.** `<main class="content">` scrolls. The inner `.page` has
  `max-width: var(--content-max)`, margin `0 auto`, padding `24px 24px 48px`, and
  `display: flex; flex-direction: column; gap: 24px`.

### 6.2 Page header pattern (`components/PageHeader.tsx`, new)

```
Title (h1 20/28 600)  [meta: count · badges]                      [ghost] [secondary] [primary]
Description (13/20 secondary, max 72ch), what this page is for, one sentence.
```

```ts
export function PageHeader(props: {
  title: ReactNode;
  description?: ReactNode;
  meta?: ReactNode;          // counts, live indicator, DEMO DATA badge
  actions?: ReactNode;       // right-aligned; at most one primary
  documentTitle?: string;    // sets document.title = `${documentTitle} · FlowState`
}): JSX.Element;
```

* The layout is flex, with `align-items: flex-start` and `justify-content: space-between`.
  Below 1024 px, actions wrap under the description.
* `meta` sits inline after the title, at 13 px secondary, with a gap of 8. Counts are
  mono.
* At most one primary button per page header. A view's launcher button lives in its
  panel, not the header, unless stated in §10.

### 6.3 Content width and grid

* Data views (Runs, Run detail, Sweeps, Reports, Scenarios) use `--content-max` 1440 px.
  Forms inside them cap at `--content-max-narrow` 960 px. Prose caps at 72ch.
* Use a 12-column grid utility `.grid-12 { display: grid; grid-template-columns:
  repeat(12, minmax(0,1fr)); gap: 16px; }` with `.col-span-{n}` helpers, used where a
  side-by-side split is specified in §10. Everything else stacks.
* Panels set `container-type: inline-size`, so tile grids respond to the panel's width
  (and therefore to sidebar collapse), not the window.
* Vertical rhythm: 24 px between top-level sections, 16 px inside panels, 12 px between a
  heading and its content, 8 px between related controls.

### 6.4 Responsive behavior

CSS can't use custom properties in media queries, so these are constants.

| Width | Sidebar | Page padding | Other |
|---|---|---|---|
| ≥ 1280 px | Expanded 232 px | 24 | Full layouts |
| 1024–1279 px | Expanded 232 px | 24 | Metric grid falls to 3 columns via container query |
| 768–1023 px (tablet) | **Collapsed 56 px icon rail.** Labels are visually hidden with the `.visually-hidden` clip, *not* `display:none`, so the accessible names and the tests are unchanged. A `title` holds the label. Group labels are hidden. | 16 | Page-header actions wrap. Launcher forms become 2 columns. Tables scroll horizontally inside `.table-wrap`. The heatmap is full width with height `clamp(240px, 42vw, 460px)`. |
| < 768 px | Hidden. A menu icon button in the topbar opens it as an overlay drawer (`--z-modal`, scrim, Esc closes, focus trapped). | 16 | Not a target, but nothing may overflow the viewport. |

---

## 7. Data visualization

### 7.1 Speed heatmap colormap: "FlowState speed" (theme-invariant)

The map is semantic heat: deep red for stopped traffic, a pale neutral at the 60 km/h
transition, and blue for free flow. It keeps the traffic-engineering convention that
red means jam, replaces the CVD-unsafe green with blue, and puts a lightness peak at the
transition so wave fronts get a crisp light edge. Each arm is monotone in OKLCH
lightness (0.34 → 0.955 on the congested arm, 0.955 → 0.50 on the free-flow arm). The
worst cross-arm separation is **ΔE 17.6 under deuteranopia, 18.7 under protanopia and
19.3 under tritanopia** (OKLab ×100, Machado 2009), against a target of ≥ 8. The map
matches the website's "jam-red through amber to free-flow blue".

Domain: 0 to `SPEED_DOMAIN_MAX_MS = 33.3` m/s (= 120 km/h, the §3.1 v0), clamped.
Interpolation is piecewise linear in sRGB between the anchors. The existing
`sampleRamp` is unchanged; only `SPEED_STOPS` changes. The stops are 15 km/h apart,
which keeps sRGB interpolation close to perceptual.

| at | km/h | mph | Hex | RGB (for `SPEED_STOPS`) | OKLCH L / C / h | Reads as |
|---|---|---|---|---|---|---|
| 0.000 | 0 | 0 | `#6a0d18` | `[106, 13, 24]` | 0.340 / 0.125 / 22 | stopped (jam core) |
| 0.125 | 15 | 9.3 | `#af2520` | `[175, 37, 32]` | 0.494 / 0.175 / 28 | crawling |
| 0.250 | 30 | 18.6 | `#de6531` | `[222, 101, 49]` | 0.648 / 0.165 / 42 | stop-and-go |
| 0.375 | 45 | 28.0 | `#f1af5d` | `[241, 175, 93]` | 0.800 / 0.125 / 70 | slow |
| 0.500 | 60 | 37.3 | `#f6f0da` | `[246, 240, 218]` | 0.955 / 0.030 / 95 | transition (neutral) |
| 0.625 | 75 | 46.6 | `#a5d2ed` | `[165, 210, 237]` | 0.841 / 0.060 / 235 | recovering |
| 0.750 | 90 | 55.9 | `#6faee2` | `[111, 174, 226]` | 0.728 / 0.100 / 245 | near free flow |
| 0.875 | 105 | 65.2 | `#4487d0` | `[68, 135, 208]` | 0.614 / 0.130 / 252 | free flow |
| 1.000 | 120 | 74.6 | `#2a61b1` | `[42, 97, 177]` | 0.500 / 0.140 / 258 | free flow at v0 |

**Why theme-invariant.** The plot is a measurement image. A screenshot, the report PDF
and the dashboard in either theme must read identically. Only the surrounding chrome is
themed.

**No-data bins.** These are bins with no vehicle (`null`). Paint them with
`--viz-null-bg`, then overlay a 45° hatch: a 1 px line every 6 px in `--viz-null-hatch`,
drawn from a 6×6 pattern canvas (`ctx.createPattern`) over each null bin's rectangle.
`binColor()` keeps returning `NULL_BIN_RGB` for null, so the image pass stays simple.
Set `NULL_BIN_RGB = [241, 240, 239]` (`--viz-null-bg` light) as the image-pass
placeholder. The hatch pass paints the themed value on top. Update `colormap.test.ts`
to the new constants.

### 7.2 Density colormap (theme-invariant)

This is a sequential, monotone-lightness map (OKLCH L 0.975 → 0.355), from a pale empty
road to deep red at jam density. "Deep red = congested" therefore holds in both fields.
Domain: 0 to `DENSITY_DOMAIN_MAX_VEHM = 0.16` veh/m (160 veh/km), as today.

| at | veh/km | veh/mi | Hex | RGB (for `DENSITY_STOPS`) |
|---|---|---|---|---|
| 0.0 | 0 | 0 | `#faf7ec` | `[250, 247, 236]` |
| 0.2 | 32 | 51 | `#efd29e` | `[239, 210, 158]` |
| 0.4 | 64 | 103 | `#ef9e4f` | `[239, 158, 79]` |
| 0.6 | 96 | 154 | `#df622b` | `[223, 98, 43]` |
| 0.8 | 128 | 206 | `#b12d26` | `[177, 45, 38]` |
| 1.0 | 160 | 257 | `#6f131c` | `[111, 19, 28]` |

The density field is light-dominant in both themes by design.

### 7.3 Heatmap anatomy (`HeatmapCanvas.tsx`, `RampLegend`)

```
┌ Space–time field ────────── [SPEED | DENSITY] ─────────────────── [⤓ Download CSV] ┐
│                                                                                     │
│ Position (km)                                                                       │
│  10 ┤▓▓▓▓▓▓▓▓▓▓░░░░▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓│ canvas
│   5 ┤       ██▒▒       ██▒▒        ██▒▒                                              │ height clamp(300px, 0.42w, 460px)
│   0 ┤                                                                                │
│     └────┬────────┬────────┬────────┬───────                                         │
│          0        5        10       15  Time (min)                                   │
│                                                                                     │
│ km/h  0     20    40▼   60    80    100   120        ▨ no data                     │ legend, 320 px bar
│      [■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■■]                                     │
│ mph   0   10   20   30   40   50   60   70           ▼ wave threshold 40 km/h (default) │
│                                                                                     │
│ t 12.5 min · x 3.40 km · 47 km/h (29 mph)                                          │ readout row, 20 px, mono 12
│ Downstream is up. Waves travelling upstream slope down to the right.               │ caption, 12 secondary
└─────────────────────────────────────────────────────────────────────────────────────┘
```

* **Plot.** Margins are `{ l: 64, r: 12, t: 8, b: 40 }`. Bins are painted at native
  resolution with nearest-neighbor scaling, as today. Frame: 1 px `--viz-frame`.
  There are **no gridlines over the field**, because they would hide the data. Tick
  marks are 4 px in `--viz-axis`; tick labels are 11 px mono in `--viz-tick-text`. Axis
  titles are 12 px Inter 500 in `--text-secondary`, sentence case with units: "Time (min)"
  and "Position (km)", or "Position (m)" when the span is under 1 km.
* **Sizing.** Use a `ResizeObserver` on the wrapper, not `window` resize, so the plot
  follows sidebar collapse and container changes.
* **Crosshair.** Solid lines, never dashed: first 3 px `--viz-crosshair-halo`, then 1 px
  `--viz-crosshair` at 0.9 alpha. A 6 px center dot with a 2 px halo.
* **Readout row.** This replaces the floating box. It is a fixed-height row under the
  legend, mono 12 px: `t {formatTimeMin} · x {formatDistAdaptive} · {value}`. For speed,
  value is `47 km/h (29 mph)`; for density, `38 veh/km (61 veh/mi)`; null is `no data`.
  When idle, show in secondary: "Hover the field, or focus it and use the arrow keys, to
  read values."
* **Keyboard (P1).** The plot wrapper is `tabIndex={0}`, `role="img"`, with
  `aria-label="Space–time {speed|density} field, {t0}–{t1} min, {x0}–{x1} km. Colour scale
  {legend range}."`
  * When focused, the crosshair starts at the center. The arrow keys move it one bin and
    Shift + arrow moves it 10 bins; Home and End jump to the time edges.
  * The readout updates, and gets `aria-live="polite"` only while the plot has focus.
  * Focus shows the standard focus ring around the plot frame.
* **Legend (`RampLegend`).** The bar is 320 px wide (min 240), 10 px tall, radius 2, with
  a 1 px `--viz-frame` border. It is drawn from the same stops via `rampGradientCSS`.
  * Speed: km/h ticks *above* at 0/20/40/60/80/100/120, and mph ticks *below* at
    0/10/…/70, each positioned at `mph × 1.609344 / 120`. All tick labels are 11 px
    mono secondary.
  * A 1 × 14 px notch in `--text-primary` at 40 km/h is labelled **"wave threshold
    40 km/h (default)"** (`CLAUDE.md` §7.2 `v_jam_thresh`). Say "default", because the
    run may configure another value.
  * Density: veh/km ticks above at 0/40/80/120/160, veh/mi ticks below at 0/50/…/250.
  * The "no data" key is a 14×10 hatch swatch with the label.
* **Table-view twin.** "Download CSV" (ghost, sm, download icon) writes the binned field
  already in memory: columns `t_s,x_m,speed_ms` (or `density_vehm`), SI units, empty for
  null. The file is `flowstate-{run_id}-{field}-seed{N}-{micro|screening}[-demo].csv` via
  `lib/download.saveText`, opening with `# key: value` provenance lines (run id, source,
  tier — `screening` for macro runs — seed, replicate, config hash, field and units, bin
  sizes, export time; skipped by pandas/numpy/R with `comment="#"`; 2026-10-07 review).
  There is no new API.
* **States** (all at the same fixed height): skeleton frame with "Loading the speed
  field…", error callout with Retry, and an empty callout when the API returns 0 bins.
  None of these may be an indefinite "loading".
* **Formatting helpers** (`lib/format.ts`, with tests): add `MS_TO_MPH = 2.2369362920544`
  and `KM_PER_MI = 1.609344` as named constants, plus `formatSpeedKmhMph(ms)` →
  `"47 km/h (29 mph)"` and `formatDensityVehKmMi(vehm)` → `"38 veh/km (61 veh/mi)"`. Use
  no inline conversions (`CLAUDE.md` §2).

### 7.4 Sweep matrix: binned diverging (blue = better, red = worse)

* Signed improvement is `s = (def.good === 'down' ? -Δ : Δ)`, where
  `Δ = (cell − ref)/|ref|`. Bin `|s|` into: **< 2 % neutral**, 2–10 % class 1, 10–25 %
  class 2, 25–50 % class 3, and ≥ 50 % class 4. The sign picks the better or worse arm.
  Discrete classes, not a gradient, is the Carbon rule.
* Replace `deltaColor()` in `lib/colormap.ts` with
  `deltaClass(s: number): 'delta-n' | 'delta-b1' | … | 'delta-b4' | 'delta-w1' | … | 'delta-w4'`.
  The CSS colors the cell from the `--viz-div-*` tokens, so the theme switches without
  JavaScript. Test the bin edges.
* Ink: neutral and classes 1–3 use `--viz-div-ink-1-3`; class 4 uses `--viz-div-ink-4`.
  Computed text contrast, light theme:

  | Class | Better-arm cell | Worse-arm cell |
  |---|---|---|
  | 3 | `#5598e7` with ink: 5.46:1 | `#e7685e` with ink: 5.08:1 |
  | 4 | `#256abf` with white: 5.39:1 | `#bd2e2b` with white: 5.85:1 |

  Dark theme:

  | Class | Better-arm cell | Worse-arm cell |
  |---|---|---|
  | 3 | `#215da5` with light ink: 5.70:1 | `#993a33` with light ink: 6.01:1 |
  | 4 | `#7aaeef` with dark ink: 8.21:1 | `#e88f85` with dark ink: 7.83:1 |

  Every other cell exceeds 7:1.
* The class-1 cells (blue vs red tint) are ΔE 6.4 under deuteranopia in light (9.2 in
  dark). That is legal only with secondary encoding, which we have: every cell prints a
  **signed** delta (`-3.1%` / `+3.1%`).
* Legend row above the matrix, left to right: `worse ≥50 · 25–50 · 10–25 · 2–10 | ±2 |
  2–10 · 10–25 · 25–50 · ≥50 better`. Swatches are 16×12 with mono 11 px labels. Then
  `≡ identical realisation` and `n = replicates per cell`.

### 7.5 Categorical series (controllers) and ordinal series

Color follows the entity, never its rank. Slots are fixed. This is the same mapping as
`CONTROLLER_COLORS` in `scripts/m3_analyze_sweep.py`, so the dashboard and the report
figures agree.

| Entity | Token | Light | Dark |
|---|---|---|---|
| baseline (no controlled vehicles): neutral, not a slot | `--viz-baseline` | `#8d8d86` | `#7c7b74` |
| `follower_stopper` | `--viz-series-1` | `#2a78d6` | `#3987e5` |
| `pi_saturation` | `--viz-series-2` | `#eb6834` | `#d95926` |
| `jad` | `--viz-series-3` | `#1baf7a` | `#199e70` |
| `vsl` / infrastructure | `--viz-series-4` | `#eda100` | `#c98500` |
| `pi_meanfrac` (superseded; always labelled so) | `--viz-series-5` | `#e87ba4` | `#d55181` |

Validator results:

* Light, surface `#ffffff`: worst adjacent CVD ΔE 9.1 (protan) and normal-vision 19.6,
  both PASS. Contrast WARN: aqua 2.82, yellow 2.17 and magenta 2.69 are under 3:1. Relief
  is mandatory: a legend always, direct labels, and the table twin.
* Dark, surface `#191918`: worst CVD ΔE 8.4 and normal 19.3, all PASS, and every series
  is at or above 3:1.
* Scatter, small multiples and any all-pairs form carry **at most three series**.
  Facet beyond that.

Penetration or compliance as series are **ordinal**, never categorical. Use
`--viz-ord-1…4`, validated `--ordinal`: light passes with a 2.11:1 light end, and dark
passes with 3.26:1. The report figures' categorical compliance colors stay as they are
for now (follow-up in §14).

### 7.6 CI and uncertainty styling

* **Line or point charts** (future σ_v-vs-penetration and similar).
  * The CI band is the series color at `--viz-ci-opacity` (14 % light, 22 % dark), with
    no stroke. The mean line is 2 px, round caps and joins.
  * Point markers are 8 px with a 2 px `--viz-surface` ring. Points with n < 20 are
    **hollow** (ring only), and the legend says "hollow = n < 20 (underpowered)".
  * Gridlines are 1 px solid `--viz-grid`, horizontal only.
  * There is never a dual y-axis.
* **Metric tile dot strip** (§9.12). The 28 px-tall SVG holds:
  * a CI band rect: series-1 at `--viz-ci-opacity`, full height;
  * a mean tick: 2 × 16 px, `--text-primary`;
  * one 8 px dot per replicate in `--viz-series-1` at 0.75 opacity with a 1.5 px
    `--viz-surface` ring, with deterministic vertical jitter (`(i % 3 - 1) × 5 px`).
  * The x-domain is `[min(values, lo95), max(values, hi95)]` padded 10 %.
  * Hovering or focusing a dot shows a tooltip: `seed 2003 · 17.42 m/s` (seeds come
    from `RunMetrics.replicates[].seed`).

### 7.7 Shared interaction rules

* One inspect behavior everywhere: hover **or** keyboard focus shows the same readout or
  tooltip. A tooltip never gates a value, because the readout row, the table or the CSV
  carries it too.
* Hit targets are at least 24 px. A dot's target includes its ring.
* On refetch, keep the previous render (no skeleton flash). Skeletons are for first load
  only.

---

## 8. Iconography

Vendor about 26 icons from **Lucide** (ISC licence) into `src/components/icons.tsx`, one
named component each, with the ISC notice in the file header. **Do not add
`lucide-react`**: this avoids a dependency, lockfile churn and this machine's npm-cache
problem. Spec:

```tsx
// 24×24 grid, stroke icons, sized by prop; decorative unless labelled by the parent.
<svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
     strokeWidth={1.75} strokeLinecap="round" strokeLinejoin="round"
     aria-hidden="true" focusable="false">…paths copied from lucide.dev…</svg>
```

The set (current Lucide names): `map-pin` (Onboard), `layers` (Scenarios), `activity`
(Runs), `grid-3x3` (Sweeps), `file-text` (Reports), `circle-play` (First run), `settings`,
`sun`, `moon`, `monitor`, `menu`, `panel-left`, `chevron-right`, `chevron-down`, `check`,
`x`, `circle-dashed` (queued), `circle-minus` (blocked), `triangle-alert` (warning),
`circle-alert` (danger), `info`, `download`, `upload`, `copy`, `external-link`,
`loader-circle`, `refresh-cw`, `search`.

Sizes: 16 px default, 14 px in sm buttons, pills and tables, 20 px in empty states. The
icon color is `currentColor`. Icons never carry meaning alone; there is always a text
label or an `aria-label`.

---

## 9. Component specs

Class names marked **(kept)** are existing names that code or tests depend on. Restyle
them; don't rename them. New classes are listed as new.

### 9.1 Buttons: `.btn` (kept) + modifiers

| Variant | Class | Rest | Hover | Active | Notes |
|---|---|---|---|---|---|
| Primary | `.btn.primary` (kept) | bg `--accent-solid`, text `--text-on-accent`, no border | bg `--accent-solid-hover` | bg `--accent-solid-active` | At most one per region |
| Secondary (default) | `.btn` | bg `--bg-surface`, 1 px `--border-strong`, text `--text-primary` | bg `--bg-hover` | bg `--bg-active` | |
| Ghost | `.btn.ghost` (new) | transparent, text `--text-secondary` | bg `--bg-hover`, text primary | bg `--bg-active` | Toolbars, table row actions, icon buttons |
| Danger | `.btn.danger` (new) | bg `--danger-solid`, white (5.21:1) | darken 6 % via `filter: brightness(0.94)` | | Reserved; nothing destructive exists today |
| Link | `.btn.link` (new) | text `--accent-text`, no padding | underline | | Inline actions |

* **Sizes.** sm `.btn.sm` (kept): 28 px, padding `0 10px`, 12/16 500, icon 14. md
  (default): 32 px, padding `0 12px`, 13/20 500, icon 16. lg `.btn.lg`: 36 px, padding
  `0 16px`, 14/20 500. Gap 6, radius `--radius-sm`. Icon-only buttons are square at the
  same heights and **must** have an `aria-label`.
* **Transition.** `background-color, border-color, color` at `--duration-1` with
  `--ease-standard`.
* **Focus.** The global ring (§11.2).
* **Disabled.** `opacity: 0.5; cursor: not-allowed`, with no hover change. When the
  reason is visible on screen, link it with `aria-describedby`, as the Onboard button
  already does. Keep the existing `title` reasons.
* **Busy.** A `loader-circle` spinner (rotating 0.8 s linear; static with reduced
  motion) replaces the leading icon. The label stays and `aria-busy="true"` is set.
* **Remove the `<label>&nbsp;</label>` spacer hacks.** Buttons sit in `.form-actions`
  (§9.2).
* **Order in footers and dialogs.** Cancel on the left, primary rightmost.

### 9.2 Inputs, selects, fields

* **`.field`** (kept): a vertical stack with gap 6. The label is a `<label>` at 12/16 500
  `--text-primary`. Under the control, with gap 4, is help text at 12/16 secondary or the
  problem text.
* **`.input`** (kept): 32 px tall, padding `0 10px`, 13 px Inter (or mono with `.mono`
  for IDs and paths), bg `--bg-input`, 1 px `--border-input`, radius `--radius-sm`,
  `font-variant-numeric: tabular-nums`. Hover: border `--text-secondary`. Focus: the
  global ring with offset 1 px. Placeholder: `--text-secondary`, since a placeholder must
  meet AA too. Disabled: bg `--bg-subtle`, text `--text-disabled`.
* **Select.** Native, `appearance: none`, chevron `background-image: var(--icon-chevron)`
  at `right 10px center`, padding-right 28.
* **Problem state.** Set `aria-invalid="true"` and border `--warning-text`, with the hint
  linked by `aria-describedby`. The hint element keeps `class="hint-amber"` (kept) and
  gets a 14 px `triangle-alert` icon before it.
* **Onboard only.** Problems on untouched fields render in a neutral `field-hint` style
  (secondary text with a `circle-dashed` icon). After the field's first blur they render
  as `hint-amber`. The text is identical, so tests that read it still pass.
* **Width classes** replace inline widths: `.w-xs` 80 px, `.w-sm` 120 px, `.w-md` 200 px,
  `.w-lg` 320 px, `.w-full`.
* **Number inputs** are left-aligned with tabular numbers.
* **File input.** Style `::file-selector-button` as a secondary sm button.
* **Range.** Track 4 px, `--gray-5` light and `--gray-6` dark. Thumb 16 px
  `--accent-solid` with a 2 px `--bg-surface` ring. The value readout to the right is
  mono 12 px primary, min-width 44, right-aligned.
* **Checkbox and radio.** Native, 16 px, `accent-color: var(--accent-solid)`.
* **`.chip-toggle`** (new), for the multi-select sets in Sweeps. It is a `<label>` that
  wraps a visually hidden (not `display:none`) checkbox plus text, so the existing labels
  and tests keep working.
  * Size: 28 px tall, padding `0 10px`, radius `--radius-sm`, 13 px mono for
    percentages.
  * Off: 1 px `--border-strong`, text secondary.
  * On (`:has(input:checked)`): bg `--accent-subtle`, border `--accent-border`, text
    `--accent-text`, leading 14 px check icon.
  * Focus: `:has(input:focus-visible)` shows the ring.
* **`.form-grid`** (new): `display: grid; grid-template-columns: repeat(auto-fill,
  minmax(180px, 1fr)); gap: 16px 24px; align-items: start`.
* **`.form-section`** (new): an `h3` panel title (14/20 600) above a `.form-grid`, with
  24 px between sections.
* **`.form-actions`** (new): flex, `justify-content: space-between; align-items: center;
  gap: 16px`, border-top `--border-subtle`, padding-top 16. The summary text (cost,
  reason) is on the left at 13 px secondary, with mono for numbers. Buttons are on the
  right.

### 9.3 Tables: `table.data` (kept)

| Property | Spec |
|---|---|
| Container | `.table-wrap` (kept): `overflow-x: auto`. Lists that grow (runs, reports, corridors, report run picker) add `.scroll-y`: `max-height: min(70vh, 720px); overflow: auto`, so the sticky header works inside it |
| Header | `th`: 12/16 500 `--text-secondary`, sentence case, **no transform**, bg `--bg-subtle`, padding `8px 12px`, bottom border `--border-default`, `position: sticky; top: 0; z-index: var(--z-sticky)` |
| Rows | Min height `--row-h` 36 (padding `8px 12px`, line 20). `.data.compact` gives 28 (padding `4px 8px`) for diagnostic tables (merge, weave, split audit) |
| Dividers | 1 px `--border-subtle` between rows; none after the last |
| Zebra | Off. **On only** for tables with ≥ 10 columns via `.data.zebra` (`tbody tr:nth-child(even) { background: var(--bg-subtle) }`). Today that is the weaving-sections table |
| Text cells | 13 px Inter primary; secondary for context columns (scenario name) |
| Numeric cells | `td.num, th.num { text-align: right }`, 12 px mono, tabular. Add `num` to every numeric column (merge diagnostics, weave exits, ramp demand, stations placed, split audit `x`) |
| ID cells | 12 px mono primary 500 (run id, report id); hashes 12 px mono secondary, truncated to the middle (`3f9a…c21e`) with a `CopyButton` on row hover/focus |
| Hover | `--bg-hover` |
| Selected | `--bg-active` (report run picker) |
| Clickable rows | The row's first cell holds a real `<a href>` (react-router `Link`) to the detail page. The whole row gets `cursor: pointer`, `onClick` navigates, and `tr:focus-within` shows `--bg-hover`. Keyboard users tab to the link. Never put `onClick` without a link |
| Wide tables | `.data.wide`: first column `position: sticky; left: 0; background: inherit; z-index: var(--z-sticky)` |
| Disabled rows | `tr.disabled` (kept): text `--text-disabled` and a visible reason (macro rows: a `MACRO SCREENING` badge plus the reason in the row's `title`). No opacity on the whole row |
| Empty | One `<tr><td colSpan>` containing `EmptyState compact` (§9.9) |
| Loading | 5 `SkeletonRows` on first load only |
| Caption | Keep the `aria-label`s (kept); see §10 for the list |

### 9.4 Panels and cards: `.panel` (kept)

* **`.panel`**: bg `--bg-surface`, 1 px `--border-default`, radius `--radius-md`, **no
  shadow**, `container-type: inline-size`.
* **`.panel-head`** (kept): min-height 48, padding `12px 16px`, border-bottom
  `--border-subtle`, flex with gap 12.
  * `.panel-title` (kept): 14/20 600 primary, sentence case, no uppercase, no tracking.
  * An optional `.panel-sub` (new) under the title: 12/16 secondary.
  * Right-side actions go after `.spacer` (kept).
* **`.panel-body`** (kept): padding 16. **`.panel-foot`** (new): padding `12px 16px`,
  border-top `--border-subtle`, bg `--bg-subtle`, used for action bars.
* **Nesting.** No panel inside a panel. Subsections inside a panel use an `h4` at 13/20
  600 and 16 px spacing.
* **Scenario card** (`.scen-card`, kept): a panel with padding 16 and gap 12.
  * Thumbnail well: 96 px tall, bg `--bg-subtle`, radius `--radius-sm`. The SVG
    strokes and fills use `var(--border-strong)`, `var(--text-secondary)` and
    `var(--accent-solid)`, with no hex.
  * Name: mono 14/20 500. Badges go on the right of the name row.
  * Facts: a two-column definition list in 12 px (label secondary, value mono primary).
  * Hash row, then actions.
  * Hover: border `--border-strong`.

### 9.5 Tabs and segmented control

* **Segmented control** (`.seg`, kept; the heatmap field toggle).
  * Keep `role="tablist"`, `role="tab"` and `aria-selected`, and keep the tab text
    `SPEED` / `DENSITY` (test anchor).
  * The container is 28 px tall with 2 px padding, bg `--bg-subtle`, 1 px
    `--border-subtle` and radius `--radius-sm`.
  * Each tab is 24 px tall, padding `0 10px`, 12/16 500, text secondary. The selected tab
    gets bg `--bg-surface`, text primary and `--shadow-sm`. Transition `--duration-2`.
  * Add arrow-key roving focus: Left and Right move and select.
* **Page tabs** (new `.tabs`; for future use, e.g. Run detail sections). These are 40 px
  tall underline tabs at 13/20 500, secondary, active primary with a 2 px
  `--accent-solid` bottom indicator. The bottom border of the strip is
  `--border-subtle`.

### 9.6 Badges: `.tag` (kept), the provenance and verdict vocabulary

Badges are 20 px tall, padding `0 6px`, radius `--radius-xs`, 11/16 500, tracking caps,
`white-space: nowrap`, 1 px border. Text stays as authored (uppercase in the source).
Tone recipe: `background: var(--{tone}-subtle); color: var(--{tone}-text);
border-color: var(--{tone}-border)`. The neutral tone uses `--bg-hover` /
`--text-secondary` / `--border-default`. The outline variant keeps a transparent
background.

| Class (kept) | Text | Tone |
|---|---|---|
| `.tag.seeded` | SEEDED | warning |
| `.tag.micro` | MICRO | neutral |
| `.tag.macro` | MACRO SCREENING | warning, outline |
| `.tag.demo` | DEMO, DEMO DATA, LOCAL | warning, **dashed** border (reads "not from this server") |
| `.tag.preset` | PRESET | neutral, outline |
| `.tag.underpowered` | UNDERPOWERED | warning (a caution, not a failure) |
| `.tag.noobs` | NO DATA | neutral |
| `.tag.server` | SERVER | success |
| `.tag.verdict-ok` | OK, ok | success |
| `.tag.verdict-defect` | WRONG SIDE, ADDED LANE, WRONG SIDE, THROUGH LANE EXIT-ONLY, above n % | danger |
| `.tag.verdict-unknown` | UNKNOWN, no vehicles planned | neutral |

### 9.7 Status pills: `.chip` (kept) + `queued | running | done | failed | blocked`

* Pills are 20 px tall, padding `0 8px 0 6px`, radius full, 12/16 500, gap 4, with a
  12 px leading icon. The text is the **verbatim API word, lowercase** (`done`, not
  "Succeeded"). This is principle 3, and tests read `done` / `queued` / `failed` /
  `running`.

| State | Background / border / text | Icon |
|---|---|---|
| `queued` | `--bg-hover` / `--border-default` / `--text-secondary` | `circle-dashed` |
| `running` (and guided "current") | `--accent-subtle` / `--accent-border` / `--accent-text` | 6 px dot, pulse opacity 1↔0.4 over 1.6 s (static with reduced motion) |
| `done` | `--success-subtle` / `--success-border` / `--success-text` | `check` |
| `failed` | `--danger-subtle` / `--danger-border` / `--danger-text` | `x` |
| `blocked` (guided) | `--bg-hover` / `--border-default` / `--text-secondary` | `circle-minus` |

* Under a failed status, the reason (`.fail-reason`, kept) is 12/16 `--danger-text`,
  clamped to 2 lines in tables (`-webkit-line-clamp: 2`), with the full text in `title`.
  In Run detail it is a full danger callout with a mono `pre`.
* **ProgressBar** (`.progress`, kept, **only on the bar element**; the runs test counts
  it). The bar is 6 px, radius full, track `--progress-track`, fill
  `--progress-running` / `--progress-done` / `--progress-failed`, width transition
  `--duration-progress` `--ease-standard`. Add `role="progressbar"`, `aria-valuemin=0`,
  `aria-valuemax={total}`, `aria-valuenow={done}` and
  `aria-label="Replicates complete"`. The count is mono 12 secondary, right-aligned,
  min-width 44.

### 9.8 Callouts: `components/ui/Callout.tsx` (new) + CSS-only `p.hint-amber` and `.fail-reason`

```ts
export function Callout(props: {
  tone: 'info' | 'success' | 'warning' | 'danger' | 'neutral';
  title?: ReactNode;
  children: ReactNode;
  action?: ReactNode;          // a sm button, right-aligned
  role?: 'note' | 'status';    // never 'alert': role=alert is reserved for the auth banner
}): JSX.Element;
```

* Bg `--{tone}-subtle`, 1 px `--{tone}-border`, radius `--radius-md`, padding
  `12px 14px`.
* A 16 px icon in `--{tone}-text`: info `info`, success `check`, warning
  `triangle-alert`, danger `circle-alert`, neutral `info`.
* The title is 13/20 600 primary. The body is 13/20 primary, with mono for verbatim
  server text.
* **CSS-only upgrade for existing markup.** `p.hint-amber` and `div.hint-amber` become
  compact warning callouts (`display: flex; gap: 8px`, with a `::before` icon drawn by
  `mask-image` of the triangle SVG and `background: currentColor`). `span.hint-amber`
  and `td.hint-amber` stay inline: `color: var(--warning-text); font-weight: 500`, and a
  table cell also gets `background: var(--warning-subtle)` as the second channel. This
  restyles every existing warning without touching its markup. The macro sweep banner's
  `<p>` stays, because the test reads `closest('p')`. `pre.fail-reason` /
  `p.fail-reason` become danger callouts the same way.

### 9.9 Empty states: `components/ui/EmptyState.tsx` (new)

```ts
export function EmptyState(props: {
  title: string;               // "No runs yet." (states what is missing; ends with a period)
  description?: ReactNode;     // < 14 words, when or how data appears
  action?: ReactNode;          // echoes the title: "No reports yet." → "Generate report"
  icon?: ReactNode;            // 20px, optional
  compact?: boolean;           // inside tables: 16px padding, no dashed border
}): JSX.Element;
```

The default is centered with padding 32, a 1 px **dashed** `--border-default`, radius
`--radius-md` and gap 4. The title is 13/20 500 primary (not bold) and the description
12/16 secondary (Stripe pattern). A filtered-to-zero list says "No runs match these
filters." and offers "Clear filters", never "create your first".

### 9.10 Error states

* **Connectivity and auth** are shell banners. Views do not repeat them (as today).
* **A failed fetch that the view depends on**, such as the heatmap or metrics in Run
  detail, a sweep `getSweep`, or a `POST` refusal in Reports, shows a **persistent
  danger Callout inside the affected panel**. It uses the server's words, an HTTP status
  prefix when there is one (`HTTP 422 — …`), and a `Retry` (refresh-cw) action. The
  toast stays as the transient echo. A failure must never leave a "loading…" placeholder.
* **Write refusals** stay inline next to the control that triggered them, as the Reports
  `launchError` already does.

### 9.11 Skeletons: `components/ui/Skeleton.tsx` (new)

```ts
export function Skeleton(props: { width?: number | string; height?: number | string; radius?: 'xs' | 'sm' | 'md' }): JSX.Element;
export function SkeletonRows(props: { rows: number; columns: number }): JSX.Element; // <tr>s
```

* Block bg `--bg-hover`, with an opacity pulse 1 ↔ 0.6 over 1.4 s (static with reduced
  motion). `aria-hidden="true"`. The surrounding region gets `aria-busy="true"`.
* Use it on first load only. Show 5 rows for the runs, reports and corridor tables,
  3 card skeletons for Scenarios, 8 tile skeletons for metrics, and the heatmap frame at
  its final height.

### 9.12 Metric tile (`components/metrics.tsx`: `MetricTile`, replaces `MetricCard` + `CIBar` + `StripChart`)

```
┌──────────────────────────────────────┐
│ THROUGHPUT                [UNDERPOWERED]│  eyebrow 11/16 500 caps secondary; badge right
│ 1842 ± 31  veh/h                      │  value Inter 24/32 600 (own element) · "± 31" 16/24 500 secondary · unit 13 secondary
│ 95% CI 1811 – 1873 · n=20             │  mono 12/16 secondary (one element; exact format)
│ · ··:·|:·· ·   [CI band]              │  dot strip 28px (§7.6)
└──────────────────────────────────────┘
```

* Bg `--bg-surface`, 1 px `--border-default`, radius `--radius-md`, padding 16, gap 8.
  The grid is `repeat(auto-fill, minmax(200px, 1fr))` with gap 12.
* **Exact text contract (test anchors).**
  * The mean is rendered **in its own element** whose text is exactly
    `formatNumber(mean, digits)`, for example `17.6`.
  * `± {hw}` and the unit are sibling elements.
  * The CI line is one element with the text `95% CI {lo} – {hi} · n={n}`. Note the
    spaces around the en dash.
  * The no-observation tile keeps `NO DATA`, `no observations` and
    `no replicate produced a value · n=0`.
* **Symmetry rule for "±".** Let `hw = (hi − lo)/2`. Show `± formatNumber(hw, digits)`
  only if `|(hi − mean) − (mean − lo)| ≤ 0.5 × 10^(−digits)`. Otherwise omit "±" and let
  the CI line carry the interval. Never print a symmetric "±" for an asymmetric
  interval.
* **Grouping.** Add `group: 'flow' | 'stability' | 'energy' | 'exposure'` to `MetricDef`.
  The keys are unchanged, so the contract test still passes. Render four sub-sections in
  this order, each with an `h4`:
  * **Traffic flow:** throughput, mean TT, p90 TT.
  * **Stability:** σ_v spatial, σ_v temporal, wave count, wave amplitude, wave speed.
  * **Energy:** fuel.
  * **Exposure and sample:** VMT, VHT, travel-time sample.

  Unknown keys go in a final "Other" group.
* The separate "Per-replicate distribution" grid is **removed**. Each tile carries its
  own dot strip, and metrics with no observations have no strip.

### 9.13 PASS/FAIL criteria rows: `components/CriteriaTable.tsx` (spec only; build when the API exposes criteria)

`ReportOut` carries **no criteria results today**. The dashboard must **not compute
verdicts on the client** (`CLAUDE.md` §7.4). Build this when the API adds them.

| Criterion | Threshold | Measured | Compared | Verdict |
|---|---|---|---|---|
| Link flows (GEH) | GEH < 5 on ≥ 85 % of link-hours | 91.2 % | 48 link-hours | ✓ PASS |
| Segment speeds | RMSPE ≤ 15 % | 18.4 % | 312 speed cells | ✕ FAIL |
| Wave speed | 14–22 km/h, unseeded | — | — | – NOT EVALUATED |

* A `table.data` where threshold and measured values are mono and right-aligned.
* The verdict badge is 20 px. PASS is success with a check, FAIL is danger with an x,
  and NOT EVALUATED is neutral with a minus. **PASS and FAIL have identical size and
  weight.** No row backgrounds. The failing value's own cell is not colored either; the
  badge carries the verdict.
* The footer line is 12 px secondary: `Profile: {name} · Source: {profile.source}`.
  Thresholds come from the server's profile registry; the dashboard never restates them
  from its own copy.

### 9.14 Toasts (`toast.tsx`)

* **Position:** bottom-right, inset 16, width 360, stacked upward with gap 8, max 4 (kept).
* **Toast:** bg `--bg-raised`, 1 px `--border-default`, `--shadow-md`, radius
  `--radius-md`, padding `12px 14px`.
  * A 16 px leading icon in the tone's text color: info `info`, ok `check`, error
    `circle-alert`.
  * Message 13/20 primary.
  * The dismiss button is a 24 px ghost icon with the existing `aria-label`. **Remove the
    colored left stripe.**
* **Enter:** opacity 0→1 and translateY 8 px→0 over `--duration-3` with `--ease-enter`.
* **Timing and semantics are kept:** 5.2 s, errors 15 s, and the container keeps
  `role="status" aria-live="polite"`.
* Toasts echo; they never hold information the screen doesn't also hold (§9.10).

### 9.15 Dialogs (`ConfirmDialog.tsx`)

* **Box:** centered, `width: min(480px, calc(100vw − 32px))`, max-height
  `calc(100dvh − 64px)` scrolling, bg `--bg-raised`, 1 px `--border-default`, radius
  `--radius-lg`, `--shadow-lg`, padding `20px 24px`, gap 16.
  * Scrim: `--bg-overlay`, `--z-overlay`; the dialog sits at `--z-modal`.
  * Enter: opacity plus scale 0.98→1 over `--duration-4` with `--ease-enter`.
* **Title:** an `h2` at 16/24 600 primary. This replaces the mono-caps `h2`. Keep the
  `aria-label={title}` on the dialog.
* **Facts:** `dl.fact-list` (kept) in a well with bg `--bg-subtle`, radius
  `--radius-sm` and padding `8px 12px`. Each row has `dt` at 13 secondary on the left
  and `dd` in mono 13 primary on the right, with 1 px `--border-subtle` dividers
  (replacing the dotted ones).
* **Footer:** right-aligned, `[Cancel]` then `[Confirm primary]`.
* **Behavior.**
  * Keep the existing mount-only initial focus on the confirm button and the Escape
    handling.
  * Add a **focus trap**: Tab and Shift+Tab cycle within the dialog. The trap is a
    `keydown` handler for Tab only. It must never move focus on render, because
    `confirmdialog.test.tsx` asserts focus is not stolen from a field being typed in.
    The Escape listener stays on `window`, mount-only, with the `cancelRef` pattern.
  * **Restore focus** to the opener on close.
  * Make the background `inert` while the dialog is open (set the `inert` attribute on
    `.app`).

### 9.16 Drawers (`SettingsDrawer.tsx`)

* **Box:** right side, width `min(400px, 100vw)`, full height, bg `--bg-raised`, border
  left `--border-default`, `--shadow-lg`, `--z-modal`. Enter: translateX 16 px→0 with
  opacity, over `--duration-4`.
* **Header:** title "Settings" (16/24 600) and an `x` close icon button.
* **Sections:**
  * **Connection:** the existing fields and copy.
  * **Appearance:** Theme as a 3-option segmented radio group (System / Light / Dark),
    applied immediately via `setThemePref` without waiting for Save.
* **Footer:** `[Cancel] [Save]` (Save is primary).
* **Behavior:** a focus trap, Escape closes, and focus is restored. `role="dialog"`
  `aria-label="Settings"` is kept.

### 9.17 Keyboard focus

See §11.2. Every interactive element shows the same ring, and nothing removes it without
replacing it.

### 9.18 Copy button and hash display: `components/ui/CopyButton.tsx` (new)

A ghost icon button, 24 px, with the `copy` icon. On click it writes to the clipboard
and swaps to a `check` icon for 1.2 s. It has `aria-label="Copy {label}"`, and a polite
live text "Copied" is announced. Hashes display truncated in the middle as `3f9a…c21e`
(first 4 and last 4). The full value goes in `title` and in the copy, never truncated.
The **demo** placeholder `— demo, no server hash —` is never truncated and never gets a
copy button.

### 9.19 Command palette (P2, optional; after QA)

⌘K or Ctrl K opens a dialog (`aria-label="Command menu"`, `--z-modal`, 560 px wide,
`--radius-lg`, top-aligned at 15vh). It holds a 40 px search input (the ARIA
combobox pattern) and a listbox of commands:

* Go to Onboard, Scenarios, Runs, Sweeps, Reports, First run.
* Launch a run (focuses the Runs launcher).
* Theme: System, Light or Dark.
* Open settings.

The list is filtered by a case-insensitive substring match. Arrows move, Enter runs and
Esc closes. It needs no new dependency and no data fetching.

---

## 10. Per-view redesign

Common to every view:

* `PageHeader` with `documentTitle`.
* The loading → error → empty → content order.
* No inline `style={{…}}` except computed geometry (positions, widths of bars and
  tooltips).
* Every string listed under "Test anchors" stays byte-identical, inside its own
  element as today.

### 10.1 Scenarios (`views/ScenariosView.tsx`)

**Purpose:** pick or create the scenario a run starts from.

```
Scenarios  6 configs  [DEMO DATA]                                        [⤒ Upload YAML]
Presets and stored scenarios. Run one, or load it into the composer to make a variant.

(Guided first run panel: only when the server reports no runs, unchanged logic)

⚠ The API is unreachable, so these are built-in demo scenarios: … (p.hint-amber → callout)

┌──────────────────────┐ ┌──────────────────────┐ ┌──────────────────────┐
│ ┌──────────────────┐ │ │                      │ │                      │
│ │  ring schematic  │ │ │  corridor schematic  │ │   osm schematic      │
│ └──────────────────┘ │ │                      │ │                      │
│ ring_sugiyama PRESET │ │ corridor_10km PRESET │ │ i24_replica          │
│ Circumference 230 m  │ │ Length  10.0 km      │ │ …                    │
│ Vehicles      22     │ │ Lanes   1            │ │                      │
│ Duration 10 min      │ │ Duration 20 min      │ │                      │
│ Replicates 20        │ │ Replicates 20        │ │                      │
│ 3f9a…c21e ⧉          │ │ 7b1d…09ae ⧉          │ │                      │
│ [Run…] [Load in composer]                     │ │                      │
└──────────────────────┘ └──────────────────────┘ └──────────────────────┘
┌ ⤒ Drop a scenario YAML here — or click to browse (48px, dashed) ──────────┐

┌ Compose scenario ───────────── based on ring_sugiyama  [Start blank] ────────┐
│ Network                      Fleet & control                Simulation        │
│ Name [            ]          Fleet model [IDM ▾]            Duration (s) [ ]  │
│ Network kind [corridor ▾]    Controller [follower_stopper ▾]Replicates  [20]  │
│ Length (m) [10000]           AV penetration ━━●━━━  5%      ⚠ below n ≥ 20    │
│ Lanes [1]                    Compliance     ━━━━●━ 80%                        │
│                              ☑ gantry VSL segments                             │
│ ⓘ Not editable here — carried through from ring_sugiyama unchanged: seed, …   │
├────────────────────────────────────────────────────────────────────────────────┤
│ POST /scenarios · validated server-side · 20 × 20 min = 6.7 sim-hours  [Create scenario] │
└────────────────────────────────────────────────────────────────────────────────┘
```

* **Components:** PageHeader (meta: count and the DEMO DATA badge; action: an
  `Upload YAML` secondary button that clicks the same hidden file input), scenario cards
  (§9.4), a slim dropzone, and the composer panel with three `.form-section`s in a
  `.grid-12` (4/4/4 at ≥ 1280, stacked below). The passthrough notice becomes a neutral
  Callout and the "dropped" line a warning one. `.form-actions` goes in `.panel-foot`.
  The launch dialog is restyled per §9.15.
* **Key improvements:**
  1. The schematics are themed (no hex).
  2. Card facts become a definition list instead of run-on mono.
  3. The hash is truncated and copyable.
  4. The composer is grouped into Network / Fleet & control / Simulation.
  5. The cost line moves to the action bar.
  6. The library empty state is EmptyState "No scenarios yet." with "Upload a scenario
     YAML or compose one below."
  7. First load shows 3 card skeletons.
* **Test anchors:** `Run…` (also `Run…`), `Load in composer`, `Create scenario`,
  `Launch run`, dialog `Launch {name}`, labels `Name`, `Length (m)`, `Lanes`,
  `Duration (s)`, `Replicates`, `Seed`, the `— demo, no server hash —` text,
  `PRESET` / `DEMO`, and `/Dropped by the network-kind change/`.

### 10.2 Onboard corridor (`views/OnboardView.tsx`)

**Purpose:** turn a bounding box and a detector export into a scenario whose demand
traces to detectors. Onboarding is not validation.

```
Onboard a corridor   mndot_i94_wb_stpaul · network
Build a scenario from an OpenStreetMap extract and a detector export. Onboarding is not validation.

┌ New corridor ─────────────────────────────────────────────────────────────────┐
│ Corridor                                                                       │
│  Name [mndot_i94_wb_stpaul]  Bounding box (S, W, N, E) [44.94, -93.09, …]  Bearing (deg) [270] │
│ Boundary stations                                                              │
│  Upstream station [S1063]     Downstream station [S97]                         │
│ Detector data                                                                  │
│  Detector CSV [Choose file]   Stations CSV [Choose file]                       │
│ Time span                                                                      │
│  Window (s) [300]  Span start (local) [06:00]  Duration (s) [14400]  Warm-up (s) [1800] │
│ ▸ Advanced — detector column names, driver population, provenance, map fixes   │
├────────────────────────────────────────────────────────────────────────────────┤
│ Downloads the map extract, builds the network and derives the demand.  [Onboard corridor] │
└────────────────────────────────────────────────────────────────────────────────┘
┌ Progress   ● running   network · 1/5 stages                          cor_ab12 ┐
│  ✓ extract ──── ● network ──── ○ observations ──── ○ demand ──── ○ install     │
│  (failed: danger callout "Onboarding failed at the network stage. Nothing was installed." + pre) │
└────────────────────────────────────────────────────────────────────────────────┘
┌ What the onboarding found                                config_hash 3f9a…c21e ⧉ ┐
│ The corridor follows a 11.8 km chain of 42 map edges. 9 interchange ramps …       │
│ ┌ Network (col 6) ───────────────┐ ┌ Demand (col 6) ─────────────────────────┐   │
│ │ Lane profile  (fact list)      │ │ Ramp demand (table.data.compact, num)   │   │
│ │ Stations placed (table)        │ │ residual / zeroed / unmatched callouts  │   │
│ │ lanes vs inventory + callouts  │ │                                          │   │
│ └────────────────────────────────┘ └──────────────────────────────────────────┘   │
│ applied: …  · before fixes: 2 defects                                             │
│ Split audit (table.data.compact) + remedy rows                                    │
│ ▸ Plain-text summary                                                              │
├───────────────────────────────────────────────────────────────────────────────────┤
│ Onboarding is not validation — 20 seeds, then a report …   [Report against observations] [Run 20 seeds] │
└───────────────────────────────────────────────────────────────────────────────────┘
┌ Corridors on this server ───────────────────── GET /corridors, newest first ┐
│ Corridor            Status     Started (UTC)          [Use]                 │
└──────────────────────────────────────────────────────────────────────────────┘
```

* **Order change:** form → Progress → Summary → history. Today history sits between the
  form and the progress the user is waiting on.
* **Progress stepper (new, P1).** The stages are a constant list `['extract', 'network',
  'observations', 'demand', 'install']` (the documented order in `api/types.ts`).
  * Each stage is a 20 px circle and a 12 px label, joined by 1 px `--border-default`
    connectors.
  * Done: `--success-text` with a check. Current: accent with a pulsing dot. Pending:
    secondary hollow. Failed stage: danger with an x.
  * The existing `{stage} · {completed}/{total} stages` text stays in the panel head
    (test anchor).
  * If the API reports a stage name outside the list, fall back to the text alone.
    Never invent progress.
* **Field problems:** neutral until the field is touched, warning after (§9.2). Add
  `aria-invalid` and `aria-describedby`.
* **Advanced** is a styled `<details>`: 13 px 500 summary with a chevron that rotates
  90° (instant with reduced motion). The content is in the DOM as today.
* Rename the table headers to sentence case: `Corridor`, `Status`, `Started`,
  `Station`, `Position`, `Offset from centreline`, `Ramp`, `Position`, `Peak`, `Source`.
  Numeric columns get `.num`. The started cell keeps its exact text
  (`2026-09-23 06:00:00 UTC`, a test anchor), now in mono.
* **Empty and loading states:**
  * History empty: "No corridors onboarded yet." with "Corridors appear here after an
    onboarding finishes."
  * History loading: 3 skeleton rows.
  * When the service has no `GET /corridors`: a neutral Callout with the existing text.
* **Test anchors:**
  * Labels: `Name`, `Bounding box (S, W, N, E)`, `Bearing (deg)`, `Upstream station`,
    `Downstream station`, `Detector CSV`, `Stations CSV`, `Window (s)`, `Duration (s)`,
    `Warm-up (s)`, `Timestamp column`, `Flow column`, `Speed column`,
    `IDM calibration (server path)`, `Source`, and both checkbox labels.
  * Buttons: `Onboard corridor`, `Run 20 seeds`, `Report against observations`, and
    the `use {name} ({status})` aria-labels.
  * Every problem string; `/network · 1\/5 stages/`, `/11.8 km chain of/`,
    `/4275 veh\/h/`; `before fixes: N defects`; `/^applied:/`; `/^remedy:/`;
    `/Zeroed: …/`; `/sits … m from the centreline/`; `/lanes vs inventory: …/`.
  * Table aria-labels `stations placed`, `ramp demand`, `split audit`,
    `corridors on this server`.

### 10.3 Runs (`views/RunsView.tsx`)

**Purpose:** launch a scenario and follow its replicates.

```
Runs  38 runs · ● Live                                                    
Launch a scenario and follow its replicates. Every run records its seeds and config hash.

⚠ The API is unreachable, so these are built-in demo runs: …   (demo only)

┌ Launch a run ────────────────────────────────────────────────────────────────────┐
│ Scenario [ring_sugiyama (preset) ▾]  Tier [micro (SUMO) ▾]  Duration (s) [1200]  Seed [42]  Replicates [20] │
│                                                                   ⚠ below reporting standard n ≥ 20        │
├──────────────────────────────────────────────────────────────────────────────────┤
│ 20 × 20 min = 6.7 sim-hours                                         [Launch run]  │
└──────────────────────────────────────────────────────────────────────────────────┘
┌ (table.data, .scroll-y, sticky header) ──────────────────────────────────────────┐
│ Run          Scenario        Tier    Status       Progress            Config        Labels │
│ run-a41d09   corridor_10km   MICRO   ✓ done       ━━━━━━━━━━ 20/20    3f9a…c21e ⧉   SEEDED │
│ run-e2190c   i24_replica     MICRO   ✕ failed     ━━         0/2      91c0…77ab ⧉           │
│              ValueError: Duration 1800 s leaves no measurement window … (2-line clamp)       │
│ run-77b310   ring_sugiyama   MACRO SCREENING  ● running  ━━━━━   8/20  …                     │
└──────────────────────────────────────────────────────────────────────────────────┘
```

* **Header meta:** the count in mono and a "Live" pill (6 px `--success-solid` dot plus
  "Live", `title="Polling every 2 s"`). When the key is rejected it reads "Paused — API
  key rejected" in warning text.
* **Launcher:** a `.form-grid` and `.panel-foot` with `.form-actions` (cost on the left,
  `Launch run` on the right). The `&nbsp;` labels go. `warmupBlock` shows as a warning
  hint under Duration, plus the reason in the action bar. The confirm dialog is
  unchanged in behavior.
* **Table:**
  * The run id is a `Link` (mono 12/500).
  * Scenario is secondary text with the id in `title`.
  * Progress is the §9.7 bar. Demo rows keep the plain `n/m — demo` text and no `.progress`.
  * Config is the truncated hash with a copy button.
  * Clickable rows follow §9.3.
* **P2: filter chips** above the table: `All 38`, `Running 2`, `Done 30`, `Failed 6`.
  They are client-side, with the count in mono, and use `.chip-toggle` single-select
  semantics (`aria-pressed`).
* **States:**
  * First load: 5 skeleton rows.
  * Empty: EmptyState compact "No runs yet." with "Runs appear here after you launch
    one above."
  * Poll errors after the first answer replace the Live pill with a warning pill
    "Stale — last update hh:mm:ss" and the server's error; the table keeps its last rows
    (2026-10-07 review: a Live pill over frozen data was misleading).
* **Test anchors:**
  * The `runs` table label.
  * Buttons `Launch run`, `Launch`; dialog `Launch this run?`.
  * Labels `Duration (s)`, `Seed`, `Replicates`.
  * `.progress` absent on demo rows.
  * `/Duration 1800 s leaves no measurement window/`, `— demo, no server hash —`,
    `DEMO`, `queued`.

### 10.4 Run detail (`views/RunDetailView.tsx`), the story view of one run

**Purpose:** answer "what happened in this run, and how sure are we?" in reading order:
provenance → field → metrics → demand integrity → diagnostics.

```
Runs / run-a41d09                                               (breadcrumb, topbar)

run-a41d09  ✓ done  MICRO  SEEDED                                    [⧉ Copy config hash]
Scenario corridor_10km · Config 3f9a…c21e ⧉ · Seeds 20 ▸ · FD v1_legacy preset (uncalibrated)  ← macro only, warning text

(not finished)  ┌ Computing replicates ───────────────────────────────────────┐
                │ ━━━━━━━━━━━━━━━  12/20   Heatmap and metrics appear when …   │
                └──────────────────────────────────────────────────────────────┘
(failed)        ┌ Run failed ──────────────────────────────────────────────────┐
                │ ⛔ No replicate produced results. The service reported:      │
                │    ValueError: …  (mono pre)                                 │
                └──────────────────────────────────────────────────────────────┘
┌ Space–time field ── [SPEED | DENSITY] ─────────────────────── [⤓ Download CSV] ┐
│ (heatmap per §7.3)                                                              │
└─────────────────────────────────────────────────────────────────────────────────┘
┌ Metrics · mean ± 95% CI over 20 replicates ─────────────────────────────────────┐
│ Traffic flow                                                                     │
│ [THROUGHPUT] [MEAN TRAVEL TIME] [P90 TRAVEL TIME]                                 │
│ Stability                                                                         │
│ [σ_v SPATIAL] [σ_v TEMPORAL] [WAVE COUNT] [WAVE AMPLITUDE] [WAVE SPEED]           │
│ Energy                     Exposure and sample                                    │
│ [FUEL]                     [VMT] [VHT] [TRAVEL-TIME SAMPLE]                       │
└──────────────────────────────────────────────────────────────────────────────────┘
┌ Demand integrity · 20 replicates ──────────────┐   (InsertionPanel, restyled)
┌ Merge diagnostics · seed 2000 ─────────────────┐   (tables .compact; weave table .zebra .wide)
```

* **Header.** This replaces the `← runs` link and the `kv` soup.
  * Line 1: `h1` with the run id (mono 20/28 500), then status pill and badges.
  * Line 2: a horizontal definition list at 13 px: `Scenario`, `Config` (hash with copy;
    demo placeholder untruncated), `Seeds` (the existing disclosure button, now
    `Seeds 20 ▸` ghost sm, toggling the seed panel), and for macro runs `FD` with the
    existing titles and the warning text when the source is the preset or unknown.
* **Field panel per §7.3.** Fetch errors become a persistent danger Callout with Retry.
  This fixes the eternal "loading field…".
* **Metrics panel per §9.12.** Remove the per-replicate grid; the dot strips live in the
  tiles. On metrics fetch error, show a Callout with Retry.
* **InsertionPanel and MergeDiagnostics.**
  * Their `panel-title` sub-labels (`Insertion`, `Weave exits`, `Ramp meters`,
    `Weaving sections`) become `h4`s at 13/20 600.
  * Prose stays 13 px secondary.
  * Numeric columns get `.num`. `.hint-amber` cells get the warning tint (§9.8).
  * The weaving-sections table (16 columns) gets `.data.compact.zebra.wide`. Its long
    headers wrap (`white-space: normal; min-width: 72px`) and keep their `title`
    explanations.
* **P2:** a `Report on this run →` secondary button, shown only for `done` + micro. It
  navigates to `/reports?select={run_id}`, and ReportsView preselects that run if it is
  listed.
* **Test anchors:**
  * Run id text; `SEEDED`, `MICRO`, `MACRO SCREENING`, `DEMO`;
    `— demo, no server hash —`; `/v1_legacy preset/` containing `(uncalibrated)`.
  * Tabs `SPEED` / `DENSITY`. Headings `Run failed`, `Merge diagnostics · seed {n}`,
    `Demand integrity · {n} replicates`.
  * Metric labels; `/95% CI/`; the exact mean text; `UNDERPOWERED`, `NO DATA`,
    `no observations`, `no replicate produced a value · n=0`.
  * Labels `ramp meters`, `weaving sections`, `weave exits`, `insertion verdict`,
    `weave exits verdict`.
  * Test ids `insertion-panel`, `insertion-summary`, `merge-diagnostics`,
    `weave-exit-flag`.
  * Header texts `Passed unstoppable`, `Deferred (vehicle-steps)`; cell texts like
    `143 / 145`.

### 10.5 Sweeps (`views/SweepsView.tsx`)

**Purpose:** run a penetration × compliance (× strategy) grid and read each cell
against an uncontrolled baseline.

```
Sweeps
Penetration × compliance grids, each cell compared with an uncontrolled p=0 baseline.

┌ New sweep ───────────────────────────────────────────────────────────────────────┐
│ ⚠ Screening tier (CTM) — not a validation result · …   (macro only; the <p> stays) │
│ Scenario [corridor_10km ▾]  Controller [follower_stopper ▾]  Tier [micro (SUMO) ▾]  Replicates / cell [5] │
│                                                             ⚠ exploratory — headline numbers need n ≥ 20 │
│ Penetration set   (1%)(2%)(5%)(10%)(15%)(20%)( 30%)                                │
│ Compliance set    (25%)(50%)(80%)(100%)                                            │
│ Strategies        (none)(vsl)(alinea)(vsl+alinea)    ALINEA target [veh/km/lane] […] │
│ Reference         ☑ include p=0 baseline cell                                      │
├───────────────────────────────────────────────────────────────────────────────────┤
│ 24 cells + baseline = 25 cells × 5 reps = 125 runs        [Launch 24 cells + baseline…] │
└───────────────────────────────────────────────────────────────────────────────────┘
┌ swp-1234 · Δ vs baseline (p=0%, no controlled vehicles, σ_v SPATIAL 3.39 m/s)  Metric [σ_v SPATIAL ▾] ┐
│ worse ≥50 25–50 10–25 2–10 | ±2 | 2–10 10–25 25–50 ≥50 better   ≡ identical   n = replicates │
│                 25%       50%       80%       100%                                 │
│ 0% · baseline  [ 3.39 m/s   BASELINE · n=20 (dashed)                     ]         │
│ 1%             [ -3.1% ]  [ -5.2% ]  [ -8.0% ]  [ -9.9% ]                          │
│                [  n=5  ]  …                                                         │
│ 5% · vsl       …                                                                    │
│ ⚠ no p=0 baseline cell … / ≡ N cells share … / sweep fan-out failed … (callouts)  │
│ Blue = better, red = worse for this metric · select a cell to open its run          │
└───────────────────────────────────────────────────────────────────────────────────┘
```

* **Launcher.** The sets use `.chip-toggle` (§9.2) and the inner labels/inputs stay, so
  `getByLabelText('none')` and similar still work. The cost line and the `Launch …`
  button go in `.panel-foot`. The confirm dialog is unchanged in behavior.
* **Matrix.**
  * `border-collapse: separate; border-spacing: 2px` (the surface gap).
  * Row headers: mono 12 right-aligned, secondary. Column headers: mono 12 centered.
  * Each cell is a `td` (role cell kept) containing a `<button class="cell-btn">`,
    96 × 56, radius `--radius-xs`, colored by `deltaClass`.
    * Line 1: the signed delta in mono 13/600.
    * Line 2: `n=… · {strategy}` in mono 11 in the same ink (no opacity).
    * `≡` marks a twin.
    * The button's `aria-label` is
      `p={pen} c={com}: {delta} vs baseline, n={n}`. Enter or click opens the run.
  * **Hover and focus show the tooltip** (raised surface, `--shadow-md`, 12 px; mono
    numbers; CI line secondary; `--z-tooltip`), anchored to the focused cell when
    keyboard-driven.
  * Hover outline: 2 px `--text-primary` at offset −2.
  * Baseline cells: bg `--bg-subtle`, 1 px dashed `--border-strong`, absolute value.
  * Pending cells: bg `--bg-subtle`, the status word in secondary (`queued` /
    `running`), not clickable.
* **States.** While the sweep id is known but no cells have arrived, show a skeleton
  matrix (rows × cols of 96 × 56 blocks) instead of "collecting sweep cells…". A
  `getSweep` error becomes a danger Callout with Retry.
* **Test anchors:**
  * Dialog `Launch this sweep?`; buttons `/^Launch \d+ runs$/`, `/^Launch/`.
  * Labels `Metric`, `ALINEA target [veh/km/lane]`, `none` and the set labels.
  * `0% · baseline`, `-50.0%`, `BASELINE · n=…`.
  * The macro banner inside a `<p>`; `/Screening tier \(CTM\)/`;
    `/share an identical aggregate vector/`; `/vsl, alinea/`.
  * The cell and row roles.

### 10.6 Reports (`views/ReportsView.tsx`)

**Purpose:** turn finished micro runs into an FHWA-style report scored against a named
criteria profile and, optionally, an observations artifact.

```
Reports  12 reports
FHWA-style calibration and validation reports from finished micro runs.

┌ New report ────────────────────────────────────────────────────────────────────────┐
│ 1  Choose finished micro runs                                                       │
│ ┌ (table.data .scroll-y max 360px, sticky header) ─────────────────────────────────┐ │
│ │ ☐  Run         Scenario        Tier             Status   Config hash   Labels     │ │
│ │ ☑  run-a41d09  corridor_10km   MICRO            ✓ done   3f9a…c21e     SEEDED     │ │
│ │ ☐  run-d0417a  corridor_10km   MACRO SCREENING  ✓ done   …   (disabled: reason)  │ │
│ └───────────────────────────────────────────────────────────────────────────────────┘ │
│ 2  Score against                                                                    │
│ Criteria profile [fhwa_2004 (default) ▾]     Score against observations [none — criteria not evaluated ▾] │
│ Source: FHWA-HRT-04-040 §5.6 (from GET /criteria)   [server path input when chosen] │
├──────────────────────────────────────────────────────────────────────────────────────┤
│ 1 run selected · scored against none: link-flow and speed rows read "not evaluated"   [Generate report (1)] │
│ ⛔ HTTP 422 — observations_path is outside the allowed data roots   (persistent, inline) │
└──────────────────────────────────────────────────────────────────────────────────────┘
┌ Generated reports ──────────────────── server history (GET /reports), newest first ┐
│ Report  Source   Status   Criteria                      Created (UTC)        Runs        │
│ rpt-2   SERVER   ✓ done   fhwa_2004                     2026-10-07 14:02:11  run-a41d09  │
│                           observed: 48 link-hours, 312 speed cells compared             │
│                                                   [⤓ .md] [⤓ .zip (with figures)] [⤓ PDF] │
└──────────────────────────────────────────────────────────────────────────────────────┘
```

* **The controls move out of the crowded panel head** into a numbered two-step body: the
  run picker first, then profile and observations as a `.form-grid`. A
  `.panel-foot` action bar holds the selection summary and `Generate report (n)`.
* The profile's `source` text is shown visibly as 12 px secondary under the select.
  This is provenance, never thresholds.
* `launchError` becomes a danger Callout in the action bar. It is persistent, as today.
* **Download buttons:** ghost sm with a `download` icon. The visible text is `.md`,
  `.zip (with figures)` and `PDF`, and **`aria-label` keeps the full names**
  `Download .md`, `Download .zip (with figures)` and `Download PDF`. Each visible text
  is contained in its accessible name, which satisfies WCAG 2.5.3 Label in Name. They
  are disabled until done, with the existing `title` reasons.
* The `Created` cell is mono and keeps its exact text (`YYYY-MM-DD hh:mm:ss UTC`).
  The same format is a test anchor in Onboard, so keep it identical everywhere.
* **States:**
  * Picker empty: "No finished runs yet." with "Finished runs appear here; macro runs
    can't be reported."
  * Reports empty: the existing two messages through EmptyState compact.
  * First load: skeleton rows.
* **Test anchors:**
  * Tables `finished runs`, `generated reports`.
  * Buttons `Generate report (1)`, `Download .md`.
  * Labels `Criteria profile`, `Score against observations`,
    `Observations path on the server`, `select {run_id}` checkboxes.
  * `SERVER`, `LOCAL`, `DEMO`; `/^observed:/`; `done`.
  * Ids `rpt-1`, `rpt-2`; `/already exists/` toasts.

### 10.7 First run (`components/GuidedFirstRun.tsx`; route `/first-run`)

* A vertical stepper. `ol.guided-steps` / `li.guided-step.{done|current|blocked}` are
  **kept** (the tests read the class names).
  * The number circle is 24 px. Done is success with a check glyph *in addition to* the
    number text (keep the number text node). Current is accent. Blocked is neutral.
  * A 1 px `--border-default` connector runs from each circle to the next (CSS
    `::before` on `li`).
  * The current step gets bg `--accent-subtle`, radius `--radius-md` and padding 12, so
    the eye lands on it.
  * `g-why` reasons become compact warning text with an icon. `fail-reason` becomes a
    danger callout.
* The header counter `{x}/{n} done` stays as its own element (test anchor) in the panel
  head, now 12 px mono secondary.
* **Test anchors:** `0/6 done`, `6/6 done`, `Use ring_sugiyama`,
  `Launch 2-replicate smoke run`, `Generate report`, `Download report.md`, link
  `Open run detail`, `/smoke test, not a result/`, `/metrics read/`, the
  `OFFLINE_WRITE_MESSAGE` strings, and the list items.

---

## 11. Accessibility

### 11.1 Contrast (computed, WCAG 2.x relative luminance)

| Pair | Light | Dark |
|---|---|---|
| `--text-primary` on canvas / surface / hover / active | 15.48 / 16.30 / 14.32 / 13.31 | 16.26 / 15.14 / 13.71 / 12.38 |
| `--text-secondary` on canvas / surface / hover / active | 5.73 / 6.04 / 5.31 / 4.93 | 9.01 / 8.39 / 7.60 / 6.86 |
| `--accent-text` on canvas / surface / hover | 5.64 / 5.94 / 5.22 | 8.95 / 8.34 / 7.55 |
| `--success-text` on surface / canvas / hover | 5.75 / 5.46 / 5.05 | 9.38 / 10.07 / 8.49 |
| `--warning-text` on surface / canvas / hover | 5.66 / 5.37 / 4.97 | 11.49 / 12.34 / 10.40 |
| `--danger-text` on surface / canvas / hover | 5.91 / 5.61 / 5.19 | 8.35 / 8.97 / 7.56 |
| Tone text on its own subtle bg (pills, badges, callouts): success / warning / danger / accent / neutral | 5.13 / 5.44 / 5.15 / 5.33 / 5.31 | 7.86 / 10.26 / 7.75 / 6.99 / 6.86 |
| `--text-on-accent` (white) on `--accent-solid` / hover | 5.39 / 6.63 | 5.39 / 5.01 |
| White on `--danger-solid` | 5.21 | 5.21 |
| Inverse tooltip text | 16.02 | 16.26 |
| Dark raised surface (`#222221`): primary / secondary | n/a | 13.71 / 7.60 |
| **Non-text (≥ 3:1):** `--border-input` vs surface | 3.34 | 3.40 (3.65 vs input bg) |
| **Non-text:** `--focus-ring` vs surface / canvas | 5.39 / 5.12 | 5.89 / 6.33 |
| Disabled text (exempt per WCAG 1.4.3) | 3.34 | 3.40 |

Every text pair passes **AA (4.5:1)**, and most pass AAA. The only sub-3:1 colors are the
light-theme categorical series aqua, yellow and magenta (§7.5), which carry mandatory
relief: a legend, direct labels and the table twin.

### 11.2 Focus

```css
:focus-visible {
  outline: var(--focus-width) solid var(--focus-ring);
  outline-offset: var(--focus-offset);
}
.input:focus-visible, select.input:focus-visible { outline-offset: 1px; }
:focus:not(:focus-visible) { outline: none; }
```

* Use outline, never `box-shadow`. Outlines survive Windows forced colors, and the old
  `--ring` box-shadow did not.
* Never `outline: none` without a replacement.
* `tr:focus-within` highlights clickable rows.
* The canvas plot, sweep cells, dot-strip dots and segmented tabs are all focusable and
  show the same ring.

### 11.3 Keyboard

* Every action is reachable by Tab in reading order.
* Table rows open via their link (§9.3).
* Sweep cells are buttons.
* The heatmap scrubs with arrows (Shift = ×10, Home/End).
* Segmented controls use Left/Right.
* Dialogs and drawers trap focus, close on Esc and restore focus.
* The mobile sidebar drawer has the same behavior.
* P2: ⌘/Ctrl K opens the command palette.
* Add a **skip link** as the first focusable element, "Skip to content", targeting
  `<main id="content">`. It is visible on focus.

### 11.4 Motion

* Every duration token goes to 0 under `prefers-reduced-motion: reduce` (in the token
  file).
* Also add this global guard:

  ```css
  @media (prefers-reduced-motion: reduce) {
    *, *::before, *::after {
      animation-duration: 0.01ms !important;
      animation-iteration-count: 1 !important;
      transition-duration: 0.01ms !important;
      scroll-behavior: auto !important;
    }
  }
  ```

* Pulses (running dot, status dot, skeleton) become static. Progress bars jump to their
  values.

### 11.5 Forced colors and screen readers

* **Forced colors.**
  * `@media (forced-colors: active)`: badges, pills and callouts get
    `border: 1px solid CanvasText`.
  * Selected tabs and chip toggles get `outline: 2px solid Highlight`.
  * Progress fill uses `background: Highlight`.
  * Icons use `currentColor`, so they follow automatically.
* **Live regions.**
  * Toasts: `role="status"` (kept).
  * The auth banner is the only `role="alert"` (the layout test asserts exactly one).
  * Progress bars carry `aria-valuenow`.
  * The heatmap readout is `aria-live="polite"` only while the plot has focus.
* **Names.**
  * Every icon-only button has an `aria-label`.
  * Every table keeps its `aria-label`.
  * Collapsed-sidebar labels are clipped, not removed.
* **Target size.** The smallest control is the 24 px icon button, which meets WCAG 2.2
  §2.5.8 (24 × 24).
* **Language.** `<html lang="en">` (kept).

---

## 12. Implementation plan

### 12.1 Rules for every step

1. **No new npm dependencies.** Icons are vendored (§8). Plain global CSS and custom
   properties only: **no CSS modules** (tests assert global class names such as
   `hint-amber`, `verdict-ok`, `tag`, `progress` and `guided-step`), no Tailwind, no
   CSS-in-JS.
2. **Strings and element boundaries are frozen** for every test anchor in §10. Restyle by
   wrapping, never by merging text nodes. `getByText` matches an element's *own* text,
   so `17.6` must stay alone in its element.
3. **Kept class names** (§9) are restyled, not renamed. `role="alert"` stays only on the
   auth banner.
4. Run the suite after each step from `frontend/`: `npm test`,
   `npx tsc -p tsconfig.app.json --noEmit`, and `npm run build`. All three must be green
   before merging. Nothing in this plan needs `npm install`; if one is ever needed, use
   the README's `--cache .npm-cache` workaround.
5. **Commits:** follow the owner's rules. One commit per step per engineer, with a
   message that says what changed visually.
6. Only **intended test edits** are allowed:
   * `colormap.test.ts`: new anchors (§7.1–7.2) and new `deltaClass` bin tests.
   * `format.test.ts`: mph and veh/mi helpers.
   * New `tokens.test.ts` and `theme.test.ts`.

   Any other failing test means a frozen anchor was broken. Fix the code, not the test.

### 12.2 Ordered steps

| Step | Owner | Files | What | Done when |
|---|---|---|---|---|
| **0. Groundwork** (half a day, **merge first**) | A | `styles/tokens.css` (replace); `styles/app.css` → **split verbatim** into `styles/base.css`, `shell.css`, `components.css`, `viz.css`, `views/{scenarios,onboard,runs,run-detail,sweeps,reports,first-run}.css` (pure move, no visual change), then delete `app.css`; `main.tsx` (imports in that order; drop JetBrains Mono 700); `index.html` (§5.1); `lib/theme.ts` (new); `components/icons.tsx` (new); move `MetricCard`/`CIBar`/`StripChart` from `bits.tsx` to `components/metrics.tsx` and `SchematicThumb` + thumbs to `components/schematics.tsx` (import updates in RunDetailView and ScenariosView only); `test/tokens.test.ts` (dark blocks identical; every token in light also in dark), `test/theme.test.ts` | The app renders with the new colors through the legacy aliases, the theme follows the OS, and all tests are green |
| **1. Shell** | A | `components/Layout.tsx`, `components/PageHeader.tsx` (new), `styles/shell.css`, `components/SettingsDrawer.tsx` (Appearance section), `styles/base.css` (reset, focus, skip link, reduced motion, forced colors) | §6 and §11.2–11.5 are done. The clock is gone, the breadcrumb works, the sidebar collapses at 1023 px, and the mobile drawer works | `layout.test.tsx` green; manual check at 1440/1024/768 |
| **2. Primitives** | B | `styles/components.css`, `components/bits.tsx` (StatusChip, badges, ProgressBar), `components/ConfirmDialog.tsx` (layout, focus trap, restore), `components/toast.tsx`, new `components/ui/{Callout,EmptyState,Skeleton,CopyButton}.tsx` | §9.1–9.11 and §9.14–9.18. **Ship `Callout`, `EmptyState`, `Skeleton` and `CopyButton` first** (by the middle of day 1), because the other tracks import them | `confirmdialog.test.tsx` green; a primitives page is not needed |
| **3. Viz** | C | `lib/colormap.ts` (§7.1, 7.2, `deltaClass`), `lib/format.ts` (mph helpers), `components/HeatmapCanvas.tsx` (tokens, ResizeObserver, hatch, legend, readout, keyboard, CSV), `components/metrics.tsx` (`MetricTile`, `DotStrip`), `lib/metrics.ts` (`group`), `styles/viz.css` | §7 complete | `colormap.test.ts` and `format.test.ts` updated and green; `rundetail.test.tsx` and `noobservations.test.tsx` green |
| **4. Views** (parallel) | A: Runs, Reports · B: Scenarios, Onboard, First run, `SplitAuditTable` · C: Run detail, Sweeps, `InsertionPanel`, `MergeDiagnostics` | Each owner's view `.tsx` files + its `styles/views/*.css` | §10 per view | Each view's tests green |
| **5. Cleanup** | A | `tokens.css` (delete the legacy alias block); a grep sweep | Nothing references an alias: `grep -rnE "var\(--(bg|panel|panel-edge|panel-raised|panel-inset|text|muted|faint|accent|accent-dim|amber|amber-dim|danger|danger-dim|ok|ok-dim|s[1-6]|r[1-3]|font-ui|card-shadow|ring)\)" src` returns nothing. No hex outside `tokens.css` and `lib/colormap.ts`: `grep -rnE "#[0-9a-fA-F]{6}" src --include=*.tsx --include=*.css` hits only those two files and tests. `style={{` appears only for computed geometry | Greps clean; tests green |
| **6. QA** | all | none | Use the browser recipe in the repo notes (LAN address, demo/inline API). Walk all 7 routes in **light and dark** at **1440, 1024 and 768 px**. Run axe DevTools on every route in both themes (0 serious/critical). Emulate forced colors and reduced motion in Chrome DevTools. Run the keyboard-only pass in §11.3 | Checklist in §12.4 ticked |
| **7. P2 (optional)** | any | `components/CommandPalette.tsx`, Runs filter chips, Run detail "Report on this run", Onboard lane strip | §9.19 and the P2 items in §10 | Separate PRs |

### 12.3 Parallel split (disjoint files)

After step 0 merges, three engineers can work in parallel without touching the same file.

| Engineer | Owns exclusively |
|---|---|
| **A: Foundations, shell, Runs, Reports** | `styles/tokens.css`, `styles/base.css`, `styles/shell.css`, `styles/views/runs.css`, `styles/views/reports.css`, `index.html`, `main.tsx`, `lib/theme.ts`, `components/icons.tsx`, `components/Layout.tsx`, `components/PageHeader.tsx`, `components/SettingsDrawer.tsx`, `components/AppContext.tsx`, `views/RunsView.tsx`, `views/ReportsView.tsx`, `test/tokens.test.ts`, `test/theme.test.ts` |
| **B: Primitives, Scenarios, Onboard, First run** | `styles/components.css`, `styles/views/scenarios.css`, `styles/views/onboard.css`, `styles/views/first-run.css`, `components/bits.tsx`, `components/ConfirmDialog.tsx`, `components/toast.tsx`, `components/ui/*`, `components/schematics.tsx`, `components/SplitAuditTable.tsx`, `components/GuidedFirstRun.tsx`, `views/ScenariosView.tsx`, `views/OnboardView.tsx` |
| **C: Data viz, Run detail, Sweeps** | `styles/viz.css`, `styles/views/run-detail.css`, `styles/views/sweeps.css`, `lib/colormap.ts`, `lib/format.ts`, `lib/metrics.ts`, `components/HeatmapCanvas.tsx`, `components/metrics.tsx`, `components/InsertionPanel.tsx`, `components/MergeDiagnostics.tsx`, `views/RunDetailView.tsx`, `views/SweepsView.tsx`, `test/colormap.test.ts`, `test/format.test.ts` |

**Interfaces fixed by this document**, so tracks don't wait on each other:

* `PageHeader` props (§6.2), owned by A.
* `Callout`, `EmptyState`, `Skeleton` / `SkeletonRows` and `CopyButton` props (§9.8–9.11,
  9.18), owned by B.
* `MetricTile` and `deltaClass` (§9.12, §7.4), owned by C.
* `useResolvedTheme` / `readToken` (§5.1), owned by A in step 0.
* Icons by Lucide name (§8), owned by A in step 0.

Until B's primitives merge, A and C may import from the agreed paths with a local stub;
delete the stub when B lands. **Shared files:** nobody but the owner edits them. If a
change is needed outside your column, ask the owner in the PR.

**Suggested calendar:** day 1 morning is step 0 (A) while B drafts primitives and C drafts
the colormap and format work in new code. Day 1 afternoon is steps 1, 2 and 3 in
parallel. Days 2–3 are step 4. Day 4 is steps 5 and 6.

### 12.4 Acceptance checklist (step 6)

- [ ] All vitest suites green; `tsc` and `vite build` green; no new dependencies in
      `package.json` or the lockfile.
- [ ] Light, dark, and pinned-light-on-dark-OS all render correctly with no flash on
      load. The theme persists across reloads.
- [ ] The heatmap shows the new speed colormap, the dual-unit legend, the 40 km/h notch,
      hatched null bins and the readout row. Arrow keys scrub, CSV downloads, the plot
      repaints on theme flip and follows sidebar collapse.
- [ ] The sweep matrix uses the binned blue/red classes with readable ink in both
      themes, cells are keyboard-focusable, and the tooltip shows on focus.
- [ ] Metric tiles show `mean ± hw` only for symmetric CIs and carry dot strips. The
      per-replicate grid is gone.
- [ ] Every view has skeleton → error callout → empty state → content. No indefinite
      "loading…".
- [ ] axe reports 0 serious or critical issues per route in both themes. Keyboard-only
      completes: launch a run, open its detail, scrub the heatmap, open a sweep cell,
      generate a report.
- [ ] The cleanup greps (step 5) are clean.

---

## 13. Things NOT to copy

* **No third-party brand assets.** No logos, wordmarks, illustrations, screenshots,
  marketing imagery or proprietary icon sets from Linear, Vercel, Stripe, Datadog,
  Grafana, Palantir, IBM, Mapbox, Foursquare, Observable or Hex. The Geist font and Geist
  icons are not used. Our fonts stay Inter and JetBrains Mono, and our icons are Lucide
  (ISC) with the notice kept.
* **No signature brand colors:** not Stripe's blurple, not Linear's indigo-violet accent
  and gradients, not Vercel's pure black-and-white extreme, not Datadog's purple, not
  Grafana's orange gradients. Our accent is FlowState blue from our own report ramp.
* **No product-specific layouts copied wholesale.** We do not replicate Linear's issue
  list, Grafana's draggable panel grid and panel menus (we are not a dashboard builder),
  Stripe's dashboard home, or Kepler/Mapbox map-first layouts.
* **Radix:** we use Radix *Colors values* (MIT, notice in `tokens.css`). We do not copy
  Radix *Themes* component styling.
* **No decoration that pretends to be data:** no glow, glassmorphism, neon accents,
  animated backgrounds, gradient text or decorative charts. Remove the mission-control
  clock and the letter-spaced console caps.
* **No unverifiable trust signals,** even as placeholders: no "trusted by", no
  "validated" badges, no customer logos (`CLAUDE.md` §0, `docs/WEBSITE_BRIEF.md` §1).

---

## 14. Follow-ups outside this brief (not blocking)

1. **Report figures.** `validation/report.py` renders speed contours with matplotlib's
   default colormap (viridis). A later backend PR should switch to the §7.1 stops so the
   PDF matches the dashboard. Figures are not goldens, but note the change in the PR.
   The `scripts/m3_analyze_sweep.py` compliance colors are categorical; move them to the
   §7.5 ordinal ramp when those figures are next regenerated.
2. **Criteria results in `ReportOut`.** These are needed before §9.13 can be built.
3. **Delta confidence in sweep cells.** Cells show the delta of means. A paired CI on the
   delta (from the API) would let the matrix mark unresolved cells, for example with a
   hollow corner. Today it would require client-side statistics, which §7.4 forbids.

---

## Appendix A: How the numbers were produced

* **Contrast:** WCAG 2.x relative luminance on sRGB hex.
* **Color math:** OKLab/OKLCH (Björn Ottosson's matrices). Colormap anchors were placed
  in OKLCH and gamut-clipped by reducing chroma.
* **CVD:** Machado, Oliveira & Fernandes (2009), severity 1.0, for protan, deutan and
  tritan. ΔE is Euclidean OKLab × 100.
* **Categorical and ordinal palettes:** the data-viz palette validator (lightness band,
  chroma floor, adjacent CVD ≥ 8 target, normal-vision floor ≥ 15, contrast ≥ 3:1).
  Light ran against surface `#ffffff` and dark against `#191918`.
* **Audit figures** (§1.2) came from the same functions applied to the current
  `tokens.css` and `colormap.ts` values.
