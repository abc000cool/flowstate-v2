/** Test helpers for keyboard-focus behaviour (busyfocus, scenarios, onboard
 * and guided tests). Not a test file itself. */

import { createElement } from 'react';
import { useLocation } from 'react-router-dom';
import { expect } from 'vitest';
import { wantsContentFocus } from '../lib/hooks';

/** Drop focus to <body>, as a browser does when the focused button is
 * disabled. jsdom keeps focus on a disabled button and ignores blur() on one,
 * so focus is parked on a throwaway element that is then removed. */
export function dropFocus(): void {
  const sink = document.createElement('input');
  document.body.appendChild(sink);
  sink.focus();
  sink.remove();
  expect(document.activeElement).toBe(document.body);
}

/** A route element that says whether the navigation into it asked the shell
 * to focus the page content (`FOCUS_CONTENT_STATE`). */
export function ContentFocusProbe({ label }: { label: string }): JSX.Element {
  const { state } = useLocation();
  return createElement(
    'div',
    null,
    `${label}: ${wantsContentFocus(state) ? 'content focus requested' : 'no focus request'}`,
  );
}
