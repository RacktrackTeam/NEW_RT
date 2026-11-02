/**
 * The name a person would call this rack.
 *
 * A scan is filed under a hash - RK-5B81BE87 - because a photograph arrives
 * before anybody has said which rack it is of. The name comes later, from one
 * of three places, and every screen that heads a page with a rack has to look
 * in all three or it ends up headed "Unidentified rack" while the devices
 * listed under it all carry the rack's name (the owner, 23 September 2026).
 *
 *   what the scan was filed with   the name typed at the time, if any
 *   who confirmed the rack         lib/rack_identity, for a known site
 *   the rack itself                lib/estate, which carries the scan's id
 *                                  once a scan has been bound to a rack
 *
 * The hash is never a name: a caller that gets null here says so in words.
 */
/* A scan's own id, in the shape lib/rack_access mints and accepts them. It is
   matched here rather than the narrower hex-only pattern the screens use,
   because the id is an id whatever letters fell out of the hash. */
const UNNAMED = /^RK-[A-Za-z0-9]{4,32}$/;

/** A name somebody would recognise, or null for a hash and for nothing. */
function realName(name) {
  const s = typeof name === 'string' ? name.trim() : '';
  if (!s || UNNAMED.test(s)) return null;
  return s;
}

/**
 * `rackId` is the scan's own id. `tenantId` is the site when the caller knows
 * it, and `given` is the name the caller already holds. Returns a name or null.
 */
function rackNameFor(rackId, { tenantId = null, given = null } = {}) {
  const mine = realName(given);
  if (mine) return mine;
  if (!rackId) return null;

  if (tenantId != null) {
    try {
      const bound = require('./rack_identity').confirmedRack(tenantId, rackId);
      const named = bound && bound.rack ? realName(bound.rack.name || bound.rack.facility_id) : null;
      if (named) return named;
    } catch { /* the main database may be shut; the next look is cheap */ }
    try {
      const rack = require('./estate').getRackByRackId(tenantId, rackId);
      const named = rack ? realName(rack.name || rack.facility_id) : null;
      if (named) return named;
    } catch { /* same */ }
  }

  // No site in hand. A rack id is minted for one rack, so both look-ups find
  // their rack without one - which is the case a report is in: it knows the
  // photograph, not the estate around it.
  try {
    const bound = require('./rack_identity').confirmedRackAnywhere(rackId);
    const named = bound && bound.rack ? realName(bound.rack.name || bound.rack.facility_id) : null;
    if (named) return named;
  } catch { /* same */ }
  try {
    const rack = require('./estate').findRackByRackId(rackId);
    const named = rack ? realName(rack.name || rack.facility_id) : null;
    if (named) return named;
  } catch { /* same */ }
  return null;
}

module.exports = { rackNameFor, UNNAMED };
