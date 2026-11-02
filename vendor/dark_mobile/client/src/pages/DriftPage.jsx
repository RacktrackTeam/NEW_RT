import { useCallback, useEffect, useMemo, useState } from 'react';
import { useLocation, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { BackIcon } from '../components/BackButton.jsx';
import { apiUrl, authFetch } from '../utils/api';
import { useApprovalsCan } from '../hooks/useApprovalsCan.js';
import { useSmartBack } from '../hooks/useSmartBack';
import ExternalLink from '../components/ExternalLink.jsx';
import PortsCheck from '../components/PortsCheck.jsx';
import TaskAsk from '../components/TaskAsk.jsx';
import { PortHistoryContent } from './PortHistoryPage.jsx';
import ReportViewer from '../components/ReportViewer.jsx';
import { driftCheckUrl, driftReportUrl } from '../utils/approvals.js';
import styles from './DriftPage.module.css';

/**
 * Drift check: the technician's screen.
 *
 * The person standing at the rack is not the person who changes the record, so
 * this screen deliberately has no write button and never says "export". It
 * answers one question - does the rack match what NetBox says - and then hands
 * the answer to the SPOC of the site, by name. Where the site has no SPOC, or
 * the person sending IS the SPOC, it says before anything is sent that an admin
 * will choose who decides it.
 *
 * After it is sent, this page becomes the technician's window on what happened
 * next: the ServiceNow incident raised for it, large, and one line under it -
 * who holds the check, whether it was approved, whether the record was updated. Somebody who
 * walks a rack deserves to know whether their work landed.
 *
 * The SPOC's side is not in this app. It is RackTrack Approvals, on its own
 * address, and once a check is sent this page links to that check there.
 *
 * Everything the newer server adds - who it goes to, the holder, the incident,
 * the ports - is optional here: against a server that says none of it the page
 * still compares, sends and follows.
 */

const WORD = {
  create: 'Not in your records',
  update: 'Different from your records',
  rebind: 'Listed under an older entry',
};

// What each of those means, in one plain sentence. The short word is the
// headline; this is the line under it; anything longer sits behind Read more.
const MEANS = {
  create: 'We saw this in the rack, but your records do not list it on this shelf.',
  update: 'Your records list this, but some details are different.',
  rebind: 'Your records list this under an older entry.',
};

// "Router U20 SP-HYB-RM01-R01-R1" is how the comparison names a box. A person
// standing at the rack already knows which rack it is, so the rack's name comes
// off the end and the shelf is said in words.
function plainName(name, rackName) {
  let out = String(name || '');
  if (rackName && out.endsWith(rackName)) out = out.slice(0, -rackName.length).trim();
  const m = out.match(/^(.+?)\s+U(\d{1,2})$/);
  return m ? `${m[1]} on shelf U${Number(m[2])}` : out;
}

// What becomes of a check after it is sent, said in one line under its incident
// number: who has it, or what was decided. It used to be a four-step tracker
// (Sent - With the SPOC - Approved - Written) with notes under the steps; the
// owner asked for the number and one line, so the same words of the workflow
// now choose that line. Everything the SPOC does with a check - looking, putting
// it on hold, sending it back - is "With <name>" to the person who sent it.
const APPROVED = ['approved', 'write_in_progress'];
const WRITTEN = ['written', 'completed'];
// Where a check has left that line. `word` is set apart, because it is the one
// thing the person needs: it was rejected, or it needs an admin. The few ends
// that have no such word keep their sentence.
const OFF_TRACK = {
  triage: { word: 'Needs an admin' },
  rejected: { word: 'Rejected' },
  cancelled: { text: 'This check was cancelled.' },
  duplicate: { text: 'This was the same as another check, so it was closed.' },
  known_exception: { text: 'This is a known exception, so nothing needs to change.' },
  write_failed: { word: 'Needs an admin' },
  manual_review: { word: 'Needs an admin' },
};
// A plan the server still calls open or draft has not been sent. Every other
// word - sent, written, rejected, a write that failed - is a check to follow.
const isSent = (p) => !!p && !!p.status && !['open', 'draft'].includes(p.status);

// RackTrack's own bookkeeping, not a difference between the rack and the
// record. When a person has said which record a rack is, the plan carries one
// more line: write RackTrack's id onto that record so the next scan finds it
// at once. The record itself is untouched - its name, site, height and shelves
// stay exactly as the customer has them - and NetBox had no id there to begin
// with. The owner opened this screen and read "1 thing does not match" over a
// rack that matched in every way that matters, with two internal keys under
// it; that line is a note now, and it is never counted.
//
// One such line is NOT bookkeeping: when the SPOC accepts that a box is on
// another shelf, the check is planned again and the same line now carries the
// shelf as well. That is the very difference the technician sent, so it stays
// in the list - it is told apart by a field a person can see.

// Fields that carry RackTrack's own keys. They are how the app finds a record
// again, not something a person saw on the rack, so they are never shown.
const INTERNAL_FIELDS = new Set(['racktrack_uid', 'racktrack_bound', 'recordId']);
const carriesChange = (item) => Object.keys(item.diff || {}).some((field) => !INTERNAL_FIELDS.has(field));
const isHousekeeping = (item) => item.action === 'rebind' && !item.fromUid && !carriesChange(item);
// A record kept, with something on it changed: said as any other difference.
const actionOf = (item) => (item.action === 'rebind' && !item.fromUid && carriesChange(item) ? 'update' : item.action);

/** What a difference means, in one sentence. A shelf is said as a shelf. */
function meaningOf(item) {
  const shelf = item.diff && item.diff.position;
  if (actionOf(item) === 'update' && shelf && shelf.from != null && shelf.to != null) {
    return `Your records list this on shelf U${shelf.from}. You saw it on shelf U${shelf.to}.`;
  }
  return MEANS[actionOf(item)] || '';
}

const STATE_WORD = {
  pending: 'Waiting on the SPOC',
  approved: 'Approved',
  rejected: 'Rejected',
  ticketed: 'Someone is checking',
};

// How this rack was found, in the words of the thing that found it. The app
// tries a code or label on the switches, then the rack's own record, then where
// it stands, then the print on the rack, then what is inside it, and last of all
// it asks a person. Whichever one answers, the person reads the answer, never
// the step number.
const FOUND_BY = {
  record: 'Confirmed: this rack is tied to that record.',
  label: 'Matched by the label read off the rack.',
  'label-netbox': 'Matched by the label read off the rack, against NetBox.',
  devices: 'Matched by the devices inside it.',
  'only-rack': 'Matched to the only rack set up in this room.',
};

// And how sure that leaves it. A record is the strongest answer there is and the
// sentence above already says so; anything read off a rack can be misread, and
// the screen says that rather than letting a reading pass for a confirmation.
const HOW_SURE = {
  probable: 'Probable, not confirmed.',
  possible: 'Possible only.',
};

// The same steps when they fall short of deciding. A suggestion is a question
// for a person and is never worded as a match.
const FITS_BY = {
  label: 'The rack label matches this rack.',
  'label-netbox': 'The rack label matches this rack.',
  devices: 'Most devices here match this rack.',
  'only-rack': 'It is the only rack in this room.',
};

function diffLines(diff) {
  if (!diff) return [];
  return Object.entries(diff)
    .filter(([field]) => !INTERNAL_FIELDS.has(field))
    .map(([field, v]) => ({
      field,
      from: v && typeof v === 'object' && 'from' in v ? v.from : null,
      to: v && typeof v === 'object' && 'to' in v ? v.to : v,
    }));
}

const show = (v) => (v === null || v === undefined || v === '' ? 'nothing' : String(v));

/* What each field is called on a rack, rather than in a record schema. The
   owner asked on 22 Sep 2026 for the differences to be detailed and clear:
   "position: 20 -> 22" is neither until it says shelf, and says which side is
   the record and which is the photograph. */
const FIELD = {
  position: 'Shelf',
  u_height: 'Height',
  name: 'Name',
  device_type: 'Model',
  manufacturer: 'Make',
  serial: 'Serial number',
  asset_tag: 'Asset tag',
  status: 'Status',
  face: 'Face',
  role: 'Role',
  portCount: 'Ports',
  port_count: 'Ports',
  ports: 'Ports',
  type: 'Port type',
  colour: 'Cable colour',
  color: 'Cable colour',
  label: 'Label',
  rack: 'Rack',
  site: 'Site',
  comments: 'Note',
};
const fieldWord = (f) => FIELD[f] || String(f).replace(/[_.]/g, ' ').replace(/^./, (c) => c.toUpperCase());

/* A value in the words of the thing it describes. A shelf is U20, not 20. */
function valueWord(field, v) {
  if (v === null || v === undefined || v === '') return 'nothing';
  if (field === 'position' || field === 'u_height') {
    const n = Number(v);
    return Number.isFinite(n) ? `U${n}` : String(v);
  }
  if (typeof v === 'object') return show(v.name || v.label || v.value || JSON.stringify(v));
  return String(v);
}

/**
 * Where a sent check has got to, in one line: With <name>, Approved, Written to
 * NetBox, Rejected or Needs an admin. Falls back to who it went to when the
 * workflow cannot be read, which is still true.
 *
 * `status` is the workflow's own word: from the check as it is followed, from
 * the answer to Send before the first poll lands, or from the plan itself.
 */
function StatusLine({ status, holder }) {
  const off = OFF_TRACK[status] || null;
  if (off && off.word) return <p><span className={styles.offWord}>{off.word}</span></p>;
  return (
    <p>
      {off ? off.text
        : WRITTEN.includes(status) ? 'Written to NetBox'
          : APPROVED.includes(status) ? 'Approved'
            : `With ${holder || 'the SPOC'}`}
    </p>
  );
}

/**
 * An older check went out as a ticket per difference, and still shows each of
 * them. A check with a holder is one line: who has it.
 */
function Tickets({ flow, rackName }) {
  const tickets = flow?.tickets || [];
  if (flow?.holder || tickets.length === 0) return null;
  const items = flow?.items || [];
  const nameOf = (uid) => plainName((items.find((i) => i.uid === uid) || {}).name || uid, rackName);
  return (
    <ul className={styles.tickets}>
      {tickets.map((t) => (
        <li key={t.itemUid}>
          <b>{nameOf(t.itemUid)}</b>
          <span>
            {t.assignee ? `With ${t.assignee}` : 'Not assigned yet'}
            {t.external?.number ? ` - ${t.external.number}` : ''}
            {t.status ? ` - ${String(t.status).replace(/_/g, ' ')}` : ''}
          </span>
          {t.finding && <span className={styles.found}>They found: {t.finding}</span>}
        </li>
      ))}
    </ul>
  );
}

/**
 * What went wrong raising the incident, if anything did. The number is a fact
 * in the block above and the way to it is in the row of actions, so the only
 * thing left for this to say is that there is no incident to go to.
 */
function Incident({ incident }) {
  if (!incident || incident.system === 'none') return null;
  if (incident.number || !incident.error) return null;
  return <p className={styles.incidentNone}>The incident could not be raised. An admin has been notified.</p>;
}

export default function DriftPage() {
  /* Whether this rack has a switch. The history of what changed on its ports
     used to be the other half of the Network page, behind a Switches/Timeline
     segment. The owner asked on 22 September 2026 for the timeline to BE the
     drift page: a difference and the change that caused it are the same
     question, and a person looking at drift should not have to go to another
     screen to see when a port last moved. Network is the switches now, and
     nothing else.

     It is only drawn once a switch exists - there is no history of ports
     nobody has read. */
  const { rackId } = useParams();
  const { search } = useLocation();
  // Does this rack hold a switch? Only then is there a history of its ports.
  const [hasSwitch, setHasSwitch] = useState(false);
  // A notice about a check that was sent names it (?plan=). That check is shown
  // as it stands; comparing again would file a new draft beside a check that
  // was rejected or written, with a live send button under it.
  const [query] = useSearchParams();
  const named = query.get('plan') || '';
  const goBack = useSmartBack(`/results/${rackId}/report`);

  const [plan, setPlan] = useState(null);
  const [spoc, setSpoc] = useState(null);
  // Who the server says this check goes to: 'spoc', or 'admin' with the reason
  // in words. A server that does not say leaves both empty.
  const [goesTo, setGoesTo] = useState(null);
  const [whyText, setWhyText] = useState('');
  // What Send answered: where the check landed, who holds it, the incident.
  const [sentInfo, setSentInfo] = useState(null);
  // Ports, asked for once the comparison is up. The page never waits on it.
  const [ports, setPorts] = useState(null);
  // Which of the three quiet groups is open, if any: one line of three, and
  // only the one a person picks opens under it.
  // How many ports the check read, for the line that folds them away. The
  // panel itself works the three figures out again; this is only the count.
  const portTotal = useMemo(() => {
    const sum = ports?.summary || {};
    const n = (k) => (Number.isFinite(Number(sum[k])) ? Number(sum[k]) : 0);
    return n('match') + n('mismatch') + n('unknown') || (ports?.rows || []).length;
  }, [ports]);
  const [rest, setRest] = useState(null);
  const [matched, setMatched] = useState(null);
  const [recordRack, setRecordRack] = useState(null);
  const [identity, setIdentity] = useState(null);
  const [pickError, setPickError] = useState('');
  const [busy, setBusy] = useState('Checking this rack against NetBox');
  const [error, setError] = useState('');
  const [needsSource, setNeedsSource] = useState(false);
  const [note, setNote] = useState('');
  const [sent, setSent] = useState(false);
  // Which differences are sent. Everything, until the person says otherwise.
  const [left, setLeft] = useState(() => new Set());
  // The same check as the approval workflow holds it: its status and its tickets.
  const [flow, setFlow] = useState(null);
  const [name, setName] = useState('');   // the person's name for this rack
  const [nameSaved, setNameSaved] = useState('');

  const navigate = useNavigate();
  // A single point of contact or an admin works in the Desk; everybody else
  // follows their own check in the app.
  const can = useApprovalsCan();
  const deskPerson = Boolean(can && (can.admin || can.spoc));
  // Who may connect a record system: the owner and an organization admin. A
  // technician is told who to ask instead. The role is read from the stored
  // session rather than the auth context, so this page keeps working wherever
  // it is mounted, and an unreadable store simply means "not an admin".
  const canConnect = useMemo(() => {
    try {
      const role = JSON.parse(localStorage.getItem('rt_authUser') || '{}')?.role;
      return role === 'owner' || role === 'org_admin';
    } catch { return false; }
  }, []);

  const items = plan?.items || [];
  const changed = useMemo(() => items.filter((i) => i.decidable && !isHousekeeping(i)), [items]);

  // The whole comparison, so the screen can say how much of the rack agrees and
  // not only what does not. A box the scan saw on the shelf where the record has
  // one is a match, and the record's own name for it is shown beside it; what
  // the record holds that the photograph did not show is listed last, because a
  // cable manager behind a wall of cables is expected and a missing switch is not.
  const orphans = plan?.orphans || [];
  const matching = useMemo(() => {
    const byBox = new Map(orphans.filter((o) => o.seen && o.matchedBox).map((o) => [o.matchedBox, o]));
    const differs = new Set(changed.map((i) => i.uid));
    return items
      .filter((i) => i.type === 'Device' && !differs.has(i.uid))
      .map((i) => ({ item: i, record: byBox.get(i.uid) || null }))
      .filter((m) => m.record || m.item.netboxId != null || ['noop', 'rebind'].includes(m.item.action));
  }, [items, orphans, changed]);
  const notSeen = useMemo(() => orphans.filter((o) => !o.seen), [orphans]);

  // The drift report: one printable page of this comparison. The server
  // attaches the same page to the incident when the check is sent.
  //
  // It is read here, in the app. It used to be handed to the system browser,
  // which took the person out of RackTrack to read RackTrack's own page.
  const [reportBusy, setReportBusy] = useState(false);
  const [reportOpen, setReportOpen] = useState(false);
  const [reportUrl, setReportUrl] = useState(null);
  const [reportError, setReportError] = useState('');
  const openReport = async () => {
    if (!plan?.id) return;
    setReportBusy(true);
    setReportUrl(null);
    setReportError('');
    setReportOpen(true);
    try {
      setReportUrl(await driftReportUrl(rackId, plan.id));
    } catch (e) {
      setReportError(e.message || 'The drift report could not be opened.');
    } finally {
      setReportBusy(false);
    }
  };
  // What is ticked to go: everything that differs, less what the person unticked.
  const picked = useMemo(() => changed.filter((i) => !left.has(i.uid)), [changed, left]);
  const toggle = (uid) => setLeft((prev) => {
    const next = new Set(prev);
    if (next.has(uid)) next.delete(uid); else next.add(uid);
    return next;
  });
  const toggleAll = () => setLeft(picked.length === changed.length ? new Set(changed.map((i) => i.uid)) : new Set());

  const load = useCallback(async ({ recompare = false } = {}) => {
    setBusy(recompare ? 'Checking this rack against NetBox again'
      : 'Checking this rack against NetBox');
    setError('');
    setPickError('');
    setNeedsSource(false);
    try {
      // The check a notice named, when it is a sent check of this rack. Anything
      // else - no such check, another rack's, one never sent - compares as usual.
      let body = null;
      let planId = null;
      if (named) {
        try {
          const asked = await authFetch(apiUrl(`/api/nb/plans/${encodeURIComponent(named)}`));
          const got = asked.ok ? await asked.json() : null;
          if (got && got.rackId === rackId && isSent(got)) { body = got; planId = got.id; }
        } catch { /* compared as usual */ }
      }

      // The check this rack already has, if it has one.
      //
      // Comparing a rack against NetBox is the expensive thing this page does,
      // and it was done on every single arrival: opening the screen, coming
      // back from the report, returning from a tab. Each run filed another
      // draft, so a person who looked four times in ninety seconds left four
      // identical drafts behind and an admin found a queue of them. The
      // comparison happens once, when the rack has nothing filed yet; after
      // that this reads what was filed. "Check again" re-runs it on purpose.
      if (!body && !recompare) {
        try {
          const open = await authFetch(apiUrl(
            `/api/approvals/plans?rackId=${encodeURIComponent(rackId)}&open=1&limit=1`));
          const list = open.ok ? await open.json() : null;
          const first = (list && Array.isArray(list.plans) ? list.plans : [])[0];
          if (first && first.id != null) {
            const one = await authFetch(apiUrl(`/api/nb/plans/${encodeURIComponent(first.id)}`));
            const got = one.ok ? await one.json() : null;
            if (got && got.rackId === rackId) { body = got; planId = got.id; }
          }
        } catch { /* nothing filed, or it could not be read: compare as usual */ }
      }

      if (!body) {
        const a = await authFetch(apiUrl(`/api/nb/scans/adopt/${encodeURIComponent(rackId)}`),
          { method: 'POST' });
        const adopted = await a.json();
        if (!a.ok) throw new Error(adopted.error || 'Could not open this rack');

        const p = await authFetch(apiUrl(`/api/nb/netbox/${adopted.id}/preview`), { method: 'POST' });
        const report = await p.json();
        if (!p.ok) {
          // No record system connected yet. That is not a failure of the check,
          // it is a thing somebody has to set up, so say who and offer the way
          // there rather than a red line the technician can do nothing about.
          if (report.hint || /no netbox connection/i.test(String(report.error || ''))) {
            setNeedsSource(true);
            return;
          }
          throw new Error(report.error || 'Could not reach NetBox');
        }

        const full = await authFetch(apiUrl(`/api/nb/plans/${report.planId}`));
        body = await full.json();
        planId = report.planId;
      }
      setPlan(body);
      setSent(isSent(body));

      const c = await authFetch(apiUrl(`/api/nb/plans/${planId}/contacts`));
      if (c.ok) {
        const body = await c.json();
        setSpoc(body.spoc || null);
        setGoesTo(body.goesTo || null);
        setWhyText(body.whyText || '');
        // Which rack in the customer's record this was compared against, and
        // why the app believes it is the same rack. The server has always
        // worked this out; the screen used to take the contact off it and drop
        // the rest, so the one question a person asks of a comparison - "the
        // same rack as what?" - had no answer anywhere in the app.
        setMatched(body.matchedRack || null);
        setRecordRack(body.rack || null);
      }

      // Which rack this is, and how that was worked out: the rack, the step that
      // found it, and the short list of racks it could be when no step can
      // decide. A failure here is not a failure of the check, so the line below
      // falls back to what the comparison itself used.
      try {
        const who = await authFetch(apiUrl(`/api/scan/${encodeURIComponent(rackId)}/identity`));
        setIdentity(who.ok ? await who.json() : null);
      } catch { setIdentity(null); }

      const nm = await authFetch(apiUrl(`/api/nb/scans/rack/${encodeURIComponent(rackId)}/name`));
      if (nm.ok) { const j = await nm.json(); setName(j.name || ''); setNameSaved(j.name || ''); }
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setBusy('');
    }
  }, [rackId, named]);

  useEffect(() => { load(); }, [load]);

  // Which rack this was compared against. Where the app has decided which rack
  // this is, the comparison is built on that rack, so the line names it and says
  // what found it. Where it has not, the line falls back to whatever the
  // comparison itself resolved, and claims nothing beyond it.
  // Was anything actually compared? Naming a rack is not the same as reading
  // it. A scan whose Site is not in the record, or whose rack was matched by
  // name and never bound, reads nothing back: every box in the photograph is
  // then proposed as new, no record goes unseen, and the page called that
  // "9 differences" - on a rack where one device had been added. Nine things
  // do not differ from a record that was never opened. So the count is only
  // a count of DIFFERENCES when the comparison read something: a record the
  // scan did not see, or a box that is anything other than new.
  const compared = matched && matched.confidence !== 'none' ? matched : null;
  const decided = identity && identity.decision === 'matched' && identity.rack ? identity : null;
  const foundBy = decided
    ? [FOUND_BY[decided.rule] || 'Matched to a rack in the record.', HOW_SURE[decided.confidence]]
      .filter(Boolean).join(' ')
    : '';

  // How the rack was matched, for whoever opens Read more: what was read off
  // the rack, and the reason the app itself gave for each rack it considered.
  // Only what the server said; nothing is inferred here. Where the phone stood
  // is no part of it: the site is chosen on the scan screen now.
  const matchedHow = useMemo(() => {
    if (!identity) return [];
    const out = [];
    const ev = identity.evidence || {};
    const labels = (ev.labels || []).map((l) => `${l.normalized || l.text}${l.conf != null ? ` (${Math.round(l.conf * 100)}% sure)` : ''}`);
    out.push(labels.length ? `Label read off the rack: ${labels.join(', ')}.` : 'No label was read off the rack in this photo.');
    for (const c of (identity.candidates || []).slice(0, 2)) {
      for (const why of (c.reasons || [])) {
        const line = `${c.facilityId || c.name}: ${why}`;
        out.push(line.endsWith('.') ? line : `${line}.`);
      }
    }
    return out;
  }, [identity]);

  // Nothing decided which rack this is, so a person still has to.
  const unsettled = !!identity && identity.decision !== 'matched';

  // What is still open, said plainly. Only ever shown when nothing was decided.
  const stillOpen = useMemo(() => {
    if (!unsettled) return '';
    if (identity.decision === 'new') {
      return 'This rack is not in your records yet.';
    }
    if (identity.decision === 'ambiguous') {
      return 'More than one rack matches. Choose the right one.';
    }
    if (identity.decision === 'suggested') {
      return `${FITS_BY[identity.rule] || 'One rack matches.'} Not confirmed yet.`;
    }
    return 'This rack could not be identified.';
  }, [identity, unsettled]);

  // The racks a person may choose from: the ones the app narrowed it down to,
  // never the whole site. Two or three, in the order they were ranked.
  const shortlist = useMemo(() => {
    if (!unsettled) return [];
    return (identity.candidates || []).filter((c) => c && (c.name || c.facilityId)).slice(0, 3);
  }, [identity, unsettled]);
  // Not in the record at all: the rack is added rather than forced onto a bad
  // match. Offered only with a name that was actually read off the rack.
  const newName = unsettled && identity.decision === 'new' ? (identity.proposal?.name || '') : '';

  // Which rack this is, for the header: its own name, and one sentence on how
  // the app knows it is that rack. It used to be a labelled box of its own under
  // the title, so the first thing on the page was a card rather than an answer.
  const rackName = decided
    ? (decided.rack.name || decided.rack.facilityId || (compared && compared.name) || 'this rack')
    : compared ? (compared.name || (recordRack && recordRack.name) || 'this rack')
      : '';
  const rackWhy = decided
    ? (decided.confidence === 'confirmed'
      ? 'This is the rack in your records.'
      : 'This looks like the rack in your records. Not confirmed yet.')
    : compared ? 'Compared with this rack in your records.'
      // Only where nothing has said which rack this is. Where something has, the
      // line below says it once and this does not say it twice.
      : stillOpen ? ''
        : 'This rack has not been set up in the record, so everything here reads as new. Set it up to compare against what is already written down.';

  /** A person says which rack this is. The check then runs again against it. */
  async function pick(choice) {
    setPickError('');
    setBusy('Saving which rack this is');
    try {
      const r = await authFetch(apiUrl(`/api/scan/${encodeURIComponent(rackId)}/identity/confirm`), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(choice),
      });
      const out = await r.json();
      if (!r.ok) throw new Error(out.error || 'That rack could not be saved');
      // Which rack this is decides what it is compared with, so this is one of
      // the two times the comparison is run on purpose.
      await load({ recompare: true });
    } catch (e) {
      setPickError(e.message || String(e));
      setBusy('');
    }
  }

  async function saveName() {
    const trimmed = name.trim();
    if (trimmed === nameSaved) return;
    try {
      const r = await authFetch(apiUrl(`/api/nb/scans/rack/${encodeURIComponent(rackId)}/name`), {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: trimmed }),
      });
      if (r.ok) setNameSaved(trimmed);
    } catch { /* a name is a convenience; a failure here is not worth a red banner */ }
  }

  // Who it goes to, for every sentence that says so. A named SPOC is named; an
  // admin is said where the server says the check will wait for one; and with
  // neither it is "the SPOC", which is who a check goes to.
  const toAdmin = goesTo === 'admin';
  const toName = !toAdmin && spoc && spoc.name ? spoc.name : '';

  async function send() {
    setBusy(toName ? `Sending it to ${toName}` : 'Sending it');
    setError('');
    try {
      const r = await authFetch(apiUrl(`/api/nb/plans/${plan.id}/submit`), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        // Everything, unless some were unticked: then the ones that stay ticked.
        body: JSON.stringify(left.size ? { note, items: picked.map((i) => i.uid) } : { note }),
      });
      const out = await r.json();
      if (!r.ok) throw new Error(out.error || 'That did not go through');
      // Where it landed, so the line below starts there and not at "Sent".
      setSentInfo({ state: out.state || null, assignee: out.assignee || null,
        needsAdmin: out.needsAdmin || null, incident: out.incident || null });
      setSent(true);
      const full = await authFetch(apiUrl(`/api/nb/plans/${plan.id}`));
      if (full.ok) setPlan(await full.json());
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setBusy('');
    }
  }

  // After it is sent the check is followed, not just waited on: asked again
  // every twenty seconds while this screen is open, so a technician standing at
  // the rack sees "With the SPOC" turn into "Approved" without pulling anything.
  useEffect(() => {
    if (!sent || !plan?.id) return undefined;
    let dropped = false;
    const ask = async () => {
      try {
        const r = await authFetch(apiUrl(`/api/approvals/plans/${plan.id}`));
        if (!dropped && r.ok) setFlow(await r.json());
      } catch { /* the line simply shows what is already known */ }
    };
    ask();
    const t = setInterval(ask, 20000);
    return () => { dropped = true; clearInterval(t); };
  }, [sent, plan?.id]);

  // Ports: the cables in the photo against the switch and NetBox. Asked for once
  // per check, after the comparison is on the screen, and never waited on - a
  // server without the route, or a refusal, simply leaves the section out.
  useEffect(() => {
    if (!plan?.id) return undefined;
    let dropped = false;
    setPorts(null);
    (async () => {
      try {
        const r = await authFetch(apiUrl(`/api/nb/plans/${plan.id}/connectivity`));
        if (!dropped && r.ok) setPorts(await r.json());
      } catch { /* the comparison stands without it */ }
    })();
    return () => { dropped = true; };
  }, [plan?.id]);

  // Where the check is, in the workflow's own word, from the best source there
  // is; who holds it; and the incident raised for it.
  const state = flow?.plan?.status || sentInfo?.state || plan?.state || 'submitted';
  const holder = flow?.holder?.username || sentInfo?.assignee?.name
    || (state !== 'triage' && goesTo === 'spoc' ? toName : '');
  const incident = flow?.incident || sentInfo?.incident || null;
  const applied = plan?.status === 'applied' || ['written', 'completed'].includes(flow?.plan?.status);
  // The drift report button. Before sending it closes the page; once sent it
  // sits with the incident number and the status line, beside the way to
  // ServiceNow.
  // Everything a person can do from this page, on one line.
  //
  // These were three controls of three shapes on three lines, each with its
  // own sentence beside it: a bordered button to ServiceNow, an underlined
  // link to Approvals, and another bordered button for the report with a
  // paragraph of explanation. They do the same kind of thing - they open
  // something - so they are one row of the same control, and the sentence
  // that explained the report is the one line under the row.
  const reportable = plan && !busy && compared;
  // Opening ServiceNow came off on 22 Sep 2026: the incident number is on the
  // screen already, the people who work incidents work them in ServiceNow
  // anyway, and a third control on this row made the two that matter smaller.
  const actions = (reportable || (sent && plan)) ? (
    <div className={styles.actions}>
      <div className={styles.actionRow}>
        {reportable && (
          <button type="button" className={styles.action} onClick={openReport} disabled={reportBusy}>
            <span className={styles.actionGlyph} aria-hidden="true">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"
                strokeLinecap="round" strokeLinejoin="round">
                <path d="M14 3H7a2 2 0 00-2 2v14a2 2 0 002 2h10a2 2 0 002-2V8z" />
                <path d="M14 3v5h5" /><path d="M9 13h6" /><path d="M9 17h4" />
              </svg>
            </span>
            <span className={styles.actionText}>
              <b>{reportBusy ? 'Opening' : 'Report'}</b>
              <small>Drift report</small>
            </span>
            <i className={styles.actionGo} aria-hidden="true" />
          </button>
        )}
        {sent && plan && (
          /* Where this goes depends on who is pressing it. Somebody who
             decides - a single point of contact, an admin - works in RackTrack
             RackTrack Control and is sent straight there. The technician who sent the
             check decides nothing, so the Desk has nothing for them: they get
             the check's own page in the app. The owner's rule, 22 Sep 2026. */
          deskPerson ? (
            <ExternalLink className={styles.action} href={driftCheckUrl(plan.id)}>
              <span className={styles.actionGlyph} aria-hidden="true">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"
                  strokeLinecap="round" strokeLinejoin="round">
                  <circle cx="12" cy="12" r="9" /><path d="M12 7v5l3.5 2" />
                </svg>
              </span>
              <span className={styles.actionText}>
                <b>RackTrack Control</b>
                <small>Read it and decide</small>
              </span>
              <i className={styles.actionGo} aria-hidden="true" />
            </ExternalLink>
          ) : (
          <button type="button" className={styles.action} onClick={() => navigate(`/checks/${plan.id}`)}>
            <span className={styles.actionGlyph} aria-hidden="true">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"
                strokeLinecap="round" strokeLinejoin="round">
                <circle cx="12" cy="12" r="9" /><path d="M12 7v5l3.5 2" />
              </svg>
            </span>
            <span className={styles.actionText}>
              <b>Track it</b>
              <small>Where it is now</small>
            </span>
            <i className={styles.actionGo} aria-hidden="true" />
          </button>
          )
        )}
        {/* The comparison runs once, when the rack has nothing filed - see the
            note in load(). This is how a person runs it again when they have
            changed something in the rack or in NetBox.

            Not once the check has gone: it is with its SPOC then, and
            comparing again would file a fresh draft behind their back. */}
        {plan && !busy && !sent && (
          <button type="button" className={styles.action} onClick={() => load({ recompare: true })}>
            <span className={styles.actionGlyph} aria-hidden="true">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"
                strokeLinecap="round" strokeLinejoin="round">
                <path d="M21 12a9 9 0 11-3.5-7.1" /><path d="M21 4v5h-5" />
              </svg>
            </span>
            <span className={styles.actionText}>
              <b>Check again</b>
              <small>Compare with NetBox again</small>
            </span>
          </button>
        )}
      </div>
      {reportable && !sent && (
        <p className={styles.actionNote}>The report is attached to the incident when you send.</p>
      )}
    </div>
  ) : null;

  useEffect(() => {
    if (!rackId) return undefined;
    let dropped = false;
    (async () => {
      try {
        const r = await authFetch(apiUrl(`/api/nb/switches?rackId=${encodeURIComponent(rackId)}`));
        if (!r.ok) return;
        const d = await r.json();
        const list = Array.isArray(d) ? d : (d.switches || d.items || []);
        if (!dropped) setHasSwitch(list.length > 0);
      } catch {
        /* no switches is the same as not knowing: the section stays away */
      }
    })();
    return () => { dropped = true; };
  }, [rackId]);

  return (
    <div className={styles.page}>
      {/* The header area: the title, and under it which rack this is - the rack's
          own name and one sentence. The scan's own id is a hash of the
          photograph and tells the person nothing, so it stays on the title.
          Everything else about the rack - how it was matched, the racks it could
          be, a name typed by hand - is quiet and folded away under it. */}
      <div className={styles.top}>
        <header className={styles.head}>
          <button type="button" className={styles.back} onClick={goBack} aria-label="Back">
            <BackIcon />
          </button>
          <span className={styles.titleWrap}>
            <span className={styles.eyebrow}>Drift check</span>
            <h1 className={rackName ? styles.titleRack : styles.title}>
              {rackName || 'Drift check'}
            </h1>
          </span>
        </header>
        {/* What the ticket asked, when this check came from one. What the
            photograph found is read against it, which is the whole point of
            the second workflow (23 September 2026). */}
        <TaskAsk search={search} rackId={rackId} className={styles.askedFor} />

        {plan && !busy && (
          <div className={styles.rack}>
            {!rackName && <span className={styles.rackUnknown}>Not identified yet</span>}
            {stillOpen && <span className={styles.rackOpen}>{stillOpen}</span>}

            {/* The last step of all: nothing could say which rack this is, so a
                person picks from the short list the steps above left. */}
            {!sent && (shortlist.length > 0 || newName) && (
              <div className={styles.pick}>
                <span className={styles.pickLabel}>Which rack is this?</span>
                <div className={styles.pickRow}>
                  {shortlist.map((c) => (
                    <button
                      key={`${c.source}-${c.id}`}
                      type="button"
                      className={styles.pickBtn}
                      disabled={!!busy}
                      onClick={() => pick(c.source === 'netbox' ? { netboxRackId: c.id } : { knownRackId: c.id })}
                    >
                      {c.name || c.facilityId}
                    </button>
                  ))}
                  {newName && (
                    <button
                      type="button"
                      className={styles.pickBtn}
                      disabled={!!busy}
                      onClick={() => pick({ name: newName })}
                    >
                      Add {newName} to your records
                    </button>
                  )}
                </div>
                {pickError && <p className={styles.pickError} role="alert">{pickError}</p>}
              </div>
            )}

            {/* A name typed by hand is the fallback for a rack nothing has
                identified. Once the app has said which rack this is, the box only
                invited people to type over an answer that was already right. */}
            {!decided && (
              <div className={styles.nameRow}>
                <label className={styles.nameLabel} htmlFor="rackname">Rack name (optional)</label>
                <input
                  id="rackname"
                  className={styles.nameInput}
                  value={name}
                  placeholder="Rack name"
                  onChange={(e) => setName(e.target.value)}
                  onBlur={saveName}
                />
                <span className={styles.nameHint}>
                  {nameSaved ? 'Saved.' : 'Leave blank to keep the current name.'}
                </span>
              </div>
            )}
          </div>
        )}
      </div>

      {busy && <p className={styles.busy}>{busy}…</p>}

      {needsSource && !busy && (
        <div className={styles.needsSource}>
          <h2>No record to check against yet</h2>
          {canConnect ? (
            <>
              <p>
                Connect NetBox or ServiceNow under Data sources, then run this check again.
              </p>
              <button type="button" className={styles.connect} onClick={() => navigate('/connections')}>
                Go to Data sources
              </button>
            </>
          ) : (
            <p>
              Ask your administrator to connect NetBox or ServiceNow.
            </p>
          )}
        </div>
      )}

      {error && <p className={styles.error} role="alert">{error}</p>}

      {plan && !busy && (
        <div className={styles.verdict}>
          {changed.length === 0 ? (
            <>
              <h2 className={styles.answer}><span className={styles.tick} aria-hidden="true">✓</span>Everything matches</h2>
            </>
          ) : (
            <>
              {/* Nothing was compared when the record holds no such rack, so
                  calling the count a mismatch is not true: those are the boxes
                  this scan would ADD. The owner read "9 things do not match" on
                  a rack the screen had just said was not in the record at all. */}
              {/* The answer, in as few words as it takes. It was a headline
                  the width of the screen with a sentence under it saying when
                  the comparison ran; the rack's name above is the headline. */}
              <h2 className={styles.answer}>
                {compared
                  ? `${changed.length} ${changed.length === 1 ? 'difference' : 'differences'}`
                  : `${changed.length} would be added`}
              </h2>
              {/* Only where the count needs it: a rack the records have never
                  held is not a rack that disagrees, and saying so once keeps
                  the count honest. Nothing is said when there is a comparison,
                  because the count is then the whole answer. */}
              {!compared && <p>Your records do not have this rack yet.</p>}
            </>
          )}
        </div>
      )}

      {plan && !busy && !sent && changed.length > 1 && (
        <label className={styles.pickAll}>
          <input type="checkbox" checked={picked.length === changed.length}
                 ref={(el) => { if (el) el.indeterminate = picked.length > 0 && picked.length < changed.length; }}
                 onChange={toggleAll} />
          <span>{picked.length === changed.length ? 'All selected' : `${picked.length} of ${changed.length} selected`}</span>
        </label>
      )}

      <ul className={styles.list}>
        {changed.map((item) => {
          const lines = diffLines(item.diff);
          const ext = item.ticket?.external;
          return (
            <li key={item.uid} className={`${styles.item} ${styles[item.decision] || ''}`}>
              <div className={styles.itemTop}>
                {!sent && changed.length > 1 && (
                  <input type="checkbox" className={styles.pickOne} checked={!left.has(item.uid)}
                         aria-label={`Send ${plainName(item.name, decided && (decided.rack.name || decided.rack.facilityId))}`}
                         onChange={() => toggle(item.uid)} />
                )}
                <strong className={styles.name}>
                  {plainName(item.name, decided && (decided.rack.name || decided.rack.facilityId))}
                </strong>
                <span className={styles.what}>{WORD[actionOf(item)] || item.action}</span>
              </div>
              {meaningOf(item) && <p className={styles.means}>{meaningOf(item)}</p>}

              {/* What differs, field by field, on the page. It used to be
                  behind a "Read more" fold, so the screen said a box was
                  "Different from your records" and made a person open a
                  disclosure to learn what was different. Two columns: what the
                  record holds, and what the photograph showed. */}
              {lines.length > 0 && (
                <div className={styles.diffTable}>
                  <div className={styles.diffHead}>
                    <span />
                    <span>Your records</span>
                    <span>In the rack</span>
                  </div>
                  {lines.map((l) => (
                    <div className={styles.diffRow} key={l.field}>
                      <span className={styles.field}>{fieldWord(l.field)}</span>
                      <span className={styles.from}>{valueWord(l.field, l.from)}</span>
                      <span className={styles.to}>{valueWord(l.field, l.to)}</span>
                    </div>
                  ))}
                </div>
              )}

              {/* What this thing is, and why the comparison says what it says.
                  A line each, quiet, under the difference itself. */}
              <p className={styles.itemWhat}>
                <span className={styles.itemKind}>{item.type}</span>
                {item.reason ? <span className={styles.itemWhy}>{item.reason}</span> : null}
              </p>

              {/* What is true of THIS difference. Who holds the check and
                  which incident it is are facts of the whole check, said once
                  in the block under the list - so this line says them only
                  where they differ from it. On a check with one difference it
                  said the holder and the number a second and third time. */}
              {sent && (
                <p className={styles.state}>
                  {item.decision === 'pending' && state === 'triage' ? 'Waiting on an admin' : STATE_WORD[item.decision] || item.decision}
                  {item.ticket?.assignee && item.decision === 'ticketed' && item.ticket.assignee !== holder && ` - ${item.ticket.assignee}`}
                  {ext?.number && ext.number !== incident?.number && ` · ${ext.number}${ext.state ? ` (${ext.state})` : ''}`}
                  {item.note && ` - “${item.note}”`}
                </p>
              )}
              {item.ticket?.finding && (
                <p className={styles.finding}>They found: {item.ticket.finding}</p>
              )}
            </li>
          );
        })}
      </ul>

      {/* Where the check has got to, once it has been sent.
          This sat ABOVE the differences, with the incident number set in the
          page's largest type - so the biggest thing on a screen about a
          router on shelf U20 was a reference number, and the difference
          itself was three sections further down. The differences are the
          page; this is what is happening to them, so it comes after them,
          and the number is a value with a label on it like any other. */}
      {sent && (
        <section className={applied ? styles.done : styles.waiting}>
          <h2 className={styles.sectionHead}>
            {applied ? 'The record has been updated'
              : state === 'triage' ? 'It needs an admin'
                : holder ? 'Sent' : 'Sent to the SPOC'}
          </h2>
          <dl className={styles.facts}>
            {incident?.number && (
              <div className={styles.fact}>
                <dt>Incident</dt>
                <dd className={styles.factMono}>{incident.number}</dd>
              </div>
            )}
            <div className={styles.fact}>
              <dt>Status</dt>
              <dd><StatusLine status={applied && !flow ? 'written' : state} holder={holder} /></dd>
            </div>
          </dl>
          <Tickets flow={flow} rackName={decided && (decided.rack.name || decided.rack.facilityId)} />
          <Incident incident={incident} />
        </section>
      )}

      {sent && actions}

      {/* What needs no attention. It was three rows of the same shape stacked
          one under another, which read as a list of things to do; it is one
          line of three now, and only the one a person picks opens. */}
      {plan && !busy && ((compared && (matching.length > 0 || notSeen.length > 0)) || ports || hasSwitch) && (
        <div className={styles.quiet}>
          {/* The three counts were the last thing on the page with nothing
              saying what they counted. */}
          <h2 className={styles.sectionHead}>The rest of the rack</h2>
          {/* These were three outlined pills on a white ground and testers
              did not know they were controls at all: "I did not know they
              were buttons". They are filled now, each with a chevron that
              turns when its list is open, so the shape says press me and the
              mark says this opens. */}
          <div className={styles.quietPick} role="tablist" aria-label="The rest of the rack">
            {compared && matching.length > 0 && (
              <button type="button" role="tab" aria-selected={rest === 'matched'}
                className={`${styles.quietTab} ${rest === 'matched' ? styles.quietOn : ''}`}
                onClick={() => setRest(rest === 'matched' ? null : 'matched')}>
                <span className={styles.dotOk} aria-hidden="true" />
                Matched<b>{matching.length}</b>
                <i className={styles.quietChev} aria-hidden="true" />
              </button>
            )}
            {compared && notSeen.length > 0 && (
              <button type="button" role="tab" aria-selected={rest === 'notseen'}
                className={`${styles.quietTab} ${rest === 'notseen' ? styles.quietOn : ''}`}
                onClick={() => setRest(rest === 'notseen' ? null : 'notseen')}>
                <span className={styles.dotIdle} aria-hidden="true" />
                Not seen<b>{notSeen.length}</b>
                <i className={styles.quietChev} aria-hidden="true" />
              </button>
            )}
            {hasSwitch && (
              <button type="button" role="tab" aria-selected={rest === 'timeline'}
                className={`${styles.quietTab} ${rest === 'timeline' ? styles.quietOn : ''}`}
                onClick={() => setRest(rest === 'timeline' ? null : 'timeline')}>
                <span className={styles.dotPort} aria-hidden="true" />
                Timeline
                <i className={styles.quietChev} aria-hidden="true" />
              </button>
            )}
            {ports && (
              <button type="button" role="tab" aria-selected={rest === 'ports'}
                className={`${styles.quietTab} ${rest === 'ports' ? styles.quietOn : ''}`}
                onClick={() => setRest(rest === 'ports' ? null : 'ports')}>
                <span className={styles.dotPort} aria-hidden="true" />
                Ports<b>{portTotal}</b>
                <i className={styles.quietChev} aria-hidden="true" />
              </button>
            )}
          </div>

          {rest === 'timeline' && (
            <div className={styles.timeline}>
              <PortHistoryContent rackId={rackId} />
            </div>
          )}
          {rest === 'matched' && (
            <ul className={styles.rows}>
              {matching.map(({ item, record }) => (
                <li key={item.uid}>
                  <span className={styles.rowName}>{plainName(item.name, decided && (decided.rack.name || decided.rack.facilityId))}</span>
                  <span className={styles.rowNote}>{record ? record.name : 'In your records'}</span>
                  <span className={styles.dotOk} aria-label="matches" />
                </li>
              ))}
            </ul>
          )}
          {rest === 'notseen' && (
            <>
              <p className={styles.groupNote}>
                Not visible in this photo. Nothing is removed from your records.
              </p>
              <ul className={styles.rows}>
                {notSeen.map((o) => (
                  <li key={o.netboxId ?? o.name}>
                    <span className={styles.rowName}>{o.name}</span>
                    <span className={styles.rowNote}>{o.position != null ? `Shelf U${o.position}` : 'No shelf recorded'}</span>
                    <span className={styles.dotIdle} aria-label="not seen" />
                  </li>
                ))}
              </ul>
            </>
          )}
          {rest === 'ports' && <PortsCheck ports={ports} open />}
        </div>
      )}

      {/* Sending it is one action, so it is one block: who it goes to, the note
          for them, and the button. Who decides a difference used to be said at
          the top of the page and the button was at the bottom of it. */}
      {plan && changed.length > 0 && !sent && (
        <div className={styles.send}>
          {/* The SPOC of the site, by name. Where the site has none, or the
              person sending IS the SPOC, it says so and says an admin will
              choose - nobody should learn that only after pressing Send. A
              server that names nobody leaves this out. */}
          {!busy && (toAdmin || spoc) && (
            <div className={styles.goesTo}>
              <span className={styles.goesToLabel}>Goes to</span>
              {toAdmin ? (
                <>
                  <strong className={styles.goesToName}>An organization admin</strong>
                  <span className={styles.goesToWhy}>
                    {whyText ? `${whyText} ` : ''}An admin will assign it.
                  </span>
                </>
              ) : (
                <>
                  <strong className={styles.goesToName}>{spoc.name}</strong>
                  {spoc.title && <span className={styles.goesToRole}>{spoc.title}</span>}
                  {spoc.email && <span className={styles.goesToMail}>{spoc.email}</span>}
                </>
              )}
            </div>
          )}
          <div className={styles.footer}>
            <label className={styles.label} htmlFor="note">
              {toAdmin ? 'Note for the admin (optional)' : 'Note for the SPOC (optional)'}
            </label>
            <textarea id="note" className={styles.textarea} value={note}
                      placeholder="For example: the router is on shelf U20"
                      onChange={(e) => setNote(e.target.value)} />
            <button type="button" className={styles.primary} disabled={!!busy || picked.length === 0} onClick={send}>
              {picked.length === changed.length ? 'Raise incident'
                : picked.length === 0 ? 'Choose at least one to send'
                  : `Raise incident for ${picked.length} of ${changed.length}`}
            </button>
          </div>
        </div>
      )}

      {!sent && actions}

      {reportOpen && (
        <ReportViewer
          title="Drift report"
          url={reportUrl}
          error={reportError}
          onClose={() => setReportOpen(false)}
        />
      )}
    </div>
  );
}
