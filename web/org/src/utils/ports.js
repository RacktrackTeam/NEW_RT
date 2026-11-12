/* What a port's status is called, and how a cable end is written. */

export const PORT_STATUS = {
  matched: { word: 'Matched', tone: 'ok' },
  wrong_port: { word: 'Wrong port', tone: 'bad' },
  missing_cable: { word: 'Missing cable', tone: 'bad' },
  not_in_sheet: { word: 'Not in sheet', tone: 'warn' },
  not_found: { word: 'Not found', tone: 'warn' },
  uncapped: { word: 'No dust cap', tone: 'warn' },
  optic_no_cap: { word: 'Optic fitted, cap off', tone: 'warn' },
  connected: { word: 'Plugged in', tone: '' },
  empty: { word: 'Empty', tone: '' },
  unknown: { word: 'Not clear on the photo', tone: '' },
};

export const portStatusWord = (s) => PORT_STATUS[s]?.word || (s ? String(s).replace(/_/g, ' ') : '');
export const portTone = (s) => PORT_STATUS[s]?.tone || '';

/** The legend for the cells, in the order a person reads a problem. */
export const LEGEND_WITH_SHEET = ['matched', 'wrong_port', 'missing_cable', 'not_in_sheet', 'not_found', 'uncapped', 'optic_no_cap', 'empty', 'unknown']
  .map((kind) => ({ kind, word: PORT_STATUS[kind].word }));
export const LEGEND_NO_SHEET = ['connected', 'empty', 'uncapped', 'optic_no_cap', 'unknown']
  .map((kind) => ({ kind, word: PORT_STATUS[kind].word }));

export const PROBLEMS = new Set(['wrong_port', 'missing_cable', 'not_in_sheet', 'uncapped', 'optic_no_cap']);

/**
 * The two dust-cap marks a person puts on a port, in words, or ''.
 *   uncapped      an empty port with its dust cap off
 *   opticNoCap    an optic fitted (staged, no cable) without a dust cap
 */
export function capWords(p) {
  if (p?.uncapped) return `No dust cap: empty optical port${p.uncapped.markedBy ? `, marked by ${p.uncapped.markedBy}` : ''}`;
  if (p?.opticNoCap) return `Optic fitted without a dust cap${p.opticNoCap.markedBy ? `, marked by ${p.opticNoCap.markedBy}` : ''}`;
  return '';
}

/** "PP-B01 port 1, R02" */
export function endText(end) {
  if (!end) return '';
  const where = [end.device, end.port != null && end.port !== '' ? `port ${end.port}` : null].filter(Boolean).join(' ');
  return [where, end.rack].filter(Boolean).join(', ');
}

/** "RJ-45, White" */
export const cableText = (p) => [p?.cableConnector, p?.cableColor].filter(Boolean).join(', ');

/** Add the word to each port so the cells can say it. */
export const withWords = (ports = []) => ports.map((p) => ({ ...p, statusWord: portStatusWord(p.status) }));
