/** How a row served by the in-browser demo backend is labelled.
 *
 * Reads fall back to the demo backend when the API is unreachable, so the
 * dashboard stays demoable — but a demo row must never read as a server
 * answer (CLAUDE.md §0.1). The Scenarios cards set the convention: a DEMO tag
 * on the row itself, and no config hash, because a demo hash exists on no
 * server and printing one fabricates provenance. Runs follow the same rules,
 * and additionally do not animate a progress bar for replicates no worker is
 * computing.
 */

/** Printed where a config hash would go on a demo row. */
export const DEMO_HASH_LABEL = '— demo, no server hash —';

/** Tooltip for the DEMO tag on a row. */
export const DEMO_ROW_TITLE =
  'Built-in demo data, not a server answer: the API was unreachable when this ' +
  'row was read, so no server has run or is running it.';
