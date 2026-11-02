/**
 * Who may use which /api/nb route.
 *
 * The mount authenticates (auth.requireAuth puts req.user on the request);
 * each route then says, on the route itself, which roles it is for. The list
 * is short and deliberate, from the frozen drift workflow
 * (docs/design/drift-approval-workflow.md):
 *
 *   technician  owner, org_admin, site_manager and member. A member is the
 *               person at the rack: they adopt a scan, compare it to NetBox,
 *               read their own plan and hand it to the admin. Nothing else.
 *   admin       owner, org_admin and site_manager. They read every plan and
 *               ticket in their organisation. Who may decide a check is not a
 *               role any more: it is the SPOC the check is with, or an
 *               organization admin, and never the person who sent it. That
 *               rule needs the plan in hand, so lib/approvals/service.js holds
 *               it and the doors to deciding and approving under
 *               /api/approvals are open to every reader.
 *
 * The Approvals sub-application (/api/approvals) is gated from the same list,
 * with the three roles the manager's specification adds:
 *
 *   approver    owner, org_admin and approver. The second signature, when
 *               an organization asks for two, and nothing else on a plan.
 *   auditor     owner, org_admin and auditor. Reads everything, writes
 *               nothing, not even a comment.
 *   readers     everyone who may open the sub-application at all. What each
 *               of them actually SEES is decided in lib/approvals/service.js,
 *               which scopes every list to the caller's organisation and, for
 *               a technician or a site manager, to their own Site. The gate is
 *               here so a role nobody planned for is refused at the door.
 *   writer      owner and org_admin: the two roles that write to NetBox.
 *
 * A route with no gate is a route a member can reach, so every route under
 * scans, plans and netbox carries one. The tests in test/netbox/rules.test.js
 * check the member's six routes answer and the rest refuse.
 */
const ADMINS = ['owner', 'org_admin', 'site_manager'];
const TECHNICIANS = [...ADMINS, 'member'];
const APPROVERS = ['owner', 'org_admin', 'approver'];
const AUDITORS = ['owner', 'org_admin', 'auditor'];
const WRITERS = ['owner', 'org_admin'];
const READERS = [...new Set([...TECHNICIANS, 'approver', 'auditor'])];

/** A gate that lets `roles` through and answers 403 to everyone else. */
function only(roles, message = 'Insufficient permissions') {
  return (req, res, next) => {
    if (!req.user) return res.status(401).json({ error: 'Authentication required' });
    if (roles.includes(req.user.role)) return next();
    return res.status(403).json({ error: message });
  };
}

const isAdmin = (req) => ADMINS.includes(req.user?.role);

module.exports = {
  only,
  admin: only(ADMINS),
  technician: only(TECHNICIANS),
  // The Approvals sub-application. Each message is a plain sentence, because
  // a person reads it: "Insufficient permissions" tells nobody what to do next.
  approver: only(APPROVERS, 'Approving and rejecting are for an approver or an organization admin.'),
  auditor: only(AUDITORS, 'This is for an auditor or an organization admin.'),
  writer: only(WRITERS, 'An admin approves and writes. Send this plan to yours to review.'),
  readers: only(READERS, 'Your account has no part in the approval workflow.'),
  isAdmin,
  ADMINS,
  TECHNICIANS,
  APPROVERS,
  AUDITORS,
  WRITERS,
  READERS,
};
