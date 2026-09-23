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
