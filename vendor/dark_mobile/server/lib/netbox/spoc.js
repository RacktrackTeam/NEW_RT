/**
 * Who a ticket for this rack goes to.
 *
 * NetBox already models people: contacts, roles, and assignments onto a rack
 * or a site. So the single point of contact is not another thing for us to
 * store and get out of step — it is read from the customer's own record at the
 * moment a ticket is raised.
 *
 * Falls back up the hierarchy, because a rack often has no contact of its own
 * and the site's applies. Returns null rather than guessing: a ticket assigned
 * to nobody is honest; a ticket assigned to the wrong person is not.
 */

const SPOC_ROLE = 'spoc';

/** One assignment row -> the bit we need. */
function toPerson(row, via, full) {
  const c = row.contact || {};
  if (!c.name) return null;
  // NetBox serialises the contact on an assignment in BRIEF form — id, name and
  // description, and nothing else. No email. That matters because a ServiceNow
  // incident is assigned by email; a name matches nothing on most instances. So
  // the real details are fetched once and joined on here by contact id.
  const detail = (full && full.get(c.id)) || {};
  return {
    name: c.name,
    email: detail.email || c.email || null,
    phone: detail.phone || c.phone || null,
    title: detail.title || c.title || null,
    role: (row.role && (row.role.name || row.role.slug)) || null,
    priority: (row.priority && (row.priority.value || row.priority)) || null,
    via,
    netboxId: c.id ?? null,
  };
}

/** Assignments on one object, best first. NetBox sorts by priority already. */
async function assignmentsFor(client, objectType, objectId, roleSlug) {
  if (!objectId) return [];
  const params = {
    object_type: objectType,
    object_id: objectId,
    ...(roleSlug ? { role: roleSlug } : {}),
  };
  try {
    const res = await client.get('/api/tenancy/contact-assignments/', params);
    return res.results || [];
  } catch {
    return [];
  }
}

/**
 * The SPOC for a rack, by name, falling back to its site.
 *
 * `rackName` rather than an id, because that is what a scan carries. One
 * lookup resolves it, and the rack row also gives us the site to fall back to.
 */
async function forRack(client, rackName) {
  const out = { spoc: null, others: [], rack: null, site: null, why: null };
  if (!rackName) { out.why = 'no rack name'; return out; }

  // One call for everybody's real details, joined on by id below. Cheaper than
  // fetching each contact as it is found, and the list is small.
  const full = new Map((await everyone(client)).map((c) => [c.netboxId, c]));

  let rack;
  try {
    const res = await client.get('/api/dcim/racks/', { name: rackName, limit: 1 });
    rack = (res.results || [])[0];
  } catch (err) {
    out.why = `could not reach NetBox: ${err.message}`;
    return out;
  }
  if (!rack) {
    // A rack photographed for the first time is not in NetBox yet, and a
    // ticket about it still has to reach somebody. Fall back to whoever covers
    // the site — "we do not know this rack, but we know who looks after this
    // room" is useful; "nobody" is not.
    out.why = `${rackName} is not in NetBox yet, so this is the site contact`;
    const site = await defaultSite(client);
    if (!site) { out.why = `no rack named ${rackName}, and no site to fall back to`; return out; }
    out.site = { id: site.id, name: site.name };
    const onSite = await assignmentsFor(client, 'dcim.site', site.id, SPOC_ROLE);
    out.spoc = onSite.map((r) => toPerson(r, 'site', full)).filter(Boolean)[0] || null;
    if (!out.spoc) out.why = `${rackName} is not in NetBox, and ${site.name} has no SPOC`;
    return out;
  }

  out.rack = { id: rack.id, name: rack.name };
  out.site = rack.site ? { id: rack.site.id, name: rack.site.name } : null;

  const onRack = await assignmentsFor(client, 'dcim.rack', rack.id, SPOC_ROLE);
  const first = onRack.map((r) => toPerson(r, 'rack', full)).filter(Boolean)[0];
  if (first) {
    out.spoc = first;
  } else if (out.site) {
    const onSite = await assignmentsFor(client, 'dcim.site', out.site.id, SPOC_ROLE);
    out.spoc = onSite.map((r) => toPerson(r, 'site', full)).filter(Boolean)[0] || null;
    if (!out.spoc) out.why = 'no contact with the SPOC role on this rack or its site';
  } else {
    out.why = 'the rack has no site, and no SPOC of its own';
  }

  // Everyone else attached to the rack, so a person can pick somebody different.
  const all = await assignmentsFor(client, 'dcim.rack', rack.id, null);
  out.others = all.map((r) => toPerson(r, 'rack', full))
    .filter(Boolean)
    .filter((p) => !out.spoc || p.name !== out.spoc.name);

  return out;
}

/**
 * The site to answer for a rack NetBox has never heard of.
 *
 * The one with the most racks, on the reasoning that it is the main room and
 * whoever covers it is the best guess. Only used as a fallback, and the caller
 * is told that is what happened.
 */
async function defaultSite(client) {
  try {
    const res = await client.get('/api/dcim/sites/', { limit: 20 });
    const sites = res.results || [];
    if (!sites.length) return null;
    sites.sort((a, b) => (b.rack_count || 0) - (a.rack_count || 0));
    return sites[0];
  } catch {
    return null;
  }
}

/** Everybody NetBox knows about, for an assignee dropdown. */
async function everyone(client) {
  try {
    const res = await client.get('/api/tenancy/contacts/', { limit: 100 });
    return (res.results || [])
      .filter((c) => c.name)
      .map((c) => ({
        name: c.name, email: c.email || null, title: c.title || null,
        phone: c.phone || null, netboxId: c.id,
      }));
  } catch {
    return [];
  }
}

module.exports = { forRack, everyone, SPOC_ROLE };
