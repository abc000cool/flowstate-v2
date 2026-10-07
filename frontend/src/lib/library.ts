/** The scenario library as both launchers see it: the stored scenarios of
 * `GET /scenarios` and the repo presets of `GET /scenarios/preset`.
 *
 * A preset is a repo YAML, not a stored scenario: it has no `scenario_id`
 * until one is created from its config, which is why `POST /runs` cannot name
 * one directly and why `ensureStored` exists. Both the Scenarios cards and the
 * Runs launcher offer presets, so the two must agree on what a library item is
 * and on how one becomes runnable — otherwise the Runs view is empty until
 * somebody visits Scenarios first.
 */

import { createScenario, listScenarios } from '../api/client';
import type { PresetSummary, ScenarioSummary } from '../api/types';
import { simMinutes } from './limits';

/** The ring benchmark preset: the canonical emergence benchmark, and the only
 * scenario in the repo that needs no data file on the machine
 * (docs/QUICKSTART.md §4, CLAUDE.md §3.2.1). The guided first run uses it,
 * and the Runs launcher opens on it. */
export const RING_PRESET_FILENAME = 'ring_sugiyama.yaml';
export const RING_PRESET_NAME = 'ring_sugiyama';

/** The ring preset among the service's presets, by file name first. */
export function findRingPreset(presets: PresetSummary[]): PresetSummary | undefined {
  return (
    presets.find((p) => p.filename === RING_PRESET_FILENAME) ??
    presets.find((p) => p.name === RING_PRESET_NAME)
  );
}

/** The preset a launcher opens on before anything is chosen: the ring
 * benchmark when the service serves it, else the cheapest preset by what a
 * launch would commit (replicates × duration; the first one on a tie).
 *
 * Not simply the first in file order: that is `corridor_10km`, 20 × 20 min
 * ≈ 6.7 sim-hours, one click from being queued. Undefined without presets. */
export function defaultPreset(presets: PresetSummary[]): PresetSummary | undefined {
  const ring = findRingPreset(presets);
  if (ring) return ring;
  let best: PresetSummary | undefined;
  let bestCost = Infinity;
  for (const p of presets) {
    const replicates = p.config?.replicates;
    const duration = p.config?.sim?.duration_s;
    // a config without the numbers is not "free" (simMinutes reads it as 0)
    if (!Number.isFinite(replicates) || !Number.isFinite(duration)) continue;
    const cost = simMinutes(replicates, duration);
    if (cost < bestCost) {
      best = p;
      bestCost = cost;
    }
  }
  return best;
}

/** A library card/option: a stored scenario, or a repo preset with no id yet. */
export type LibraryItem = ScenarioSummary | PresetSummary;

export const isPreset = (s: LibraryItem): s is PresetSummary => !('scenario_id' in s);

/** A stable React key / select value for either kind. */
export const itemKey = (s: LibraryItem): string =>
  isPreset(s) ? `preset:${s.filename}` : s.scenario_id;

/** Merge the two endpoints into one library: a preset whose config is already
 * stored shows as the stored scenario, so the same config is not offered
 * twice under two different ids. */
export function mergeLibrary(
  presets: PresetSummary[],
  stored: ScenarioSummary[],
): LibraryItem[] {
  const storedHashes = new Set(stored.map((s) => s.config_hash));
  return [...presets.filter((p) => !storedHashes.has(p.config_hash)), ...stored];
}

/** The `scenario_id` to launch against, storing a preset first when needed.
 *
 * `stored` is true only when this call created the scenario, so the caller can
 * say so once instead of on every launch of the same preset.
 */
export async function ensureStored(
  s: LibraryItem,
): Promise<{ scenario_id: string; stored: boolean }> {
  if (!isPreset(s)) return { scenario_id: s.scenario_id, stored: false };
  const existing = (await listScenarios()).find((x) => x.config_hash === s.config_hash);
  if (existing) return { scenario_id: existing.scenario_id, stored: false };
  const res = await createScenario(s.config);
  return { scenario_id: res.scenario_id, stored: true };
}
