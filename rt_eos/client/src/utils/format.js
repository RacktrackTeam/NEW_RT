/* Words and numbers the way a person reads them. */

export const ROLE_WORD = {
  owner: 'Platform owner',
  org_owner: 'Organization owner',
  org_admin: 'Admin',
  site_manager: 'Site manager',
  member: 'Technician',
};

export const roleWord = (role) => ROLE_WORD[role] || 'Technician';

export const isManager = (role) => role === 'site_manager' || role === 'org_admin' || role === 'org_owner' || role === 'owner';
export const isAdmin = (role) => role === 'org_admin' || role === 'org_owner' || role === 'owner';

export function plural(n, one, many) {
  const k = Number(n) || 0;
  return `${k} ${k === 1 ? one : (many || `${one}s`)}`;
}

function toDate(v) {
  if (v == null || v === '') return null;
  const d = v instanceof Date ? v : new Date(typeof v === 'string' && /^\d{4}-\d\d-\d\d \d/.test(v) ? v.replace(' ', 'T') + 'Z' : v);
  return Number.isFinite(d.getTime()) ? d : null;
}

const pad = (n) => String(n).padStart(2, '0');
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

function sameDay(a, b) {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}

/** "10:42" */
export function timeOf(v) {
  const d = toDate(v);
  return d ? `${pad(d.getHours())}:${pad(d.getMinutes())}` : '';
}

/** "26 Sep 2026" */
export function dateOf(v) {
  const d = toDate(v);
  return d ? `${d.getDate()} ${MONTHS[d.getMonth()]} ${d.getFullYear()}` : '';
}

/** "26 Sep" */
export function shortDate(v) {
  const d = toDate(v);
  return d ? `${d.getDate()} ${MONTHS[d.getMonth()]}` : '';
}

/** "Today", "Yesterday" or "26 Sep", for grouping. */
export function dayLabel(v, now = new Date()) {
  const d = toDate(v);
  if (!d) return 'Earlier';
  if (sameDay(d, now)) return 'Today';
  const y = new Date(now); y.setDate(now.getDate() - 1);
  if (sameDay(d, y)) return 'Yesterday';
  return d.getFullYear() === now.getFullYear() ? shortDate(d) : dateOf(d);
}

/** "10:42" today, "Yesterday", or "26 Sep" - the short meta under a thumbnail. */
export function whenShort(v, now = new Date()) {
  const d = toDate(v);
  if (!d) return '';
  if (sameDay(d, now)) return timeOf(d);
  const label = dayLabel(d, now);
  return label;
}

export function fileSize(bytes) {
  const b = Number(bytes) || 0;
  if (b >= 1024 * 1024) return `${(b / (1024 * 1024)).toFixed(1)} MB`;
  if (b >= 1024) return `${Math.round(b / 1024)} KB`;
  return `${b} bytes`;
}

export function percent(v, digits = 0) {
  if (v == null || !Number.isFinite(Number(v))) return '';
  const n = Number(v) <= 1 ? Number(v) * 100 : Number(v);
  return `${n.toFixed(digits)}%`;
}

export { toDate };

/** The one sentence shown wherever a check needs the Excel and none has been imported for the site. */
export const NO_EXCEL_TEXT = 'No Excel file has been uploaded. Please upload an Excel file to compare and cross-check the physical rack information.';

/** A person's name as shown: the first letter in capitals ("barry" -> "Barry"); the rest as typed. */
export const personName = (v) => { const t = String(v ?? '').trim(); return t ? t.charAt(0).toUpperCase() + t.slice(1) : ''; };
