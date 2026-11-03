// Rack authorization policy.
//
// This is the check that decides whether one customer can read another's rack.
// It previously existed as four copies across app.js and two proxy modules that
// had drifted apart - one had no check at all, and two disagreed about a user
// with no Site. These tests pin the policy so the next
// copy cannot silently differ, and they run against the real module with a
// stub tenant store so no database is needed.

const test = require('node:test');
const assert = require('node:assert');

const { canAccessRack, isValidRackId, rackOwnershipParam } = require('../lib/rack_access');

// Stub tenant store: site 11 owns RK-AAAA1111; org 10 contains site 11.
const tenant = {
  tenantOwnsRack: (tenantId, rackId) => tenantId === 11 && rackId === 'RK-AAAA1111',
  rackInOrg: (rackId, orgId) => orgId === 10 && rackId === 'RK-AAAA1111',
};

const OWNED = 'RK-AAAA1111';
const FOREIGN = 'RK-BBBB2222';

test('unauthenticated callers are refused', () => {
  // This returned TRUE for a while, as a compatibility shim for shipped app
  // builds that sent no Authorization header. It made every tenant check built
  // on top of it a no-op against exactly the caller that matters most.
  assert.equal(canAccessRack(null, OWNED, tenant), false);
  assert.equal(canAccessRack(undefined, OWNED, tenant), false);
});

test('the platform owner reaches every rack', () => {
  const owner = { role: 'owner', tenantId: 2 };
  assert.equal(canAccessRack(owner, OWNED, tenant), true);
  assert.equal(canAccessRack(owner, FOREIGN, tenant), true);
});

test('an org admin reaches racks held by a Site in their org, and no others', () => {
  const admin = { role: 'org_admin', organizationId: 10, tenantId: 2 };
  assert.equal(canAccessRack(admin, OWNED, tenant), true);
  assert.equal(canAccessRack(admin, FOREIGN, tenant), false);

  const otherOrg = { role: 'org_admin', organizationId: 77, tenantId: 2 };
  assert.equal(canAccessRack(otherOrg, OWNED, tenant), false);
});

test('a member reaches only their own Site', () => {
  assert.equal(canAccessRack({ role: 'member', tenantId: 11 }, OWNED, tenant), true);
  assert.equal(canAccessRack({ role: 'member', tenantId: 11 }, FOREIGN, tenant), false);
  assert.equal(canAccessRack({ role: 'member', tenantId: 99 }, OWNED, tenant), false);
});

test('a principal with no Site inherits nothing', () => {
  // The regression this pins: one guard let a NULL tenant_id through to every
  // rack on the platform while its twin refused the same principal. Fails
  // closed, in every shape the two call styles produce.
  assert.equal(canAccessRack({ role: 'member', tenantId: null }, OWNED, tenant), false);
  assert.equal(canAccessRack({ role: 'member', tenant_id: null }, OWNED, tenant), false);
  assert.equal(canAccessRack({ role: 'member' }, OWNED, tenant), false);
});

test('both principal shapes are accepted', () => {
  // JWT payloads are camelCase, req.user rows are snake_case. A guard that
  // understood only one shape would read undefined and fail open or closed by
  // accident rather than by policy.
  assert.equal(canAccessRack({ role: 'member', tenant_id: 11 }, OWNED, tenant), true);
  assert.equal(canAccessRack({ role: 'org_admin', organization_id: 10 }, OWNED, tenant), true);
});

test('rack ids are shape-validated', () => {
  assert.ok(isValidRackId('RK-AAAA1111'));
  assert.ok(!isValidRackId('../server/data'), 'traversal must not pass');
  assert.ok(!isValidRackId('RK-'), 'too short');
  assert.ok(!isValidRackId(''));
  assert.ok(!isValidRackId(null));
  assert.ok(!isValidRackId('RK-AAAA/../..'));
});

// ── the Express param guard ──────────────────────────────────────────
function runGuard(guard, { user = null, rackId = OWNED, query = {}, method = 'GET' } = {}) {
  const req = { user, query, method, path: '/api/scan/' + rackId };
  const out = { status: null, body: null, nexted: false };
  const res = {
    status(code) { out.status = code; return this; },
    json(body) { out.body = body; return this; },
  };
  guard(req, res, () => { out.nexted = true; }, rackId);
  return out;
}

test('the param guard 400s a malformed rack id before touching auth', () => {
  const guard = rackOwnershipParam({ tenant });
  const r = runGuard(guard, { user: { role: 'owner' }, rackId: '../server/data' });
  assert.equal(r.status, 400);
  assert.equal(r.nexted, false);
});

test('the param guard 401s an unauthenticated caller and 404s an unauthorized one', () => {
  const guard = rackOwnershipParam({ tenant });

  const anon = runGuard(guard, { user: null });
  assert.equal(anon.status, 401);

  // 404 rather than 403 on purpose: a 403 confirms the rack exists, which is
  // itself the cross-tenant disclosure we are preventing.
  const wrongTenant = runGuard(guard, { user: { role: 'member', tenant_id: 99 } });
  assert.equal(wrongTenant.status, 404);
  assert.equal(wrongTenant.nexted, false);
});

test('the param guard admits an authorized caller', () => {
  const guard = rackOwnershipParam({ tenant });
  const r = runGuard(guard, { user: { role: 'member', tenant_id: 11 } });
  assert.equal(r.nexted, true);
  assert.equal(r.status, null);
});

test('a capability token admits the caller for that rack only', () => {
  // A capability that lets a caller read one rack without a session must not
  // become a general skeleton key.
  const guard = rackOwnershipParam({
    tenant,
    allow: (req, rackId) => req.query.t === 'good' && rackId === OWNED,
  });

  assert.equal(runGuard(guard, { query: { t: 'good' } }).nexted, true);
  assert.equal(runGuard(guard, { query: { t: 'good' }, rackId: FOREIGN }).status, 401);
  assert.equal(runGuard(guard, { query: { t: 'bad' } }).status, 401);
});

test('a capability token is read-only', () => {
  // A capability is a READ capability. It used to admit any method, so a
  // forwarded link could POST to the rack routes that carry no requireAuth of
  // their own - mutating OCR device lists and side labels with no
  // attributable identity.
  const guard = rackOwnershipParam({
    tenant,
    allow: (req) => req.query.t === 'good',
  });

  assert.equal(runGuard(guard, { query: { t: 'good' }, method: 'GET' }).nexted, true);
  assert.equal(runGuard(guard, { query: { t: 'good' }, method: 'HEAD' }).nexted, true);
  for (const method of ['POST', 'PUT', 'PATCH', 'DELETE']) {
    const r = runGuard(guard, { query: { t: 'good' }, method });
    assert.equal(r.status, 401, `${method} with a capability token must be refused`);
    assert.equal(r.nexted, false);
  }
});

test('shape is validated before the capability hatch', () => {
  // The hatch used to run first, so a token minted for `../../server/data`
  // skipped the only check that exists because rackId reaches path.join.
  const guard = rackOwnershipParam({ tenant, allow: () => true });
  const r = runGuard(guard, { rackId: '../../server/data' });
  assert.equal(r.status, 400);
  assert.equal(r.nexted, false);
});
