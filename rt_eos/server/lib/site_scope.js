/**
 * "My site": which Site (tenant) a request is about, and what the caller may
 * do there.
 *
 * Everyone works on their own Site. The owner and an organisation admin may
 * name another one with ?siteId= (or siteId in the body); anyone else who
 * names a Site gets their own only when it is the same one. What the caller
 * may do comes from lib/estate.accessLevel: 'write' for the owner, the
 * organisation's admin and the Site's manager, 'read' for a member of the
 * Site. A Site the caller cannot see is "not found", never "forbidden".
 */
'use strict';

const estate = require('./estate');

class ScopeError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

const ADMIN_ROLES = new Set(['owner', 'org_admin']);

/**
 * @param {object} user   req.user
 * @param {*} asked       the siteId the request named, if any
 * @returns {{ siteId: number, orgId: number|null, level: 'read'|'write', site: object }}
 */
function resolveSite(user, asked) {
  if (!user) throw new ScopeError(401, 'Sign in first.');
  const own = Number(user.tenant_id ?? user.tenantId ?? 0) || null;
  let siteId = own;
  if (asked !== undefined && asked !== null && String(asked).trim() !== '') {
    if (!/^\d+$/.test(String(asked).trim())) throw new ScopeError(400, 'That site id is not valid.');
    const n = Number(String(asked).trim());
    if (ADMIN_ROLES.has(user.role)) siteId = n;
    else if (n !== own) throw new ScopeError(404, 'Site not found.');
  }
  if (!siteId) throw new ScopeError(404, 'You are not part of a site yet.');
  const level = estate.accessLevel(user, siteId);
  if (!level) throw new ScopeError(404, 'Site not found.');
  const site = estate.getTenant(siteId);
  return { siteId, orgId: site?.organization_id ?? null, level, site };
}

/** The same, straight from a request (query first, then body). */
function siteOf(req) {
  return resolveSite(req.user, req.query?.siteId ?? req.body?.siteId);
}

module.exports = { resolveSite, siteOf, ScopeError };
