/**
 * Dual-unit speed legend (docs/design/DASHBOARD_DESIGN.md §7.3): a bar drawn from the
 * §7.1 stops, km/h ticks above and mph ticks below, an optional threshold notch and an
 * optional "no data" hatch key. Every tick is computed from the domain it is given.
 */
import { rampGradientCSS } from './colormap';
import { kmhToMph, niceTicks } from './units';

export interface LegendOpts {
  /** Domain top in km/h (the bar's right edge). */
  topKmh: number;
  /** Optional notch, in km/h, with its label. */
  threshold?: { kmh: number; label: string };
  /** Show the hatched "no data" key. */
  noData?: boolean;
  /** Decimal places for an end label that is not a round tick. */
  endDigits?: number;
}

export interface TickMark {
  /** Position on the bar, 0–100 %. */
  pct: number;
  label: string;
}

/** Round ticks from 0 up to the top, plus the top itself when it is not near a round tick. */
export function legendTicks(top: number, maxTicks: number, endDigits: number): TickMark[] {
  const ticks = niceTicks(top, maxTicks).filter((v) => v <= top + 1e-9);
  const marks = ticks.map((v) => ({ pct: (v / top) * 100, label: `${+v.toFixed(2)}` }));
  const last = ticks[ticks.length - 1];
  if (top - last > 0.12 * top) marks.push({ pct: 100, label: top.toFixed(endDigits) });
  return marks;
}

function tickRow(cls: string, unit: string, marks: TickMark[]): HTMLElement {
  const row = document.createElement('div');
  row.className = `lg-row ${cls}`;
  const u = document.createElement('span');
  u.className = 'lg-unit';
  u.textContent = unit;
  const track = document.createElement('div');
  track.className = 'lg-ticks';
  for (const m of marks) {
    const s = document.createElement('span');
    s.className = 'lg-tick';
    s.style.left = `${m.pct}%`;
    if (m.pct < 3) s.classList.add('is-start');
    else if (m.pct > 97) s.classList.add('is-end');
    s.textContent = m.label;
    track.appendChild(s);
  }
  row.append(u, track);
  return row;
}

export function renderLegend(host: HTMLElement, o: LegendOpts): void {
  const top = o.topKmh;
  const topMph = kmhToMph(top);
  const endDigits = o.endDigits ?? 1;
  host.replaceChildren();
  host.classList.add('legend');
  host.setAttribute('role', 'img');
  host.setAttribute(
    'aria-label',
    `Speed colour scale from 0 to ${top.toFixed(endDigits)} km/h (${topMph.toFixed(endDigits)} mph): ` +
      'deep red is stopped, pale at mid-scale, blue at the top' +
      (o.threshold ? `; ${o.threshold.label}` : '') +
      (o.noData ? '; hatched bins have no data' : '') +
      '.',
  );

  const kmh = tickRow('is-top', 'km/h', legendTicks(top, 6, endDigits));
  const barRow = document.createElement('div');
  barRow.className = 'lg-row';
  barRow.appendChild(document.createElement('span')).className = 'lg-unit';
  const bar = document.createElement('div');
  bar.className = 'lg-bar';
  bar.style.background = rampGradientCSS();
  if (o.threshold && o.threshold.kmh > 0 && o.threshold.kmh < top) {
    const notch = document.createElement('span');
    notch.className = 'lg-notch';
    notch.style.left = `${(o.threshold.kmh / top) * 100}%`;
    bar.appendChild(notch);
  }
  barRow.appendChild(bar);
  // the bar spans 0–top km/h, which is 0–topMph mph, so mph ticks sit at v / topMph
  const mph = tickRow('is-bottom', 'mph', legendTicks(topMph, 8, endDigits));

  const scale = document.createElement('div');
  scale.className = 'lg-scale';
  scale.append(kmh, barRow, mph);
  host.appendChild(scale);

  if (o.threshold || o.noData) {
    const keys = document.createElement('div');
    keys.className = 'lg-keys';
    if (o.threshold) {
      const k = document.createElement('span');
      k.className = 'lg-key';
      const sw = document.createElement('span');
      sw.className = 'lg-notch-key';
      sw.setAttribute('aria-hidden', 'true');
      k.append(sw, document.createTextNode(o.threshold.label));
      keys.appendChild(k);
    }
    if (o.noData) {
      const k = document.createElement('span');
      k.className = 'lg-key';
      const sw = document.createElement('span');
      sw.className = 'lg-hatch';
      sw.setAttribute('aria-hidden', 'true');
      k.append(sw, document.createTextNode('no data'));
      keys.appendChild(k);
    }
    host.appendChild(keys);
  }
}
