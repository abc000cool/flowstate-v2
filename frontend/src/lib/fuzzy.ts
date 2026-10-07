/** Fuzzy matching for the command palette (docs/design/DASHBOARD_DESIGN.md
 * §9.19). Case-insensitive; the query is split on whitespace and every word
 * must match, in the label or, failing that, in the command's keywords.
 *
 * A word scores highest as a prefix of the label, then at the start of a
 * word in it, then anywhere in it, then as a subsequence ("gtr" in "Go to
 * Reports"); the same match in the keywords counts for less and highlights
 * nothing. Pure and deterministic, so the ranking is unit-tested. */

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
  // subsequence: greedy, but jump to the next word start when it fits
  const indices: number[] = [];
  let from = 0;
  for (const ch of word) {
    let i = text.indexOf(ch, from);
    if (i === -1) return null;
    for (let j = i; j !== -1; j = text.indexOf(ch, j + 1)) {
      if (isWordStart(text, j)) {
        i = j;
        break;
      }
    }
    indices.push(i);
    from = i + 1;
  }
  const span = indices[indices.length - 1] - indices[0] + 1;
  const starts = indices.filter((i) => isWordStart(text, i)).length;
  const score = Math.max(1, 30 - (span - word.length) + 4 * starts);
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
