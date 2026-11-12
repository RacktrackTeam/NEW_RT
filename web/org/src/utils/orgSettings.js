/**
 * Organization settings -  the model, without the screen.
 *
 * The state lives on the server (/api/setup, server/lib/estate.js and the
 * profile sections beside it). What that state MEANS -  which step is done,
 * what is still required, which step to open first -  is decided here, once,
 * so the stepper, the review and the tests cannot disagree.
 *
 * Five steps since 22 Sep 2026: the organization, its sites (each with a
 * location and a single point of contact who gets an account), the rules,
 * the review, and a closing step that thanks the person and closes.
 *
 * A site is a Site: the console calls it a Site, the server calls it a
 * tenant, and the older code and routes here call it a datacentre.
 *
 * model = { org: the organization profile or null, dcs: [site] }
 * site = the GET /api/setup/:id snapshot, flattened by the hook:
 *   { id, name, organization_id, datacentre, approver, rules,
 *     completeness, profile }
 */

/* -- The three facts the first photo needs, as the server reports them -- */
export const MANDATORY = [
  { key: 'location', step: 'sites', short: 'Location', need: 'a location' },
  { key: 'approver', step: 'sites', short: 'SPOC', need: 'a SPOC with an account' },
  { key: 'rules', step: 'rules', short: 'Rules accepted', need: 'the rules accepted' },
];

/* -- The steps, in order ----------------------------------------------
   `kind` says what a step is to the flow: work that must be complete before
   the next step (required), or the closing step (done). `needsDc` marks a
   step with nothing to show until a site exists, so it is locked rather
   than opened empty. `lead` is the one help line under the step title.

   The review is required too: the closing step says setup is done, so it is
   not reached while something is still missing. */
export const STEPS = [
  // No lead: the fields below are labelled, so listing them above was the
  // screen reading itself out.
  { key: 'org', label: 'Organization', title: 'Organization', kind: 'required', lead: '' },
  { key: 'sites', label: 'Sites', title: 'Sites', kind: 'required', lead: 'Each place that holds racks, and the person in charge there.' },
  { key: 'rules', label: 'Rules', title: 'Rules', kind: 'required', needsDc: true, lead: '' },
  { key: 'review', label: 'Review', title: 'Review', kind: 'required', lead: '' },
  { key: 'done', label: 'Done', title: 'Thank you', kind: 'done', lead: '' },
];
export const STEP_KEYS = STEPS.map((s) => s.key);
export const stepOf = (key) => STEPS.find((s) => s.key === key) || null;

/* -- What is done ----------------------------------------------------- */
const has = (v) => (typeof v === 'string' ? v.trim().length > 0 : v != null && v !== '');

/* The short code is not here: the server derives it from the name the first
   time the organization is read, so it is never something a person owes. */
export const ORG_NEEDED = [
  { key: 'name', label: 'Name' }, { key: 'timezone', label: 'Time zone' }, { key: 'country', label: 'Country' },
];
export const orgMissing = (p) => ORG_NEEDED.filter((f) => !has(p?.[f.key]));
export const orgComplete = (p) => orgMissing(p).length === 0;

export const dcAddressed = (dc) => has(dc?.datacentre?.address) && has(dc?.datacentre?.timezone);
/* The SPOC is the site's approver on the server: the account made for them
   in the Sites step is what the approver fact points at. */
export const dcHasSpoc = (dc) => !!dc?.completeness?.mandatory?.approver || !!dc?.approver;
export const dcRulesAccepted = (dc) => !!dc?.completeness?.mandatory?.rules || !!dc?.rules?.accepted_at;

/** The site's single point of contact as the contacts section holds it. */
export const spocOf = (dc) => (dc?.profile?.contacts || []).find((c) => c.role === 'spoc') || null;

/** A site is ready when it says where it is and who its SPOC is. */
export const siteReady = (dc) => dcAddressed(dc) && dcHasSpoc(dc);

export function stepDone(step, model) {
  const dcs = model?.dcs || []; const any = dcs.length > 0;
  switch (step) {
    case 'org': return orgComplete(model?.org);
    case 'sites': return any && dcs.every(siteReady);
    case 'rules': return any && dcs.every(dcRulesAccepted);
    case 'review': case 'done': { const p = progress(model); return p.required.done === p.required.total; }
    default: return false;
  }
}
export const stepLocked = (step, model) => !!stepOf(step)?.needsDc && !(model?.dcs || []).length;

/** The mandatory items, each with what is done. `optional` stays in the
 *  shape for the callers that read it; setup has no optional step now. */
export function progress(model) {
  const dcs = model?.dcs || []; const any = dcs.length > 0;
  const each = (one, many) => (dcs.length > 1 ? many : one);
  const required = [
    { key: 'org', step: 'org', label: 'Organization profile', done: orgComplete(model?.org) },
    { key: 'sites', step: 'sites', label: each('A site with a location', 'A location for every site'), done: any && dcs.every(dcAddressed) },
    { key: 'spoc', step: 'sites', label: each('A SPOC with an account', 'A SPOC with an account at every site'), done: any && dcs.every(dcHasSpoc) },
    { key: 'rules', step: 'rules', label: 'Rules accepted', done: any && dcs.every(dcRulesAccepted) },
  ];
  const count = (xs) => ({ items: xs, done: xs.filter((x) => x.done).length, total: xs.length });
  return { required: count(required), optional: count([]) };
}

export const mandatoryDone = (model) => { const p = progress(model); return p.required.done === p.required.total; };

/** What is still to do, by step: the list the settings view shows. */
export function remaining(model) {
  const p = progress(model);
  return {
    required: p.required.items.filter((x) => !x.done),
    optional: p.optional.items.filter((x) => !x.done),
  };
}

/* The one-line address the site record holds, composed from the facility
   section so the two never disagree. */
export function composeAddress(f) {
  const parts = [f?.address_line1, f?.address_line2, f?.city, f?.region, f?.postcode, f?.country ? countryName(f.country) : null]
    .map((x) => (x == null ? '' : String(x).trim())).filter(Boolean);
  return parts.join(', ');
}

/** The first step that still has required work; the review when none does.
 *  Never the closing step: that one is reached by pressing Next. */
export function firstIncompleteStep(model) {
  const s = STEPS.find((x) => x.kind === 'required' && !stepDone(x.key, model));
  return s ? s.key : 'review';
}

/** The state a step is drawn in: on, done, locked, todo. */
export function stepState(step, model, current) {
  if (step === current) return 'on';
  if (stepLocked(step, model)) return 'locked';
  if (stepDone(step, model)) return 'done';
  return 'todo';
}

/* -- Time zones and countries ---------------------------------------- */
export function browserTimezone() {
  try { return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'; } catch { return 'UTC'; }
}

/* Every zone this browser knows, plus whatever the record already holds:
   the server accepts aliases (Asia/Calcutta) the canonical list leaves out,
   and a select must be able to show the value it was given. */
let zones = null;
export function timezones(...extra) {
  if (!zones) {
    try { zones = typeof Intl.supportedValuesOf === 'function' ? Intl.supportedValuesOf('timeZone') : []; } catch { zones = []; }
  }
  const set = new Set(zones); set.add('UTC'); extra.filter(Boolean).forEach((z) => set.add(z));
  return [...set].sort();
}

/* Every ISO 3166-1 alpha-2 region, named by the browser in English. The code
   is what is stored; the name is only for the select. Never guessed: a
   country is chosen by a person. */
const CODES = 'AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ BR BS BT BV BW BY BZ CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM HN HR HT HU ID IE IL IM IN IO IQ IR IS IT JE JM JO JP KE KG KH KI KM KN KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML MM MN MO MP MQ MR MS MT MU MV MW MX MY MZ NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM PN PR PS PT PW PY QA RE RO RS RU RW SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ TC TD TF TG TH TJ TK TL TM TN TO TR TT TV TW TZ UA UG UM US UY UZ VA VC VE VG VI VN VU WF WS YE YT ZA ZM ZW'.split(' ');
let countryList = null;
export function countries(...extra) {
  if (!countryList) {
    let dn = null;
    try { dn = typeof Intl.DisplayNames === 'function' ? new Intl.DisplayNames(['en'], { type: 'region' }) : null; } catch { dn = null; }
    const name = (c) => { try { return (dn && dn.of(c)) || c; } catch { return c; } };
    countryList = CODES.map((code) => ({ code, name: name(code) })).sort((a, b) => a.name.localeCompare(b.name));
  }
  const known = new Set(countryList.map((c) => c.code));
  const more = extra.filter((c) => c && !known.has(c)).map((code) => ({ code, name: code }));
  return more.length ? [...countryList, ...more] : countryList;
}
export const countryName = (code) => (code ? (countries().find((c) => c.code === code)?.name || code) : '');

/* -- Field checks ---------------------------------------------------
   Each returns a message to show beside the field, or null. Empty is fine
   unless the caller says required: the sections decide what is mandatory,
   these decide what is well formed. */
const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/;
const USERNAME = /^[A-Za-z0-9._-]{3,32}$/;

export const required = (v, msg = 'This is needed') => (v == null || String(v).trim() === '' ? msg : null);
export const vEmail = (v) => (!v ? null : EMAIL.test(String(v).trim()) ? null : 'Not a valid email address');
export const vPhone = (v) => (!v ? null : /^[+\d][\d\s().-]{5,24}$/.test(String(v).trim()) ? null : 'Not a valid phone number');
export const vUsername = (v) => (!v ? null : USERNAME.test(String(v).trim()) ? null : '3 to 32 letters, digits, dots, underscores or hyphens');
/* The same rule, in the same words, as the server's validatePassword. */
export function vPassword(v) {
  if (!v) return null;
  if (String(v).length < 8) return 'Password must be at least 8 characters';
  if (!/[A-Z]/.test(v)) return 'Password must contain an uppercase letter';
  if (!/[a-z]/.test(v)) return 'Password must contain a lowercase letter';
  if (!/[0-9]/.test(v)) return 'Password must contain a digit';
  if (!/[^A-Za-z0-9]/.test(v)) return 'Password must contain a special character';
  return null;
}

/* A user name proposed from a person's name or email: "Priya Nair" gives
   priya.nair, "ops@acme.example" gives ops. Offered, never forced: the
   person creating the account can type over it. */
export function proposeUsername(name, email) {
  const fromName = String(name || '').toLowerCase().replace(/[^a-z0-9\s._-]/g, ' ').trim().split(/\s+/).filter(Boolean).join('.');
  const fromMail = String(email || '').split('@')[0].toLowerCase().replace(/[^a-z0-9._-]/g, '');
  const u = (fromName.length >= 3 ? fromName : fromMail).slice(0, 32).replace(/^[._-]+|[._-]+$/g, '');
  return u.length >= 3 ? u : '';
}

/* A record from the server says null where a field is empty; a controlled
   input wants ''. */
export const blank = (o) => Object.fromEntries(Object.entries(o || {}).map(([k, v]) => [k, v === null ? '' : v]));

export const plural = (n, one) => `${Number(n) || 0} ${Number(n) === 1 ? one : `${one}s`}`;

export function fmtDate(s) {
  if (!s) return '';
  try {
    const d = typeof s === 'number' ? new Date(s) : new Date(String(s).replace(' ', 'T') + (/[zZ]|[+-]\d\d:?\d\d$/.test(String(s)) ? '' : 'Z'));
    if (Number.isNaN(d.getTime())) return String(s);
    return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
  } catch { return String(s); }
}
export function fmtTime(d) {
  try { return new Date(d).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' }); } catch { return ''; }
}

/* -- The review's one line per step --------------------------------- */
export function summaryOf(key, model) {
  const org = model?.org || {}; const dcs = model?.dcs || [];
  const names = (xs) => xs.map((d) => d.name).join(', ');
  switch (key) {
    case 'org': {
      const miss = orgMissing(org);
      return miss.length ? `Missing ${miss.map((m) => m.label.toLowerCase()).join(', ')}` : [org.name, countryName(org.country), org.timezone].filter(Boolean).join(', ');
    }
    case 'sites': {
      if (!dcs.length) return 'No site yet';
      return dcs.map((d) => {
        const who = spocOf(d)?.name || d.approver?.username || d.approver?.email || null;
        const gaps = [dcAddressed(d) ? null : 'no location', dcHasSpoc(d) ? null : 'no SPOC account'].filter(Boolean);
        return `${d.name}: ${gaps.length ? gaps.join(', ') : `SPOC ${who || 'set'}`}`;
      }).join('; ');
    }
    case 'rules': {
      if (!dcs.length) return 'No site yet';
      const bad = dcs.filter((d) => !dcRulesAccepted(d));
      return bad.length ? `Not accepted for ${names(bad)}` : 'Accepted';
    }
    default: return '';
  }
}
