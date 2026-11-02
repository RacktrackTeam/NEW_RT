/**
 * One NetBox client for a signed-in user.
 *
 * The same lookup the export and plan routes each keep a copy of, in the same
 * order: the organisation's NetBox from Data Sources first, then the caller's
 * own (an owner account belongs to no organisation), then the server env for a
 * single-tenant install. Null when none of the three is set up, so a caller
 * that only wants to look something up can carry on without NetBox instead of
 * failing.
 */
const cfg = require('./config');
const profiles = require('../connection_profiles');
const { NetBox } = require('./netbox');

function clientForUser(user) {
  const orgId = user?.organization_id;
  const creds = (orgId ? profiles.resolveCredsForOrg(orgId, 'netbox') : null)
    || (user?.id ? profiles.resolveCredsForType(user.id, 'netbox') : null);
  if (creds?.secret?.base_url) {
    return new NetBox(creds.secret.base_url, creds.secret.token || '');
  }
  // The env fallback needs both halves, as the export route's target() does:
  // config.js defaults NETBOX_URL to localhost, and a client pointed there with
  // no token would only stall every lookup on a machine that has no NetBox.
  if (cfg.NETBOX_URL && cfg.NETBOX_TOKEN) return new NetBox(cfg.NETBOX_URL, cfg.NETBOX_TOKEN);
  return null;
}

module.exports = { clientForUser };
