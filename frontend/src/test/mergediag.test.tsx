/** MergeDiagnosticsPanel: the run detail's "Merge diagnostics" section
 * (MetricsOut.merge_diagnostics). Pinned: nothing renders when the run has
 * neither list (a ring, a plain corridor, an older service); each present
 * list is its own table with plain-English headers and the counters as
 * written; the seed the counters came from is named, because they are one
 * replicate's numbers and never a mean. */

import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { MergeDiagnostics } from '../api/types';
import { MergeDiagnosticsPanel } from '../components/MergeDiagnostics';

const METER = {
  ramp: 'Hickory Hollow Pkwy',
  controller: 'alinea',
  edge: '19441652#1',
  interval_s: 30,
  n_released: 412,
  n_passed_unstoppable: 6,
  n_rate_updates: 40,
};

const WEAVE = {
  ramp: 'Ruth St',
  exit: 'T.H.52',
  length_m: 305.2,
  n_entered: 388,
  n_changed_in: 241,
  n_changed_out: 145,
  n_exited: 143,
  n_reached_section_exiting: 145,
  n_departed_exiting: 147,
  n_forced: 19,
  n_forced_deferred: 57,
  n_missed: 1,
  n_unfinished: 1,
  n_cooperations: 612,
  mean_follower_decel_ms2: 0.416,
  n_changer_eased: 208,
  wait_s_mean: 4.84,
  wait_in_s_mean: 3.9,
  wait_out_s_mean: 6.3,
};

describe('MergeDiagnosticsPanel', () => {
  it('renders nothing without diagnostics or with two empty lists', () => {
    const none = render(<MergeDiagnosticsPanel diagnostics={null} />);
    expect(none.container).toBeEmptyDOMElement();
    const absent = render(<MergeDiagnosticsPanel diagnostics={undefined} />);
    expect(absent.container).toBeEmptyDOMElement();
    const empty: MergeDiagnostics = { seed: 1, ramp_meters: [], weave_sections: [] };
    const both = render(<MergeDiagnosticsPanel diagnostics={empty} />);
    expect(both.container).toBeEmptyDOMElement();
    expect(screen.queryByTestId('merge-diagnostics')).toBeNull();
  });

  it('shows the ramp-meter table alone when only meters ran', () => {
    render(
      <MergeDiagnosticsPanel diagnostics={{ seed: 4242, ramp_meters: [METER], weave_sections: [] }} />,
    );
    expect(screen.getByText('Merge diagnostics · seed 4242')).toBeInTheDocument();
    const table = within(screen.getByLabelText('ramp meters'));
    for (const header of ['Ramp', 'Controller', 'Interval [s]', 'Released', 'Passed unstoppable', 'Rate updates']) {
      expect(table.getByText(header)).toBeInTheDocument();
    }
    expect(table.getByText('Hickory Hollow Pkwy')).toBeInTheDocument();
    expect(table.getByText('alinea')).toBeInTheDocument();
    expect(table.getByText('412')).toBeInTheDocument();
    // vehicles that could not stop for the meter are flagged, not hidden
    expect(table.getByText('6')).toHaveClass('hint-amber');
    expect(table.getByText('40')).toBeInTheDocument();
    expect(screen.queryByLabelText('weaving sections')).toBeNull();
  });

  it('shows the weaving-section table with every counter and the mean wait', () => {
    render(
      <MergeDiagnosticsPanel diagnostics={{ seed: 7, ramp_meters: [], weave_sections: [WEAVE] }} />,
    );
    expect(screen.queryByLabelText('ramp meters')).toBeNull();
    const table = within(screen.getByLabelText('weaving sections'));
    for (const header of [
      'Section',
      'Entered',
      'Changed in',
      'Changed out',
      'Exited / reached',
      'Forced',
      'Deferred (vehicle-steps)',
      'Missed',
      'Unfinished',
      'Mean wait [s]',
      'Follower cooperations (vehicle-steps)',
      'Mean follower decel [m/s²]',
      'Changer easings',
    ]) {
      expect(table.getByText(header)).toBeInTheDocument();
    }
    expect(table.getByText('Ruth St → T.H.52')).toBeInTheDocument();
    expect(table.getByText('388')).toBeInTheDocument();
    expect(table.getByText('241')).toBeInTheDocument();
    expect(table.getByText('145')).toBeInTheDocument();
    expect(table.getByText('143 / 145')).toBeInTheDocument();
    expect(table.getByText('19')).toBeInTheDocument();
    expect(table.getByText('57')).toBeInTheDocument();
    // a change never made and a vehicle still under control are flagged
    const ones = table.getAllByText('1');
    expect(ones).toHaveLength(2);
    for (const cell of ones) expect(cell).toHaveClass('hint-amber');
    expect(table.getByText('4.8')).toBeInTheDocument();
    // the follower-cooperation counters: steps, the mean decel to two places, easings
    expect(table.getByText('612')).toBeInTheDocument();
    expect(table.getByText('0.42')).toBeInTheDocument();
    expect(table.getByText('208')).toBeInTheDocument();
  });

  it('shows a dash for each cooperation counter a meta written before the rule lacks', () => {
    const older = render(
      <MergeDiagnosticsPanel
        diagnostics={{
          seed: 2,
          ramp_meters: [],
          weave_sections: [
            {
              ...WEAVE,
              n_cooperations: null,
              mean_follower_decel_ms2: null,
              n_changer_eased: null,
            },
          ],
        }}
      />,
    );
    const table = within(screen.getByLabelText('weaving sections'));
    // the three new cells are dashes; the wait is still shown
    expect(table.getAllByText('—')).toHaveLength(3);
    expect(table.getByText('4.8')).toBeInTheDocument();
    older.unmount();
    // a section with cooperations but no commanded deceleration shows the count and a dash
    render(
      <MergeDiagnosticsPanel
        diagnostics={{
          seed: 3,
          ramp_meters: [],
          weave_sections: [{ ...WEAVE, n_cooperations: 0, mean_follower_decel_ms2: null }],
        }}
      />,
    );
    const again = within(screen.getByLabelText('weaving sections'));
    expect(again.getAllByText('—')).toHaveLength(1);
    expect(again.getByText('0')).toBeInTheDocument();
  });

  it('shows both tables when both kinds ran, and a dash for an unknown wait', () => {
    render(
      <MergeDiagnosticsPanel
        diagnostics={{
          seed: 1,
          ramp_meters: [METER],
          weave_sections: [
            {
              ...WEAVE,
              wait_s_mean: null,
              n_missed: 0,
              n_unfinished: 0,
              n_reached_section_exiting: null,
            },
          ],
        }}
      />,
    );
    expect(screen.getByLabelText('ramp meters')).toBeInTheDocument();
    const weaves = within(screen.getByLabelText('weaving sections'));
    expect(weaves.getByText('—')).toBeInTheDocument();
    // an older meta without the reached counter falls back to the routed vehicles
    expect(weaves.getByText('143 / 147')).toBeInTheDocument();
    // zero counters are not flagged
    for (const cell of weaves.getAllByText('0')) expect(cell).not.toHaveClass('hint-amber');
    // the section is honest about what the numbers are
    expect(screen.getByText(/not a corridor result and not a replicate mean/)).toBeInTheDocument();
  });
});
