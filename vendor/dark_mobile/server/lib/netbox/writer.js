/**
 * The NetBox writer.
 *
 * Four rules decide whether this is deployable. They are implemented here, not
 * described elsewhere and hoped for:
 *
 *   1. IDEMPOTENT.  NetBox has no upsert. Every object carries our uid in the
 *      racktrack_uid custom field; we GET by it, then POST or PATCH. Push the
 *      same scan twice and NetBox holds one clean set of records. A scan
 *      later keyed on the customer's rack finds the records it wrote under
 *      its photo hash and REBINDS them (the uid field alone, and only once
 *      the admin has seen and approved it) rather than writing a second set.
 *   2. DRY-RUN FIRST.  plan() performs no writes and returns the exact diff
 *      push() would apply.
 *   3. NEVER DELETE.  There is no delete path in this module or in the client
 *      it uses. A device that has vanished from a scan is REPORTED for a human
 *      to judge; it is never removed, and not even set offline unless asked.
 *      A tool that removes production records loses all trust the first time
 *      it is wrong, and it will eventually be wrong.
 *   4. CONFIDENCE -> STATUS.  Proven becomes `connected`, camera-only becomes
 *      `planned`, and a CONFLICT is not exported at all — a disagreement is a
 *      finding for review, not a fact to write down.
 */
const { exportable, EXPORT_ORDER } = require('./model');
const { orderedSpecs, objectTypes, withUid } = require('./mapping');
const { UID_FIELD, BOUND_FIELD, NetBoxError } = require('./netbox');
const { slug, spanByUid, cameraDevices } = require('./reconcile');
const find = require('./find');
const identity = require('./identity');

/**
 * A reference to an object that will not exist until this push runs.
 * Kept distinct from a real id so the dry-run diff can say "this depends on a
 * create earlier in the plan" instead of inventing a change.
 */
class Pending {
  constructor(label, uid) { this.label = label; this.uid = uid; }
  toString() { return `new:${this.label}:${this.uid}`; }
}
const isPending = (v) => v instanceof Pending;

/**
 * What a create sends, trimmed to its plain values, for the change row: the
 * registry records it as what was created. A reference this same push is
 * still to create reads null; a list or an object (terminations, custom
 * fields) is left out. It rides beside the row and is never part of a diff,
 * so no fingerprint moves because of it.
 */
function onlyScalars(payload) {
  const out = {};
  for (const [k, v] of Object.entries(payload || {})) {
    if (v === undefined) continue;
    if (isPending(v)) out[k] = null;
    else if (v === null || ['string', 'number', 'boolean'].includes(typeof v)) out[k] = v;
  }
  return out;
}

/**
 * What NetBox refused, in a sentence rather than in JSON.
 *
 * An admin approving an export reads these reasons, and a reason that arrives as
 * `{"name":["already exists"]}` is the wire format on a screen. NetBox answers a
 * refusal as an object keyed by the field it objected to, so the field name and
 * its complaint are what a person needs; the braces and quotes are not. The
 * cause is never dropped - only its punctuation.
 */
function refusalText(err) {
  const detail = err && err.detail;
  if (typeof detail === 'string' && detail) return detail;
  if (detail && typeof detail === 'object') {
    const lines = Object.entries(detail)
      .map(([field, said]) => `${field}: ${[].concat(said).join(' ')}`);
    if (lines.length) return lines.join('; ');
  }
  return String((err && err.message) || 'no reason given');
}

/**
 * NetBox's value for `key`, flattened to something comparable.
 * It nests foreign keys as {id, ...} and choice fields as {value, label}.
 */
function current(existing, key) {
  const v = existing[key];
  if (v && typeof v === 'object' && !Array.isArray(v)) {
    if ('id' in v) return v.id;
    if ('value' in v) return v.value;
  }
  if (key.endsWith('_terminations') && Array.isArray(v)) {
    return v.map((t) => ({ object_type: t.object_type, object_id: t.object_id }));
  }
  // A front port's rear_ports come back with the rear port nested as {id, ...}.
  if (key === 'rear_ports' && Array.isArray(v)) {
    return v.map((m) => ({
      position: m.position,
      rear_port: m.rear_port && typeof m.rear_port === 'object' ? m.rear_port.id : m.rear_port,
      rear_port_position: m.rear_port_position,
    }));
  }
  return v;
}

const sameValue = (a, b) => JSON.stringify(a) === JSON.stringify(b);

/**
 * What would change, and what cannot be judged yet.
 * A field whose new value is Pending is not a difference — it is a reference
 * to something this same plan will create. Reporting it would be noise at
 * best and a false positive at worst.
 */
function diff(payload, existing) {
  const changed = {};
  const pending = [];
  for (const [k, next] of Object.entries(payload)) {
    if (k === 'custom_fields' || next === undefined) continue;
    if (isPending(next)) { pending.push(k); continue; }
    // rear_ports nests its reference one level down.
    if (k === 'rear_ports' && Array.isArray(next) && next.some((m) => isPending(m.rear_port))) {
      pending.push(k); continue;
    }
    const old = current(existing, k);
    if ((old === null || old === undefined) && (next === '' || next === null)) continue;
    // A port the record names its own way is the same port: its name is theirs.
    if (k === 'name' && 'device' in payload && samePortName(old, next)) continue;
    if (!sameValue(old, next)) changed[k] = { from: old ?? null, to: next };
  }
  return { changed, pending };
}

/**
 * The uid an object carried before its rack was keyed, or null.
 *
 * A keyed snapshot builds every rack-scoped uid on the customer's rack key
 * where the photo hash used to be: rack:<key>, dev:<key>:u10,
 * if:dev:<key>:u10:1. Putting the hash back into that one segment gives the
 * uid the same object was written under before it was keyed. A uid that does
 * not carry the key (a manufacturer, a device type, a site) was never
 * rack-scoped and has no alias.
 *
 * A cable is the one exception to the segment form. reconcile.js mints its uid
 * as cable:<slug of both interface uids>, and slug turns every colon into a
 * dash: cable:if-dev-t7-5-u10-1-if-dev-t7-5-u12-1. So for a cable the slugged
 * key (t7-5) is swapped for the slugged hash (rk-old00001), and only where it
 * is a whole dash-delimited token, so t7-5 never matches inside t7-51.
 */
/**
 * The patch body for an update: the fields that actually differ, and the uid.
 *
 * An update used to send the whole mapped payload. NetBox takes a PATCH field
 * by field, so that rewrote every field this system maps, whether or not the
 * admin had approved anything about it - and for a field no source stated,
 * what it rewrote it to was empty. The record belongs to the customer, and an
 * approval is for one difference, not for everything RackTrack happens to
 * know how to write.
 *
 * custom_fields always travels, because it is how the object stays findable.
 * A nested reference already resolved in the payload is sent as it stands.
 */
function onlyChanged(payload, changed) {
  const body = {};
  for (const k of Object.keys(changed)) {
    if (k in payload) body[k] = payload[k];
  }
  if (payload.custom_fields !== undefined) body.custom_fields = payload.custom_fields;
  return body;
}

/**
 * Did NetBox refuse because the object is already there under its own name?
 *
 * NetBox answers 400 with a field error whose wording varies by model and by
 * version: "manufacturer with this name already exists", "a top-level device
 * role with this name and slug already exists", "interface with this Device
 * and Name already exists". The one thing they share is the phrase, so that is
 * what is matched, and only on a 400. Any other refusal is a real failure and
 * is left alone.
 */
function alreadyExists(err) {
  if (!err || err.status !== 400) return false;
  const body = typeof err.detail === 'string' ? err.detail : JSON.stringify(err.detail ?? '');
  return /already exist/i.test(body);
}

/**
 * Claim an object NetBox already holds, instead of counting it a failure.
 *
 * The join in this system is our own custom field, which only we write. An
 * estate that already lists D-Link, or a role called Router, or the interfaces
 * of a device somebody created by hand, carries none of our uids, so every one
 * of those is planned as a create and refused by NetBox on its own uniqueness
 * rules. Eight such refusals turned one live write into "write_failed" for
 * objects that were already correct.
 *
 * So: look the object up by the natural key that made NetBox refuse, and if
 * exactly one object answers, stamp our uid on it and use it. Only the custom
 * field is patched. Nothing about the customer's object is renamed, moved or
 * re-parented.
 *
 * The guards are the point, and they are the ones three reviewers wrote after
 * refuting the first version of this idea:
 *
 *   - Only specs that HAVE a natural key. A rack and a device deliberately do
 *     not. A device sitting at a shelf is somebody's asset, and claiming it
 *     because the position collided is exactly the mis-merge that was refuted:
 *     it stays a failure until the rack ladder and a person bind it.
 *   - The key must be complete and scoped. An interface is found by its device
 *     and its name, never by name alone; a VLAN by its id AND its site, never
 *     by an id that repeats across an estate.
 *   - Exactly one hit. Two is ambiguous and is left as a failure.
 *   - An object already carrying somebody else's uid is never taken. That is
 *     the "one uid on two objects" trap, and it stops here.
 *   - Our id is stamped on a port or a socket we would have made ourselves, and
 *     NOT on the customer's catalogue - their site, their maker, their model,
 *     their role. Their name and their slug are theirs: an id of ours on an
 *     object whose name or slug is not the one we would have minted turns the
 *     NEXT comparison into an offer to rename it, which is how a shelf move
 *     would come to carry a rename of the customer's site. A catalogue object is
 *     found by its name every time instead, which costs one read and cannot
 *     propose anything.
 */
async function adopt(client, spec, payload, uid, err, { stamp = true } = {}) {
  if (!alreadyExists(err)) return null;
  const byKey = await adoptByKey(client, spec, payload, uid, stamp);
  if (byKey) return byKey;
  // The last line of defence, and the one the live write needed. The key that
  // made NetBox refuse is not always a key we can ask with: the scan's slug for
  // the site was office-sprintpark and the customer's site of that name is
  // office-sprint, so the slug lookup answered nothing and a write an approver
  // had signed died on "site with this name already exists". NetBox has just
  // told us the thing is there. Ask it by name, take it as it stands, and write
  // nothing at all on it.
  const hit = await lookupByName(client, spec, payload, uid);
  return hit && hit.id
    ? { id: hit.id, by: 'its name', stamped: false, why: hit.why, carried: hit.carried ?? null }
    : null;
}

/** Adopt by the key that made NetBox refuse: our id on it, unless it is theirs. */
async function adoptByKey(client, spec, payload, uid, stamp = true) {
  if (typeof spec.naturalKey !== 'function') return null;
  const key = spec.naturalKey(payload);
  if (!key || !Object.keys(key).length) return null;

  let hits;
  try {
    const res = await client.get(spec.endpoint, { ...key, limit: 2 });
    hits = res.results || [];
  } catch { return null; }
  if (hits.length !== 1) return null;

  const found = hits[0];
  const carried = (found.custom_fields || {})[UID_FIELD];
  // Already ours, under this very uid: nothing to stamp, just use it.
  if (carried && carried !== uid) return null;
  if (!stamp) return { id: found.id, by: Object.keys(key).join(' and '), stamped: false,
                       why: `the record already holds it under its ${Object.keys(key).join(' and ')}` };
  if (!carried) {
    try {
      await client.patch(spec.endpoint, found.id, { custom_fields: { [UID_FIELD]: uid } });
    } catch { return null; }
  }
  return { id: found.id, by: Object.keys(key).join(' and '), stamped: true };
}

/**
 * The catalogue object NetBox already holds under this name, or null.
 *
 * Only a type whose name NetBox itself keeps unique, and only inside the scope
 * it is unique in - mapping.js says which, and a rack and a device deliberately
 * have no name key at all. find.byName refuses on two rows and writes nothing.
 *
 * Nothing is stamped on what comes back, and that is the point rather than an
 * omission. Their slug is theirs: if our id were written onto a site whose slug
 * is not the slug we would have minted, the very NEXT comparison would find it
 * by our id, see the slugs differ and offer to rename the customer's site. So
 * the object is used and left exactly as they have it, and the name finds it
 * again next time just as reliably.
 */
async function lookupByName(client, spec, payload, uid) {
  if (typeof spec.nameKey !== 'function') return null;
  const key = spec.nameKey(payload);
  // No name, or a scope this push has not created yet: there is nothing to ask
  // with, and an unscoped question about a name NetBox lets repeat is exactly
  // the question this file refuses to ask.
  if (!key || !key.value) return null;
  return find.byName(client, spec.endpoint, {
    name: key.value, field: key.field || 'name', scope: key.scope || null,
    what: String(spec.label).toLowerCase(), uid,
  });
}

function aliasUid(uid, key, hash) {
  if (!uid || !key || !hash) return null;
  const s = String(uid);
  const at = s.indexOf(`:${key}`);
  if (at >= 0) {
    const end = at + 1 + key.length;
    // The key has to be the whole segment: rack:t7:5 is not rack:t7:51.
    if (end < s.length && s[end] !== ':') return null;
    return `${s.slice(0, at + 1)}${hash}${s.slice(end)}`;
  }
  if (!s.startsWith('cable:')) return null;
  const escaped = slug(key).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  // Preceded by ':' or '-', followed by '-' or the end: a whole token.
  const token = new RegExp(`([:-])${escaped}(?=-|$)`, 'g');
  const swapped = s.replace(token, `$1${slug(hash)}`);
  return swapped === s ? null : swapped;
}

/**
 * A record id, or null for anything that is not one.
 *
 * The same strictness as bindings.asNetboxId, for the same reason: Number(true)
 * is 1, and binding the customer's NetBox row 1 because a flag was serialised
 * into this field is exactly the failure the guarantee is about.
 */
function asId(v) {
  if (typeof v === 'number') return Number.isInteger(v) && v > 0 ? v : null;
  if (typeof v !== 'string') return null;
  const s = v.trim();
  if (!/^[0-9]+$/.test(s)) return null;
  const n = Number(s);
  return Number.isInteger(n) && n > 0 ? n : null;
}

const bindingOn = (snapshot) => (snapshot && snapshot.recordBinding
  && typeof snapshot.recordBinding === 'object' ? snapshot.recordBinding : null);

/**
 * The record id a person said this object is, or null.
 *
 * A snapshot may carry a record binding: the rows of the customer's own record
 * that a person, looking at the rack, said this rack and these boxes are. It is
 * the SECOND way the rebind below can find its target. The first, aliasUid,
 * only ever finds an object RackTrack itself wrote, under a uid RackTrack itself
 * minted - which is why a rack the customer filled in by hand was invisible. The
 * third is the resolver, which asks the customer's own record what this is.
 *
 * Only a rack and a device. Those are the two object types adopt() deliberately
 * refuses to claim on a collision, and this is how they are bound instead: by
 * evidence and a person, never by a name clash.
 */
function boundIdFor(snapshot, field, uid) {
  const b = bindingOn(snapshot);
  if (!b) return null;
  if (field === 'racks') return asId(b.rackNetboxId);
  if (field === 'devices') {
    const map = b.deviceNetboxIds && typeof b.deviceNetboxIds === 'object' ? b.deviceNetboxIds : {};
    return asId(map[uid]);
  }
  return null;
}

/**
 * What the person was reading when they named that record, or null.
 *
 * Handed to find.byId, which refuses the binding when the row no longer matches
 * it: a NetBox restored from a backup renumbers ids while the answer still names
 * 51, and one transposed digit in a tap looks exactly like a correct answer.
 */
function shownFor(snapshot, field, uid) {
  const b = bindingOn(snapshot);
  const shown = b && b.shown && typeof b.shown === 'object' ? b.shown : null;
  if (!shown) return null;
  if (field === 'racks') return shown.rack && typeof shown.rack === 'object' ? shown.rack : null;
  if (field === 'devices') {
    const map = shown.devices && typeof shown.devices === 'object' ? shown.devices : {};
    return map[uid] && typeof map[uid] === 'object' ? map[uid] : null;
  }
  return null;
}

/**
 * The rack this scan resolved to in the customer's own record, when the resolver
 * found it by a key that is allowed to be written.
 *
 * rack_match asks NetBox by the rack's facility id inside the scan's site and
 * refuses a tie, so an answer here is the customer's own rack row. A rack found
 * by NAME is carried too, and deliberately not used as a bind target: a name
 * finds the record and proves nothing, so it becomes a question instead.
 */
function rackMatchOn(snapshot) {
  const m = snapshot && snapshot.recordMatch && typeof snapshot.recordMatch === 'object'
    ? snapshot.recordMatch : null;
  if (!m) return null;
  const id = asId(m.rackNetboxId);
  if (id === null) return null;
  return { id, by: String(m.by || ''), confidence: String(m.confidence || ''), why: String(m.why || '') };
}

/**
 * The site in the CUSTOMER'S record that this scan was recognised at, or null.
 *
 * Not the site row RackTrack mints from the tenant's name, which is often a
 * different row in the same NetBox: checking a bound rack against that one would
 * refuse every correct answer. This is the site rack_match resolved by name
 * before the rack was looked up, so it is an independent check on a rack id
 * somebody typed: a rack at Frankfurt cannot be the rack in a London scan.
 */
const scanSiteId = (snapshot) => asId(((snapshot || {}).recordMatch || {}).siteId);

/**
 * Why a rack bind cannot be checked, or null when it can be.
 *
 * The site is the outer scope every rack bind rests on, and it was the hole this
 * whole round exists to close: find.byId only checks the site when it is given
 * one, the site arrives as null for every scan whose site name is the tenant's
 * own (or the literal "RackTrack"), and null read as "no check needed" bound a
 * Frankfurt rack to a London scan.
 *
 * A rack id, a rack name and a person's tap all name a row and none of them says
 * which building it is in. So where the scan has not been placed at a site in the
 * customer's own record there is nothing to check a named rack against, and being
 * unable to check is not permission to proceed. An absent scope is a refusal.
 */
function siteUnknown(snapshot) {
  if (scanSiteId(snapshot) !== null) return null;
  const m = (snapshot || {}).recordMatch;
  const why = m && typeof m === 'object' ? String(m.siteWhy || '').trim() : '';
  return 'this scan has not been placed at a site in the customer\'s own record'
    + `${why ? ` (${why})` : ''}. The same rack id and the same rack name are used at more than one `
    + 'site, so there is nothing to check the record against. Name the site in the customer\'s '
    + 'record that this rack is at, then say which record this is again.';
}

/**
 * Why a device bind cannot be checked, or null when it can be.
 *
 * A device is scoped by the rack it sits in, and that scope is only real once
 * the rack itself has been resolved to a row in the customer's record. While the
 * rack is still Pending - this same plan is creating it - the expectation handed
 * to find.byId was null, so a record id with one digit transposed bound a box in
 * another rack, at another site, and nothing looked.
 */
function rackUnknown(rackNetboxId) {
  if (rackNetboxId !== null && rackNetboxId !== undefined && !isPending(rackNetboxId)) return null;
  return 'the rack holding this box has not been found in the customer\'s record yet, so there is '
    + 'nothing to check a box against: a record id on its own does not say which rack the box is '
    + 'in. Say which record the rack is first, and this box after it.';
}

/**
 * What a record-bound object does NOT get patched on.
 *
 * A bind is bind-only. This is the fourth refutation of the first design of
 * this feature, written down as code: claiming the customer's own rack let the
 * writer rename it to the local alias the technician typed and move it to the
 * RackTrack site, because the payload still came from the scan. There was no
 * notion of a bind-only object. There is now.
 *
 * These are the fields the customer owns and RackTrack did not read off the
 * hardware: what the thing is called, where it lives, how big it is and WHAT IT
 * IS. A difference on one of them is REPORTED and never written. Everything else
 * - the serial the switch published, an asset tag, a status, a description - is
 * left alone here and still reaches the admin as an ordinary change to approve,
 * because filling a gap in the record is the point of the whole system.
 *
 * device_type and role are on the list, and they are the ones that were missed.
 * cv.js mints a device type from the camera alone ("Unidentified Switch
 * (48-port)" whenever OCR read no model), so a bound record holding the
 * customer's real Catalyst 9300-48P was being re-parented onto a guess, shown to
 * the admin as two opaque numbers with no model string in sight. Rule 3 of the
 * six that never change: a guessed model is shown as probably and never written.
 *
 * It applies to an object a person bound to the customer's own record, and to
 * any object NetBox says was bound that way before (the racktrack_bound field).
 * A rack RackTrack itself created is RackTrack's to keep correct.
 */
const CUSTOMER_OWNED = Object.freeze({
  racks: ['name', 'site', 'location', 'u_height', 'desc_units'],
  devices: ['name', 'site', 'rack', 'position', 'face', 'device_type', 'role', 'tenant'],
});

/**
 * The hardware facts, which a contradiction holds back.
 *
 * Withheld only when the record and the scan disagree about which box this is
 * (see replacement below). Until then they are the gap the scan exists to fill.
 */
const HARDWARE_FIELDS = Object.freeze(['serial', 'asset_tag', 'device_type', 'role']);

/**
 * The catalogue a scan brings with it, which may turn out not to be needed.
 *
 * Everything this writer can make that is not hardware: the makes, models and
 * roles a box brings, and the site and location the rack hangs off. Not one of
 * them is what anybody approved, and until the site was on this list one of
 * them could refuse a write that had been approved - so each one is looked up
 * by name before it is made, dropped when nothing in the write needs it, and
 * never a reason for the rest of the write to fail.
 */
const SCAFFOLDING = new Set(['manufacturers', 'deviceTypes', 'deviceRoles', 'sites', 'locations']);

/** Is this record marked, on the record itself, as one a person bound? */
const boundOnRecord = (row) => Boolean(String(((row || {}).custom_fields || {})[BOUND_FIELD] ?? '').trim());

/** What the mark on the record says: who bound it, or what matched it. */
function markFor(why, snapshot, hit) {
  if (why === 'record-match') return `matched by ${(hit && hit.by) || 'the record'}`.slice(0, 200);
  const b = bindingOn(snapshot) || {};
  const who = String(b.by || '').trim();
  const at = String(b.at || '').trim();
  return `bound by ${who || 'a person'}${at ? ` on ${at}` : ''}`.slice(0, 200);
}

/**
 * The shelf a person approved moving this one record to, or null.
 *
 * The narrow exception to CUSTOMER_OWNED, and off unless the snapshot carries
 * it: a SPOC accepted "same device, wrong shelf" on a check, so for that check
 * and that one record the position (and the face, only where it differs) may
 * be written. It rides on the snapshot as approvedMoves, keyed by the box and
 * naming the record, so an allowance given for record 199 allows nothing on
 * any other record. Name, site, rack, role, device type and tenant are never
 * in it, whatever the snapshot says.
 */
const MOVE_ALLOWS = Object.freeze(['position', 'face']);
function allowanceFor(snapshot, uid, recordId) {
  const moves = snapshot && snapshot.approvedMoves && typeof snapshot.approvedMoves === 'object'
    ? snapshot.approvedMoves : null;
  const move = moves && moves[uid] && typeof moves[uid] === 'object' ? moves[uid] : null;
  if (!move || asId(move.netboxId) === null || asId(move.netboxId) !== asId(recordId)) return null;
  const fields = move.fields && typeof move.fields === 'object' ? move.fields : {};
  const out = {};
  for (const k of MOVE_ALLOWS) {
    if (fields[k] && typeof fields[k] === 'object' && 'to' in fields[k]) out[k] = fields[k];
  }
  return Object.keys(out).length ? out : null;
}

/** Same shelf or same face, whether NetBox spelt it 22, 22.0 or "22". */
const sameShelfValue = (a, b) => (a === null || a === undefined || a === '' || b === null || b === undefined || b === ''
  ? (a ?? '') === (b ?? '')
  : (Number.isFinite(Number(a)) && Number.isFinite(Number(b)) ? Number(a) === Number(b) : String(a) === String(b)));

/** Is this exact change - from this value, to that one - the one that was approved? */
const moveAllows = (allow, key, change) => Boolean(allow && allow[key] && change
  && sameShelfValue(allow[key].from, change.from) && sameShelfValue(allow[key].to, change.to));

/**
 * Hold back the fields a bound object's owner decides, and say so out loud.
 *
 * Returns the number withheld. `changed` is edited in place, so what is left is
 * exactly what the plan will propose.
 */
function bindOnly(spec, obj, row, changed, report, allow = null) {
  const owned = CUSTOMER_OWNED[spec.field] || [];
  const held = owned.filter((k) => Object.prototype.hasOwnProperty.call(changed, k)
    && !moveAllows(allow, k, changed[k]));
  if (!held.length) return 0;
  const said = held.map((k) => `${k} (${JSON.stringify(changed[k].from)} in the record, `
    + `${JSON.stringify(changed[k].to)} on this scan)`).join(', ');
  const named = String(row.name ?? '') || `record ${row.id}`;
  // It used to say "only the RackTrack id is ever written on it", in a plan whose
  // own rows went on to write a serial on that record. What is true is narrower
  // and is what it now says: these fields are the customer's and are never
  // written. A gap the scan can fill is still proposed, and still approved.
  const why = `"${named}" is the customer's own record, bound by a person. These fields are `
    + `theirs and are reported, never written: ${said}.`;
  report.findings.push({
    tier: 'medium', kind: 'bind-only', type: spec.label, uid: obj.uid,
    netboxId: row.id, name: String(row.name ?? ''),
    fields: held.map((k) => ({ field: k, was: changed[k].from, now: changed[k].to })),
    why,
  });
  report.warnings.push(why);
  for (const k of held) delete changed[k];
  return held.length;
}

/**
 * The plan's high finding: "Replaced. Same shelf, same address, different
 * serial inside."
 *
 * One row, saying was X and now Y. NOT a removal plus an addition: that shape
 * is what happens when a box loses its binding and reads as new here and gone
 * there, and it hides the one thing the admin needs to see, which is that the
 * hardware in the slot changed while the record stayed still.
 *
 * Only where both sides actually state a serial. The record holding none and
 * the switch reading one is the plan's LOW tier - a gap the scan can fill - and
 * it is not a replacement. Compared in the normalised form identity.js uses, so
 * FDO-2117-A0X9 and fdo2117a0x9 are not reported as a swap.
 */
function replacement(spec, obj, row, report) {
  if (spec.field !== 'devices') return null;
  const was = String((row || {}).serial ?? '').trim();
  const now = String((obj || {}).serial ?? '').trim();
  if (!was || !now) return null;
  if (identity.normalise(was) === identity.normalise(now)) return null;
  const named = String(row.name ?? '') || `record ${row.id}`;
  const here = obj.position === null || obj.position === undefined ? null : Number(obj.position);
  const there = row.position === null || row.position === undefined ? null : Number(row.position);
  // The plan's rule is narrower than "two serials differ": same shelf, same
  // address, different hardware inside. The shelf half is checkable here, so a
  // record on another shelf is reported as a disagreement rather than as a box
  // somebody swapped out, and the high tier keeps its meaning.
  const sameShelf = here !== null && there !== null && here === there;
  const where = here === null ? 'this rack' : `shelf U${here}`;
  const why = sameShelf
    ? `Replaced: ${where} still holds the record "${named}", and the box in it is a different one. `
      + `The serial was ${was} and is now ${now}.`
    : `The serial on this box does not match the record "${named}"`
      + `${there === null ? '' : `, which sits on U${there}`}. `
      + `The record says ${was} and this box publishes ${now}, so they are not the same box.`;
  const finding = {
    tier: sameShelf ? 'high' : 'low', kind: sameShelf ? 'replaced' : 'serial-differs',
    type: spec.label, uid: obj.uid,
    netboxId: row.id, name: String(row.name ?? ''), position: here,
    recordPosition: there, field: 'serial', was, now, why,
  };
  report.findings.push(finding);
  // Findings are new here and the stored plan has no column for them yet, so the
  // same sentence goes on the warnings the plan already carries. A person reads
  // it either way.
  report.warnings.push(why);
  return finding;
}

/**
 * A contradiction holds the hardware facts back, rather than writing them.
 *
 * Two serials that disagree are positive evidence of two different boxes
 * (identity.sameDevice calls it refuted), and rule 4 says a conflict waits for a
 * person. So the record keeps the serial, asset tag, model and role it has, the
 * disagreement is reported, and nothing about what the hardware IS is written on
 * a record this scan cannot prove it is looking at. Without this the plan carried
 * the high finding "the box in it is a different one" AND an update row writing
 * the new box's serial onto the old box's record, in one response.
 */
function holdBack(spec, obj, row, changed, report, finding) {
  const held = HARDWARE_FIELDS.filter((k) => Object.prototype.hasOwnProperty.call(changed, k));
  if (!held.length) return 0;
  const named = String(row.name ?? '') || `record ${row.id}`;
  const why = `The record "${named}" and this box do not agree on which box it is, so what the `
    + `hardware is stays as the customer has it. Reported and not written: ${held.join(', ')}. `
    + 'Say which record this box is, and it can be filled in then.';
  report.findings.push({
    tier: finding && finding.tier === 'high' ? 'high' : 'medium',
    kind: 'held-back', type: spec.label, uid: obj.uid,
    netboxId: row.id, name: String(row.name ?? ''),
    fields: held.map((k) => ({ field: k, was: changed[k].from, now: changed[k].to })),
    why,
  });
  report.warnings.push(why);
  for (const k of held) delete changed[k];
  return held.length;
}

/**
 * One device holds one port of each name, in an old snapshot too.
 *
 * cv.toSnapshot now gives every port on a device its own name, but a snapshot
 * is built when the photograph is analysed and frozen there: a compare made
 * today against a scan taken last week still carries last week's names. A
 * live write refused four interfaces for exactly this reason after the build
 * fix had landed, because the rack had not been photographed since.
 *
 * So the same rule is applied where the snapshot is READ, not only where it is
 * written. It is idempotent: a snapshot already correct is untouched, and
 * nothing here is invented - the uid is what it always was, only the duplicate
 * name is replaced by the port's own position, and each one is named in the
 * report so the reading problem stays visible.
 */
function uniqueInterfaceNames(snapshot, report) {
  // The device's own name, because the warning names the box a person has to go
  // back and photograph. Its uid names nothing to anybody reading the screen.
  const nameOf = new Map((snapshot.devices || []).map((d) => [d.uid, d.name]));
  const byDevice = new Map();
  for (const i of snapshot.interfaces || []) {
    if (!i || !i.deviceUid) continue;
    if (!byDevice.has(i.deviceUid)) byDevice.set(i.deviceUid, []);
    byDevice.get(i.deviceUid).push(i);
  }

  for (const [deviceUid, ports] of byDevice) {
    // Every name this device's ports ask for, collected BEFORE any of them is
    // changed. The old pass only knew the names it had already walked past, so
    // a rename could take a name a later port was still going to use. That is
    // not hypothetical: on a live 52 port switch, two ports both read as "3",
    // the second was moved to "46" because 46 was its place in the list, and
    // the port that genuinely read 46 came afterwards. NetBox then refused the
    // write with "Interface with this Device and Name already exists", and one
    // item of an otherwise clean plan was lost.
    const wanted = new Set(ports.map((i) => String(i.name ?? '')));
    const used = new Set();

    for (const i of ports) {
      const name = String(i.name ?? '');
      if (!used.has(name)) { used.add(name); continue; }

      // The port's place in the list is unique per device, so it is the first
      // choice - but only when no other port on this device is going to want
      // it as a read number.
      // The place is the uid's last segment, without the ".1" a port gets when
      // its place is already some other port's record (cv.js). That suffix is
      // an id, not something a person should read as a port number.
      const place = String(i.uid || '').split(':').pop().split('.')[0];
      let replacement = place && !wanted.has(place) && !used.has(place) ? place : null;
      if (!replacement) {
        let n = 2;
        while (wanted.has(`${name}-${n}`) || used.has(`${name}-${n}`)) n += 1;
        replacement = `${name}-${n}`;
      }

      report.warnings.push(
        `${nameOf.get(deviceUid) || 'One device'}: two ports both read as "${name}", `
        + `so the second is recorded as "${replacement}". Photograph the rack again.`);
      i.name = replacement;
      used.add(replacement);
      // A later rename must not land on this one either.
      wanted.add(replacement);
    }
  }
}

/**
 * Take every device that is about to move out of its U before any of them is
 * placed again.
 *
 * NetBox refuses to put a device into a U another device still occupies, and it
 * checks one PATCH at a time. So a rack whose unit grid improved - where the
 * Switch at U12 now reads as U13 and the Router that was at U13 now reads as
 * U20 - cannot be written in any order that works, because each move lands on
 * a device that has not moved yet. The same goes for a device whose type grows
 * from 1U to 2U where it stands: the U above it has to be free first.
 *
 * Every device still in the snapshot at this point is either unchanged or an
 * approved change (unapproved items were filtered out before the push), so only
 * approved moves are touched, and the device is only UNRACKED - it keeps its
 * rack, its site and everything else, and the walk below puts it back in its
 * new U straight away. Nothing is deleted. If the push dies between the two
 * steps the device is left in its rack without a U, which the next comparison
 * reports as a position to set, and a person approves it again.
 */
async function makeRoom(snapshot, client) {
  const spec = orderedSpecs().find((s) => s.field === 'devices');
  const typeSpec = orderedSpecs().find((s) => s.field === 'deviceTypes');
  if (!spec) return [];
  // Our device type's NetBox id, by the uid we stamped on it. Unknown means the
  // type does not exist yet, so the device is certainly changing type.
  const typeIds = new Map();
  const typeIdOf = async (uid) => {
    if (!uid || !typeSpec) return undefined;
    if (!typeIds.has(uid)) {
      let t = null;
      try { t = await client.findByUid(typeSpec.endpoint, uid, { fresh: true }); } catch { t = null; }
      typeIds.set(uid, t ? t.id : null);
    }
    return typeIds.get(uid);
  };
  const cleared = [];
  for (const d of snapshot.devices || []) {
    if (d.position === null || d.position === undefined) continue;
    let existing;
    // From the preload, not a fresh read. The walk below reads every device
    // fresh before it writes it, so nothing is decided on this copy except
    // whether to clear a U - and a fresh read here would be the push's FIRST
    // question about each uid, which moves the moment a concurrent writer can
    // be caught to before the re-check that exists to catch it.
    try { existing = await client.findByUid(spec.endpoint, d.uid, { fresh: false }); } catch { continue; }
    if (!existing || existing.position === null || existing.position === undefined) continue;
    // Never a record that is the customer's. Its shelf is theirs and is not
    // written, so a record taken out of its U here is never put back: the walk
    // finds it, withholds the position, calls the row a noop, and putBack only
    // restores a row that failed. A shelf move a SPOC approved needs no room
    // made either: it is only offered when the target U is empty in NetBox,
    // and NetBox itself refuses the patch otherwise, with nothing lost.
    if (boundOnRecord(existing) || boundIdFor(snapshot, 'devices', d.uid) !== null) continue;
    const samePlace = Number(existing.position) === Number(d.position);
    const wantType = await typeIdOf(d.deviceTypeUid);
    // current() flattens NetBox's nested {id, ...} to the id, and leaves a bare
    // id alone, so this compares like with like either way.
    const sameType = wantType === undefined || current(existing, 'device_type') === wantType;
    if (samePlace && sameType) continue;
    // What to put back, taken before anything changes it.
    const undo = { position: existing.position, face: current(existing, 'face') || 'front' };
    try {
      await client.patch(spec.endpoint, existing.id, { position: null, face: '' });
      cleared.push({ endpoint: spec.endpoint, id: existing.id, uid: d.uid, kind: 'device', undo });
    } catch { /* the device's own update below says why, in NetBox's words */ }
  }

  // The same problem one level down. NetBox holds an interface name unique per
  // device and checks one rename at a time, so a switch whose ports were read
  // again and numbered differently cannot be renamed in any order that works:
  // port 2 becoming "3" is refused while port 3 still holds "3". Seen on the
  // demo rack, where re-reading it with the current models failed 120 renames
  // in one write. So every interface about to be renamed first takes a name
  // nobody else can hold - "~" and its own NetBox id - and the walk then gives
  // each its real name. From the preload, for the same reason as above.
  const ifSpec = orderedSpecs().find((s) => s.field === 'interfaces');
  if (ifSpec) {
    for (const i of snapshot.interfaces || []) {
      let existing;
      try { existing = await client.findByUid(ifSpec.endpoint, i.uid, { fresh: false }); } catch { continue; }
      if (!existing || String(existing.name) === String(i.name)) continue;
      const undo = { name: existing.name };
      try {
        await client.patch(ifSpec.endpoint, existing.id, { name: `~${existing.id}` });
        cleared.push({ endpoint: ifSpec.endpoint, id: existing.id, uid: i.uid, kind: 'interface', undo });
      } catch { /* its own update below says why */ }
    }
  }
  return cleared;
}

/**
 * Put back anything make-room cleared whose real change then failed, so a
 * refused write never leaves a device out of its U or a port called "~4178".
 * Best effort: if the old value has been taken meanwhile, say so rather than
 * guess.
 */
async function putBack(cleared, failed, client, report) {
  for (const c of cleared) {
    if (!failed.has(c.uid)) continue;
    try {
      await client.patch(c.endpoint, c.id, c.undo);
    } catch (err) {
      report.warnings.push(
        `A ${c.kind} could not be put back as it was after its change failed (NetBox id ${c.id}): `
        + `${refusalText(err)}. It needs looking at by hand.`);
    }
  }
}

/**
 * Ask the customer's own record what this object is, where nothing of ours
 * carries its uid and nobody has answered about it.
 *
 * This is the resolver being wired, which is the whole point of the slice: the
 * only way anything found an object in NetBox was our own custom field, so a
 * rack the customer filled in by hand was invisible and every box in it read as
 * a create that NetBox then refused on an occupied shelf.
 *
 * What may bind and what may only be asked about is the standard's line, not a
 * judgement made here. A rack's own facility id inside its own site, a serial, an
 * asset tag: the system of record states these, they are rank 3, and they bind. A
 * shelf and a name are rank 8, which reads as 'possible', and a possible binding
 * is a question and never a write.
 *
 * Returns the resolver's answer, or null when there was nothing to ask.
 */
/**
 * The digits of a port name, in order: "gigabitEthernet 1/0/24" [1, 0, 24],
 * "Port24" [24], "GigabitEthernet 24" [24].
 */
const portDigits = (name) => (String(name ?? '').match(/\d+/g) || []).map(Number);

/**
 * Two names for the same socket on one device.
 *
 * A switch names its own ports ("gigabitEthernet 1/0/24", "Slot0/24") and the
 * customer's record names them however it was typed ("Port24"). Those are the
 * same port when their numbers agree. When BOTH names carry a full path
 * (1/0/24 against 1/1/24, two members of a stack) the whole path has to agree,
 * so a second stack member's port is never taken for the first's. Found on
 * the office rack on 25 September 2026: 56 ports offered as "not in NetBox"
 * that were Port1 to Port28 on both TP-Links.
 */
function samePortName(a, b) {
  if (String(a ?? '').trim().toLowerCase() === String(b ?? '').trim().toLowerCase()) return true;
  // "~9" is a port parked under a temporary name mid-rename, not port 9.
  if (/^~/.test(String(a ?? '')) || /^~/.test(String(b ?? ''))) return false;
  const da = portDigits(a); const db = portDigits(b);
  if (!da.length || !db.length) return false;
  if (da.length >= 2 && db.length >= 2) return da.join('/') === db.join('/');
  return da[da.length - 1] === db[db.length - 1];
}

/**
 * This port on the record's own device, found by its number.
 *
 * Only on a device the record already holds (the plan resolved it to a
 * NetBox id), only when exactly one of that device's ports has this number,
 * and never one already carrying somebody else's id. The port is bound and
 * keeps the record's name: nothing is renamed and no second port is made.
 */
async function portOnRecord(client, spec, obj, deviceId) {
  if (!Number.isInteger(deviceId) || obj.name == null || !portDigits(obj.name).length) return null;
  let rows;
  try {
    const res = await client.get(spec.endpoint, { device_id: deviceId, limit: 1000 });
    rows = res.results || [];
  } catch { return null; }
  const same = rows.filter((r) => samePortName(r.name, obj.name));
  if (same.length !== 1) return null;
  const row = same[0];
  const carried = (row.custom_fields || {})[UID_FIELD];
  if (carried && carried !== obj.uid) return null;
  return {
    id: row.id, row, by: 'port-number', writable: true,
    why: `the record holds this port as "${row.name}" on the same device`,
  };
}

async function proposeTarget(client, spec, obj, snapshot, { siteId, rackId, rackWasFound, deviceId = null }) {
  if (spec.field === 'interfaces') return portOnRecord(client, spec, obj, deviceId);
  if (spec.field === 'racks') {
    const m = rackMatchOn(snapshot);
    if (!m) return null;
    if (m.by !== 'facility-id') {
      return { id: m.id, by: m.by || 'name', writable: false, row: null,
               why: `the customer's record has a rack that ${m.why || 'looks like this one'}, `
                 + 'found by its name. A name finds the record and proves nothing about the rack, '
                 + 'so nothing is bound to it until a person says it is the one' };
    }
    // The same refusal the person's own answer gets. A match the resolver made
    // is still a rack id, and a rack id names a row at any site there is.
    const noSite = siteUnknown(snapshot);
    if (noSite || !Number.isInteger(siteId)) {
      return { id: m.id, by: m.by, writable: false, row: null,
               why: `the customer's record has a rack that ${m.why || 'looks like this one'}, but `
                 + (noSite || 'the site this scan is at was not carried through to the plan, so the '
                   + 'rack could not be checked against it') };
    }
    const hit = await find.byId(client, spec.endpoint, m.id, {
      uid: obj.uid, expect: { siteId: siteId ?? null },
    });
    if (hit.none) {
      return { id: m.id, by: m.by, writable: false, row: null, blocked: Boolean(hit.blocked),
               why: `the rack this scan was recognised as is record ${m.id}, and ${hit.why}` };
    }
    // Re-minted honestly: the record stated this, a person did not. Rank 3, which
    // is still writable, and it says where it came from.
    const why = `the customer's own record holds this rack under its rack id (record ${hit.id}). ${m.why}`;
    const evidence = identity.evidence('modelled', why);
    return {
      ...hit, by: m.by, evidence, rank: evidence.rank,
      confidence: identity.confidenceOf([evidence]),
      writable: identity.writable(identity.confidenceOf([evidence])), why,
    };
  }

  if (spec.field !== 'devices') return null;
  // Only inside a rack the record actually holds. A rack this plan just created
  // holds nothing, so there is nothing to ask it about.
  if (!Number.isInteger(rackId) || !rackWasFound) return null;

  const type = (snapshot.deviceTypes || []).find((t) => t.uid === obj.deviceTypeUid) || null;
  const hit = await find.findDevice(client, {
    siteId: siteId ?? null, rackId, position: obj.position ?? null,
    serial: obj.serial ?? null, assetTag: obj.assetTag ?? null,
    name: obj.name ?? null, face: obj.face ?? null, uid: obj.uid,
    // So a serial field holding the box's own model number is not taken for a
    // serial, which is the same gate identity.aliasesOf applies.
    models: [type ? type.model : null, obj.model ?? null].filter(Boolean),
  });
  if (hit.ambiguous) return hit;
  if (hit.none) return hit.blocked ? hit : null;

  // Found by a strong key, but in a different rack from the one being scanned.
  // That is the plan's finding "a device turns up in a different rack from its
  // record", and it is reported rather than bound: moving somebody's asset
  // between racks is not a side effect of a photograph.
  const at = (hit.row || {}).rack;
  const inRack = at && typeof at === 'object' ? Number(at.id) : Number(at);
  if (Number.isFinite(inRack) && inRack !== rackId) {
    return { ...hit, writable: false,
             why: `${hit.why}, and that record sits in rack ${inRack} rather than this one, so `
               + 'nothing is bound to it here' };
  }
  return hit;
}

/**
 * The record that might be this object, said out loud and bound to nothing.
 *
 * This is the shortlist the plan asks for: two or three candidates with the
 * reason, put to a person. It is also where a lookup that could not be made ends
 * up, so "the record could not be asked" reaches the admin instead of passing as
 * a shrug nobody sees.
 */
function sayCandidate(spec, obj, hit, report) {
  if (hit.ambiguous) {
    const why = `More than one record could be this ${spec.label.toLowerCase()}: ${hit.why} `
      + 'Nothing is bound until a person says which.';
    report.findings.push({
      tier: 'medium', kind: 'record-candidates', type: spec.label, uid: obj.uid,
      candidates: hit.ambiguous, why,
    });
    report.warnings.push(why);
    return;
  }
  if (hit.none) {
    const why = `The customer's record could not be asked what this ${spec.label.toLowerCase()} is: ${hit.why}`;
    report.warnings.push(why);
    report.findings.push({
      tier: 'medium', kind: 'record-not-asked', type: spec.label, uid: obj.uid, why,
    });
    return;
  }
  const why = `The customer's record may already hold this ${spec.label.toLowerCase()}: ${hit.why}. `
    + 'That is not enough to write on it, so it is a question: confirm it is the same one, or say '
    + 'it is a different one.';
  report.findings.push({
    tier: 'medium', kind: 'record-candidate', type: spec.label, uid: obj.uid,
    netboxId: hit.id ?? null, by: hit.by ?? null, confidence: hit.confidence ?? null, why,
  });
  report.warnings.push(why);
}

/**
 * A catalogue entry NetBox already holds under its own name: reported, used and
 * left alone.
 *
 * One row, one finding, no change. The row is a noop because that is the truth
 * - nothing was written on the customer's object, not even our own id - and a
 * noop is not actionable, so nothing here is put to a person as a decision and
 * nothing here moves a fingerprint. What a person reads is the finding: the
 * thing was already there, it is being used, and this is what its record says.
 */
function sayFound(spec, obj, hit, report, { refused = null } = {}) {
  const what = String(spec.label).toLowerCase();
  const name = obj.name || obj.model || obj.label || obj.uid;
  const why = refused
    ? `NetBox would not make a second ${what} called "${name}" (${refused}), and it already holds `
      + `the one of that name: ${hit.why || `record ${hit.id}`}. That one is used and nothing was `
      + 'written on it.'
    : `The customer's record already holds this ${what}: ${hit.why || `record ${hit.id}`}. It is used `
      + 'as it stands and nothing was written on it.';
  report.findings.push({
    tier: 'low', kind: 'catalogue-already-there', type: spec.label, uid: obj.uid,
    netboxId: hit.id ?? null, by: hit.by ?? null, why,
  });
  report.changes.push({
    type: spec.label, uid: obj.uid, name: String(name), action: 'noop',
    netboxId: hit.id, reason: why,
  });
}

/**
 * More than one thing of one name, where NetBox keeps that name unique: said
 * out loud, and nothing is made.
 *
 * A second one is not made on a shrug. The row is a skip, so whatever refers to
 * it waits with it rather than being written against a guess.
 */
function sayTwoOfAName(spec, obj, hit, report) {
  const what = String(spec.label).toLowerCase();
  const name = obj.name || obj.model || obj.label || obj.uid;
  const why = `More than one ${what} in the customer's record is called "${name}": ${hit.why} `
    + 'Nothing is made and nothing is used until a person says which one this is.';
  report.findings.push({
    tier: 'medium', kind: 'catalogue-candidates', type: spec.label, uid: obj.uid,
    candidates: hit.ambiguous, why,
  });
  report.warnings.push(why);
  report.changes.push({
    type: spec.label, uid: obj.uid, name: String(name), action: 'skip', reason: why,
  });
}

async function walk(snapshot, client, apply, report, { boundField = true } = {}) {
  uniqueInterfaceNames(snapshot, report);
  const resolved = new Map();   // our uid -> NetBox id (or Pending)
  const skipped = new Set();    // uids excluded, so dependents can say why
  const failed = new Set();
  const counts = {};
  let rackNetboxId = null;
  // Was the rack found in the customer's record, rather than created by this very
  // plan? A rack RackTrack just created holds nothing, so there is no point
  // asking the record what is in it.
  let rackWasFound = false;
  // Records a person bound this scan's objects to, or that the resolver matched.
  // Held so the orphan check below does not report a device as missing from the
  // rack while this same plan is about to bind it: on a preview nothing has been
  // patched yet, so the record still carries no uid of ours.
  //
  // Racks and devices are kept apart. They were one set, and NetBox numbers the
  // two endpoints independently, so binding rack 7 silently hid device 7 - a
  // decommissioned box that this check exists to report - from the orphan list.
  const boundIds = { racks: new Set(), devices: new Set() };
  // Which uid claimed which record in this one plan, per endpoint. Two boxes
  // bound to one record is refused here as well as in the store, because the
  // preview must be what happens: it used to promise two rebinds of one record
  // and let the write arbitrate by loop order.
  const claimed = new Map();

  const bump = (k) => { counts[k] = (counts[k] || 0) + 1; };
  const keyOf = (spec, id) => `${spec.field}:${id}`;
  const remember = (spec, id) => {
    const set = boundIds[spec.field];
    if (set) set.add(id);
  };

  // Ask NetBox for this rack's objects once per type, up front, instead of
  // once per object inside the loop below. Every uid this snapshot writes
  // carries the rack id (rack:RK-…, dev:RK-…:u16, if:dev:RK-…), so a single
  // "contains RK-…" filter on the custom field brings back everything of ours
  // on that endpoint. Shared objects — manufacturers, device types, roles —
  // carry no rack id and still resolve one at a time; there are a handful.
  const rackKey = String(snapshot.rackUid || '').replace(/^rack:/, '');
  // A snapshot keyed on the customer's rack remembers the hash-based rack uid
  // it carried before (aliasOf). Objects written under that hash are the same
  // objects: the plan REBINDS each one (our custom field moves to the new uid,
  // nothing else is touched, and the admin sees it as a row like any other)
  // instead of creating a twin beside it.
  const aliasHash = String(snapshot.aliasOf || '').replace(/^rack:/, '');
  const alias = rackKey && aliasHash && aliasHash !== rackKey ? { key: rackKey, hash: aliasHash } : null;
  // How many records each endpoint still holds under the hash, per the alias
  // preload. An endpoint that answered zero has nothing left under the old
  // uid, so the leftover-twin check below can skip it without a round trip.
  const underHash = new Map();

  // A person's answer is stored against the box uid, and cv.js mints that uid
  // from the shelf. So a box read one unit off, or re-detected after a model
  // update, arrives under a uid the answer does not name, and the answer is
  // silently not applied. That is worth saying out loud rather than discovering
  // from a duplicate box in the customer's rack.
  //
  // An answer that cannot be applied also stops this plan CREATING boxes in the
  // customer's rack. The box the answer is about is almost certainly one of the
  // boxes in front of us, read at a different shelf - so a create here is a
  // second row in NetBox for hardware the customer already has a record of, put
  // there beside a warning saying so. A warning next to a duplicate is not a
  // refusal, so nothing is created until a person says which box the record is.
  const unapplied = [];
  const binding = bindingOn(snapshot);
  if (binding) {
    const here = new Set((snapshot.devices || []).map((d) => d.uid));
    const lost = Object.entries(binding.deviceNetboxIds && typeof binding.deviceNetboxIds === 'object'
      ? binding.deviceNetboxIds : {}).filter(([uid]) => !here.has(uid));
    for (const [uid, id] of lost) {
      unapplied.push({ uid, id: asId(id) });
      report.warnings.push(
        `Somebody said record ${id} is the box ${uid}, and this scan has no box called ${uid} - `
        + 'the box has moved shelf, or it was read differently this time. That answer is not '
        + 'applied to any box here. Say which box record ' + `${id} is before anything is written.`);
      report.findings.push({
        tier: 'medium', kind: 'binding-not-applied', type: 'Device', uid,
        netboxId: asId(id), why: `The answer naming record ${id} was given about the box ${uid}, `
          + 'which this scan does not have.',
      });
    }
  }
  /** The sentence a held-back create reads, or null when creates may proceed. */
  const createHeld = (spec, obj) => {
    if (spec.field !== 'devices' || !unapplied.length) return null;
    if (boundIdFor(snapshot, 'devices', obj.uid) !== null) return null;
    const said = unapplied.map((u) => `record ${u.id} was named as the box ${u.uid}`).join(', and ');
    return `${said}, and this scan has no box of that name. That record is probably this very box, `
      + 'read at a different shelf, so making a new one here would give the customer two records '
      + 'for one box. Nothing is created until somebody says which box that record is, or takes '
      + 'the answer back.';
  };

  if (rackKey && typeof client.preloadByUid === 'function') {
    const endpoints = [...new Set(orderedSpecs().map((s) => s.endpoint))];
    for (const ep of endpoints) {
      await client.preloadByUid(ep, { [`cf_${UID_FIELD}__ic`]: rackKey });
      // What a rebind looks for carries the hash, not the key, so it needs its
      // own preload or every rebound object costs a round trip.
      if (alias) underHash.set(ep, await client.preloadByUid(ep, { [`cf_${UID_FIELD}__ic`]: alias.hash }));
    }
  }

  // Catalogue entries nothing in this snapshot needs any more (overrides.js
  // names them when a box is moved onto the customer's own record).
  const deferred = new Set(Array.isArray(snapshot.deferScaffolding) || snapshot.deferScaffolding instanceof Set
    ? snapshot.deferScaffolding : []);

  let cleared = [];
  if (apply) {
    cleared = await makeRoom(snapshot, client);
    const moved = cleared.filter((c) => c.kind === 'device').length;
    const renamed = cleared.filter((c) => c.kind === 'interface').length;
    if (moved) {
      report.warnings.push(
        `${moved} device${moved === 1 ? ' was' : 's were'} taken out of ${moved === 1 ? 'its' : 'their'} U `
        + 'before being placed again, so that no two devices claimed the same U during the move.');
    }
    if (renamed) {
      report.warnings.push(
        `${renamed} port${renamed === 1 ? ' was' : 's were'} given a temporary name before being renamed, `
        + 'so that no two ports on one device held the same name during the change.');
    }
  }

  for (const spec of orderedSpecs()) {
    for (const obj of snapshot[spec.field] || []) {
      const misses = [];
      const ref = (uid) => {
        if (!uid) return null;
        if (resolved.has(uid)) return resolved.get(uid);
        misses.push(uid);
        return null;
      };

      const name = obj.name || obj.model || obj.label || obj.uid;

      // Rule 4 — a conflict is a finding, not a fact.
      if (!exportable(obj.evidence)) {
        skipped.add(obj.uid);
        report.changes.push({
          type: spec.label, uid: obj.uid, name: String(name), action: 'skip',
          reason: 'the sources disagree about this, so it goes to review and not to NetBox',
        });
        bump('skip');
        continue;
      }

      const payload = withUid(spec.payload(obj, ref), obj.uid, obj.customFields || {});

      if (misses.length) {
        const blockedBy = misses.filter((u) => skipped.has(u) || failed.has(u));
        skipped.add(obj.uid);
        report.changes.push({
          type: spec.label, uid: obj.uid, name: String(name), action: 'skip',
          reason: blockedBy.length
            ? 'held back with the record it belongs to'
            : 'held back: something it belongs to is missing from this scan',
        });
        bump('skip');
        continue;
      }

      let existing;
      try {
        // Previewing may answer from the preload; writing asks NetBox itself,
        // so a create is decided on what is there now, not on what was there
        // when the preload ran.
        existing = await client.findByUid(spec.endpoint, obj.uid, { fresh: apply });
      } catch (err) {
        failed.add(obj.uid);
        report.changes.push({
          type: spec.label, uid: obj.uid, name: String(name), action: 'fail',
          reason: `NetBox did not answer: ${refusalText(err)}`,
        });
        bump('fail');
        continue;
      }

      if (existing) {
        const { changed, pending } = diff(payload, existing);

        // Our uid already names one record and a person named another. Neither is
        // touched and neither is guessed between: both are said out loud so
        // somebody can settle it. It used to say "neither was changed" and then
        // patch the one our uid is on - renaming the customer's rack, re-siting it
        // and cutting its height - in the same response. A disagreement about
        // WHICH record this is waits for a person (rule 4), so nothing is written
        // on either of them and the objects under it wait too.
        const said = boundIdFor(snapshot, spec.field, obj.uid);
        if (said !== null && said !== existing.id) {
          skipped.add(obj.uid);
          const both = `${spec.label} "${name}" already carries ${obj.uid} on record ${existing.id}, `
            + `and somebody named record ${said} as the same thing. Neither was changed; `
            + 'a person has to say which one is right.';
          report.warnings.push(both);
          report.findings.push({
            tier: 'high', kind: 'two-records', type: spec.label, uid: obj.uid,
            netboxId: existing.id, named: said, why: both,
          });
          report.changes.push({
            type: spec.label, uid: obj.uid, name: String(name), action: 'skip',
            netboxId: existing.id, reason: both,
          });
          bump('skip');
          continue;
        }

        resolved.set(obj.uid, existing.id);
        if (spec.field === 'racks') { rackNetboxId = existing.id; rackWasFound = true; }
        const swap = replacement(spec, obj, existing, report);

        // Bound by a person, either because the answer still says so or because
        // the record itself is marked. The marker is what makes this durable: the
        // answer lives in a file that can be corrected, taken back or lost, and
        // the uid it wrote is permanent, so a bind whose only protection was the
        // file let the next compare rename and re-site the customer's rack.
        if (said !== null || boundOnRecord(existing)) {
          if (said !== null) remember(spec, said);
          bindOnly(spec, obj, existing, changed, report, allowanceFor(snapshot, obj.uid, existing.id));
        }
        // The record and this box disagree about which box it is. What the
        // hardware is stays as the customer has it until somebody settles it.
        if (swap) holdBack(spec, obj, existing, changed, report, swap);

        // The keyed record is here. Is there ALSO one under the old hash uid?
        // Then a twin was left behind (a rebind that never ran, or a second
        // photo pushed before this rack was keyed). Say so, plainly. Nothing
        // is patched or removed for it: which of the two is right is a
        // person's call, and rule 3 stands.
        const staleUid = alias ? aliasUid(obj.uid, alias.key, alias.hash) : null;
        if (staleUid && underHash.get(spec.endpoint) !== 0) {
          let twin = null;
          try { twin = await client.findByUid(spec.endpoint, staleUid, { fresh: apply }); } catch { twin = null; }
          if (twin) {
            report.warnings.push(
              `${spec.label} "${name}" also has an older record in NetBox (id ${twin.id}). `
              + 'Nothing was merged or removed.');
          }
        }

        if (!Object.keys(changed).length) {
          report.changes.push({
            type: spec.label, uid: obj.uid, name: String(name), action: 'noop',
            netboxId: existing.id, pendingRefs: pending,
          });
          bump('noop');
          continue;
        }
        if (apply) {
          try {
            // Only the fields that differ, never the whole mapped payload.
            // Sending all of it rewrites every field this system maps on the
            // customer's object, including blanking one it typed and we have
            // never read: mapping.js sends serial: '' for a device whose
            // serial no source stated, and diff() deliberately does not count
            // NetBox-null against our empty string as a difference, so the
            // admin approving one correction could not see the other field
            // being cleared. Approve one thing, change one thing.
            await client.patch(spec.endpoint, existing.id, onlyChanged(payload, changed));
          } catch (err) {
            failed.add(obj.uid);
            report.changes.push({
              type: spec.label, uid: obj.uid, name: String(name), action: 'fail',
              netboxId: existing.id, reason: `NetBox refused this change: ${refusalText(err)}`,
            });
            bump('fail');
            continue;
          }
        }
        report.changes.push({
          type: spec.label, uid: obj.uid, name: String(name), action: 'update',
          netboxId: existing.id, diff: changed, pendingRefs: pending,
        });
        bump('update');
        continue;
      }

      // Nothing of ours carries this uid, and this is a catalogue entry rather
      // than hardware: a site, a location, a make, a model, a role. Two
      // questions come before "make it", in this order.
      //
      // First: does this write need it at all? A comparison lists the catalogue
      // of every box in the photograph, ticked or not, so a write of one
      // approved shelf move carries the site the rack hangs off and the makes
      // and models guessed for fifteen boxes nobody decided about. What nothing
      // in the write refers to is named on the way in (approvals/write.js,
      // overrides.js) and is not made here. The object still resolves, as
      // something a later plan may make, so whatever refers to it can say so.
      //
      // Second: is it already there under its own name? The customer's estate
      // has their site, their makers and their roles in it, named as they name
      // them and carrying none of our ids - so our id finds nothing and this
      // read as a create. NetBox then refuses the create, because a site with
      // that name already exists, and on 21 September that refusal failed a
      // write an approver had signed: nothing at all was written, over one
      // object the write did not need. Asking the record by name settles it
      // before a single POST, in the preview as well as in the write, because
      // the preview is what happens.
      if (SCAFFOLDING.has(spec.field)) {
        if (deferred.has(obj.uid)) {
          resolved.set(obj.uid, new Pending(spec.label, obj.uid));
          report.changes.push({
            type: spec.label, uid: obj.uid, name: String(name), action: 'skip',
            reason: 'not needed: nothing this write changes refers to it, so it is not made in the '
              + "customer's record and what that record holds stays as they have it",
          });
          bump('skip');
          continue;
        }
        let known;
        try {
          known = await lookupByName(client, spec, payload, obj.uid);
        } catch { known = null; }
        if (known && known.id) {
          resolved.set(obj.uid, known.id);
          sayFound(spec, obj, known, report);
          bump('noop');
          bump('adopted');
          continue;
        }
        if (known && known.ambiguous) {
          skipped.add(obj.uid);
          sayTwoOfAName(spec, obj, known, report);
          bump('skip');
          continue;
        }
      }

      // Nothing carries this uid. Before calling it a create: was this same
      // object written under the scan's old hash uid? Then it is not new, it
      // is ours to rebind. Only the custom field moves; the object's name,
      // site, height and position are left exactly as they are.
      const oldUid = alias ? aliasUid(obj.uid, alias.key, alias.hash) : null;
      let previous = null;
      let boundBy = null;
      if (oldUid) {
        try {
          previous = await client.findByUid(spec.endpoint, oldUid, { fresh: apply });
        } catch (err) {
          failed.add(obj.uid);
          report.changes.push({
            type: spec.label, uid: obj.uid, fromUid: oldUid, name: String(name), action: 'fail',
            reason: `NetBox did not answer: ${refusalText(err)}`,
          });
          bump('fail');
          continue;
        }
      }

      // The second way to find a rebind's target. Nothing of ours carries this
      // uid and nothing of ours carried it under the old one either - but a
      // person, standing at the rack, said which row of the customer's own
      // record this is. find.byId checks that row is still there and still
      // free; it is never taken on trust.
      //
      // What happens next is deliberately unchanged: the same rebind, the same
      // custom field and nothing else, the same re-check immediately before the
      // patch, and the same visible plan row an admin approves.
      // The scope the target has to be inside. A rack has to be at the site this
      // scan resolved to and a device has to be in the rack being scanned: those
      // two checks are what stop a one-digit typo binding a rack in another
      // building, or a box in another rack, at the top of the ladder.
      //
      // Both of those checks are only worth having when the scope they compare
      // against exists, so an absent scope is a refusal rather than a skipped
      // check: no site means no rack bind, and an unresolved rack means no
      // device bind. scopeFor therefore never hands find.byId a null where the
      // scope was required - noScope is consulted first and the bind never gets
      // that far.
      const noScope = () => {
        if (spec.field === 'racks') return siteUnknown(snapshot);
        if (spec.field === 'devices') return rackUnknown(rackNetboxId);
        return null;
      };
      const scopeFor = () => {
        if (spec.field === 'racks') {
          return { siteId: scanSiteId(snapshot), shown: shownFor(snapshot, 'racks', obj.uid) };
        }
        if (spec.field === 'devices') {
          return {
            rackId: rackNetboxId === null || isPending(rackNetboxId) ? null : rackNetboxId,
            shown: shownFor(snapshot, 'devices', obj.uid),
          };
        }
        return {};
      };

      let bindWhy = null;
      if (!previous) {
        const said = boundIdFor(snapshot, spec.field, obj.uid);
        if (said !== null) {
          const blocked = noScope();
          if (blocked) {
            skipped.add(obj.uid);
            report.changes.push({
              type: spec.label, uid: obj.uid, name: String(name), action: 'skip',
              reason: `somebody said this is record ${said}, but ${blocked} Nothing was written.`,
            });
            report.warnings.push(`Record ${said} was named as ${spec.label.toLowerCase()} `
              + `"${name}", and ${blocked}`);
            bump('skip');
            continue;
          }
          const hit = await find.byId(client, spec.endpoint, said, { uid: obj.uid, expect: scopeFor() });
          if (hit.none) {
            skipped.add(obj.uid);
            report.changes.push({
              type: spec.label, uid: obj.uid, name: String(name), action: 'skip',
              reason: `somebody said this is record ${said}, but ${hit.why}. Nothing was written.`,
            });
            bump('skip');
            continue;
          }
          previous = hit.row;
          boundBy = hit;
          bindWhy = 'record-binding';
        }
      }

      // Nobody has answered about this object, so ask the customer's own record
      // what it is. This is the resolver slice 2 exists to wire: until it ran, a
      // rack the customer typed in was invisible and every box in it read as a
      // create that NetBox then refused on an occupied shelf.
      //
      // Only an answer the standard allows to be written binds: the rack's own
      // facility id inside its site, a serial, an asset tag. A shelf and a name
      // come back as 'possible', which is a question for a person and is
      // reported as a candidate, never bound.
      if (!previous) {
        const hit = await proposeTarget(client, spec, obj, snapshot, {
          siteId: scanSiteId(snapshot),
          rackId: rackNetboxId === null || isPending(rackNetboxId) ? null : rackNetboxId,
          rackWasFound,
          deviceId: obj.deviceUid ? resolved.get(obj.deviceUid) : null,
        });
        if (hit) {
          if (hit.id && hit.writable) {
            previous = hit.row;
            boundBy = hit;
            bindWhy = 'record-match';
          } else {
            sayCandidate(spec, obj, hit, report);
            // The record already has a box on this very shelf and nobody has
            // said whether it is this one. Creating a second box there is a
            // duplicate NetBox refuses on an occupied position, so the plan says
            // so now rather than the write discovering it: "reported when the
            // list is frozen, rather than found from an error at write time".
            if (hit.id && hit.by === 'rack-position') {
              skipped.add(obj.uid);
              report.changes.push({
                type: spec.label, uid: obj.uid, name: String(name), action: 'skip',
                netboxId: hit.id,
                reason: `${hit.why}. Nothing is written and no second box is made on that shelf `
                  + 'until somebody says whether this is that record.',
              });
              bump('skip');
              continue;
            }
          }
        }
      }

      if (previous && boundBy && !boundField) {
        // A bind that cannot be marked on the record cannot be protected from the
        // next compare, and an unprotected bind is how the customer's rack gets
        // renamed. So it waits rather than half-happening.
        skipped.add(obj.uid);
        const why = `${boundBy.why}, but this record cannot be marked as the customer's own in `
          + `NetBox (the ${BOUND_FIELD} field is missing), so nothing was bound to it.`;
        report.warnings.push(why);
        report.changes.push({
          type: spec.label, uid: obj.uid, name: String(name), action: 'skip',
          netboxId: previous.id, reason: why,
        });
        bump('skip');
        continue;
      }

      if (previous) {
        // Two boxes cannot be one record. Refused here, in the PREVIEW, because
        // the preview is exactly what happens: it used to print both rebinds,
        // an admin approved both, and the write did the first and skipped the
        // second, so which box got the record was decided by loop order.
        const alreadyClaimedBy = claimed.get(keyOf(spec, previous.id));
        if (alreadyClaimedBy && alreadyClaimedBy !== obj.uid) {
          skipped.add(obj.uid);
          const why = `Record ${previous.id} is already this plan's answer for ${alreadyClaimedBy}, `
            + `and ${obj.uid} names it too. One record is one ${spec.label.toLowerCase()}: say which `
            + 'of the two it is. Neither was changed.';
          report.warnings.push(why);
          report.findings.push({
            tier: 'high', kind: 'one-record-two-boxes', type: spec.label, uid: obj.uid,
            netboxId: previous.id, other: alreadyClaimedBy, why,
          });
          report.changes.push({
            type: spec.label, uid: obj.uid, name: String(name), action: 'skip',
            netboxId: previous.id, reason: why,
          });
          bump('skip');
          continue;
        }

        // The record says one box and this scan is looking at another: two
        // serials that disagree are evidence of two different boxes, so the
        // answer is not re-applied to whatever is on that shelf now. Nothing is
        // bound and nothing is written.
        const swap = boundBy ? replacement(spec, obj, previous, report) : null;
        if (swap) {
          skipped.add(obj.uid);
          const why = `${swap.why} So the answer naming record ${previous.id} is not applied to this `
            + 'box. Say which record this box is, and nothing is written until then.';
          report.warnings.push(why);
          report.changes.push({
            type: spec.label, uid: obj.uid, name: String(name), action: 'skip',
            netboxId: previous.id, reason: why,
          });
          bump('skip');
          continue;
        }

        // A shelf move a SPOC approved for this record, on this check. The only
        // fields of the customer's that are ever written on a bind, and only
        // when the record still stands where it stood when the move was
        // accepted: the change is from THAT shelf to this one, and a record
        // somebody has moved since is a different change nobody approved.
        const allow = boundBy ? allowanceFor(snapshot, obj.uid, previous.id) : null;
        const shelfMove = {};
        let movedSince = false;
        for (const k of allow ? Object.keys(allow) : []) {
          const was = current(previous, k) ?? null;
          if (sameShelfValue(was, payload[k])) continue;
          if (moveAllows(allow, k, { from: was, to: payload[k] })) shelfMove[k] = { from: was, to: payload[k] };
          else movedSince = true;
        }
        if (movedSince) {
          skipped.add(obj.uid);
          const why = `the record moved since this change was accepted: "${String(previous.name ?? '')}" is `
            + `on ${previous.position == null ? 'no shelf' : `U${Number(previous.position)}`} now. `
            + 'Nothing was written. Compare the rack again.';
          report.warnings.push(why.charAt(0).toUpperCase() + why.slice(1));
          report.changes.push({
            type: spec.label, uid: obj.uid, name: String(name), action: 'skip',
            netboxId: previous.id, reason: why,
          });
          bump('skip');
          continue;
        }

        if (apply) {
          // Look once more, right before the patch. The plan found the new uid
          // absent, but another writer may have minted it since, and two
          // objects with one uid is the failure the uid exists to prevent.
          let taken;
          try {
            taken = await client.findByUid(spec.endpoint, obj.uid, { fresh: true });
          } catch (err) {
            failed.add(obj.uid);
            report.changes.push({
              type: spec.label, uid: obj.uid, fromUid: oldUid, name: String(name), action: 'fail',
              netboxId: previous.id, reason: `NetBox did not answer: ${refusalText(err)}`,
            });
            bump('fail');
            continue;
          }
          if (taken) {
            failed.add(obj.uid);
            report.changes.push({
              type: spec.label, uid: obj.uid, fromUid: oldUid, name: String(name), action: 'fail',
              netboxId: previous.id,
              reason: 'another record took this id while the plan was running. Run the plan again',
            });
            bump('fail');
            continue;
          }
          // The other half of the same re-check, for a target a person named
          // rather than one we wrote ourselves: it must still be there, still
          // free of anybody else's uid, still inside the scope, and still the row
          // that was named, at the moment of the patch.
          if (boundBy) {
            const again = await find.byId(client, spec.endpoint, previous.id,
              { uid: obj.uid, expect: scopeFor() });
            if (again.none) {
              failed.add(obj.uid);
              report.changes.push({
                type: spec.label, uid: obj.uid, name: String(name), action: 'fail',
                netboxId: previous.id, reason: `the record changed while this was being approved: ${again.why}`,
              });
              bump('fail');
              continue;
            }
          }
          try {
            // The uid, and - on a record that was the customer's before this -
            // the mark that says so, written where the uid is written. Without
            // the mark the protection lived in a file beside the code, and
            // losing that file let the next compare rename and re-site their
            // rack. With it, every later compare knows what this record is
            // whatever any file says.
            const mark = boundBy && boundField
              ? { [BOUND_FIELD]: markFor(bindWhy, snapshot, boundBy) }
              : {};
            // One patch. The approved shelf rides with the uid, so NetBox records
            // one change on the record, and nothing of the customer's but the
            // shelf is in it.
            const shelf = Object.fromEntries(Object.keys(shelfMove).map((k) => [k, payload[k]]));
            await client.patch(spec.endpoint, previous.id,
              { ...shelf, custom_fields: { [UID_FIELD]: obj.uid, ...mark } });
          } catch (err) {
            failed.add(obj.uid);
            report.changes.push({
              type: spec.label, uid: obj.uid, fromUid: oldUid, name: String(name), action: 'fail',
              netboxId: previous.id, reason: `NetBox refused this change: ${refusalText(err)}`,
            });
            bump('fail');
            continue;
          }
        }
        resolved.set(obj.uid, previous.id);
        claimed.set(keyOf(spec, previous.id), obj.uid);
        if (spec.field === 'racks') { rackNetboxId = previous.id; rackWasFound = true; }
        if (boundBy) remember(spec, previous.id);
        if (!boundBy) replacement(spec, obj, previous, report);
        // What the record carried before. For our own rebind that is the uid we
        // wrote under the photo hash. For one a person bound, it is whatever the
        // customer's record carried, which is normally nothing at all - and
        // saying oldUid there would name a uid that was never on it.
        const wasUid = boundBy
          ? (((previous.custom_fields || {})[UID_FIELD]) || null)
          : oldUid;
        report.changes.push({
          type: spec.label, uid: obj.uid, fromUid: boundBy ? null : oldUid,
          name: String(name), action: 'rebind',
          netboxId: previous.id,
          // The target is INSIDE the diff, and that is not cosmetic. An approval
          // is a signature over uid, action and diff, and a bind's target is
          // chosen rather than derived from the uid - so with the id outside the
          // diff, a plan binding record 7 and a plan binding record 8 signed
          // identically, and a re-answer between the approval and the write moved
          // the uid onto a rack at another site that the admin never saw. Now the
          // signature changes with the target and the write is refused instead.
          diff: {
            [UID_FIELD]: { from: wasUid, to: obj.uid },
            ...(boundBy ? { recordId: { from: null, to: previous.id } } : {}),
            // The approved shelf is inside the diff for the same reason the
            // target is: a check moving the record to U20 and one moving it to
            // U21 must not sign identically.
            ...shelfMove,
          },
          ...(boundBy ? {
            boundBy: bindWhy || 'record-binding',
            evidence: boundBy.evidence ?? null,
            confidence: boundBy.confidence ?? null,
            // What the mark on the record says. Beside the diff and never in it:
            // the diff is what open approvals have signed.
            ...(boundField ? { boundMark: markFor(bindWhy, snapshot, boundBy) } : {}),
            reason: Object.keys(shelfMove).length
              ? `${boundBy.why}. The shelf is moved ${shelfMove.position
                ? `from U${Number(shelfMove.position.from)} to U${Number(shelfMove.position.to)} ` : ''}`
                + 'on the word of the person who accepted that change, and the RackTrack id is written on '
                + 'it. Its name, its site, its rack, its role and what it is are left exactly as the '
                + 'customer has them.'
              : `${boundBy.why}. Only the RackTrack id is written on it: its name, its site, `
                + 'its height, its position and what it is are left exactly as the customer has them.',
          } : {}),
        });
        bump('rebind');
        continue;
      }

      // Nothing in NetBox carries this uid - it is a create. Unless an answer
      // about a box this scan does not have is still outstanding, in which case
      // a create is how that box gets recorded twice.
      const held = createHeld(spec, obj);
      if (held) {
        skipped.add(obj.uid);
        report.changes.push({
          type: spec.label, uid: obj.uid, name: String(name), action: 'skip', reason: held,
        });
        report.findings.push({
          tier: 'medium', kind: 'create-held', type: spec.label, uid: obj.uid,
          netboxId: unapplied[0] ? unapplied[0].id : null, why: held,
        });
        bump('skip');
        continue;
      }
      if (apply) {
        let created;
        try {
          created = await client.post(spec.endpoint, payload);
        } catch (err) {
          // NetBox refused because it already holds this object under its own
          // name. That is a match, not a failure: the thing we were about to
          // create is already there, it simply has never carried our uid.
          const claimed = await adopt(client, spec, payload, obj.uid, err,
            { stamp: !SCAFFOLDING.has(spec.field) });
          if (claimed) {
            resolved.set(obj.uid, claimed.id);
            // Claimed by its own name, with nothing written on it. There is no
            // change to report and no diff to sign, so it is a noop with a
            // finding beside it: a write that carries on is still a write
            // somebody should be able to read the reason for.
            if (claimed.stamped === false) {
              sayFound(spec, obj, claimed, report, { refused: refusalText(err) });
              bump('noop');
              bump('adopted');
              continue;
            }
            report.changes.push({
              type: spec.label, uid: obj.uid, name: String(name), action: 'update',
              netboxId: claimed.id,
              diff: { [UID_FIELD]: { from: null, to: obj.uid } },
              reason: 'NetBox already had this one, so it was updated rather than created',
            });
            bump('update');
            bump('adopted');
            continue;
          }
          failed.add(obj.uid);
          report.changes.push({
            type: spec.label, uid: obj.uid, name: String(name), action: 'fail',
            reason: `NetBox refused this change: ${refusalText(err)}`,
          });
          bump('fail');
          continue;
        }
        resolved.set(obj.uid, created.id);
        if (spec.field === 'racks') rackNetboxId = created.id;
        report.changes.push({
          type: spec.label, uid: obj.uid, name: String(name), action: 'create',
          netboxId: created.id, created: onlyScalars(payload),
        });
      } else {
        resolved.set(obj.uid, new Pending(spec.label, obj.uid));
        report.changes.push({
          type: spec.label, uid: obj.uid, name: String(name), action: 'create',
          created: onlyScalars(payload),
        });
      }
      bump('create');
    }
  }

  if (apply && cleared.length) await putBack(cleared, failed, client, report);
  report.counts = counts;
  // The one read of everything NetBox holds in this rack answers three things:
  // which records the scan did not see, what a suggestion may compare the
  // photo with, and nothing else is asked twice.
  const inRack = {};
  report.orphans = await orphans(snapshot, client, rackNetboxId, report, alias, boundIds.devices, inRack);
  report.records = recordsOf(inRack.present, boundIds.devices);
  // Evidence for a suggestion, never a reason for a comparison to fail.
  try { report.boxes = boxesOf(snapshot); } catch { report.boxes = []; }
  await offlineRows(snapshot, client, report, apply, { rackNetboxId, bump });
  return report;
}

/** NetBox nests a choice as {value, label} and a reference as {id, name, ...}. */
const choiceOf = (v) => (v && typeof v === 'object' ? (v.value ?? null) : (v ?? null));
const roleOf = (d) => {
  const r = d.role || d.device_role || null;
  return r && typeof r === 'object' ? { id: r.id ?? null, name: r.name ?? null, slug: r.slug ?? null } : null;
};
const typeOf = (d) => {
  const t = d.device_type && typeof d.device_type === 'object' ? d.device_type : null;
  if (!t) return { model: null, manufacturer: null, uHeight: null };
  const maker = t.manufacturer && typeof t.manufacturer === 'object' ? t.manufacturer.name : t.manufacturer;
  const tall = t.u_height === null || t.u_height === undefined || t.u_height === '' ? null : Number(t.u_height);
  return { model: t.model ?? null, manufacturer: maker ?? null, uHeight: Number.isFinite(tall) ? tall : null };
};

/**
 * Every device NetBox holds in this rack, as a suggestion reads it: enough to
 * say which shelves the record calls taken and what kind of box sits on each.
 * Reported, never fingerprinted, and nothing here reaches the write path.
 */
function recordsOf(present, bound = null) {
  const boundIds = bound instanceof Set ? bound : new Set();
  return (Array.isArray(present) ? present : []).map((d) => {
    const type = typeOf(d);
    return {
      netboxId: d.id, name: d.name ?? null, position: d.position ?? null, uHeight: type.uHeight,
      face: choiceOf(d.face), status: choiceOf(d.status), role: roleOf(d),
      deviceType: { model: type.model, manufacturer: type.manufacturer },
      serial: d.serial || null, assetTag: d.asset_tag || null,
      uid: (d.custom_fields || {})[UID_FIELD] || null,
      bound: boundOnRecord(d) || boundIds.has(d.id),
    };
  });
}

/** The boxes of this photograph, as a suggestion reads them. */
function boxesOf(snapshot) {
  if (!snapshot || !Array.isArray(snapshot.devices)) return [];
  const shaped = { ...snapshot, deviceTypes: snapshot.deviceTypes || [], manufacturers: snapshot.manufacturers || [] };
  const spans = spanByUid(shaped);
  const byUid = new Map(snapshot.devices.map((d) => [d.uid, d]));
  const typeEvidence = new Map(shaped.deviceTypes.map((t) => [t.uid, t.evidence]));
  return cameraDevices(shaped).map((seen) => {
    const d = byUid.get(seen.uid) || {};
    // The socket list and the rectangle on the photo stay with the screens that draw them.
    const b = { ...seen };
    delete b.sockets;
    delete b.box;
    return {
      ...b,
      span: spans.get(b.uid) ?? 1,
      // A model is evidence only when it was read off the faceplate. One the
      // camera made up from the class and the port count says nothing.
      modelIsOcr: typeEvidence.get(d.deviceTypeUid) === 'cv_ocr',
      assetTag: d.assetTag || null,
      evidence: d.evidence ?? null,
    };
  });
}

/**
 * Records a SPOC said to mark offline: in this rack, in the record, not seen by
 * this scan. One `update` row each, status to offline and nothing else, so the
 * change is fingerprinted, signed, rechecked, written, checked after the write
 * and registered like any other row. This writer never deletes, and this is
 * not a delete: the record stays, with everything the customer wrote on it.
 *
 * The record is read fresh by its id every time, because the answer decides a
 * write. It has to be in the rack being scanned still, hold the status it held
 * when the person decided, and not be a box this scan carries after all.
 */
async function offlineRows(snapshot, client, report, apply, { rackNetboxId = null, bump = () => {} } = {}) {
  const asked = snapshot && snapshot.approvedOffline && typeof snapshot.approvedOffline === 'object'
    ? snapshot.approvedOffline : null;
  if (!asked) return;
  const endpoint = '/api/dcim/devices/';
  const carried = new Set((snapshot.devices || []).map((d) => d.uid));
  for (const [key, want] of Object.entries(asked)) {
    const id = asId(key);
    if (id === null || !want || typeof want !== 'object') continue;
    const uid = `nb:device:${id}`;
    const row = (extra) => ({ type: 'Device', uid, name: String(want.name || `record ${id}`),
      netboxId: id, synthetic: 'offline', ...extra });
    const skip = (reason) => { report.changes.push(row({ action: 'skip', reason })); bump('skip'); };
    let record;
    try {
      const res = await client.get(endpoint, { id });
      record = (Array.isArray(res && res.results) ? res.results : []).find((r) => Number(r.id) === id) || null;
    } catch (err) {
      report.changes.push(row({ action: 'fail', reason: `NetBox did not answer: ${refusalText(err)}` }));
      bump('fail');
      continue;
    }
    if (!record) { skip('the record is no longer in NetBox, so there is nothing to mark offline'); continue; }
    const named = (extra) => ({ ...row(extra), name: String(record.name || want.name || `record ${id}`) });
    const inRack = record.rack && typeof record.rack === 'object' ? record.rack.id : record.rack;
    if (rackNetboxId === null || isPending(rackNetboxId) || asId(inRack) !== asId(rackNetboxId)) {
      skip('the record is no longer in this rack, so it is not marked offline from a scan of this rack');
      continue;
    }
    const itsUid = (record.custom_fields || {})[UID_FIELD] || null;
    if (itsUid && carried.has(itsUid)) {
      skip('this scan shows the box after all, so the record is not marked offline');
      continue;
    }
    const status = choiceOf(record.status);
    if (status === 'offline') {
      report.changes.push(named({ action: 'noop' }));
      bump('noop');
      continue;
    }
    if (status !== (want.from ?? 'active')) {
      skip(`the record's status changed to ${status || 'nothing'} since this was decided, so it was left as it is`);
      continue;
    }
    if (apply) {
      try {
        await client.patch(endpoint, id, { status: 'offline' });
      } catch (err) {
        report.changes.push(named({ action: 'fail', reason: `NetBox refused this change: ${refusalText(err)}` }));
        bump('fail');
        continue;
      }
    }
    report.changes.push(named({
      action: 'update', diff: { status: { from: status, to: 'offline' } },
      reason: 'Marked offline on the SPOC\'s word: the record is in this rack and the scan did not see it.',
    }));
    bump('update');
  }
}

/**
 * Devices the record holds for this rack that this scan did not see.
 *
 * This is the plan's finding "a device on the record, gone from the rack", and
 * until now it could not fire on the devices that matter. The check used to
 * throw away every record that did not carry our own uid, so the customer's own
 * devices - the ones somebody typed in when the rack was built, which are most
 * of them - were invisible to it and the list came back empty however full the
 * rack was. Now a record in this rack that this scan did not see is reported
 * whether RackTrack wrote it or the customer did, and the row says which.
 *
 * Rule 3 is unchanged and is the reason this is safe to widen: these are
 * REPORTED. Nothing here deletes, patches, or even reads a device into the
 * write path. It is still scoped to this one rack, so a device in another rack
 * is invisible to it.
 *
 * Three records are not "gone from the rack": one this scan carries under its own
 * uid, one a person has bound this scan to (which matters on a preview, where the
 * bind has not been written yet, so the record still carries nothing of ours),
 * and one a box in this scan plainly answers for - by its serial, its asset tag
 * or the shelf it is on. That third case is the one that made the widened check
 * lie: with the rack bound and its boxes not, every box the scan was looking at
 * was reported as missing from the shelf it was visible on, beside a create for
 * the same shelf that a real NetBox refuses. Both statements were about the same
 * box and one of them was false.
 *
 * So a record a box answers for comes back as "on the record, not yet bound",
 * with the box that answers named, and "gone from the rack" keeps its meaning:
 * the record says this box is here and the scan found that shelf empty.
 */
async function orphans(snapshot, client, rackNetboxId, report, alias = null, bound = null, keep = null) {
  if (rackNetboxId === null || isPending(rackNetboxId)) return [];
  let present;
  try {
    present = await client.paginate('/api/dcim/devices/', { rack_id: rackNetboxId });
  } catch (err) {
    // The admin reading the plan gets the plain line; the cause is an operator's
    // problem and is kept where an operator looks, rather than dropped for the
    // sake of a shorter sentence.
    console.warn('[netbox.writer] orphan check failed:', refusalText(err));
    report.warnings.push('NetBox could not be asked which devices are missing from this scan.');
    return [];
  }
  // Handed back to the caller, so what NetBox holds in this rack is read once.
  if (keep && typeof keep === 'object') keep.present = present;
  const seen = new Set();
  for (const d of snapshot.devices || []) {
    seen.add(d.uid);
    // A device this plan rebinds still carries its old uid until the push
    // runs. It was seen; it simply has not been renamed yet.
    const old = alias ? aliasUid(d.uid, alias.key, alias.hash) : null;
    if (old) seen.add(old);
  }
  const boundIds = bound instanceof Set ? bound : new Set();

  // This scan's boxes, indexed by the three things a record can be recognised
  // by. Nothing here binds anything: it only decides whether the scan can be
  // said to have seen the box a record describes.
  const bySerial = new Map();
  const byTag = new Map();
  const byShelf = new Map();
  for (const d of snapshot.devices || []) {
    const serial = identity.normalise(d.serial);
    if (serial && !identity.isJunkValue(d.serial)) bySerial.set(serial, d);
    const tag = identity.normalise(d.assetTag);
    if (tag && !identity.isJunkValue(d.assetTag)) byTag.set(tag, d);
    if (d.position !== null && d.position !== undefined) byShelf.set(Number(d.position), d);
  }
  const answersFor = (d) => {
    const serial = identity.normalise(d.serial);
    if (serial && bySerial.has(serial)) return { box: bySerial.get(serial), by: 'the same serial number' };
    const tag = identity.normalise(d.asset_tag);
    if (tag && byTag.has(tag)) return { box: byTag.get(tag), by: 'the same asset tag' };
    const at = d.position === null || d.position === undefined ? null : Number(d.position);
    if (at !== null && byShelf.has(at)) return { box: byShelf.get(at), by: `a box on shelf U${at}` };
    return null;
  };

  // What kind of box the record says it is. A suggestion reads these to tell a
  // switch that has gone from a PDU no front photograph could ever show.
  const seenAs = (d) => ({ role: roleOf(d), deviceType: typeOf(d), assetTag: d.asset_tag || null,
    face: choiceOf(d.face) });

  return present.flatMap((d) => {
    const uid = (d.custom_fields || {})[UID_FIELD] || null;
    if (uid && seen.has(uid)) return [];
    if (boundIds.has(d.id)) return [];
    const ours = Boolean(uid);
    const answer = ours ? null : answersFor(d);
    if (answer) {
      return [{
        netboxId: d.id, name: d.name, uid, ours: false, seen: true,
        position: d.position ?? null, serial: d.serial || null,
        status: (d.status || {}).value,
        ...seenAs(d),
        matchedBox: answer.box.uid, matchedBy: answer.by,
        whose: 'the customer wrote this record and RackTrack has never touched it',
        recommendation: `Review. The record puts "${d.name}" here and this scan saw ${answer.by} `
          + `("${answer.box.name}") that nobody has said is this record. It is not missing. `
          + 'Confirm it is this record, or say it is a different box. Nothing has been changed.',
      }];
    }
    return [{
      netboxId: d.id, name: d.name, uid, ours, seen: false,
      position: d.position ?? null, serial: d.serial || null,
      status: (d.status || {}).value,
      ...seenAs(d),
      whose: ours
        ? 'RackTrack wrote this record'
        : 'the customer wrote this record and RackTrack has never touched it',
      recommendation: ours
        ? 'Review. It was in a previous scan and is absent from this one. '
          + 'Not deleted. Set status=offline only after a human checks.'
        : 'Review. The record says this box is in this rack and this scan did not see it. '
          + 'Nothing has been changed and nothing will be: this record is the customer\'s, '
          + 'and nothing here deletes.',
    }];
  });
}

const newReport = (snapshot, dryRun, client) => ({
  rackUid: snapshot.rackUid, dryRun, netboxUrl: client.url,
  customField: '', boundField: '', changes: [], orphans: [], counts: {}, warnings: [],
  // Findings are what the record says that the rack contradicts, as opposed to
  // changes, which are what this plan would write. Today only the plan's high
  // finding "Replaced" lands here.
  findings: [],
});

/**
 * Dry run. Performs no writes and returns exactly what push() would do.
 *
 * ensureField is off by default so a plan really is read-only. The cost is
 * that if the racktrack_uid custom field does not exist yet, nothing can be
 * matched and every object reads as a create — so we say so out loud rather
 * than let the number mislead.
 */
async function plan(snapshot, client, { ensureField = false } = {}) {
  const report = newReport(snapshot, true, client);
  const cf = await client.customField();
  if (!cf) {
    if (ensureField) {
      await client.ensureCustomField(objectTypes());
      report.customField = 'created';
    } else {
      report.customField = 'missing';
      report.warnings.push(
        'NetBox is not set up for RackTrack yet, so every record below looks new. '
        + 'Exporting sets it up and matches them.');
    }
  } else {
    report.customField = 'present';
  }

  // The field that marks a record as the customer's own, checked HERE as well as
  // in push(). It used to be checked only on the way out, so a preview printed
  // rebind rows an admin approved and the write then skipped every one of them,
  // and the frozen rule is that the preview is what happens. A plan cannot know
  // whether a field it cannot see could be created, and being unable to check is
  // not permission to promise: an absent field means the binds are shown as
  // held, not as done.
  let boundField = true;
  if (typeof client.customField === 'function') {
    let have = null;
    try {
      have = await client.customField(BOUND_FIELD);
    } catch (err) {
      boundField = false;
      report.boundField = 'UNKNOWN';
      report.warnings.push(
        `NetBox could not be asked whether the '${BOUND_FIELD}' field exists `
        + `(${JSON.stringify(err.detail ?? err.message)}), so no record is shown as bound in this `
        + 'preview: a bind that cannot be marked as the customer\'s own cannot be protected from '
        + 'the next compare.');
    }
    if (boundField && !have) {
      if (ensureField && typeof client.ensureBoundField === 'function') {
        const bf = await client.ensureBoundField();
        report.boundField = `${bf.action} (schema change made so this diff is accurate)`;
      } else {
        boundField = false;
        report.boundField = 'ABSENT';
        report.warnings.push(
          `The '${BOUND_FIELD}' field does not exist in NetBox yet, so a record the customer owns `
          + 'cannot be marked as theirs and nothing is shown here as bound to one. Export creates '
          + 'the field; run this preview again after that and the binds appear.');
      }
    } else if (boundField && have) {
      report.boundField = 'present';
    }
  }
  return walk(snapshot, client, false, report, { boundField });
}

/** Write to NetBox. Idempotent: safe to run on the same scan repeatedly. */
async function push(snapshot, client) {
  const report = newReport(snapshot, false, client);
  const cf = await client.ensureCustomField(objectTypes());
  report.customField = cf.action;
  // The field that marks a record as the customer's own. A bind that cannot be
  // marked cannot be protected from the next compare, so if this cannot be made
  // the binds are held back and the reason is said out loud rather than a rack
  // being bound now and renamed later.
  let boundField = true;
  if (typeof client.ensureBoundField === 'function') {
    try {
      const bf = await client.ensureBoundField();
      report.boundField = bf.action;
    } catch (err) {
      boundField = false;
      report.boundField = 'ABSENT';
      report.warnings.push(
        `The '${BOUND_FIELD}' field could not be made in NetBox (${JSON.stringify(err.detail ?? err.message)}), `
        + 'so a record the customer owns cannot be marked as theirs and nothing is bound to one in '
        + 'this write. Everything else is unaffected.');
    }
  }
  return walk(snapshot, client, true, report, { boundField });
}

module.exports = {
  plan, push, Pending, isPending, diff, current, aliasUid, EXPORT_ORDER, NetBoxError,
  // Exported for the test that holds the interface naming rule down. It runs
  // inside walk() and has no other way in, and the rule it enforces is the
  // one NetBox refuses a whole write over.
  _internal: { samePortName, portOnRecord, uniqueInterfaceNames, makeRoom, offlineRows, recordsOf, boxesOf, allowanceFor },
};
