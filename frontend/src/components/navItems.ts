/** The app's routes as the sidebar groups them (docs/design/DASHBOARD_DESIGN.md
 * §6.1), shared with the command palette so the two never disagree on what a
 * page is called or where it lives. */

import type { IconName } from './icons';

export interface NavEntry {
  to: string;
  label: string;
  icon: IconName;
}

export const NAV_GROUPS: { id: string; label: string; items: NavEntry[] }[] = [
  {
    id: 'setup',
    label: 'Set up',
    items: [
      { to: '/onboard', label: 'Onboard corridor', icon: 'map-pin' },
      { to: '/scenarios', label: 'Scenarios', icon: 'layers' },
    ],
  },
  {
    id: 'simulate',
    label: 'Simulate',
    items: [
      { to: '/runs', label: 'Runs', icon: 'activity' },
      { to: '/sweeps', label: 'Sweeps', icon: 'grid-3x3' },
    ],
  },
  {
    id: 'report',
    label: 'Report',
    items: [{ to: '/reports', label: 'Reports', icon: 'file-text' }],
  },
];

/** The guided QUICKSTART path, kept in the sidebar footer. */
export const FIRST_RUN: NavEntry = { to: '/first-run', label: 'First run', icon: 'circle-play' };

/** Every page in sidebar order, First run last. */
export const ALL_PAGES: NavEntry[] = [...NAV_GROUPS.flatMap((g) => g.items), FIRST_RUN];
