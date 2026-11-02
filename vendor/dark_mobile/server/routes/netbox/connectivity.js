/**
 * The ports section of a drift check, for the phone.
 *
 * GET /api/nb/plans/:planId/connectivity - the photo against the switch and
 * NetBox, socket by socket (lib/approvals/connectivity). A read, and the
 * technician who raised the check may have it.
 *
 * A router of its own, mounted in app.js on the plans prefix BEFORE the plans
 * router and behind the same login gate. It answers this one path and nothing
 * else, so every other request falls through to routes/netbox/plans.js
 * untouched. Who may open a plan is the rule that file applies to a read: a
 * plan the caller's organisation does not own is 404, a technician sees the
 * plans they raised, an admin every plan in the organisation.
 */
const express = require('express');

const plans = require('../../lib/netbox/plans');
const connectivity = require('../../lib/approvals/connectivity');
const { netboxFor } = require('../../lib/approvals/connections');
const gates = require('./gates');

const router = express.Router();

const who = (req) => (req.user && (req.user.username || req.user.email)) || null;
const mine = (req, plan) => plan && plans.visibleTo(plan, req.user)
  && (gates.isAdmin(req) || plan.createdBy === who(req));

router.get('/:planId/connectivity', gates.technician, async (req, res, next) => {
  try {
    const plan = plans.get(req.params.planId);
    if (!mine(req, plan)) return res.status(404).json({ error: 'no such plan' });
    const client = netboxFor({ orgId: req.user?.organization_id ?? null, userId: req.user?.id });
    return res.json(await connectivity.forPlan(plan, plan.items, { client }));
  } catch (err) {
    return next(err);
  }
});

module.exports = router;
