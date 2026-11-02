/**
 * Recognise the rack.
 *
 * A scan is identified only by a hash of its photo (the RK- id). That hash is
 * never the name the customer's own record uses, so the SPOC lookup and the
 * NetBox write land on a fresh rack instead of the real one. This resolves the
 * scan to the rack the customer already has, so both land on the right rack.
 *
 * The honest half, phase 1. It matches only on what we already store, and it
 * refuses to guess:
 *
 *   - A person's answer comes first. Where somebody has said which rack this
 *     scan is (lib/rack_identity, the confirm route), that rack is the rack and
 *     this resolver's own weaker guesses are not consulted.
 *   - Otherwise: a scan is tied to a SPACE when it is captured. A space holds
 *     racks the admin typed in setup, each carrying a real name and, often, a
 *     facility id.
 *   - We match the scan to one of those typed racks, and only when it is
 *     unambiguous: the scan's own chosen name equals a typed rack in the space,
 *     or the space holds exactly one typed rack.
 *   - Then we look that rack up in NetBox (by facility id first, then name) to
 *     get the name NetBox actually uses.
 *
 * Anything unclear stays unresolved and the caller keeps today's behaviour. We
 * never pick between two racks on nothing, and we never write here.
 */

const estate = require('../estate');
const find = require('./find');

/**
 * The rack a person said this scan is, or null when nobody has said.
 *
 * Required at the point of use rather than at the top of the file: lib/rack_identity
 * requires this module, and requiring it back at load time would be a cycle.
 * Anything that goes wrong reading it means "nobody has confirmed anything": a
 * confirmation may change which rack this resolver answers with, never whether
 * it answers at all.
 */
function personsChoice(tenantId, rackId) {
  try {
    const bound = require('../rack_identity').confirmedRack(tenantId, rackId);
    return bound && bound.rack && bound.rack.id != null ? bound : null;
  } catch { return null; }
}

/**
 * The write key a person's confirmation gives this scan, or null when nobody
 * has confirmed it. One indexed read of the local database: no NetBox, no
 * photo, so a caller may ask it on every open.
 */
function confirmedKeyFor(tenantId, rackId) {
  const chosen = personsChoice(tenantId, rackId);
  return chosen ? rackKeyFor(tenantId, chosen.rack.id) : null;
}

/**
 * The write key for a typed rack: t<tenant>:<racks_known.id>.
 *
 * It stands where the photo hash stood in every rack-scoped uid, so the rack
 * reads rack:t7:5, a device dev:t7:5:u10 and a port if:dev:t7:5:u10:1. Minted
 * from the row's own id, which is unique across every tenant that shares a
 * NetBox, and never from anything the admin typed: two tenants each typing
 * RK-ROW1 must not merge their racks in a NetBox both of them use.
 */
const rackKeyFor = (tenantId, rowId) => `t${Number(tenantId)}:${Number(rowId)}`;

/** Choose the one typed rack this scan is, or none when it cannot be told. */
function pickCandidate(candidates, scanName) {
  if (!candidates.length) return { rack: null, why: 'no rack has been set up in this space' };
  if (scanName) {
    const want = String(scanName).trim().toLowerCase();
    const byName = candidates.find((r) => r.name && r.name.trim().toLowerCase() === want);
    if (byName) return { rack: byName, source: 'name', why: 'its name matches a rack set up in this space' };
  }
  if (candidates.length === 1) {
    return { rack: candidates[0], source: 'space', why: 'the only rack set up in this space' };
  }
  return { rack: null, source: 'space',
    why: `${candidates.length} racks are set up in this space and nothing tells them apart yet` };
}

/**
 * The site in the customer's record that this scan is at, or nothing.
 *
 * Asked by name, exactly, and refused when two sites answer. Everything below
 * depends on it: the same rack name and the same rack id are used at more than
 * one site, so a rack lookup that is not filtered by site can name the wrong
 * building - which is what the first design of this feature did, and what all
 * three reviewers refuted it for.
 */
async function findSite(client, siteName) {
  const want = String(siteName ?? '').trim();
  if (!client || !want) {
    return { id: null, why: 'no site was named, so the record was not asked which site this is' };
  }
  try {
    const r = await client.get('/api/dcim/sites/', { name: want, limit: 2 });
    const rows = ((r && r.results) || []).filter(
      (s) => String(s.name ?? '').trim().toLowerCase() === want.toLowerCase());
    if (rows.length === 1) return { id: rows[0].id ?? null, why: `the site ${want}` };
    if (rows.length > 1) {
      return { id: null, why: `${rows.length} sites in the record are called ${want}, so nothing is claimed` };
    }
    return { id: null, why: `the record has no site called ${want}` };
  } catch {
    return { id: null, why: 'the record could not be asked which site this is' };
  }
}

/**
 * Find the customer's rack in NetBox, inside one site, refusing a tie.
 *
 * Inside a site the shared resolver does the work: facility id first because it
 * is the customer's own key, then the name, two rows asked for every time and a
 * refusal that names both when two answer.
 *
 * With no site, NOTHING is looked up - not the name, and not the customer's own
 * rack id either. This used to fall back to an unscoped facility-id lookup
 * across the whole NetBox instance and report a confident match from it, which
 * is the very thing the design review before this work refuted: a rack id is
 * unique inside a site and nowhere else, one instance holds every customer's
 * estate, and a single answer across all of it is a coincidence, not a proof.
 * An absent scope is a refusal, and the refusal names what would settle it.
 */
async function findInNetBox(client, known, { siteId = null } = {}) {
  if (!client || !known) return { none: true, why: 'there is nothing to look this rack up by' };
  const site = siteId === null || siteId === undefined || siteId === '' ? null : Number(siteId);
  if (!Number.isFinite(site)) {
    return { none: true, why: 'the site this rack is at was not found in the customer\'s record, so '
      + 'there is nothing to look the rack up inside. The same rack id and the same rack name are '
      + 'used at more than one site, so a rack found without a site proves nothing. Name the site '
      + 'in the customer\'s record that this rack is at, then try again' };
  }
  return find.findRack(client, {
    siteId: site, facilityId: known.facility_id || null, name: known.name || null,
  });
}

/**
 * One NetBox rack by its id: the rack a person picked by hand when they
 * confirmed. Stronger than a lookup by name, because it is the record they were
 * looking at. Null when it cannot be had, and then the name lookup runs as usual.
 */
async function findInNetBoxById(client, netboxRackId) {
  if (!client || netboxRackId == null) return null;
  try {
    const r = await client.get(`/api/dcim/racks/${Number(netboxRackId)}/`);
    return r && r.id != null && r.name ? r : null;
  } catch { return null; }
}

/**
 * Resolve a scanned rack to the customer's rack.
 *
 * Returns a stable shape whatever happens, so a caller can always read `.name`:
 *   { name, netboxId, knownRackId, spaceId, tenantId, rackKey, confidence, source, why }
 * `name` is the rack to use downstream: the NetBox name when confirmed, else the
 * typed name, else the fallback the caller passed. `confidence` is one of
 * 'confirmed' (found in NetBox), 'known' (a typed rack, not yet in NetBox), or
 * 'none' (unresolved — behave exactly as before).
 *
 * `rackKey` is the key NetBox uids are built on, t<tenant>:<row>, standing
 * where the photo hash stood (rack:t7:5, dev:t7:5:u10). It is set only when
 * the scan was identified explicitly (source 'confirmed', 'name' or
 * 'set-up-directly') and is null under the only-rack-in-the-space rule, which
 * may name the rack and its contact but never chooses the write key. An
 * unresolved result (confidence 'none') never carries a key, with one exception:
 * a rack a person confirmed keeps its key even when that rack has no name to
 * use, because the key is built from the record's own id and not from its name.
 */
async function resolveRack(client, {
  tenantId, rackId, scanName = null, fallbackName = null, siteName = null,
} = {}) {
  const out = {
    name: fallbackName ?? (rackId ?? null),
    netboxId: null, knownRackId: null, spaceId: null,
    tenantId: tenantId ?? null,
    rackKey: null,
    // The site in the customer's record this scan is at, and how the rack was
    // found in it. `netboxBy` is what decides whether the answer may be written:
    // a rack found by its own rack id is the customer's key for it, a rack found
    // by name is a question. The writer refuses to bind on a name.
    siteId: null, siteWhy: null, netboxBy: null, netboxWhy: null,
    confidence: 'none', source: 'scan',
    why: 'this scan is not tied to a rack the customer has set up',
  };
  if (tenantId == null || !rackId) return out;

  let bound = null;
  try { bound = estate.getRackByRackId(tenantId, rackId); } catch { bound = null; }
  // A person's answer to "which rack is this" outranks everything below it, and
  // it is why a scan with no record of its own can still resolve: confirming a
  // record that already exists does not name the scan's own row, so without this
  // the answer a person gave reached the screen and never reached the write.
  const chosen = personsChoice(tenantId, rackId);
  if (!bound && !chosen) return out;
  if (bound) out.spaceId = bound.space_id ?? null;

  // If the scan's own row was typed (it carries a name or a facility id), it is
  // already the known rack — no need to look across the space.
  let known = chosen ? chosen.rack : ((bound.name || bound.facility_id) ? bound : null);
  // How the typed rack was chosen. Kept apart from `source`, which NetBox may
  // refine to 'facility-id' below: the key depends on how the scan was tied to
  // a typed rack, not on how NetBox confirmed that rack.
  let how;
  if (chosen) {
    how = 'confirmed';
    out.source = how;
    out.why = 'a person confirmed which rack this is';
  } else if (known) {
    how = 'set-up-directly';
    out.source = how;
    out.why = 'this rack was set up directly';
  } else {
    if (bound.space_id == null) return out;
    let inSpace = [];
    try { inSpace = estate.listRacks(tenantId, bound.space_id) || []; } catch { inSpace = []; }
    const candidates = inSpace.filter(
      (r) => String(r.rack_id) !== String(rackId) && (r.name || r.facility_id));
    const picked = pickCandidate(candidates, scanName);
    if (!picked.rack) { out.why = picked.why; return out; }
    known = picked.rack;
    how = picked.source;
    out.source = picked.source;
    out.why = picked.why;
  }

  out.knownRackId = known.id ?? null;
  // Only an explicit identification chooses the write key: a person confirmed
  // it, the scan's own row was typed, or its chosen name matched a typed rack.
  // "The only rack set up in this space" still names the rack and its contact,
  // but it never picks the key: a room with one typed rack and twelve physical
  // ones would merge all twelve into that one record.
  if ((how === 'confirmed' || how === 'name' || how === 'set-up-directly') && known.id != null) {
    out.rackKey = rackKeyFor(tenantId, known.id);
  }
  if (known.name) { out.name = known.name; out.confidence = 'known'; }

  const site = await findSite(client, siteName);
  out.siteId = site.id ?? null;
  out.siteWhy = site.why;
  // A person who confirmed by picking a NetBox rack picked a record, not a name:
  // read that record back by its id, and only otherwise go looking by name.
  const byId = chosen ? await findInNetBoxById(client, chosen.netboxRackId) : null;
  const answer = byId
    ? { id: byId.id, row: byId, by: 'record' }
    : await findInNetBox(client, known, { siteId: out.siteId });
  out.netboxWhy = answer.why || null;
  if (answer.ambiguous) {
    // Two racks answered and nothing tells them apart. Both names travel, and
    // nothing is claimed: a tie is never broken by sort order.
    out.candidates = answer.ambiguous;
  }
  const nb = answer.id ? (answer.row || null) : null;
  if (nb) {
    out.name = nb.name || out.name;
    out.netboxId = nb.id ?? null;
    out.netboxBy = answer.by || null;
    out.confidence = 'confirmed';
    // A person's answer is how this scan was tied to the record, and NetBox
    // agreeing does not change who said so.
    if (how === 'confirmed') {
      out.why = 'a person confirmed which rack this is, and it is in NetBox';
    } else {
      if (answer.by === 'facility-id') out.source = 'facility-id';
      out.why = `matched a rack already in NetBox by its ${answer.by === 'facility-id' ? 'rack id' : 'name'}`
        + `${site.id === null ? '' : `, inside ${site.why}`}`;
    }
  } else if (known.name) {
    out.why = how === 'confirmed'
      ? 'a person confirmed which rack this is; it is not in NetBox yet'
      : `matched a rack the customer set up; not found in NetBox (${out.netboxWhy})`;
  } else {
    // The typed rack has only a facility id and NetBox has no such rack, so
    // there is no name to use. Stay unresolved rather than invent one, and
    // hand out no key: an unresolved scan writes under its hash, as before.
    // A rack a person confirmed keeps its key even so: the key is that record's
    // own id, and a person saying which record it is does not become less true
    // because nobody has given the record a name yet.
    out.name = fallbackName ?? rackId;
    out.confidence = 'none';
    if (how !== 'confirmed') out.rackKey = null;
    out.why = how === 'confirmed'
      ? 'a person confirmed which rack this is; that record has no name and is not in NetBox'
      : 'a rack is set up here but has no name and is not in NetBox';
  }
  return out;
}

module.exports = {
  resolveRack, pickCandidate, findInNetBox, findInNetBoxById, findSite,
  rackKeyFor, confirmedKeyFor,
};
