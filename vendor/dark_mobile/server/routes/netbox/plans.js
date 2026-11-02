/**
 * The push workflow: compare, identify, send to the SPOC of the site.
 *
 * Everything a rack scan would change goes to one person: the SPOC setup named
 * for the site the rack was scanned under. The check is theirs to review and
 * approve as a whole, and only what they approve is written, so there is one
 * name against every change that reaches the customer's record. When the site
 * has nobody valid to give it to, the check waits for an organization admin,
 * who chooses somebody in RackTrack Control. The person who sent a check decides
 * nothing on it.
 *
 * The rules are held in lib/approvals (machine.js, service.js, spoc.js), not
 * in a screen and not in this file: every route here hands the real signed-in
 * user to the service, so the same rules apply as under /api/approvals.
 *
 * The write itself lives in netbox.js, gated on a plan from here.
 */
const express = require('express');

const plans = require('../../lib/netbox/plans');
const profiles = require('../../lib/connection_profiles');
const service = require('../../lib/approvals/service');
const tickets = require('../../lib/netbox/tickets');
const { sendNotice } = require('../../auth');
const gates = require('./gates');
const trail = require('./trail');

const router = express.Router();

/**
 * The ServiceNow the admin configured, in the shape tickets.js wants.
 *
 * Null when nothing is set up, and that is not an error: the ticket still
 * exists here, it simply has no external number. Ours first, theirs mirrored.
 */
function serviceNowFor(req) {
  const orgId = req.user?.organization_id;
  const creds = (orgId ? profiles.resolveCredsForOrg(orgId, 'servicenow') : null)
    || (req.user?.id ? profiles.resolveCredsForType(req.user.id, 'servicenow') : null);
  const s = creds?.secret;
  if (!s || !(s.instance || s.instanceUrl)) return null;

  // The connection form stores just the instance name ("dev322173"), or a full
  // URL. Build a real base URL either way: a bare name needs the domain
  // appended, or every call resolves the wrong host.
  const raw = String(s.instanceUrl || s.instance).trim();
  let instanceUrl;
  if (/^https?:\/\//i.test(raw)) {
    instanceUrl = raw.replace(/\/+$/, '');
  } else if (raw.includes('.')) {
    instanceUrl = `https://${raw.replace(/\/+$/, '')}`;
  } else {
    instanceUrl = `https://${raw}.service-now.com`;
  }
  return {
    instanceUrl,
    username: s.user || s.username || '',
    password: s.password || '',
    incidentTable: s.incidentTable || 'incident',
  };
}

const who = (req) => (req.user && (req.user.username || req.user.email)) || null;
const isAdmin = gates.isAdmin;
// A plan the caller's organisation does not own is, to them, 404 - the same
// answer they get for a plan that does not exist. No cross-org leak, no owner
// bypass; a plan raised by an account with no organisation belongs to that
// account alone (plans.visibleTo, the one rule the list uses too). A
// technician sees only the plans they raised themselves; an admin sees every
// plan in the organisation.
const mine = (req, plan) => plan && plans.visibleTo(plan, req.user)
  && (isAdmin(req) || plan.createdBy === who(req));

/** A plan with the counts a screen shows, the write result included. */
const summaryOf = (plan) => plans.summarise(plan.items, plan.result);

/**
 * Everything in one place.
 *
 * `?status=submitted` is the admin's inbox: comparisons a technician has
 * handed over and nobody has acted on. Without it an admin would have to know
 * a scan happened and go looking for it.
 *
 * `?mine=1` narrows the list to the plans the caller raised. For a technician
 * the filter is always on: they see their own comparisons and nobody else's.
 */
router.get('/', gates.technician, (req, res) => {
  const onlyMine = req.query.mine === '1' || req.query.mine === 'true' || !isAdmin(req);
  res.json({
    plans: plans.list({
      scanId: req.query.scanId ?? null,
      rackId: req.query.rackId ?? null,
      status: req.query.status ?? null,
      // The same test the read applies, so nothing is listed that answers
      // "no such plan" when it is opened.
      seenBy: plans.seenBy(req.user),
      createdBy: onlyMine ? who(req) : null,
      limit: Math.min(Number(req.query.limit) || 50, 200),
    }),
    youAre: req.user?.role || null,
    mine: onlyMine,
  });
});

/**
 * A technician sends the comparison.
 *
 * They cannot write to NetBox and this does not try to. The check goes
 * straight to the SPOC of the site - `state: 'assigned'`, with who that is and
 * the incident raised for them - or, when the site has nobody valid to give it
 * to, it waits for an admin: `state: 'triage'` and `needsAdmin` says why.
 * `status` stays the word the older phone builds read.
 */
router.post('/:planId/submit', gates.technician, async (req, res) => {
  if (!mine(req, plans.get(req.params.planId))) return res.status(404).json({ error: 'no such plan' });
  // { items: [uid] } sends those differences and leaves the rest marked as not
  // sent; without it everything goes, as it always did.
  const chosen = (req.body || {}).items;
  let out;
  try {
    out = await service.submitAndDispatch(req.params.planId, {
      note: (req.body || {}).note, items: Array.isArray(chosen) ? chosen.map(String) : null,
      actor: req.user, req,
    });
  } catch {
    return res.status(500).json({ error: 'The check could not be sent. Try again.' });
  }
  if (out.error) return res.status(out.code === 'not_found' ? 404 : 409).json({ error: out.error });
  const plan = plans.get(req.params.planId);
  if (!out.already) {
    trail.record(req, plan, 'drift.submit', {
      payload: { note: plan.submittedNote, ...summaryOf(plan) },
    });
  }
  const holder = out.holder || null;
  const incident = out.incident || null;
  res.json({
    planId: plan.id,
    status: plan.status,
    already: Boolean(out.already),
    submittedBy: plan.submittedBy,
    summary: summaryOf(plan),
    state: out.plan.status,
    goesTo: holder ? 'spoc' : 'admin',
    assignee: holder ? { userId: holder.userId, name: holder.username, email: holder.email ?? null } : null,
    needsAdmin: out.needsAdmin ? { why: out.needsAdmin.why, text: out.needsAdmin.text } : null,
    incident: !incident ? null : incident.system === 'none' ? { system: 'none' }
      : { system: incident.system, number: incident.number || null, url: incident.url || null,
          state: incident.state || null, assigned: Boolean(incident.assigned), error: incident.error || null },
  });
});

/**
 * Who this check goes to when it is sent: the SPOC of the site, by name, or
 * `goesTo: 'admin'` with the reason when the site has nobody valid. The rack
 * NetBox recognises is still named, when there is a NetBox to ask.
 */
router.get('/:planId/contacts', gates.technician, async (req, res) => {
  const plan = plans.get(req.params.planId);
  if (!mine(req, plan)) return res.status(404).json({ error: 'no such plan' });
  const out = await service.contacts(req.params.planId, { actor: req.user });
  if (out.error) return res.status(out.code === 'not_found' ? 404 : 409).json({ error: out.error });
  // Who else it could go to is an admin's question, asked in RackTrack Control.
  delete out.assignable;
  res.json(out);
});

/**
 * One plan, with its tickets brought up to date first.
 *
 * ServiceNow owns whether an incident is open or closed, so opening a plan
 * asks it rather than trusting what we last wrote down. An incident somebody
 * resolved over there returns its item here as UNDECIDED - having looked is
 * not the same as having approved.
 */
router.get('/:planId', gates.technician, async (req, res) => {
  let plan = plans.get(req.params.planId);
  if (!mine(req, plan)) return res.status(404).json({ error: 'no such plan' });

  let heardBack = [];
  const sn = serviceNowFor(req);
  const waiting = plans.openSysIds(plan);
  if (sn && waiting.length) {
    try {
      const r = await tickets.statusOf(sn, waiting);
      if (r.ok) {
        const applied = plans.applyTicketStates(plan.id, r.states);
        plan = applied.plan;
        heardBack = applied.changed;
      }
    } catch { /* ServiceNow being unreachable must not hide the plan */ }
  }

  res.json({
    ...plan,
    settled: plans.isSettled(plan),
    summary: summaryOf(plan),
    heardBack,
    serviceNowConfigured: Boolean(sn),
  });
});

/**
 * Every drift ticket this account has raised, across all plans.
 *
 * The Incidents view reads this: one row per ticket, with its ServiceNow
 * number and link, who it went to, its state, and what the rack it came from
 * is - so an admin sees the whole picture in one place, before and after each
 * one is resolved. Admin only: a technician has no tickets to track.
 */
router.get('/tickets/all', gates.admin, (req, res) => {
  const status = req.query.status || null;
  const rows = [];
  for (const idx of plans.list({ seenBy: plans.seenBy(req.user), limit: 200 })) {
    const plan = plans.get(idx.id);
    if (!plan) continue;
    for (const it of plan.items) {
      if (!it.ticket) continue;
      // An interface shares its device's ticket; the device row is the ticket.
      if (it.ticket.sharedWith) continue;
      if (status && it.ticket.status !== status) continue;
      const ext = it.ticket.external || {};
      rows.push({
        planId: plan.id,
        rackId: plan.rackId,
        uid: it.uid,
        type: it.type,
        name: it.name,
        action: it.action,
        assignee: it.ticket.assignee,
        assigneeId: it.ticket.assigneeId ?? null,
        assigneeEmail: it.ticket.assigneeEmail ?? null,
        status: it.ticket.status,               // open | resolved | closed
        question: it.ticket.question,
        finding: it.ticket.finding,
        raisedAt: it.ticket.raisedAt,
        resolvedAt: it.ticket.resolvedAt,
        number: ext.number || null,
        state: ext.state || null,               // ServiceNow state
        url: ext.url || null,
        system: ext.system || 'none',
        error: ext.error || null,
      });
    }
  }
  rows.sort((a, b) => String(b.raisedAt || '').localeCompare(String(a.raisedAt || '')));
  res.json({ tickets: rows });
});

/** Just the tickets, for whoever has to work them. */
router.get('/:planId/tickets', gates.admin, (req, res) => {
  const plan = plans.get(req.params.planId);
  if (!mine(req, plan)) return res.status(404).json({ error: 'no such plan' });
  const rows = plan.items
    .filter((i) => i.ticket && !i.ticket.sharedWith)
    .filter((i) => !req.query.assignee || i.ticket.assignee === req.query.assignee)
    .filter((i) => !req.query.status || i.ticket.status === req.query.status)
    .map((i) => ({ ...i.ticket, uid: i.uid, type: i.type, name: i.name,
                   action: i.action, diff: i.diff }));
  res.json({ planId: plan.id, tickets: rows });
});

/** What an incident description says an item is. */
const whatDiffers = (item) => (item.action === 'create'
  ? 'found in the rack, not in NetBox' : 'does not match NetBox');

/** "Device "Sw1" - found in the rack, not in NetBox (48 ports follow it)" */
function itemLine(plan, item) {
  const ports = plans.childrenOf(plan, item.uid).length;
  return `${item.type} "${item.name}" - ${whatDiffers(item)}`
    + (ports ? ` (${ports} port${ports === 1 ? '' : 's'} follow it)` : '');
}

/**
 * One email to one person about everything just assigned to them. The email
 * is a courtesy on top of the ServiceNow incident, not the record - a failure
 * here never blocks the decision. Sent to their own address, read from NetBox.
 */
function notifyAssignee(plan, { person, items, incidents, rackName, siteName, by, question, wholeRack }) {
  const where = [rackName, siteName].filter(Boolean).join(', ');
  const n = items.length;
  const subject = wholeRack
    ? `RackTrack: please check rack ${rackName} (${n} item${n === 1 ? '' : 's'})`
    : n === 1
      ? `RackTrack: please check ${items[0].type} "${items[0].name}" in ${rackName}`
      : `RackTrack: please check ${n} items in ${rackName}`;
  const incLines = incidents.flatMap((ext) => [
    ext.number ? `ServiceNow incident: ${ext.number}` : null,
    ext.url || null,
  ]).filter(Boolean);
  const text = [
    `Hello ${person.name},`,
    '',
    `A rack scan of ${where} found ${n === 1 ? 'something' : `${n} things`} that `
      + `${n === 1 ? 'does' : 'do'} not match NetBox, and ${by || 'an admin'} has asked you `
      + `to check ${n === 1 ? 'it' : 'them'} at the rack.`,
    '',
    ...items.map((i) => `  ${itemLine(plan, i)}`),
    question ? `\nThey ask: ${question}` : '',
    incLines.length ? `\n${incLines.join('\n')}` : '',
    '',
    'Nothing has been written to NetBox. Please check the rack and resolve the '
      + `incident${incidents.length === 1 ? '' : 's'} with what you find; it then comes back for approval.`,
    '',
    '- RackTrack',
  ].filter((l) => l !== undefined).join('\n');

  // The send finishes whenever the mail server answers, which is after this
  // request has returned and possibly after somebody has already acted on the
  // ticket. So the plan is read again here and only the two email fields are
  // written. It used to save the copy of the plan it was handed at assignment
  // time, which put back everything as it was then: resolve the first ticket
  // straight after assigning the rack and the email landing a moment later
  // reopened it. That happened on the demo, on plans 115 and 126, and is why
  // one ticket always seemed to need resolving twice.
  const stamp = (patch) => {
    try {
      const fresh = plans.get(plan.id);
      if (!fresh) return;
      const uids = new Set(items.map((i) => i.uid));
      let touched = false;
      for (const item of fresh.items || []) {
        if (!uids.has(item.uid) || !item.ticket) continue;
        Object.assign(item.ticket, patch);
        touched = true;
      }
      if (touched) plans.save(fresh);
    } catch { /* a courtesy note on the ticket; never worth failing over */ }
  };
  return sendNotice({ to: person.email, subject, text })
    .then((ok) => stamp(ok ? { emailedAt: new Date().toISOString() }
      : { emailNote: 'no mail transport configured' }))
    .catch(() => stamp({ emailNote: 'the notice could not be sent' }));
}

const GOES_TO_THE_SPOC = 'A check goes to the site SPOC when it is sent. '
  + 'An organization admin reassigns it in RackTrack Control.';

/**
 * Approve or reject items from the phone, one by one.
 *
 *   { decisions: [ { uid, decision: 'approved'|'rejected', note } ] }
 *
 * Assigning from here is gone: a check goes to the site SPOC when it is sent,
 * so `ticketed`, the whole-rack '*' and scope 'rack' are refused with a
 * sentence. Approve and reject go to the same strict service the desk uses,
 * with the real signed-in user: they are for the SPOC the check is with or an
 * organization admin, and never for the person who sent it.
 */
router.post('/:planId/decide', gates.only(gates.ADMINS,
  'Only an admin decides what gets written. Ask yours to review this plan.'), (req, res) => {
  if (!mine(req, plans.get(req.params.planId))) return res.status(404).json({ error: 'no such plan' });
  const body = req.body || {};
  const decisions = Array.isArray(body.decisions) ? body.decisions : [];
  // A whole-rack row beside single items was always a malformed body, and
  // still is, before it is anything else.
  const star = decisions.some((d) => d && d.uid === '*');
  if ((star && decisions.some((d) => !d || d.uid !== '*')) || (!star && body.scope === 'rack' && decisions.length)) {
    return res.status(400).json({
      error: 'send the whole-rack decision on its own, not mixed with single items',
    });
  }
  if (star || body.scope === 'rack' || decisions.some((d) => d && d.decision === 'ticketed')) {
    return res.status(409).json({ error: GOES_TO_THE_SPOC });
  }
  if (!decisions.length) {
    return res.status(400).json({ error: 'send { decisions: [ { uid, decision } ] }' });
  }
  const out = service.decideItems(req.params.planId, decisions, { actor: req.user, req });
  if (out.error) return res.status(service.httpStatus(out)).json({ error: out.error });
  const plan = plans.get(req.params.planId);

  // The trail: one row per approve or reject that went through.
  for (const d of out.applied) {
    const sent = decisions.find((x) => x && x.uid === d.uid) || {};
    trail.record(req, plan, 'drift.decide', {
      payload: { uid: d.uid, decision: d.decision, note: sent.note || null },
    });
  }
  res.json({
    planId: plan.id,
    applied: out.applied,
    refused: out.refused,
    summary: summaryOf(plan),
    settled: plans.isSettled(plan),
  });
});

/**
 * Is the caller the person this ticket was assigned to?
 *
 * Matched three ways, because the assignee is a NetBox contact and the caller
 * is a RackTrack user: by the name the admin picked, by the contact's email
 * against the caller's, or by the contact id when the caller carries one. All
 * three come from the signed-in account, never from anything the request says.
 */
function isAssignee(req, ticket) {
  const u = req.user || {};
  const theirs = [ticket.assignee, ticket.assigneeEmail,
    ticket.assigneeId != null ? String(ticket.assigneeId) : null]
    .filter(Boolean).map((v) => String(v).toLowerCase());
  const ours = [u.username, u.email, u.netbox_contact_id != null ? String(u.netbox_contact_id) : null]
    .filter(Boolean).map((v) => String(v).toLowerCase());
  return ours.some((v) => theirs.includes(v));
}

/**
 * Whoever the ticket went to has been and looked.
 *
 * Their answer returns the item to the admin as undecided. A finished ticket
 * is not an approval, and this route deliberately cannot approve anything.
 */
router.post('/:planId/tickets/:uid/resolve', gates.admin, (req, res) => {
  const { finding, outcome } = req.body || {};
  const plan = plans.get(req.params.planId);
  if (!mine(req, plan)) return res.status(404).json({ error: 'no such plan' });

  const item = plan.items.find((i) => i.uid === req.params.uid);
  if (!item || !item.ticket) return res.status(404).json({ error: 'no ticket on that item' });
  // A port shares its device's ticket; the device row is the one to resolve.
  if (item.ticket.sharedWith) {
    return res.status(409).json({ error: 'this port follows its device; resolve the device ticket instead' });
  }

  // The person it was assigned to can resolve it; so can an org admin or the
  // owner. A site manager who is not the assignee cannot close somebody
  // else's ticket.
  const me = who(req);
  const orgAdmin = ['owner', 'org_admin'].includes(req.user?.role);
  if (!isAssignee(req, item.ticket) && !orgAdmin) {
    return res.status(403).json({
      error: `this ticket is assigned to ${item.ticket.assignee}`,
    });
  }

  const out = plans.resolveTicket(req.params.planId, req.params.uid,
                                  { by: me, finding, outcome });
  if (out.error) return res.status(409).json(out);
  trail.record(req, out.plan, 'drift.resolve', {
    payload: { uid: out.item.uid, outcome: out.item.ticket.outcome,
               finding: out.item.ticket.finding, assignee: out.item.ticket.assignee },
  });
  res.json({
    planId: out.plan.id,
    uid: out.item.uid,
    ticket: out.item.ticket,
    decision: out.item.decision,
    note: 'Back with the admin. A resolved ticket writes nothing on its own.',
  });
});

module.exports = router;
// For the test that holds the assignment email to what it may change.
module.exports._internal = { notifyAssignee };
