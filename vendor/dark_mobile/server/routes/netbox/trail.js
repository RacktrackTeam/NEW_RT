/**
 * The audit trail of the drift workflow.
 *
 * Every transition a plan goes through is written to audit_log, the same
 * table every other sensitive action in RackTrack lands in, so "who approved
 * this, and when" is answered from one place. One row per step, per item
 * where the step is per item:
 *
 *   drift.submit   the technician handed the plan over
 *   drift.assign   one item handed to a person        { uid, assignee, assigneeId, incident }
 *   drift.resolve  the assignee reported back         { uid, outcome, finding }
 *   drift.decide   the admin approved or rejected     { uid, decision, note }
 *   drift.write    the write ran, ok or fail          { counts, written, failed }
 *
 * The target is always the plan (drift_plan, planId). The row's tenant is the
 * plan's own when it has one: the plan belongs to the Site the rack was
 * scanned under, which for an owner or an org admin is not their own row's.
 *
 * audit.log() never throws, and this wraps it once more: a broken audit
 * table must not stop an approval or a write.
 */
const audit = require('../../audit');

function record(req, plan, action, { status = 'ok', payload = null, error = null } = {}) {
  try {
    const user = req.user
      ? { ...req.user, tenant_id: plan?.tenantId ?? req.user.tenant_id ?? null }
      : null;
    audit.log({
      req, user, action, status, error,
      targetType: 'drift_plan',
      targetId: plan?.id ?? null,
      payload,
    });
  } catch { /* the trail is a record of the request, never a reason to fail it */ }
}

module.exports = { record };
