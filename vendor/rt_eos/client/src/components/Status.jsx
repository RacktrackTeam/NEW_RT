import styles from './Status.module.css';

/**
 * The status words, the same everywhere: coloured text in lists and
 * coloured outlines on pictures, never pills.
 */
export const STATUS = {
  matched: { word: 'Matched', tone: 'ok' },
  on_tag: { word: 'Printed on the tag', tone: 'ok' },
  ok: { word: 'Matched', tone: 'ok' },
  wrong_port: { word: 'Wrong port', tone: 'bad' },
  missing_cable: { word: 'Missing cable', tone: 'bad' },
  wrong_unit: { word: 'Wrong unit', tone: 'bad' },
  not_seen: { word: 'Not seen', tone: 'warn' },
  failed: { word: 'Failed', tone: 'bad' },
  not_in_sheet: { word: 'Not in sheet', tone: 'warn' },
  not_found: { word: 'Not found', tone: 'warn' },
  uncapped: { word: 'No dust cap', tone: 'warn' },
  cap_unsure: { word: 'Check cap', tone: 'warn' },
  optic_no_cap: { word: 'Optic fitted, cap off', tone: 'warn' },
  wrong_type: { word: 'Wrong port type', tone: 'warn' },
  check: { word: 'Check', tone: 'warn' },
  connected: { word: 'Cable plugged in', tone: '' },
  empty: { word: 'Empty', tone: '' },
  unknown: { word: 'Not clear on the photo', tone: '' },
  different: { word: 'Different', tone: 'warn' },
};

export function statusOf(kind) {
  const k = String(kind || '').toLowerCase().replace(/[\s-]+/g, '_');
  return STATUS[k] || { word: kind ? String(kind) : '', tone: '' };
}

export const toneOf = (kind) => statusOf(kind).tone;

export function Status({ kind, children, className }) {
  const s = statusOf(kind);
  const cls = [styles.st, s.tone && styles[s.tone], className].filter(Boolean).join(' ');
  return <span className={cls}>{children || s.word}</span>;
}

export default Status;
