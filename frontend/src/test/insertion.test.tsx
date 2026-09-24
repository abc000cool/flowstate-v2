/** InsertionPanel: the run detail's "Demand integrity" section
 * (MetricsOut.insertion + MetricsOut.weave_exits, the corridor battery's own
 * blocks). Pinned: nothing renders when the service reports neither block
 * (a macro run, an older service); each present block shows its verdict as
 * a badge — ok green, a problem red, an empty plan grey; the weave-exits
 * table lists every section with the given-up count and share, and a red
 * badge marks a section above the threshold; an undefined share is a dash. */

import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { InsertionSummary, WeaveExits } from '../api/types';
import { InsertionPanel } from '../components/InsertionPanel';

const INSERTION_OK: InsertionSummary = {
  n_runs: 20,
  planned: 31640,
  departed: 31402,
  mean_arrived: 1498.4,
  n_with_arrived: 20,
  mean_departed_fraction: 0.9925,
  min_departed_fraction: 0.981,
  starved_ramps: [],
  verdict: 'ok',
};

const WEAVE_OK: WeaveExits = {
  threshold_share: 0.02,
  n_runs: 20,
  sections: [
    {
      ramp: 'Ruth St',
      exit: 'T.H.52',
      n_runs: 20,
      reached: 2900,
      missed_exit: { n: 23, share: 0.0079 },
      flagged: false,
    },
  ],
  verdict: 'ok',
};

describe('InsertionPanel', () => {
  it('renders nothing when the service reports neither block', () => {
    const none = render(<InsertionPanel insertion={null} weaveExits={null} />);
    expect(none.container).toBeEmptyDOMElement();
    const absent = render(<InsertionPanel insertion={undefined} weaveExits={undefined} />);
    expect(absent.container).toBeEmptyDOMElement();
    expect(screen.queryByTestId('insertion-panel')).toBeNull();
  });

  it('shows a healthy insertion line with a green badge and no weave table', () => {
    render(<InsertionPanel insertion={INSERTION_OK} weaveExits={null} />);
    expect(screen.getByText('Demand integrity · 20 replicates')).toBeInTheDocument();
    const badge = screen.getByLabelText('insertion verdict');
    expect(badge).toHaveTextContent('ok');
    expect(badge).toHaveClass('tag', 'verdict-ok');
    const line = screen.getByTestId('insertion-summary');
    expect(line).toHaveTextContent('departed 99.3 % of 31640 planned');
    expect(line).toHaveTextContent('(worst seed 98.1 %)');
    expect(line).toHaveTextContent('arrived 1498 per replicate');
    expect(line).not.toHaveTextContent('over');
    expect(screen.queryByText(/starved:/)).toBeNull();
    expect(screen.queryByLabelText('weave exits')).toBeNull();
    expect(screen.queryByLabelText('weave exits verdict')).toBeNull();
  });

  it('shows a backlog verdict in red, the starved ramps, and a partial arrival count', () => {
    render(
      <InsertionPanel
        insertion={{
          n_runs: 3,
          planned: 3000,
          departed: 2690,
          mean_arrived: 775,
          n_with_arrived: 2,
          mean_departed_fraction: 0.8967,
          min_departed_fraction: 0.7,
          starved_ramps: ['Ruth St', 'Hickory Hollow'],
          verdict: 'backlog: 10 % of planned vehicles never departed; starved ramps: Ruth St',
        }}
        weaveExits={null}
      />,
    );
    const badge = screen.getByLabelText('insertion verdict');
    expect(badge).toHaveTextContent(
      'backlog: 10 % of planned vehicles never departed; starved ramps: Ruth St',
    );
    expect(badge).toHaveClass('verdict-defect');
    expect(badge).not.toHaveClass('verdict-ok');
    expect(screen.getByText('starved: Ruth St, Hickory Hollow')).toHaveClass('hint-amber');
    // the arrived mean rests on fewer replicates than the sums, and says so
    expect(screen.getByTestId('insertion-summary')).toHaveTextContent(
      'arrived 775 per replicate over 2 of 3',
    );
  });

  it('shows an empty plan as a grey verdict with dashes, never zeros', () => {
    render(
      <InsertionPanel
        insertion={{
          n_runs: 3,
          planned: 0,
          departed: 0,
          mean_arrived: null,
          n_with_arrived: 0,
          mean_departed_fraction: null,
          min_departed_fraction: null,
          starved_ramps: [],
          verdict: 'no vehicles planned',
        }}
        weaveExits={null}
      />,
    );
    expect(screen.getByLabelText('insertion verdict')).toHaveClass('verdict-unknown');
    const line = screen.getByTestId('insertion-summary');
    expect(line).toHaveTextContent('departed — of 0 planned');
    expect(line).not.toHaveTextContent('worst seed');
    expect(line).not.toHaveTextContent('arrived');
  });

  it('shows the weave-exits table beside its verdict without an insertion block', () => {
    render(<InsertionPanel insertion={null} weaveExits={WEAVE_OK} />);
    expect(screen.getByText('Demand integrity · 20 replicates')).toBeInTheDocument();
    expect(screen.queryByLabelText('insertion verdict')).toBeNull();
    const verdict = screen.getByLabelText('weave exits verdict');
    expect(verdict).toHaveTextContent('ok');
    expect(verdict).toHaveClass('verdict-ok');
    const table = within(screen.getByLabelText('weave exits'));
    for (const header of ['Section', 'Reached', 'Given up', 'Share', 'Replicates']) {
      expect(table.getByText(header)).toBeInTheDocument();
    }
    expect(table.getByText('Ruth St → T.H.52')).toBeInTheDocument();
    expect(table.getByText('2900')).toBeInTheDocument();
    // a given-up count is flagged amber, but a share under the threshold gets no badge
    expect(table.getByText('23')).toHaveClass('hint-amber');
    expect(table.getByText('0.8 %')).toBeInTheDocument();
    expect(screen.queryByTestId('weave-exit-flag')).toBeNull();
    expect(table.getByText('20 / 20')).toBeInTheDocument();
  });

  it('marks a section above the threshold with a red badge and reddens the verdict', () => {
    render(
      <InsertionPanel
        insertion={INSERTION_OK}
        weaveExits={{
          threshold_share: 0.02,
          n_runs: 3,
          sections: [
            {
              ramp: 'Ruth St',
              exit: 'T.H.52',
              n_runs: 3,
              reached: 300,
              missed_exit: { n: 15, share: 0.05 },
              flagged: true,
            },
            {
              // an older meta pair: no exiter reached, share undefined, not flagged
              ramp: 'Hickory Hollow',
              exit: null,
              n_runs: 1,
              reached: 0,
              missed_exit: { n: 0, share: null },
              flagged: false,
            },
          ],
          verdict: 'exits given up: 5.0 % at Ruth St',
        }}
      />,
    );
    // both verdicts sit in the one panel: insertion green, weave exits red
    expect(screen.getByLabelText('insertion verdict')).toHaveClass('verdict-ok');
    const verdict = screen.getByLabelText('weave exits verdict');
    expect(verdict).toHaveTextContent('exits given up: 5.0 % at Ruth St');
    expect(verdict).toHaveClass('verdict-defect');
    const table = within(screen.getByLabelText('weave exits'));
    const flags = table.getAllByTestId('weave-exit-flag');
    expect(flags).toHaveLength(1);
    expect(flags[0]).toHaveTextContent('above 2 %');
    expect(flags[0]).toHaveClass('tag', 'verdict-defect');
    expect(table.getByText('5.0 %')).toBeInTheDocument();
    expect(table.getByText('3 / 3')).toBeInTheDocument();
    // the section without an exit name is listed by its ramp, with a dash for the share
    expect(table.getByText('Hickory Hollow')).toBeInTheDocument();
    expect(table.getByText('—')).toBeInTheDocument();
    expect(table.getByText('1 / 3')).toBeInTheDocument();
    for (const cell of table.getAllByText('0')) expect(cell).not.toHaveClass('hint-amber');
  });
});
