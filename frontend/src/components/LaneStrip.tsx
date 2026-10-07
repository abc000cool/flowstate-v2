/** Lane strip of an onboarded corridor (docs/design/DASHBOARD_DESIGN.md §10.2
 * P2): a schematic of the carriageway drawn from the onboarding summary the
 * view already holds — no request of its own.
 *
 *   top row     split-audit verdicts (○ as drawn, ◆ defect, dashed ○ side
 *               undecided) and stations whose lane count differs from the
 *               detector inventory (■)
 *   carriageway one band per `lanes_profile` run, lane 0 (SUMO's rightmost)
 *               at the bottom, dashed lane lines; at each audited split the
 *               lanes feeding the exit are shaded over the approach, and the
 *               lanes a defect traps (`trapped_lanes` on a
 *               `through_lane_exit_only`, the feeding lanes on a wrong-side
 *               verdict) are hatched in the danger colour
 *   bottom row  the demand model's ramps: ▲ entrance, ▼ exit
 *   axis        position along the corridor, upstream at the left
 *
 * Schematic, not to lateral scale: the profile does not say on which side a
 * lane is added, so bands are aligned on the right edge, as SUMO numbers
 * them. Nothing is inferred beyond the summary's own fields.
 *
 * Pure SVG on tokens (classes in styles/views/onboard.css), so both themes
 * and forced colors follow. The SVG is one `role="img"` with a one-line
 * name; the full text alternative is the "Lane strip as text" list under it,
 * one line per position, which also carries every value the hover titles
 * show. */

import { useEffect, useId, useMemo, useRef, useState, type RefObject } from 'react';
import type { CorridorLaneMismatch, CorridorSplitFinding, CorridorSummary } from '../api/types';
import { distUnit, formatDistKm, formatTickDist, spaceTicks } from '../lib/format';
import { isSplitDefect } from './SplitAuditTable';

export interface LaneSegment {
  x0: number;
  x1: number;
  lanes: number;
}

export interface LaneSplit {
  x: number;
  finding: CorridorSplitFinding;
  defect: boolean;
  /** Lanes (0 = rightmost) feeding the exit. */
  feed: number[];
  /** Of the lanes drawn, the ones the defect traps through traffic in. */
  trapped: number[];
}

/** One line of the text alternative, at a position along the corridor. */
export interface LaneStripEvent {
  x: number;
  text: string;
  tone: 'plain' | 'defect' | 'warning';
}

export interface LaneStripModel {
  length: number;
  segments: LaneSegment[];
  /** Rows the carriageway needs: the widest run, or a split's compiled lanes. */
  rows: number;
  entrances: { x: number; name: string }[];
  exits: { x: number; name: string }[];
  splits: LaneSplit[];
  mismatches: CorridorLaneMismatch[];
  events: LaneStripEvent[];
}

const plural = (n: number, one: string, many = `${one}s`): string => `${n} ${n === 1 ? one : many}`;

function laneList(lanes: number[]): string {
  return `${lanes.length === 1 ? 'lane' : 'lanes'} ${lanes.join(', ')}`;
}

/** The trapped lanes of a defect: the lanes the map draws as continuing on a
 * `through_lane_exit_only`, the feeding lanes on a wrong-side verdict. */
function trappedLanes(f: CorridorSplitFinding): number[] {
  if (!isSplitDefect(f.verdict)) return [];
  if (f.verdict === 'through_lane_exit_only' && f.trapped_lanes && f.trapped_lanes.length > 0) {
    return f.trapped_lanes;
  }
  return f.exit_from_lanes;
}

/** What one split-audit finding says, in a line. */
export function splitText(f: CorridorSplitFinding): string {
  const at = `split to exit ${f.exit_edge}`;
  const of = `of ${f.compiled_lanes}`;
  const feed = f.exit_from_lanes.length > 0 ? `${laneList(f.exit_from_lanes)} ${of}` : 'no lane';
  switch (f.verdict) {
    case 'ok':
      return `${at}: as drawn (${f.osm_side} side), fed from ${feed}`;
    case 'wrong_side':
      return (
        `${at}: defect, drawn on the ${f.osm_side} side but compiled ${f.compiled_side} ` +
        `(fed from ${feed}); through traffic there is trapped`
      );
    case 'added_lane_wrong_side':
      return (
        `${at}: defect, a lane added by ramp guessing feeds the exit from the wrong side ` +
        `(fed from ${feed})`
      );
    case 'through_lane_exit_only': {
      const trapped = trappedLanes(f);
      const evidence = f.trapped_evidence ? ` (by ${f.trapped_evidence})` : '';
      const verb = trapped.length === 1 ? 'is' : 'are';
      return (
        `${at}: defect, ${laneList(trapped)} ${of} ${verb} drawn as continuing${evidence} ` +
        'but compiled exit-only; through traffic there is trapped'
      );
    }
    default:
      return `${at}: side undecided (no continuing way or usable geometry), fed from ${feed}`;
  }
}

/** The strip's geometry and its text alternative, from the summary alone;
 * null when there is nothing to draw (no length or no lane profile). */
export function laneStripModel(summary: CorridorSummary): LaneStripModel | null {
  const segments = summary.lanes_profile
    .map(([x0, x1, lanes]) => ({ x0, x1, lanes }))
    .filter((s) => Number.isFinite(s.x0) && Number.isFinite(s.x1) && s.x1 > s.x0 && s.lanes > 0)
    .sort((a, b) => a.x0 - b.x0);
  const length = Math.max(
    summary.chain_length_m,
    segments.length > 0 ? segments[segments.length - 1].x1 : 0,
  );
  if (!(length > 0) || segments.length === 0) return null;

  const entrances = summary.ramps
    .filter((r) => r.kind === 'on')
    .map((r) => ({ x: r.x_m, name: r.name }));
  const exits = summary.ramps
    .filter((r) => r.kind === 'off')
    .map((r) => ({ x: r.x_m, name: r.name }));
  const splits: LaneSplit[] = (summary.split_audit ?? []).map((f) => ({
    x: f.x_m,
    finding: f,
    defect: isSplitDefect(f.verdict),
    feed: f.exit_from_lanes,
    trapped: trappedLanes(f),
  }));
  const mismatches = summary.lane_mismatches ?? [];
  const rows = Math.max(
    ...segments.map((s) => s.lanes),
    ...splits.map((s) => s.finding.compiled_lanes),
    ...splits.flatMap((s) => s.feed.map((i) => i + 1)),
  );

  const events: LaneStripEvent[] = [];
  segments.forEach((s, i) => {
    const prev = segments[i - 1];
    if (!prev) events.push({ x: s.x0, text: plural(s.lanes, 'lane'), tone: 'plain' });
    else if (s.lanes !== prev.lanes || s.x0 > prev.x1) {
      events.push({ x: s.x0, text: `${prev.lanes} → ${plural(s.lanes, 'lane')}`, tone: 'plain' });
    }
  });
  for (const e of entrances) events.push({ x: e.x, text: `entrance, ${e.name}`, tone: 'plain' });
  for (const e of exits) events.push({ x: e.x, text: `exit, ${e.name}`, tone: 'plain' });
  for (const s of splits) {
    events.push({ x: s.x, text: splitText(s.finding), tone: s.defect ? 'defect' : 'plain' });
  }
  for (const m of mismatches) {
    events.push({
      x: m.x_m,
      text:
        `station ${m.station}: lane count differs, the map has ${m.compiled_lanes} ` +
        `and the detector inventory ${m.inventory_lanes}`,
      tone: 'warning',
    });
  }
  // by position; at one position, lane changes first (stable otherwise)
  events.sort((a, b) => a.x - b.x);

  return { length, segments, rows, entrances, exits, splits, mismatches, events };
}

/** The strip's one-line summary: its name as an image, and its caption. */
export function laneStripSummary(model: LaneStripModel): string {
  const lanes = model.segments.map((s) => s.lanes);
  const lo = Math.min(...lanes);
  const hi = Math.max(...lanes);
  const parts = [
    lo === hi ? plural(lo, 'lane') : `${lo}–${hi} lanes`,
    plural(model.entrances.length, 'entrance'),
    plural(model.exits.length, 'exit'),
  ];
  if (model.splits.length > 0) {
    const defects = model.splits.filter((s) => s.defect).length;
    parts.push(
      `${plural(model.splits.length, 'split')} audited, ${plural(defects, 'defect')}`,
    );
  }
  if (model.mismatches.length > 0) {
    parts.push(plural(model.mismatches.length, 'lane-count mismatch', 'lane-count mismatches'));
  }
  return parts.join(' · ');
}

/* ------------------------------ geometry ------------------------------ */

const PAD_X = 14;
const LANE_H = 10;
const MARK_ROW = 18;
const ROAD_TOP = MARK_ROW + 6;
const RAMP_GAP = 5;
const RAMP_H = 10;
const AXIS_GAP = 8;
const TICK = 4;
const LABEL_DY = 15;
/** Width used before the container has been measured (and in jsdom). */
const FALLBACK_WIDTH = 720;
const MIN_WIDTH = 280;
/** Approach drawn before a split, as a share of the corridor (at least
 * 150 m, at least 10 px). */
const APPROACH_SHARE = 0.025;
const APPROACH_MIN_M = 150;
const APPROACH_MIN_PX = 10;

function useContainerWidth(enabled: boolean): [RefObject<HTMLDivElement>, number] {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(FALLBACK_WIDTH);
  useEffect(() => {
    const el = ref.current;
    if (!enabled || !el) return;
    const measure = (): void => {
      const w = el.clientWidth;
      if (w > 0) setWidth(Math.max(MIN_WIDTH, Math.round(w)));
    };
    measure();
    if (typeof ResizeObserver === 'function') {
      const ro = new ResizeObserver(measure);
      ro.observe(el);
      return () => ro.disconnect();
    }
    window.addEventListener('resize', measure);
    return () => window.removeEventListener('resize', measure);
  }, [enabled]);
  return [ref, width];
}

/** Legend swatches: the same shapes and classes as the plot. */
function Swatch({ kind, hatchId }: { kind: string; hatchId: string }): JSX.Element {
  let shape: JSX.Element;
  switch (kind) {
    case 'entrance':
      shape = <polygon className="ls-ramp" points="3,11 13,11 8,2" />;
      break;
    case 'exit':
      shape = <polygon className="ls-ramp" points="3,2 13,2 8,11" />;
      break;
    case 'feed':
      shape = <rect className="ls-feed" x="1" y="3" width="14" height="7" />;
      break;
    case 'trapped':
      shape = (
        <rect className="ls-trapped" x="1" y="3" width="14" height="7" fill={`url(#${hatchId})`} />
      );
      break;
    case 'ok':
      shape = <circle className="ls-mark-ok" cx="8" cy="6.5" r="4" />;
      break;
    case 'defect':
      shape = <polygon className="ls-mark-defect" points="8,1.5 13,6.5 8,11.5 3,6.5" />;
      break;
    case 'unknown':
      shape = <circle className="ls-mark-unknown" cx="8" cy="6.5" r="4" />;
      break;
    default:
      shape = <rect className="ls-mark-warn" x="4.5" y="3" width="7" height="7" />;
  }
  return (
    <svg className="lane-strip-swatch" width="16" height="13" viewBox="0 0 16 13" aria-hidden="true" focusable="false">
      {shape}
    </svg>
  );
}

export function LaneStrip({ summary }: { summary: CorridorSummary }): JSX.Element | null {
  const model = useMemo(() => laneStripModel(summary), [summary]);
  const [wrapRef, width] = useContainerWidth(model !== null);
  const uid = useId();
  const hatchId = `${uid}-hatch`;
  if (!model) return null;

  const L = model.length;
  const W = width;
  const roadBottom = ROAD_TOP + model.rows * LANE_H;
  const rampY = roadBottom + RAMP_GAP;
  const axisY = rampY + RAMP_H + AXIS_GAP;
  const H = axisY + LABEL_DY + 4;
  const sx = (x: number): number => PAD_X + (Math.min(Math.max(x, 0), L) / L) * (W - 2 * PAD_X);
  const topOf = (lanes: number): number => roadBottom - lanes * LANE_H;
  const laneY = (i: number): number => roadBottom - (i + 1) * LANE_H;
  const lanesAt = (x: number): number =>
    model.segments.find((s) => x >= s.x0 && x <= s.x1)?.lanes ?? 0;
  const km = (x: number): string => formatDistKm(x);
  const ticks = spaceTicks(0, L, Math.max(2, Math.floor(W / 110)));
  const unit = distUnit(L);
  const approachM = Math.max(L * APPROACH_SHARE, APPROACH_MIN_M);
  const summaryText = laneStripSummary(model);

  const legend: [string, string][] = [];
  if (model.entrances.length > 0) legend.push(['entrance', 'Entrance']);
  if (model.exits.length > 0) legend.push(['exit', 'Exit']);
  if (model.splits.length > 0) legend.push(['feed', 'Lanes feeding an exit']);
  if (model.splits.some((s) => !s.defect && s.finding.verdict === 'ok')) {
    legend.push(['ok', 'Split as drawn']);
  }
  if (model.splits.some((s) => s.defect)) {
    legend.push(['defect', 'Split defect']);
    legend.push(['trapped', 'Lane that traps through traffic']);
  }
  if (model.splits.some((s) => s.finding.verdict === 'unknown')) {
    legend.push(['unknown', 'Side undecided']);
  }
  if (model.mismatches.length > 0) legend.push(['mismatch', 'Lane count differs from the inventory']);

  return (
    <figure className="lane-strip">
      <div className="lane-strip-plot" ref={wrapRef}>
        <svg
          width={W}
          height={H}
          viewBox={`0 0 ${W} ${H}`}
          role="img"
          aria-label={`Lane strip, ${km(L)}: ${summaryText}. Each position is listed in “Lane strip as text”.`}
        >
          <defs>
            <pattern id={hatchId} width="5" height="5" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
              <rect className="ls-trapped-bg" width="5" height="5" />
              <line className="ls-trapped-hatch" x1="0" y1="0" x2="0" y2="5" />
            </pattern>
          </defs>

          {/* carriageway: bands, lane lines, edges, steps where lanes change */}
          {model.segments.map((s, i) => {
            const x0 = sx(s.x0);
            const x1 = sx(s.x1);
            const top = topOf(s.lanes);
            const prev = model.segments[i - 1];
            return (
              <g key={`seg-${i}`}>
                <rect className="ls-road" x={x0} y={top} width={Math.max(0, x1 - x0)} height={s.lanes * LANE_H} />
                {Array.from({ length: s.lanes - 1 }, (_, k) => (
                  <line key={k} className="ls-lane-line" x1={x0} x2={x1} y1={topOf(k + 1)} y2={topOf(k + 1)} />
                ))}
                <line className="ls-edge" x1={x0} x2={x1} y1={top} y2={top} />
                <line className="ls-edge" x1={x0} x2={x1} y1={roadBottom} y2={roadBottom} />
                {prev && prev.lanes !== s.lanes && (
                  <line
                    className="ls-edge"
                    x1={x0}
                    x2={x0}
                    y1={Math.min(top, topOf(prev.lanes))}
                    y2={Math.max(top, topOf(prev.lanes))}
                  />
                )}
              </g>
            );
          })}

          {/* splits: the approach lanes feeding the exit, trapped lanes, the
              split line, and the verdict marker above */}
          {model.splits.map((s, i) => {
            const xEnd = sx(s.x);
            const xStart = Math.min(xEnd - APPROACH_MIN_PX, sx(s.x - approachM));
            const x0 = Math.max(PAD_X, xStart);
            // the profile's lanes over the approach, and the lanes compiled at
            // the split; any more than the profile widen the carriageway there
            const profileLanes = Math.min(lanesAt(s.x), lanesAt(s.x - approachM) || lanesAt(s.x));
            const lanes = Math.max(profileLanes, s.finding.compiled_lanes, ...s.feed.map((l) => l + 1));
            const v = s.finding.verdict;
            const title = `${km(s.x)}: ${splitText(s.finding)}`;
            return (
              <g key={`split-${i}`} className={s.defect ? 'ls-split-group is-defect' : 'ls-split-group'}>
                <title>{title}</title>
                {lanes > profileLanes && profileLanes > 0 && (
                  <>
                    <rect
                      className="ls-road"
                      x={x0}
                      y={topOf(lanes)}
                      width={xEnd - x0}
                      // 1 px over the profile's top edge, which is a lane line here
                      height={(lanes - profileLanes) * LANE_H + 1}
                    />
                    {Array.from({ length: lanes - profileLanes }, (_, k) => (
                      <line
                        key={`w${k}`}
                        className="ls-lane-line"
                        x1={x0}
                        x2={xEnd}
                        y1={topOf(profileLanes + k)}
                        y2={topOf(profileLanes + k)}
                      />
                    ))}
                    <line className="ls-edge" x1={x0} x2={xEnd} y1={topOf(lanes)} y2={topOf(lanes)} />
                    <line className="ls-edge" x1={x0} x2={x0} y1={topOf(lanes)} y2={topOf(profileLanes)} />
                  </>
                )}
                {s.feed
                  .filter((lane) => !s.trapped.includes(lane))
                  .map((lane) => (
                    <rect key={`f${lane}`} className="ls-feed" x={x0} y={laneY(lane)} width={xEnd - x0} height={LANE_H} />
                  ))}
                {s.trapped.map((lane) => (
                  <rect
                    key={`t${lane}`}
                    className="ls-trapped"
                    x={x0}
                    y={laneY(lane)}
                    width={xEnd - x0}
                    height={LANE_H}
                    fill={`url(#${hatchId})`}
                  />
                ))}
                <line className={s.defect ? 'ls-split is-defect' : 'ls-split'} x1={xEnd} x2={xEnd} y1={topOf(lanes)} y2={roadBottom} />
                {v === 'ok' ? (
                  <circle className="ls-mark-ok" cx={xEnd} cy={MARK_ROW / 2 + 1} r={4.5} />
                ) : s.defect ? (
                  <polygon
                    className="ls-mark-defect"
                    points={`${xEnd},${MARK_ROW / 2 - 5} ${xEnd + 5.5},${MARK_ROW / 2 + 1} ${xEnd},${MARK_ROW / 2 + 7} ${xEnd - 5.5},${MARK_ROW / 2 + 1}`}
                  />
                ) : (
                  <circle className="ls-mark-unknown" cx={xEnd} cy={MARK_ROW / 2 + 1} r={4.5} />
                )}
              </g>
            );
          })}

          {/* stations whose lane count disagrees with the detector inventory */}
          {model.mismatches.map((m) => (
            <g key={`mm-${m.station}`}>
              <title>{`${km(m.x_m)}: station ${m.station}, the map has ${m.compiled_lanes} lanes and the detector inventory ${m.inventory_lanes}`}</title>
              <rect className="ls-mark-warn" x={sx(m.x_m) - 4} y={MARK_ROW / 2 - 3} width={8} height={8} />
            </g>
          ))}

          {/* the demand model's ramps */}
          {model.entrances.map((e, i) => {
            const cx = sx(e.x);
            return (
              <g key={`on-${i}`}>
                <title>{`${km(e.x)}: entrance, ${e.name}`}</title>
                <polygon className="ls-ramp" points={`${cx - 5},${rampY + RAMP_H} ${cx + 5},${rampY + RAMP_H} ${cx},${rampY}`} />
              </g>
            );
          })}
          {model.exits.map((e, i) => {
            const cx = sx(e.x);
            return (
              <g key={`off-${i}`}>
                <title>{`${km(e.x)}: exit, ${e.name}`}</title>
                <polygon className="ls-ramp" points={`${cx - 5},${rampY} ${cx + 5},${rampY} ${cx},${rampY + RAMP_H}`} />
              </g>
            );
          })}

          {/* position axis */}
          <line className="ls-axis" x1={PAD_X} x2={W - PAD_X} y1={axisY} y2={axisY} />
          {ticks.map((t, i) => {
            const x = sx(t);
            const anchor = i === 0 && x - PAD_X < 12 ? 'start' : x > W - PAD_X - 12 ? 'end' : 'middle';
            return (
              <g key={`tick-${t}`}>
                <line className="ls-axis" x1={x} x2={x} y1={axisY} y2={axisY + TICK} />
                <text className="ls-tick-label" x={x} y={axisY + LABEL_DY} textAnchor={anchor}>
                  {`${formatTickDist(t, L)} ${unit}`}
                </text>
              </g>
            );
          })}
        </svg>
      </div>

      <figcaption className="lane-strip-caption">
        {summaryText}. Upstream at the left; lane 0, the rightmost, at the bottom. Schematic, not to
        lateral scale.
      </figcaption>

      {legend.length > 0 && (
        <ul className="lane-strip-legend">
          {legend.map(([kind, label]) => (
            <li key={kind}>
              <Swatch kind={kind} hatchId={hatchId} />
              {label}
            </li>
          ))}
        </ul>
      )}

      <details className="disclosure lane-strip-text">
        <summary>Lane strip as text</summary>
        <div className="disclosure-body">
          <ol className="lane-strip-list">
            {model.events.map((e, i) => (
              <li key={i} className={e.tone === 'plain' ? undefined : `is-${e.tone}`}>
                <span className="lane-strip-at mono">{km(e.x)}</span> {e.text}
              </li>
            ))}
          </ol>
        </div>
      </details>
    </figure>
  );
}
