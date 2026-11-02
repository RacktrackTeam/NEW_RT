/**
 * Change plans - now an adapter, not a store.
 *
 * A comparison against NetBox produces a diff, and this module makes that diff
 * a durable object with an id, so that an approval is a signature on THAT list
 * rather than on "whatever the differences are right now", a ticket hangs off
 * one item of it, the push writes only what was approved, and the history is
 * the sequence of plans over time.
 *
 * WHAT CHANGED ON 18 SEPTEMBER 2026. The plan used to be a JSON file per plan
 * with an index beside it, and every rule about a plan lived in this file. The
 * manager's ticketing and approval specification needs twenty-two statuses,
 * SLA clocks, notifications and reports across plans, which is a database
 * question and not a directory of files. So the plans moved into SQLite tables
 * in auth.db and the rules moved with them:
 *
 *   lib/approvals/store.js    the tables and plain reads and writes
 *   lib/approvals/machine.js  which status follows which, and who may move it
 *   lib/approvals/shape.js    the fingerprint, one row of a plan, the counts,
 *                             and the OLD SHAPE this file still hands back
 *   lib/approvals/service.js  the operations, with the rules applied
 *
 * WHAT DID NOT CHANGE is this file's face. Every function keeps its name, its
 * arguments and the exact object it returned, because the phone app, the
 * /api/nb routes and the tests that pin the frozen workflow all read it:
 * `plan.items[].ticket` nested on the item, the four old status words, the old
 * event lines. shape.legacyPlan() builds that view from the tables on each
 * read. Nothing here writes a file, and nothing here reads one.
 *
 * WHO IS ASKING. These functions take a name and nothing else - `{ by: 'meera' }` -
 * from callers that had already decided the caller was allowed: a unit test, a
 * script, a route that gated itself first. They arrive at the service as a
 * `trusted` actor, so the item rules still hold in full (assign before decide,
 * a port follows its device, a rebind is never sent to the rack) while the
 * status table and the role checks stand aside. A route that wants the rules
 * in full calls the service directly, which is what /api/approvals does.
 */
const path = require('path');

const service = require('../approvals/service');
const shape = require('../approvals/shape');
const store = require('../approvals/store');

// Kept so a log line or a script that named the old directory still resolves.
// Nothing here reads or writes it; the files are the migration's business
// (lib/approvals/migrate.js), and it never changes them either.
const DATA_DIR = process.env.RT_DATA_DIR || path.join(__dirname, '..', 'data');
const PLANS_DIR = path.join(DATA_DIR, 'plans');

/** Which object types a person decides about, and which simply follow. */
const SUPPORTING = shape.SUPPORTING;
/** Actions that would change NetBox. Everything else is reported, not decided. */
const ACTIONABLE = shape.ACTIONABLE;
/**
 * Where a plan is in its life, in the four words the app knows.
 *
 *   open          a technician compared and has not handed it over
 *   submitted     handed over: with the admin, with an assignee, waiting for
 *                 approval - everything between the hand-over and the write
 *   applied       written to NetBox, every approved object
 *   write_failed  the write ran and NetBox refused at least one object
 *
 * The workflow's own twenty-two statuses are on the same plan as `state`;
 * shape.legacyStatus() is the map between them.
 */
const STATUSES = new Set(['open', 'submitted', 'applied', 'write_failed']);

/** The caller, as the service reads it: a name somebody else already trusted. */
const actorOf = (by) => service.trustedActor(by ?? null);

/** One plan in the old shape, or null. */
const get = (id) => shape.legacyPlan(service.get(id, actorOf(null)));

/** The fingerprint: a stable hash of what a list of changes would do. */
const fingerprint = (changes) => shape.fingerprint(changes);

/**
 * File a comparison as a plan.
 *
 * `report` is exactly what writer.plan() returned. Nothing is interpreted
 * beyond splitting it into rows: the plan is a faithful record of what the
 * comparison said at that moment, and the moment is part of the point.
 *
 * Every call files a new plan. The service can hand back an open plan of the
 * same rack with the same fingerprint instead (`reuse`), and /api/approvals
 * finds one with `?rackId=&open=1`; this path does not, because two people
 * comparing the same rack each get their own plan and always have.
 */
function create({ scanId = null, rackId = null, rackUid = null, rackName = null, report,
                  by = null, orgId = null, tenantId = null, parentPlanId = null, reuse = false }) {
  return shape.legacyPlan(service.create({
    scanId, rackId, rackUid, rackName, report, orgId, tenantId, parentPlanId,
    actor: actorOf(by), reuse: Boolean(reuse), ownOnly: Boolean(reuse),
  }));
}

/** A one-line count of what a plan holds, for a list row and the events log. */
const summarise = (items, result = null) => shape.summarise(items, result);

/**
 * Whether a caller from `orgId` may see a plan owned by `planOrgId`.
 *
 * The organisation half of the visibility rule, on its own: a plan belongs to
 * one organisation and only that organisation sees it - no owner bypass,
 * because "the owner sees everything" is exactly the cross-tenant leak this
 * closes. A plan with no organisation matches nobody here, which is why the
 * whole rule needs the caller and not just their organisation id; see
 * visibleTo() below. Kept because callers ask it this way.
 */
const canSee = (planOrgId, orgId) => planOrgId != null && planOrgId === orgId;

/**
 * THE visibility rule, whole: may this person see this plan?
 *
 * The caller's organisation equals the plan's when the plan has one;
 * otherwise the caller is the account that raised it. One function
 * (lib/approvals/service.canSee), used by the read AND by the list, so a
 * plan that cannot be opened is never listed. On the live server on
 * 18 September the two disagreed: as the platform owner, GET /api/nb/plans
 * listed four plans and every one answered "no such plan".
 */
const visibleTo = (plan, user) => service.canSee(plan, service.actorOf(user));

/** The same rule as a list filter: { orgId, userId, username } from a req.user. */
const seenBy = (user) => service.seenByOf(service.actorOf(user));

/**
 * The index rows, filtered. `status` is one of the four old words and covers
 * every workflow status that word stands for, so `status=submitted` is still
 * the admin's inbox and now also catches a plan that is out with an assignee.
 */
function list({ scanId = null, rackId = null, status = null, orgId = undefined,
                seenBy: who = null, createdBy = null, limit = 50 } = {}) {
  const f = { limit: Math.min(Number(limit) || 50, 200) };
  if (scanId != null) f.scanId = scanId;
  if (rackId != null) f.rackId = rackId;
  if (status != null) f.status = shape.statusesForLegacy(status);
  // `seenBy` is the visibility rule and is what a route should pass: it lists
  // exactly what that person could open. `orgId` is the plain organisation
  // filter, for a server-side caller that has already decided who is asking.
  if (who) f.seenBy = who;
  if (orgId !== undefined) f.orgId = orgId;
  if (createdBy != null) f.createdBy = createdBy;
  return store.listPlans(f)
    .map((p) => shape.legacyIndexRow(p, store.itemsOf(p.id), store.ticketsOf(p.id)));
}

/** The interfaces that follow a device, by its uid. */
const childrenOf = (plan, uid) => shape.childrenOf(plan && plan.items, uid);

/** True when the item has come back from whoever was asked to look at it. */
const hasFinding = (item) => Boolean(item && item.ticket && item.ticket.status === 'resolved');

/** The uids this plan must NOT write: something a person was asked about and did not approve. */
const excludedUids = (plan) => shape.excludedUids(plan && plan.items);

/** The same snapshot, minus what was not approved. */
const filterSnapshot = (snapshot, excluded) => shape.filterSnapshot(snapshot, excluded);

/** True when nothing is still waiting on somebody. */
const isSettled = (plan) => shape.isSettled(plan && plan.items);

/** The items a whole-rack assign would take: pending, checkable at the rack, never assigned. */
const assignableUids = (planId) => service.assignableUids(planId);

/**
 * Record what the admin decided about one or more items.
 *
 *   { uid, decision: 'ticketed', assignee, assigneeId, assigneeEmail, note }
 *   { uid, decision: 'approved' | 'rejected', note }
 *
 * The admin assigns before they decide. Approve and reject are refused on an
 * item nobody has been asked to check, on one whose ticket is still open, and
 * on one that came back with nothing said - the refusal is
 * `{ uid, why: 'assign first' }`. The one exception is an item that cannot be
 * checked at the rack at all (a rebind): it is decided as it stands, because
 * it cannot be assigned either and the two rules together would otherwise
 * leave it undecidable for ever. This is rule 2 of the frozen workflow, held
 * on the server, so no screen can approve from a desk.
 *
 * A decision on a device is a decision on the ports that follow it.
 *
 * The two kinds of move go to two different operations, so `applied` and
 * `refused` list the assignments first and the approvals and rejections after,
 * each group in the order it was sent. A caller that sends one kind at a time,
 * which is every caller there is, sees its own order untouched.
 */
function decide(id, decisions, { by } = {}) {
  const plan = store.getPlan(id, { heavy: false });
  if (!plan) return { error: 'no such plan' };
  if (shape.legacyStatus(plan.status) === 'applied') {
    return { error: 'this plan has already been written' };
  }
  const rows = Array.isArray(decisions) ? decisions : [];
  const actor = actorOf(by);
  const applied = [];
  const refused = [];
  const take = (out) => {
    if (out && out.error && !out.applied) { refused.push({ uid: null, why: out.error }); return; }
    applied.push(...(out.applied || []));
    refused.push(...(out.refused || []));
  };

  const ticketed = rows.filter((d) => d && typeof d === 'object' && d.decision === 'ticketed');
  const judged = rows.filter((d) => !d || typeof d !== 'object' || d.decision !== 'ticketed');
  if (ticketed.length) take(service.assignLocal(id, ticketed, { actor }));
  // A decision this interface does not know by name - 'pending', or a word
  // nobody has heard of - is refused by the service in the same words the old
  // one used, so nothing here has to check it twice.
  if (judged.length) take(service.decideItems(id, judged, { actor }));
  return { plan: get(id), applied, refused };
}

/**
 * Whoever the ticket went to has been and looked.
 *
 * Their answer comes back to the admin; it does not write anything by itself.
 * The item returns to pending so the admin decides again, now knowing what was
 * actually in the rack.
 */
function resolveTicket(id, uid, { by, finding, outcome = 'checked' } = {}) {
  const out = service.resolveTicket(id, uid, { finding, outcome }, { actor: actorOf(by) });
  if (out && out.error) return { error: out.error };
  const plan = get(id);
  return { plan, item: plan.items.find((i) => i.uid === uid) };
}

/**
 * A technician hands the comparison to the admin.
 *
 * Nothing about the plan changes except who is now waiting on it. The
 * technician's note travels with it, because "three of these look wrong to me"
 * is worth more than the diff on its own.
 */
function submit(id, { by, note, items = null } = {}) {
  const out = service.submit(id, { note, items, actor: actorOf(by) });
  if (out && out.error) return { error: out.error };
  return { plan: get(id), already: Boolean(out.already) };
}

/**
 * Record what the write actually did.
 *
 * Every object went through: the plan is applied and closed. NetBox refused
 * any of them: the plan is write_failed, not applied. What was written is
 * written and is not undone (the writer never deletes); what failed is listed
 * by uid with NetBox's reason, so the admin knows exactly which objects to
 * look at, and the plan stays open to a second export.
 */
function markApplied(id, { by, result } = {}) {
  const out = service.finishWrite(id, { actor: actorOf(by), result });
  return out ? get(id) : null;
}

/**
 * Apply what ServiceNow now says about the incidents we raised.
 *
 * ServiceNow owns whether an incident is open or closed - we read it and never
 * argue with it. A closed incident returns its item to the admin as UNDECIDED,
 * exactly as a person resolving it by hand would.
 */
function applyTicketStates(id, states, { by = 'ServiceNow' } = {}) {
  const out = service.applyTicketStates(id, states, { by });
  return { plan: out.plan ? get(id) : null, changed: out.changed };
}

/** Every open incident a plan is waiting on, as sys_ids. Takes a plan or its id. */
const openSysIds = (plan) => service.openSysIds(plan && typeof plan === 'object' ? plan.id : plan);

/**
 * Write an old-shape plan object back.
 *
 * The routes and the tests read a plan, change something on it in memory and
 * hand it back - the ServiceNow number stamped onto a ticket, the timestamp of
 * the email that went out. There is no file to rewrite any more, so this walks
 * the object and writes the fields that can honestly be changed this way:
 * a decision on an item, and the ticket on it.
 *
 * A status change is NOT one of them. Moving a plan from one status to the
 * next is the workflow's business - submit(), decide(), markApplied() - and
 * silently taking one from an object handed back here would put a plan
 * anywhere at all with nothing in the history to say who did it.
 */
function save(plan) {
  if (!plan || plan.id == null) return plan;
  const id = Number(plan.id);
  if (!store.getPlan(id, { heavy: false })) return plan;
  store.tx(() => {
    for (const item of plan.items || []) {
      const row = store.getItem(id, item.uid);
      if (!row) continue;
      store.updateItem(id, item.uid, {
        decision: item.decision === 'not applicable' ? 'not_applicable' : item.decision,
        decidedBy: item.decidedBy ?? null,
        decidedAt: item.decidedAt ?? null,
        note: item.note ?? null,
      }, { touch: false });
      const t = item.ticket;
      // A port's ticket is a reference to its device's; the device row is the
      // ticket, and writing the reference back would overwrite it with less.
      if (!t || t.sharedWith) continue;
      if (!store.getTicket(id, item.uid)) continue;
      store.updateTicket(id, item.uid, {
        assignee: t.assignee ?? null,
        assigneeId: t.assigneeId ?? null,
        assigneeEmail: t.assigneeEmail ?? null,
        spoc: t.spoc ?? null,
        scope: t.scope ?? null,
        // `state` carries the workflow's own word; `status` is the app's, which
        // calls accepted, in progress and pending all "open". Prefer the one
        // that says more, so a save cannot walk a ticket backwards.
        status: t.state || t.status || 'open',
        question: t.question ?? null,
        finding: t.finding ?? null,
        outcome: t.outcome ?? null,
        resolvedBy: t.resolvedBy ?? null,
        resolvedAt: t.resolvedAt ?? null,
        closedBy: t.closedBy ?? null,
        closedAt: t.closedAt ?? null,
        closedWith: t.closedWith ?? null,
        external: t.external ?? null,
        emailedAt: t.emailedAt ?? null,
        emailNote: t.emailNote ?? null,
      }, { touch: false });
    }
    store.touchPlan(id);
  });
  return get(id);
}

module.exports = {
  create, get, list, save, decide, resolveTicket, markApplied, submit,
  applyTicketStates, openSysIds, childrenOf, hasFinding,
  fingerprint, excludedUids, filterSnapshot, isSettled, summarise,
  canSee, visibleTo, seenBy, assignableUids,
  SUPPORTING, ACTIONABLE, STATUSES, PLANS_DIR,
};
