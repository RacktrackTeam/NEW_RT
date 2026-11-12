/**
 * Matching a label the camera read against the labels in the Excel dataset,
 * with the misreadings a camera makes allowed for (Barry's key test).
 *
 * Three ways a reading can match one dataset label, tried in this order:
 *
 *   exact       the same letters and digits once case and separators (spaces,
 *               dashes, underscores, dots, slashes) are set aside:
 *               "fc-001" and "FC 001" are FC-001.
 *   normalised  the same once the usual camera confusions are treated as one
 *               character: O and 0, I, 1 and L, S and 5, B and 8, Z and 2,
 *               G and 6. "RO1" is R01, "CAB-O1O3" is CAB-0103.
 *   one_edit    one character substituted, added or dropped (Levenshtein 1),
 *               on labels of 6 or more letters and digits, and only when
 *               exactly one dataset label is that close.
 *   tail        one is the end of the other, on 8 or more letters and
 *               digits, and only when exactly one dataset label fits: the
 *               flag prints "nvs-sgkc-sis-sw05b Gi1-0-14 B12" and the sheet
 *               holds "sw05b Gi1-0-14" (or the other way round, when the
 *               camera cut the start of the flag off).
 *
 * A reading that could be two dataset labels is refused: two labels equal
 * once normalised, two labels one edit away, or two labels sharing the
 * tail. It is never guessed.
 *
 * Only the comparison uses these forms. The text read and the dataset's text
 * are kept as they are; a match carries the dataset's own text and says how
 * it was made, so a screen can say "matched after correcting the reading".
 */
'use strict';

const ONE_EDIT_MIN_LENGTH = 6;
const TAIL_MIN_LENGTH = 8;

// The camera's usual confusions, each group written as its digit.
const CONFUSED = { O: '0', I: '1', L: '1', S: '5', B: '8', Z: '2', G: '6' };

/** Upper case, letters and digits only. */
function plain(s) {
  return String(s ?? '').toUpperCase().replace(/[^A-Z0-9]/g, '');
}

/** The comparison form: plain(), then each confusable letter as its digit. */
function canon(s) {
  let out = '';
  for (const ch of plain(s)) out += CONFUSED[ch] || ch;
  return out;
}

/** True when a and b are exactly one substitution, insertion or deletion apart. */
function oneEdit(a, b) {
  if (a === b) return false;
  const la = a.length;
  const lb = b.length;
  if (Math.abs(la - lb) > 1) return false;
  if (la === lb) {
    let wrong = 0;
    for (let i = 0; i < la; i += 1) if (a[i] !== b[i] && (wrong += 1) > 1) return false;
    return wrong === 1;
  }
  const [s, l] = la < lb ? [a, b] : [b, a];
  let i = 0;
  let j = 0;
  let skipped = false;
  while (i < s.length && j < l.length) {
    if (s[i] === l[j]) { i += 1; j += 1; continue; }
    if (skipped) return false;
    skipped = true;
    j += 1;
  }
  return true;
}

/**
 * True when a and b are at most k substitutions, insertions or deletions
 * apart (Levenshtein within k): the camera reads mvs-s9kc-wan-n05 for
 * nvs-sgkc-wan-n05, two characters off on a sixteen-character name.
 */
function editsWithin(a, b, k) {
  if (Math.abs(a.length - b.length) > k) return false;
  let prev = Array.from({ length: b.length + 1 }, (_, j) => j);
  for (let i = 1; i <= a.length; i += 1) {
    const cur = [i];
    let best = i;
    for (let j = 1; j <= b.length; j += 1) {
      cur[j] = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1));
      if (cur[j] < best) best = cur[j];
    }
    if (best > k) return false;
    prev = cur;
  }
  return prev[b.length] <= k;
}

/**
 * A matcher over the dataset's labels.
 *   entries: iterable of [datasetText, value]. Texts equal once plain() are
 *   one label (the first value given is kept).
 * -> { match(text) -> { key, text, value, how, read } | null, why(text) -> string | null, size }
 *   key   plain() of the dataset text (what callers compare on)
 *   text  the dataset's own text
 *   how   'exact' | 'normalised' | 'one_edit' | 'tail'
 */
function createMatcher(entries, { oneEditMinLength = ONE_EDIT_MIN_LENGTH, tailMinLength = TAIL_MIN_LENGTH } = {}) {
  const byPlain = new Map(); // plain -> { key, text, value, canon }
  const byCanon = new Map(); // canon -> [plain keys]
  const byLength = new Map(); // canon length -> [plain keys]
  for (const [text, value] of entries || []) {
    const key = plain(text);
    if (!key || byPlain.has(key)) continue;
    const c = canon(text);
    byPlain.set(key, { key, text: String(text).trim(), value, canon: c });
    if (!byCanon.has(c)) byCanon.set(c, []);
    byCanon.get(c).push(key);
    if (!byLength.has(c.length)) byLength.set(c.length, []);
    byLength.get(c.length).push(key);
  }

  // Several dataset texts may name one thing (a device's name, its ID and
  // the label printed on it): candidates that all carry the same value are
  // one answer, never a tie.
  const oneValue = (keys) => keys.length > 1 && keys.every((k) => byPlain.get(k).value === byPlain.get(keys[0]).value);

  const answer = (key, how, read) => {
    const e = byPlain.get(key);
    return { key, text: e.text, value: e.value, how, read: String(read ?? '').trim() };
  };

  // { hit } or { refused: reason } or {}
  function look(read) {
    const p = plain(read);
    if (!p) return {};
    if (byPlain.has(p)) return { hit: answer(p, 'exact', read) };
    const c = canon(read);
    const same = byCanon.get(c) || [];
    if (same.length === 1 || oneValue(same)) return { hit: answer(same[0], 'normalised', read) };
    if (same.length > 1) {
      return { refused: `"${String(read).trim()}" could be ${same.map((k) => byPlain.get(k).text).join(' or ')} once the reading is corrected, so neither is taken` };
    }
    if (c.length < oneEditMinLength) return {};
    const near = [];
    for (const len of [c.length - 1, c.length, c.length + 1]) {
      for (const k of byLength.get(len) || []) {
        const e = byPlain.get(k);
        if (e.key.length < oneEditMinLength) continue;
        if (oneEdit(c, e.canon)) near.push(k);
      }
    }
    if (near.length === 1 || oneValue(near)) return { hit: answer(near[0], 'one_edit', read) };
    if (near.length > 1) {
      return { refused: `"${String(read).trim()}" is one character from ${near.length} labels (${near.slice(0, 4).map((k) => byPlain.get(k).text).join(', ')}), so none is taken` };
    }
    if (c.length < tailMinLength) return {};
    const tails = [];
    for (const [k, e] of byPlain) {
      if (e.canon.length < tailMinLength || e.canon === c) continue;
      if (e.canon.endsWith(c) || c.endsWith(e.canon)) tails.push(k);
    }
    if (tails.length === 1 || oneValue(tails)) return { hit: answer(tails[0], 'tail', read) };
    if (tails.length > 1) {
      return { refused: `"${String(read).trim()}" ends ${tails.length} labels (${tails.slice(0, 4).map((k) => byPlain.get(k).text).join(', ')}), so none is taken` };
    }
    return {};
  }

  return {
    size: byPlain.size,
    match: (read) => look(read).hit || null,
    why: (read) => look(read).refused || null,
    has: (read) => byPlain.has(plain(read)),
  };
}

/** The words a screen or report shows for how a label matched; null for an exact match. */
function howWords(how) {
  if (how === 'normalised') return 'matched after correcting the reading';
  if (how === 'one_edit') return 'matched after correcting the reading (one character)';
  if (how === 'two_edits') return 'matched after correcting the reading (two characters)';
  if (how === 'tail') return 'matched on the part printed';
  if (how === 'other_face') return 'port from the other face of its tag';
  if (how === 'only_cable') return 'port from the only Excel cable on that device not yet seen';
  return null;
}

module.exports = { plain, canon, oneEdit, editsWithin, createMatcher, howWords, ONE_EDIT_MIN_LENGTH, TAIL_MIN_LENGTH };
