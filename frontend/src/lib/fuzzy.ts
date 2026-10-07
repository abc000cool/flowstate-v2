/** Fuzzy matching for the command palette (docs/design/DASHBOARD_DESIGN.md
 * §9.19). Case-insensitive; the query is split on whitespace and every word
 * must match, in the label or, failing that, in the command's keywords.
 *
 * A word scores highest as a prefix of the label, then at the start of a
 * word in it, then anywhere in it, then as a subsequence ("gtr" in "Go to
 * Reports"); the same match in the keywords counts for less and highlights
 * nothing. Pure and deterministic, so the ranking is unit-tested.
 *
 * A word that is a subsequence of the text always matches. Among its
 * alignments the one that scores best is chosen (letters on word starts,
 * then a tight span), so "goto" finds "Go to Onboard corridor" even though
 * its second "o" could start "onboard": a greedy jump to the next word start
 * would strand the "t" it still needs. */

export interface FuzzyMatch {
  score: number;
  /** Positions in the label to highlight, ascending, no duplicates. */
  indices: number[];
}

const WORD_BREAK = /[\s\-_/·:.(),]/;

function isWordStart(text: string, i: number): boolean {
  return i === 0 || WORD_BREAK.test(text[i - 1]);
}

/** One query word against one text (both lower-case): a score and the
 * matched positions, or null. */
function matchWord(word: string, text: string): FuzzyMatch | null {
  if (word === '') return { score: 0, indices: [] };
  // contiguous: prefer a word start over the first occurrence
  let at = -1;
  for (let i = text.indexOf(word); i !== -1; i = text.indexOf(word, i + 1)) {
    if (isWordStart(text, i)) {
      at = i;
      break;
    }
    if (at === -1) at = i;
  }
  if (at !== -1) {
    const indices = Array.from({ length: word.length }, (_, k) => at + k);
    let score = 60 - Math.min(at, 30);
    if (at === 0) score += 40;
    else if (isWordStart(text, at)) score += 25;
    if (word.length === text.length) score += 20;
    return { score, indices };
  }
  return matchSubsequence(word, text);
}

/** Extra score per subsequence letter that lands on a word start; each letter
 * skipped inside the span costs one. */
const WORD_START_BONUS = 4;

/** `word` as a (non-contiguous) subsequence of `text`, both lower-case: the
 * alignment with the best score, or null when `word` is not a subsequence.
 *
 * The score is `30 − (span − |word|) + 4 · word starts`, and `span − 1` is
 * the sum of the steps between consecutive matched letters, so the score
 * splits per letter and a dynamic programme over (letter, position) finds the
 * best alignment exactly, in O(|word| · |text|). Ties go to the earliest
 * positions. */
function matchSubsequence(word: string, text: string): FuzzyMatch | null {
  const m = word.length;
  const n = text.length;
  // best[k][i]: the highest `bonus − (i − first)` over alignments of
  // word[0..k] whose letter k sits at text[i]; -Infinity where none exists.
  // from[k][i]: where letter k − 1 sits in that alignment.
  const best: number[][] = [];
  const from: number[][] = [];
  for (let k = 0; k < m; k++) {
    const row = new Array<number>(n).fill(-Infinity);
    const back = new Array<number>(n).fill(-1);
    const prev = k > 0 ? best[k - 1] : null;
    // max over j < i of prev[j] + j, and the earliest j that reaches it
    let carry = -Infinity;
    let carryAt = -1;
    for (let i = 0; i < n; i++) {
      if (prev !== null && i > 0 && prev[i - 1] + (i - 1) > carry) {
        carry = prev[i - 1] + (i - 1);
        carryAt = i - 1;
      }
      if (text[i] !== word[k]) continue;
      const bonus = isWordStart(text, i) ? WORD_START_BONUS : 0;
      if (prev === null) {
        row[i] = bonus;
      } else if (carryAt !== -1) {
        row[i] = carry - i + bonus;
        back[i] = carryAt;
      }
    }
    best.push(row);
    from.push(back);
  }
  let end = -1;
  for (let i = 0; i < n; i++) {
    if (best[m - 1][i] > (end === -1 ? -Infinity : best[m - 1][end])) end = i;
  }
  if (end === -1) return null;
  const indices = new Array<number>(m);
  for (let k = m - 1, i = end; k >= 0; k--) {
    indices[k] = i;
    i = from[k][i];
  }
  const span = indices[m - 1] - indices[0] + 1;
  const starts = indices.filter((i) => isWordStart(text, i)).length;
  const score = Math.max(1, 30 - (span - m) + WORD_START_BONUS * starts);
  return { score, indices };
}

/** Score `query` against a command's `label` and optional `keywords`; null
 * when some word of the query matches neither. An empty query matches
 * everything with score 0. */
export function fuzzyMatch(query: string, label: string, keywords = ''): FuzzyMatch | null {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  if (words.length === 0) return { score: 0, indices: [] };
  const lowerLabel = label.toLowerCase();
  const lowerKeywords = keywords.toLowerCase();
  let score = 0;
  const marked = new Set<number>();
  for (const word of words) {
    const inLabel = matchWord(word, lowerLabel);
    const inKeywords = lowerKeywords === '' ? null : matchWord(word, lowerKeywords);
    if (inLabel && (!inKeywords || inLabel.score >= inKeywords.score / 2)) {
      score += inLabel.score;
      for (const i of inLabel.indices) marked.add(i);
    } else if (inKeywords) {
      score += inKeywords.score / 2;
    } else {
      return null;
    }
  }
  return { score, indices: [...marked].sort((a, b) => a - b) };
}

/** `label` split into runs of matched and unmatched characters, for
 * rendering the highlight. */
export function highlightSegments(
  label: string,
  indices: number[],
): { text: string; match: boolean }[] {
  const marked = new Set(indices);
  const out: { text: string; match: boolean }[] = [];
  for (let i = 0; i < label.length; i++) {
    const match = marked.has(i);
    const last = out[out.length - 1];
    if (last && last.match === match) last.text += label[i];
    else out.push({ text: label[i], match });
  }
  return out;
}
