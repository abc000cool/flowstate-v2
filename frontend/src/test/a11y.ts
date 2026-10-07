/** An axe-like accessibility audit for jsdom, built only on what the
 * dashboard already depends on (Testing Library's role queries, which compute
 * accessible names per the W3C accname spec through dom-accessibility-api).
 *
 * axe-core is not installed (it is not in package.json or the lockfile, and
 * nothing installed pulls it in), and §12.1 forbids new dependencies, so
 * this stands in for the axe pass of docs/design/DASHBOARD_DESIGN.md §12.4.
 * It implements the subset of axe rules of impact *serious* or *critical*
 * that can be judged from the DOM alone, under axe's rule ids:
 *
 *   names        button-name, link-name, input-button-name, label,
 *                select-name, aria-input-field-name, aria-toggle-field-name,
 *                aria-command-name, aria-progressbar-name, aria-meter-name,
 *                aria-tooltip-name, aria-dialog-name, aria-treeitem-name,
 *                image-alt, role-img-alt, svg-img-alt
 *   ARIA         aria-roles, aria-valid-attr-value (dangling id references),
 *                aria-required-attr, aria-required-children,
 *                aria-required-parent, aria-allowed-attr (the state
 *                attributes), aria-prohibited-attr (a name on a generic
 *                element), aria-hidden-focus, nested-interactive
 *   structure    duplicate-id-aria, duplicate-id-active, list, listitem,
 *                definition-list, dlitem, tabindex
 *
 * Not covered, because jsdom has no layout or computed styles: color-contrast
 * (the token pairs are computed in the brief, §11.1), scrollable-region-
 * focusable, target-size, and anything that needs rendering. Moderate and
 * minor rules (heading order, landmarks, region) are out of scope, as they
 * are for the §12.4 criterion. Not a test file itself. */

import { getRoles, isInaccessible, queryAllByRole } from '@testing-library/react';

export interface A11yViolation {
  rule: string;
  impact: 'critical' | 'serious';
  message: string;
  /** The element's opening tag, trimmed. */
  html: string;
}

/** WAI-ARIA 1.2 roles (concrete and the DPUB/graphics ones a page may use). */
const VALID_ROLES = new Set(
  (
    'alert alertdialog application article banner blockquote button caption cell checkbox ' +
    'code columnheader combobox complementary contentinfo definition deletion dialog ' +
    'directory document emphasis feed figure form generic grid gridcell group heading img ' +
    'insertion link list listbox listitem log main marquee math menu menubar menuitem ' +
    'menuitemcheckbox menuitemradio meter navigation none note option paragraph presentation ' +
    'progressbar radio radiogroup region row rowgroup rowheader scrollbar search searchbox ' +
    'separator slider spinbutton status strong subscript superscript switch tab table ' +
    'tablist tabpanel term textbox time timer toolbar tooltip tree treegrid treeitem ' +
    'graphics-document graphics-object graphics-symbol'
  ).split(' '),
);

/** Roles whose accessible name ARIA prohibits (an author name is ignored). */
const NAME_PROHIBITED = new Set([
  'caption',
  'code',
  'deletion',
  'emphasis',
  'generic',
  'insertion',
  'paragraph',
  'presentation',
  'none',
  'strong',
  'subscript',
  'superscript',
]);

/** Roles whose children are presentational: nothing focusable may sit inside. */
const PRESENTATIONAL_CHILDREN = new Set([
  'button',
  'checkbox',
  'img',
  'menuitemcheckbox',
  'menuitemradio',
  'meter',
  'option',
  'progressbar',
  'radio',
  'scrollbar',
  'separator',
  'slider',
  'switch',
  'tab',
]);

/** Roles that must have a non-empty accessible name, with axe's rule id. */
const NAMED_ROLES: [string, string, A11yViolation['impact']][] = [
  ['button', 'button-name', 'critical'],
  ['link', 'link-name', 'serious'],
  ['textbox', 'label', 'critical'],
  ['searchbox', 'label', 'critical'],
  ['spinbutton', 'label', 'critical'],
  ['combobox', 'select-name', 'critical'],
  ['listbox', 'select-name', 'critical'],
  ['checkbox', 'label', 'critical'],
  ['radio', 'label', 'critical'],
  ['slider', 'aria-input-field-name', 'serious'],
  ['switch', 'aria-toggle-field-name', 'serious'],
  ['menuitem', 'aria-command-name', 'serious'],
  ['progressbar', 'aria-progressbar-name', 'serious'],
  ['meter', 'aria-meter-name', 'serious'],
  ['tooltip', 'aria-tooltip-name', 'serious'],
  ['dialog', 'aria-dialog-name', 'serious'],
  ['alertdialog', 'aria-dialog-name', 'serious'],
  ['treeitem', 'aria-treeitem-name', 'serious'],
  ['img', 'role-img-alt', 'serious'],
];

/** State attributes and the roles that support them (ARIA 1.2). */
const STATE_ATTR_ROLES: Record<string, Set<string>> = {
  'aria-selected': new Set(['gridcell', 'option', 'row', 'tab', 'columnheader', 'rowheader']),
  'aria-checked': new Set([
    'checkbox',
    'menuitemcheckbox',
    'menuitemradio',
    'option',
    'radio',
    'switch',
    'treeitem',
  ]),
  'aria-pressed': new Set(['button']),
  'aria-valuenow': new Set(['meter', 'progressbar', 'scrollbar', 'separator', 'slider', 'spinbutton']),
  'aria-valuemin': new Set(['meter', 'progressbar', 'scrollbar', 'separator', 'slider', 'spinbutton']),
  'aria-valuemax': new Set(['meter', 'progressbar', 'scrollbar', 'separator', 'slider', 'spinbutton']),
  'aria-sort': new Set(['columnheader', 'rowheader']),
  'aria-level': new Set(['heading', 'listitem', 'row', 'treeitem', 'tablist', 'grid', 'comment']),
};

/** Attributes holding id references, and whether every id must resolve
 * (`all`) or at least one (`any`, axe's reading of the labelling ones). */
const IDREF_ATTRS: [string, 'all' | 'any'][] = [
  ['aria-labelledby', 'any'],
  ['aria-describedby', 'any'],
  ['aria-controls', 'all'],
  ['aria-owns', 'all'],
  ['aria-activedescendant', 'all'],
  ['aria-errormessage', 'all'],
  ['aria-details', 'all'],
];

const FOCUSABLE_SELECTOR =
  'a[href], area[href], button, input:not([type="hidden"]), select, textarea, summary, ' +
  '[tabindex], [contenteditable=""], [contenteditable="true"]';

function openTag(el: Element): string {
  const html = el.outerHTML;
  const end = html.indexOf('>');
  const tag = end >= 0 ? html.slice(0, end + 1) : html;
  return tag.length > 200 ? `${tag.slice(0, 197)}...` : tag;
}

/** Focusable in the sequential order or by script. jsdom has no CSS, so
 * `display: none` is read from the `hidden` attribute only. */
function isFocusable(el: Element): boolean {
  if (!el.matches(FOCUSABLE_SELECTOR)) return false;
  if ((el as HTMLButtonElement).disabled) return false;
  if (el.closest('[inert], [hidden]')) return false;
  return true;
}

/** In the tab order: focusable and not tabindex="-1". */
function isTabbable(el: Element): boolean {
  return isFocusable(el) && el.getAttribute('tabindex') !== '-1';
}

/** `getRoles` takes `{ hidden }` at runtime (@testing-library/dom
 * role-helpers.js) though its type declares only the container. */
const getAllRoles = getRoles as (
  container: HTMLElement,
  options: { hidden: boolean },
) => Record<string, HTMLElement[]>;

/** The role every element in `root` exposes (explicit or implicit). */
function roleMap(root: HTMLElement): Map<Element, string> {
  const map = new Map<Element, string>();
  for (const [role, elements] of Object.entries(getAllRoles(root, { hidden: true }))) {
    for (const el of elements) if (!map.has(el)) map.set(el, role);
  }
  return map;
}

function textOfIds(doc: Document, ids: string[]): string {
  return ids
    .map((id) => doc.getElementById(id)?.textContent ?? '')
    .join(' ')
    .trim();
}

/** A form field's name by the native and ARIA labelling routes (used for the
 * fields Testing Library gives no role, such as file and time inputs). */
function fieldHasName(el: HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement): boolean {
  const doc = el.ownerDocument;
  if ((el.getAttribute('aria-label') ?? '').trim() !== '') return true;
  const labelledby = (el.getAttribute('aria-labelledby') ?? '').split(/\s+/).filter(Boolean);
  if (labelledby.length > 0 && textOfIds(doc, labelledby) !== '') return true;
  if (Array.from(el.labels ?? []).some((l) => (l.textContent ?? '').trim() !== '')) return true;
  if ((el.getAttribute('title') ?? '').trim() !== '') return true;
  if ((el.getAttribute('placeholder') ?? '').trim() !== '') return true;
  return false;
}

/** Audit everything under `root` (default: the whole body, so portalled
 * dialogs and toasts are included). Inaccessible subtrees (hidden,
 * aria-hidden, display:none by the `hidden` attribute) are skipped except by
 * aria-hidden-focus, which exists to look inside them. */
export function auditA11y(root: HTMLElement = document.body): A11yViolation[] {
  const out: A11yViolation[] = [];
  const doc = root.ownerDocument;
  const add = (rule: string, impact: A11yViolation['impact'], message: string, el: Element): void => {
    out.push({ rule, impact, message, html: openTag(el) });
  };
  const roles = roleMap(root);
  const all = Array.from(root.querySelectorAll('*'));
  const visible = all.filter((el) => !isInaccessible(el));

  // ---- names --------------------------------------------------------------
  const flagged = new Set<Element>();
  for (const [role, rule, impact] of NAMED_ROLES) {
    const unnamed = queryAllByRole(root, role, { name: (name) => name.trim() === '' });
    for (const el of unnamed) {
      // an <svg aria-hidden> icon never reaches here (inaccessible); an
      // <img alt=""> is presentational, not an unnamed image
      if (role === 'img' && el.tagName === 'IMG' && el.getAttribute('alt') === '') continue;
      flagged.add(el);
      if (role === 'img' && el.tagName === 'IMG') {
        add('image-alt', 'critical', 'img has no alt text', el);
      } else {
        add(rule, impact, `${role} has no accessible name`, el);
      }
    }
  }
  for (const el of visible) {
    if (el instanceof HTMLImageElement && !el.hasAttribute('alt') && !flagged.has(el)) {
      add('image-alt', 'critical', 'img has no alt attribute', el);
    }
    if (
      el instanceof HTMLInputElement &&
      ['button', 'submit', 'reset'].includes(el.type) &&
      (el.value ?? '').trim() === '' &&
      !fieldHasName(el)
    ) {
      add('input-button-name', 'critical', 'input button has no value or label', el);
    }
    if (
      (el instanceof HTMLInputElement && el.type !== 'hidden') ||
      el instanceof HTMLSelectElement ||
      el instanceof HTMLTextAreaElement
    ) {
      if (!flagged.has(el) && !fieldHasName(el)) {
        add('label', 'critical', 'form field has no label', el);
      }
    }
    if (el.tagName.toLowerCase() === 'svg' && el.getAttribute('role') === 'img') {
      const named =
        (el.getAttribute('aria-label') ?? '').trim() !== '' ||
        el.querySelector(':scope > title') !== null ||
        textOfIds(doc, (el.getAttribute('aria-labelledby') ?? '').split(/\s+/).filter(Boolean)) !== '';
      if (!named && !flagged.has(el)) add('svg-img-alt', 'serious', 'svg role="img" has no name', el);
    }
  }

  // ---- ARIA -----------------------------------------------------------------
  for (const el of all) {
    const explicit = el.getAttribute('role');
    if (explicit !== null) {
      const tokens = explicit.trim().split(/\s+/).filter(Boolean);
      if (!tokens.some((t) => VALID_ROLES.has(t))) {
        add('aria-roles', 'critical', `role="${explicit}" is not a valid ARIA role`, el);
      }
    }
    for (const [attr, mode] of IDREF_ATTRS) {
      const raw = el.getAttribute(attr);
      if (raw === null) continue;
      const ids = raw.split(/\s+/).filter(Boolean);
      // a collapsed control may point at content that is not rendered yet
      if (attr === 'aria-controls' && el.getAttribute('aria-expanded') === 'false') continue;
      const missing = ids.filter((id) => doc.getElementById(id) === null);
      if (ids.length === 0 || (mode === 'any' ? missing.length === ids.length : missing.length > 0)) {
        add('aria-valid-attr-value', 'critical', `${attr} references missing id(s): ${missing.join(', ') || '(empty)'}`, el);
      } else if (missing.length > 0) {
        // axe passes a partly dangling labelling reference; it is still a
        // broken description, so it is reported under the same rule
        add('aria-valid-attr-value', 'serious', `${attr} references missing id(s): ${missing.join(', ')}`, el);
      }
    }
  }

  for (const el of visible) {
    // '' for an element with no mapped role (an SVG <g>, a <label>): the SVG
    // ones map to group once named or focusable, so no rule reads them as
    // generic
    const role = roles.get(el) ?? '';
    const explicitRole = el.getAttribute('role');

    // required state on custom widgets (native inputs carry it themselves)
    const native = el instanceof HTMLInputElement || el instanceof HTMLSelectElement;
    if (explicitRole !== null && !native) {
      const need: Record<string, string[]> = {
        checkbox: ['aria-checked'],
        radio: ['aria-checked'],
        switch: ['aria-checked'],
        menuitemcheckbox: ['aria-checked'],
        menuitemradio: ['aria-checked'],
        slider: ['aria-valuenow'],
        scrollbar: ['aria-valuenow', 'aria-controls'],
        heading: ['aria-level'],
        combobox: ['aria-expanded'],
      };
      const required = need[role] ?? [];
      if (role === 'heading' && /^H[1-6]$/.test(el.tagName)) required.length = 0;
      for (const attr of required) {
        if (!el.hasAttribute(attr)) add('aria-required-attr', 'critical', `role=${role} needs ${attr}`, el);
      }
    }

    // state attributes on roles that do not support them
    for (const [attr, allowed] of Object.entries(STATE_ATTR_ROLES)) {
      if (!el.hasAttribute(attr)) continue;
      if (allowed.has(role)) continue;
      // native checkboxes/radios are checkbox/radio; aria-pressed on a native button is fine
      add('aria-allowed-attr', 'critical', `${attr} is not supported on role=${role}`, el);
    }

    // a name on an element whose role prohibits one, or on an HTML element
    // with no role at all (axe: a name is prohibited when there is no role;
    // a <dl> is one). SVG elements are left out: a named or focusable SVG
    // element maps to a role of its own.
    const html = el.namespaceURI === 'http://www.w3.org/1999/xhtml';
    if (
      (NAME_PROHIBITED.has(role) || (role === '' && html)) &&
      (el.hasAttribute('aria-label') || el.hasAttribute('aria-labelledby'))
    ) {
      add(
        'aria-prohibited-attr',
        'serious',
        `aria-label/aria-labelledby on role=${role || '(none)'}: give it a role or use visible text`,
        el,
      );
    }

    // children of presentational-children roles must not be focusable
    if (PRESENTATIONAL_CHILDREN.has(role)) {
      const inner = Array.from(el.querySelectorAll(FOCUSABLE_SELECTOR)).filter(isFocusable);
      if (inner.length > 0) {
        add('nested-interactive', 'serious', `role=${role} contains focusable ${openTag(inner[0])}`, el);
      }
    }

    // ownership: required children and parents
    if (explicitRole !== null) {
      const childNeed: Record<string, string[]> = {
        tablist: ['tab'],
        list: ['listitem'],
        listbox: ['option', 'group'],
        menu: ['menuitem', 'menuitemcheckbox', 'menuitemradio', 'group'],
        menubar: ['menuitem', 'menuitemcheckbox', 'menuitemradio', 'group'],
        radiogroup: ['radio'],
        tree: ['treeitem', 'group'],
      };
      const need = childNeed[role];
      if (need) {
        const owned = Array.from(el.querySelectorAll('*')).some((c) => need.includes(roles.get(c) ?? ''));
        if (!owned) add('aria-required-children', 'critical', `role=${role} owns no ${need.join('/')}`, el);
      }
      const parentNeed: Record<string, string[]> = {
        tab: ['tablist'],
        listitem: ['list', 'directory'],
        option: ['listbox', 'group'],
        menuitem: ['menu', 'menubar', 'group'],
        treeitem: ['tree', 'group'],
        row: ['table', 'grid', 'rowgroup', 'treegrid'],
      };
      const parents = parentNeed[role];
      if (parents) {
        let p = el.parentElement;
        while (p && (roles.get(p) ?? 'generic') === 'generic') p = p.parentElement;
        if (!p || !parents.includes(roles.get(p) ?? '')) {
          add('aria-required-parent', 'critical', `role=${role} is not inside ${parents.join('/')}`, el);
        }
      }
    }

    // positive tabindex
    const tabindex = el.getAttribute('tabindex');
    if (tabindex !== null && Number(tabindex) > 0) {
      add('tabindex', 'serious', `tabindex="${tabindex}" reorders the tab sequence`, el);
    }

    // list structure
    if ((el.tagName === 'UL' || el.tagName === 'OL') && explicitRole === null) {
      for (const child of Array.from(el.children)) {
        if (!['LI', 'SCRIPT', 'TEMPLATE'].includes(child.tagName)) {
          add('list', 'serious', `<${el.tagName.toLowerCase()}> holds a <${child.tagName.toLowerCase()}>`, el);
          break;
        }
      }
    }
    if (el.tagName === 'LI' && explicitRole === null) {
      const parent = el.parentElement;
      const ok =
        parent !== null &&
        (['UL', 'OL', 'MENU'].includes(parent.tagName) || parent.getAttribute('role') === 'list');
      if (!ok) add('listitem', 'serious', '<li> is not inside a list', el);
    }
    if (el.tagName === 'DL') {
      for (const child of Array.from(el.children)) {
        const okDiv =
          child.tagName === 'DIV' &&
          Array.from(child.children).every((g) => ['DT', 'DD', 'SCRIPT', 'TEMPLATE'].includes(g.tagName));
        if (!['DT', 'DD', 'SCRIPT', 'TEMPLATE'].includes(child.tagName) && !okDiv) {
          add('definition-list', 'serious', `<dl> holds a <${child.tagName.toLowerCase()}>`, el);
          break;
        }
      }
    }
    if (el.tagName === 'DT' || el.tagName === 'DD') {
      const parent = el.parentElement;
      const ok =
        parent !== null &&
        (parent.tagName === 'DL' || (parent.tagName === 'DIV' && parent.parentElement?.tagName === 'DL'));
      if (!ok) add('dlitem', 'serious', `<${el.tagName.toLowerCase()}> is not inside a <dl>`, el);
    }
  }

  // aria-hidden content must not be focusable
  for (const el of Array.from(root.querySelectorAll('[aria-hidden="true"]'))) {
    const candidates = [el, ...Array.from(el.querySelectorAll(FOCUSABLE_SELECTOR))];
    const focusable = candidates.find(isTabbable);
    if (focusable) {
      add('aria-hidden-focus', 'serious', `aria-hidden subtree holds focusable ${openTag(focusable)}`, el);
    }
  }

  // duplicate ids: referenced ones (critical), on focusable elements (serious)
  const byId = new Map<string, Element[]>();
  for (const el of Array.from(root.querySelectorAll('[id]'))) {
    const list = byId.get(el.id) ?? [];
    list.push(el);
    byId.set(el.id, list);
  }
  const referenced = new Set<string>();
  for (const el of Array.from(root.querySelectorAll('*'))) {
    for (const [attr] of IDREF_ATTRS) {
      for (const id of (el.getAttribute(attr) ?? '').split(/\s+/).filter(Boolean)) referenced.add(id);
    }
    const htmlFor = el.getAttribute('for');
    if (htmlFor) referenced.add(htmlFor);
  }
  for (const [id, els] of byId) {
    if (els.length < 2) continue;
    if (referenced.has(id)) {
      add('duplicate-id-aria', 'critical', `id "${id}" is used ${els.length} times and referenced`, els[1]);
    } else if (els.some(isFocusable)) {
      add('duplicate-id-active', 'serious', `id "${id}" is used ${els.length} times on focusable elements`, els[1]);
    }
  }

  return out;
}

/** One line per violation, for a readable assertion failure. */
export function formatViolations(violations: A11yViolation[]): string[] {
  return violations.map((v) => `${v.rule} (${v.impact}): ${v.message} — ${v.html}`);
}
