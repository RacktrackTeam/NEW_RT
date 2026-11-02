/**
 * Bindings: which device a person said sits in which box, kept outside the scan.
 *
 * A binding is a dated, sourced statement that one device identity occupies one
 * box in one rack (standard section 2). It is a record in its own right, and the
 * reason it lives here rather than in the scan payload is a bug we had:
 * store.setPayload rewrites a scan's payload wholesale, so re-adopting a rack, or
 * re-running detection on it, silently dropped every match anybody had confirmed.
 * A person's answer must outlive the photograph they gave it about.
 *
 * So the file is keyed on the rack, not the scan: one file per scope, where a
 * scope is a tenant plus the scan's rack id - the one handle on a rack that
 * nothing recomputes. A second photo of the same rack lands in the same scope and
 * finds the same bindings by alias, without anybody confirming anything twice.
 *
 * Written in the same shape as unmanaged.js: plain JSON on disk, read and written
 * whole. There is no query load here - a rack holds a handful of boxes - and a
 * database would be one more thing to fail on a fresh machine. When that stops
 * being true, this module is the only thing that changes.
 *
 * What it will not do:
 *   - store a binding whose identity is only a name or a management address
 *     (standard 4.3, 4.4). Nothing weaker than a serial, chassis, bridge or
 *     management address gets to be an identity;
 *   - keep two bindings for one box, or two for one identity. A confirm replaces
 *     whatever it contradicts, and says so in the record it returns.
 */
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');

const identity = require('./identity');

const DATA_DIR = process.env.RT_DATA_DIR || path.join(__dirname, '..', 'data');
const DIR = path.join(DATA_DIR, 'bindings');

const nowIso = () => new Date().toISOString().replace(/\.\d+Z$/, 'Z');
const str = (v, max = 200) => String(v ?? '').trim().slice(0, max);
const asInt = (v) => { const n = Number(v); return Number.isFinite(n) ? Math.trunc(n) : null; };

/**
 * The scope a binding belongs to: this tenant, this rack.
 *
 * Keyed on the scan's rackId, which is a hash of the photograph and never
 * changes for the life of the rack's record, and NOT on the customer's rack key.
 * The key was the obvious choice and it was wrong: recogniseRack fails soft to
 * null whenever NetBox is unreachable for a second, so the same rack moved
 * between `t7|t7:5` and `t7|RK-ABCD` on an ordinary re-adopt and every
 * confirmation for it became invisible. The rackKey is recorded INSIDE each
 * binding instead, where losing the lookup cannot lose the record.
 *
 * The tenant is in front because racktrack_uid is one namespace for a whole
 * NetBox instance: two tenants that both type RK-ROW1 must not share bindings.
 */
function scopeOf({ tenantId = null, rackId = null } = {}) {
  const t = tenantId === null || tenantId === undefined || tenantId === ''
    ? '0' : str(tenantId, 40);
  return `t${t}|${str(rackId, 200) || 'unknown'}`;
}

/**
 * The scopes this rack's bindings may already sit in, from before the scope was
 * keyed on the rackId alone, and from a scan whose tenant was not yet known.
 *
 * Read so nobody's confirmation is lost, and migrated forward the first time the
 * rack is opened. Never written to.
 */
function legacyScopesOf({ tenantId = null, rackKey = null, rackId = null } = {}) {
  const t = tenantId === null || tenantId === undefined || tenantId === ''
    ? '0' : str(tenantId, 40);
  const key = str(rackKey, 200);
  const id = str(rackId, 200);
  const out = [];
  if (key) { out.push(`t${t}|${key}`); out.push(`t0|${key}`); }
  if (id && t !== '0') out.push(`t0|${id}`);
  return [...new Set(out)].filter((s) => s !== scopeOf({ tenantId, rackId }));
}

/**
 * The file a scope lives in.
 *
 * A readable slug so a person can find it, plus a hash of the exact scope so two
 * scopes can never land in one file however they are spelled.
 */
function fileFor(scope) {
  const s = String(scope || '');
  const slug = s.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 60) || 'scope';
  const hash = crypto.createHash('sha1').update(s).digest('hex').slice(0, 10);
  return path.join(DIR, `${slug}-${hash}.json`);
}

const empty = (scope) => ({ scope: String(scope || ''), nextId: 1, items: [], record: null });

function read(scope) {
  const file = fileFor(scope);
  if (!fs.existsSync(file)) return empty(scope);
  try {
    const db = JSON.parse(fs.readFileSync(file, 'utf8'));
    return {
      scope: String(scope || ''),
      nextId: asInt(db.nextId) || 1,
      items: Array.isArray(db.items) ? db.items : [],
      // The record binding lives in the same file and must survive every write
      // through it. It is read back here so confirm(), forget() and migrate(),
      // which all write the whole object, carry it forward instead of erasing
      // it the next time somebody says which switch is in which box.
      record: db.record && typeof db.record === 'object' ? db.record : null,
    };
  } catch {
    // A corrupt file is not a reason to fail a scan. It is a reason to behave as
    // though nobody has confirmed anything yet, which is the honest answer.
    return empty(scope);
  }
}

function write(scope, db) {
  fs.mkdirSync(DIR, { recursive: true });
  fs.writeFileSync(fileFor(scope), JSON.stringify(db, null, 2));
}

const view = (r) => JSON.parse(JSON.stringify(r));

/** Every binding in this scope, oldest first. */
function list(scope) {
  return read(scope).items.map(view);
}

/**
 * The binding for this identity, found by a shared strong alias.
 *
 * Returns { binding, by, rank } or null. When several bindings share an alias
 * with the identity - which a confirm is supposed to make impossible, but a hand
 * edited file is not - the record confirmed against THIS switch record wins
 * first, then the strongest shared alias, then the oldest binding, so the answer
 * never depends on file order and two records sharing one alias cannot hand each
 * other's box over on a coin toss.
 *
 * `opts.switchId` is the record being matched, used only as that tie-breaker.
 * `opts.weak` also reports a binding that shares nothing but a name or a
 * management address. It is NEVER returned as a match (4.3, 4.4); it comes back
 * as { weak: true } so the reason can say the confirmation exists and name what
 * to re-read.
 */
function find(scope, aliases, { switchId = null, weak = false } = {}) {
  const wanted = Array.isArray(aliases) ? aliases : [];
  if (!wanted.length) return null;
  const want = switchId === null || switchId === undefined ? null : String(switchId);
  let best = null;
  let nearest = null;
  for (const b of read(scope).items) {
    const hit = identity.sameDevice(b.aliases, wanted);
    if (!hit.same) {
      if (!weak || nearest) continue;
      const shared = identity.weakOverlap(b.aliases, wanted);
      if (shared.length) {
        nearest = { binding: view(b), by: identity.kindOf(shared[0]), rank: null,
                    alias: shared[0], weak: true, refutedBy: hit.refutedBy || null };
      }
      continue;
    }
    const mine = want !== null && String(b.switchId ?? '') === want;
    const better = best === null
      || (mine && !best.mine)
      || (mine === best.mine && (hit.rank < best.rank
        || (hit.rank === best.rank && b.id < best.binding.id)));
    if (better) {
      best = { binding: view(b), by: hit.by, rank: hit.rank, alias: hit.alias, weak: false, mine };
    }
  }
  if (best) { delete best.mine; return best; }
  return nearest;
}

/**
 * Record that a person put this device in this box.
 *
 * Replaces anything it contradicts: the box's previous binding, and any binding
 * that shares a strong alias with this identity. One box has at most one
 * binding, and one identity is bound in at most one box per rack, so a device
 * that moved down two shelves does not end up bound twice (standard 10.2).
 *
 * Refuses an identity with no strong alias. A binding keyed on a name is what
 * 4.4 exists to forbid, and it would be worse than no binding at all, because it
 * would be believed.
 */
function confirm(scope, {
  aliases, deviceUid, position = null, switchId = null,
  evidence = null, by = null, at = null, why = '',
  scanId = null, snapshotStamp = null, boxPrint = null, rackKey = null,
} = {}) {
  const strong = (Array.isArray(aliases) ? aliases : []).filter((a) => identity.isStrong(a));
  if (!strong.length) {
    return { error: 'This switch published nothing that identifies it, so the match cannot be confirmed. '
      + 'A serial number, a chassis address, a bridge address or a management MAC is needed.' };
  }
  const uid = str(deviceUid, 200);
  if (!uid) return { error: 'Say which box is being confirmed.' };

  const ev = Array.isArray(evidence) && evidence.length
    ? evidence
    : [identity.evidence('confirmed', why || 'a person on site confirmed this box')];
  const confidence = identity.confidenceOf(ev);

  const db = read(scope);
  const keep = [];
  const replaced = [];
  for (const b of db.items) {
    const sameBox = b.deviceUid === uid;
    const sameThing = identity.sameDevice(b.aliases, aliases).same;
    if (sameBox || sameThing) replaced.push(view(b));
    else keep.push(b);
  }

  const rec = {
    id: db.nextId++,
    scope: String(scope || ''),
    deviceUid: uid,
    position: asInt(position),
    switchId: switchId === null || switchId === undefined ? null : str(switchId, 40),
    // The photograph this was observed against. Standard 10.1: a position is
    // observed fresh in each scan, and an earlier one is retained as a rank 4
    // source, never presented as current. These three fields are how the matcher
    // tells the two apart - same scan and same detection result means the
    // person's act is about the picture on the screen now; anything else is a
    // recollection, and a recollection is 'probable' at best.
    scanId: scanId === null || scanId === undefined ? null : str(scanId, 60),
    snapshotStamp: str(snapshotStamp, 80) || null,
    // What the box looked like when the person confirmed it, so a later
    // photograph showing a different box at the same shelf is a contradiction
    // (a replacement, 10.3) rather than something bound silently.
    boxPrint: boxPrint && typeof boxPrint === 'object' ? view(boxPrint) : null,
    // The customer's rack this scan was recognised as, when it was. Recorded
    // here rather than in the file name, because a NetBox lookup that fails for
    // one second must not move the record.
    rackKey: str(rackKey, 200) || null,
    // The whole set, not only the strong ones: a later reading that publishes
    // only the name still reads as the same record, and the strong aliases are
    // what identity.sameDevice will actually match on.
    aliases: [...new Set((Array.isArray(aliases) ? aliases : []).map((a) => str(a, 200)))].sort(),
    evidence: ev,
    confidence,
    by: by === null || by === undefined ? null : str(by, 120),
    at: str(at, 40) || nowIso(),
    why: str(why, 500),
  };
  db.items = [...keep, rec];
  write(scope, db);
  return { binding: view(rec), replaced };
}

/**
 * Move every binding held under an older scope spelling into this one.
 *
 * Called when a rack is opened, so a confirmation made when the scope was keyed
 * on the customer's rack key is found under the scope keyed on the rack id.
 * Idempotent, and it only writes when it actually moved something: a record
 * whose box is already bound here, or whose identity is, is left behind rather
 * than fighting with the newer answer.
 */
function migrate(scope, legacyScopes = []) {
  const moved = [];
  for (const from of Array.isArray(legacyScopes) ? legacyScopes : []) {
    if (!from || from === scope) continue;
    const old = read(from);
    if (!old.items.length && !old.record) continue;
    const db = read(scope);
    let changed = false;
    // The record binding moves with the confirmations, and only into a scope
    // that holds none: a newer answer here is never overwritten by an older one
    // kept under a spelling of the scope nobody uses any more.
    if (old.record && !db.record) { db.record = old.record; changed = true; }
    for (const b of old.items) {
      const clash = db.items.some((held) => held.deviceUid === b.deviceUid
        || identity.sameDevice(held.aliases, b.aliases).same);
      if (clash) continue;
      db.items.push({ ...view(b), id: db.nextId++, scope: String(scope || ''), movedFrom: from });
      moved.push(b.deviceUid);
      changed = true;
    }
    // The legacy file is emptied whether or not anything moved. A binding that
    // clashed is already held here under this scope, and one that moved is now
    // held twice. Leaving the old file behind meant every later open drained
    // it again, so a box somebody had said "not in this rack" about came back
    // on the next page load: forget() removed it from this scope, and migrate
    // copied it straight back out of the old one. A drain happens once.
    write(from, { ...old, items: [], record: null, drainedInto: String(scope || ''), drainedAt: nowIso() });
    if (changed) write(scope, db);
  }
  return moved;
}

// ── the record binding ──────────────────────────────────────────────────────
//
// A different question from the one above, kept in the same place because it
// has the same life. The bindings above say which LIVE SWITCH is in which box.
// A record binding says which row of the CUSTOMER'S OWN RECORD a rack and its
// boxes are - the NetBox rack the customer typed in by hand, and the devices
// already sitting in it.
//
// It lives here, in the same scope file, for the same reason: store.setPayload
// rewrites a scan's payload whole, so a binding kept on the scan is lost on the
// next re-adopt or re-detect, and a person's answer must outlive the
// photograph they gave it about. The scope is the tenant plus the scan's rack
// id, so a second photo of the same rack finds the same answer.
//
// Nothing here writes to NetBox. It records what a person said; the writer
// still plans a visible rebind row and an admin still approves it.

/**
 * A NetBox id, or null for anything that is not one.
 *
 * Strict on purpose, and the strictness is the guarantee. Number() alone turns
 * true into 1 and [7] into 7, so a client sending a checkbox value, or a
 * serialiser turning a flag into this field, minted a binding to whatever
 * NetBox row 1 happens to be. A record id is a number or a string of digits and
 * nothing else.
 */
const asNetboxId = (v) => {
  if (typeof v === 'number') return Number.isInteger(v) && v > 0 ? v : null;
  if (typeof v !== 'string') return null;
  const s = v.trim();
  if (!/^[0-9]+$/.test(s)) return null;
  const n = Number(s);
  return Number.isInteger(n) && n > 0 ? n : null;
};

/** The record binding for this rack, or null when nobody has said. */
function recordBinding(scope) {
  const rec = read(scope).record;
  return rec ? view(rec) : null;
}

/**
 * What a person was reading when they named a row, kept so the answer can be
 * refused later if the row stops being that row.
 *
 * Only the fields that identify it, and only the ones that were actually there.
 * A NetBox restored from a backup renumbers ids while the remembered answer
 * still names 51, so the id on its own is not enough to say "this is the row
 * they were shown".
 */
function shownOf(raw) {
  const o = raw && typeof raw === 'object' ? raw : null;
  if (!o) return null;
  const out = {};
  const put = (key, value, max = 200) => { const s = str(value, max); if (s) out[key] = s; };
  put('name', o.name);
  put('facilityId', o.facilityId ?? o.facility_id);
  put('serial', o.serial);
  put('assetTag', o.assetTag ?? o.asset_tag);
  const pos = asInt(o.position);
  if (pos !== null) out.position = pos;
  const rack = asNetboxId(o.rackId ?? o.rack_id ?? o.rack);
  if (rack !== null) out.rackId = rack;
  const site = asNetboxId(o.siteId ?? o.site_id ?? o.site);
  if (site !== null) out.siteId = site;
  return Object.keys(out).length ? out : null;
}

/**
 * Record which rows of the customer's own record this rack and its boxes are.
 *
 * Merges: a call naming only the rack leaves the boxes alone, and a call naming
 * one box leaves the rack and the other boxes alone. A box mapped to null is
 * removed, which is how a person takes an answer back.
 *
 * Refuses anything that is not a NetBox id, so a binding can never carry a
 * string, a zero or a negative number into the write path. Refuses one record
 * claimed by two boxes as well: the switch matcher has enforced "one box holds
 * one switch" for years, and without the same rule here the preview promised two
 * rebinds of one record and the write did the first and skipped the second, so
 * which box got the record was decided by the order of a loop.
 *
 * `rackShown` and `deviceShown` are what the person was looking at when they
 * answered, stored beside the id so the answer can refuse itself when the row
 * no longer matches.
 */
function bindRecord(scope, {
  rackNetboxId, deviceNetboxIds = null, rackShown = null, deviceShown = null,
  by = null, at = null, why = '',
} = {}) {
  const held = read(scope);
  const current = held.record && typeof held.record === 'object' ? held.record : {};
  const devices = { ...(current.deviceNetboxIds && typeof current.deviceNetboxIds === 'object'
    ? current.deviceNetboxIds : {}) };
  const heldShown = current.shown && typeof current.shown === 'object' ? current.shown : {};
  const shown = {
    rack: heldShown.rack && typeof heldShown.rack === 'object' ? { ...heldShown.rack } : null,
    devices: { ...(heldShown.devices && typeof heldShown.devices === 'object' ? heldShown.devices : {}) },
  };

  const refused = [];
  if (deviceNetboxIds && typeof deviceNetboxIds === 'object') {
    for (const [uid, value] of Object.entries(deviceNetboxIds)) {
      const key = str(uid, 200);
      if (!key) continue;
      if (value === null || value === undefined || value === '') {
        delete devices[key];
        delete shown.devices[key];
        continue;
      }
      const id = asNetboxId(value);
      if (id === null) { refused.push(key); continue; }
      devices[key] = id;
      const said = shownOf((deviceShown && typeof deviceShown === 'object' ? deviceShown : {})[key]);
      if (said) shown.devices[key] = said;
    }
  }

  let rack = current.rackNetboxId ?? null;
  if (rackNetboxId === null || rackNetboxId === '') {
    rack = null;
    shown.rack = null;
  } else if (rackNetboxId !== undefined) {
    const id = asNetboxId(rackNetboxId);
    if (id === null) refused.push('the rack');
    else {
      rack = id;
      const said = shownOf(rackShown);
      if (said) shown.rack = said;
    }
  }

  if (refused.length) {
    return { error: `This is not a record id: ${refused.join(', ')}. A record binding needs the `
      + 'number the customer\'s own record gives the rack or the box.' };
  }

  // One record is one box. Checked over the whole map, not only the rows this
  // call named, so a second answer cannot quietly join a first one.
  const byId = new Map();
  for (const [uid, id] of Object.entries(devices)) {
    const twin = byId.get(id);
    if (twin) {
      return { error: `Record ${id} is already the box ${twin}. One record is one box: say which `
        + `of ${twin} and ${uid} it is, or take the first answer back.` };
    }
    byId.set(id, uid);
  }

  const rec = {
    rackNetboxId: rack,
    deviceNetboxIds: devices,
    shown,
    by: by === null || by === undefined ? (current.by ?? null) : str(by, 120),
    at: str(at, 40) || nowIso(),
    why: str(why, 500) || str(current.why, 500),
  };
  write(scope, { ...held, record: rec });
  return { record: view(rec) };
}

/**
 * Take a record binding back: one box, or the whole rack when no box is named.
 *
 * Used when a person says they named the wrong record. Nothing in NetBox
 * changes: a uid already patched onto an object stays where it is, and undoing
 * that is its own approved change, never a side effect of forgetting.
 */
function forgetRecordBinding(scope, { deviceUid = null } = {}) {
  const held = read(scope);
  if (!held.record) return { error: 'Nothing in the record is bound to this rack.' };
  const uid = str(deviceUid, 200);
  if (!uid) {
    write(scope, { ...held, record: null });
    return { ok: true, forgot: 'the whole rack' };
  }
  const devices = { ...(held.record.deviceNetboxIds || {}) };
  if (!(uid in devices)) return { error: 'Nothing in the record is bound to that box.' };
  delete devices[uid];
  const heldShown = held.record.shown && typeof held.record.shown === 'object' ? held.record.shown : {};
  const shown = {
    rack: heldShown.rack ?? null,
    devices: { ...(heldShown.devices && typeof heldShown.devices === 'object' ? heldShown.devices : {}) },
  };
  delete shown.devices[uid];
  write(scope, { ...held, record: { ...held.record, deviceNetboxIds: devices, shown } });
  return { ok: true, forgot: uid };
}

/** Drop the binding for a box. Used when a person says it is not that one. */
function forget(scope, deviceUid) {
  const uid = str(deviceUid, 200);
  const db = read(scope);
  const before = db.items.length;
  db.items = db.items.filter((b) => b.deviceUid !== uid);
  if (db.items.length === before) return { error: 'Nothing is bound to that box.' };
  write(scope, db);
  return { ok: true, forgot: uid };
}

module.exports = {
  scopeOf, legacyScopesOf, fileFor, list, find, confirm, forget, migrate, DIR,
  recordBinding, bindRecord, forgetRecordBinding, asNetboxId, shownOf,
};
