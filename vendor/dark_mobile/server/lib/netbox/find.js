/**
 * Find the customer's own record, not only our own.
 *
 * Until this file existed, the only way anything in this system found an object
 * in NetBox was client.findByUid, which filters on OUR custom field
 * racktrack_uid. Nothing looked a device up by its serial, its asset tag, its
 * name or its shelf. So a rack the customer filled in by hand was invisible:
 * every box in it was planned as a create, NetBox refused the ones that
 * collided, and the devices already in the record could not be reported either.
 *
 * This module answers one question and writes nothing: "which NetBox object is
 * this, if any". It is a resolver. It never patches, never creates, never
 * stamps a uid, and it has no side effects at all, so it can be called from a
 * preview as freely as from a write.
 *
 * The owner's sentence is "connect a rack to the database rack exactly the one,
 * I need 100 percent guarantee on that". The guarantee is not that this always
 * answers. It is that it NEVER names the wrong one. So:
 *
 *   1. Every lookup is SCOPED. A rack is looked up inside one site and never
 *      across the whole instance; a device by name inside one site, by position
 *      inside one rack. An unscoped lookup is refused with a sentence saying
 *      what would settle it, because "Rack 1" exists at every site there is.
 *   2. Every lookup asks for TWO rows and refuses when two answer, naming both.
 *      It never takes the first hit. A tie is never broken by sort order.
 *   3. Every hit is VERIFIED against the value that was asked for, in the same
 *      normalised form identity.js compares hardware values in, because NetBox's
 *      own text filters are looser than they look.
 *   4. An object already carrying a racktrack_uid that is not the one being
 *      asked about is NEVER returned. It belongs to another identity, and
 *      claiming it is the "one uid on two objects" trap. One exception, and it
 *      is at the bottom of the file: byName answers about a CATALOGUE object -
 *      a site, a maker, a model, a role - whose name NetBox itself keeps unique
 *      and on which nothing is ever written. A site is one site whatever a scan
 *      called it, so the id another scan left on it is reported rather than
 *      being a reason to ask NetBox for a second site of that name.
 *   5. Every answer carries a plain English sentence a person reads.
 *
 * Three shapes come back, and only three:
 *
 *   { id, by, rank, evidence, confidence, row, why }   exactly one object
 *   { none: true, why }                                nothing answered
 *   { ambiguous: [ {id, name, ...}, ... ], why }       more than one answered
 *
 * `by` says which identifier did it: 'facility-id', 'serial', 'asset-tag',
 * 'rack-position' or 'name'. `rank` and `evidence` are minted through
 * identity.evidence so a rank this version cannot honestly produce cannot be
 * claimed here either. A record key that names the hardware - a facility id, a
 * serial, an asset tag - is 'modelled' (rank 3): the system of record states it.
 * A SHELF and a NAME are both 'inferred' (rank 8) with one candidate, which
 * reads as 'possible' and is never written. The plan's own confidence table
 * says so for the shelf ("only the shelf or the shape fits ... never written")
 * and standard 4.4 says so for the name: a name finds the record and proves
 * nothing about the hardware in it, and the box on a shelf today is not
 * necessarily the box that was on it yesterday.
 *
 * Nothing here binds anything. A bind is evidence plus a person, and the person
 * half arrives separately.
 */
const identity = require('./identity');
const { UID_FIELD } = require('./netbox');

/** The two endpoints this module looks in. */
const RACKS = '/api/dcim/racks/';
const DEVICES = '/api/dcim/devices/';

/** Ask for two. One is an answer; two is a refusal that names both. */
const LIMIT = 2;

/**
 * How many rows a "contains" lookup may answer with before the verification
 * runs. Two is right for a filter that answers only the row that matches; a
 * filter that also answers the rows around it needs room for the one that is
 * really being asked about, or the answer is decided by NetBox's sort order.
 */
const LOOSE_LIMIT = 25;

const text = (v) => String(v ?? '').trim();

/** A NetBox foreign key comes back as {id, ...} on a detail read and as an id on some filters. */
const idOf = (v) => (v && typeof v === 'object' ? v.id : v);

/** A finite number, or null. Anything that is not plainly a number is null. */
const num = (v) => {
  if (v === null || v === undefined || v === '') return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
};

/** The uid an object carries, or ''. */
const uidOn = (row) => String(((row || {}).custom_fields || {})[UID_FIELD] ?? '');

/** The model string a NetBox row's device type states, normalised, or ''. */
const modelOn = (row) => {
  const t = (row || {}).device_type;
  if (!t || typeof t !== 'object') return '';
  return identity.normalise(t.model ?? t.display ?? '');
};

/** A short, readable handle for a row in a refusal sentence. */
const label = (row) => {
  const r = row || {};
  const name = text(r.name) || text(r.display) || `id ${r.id}`;
  return `${name} (id ${r.id})`;
};

/**
 * Nothing is claimed.
 *
 * `blocked` separates the two reasons, and the difference decides whether a
 * weaker identifier may still be tried. Nothing in the record has this value,
 * so ask the next question: blocked false. Something DID answer and was refused
 * - it belongs to another identity - or the record would not answer at all:
 * blocked true, and the ladder stops there. A stronger key that has spoken is
 * never overruled by a weaker one.
 */
const none = (why, blocked = false) => ({ none: true, blocked, why });
const ambiguous = (rows, why) => ({
  ambiguous: rows.map((r) => ({ id: r.id, name: text(r.name) || null, uid: uidOn(r) || null })),
  why,
});

/**
 * Which evidence rank each identifier honestly earns.
 *
 *   record-binding  a person said so. Standard rank 1.
 *   facility-id, serial, asset-tag, rack-position
 *                   the system of record states it. Rank 3, 'modelled'.
 *   name            rank 8 with one candidate, which reads as 'possible' and is
 *                   never written: standard 4.4 forbids a name as an identity.
 */
const SOURCE_FOR = Object.freeze({
  'record-binding': 'confirmed',
  // A catalogue name NetBox itself keeps unique - a site, a maker, a role. The
  // record states there is exactly one thing of that name, so the name IS the
  // key here and not a guess about hardware. Rank 3, 'modelled'. This is the
  // one place a name earns that, and it is why byName below is the only door
  // to it: a rack name and a device name stay 'inferred', as 4.4 says.
  'unique-name': 'modelled',
  'facility-id': 'modelled',
  serial: 'modelled',
  'asset-tag': 'modelled',
  // A shelf is provisional, not a statement about the hardware in it. The
  // plan's confidence table puts "only the shelf fits" at 'possible', which is
  // put to a person as a question and never written, so it is minted at rank 8
  // here rather than as a record key. The record on U18 may be the box that was
  // swapped out yesterday.
  'rack-position': 'inferred',
  name: 'inferred',
});

/** The one answer, with its evidence minted honestly through identity.js. */
function found(row, by, why) {
  const source = SOURCE_FOR[by] || 'inferred';
  const evidence = source === 'inferred'
    ? identity.evidence('inferred', why, { candidateCount: 1 })
    : identity.evidence(source, why);
  const confidence = identity.confidenceOf([evidence]);
  return {
    id: row.id,
    by,
    rank: evidence.rank,
    evidence,
    confidence,
    // Says out loud what the confidence already means: this answer is a
    // question for a person, not something to write. A caller that binds on it
    // has to ignore two fields, not miss one.
    provisional: !identity.writable(confidence),
    writable: identity.writable(confidence),
    row,
    why,
  };
}

/**
 * One page of rows from an endpoint, and what went wrong if nothing came back.
 *
 * `{ rows: [] }` and `{ rows: null }` are kept apart on purpose. [] means NetBox
 * answered and there is nothing there, which is a fact. null means we do not
 * know, and a caller must not read that as "nothing there" and go on to create a
 * second copy.
 *
 * The error travels with it. A revoked token, a 403 on the devices endpoint and
 * a NetBox that is down all used to read as the same shrug, and the one action
 * that fixes the first of them - renew the token under Data Sources - was never
 * named to the person staring at the sentence.
 */
async function ask(client, endpoint, params, limit = LIMIT) {
  try {
    const res = await client.get(endpoint, { ...params, limit });
    return { rows: Array.isArray(res && res.results) ? res.results : [] };
  } catch (err) {
    return { rows: null, error: err || null };
  }
}

/** What NetBox said when it would not answer, in a sentence a person can act on. */
function blockedBy(err) {
  const status = Number((err || {}).status);
  const detail = (err || {}).detail;
  const said = detail === null || detail === undefined || detail === ''
    ? String((err || {}).message || '').slice(0, 200)
    : (typeof detail === 'string' ? detail : JSON.stringify(detail)).slice(0, 200);
  if (status === 401 || status === 403) {
    return `NetBox answered HTTP ${status}${said ? ` (${said})` : ''}. The token may have expired or `
      + 'may not be allowed to read this. Renew it under Data Sources. Nothing is claimed.';
  }
  if (Number.isFinite(status) && status > 0) {
    return `NetBox answered HTTP ${status}${said ? ` (${said})` : ''}, so nothing is claimed.`;
  }
  return `NetBox could not be reached${said ? ` (${said})` : ''}, so nothing is claimed.`;
}

/**
 * Judge one lookup's rows: one answer, no answer, or a refusal naming both.
 *
 * `keep` is the verification: NetBox's text filters match more loosely than
 * they read, so a row only counts when its own field really is the value that
 * was asked for. The count that decides ambiguity is the count of rows that
 * passed verification, so a loose filter's extra rows do not turn a clean
 * single answer into a refusal.
 */
function judge(res, { by, uid, asked, keep, refused = null }) {
  const rows = res === null || res === undefined ? null : res.rows;
  if (rows === null || rows === undefined) {
    return none(`the record could not be asked about ${asked}. ${blockedBy((res || {}).error)}`, true);
  }
  const real = rows.filter(keep);
  if (!real.length) {
    // A row that answered the filter and was then thrown out is worth saying
    // out loud: "nothing has this serial" is false when a row does hold it and
    // was refused for a reason of ours.
    if (typeof refused === 'function') {
      const said = refused(rows);
      // Blocked: a row DID answer this identifier and was refused for a reason
      // of ours, so the ladder stops here rather than letting a weaker rung
      // answer a question a stronger one has already spoken about.
      if (said) return none(said, true);
    }
    return none(`nothing in the record has ${asked}`);
  }
  if (real.length > 1) {
    return ambiguous(real,
      `${real.length} records have ${asked}: ${real.map(label).join(' and ')}. `
      + 'Nothing here can tell them apart, so none of them is claimed.');
  }
  const row = real[0];
  const carried = uidOn(row);
  if (carried && carried !== text(uid)) {
    return none(
      `${label(row)} has ${asked}, but it is already recorded as ${carried}, `
      + 'so it belongs to another RackTrack record and is not claimed here', true);
  }
  return found(row, by, `the record holds ${label(row)} with ${asked}`);
}

/** Has this lookup settled the question, one way or the other? */
const settled = (out) => !out.none || out.blocked;

// ── the rack ────────────────────────────────────────────────────────────────

/**
 * Which NetBox rack is this, inside one site.
 *
 * Facility id first, because that is the customer's own key for the rack and
 * rack ladder rung 2 asks for it by name. Then the rack's name, which finds the
 * record and proves nothing on its own.
 *
 * `siteId` is REQUIRED and there is no unscoped form. An earlier design looked
 * a rack up by name across the whole instance and took the first hit, and three
 * reviewers refuted it for exactly the reason you would expect: "Rack 1" at
 * another site was adopted and then moved.
 */
async function findRack(client, { siteId = null, facilityId = null, name = null, uid = null } = {}) {
  const site = siteId === null || siteId === undefined || siteId === '' ? null : Number(siteId);
  if (!Number.isFinite(site)) {
    return none('a site is needed before a rack can be looked up: the same rack name and the '
      + 'same rack id are used at more than one site, so an unscoped lookup could name the wrong '
      + 'building. Say which site this rack is in.');
  }
  if (!client) return none('there is no connection to the record, so nothing is claimed about this rack');

  const facility = text(facilityId);
  if (facility) {
    const res = await ask(client, RACKS, { site_id: site, facility_id: facility });
    const out = judge(res, {
      by: 'facility-id', uid,
      asked: `rack id ${facility} at this site`,
      keep: (r) => text(r.facility_id).toLowerCase() === facility.toLowerCase(),
    });
    // A facility id that answered nothing falls through to the name. A facility
    // id that answered two racks, or one that belongs to somebody else, does
    // not: the stronger key has spoken and the weaker one must not overrule it.
    if (settled(out)) return out;
  }

  const want = text(name);
  if (!want) {
    return none('this rack has no rack id and no name to look up, so nothing is claimed about it');
  }
  const res = await ask(client, RACKS, { site_id: site, name: want });
  return judge(res, {
    by: 'name', uid,
    asked: `the name ${want} at this site`,
    keep: (r) => text(r.name).toLowerCase() === want.toLowerCase(),
  });
}

// ── the device ──────────────────────────────────────────────────────────────

/**
 * Which NetBox device is this box, strongest identifier first.
 *
 *   serial         the factory's own number. Normalised through identity.js, so
 *                  FDO-2117-A0X9, fdo 2117 a0x9 and FDO2117a0x9 are one serial.
 *   asset tag      the customer's own sticker. Unique across a NetBox install.
 *   rack and shelf what the record says is on this shelf. The plan calls this
 *                  provisional and so does this: it is 'modelled', never a
 *                  statement about the hardware in the slot.
 *   name           finds the record and proves nothing. Inside the rack first,
 *                  then inside the site. Exact only - no nearest-name guess,
 *                  ever.
 *
 * Every step refuses on two hits and names both. A serial search is deliberately
 * NOT narrowed to the rack: a device that moved rack is found where it actually
 * is, which is the plan's rack case 04, rather than read as new here and missing
 * there.
 */
async function findDevice(client, {
  rackId = null, position = null, serial = null, assetTag = null,
  name = null, siteId = null, face = null, uid = null, models = [],
} = {}) {
  if (!client) return none('there is no connection to the record, so nothing is claimed about this box');
  const site = siteId === null || siteId === undefined || siteId === '' ? null : Number(siteId);
  const rack = rackId === null || rackId === undefined || rackId === '' ? null : Number(rackId);
  const scope = Number.isFinite(site) ? { site_id: site } : {};
  const tried = [];

  // ── serial ────────────────────────────────────────────────────────────────
  //
  // A serial goes through the same gate identity.aliasesOf uses. A field holding
  // the box's own model number is not a serial: two GS724Tv4 switches both
  // reporting GS724Tv4 would otherwise be one device at the top of the ladder,
  // and identity.js refuses exactly that. So a value equal to this box's model
  // is never asked about, and a row whose serial is its own device type's model
  // is never handed back.
  const wantSerial = identity.normalise(serial);
  const shapes = new Set((Array.isArray(models) ? models : [])
    .map((m) => identity.normalise(m)).filter(Boolean));
  const serialIsModel = Boolean(wantSerial) && shapes.has(wantSerial);
  if (text(serial) && !identity.isJunkValue(serial) && !serialIsModel) {
    // The customer may have typed the serial with dashes or spaces where the
    // switch published it bare, so both spellings are asked for, and then a
    // separator-tolerant question as well: NetBox's serial filter is exact, so
    // a record holding FDO-2117-A0X9 answers neither spelling of FDO2117A0X9
    // and the strongest rung on the ladder was being defeated by a hyphen.
    // Whatever comes back is verified on the normalised form, which is the only
    // comparison that means anything.
    const spellings = [...new Set([text(serial), wantSerial].filter(Boolean))];
    // The tolerant question is asked on the TAIL of the value, because that is
    // the part a separator cannot be sitting in the middle of: a record holding
    // FDO-2117-A0X9 contains a0x9, and neither spelling of FDO2117A0X9 finds it
    // otherwise. Short, so it is a filter and not a sweep, and every row it
    // brings back is still verified on the normalised form - so the worst this
    // can do is bring back rows that are then thrown away.
    const tail = wantSerial.length >= 4 ? wantSerial.slice(-4) : '';
    let rows = [];
    let error = null;
    const add = (page) => { for (const r of page) if (!rows.some((held) => held.id === r.id)) rows.push(r); };
    for (const spelling of spellings) {
      const page = await ask(client, DEVICES, { ...scope, serial: spelling });
      if (page.rows === null) { rows = null; error = page.error; break; }
      add(page.rows);
    }
    if (rows !== null && tail) {
      const page = await ask(client, DEVICES, { ...scope, serial__ic: tail }, 25);
      if (page.rows !== null) add(page.rows);
    }
    const out = judge(rows === null ? { rows: null, error } : { rows }, {
      by: 'serial', uid,
      asked: `the serial number ${text(serial)}`,
      keep: (r) => identity.normalise(r.serial) === wantSerial && modelOn(r) !== wantSerial,
      refused: (all) => {
        const shaped = all.filter((r) => identity.normalise(r.serial) === wantSerial
          && modelOn(r) === wantSerial);
        if (!shaped.length) return null;
        return `${shaped.map(label).join(' and ')} hold ${text(serial)} in the serial field, but that `
          + 'is their own model number and not a serial, so nothing is claimed from it';
      },
    });
    if (settled(out)) return out;
    tried.push('serial number');
  } else if (serialIsModel) {
    tried.push(`serial number (${text(serial)} is this box's model, not a serial)`);
  } else if (text(serial)) {
    tried.push(`serial number (${text(serial)} does not identify anything)`);
  }

  // ── asset tag ─────────────────────────────────────────────────────────────
  const wantTag = identity.normalise(assetTag);
  if (text(assetTag) && !identity.isJunkValue(assetTag)) {
    const res = await ask(client, DEVICES, { ...scope, asset_tag: text(assetTag) });
    const out = judge(res, {
      by: 'asset-tag', uid,
      asked: `the asset tag ${text(assetTag)}`,
      keep: (r) => identity.normalise(r.asset_tag) === wantTag,
    });
    if (settled(out)) return out;
    tried.push('asset tag');
  }

  // ── rack and shelf ────────────────────────────────────────────────────────
  const shelf = position === null || position === undefined || position === '' ? null : Number(position);
  if (Number.isFinite(rack) && Number.isFinite(shelf)) {
    const params = { rack_id: rack, position: shelf };
    if (text(face)) params.face = text(face);
    const res = await ask(client, DEVICES, params);
    const out = judge(res, {
      by: 'rack-position', uid,
      asked: `shelf U${shelf} of this rack`,
      keep: (r) => Number(r.position) === shelf
        && (!text(face) || !r.face || text((r.face || {}).value ?? r.face).toLowerCase() === text(face).toLowerCase()),
    });
    if (settled(out)) return out;
    tried.push('rack and shelf');
  }

  // ── name ──────────────────────────────────────────────────────────────────
  const want = text(name);
  if (want) {
    // Inside the rack first, then inside the site, and never wider. "SW1"
    // exists in every rack there is.
    const scopes = [];
    if (Number.isFinite(rack)) scopes.push([{ rack_id: rack }, 'in this rack']);
    if (Number.isFinite(site)) scopes.push([{ site_id: site }, 'at this site']);
    if (!scopes.length) {
      tried.push('name (there is no rack and no site to look inside, and a name on its own '
        + 'is not looked up across the whole record)');
    }
    for (const [params, where] of scopes) {
      const res = await ask(client, DEVICES, { ...params, name: want });
      const out = judge(res, {
        by: 'name', uid,
        asked: `the name ${want} ${where}`,
        keep: (r) => text(r.name).toLowerCase() === want.toLowerCase(),
      });
      if (settled(out)) return out;
    }
    if (scopes.length) tried.push('name');
  }

  const looked = tried.length ? `Looked by ${tried.join(', then ')}.` : 'There was nothing to look it up by.';
  return none(`nothing in the record matches this box. ${looked}`);
}

// ── an object a person already named ────────────────────────────────────────

/**
 * Which of the shown fields the live row no longer agrees with.
 *
 * A record binding is stored with the fields the person was reading when they
 * answered. A NetBox restored from a backup, or re-imported, renumbers ids while
 * the remembered answer still names 51 and 52, so the id alone is not the answer
 * to "is this the row they were shown". Only fields present on both sides are
 * compared, so an older binding that kept nothing is not refused for it.
 */
function disagreements(row, shown) {
  const want = shown && typeof shown === 'object' ? shown : null;
  if (!want) return [];
  const out = [];
  const say = (field, was, now) => out.push(`${field} was ${JSON.stringify(was)} and is now ${JSON.stringify(now)}`);
  const sameText = (a, b) => text(a).toLowerCase() === text(b).toLowerCase();
  if (text(want.name) && !sameText(want.name, row.name)) say('the name', text(want.name), text(row.name));
  if (text(want.facilityId) && !sameText(want.facilityId, row.facility_id)) {
    say('the rack id', text(want.facilityId), text(row.facility_id));
  }
  if (text(want.serial) && identity.normalise(want.serial) !== identity.normalise(row.serial)) {
    say('the serial number', text(want.serial), text(row.serial));
  }
  if (text(want.assetTag) && identity.normalise(want.assetTag) !== identity.normalise(row.asset_tag)) {
    say('the asset tag', text(want.assetTag), text(row.asset_tag));
  }
  // A shelf of zero is not a shelf: NetBox numbers a rack's units from one,
  // and a rack itself has none at all. A binding that kept a zero - which is
  // what a rack's own row gave - read as "the shelf was 0 and is now null" and
  // refused the very answer the person had just given.
  const shelf = num(want.position);
  if (shelf !== null && shelf !== 0 && shelf !== num(row.position)) {
    say('the shelf', shelf, num(row.position));
  }
  if (num(want.rackId) !== null && num(want.rackId) !== num(idOf(row.rack))) {
    say('the rack it is in', num(want.rackId), num(idOf(row.rack)));
  }
  if (num(want.siteId) !== null && num(want.siteId) !== num(idOf(row.site))) {
    say('the site', num(want.siteId), num(idOf(row.site)));
  }
  return out;
}

/**
 * The object at this NetBox id, when it is safe to treat it as ours to bind.
 *
 * This is the other half of the resolver: a person has already said which
 * record a box is, and the id travels with the scan. A person's word is rank 1,
 * the top of the ladder, and that is exactly why the id itself is checked
 * hardest. It arrives as a bare integer, so a transposed digit is the expected
 * input rather than an exotic one, and the file remembering it outlives the
 * NetBox it names.
 *
 * Four refusals, and the first three are the ones a typo actually hits:
 *
 *   - `expect.siteId`: a rack has to be at the site this scan resolved to. A
 *     rack in another building cannot be this rack.
 *   - `expect.rackId`: a device has to sit in the rack being bound. A box in
 *     another rack cannot be this box.
 *   - `expect.shown`: the row still has to look like the row the person was
 *     shown when they answered.
 *   - the uid rule every lookup above follows: an object carrying somebody
 *     else's racktrack_uid is never handed back.
 *
 * It asks NetBox itself every time and never anything remembered, because the
 * answer decides a write, and a preload is a snapshot of a moment.
 */
async function byId(client, endpoint, netboxId, { uid = null, expect = null } = {}) {
  const id = netboxId === null || netboxId === undefined || netboxId === '' ? null : Number(netboxId);
  if (!Number.isFinite(id)) return none('no record id was given, so nothing is claimed');
  if (!client) return none('there is no connection to the record, so nothing is claimed');
  let row;
  try {
    const res = await client.get(endpoint, { id });
    const hits = (Array.isArray(res && res.results) ? res.results : []).filter((r) => Number(r.id) === id);
    row = hits[0] || null;
  } catch (err) {
    return none(`the record could not be asked about id ${id}. ${blockedBy(err)}`, true);
  }
  if (!row) return none(`the record holds nothing at id ${id} any more`, true);
  const carried = uidOn(row);
  if (carried && carried !== text(uid)) {
    return none(`${label(row)} is already recorded as ${carried}, so it belongs to another `
      + 'RackTrack record and is not claimed here', true);
  }

  const want = expect && typeof expect === 'object' ? expect : {};
  const wantSite = num(want.siteId);
  if (wantSite !== null) {
    const at = num(idOf(row.site));
    if (at !== wantSite) {
      return none(`${label(row)} is at site ${at === null ? 'no site at all' : at} and this scan is of `
        + `site ${wantSite}, so it cannot be this one. Check the record number.`, true);
    }
  }
  const wantRack = num(want.rackId);
  if (wantRack !== null) {
    const at = num(idOf(row.rack));
    if (at !== wantRack) {
      return none(`${label(row)} is in rack ${at === null ? 'no rack at all' : at} and this scan is of `
        + `rack ${wantRack}, so it cannot be a box in this rack. Check the record number.`, true);
    }
  }
  const moved = disagreements(row, want.shown);
  if (moved.length) {
    return none(`${label(row)} is not the record that was named: ${moved.join(', ')}. `
      + 'Say which record this is again before anything is written.', true);
  }

  return found(row, 'record-binding', `a person named ${label(row)} as this object's record`);
}

// ── the catalogue ───────────────────────────────────────────────────────────

/**
 * Which NetBox object this catalogue entry is, by the name NetBox itself keeps
 * unique: a site, a location inside a site, a maker, a model of that maker, a
 * role.
 *
 * This exists because of a live write that was refused and lost everything
 * with it. The scan minted the site's id from the site's NAME - office
 * sprintpark - and the customer's own site of that name carries the slug
 * office-sprint. Nothing in NetBox carried the id we asked for, so the site
 * read as missing, a create was planned, and NetBox refused it with "site with
 * this name already exists". One object nobody needed took a shelf move an
 * approver had signed down with it.
 *
 * So the question asked here is the one the record can answer: what is the
 * thing of this name. Three things make that safe, and they are the same three
 * the rest of this file lives by:
 *
 *   - The comparison is on the NAME, trimmed and case-insensitive, because
 *     "Office-Sprintpark" and " office-sprintpark " are one site. Our slug is
 *     never compared: the slug is the customer's, and they are free to have
 *     chosen another one.
 *   - Two rows is a refusal that names both, never the first hit. Inside a
 *     scope where NetBox allows the name to repeat - a location in a site, a
 *     model of a maker - the scope is part of the question or it is not asked.
 *   - Nothing is written, so a row already carrying another RackTrack id is
 *     still the right object: a site is one site whatever a scan called it.
 *     That is SAID in the answer, as `carried`, and it is the caller's to
 *     report; it is not a reason to make a second site.
 */
async function byName(client, endpoint, {
  name = null, field = 'name', scope = null, what = 'record',
} = {}) {
  if (!client) return none(`there is no connection to the record, so nothing is claimed about this ${what}`);
  const want = text(name);
  if (!want) return none(`this ${what} has no name to look up, so nothing is claimed about it`);
  const where = scope && typeof scope === 'object' ? scope : {};
  const asked = `the name ${want}${Object.keys(where).length ? ' in this scope' : ''}`;
  // NetBox's own case-insensitive exact filter. A plain `name=` is exact and
  // case-sensitive on the versions that matter, which is how a differently
  // capitalised site stayed invisible; an instance that will not take the
  // lookup is asked the plain way instead, and the rows are verified either way.
  let res = await ask(client, endpoint, { ...where, [`${field}__ie`]: want });
  if (res.rows === null) res = await ask(client, endpoint, { ...where, [field]: want });
  if (res.rows === null) {
    return none(`the record could not be asked about ${asked}. ${blockedBy(res.error)}`, true);
  }
  /** A row whose own field really is this name, either side's spaces ignored. */
  const is = (r) => text(r[field]).toLowerCase() === want.toLowerCase();
  // Exact said nothing. A name typed with a space on the end is still that
  // name, so ask the looser way once - contains, case-insensitive - and let the
  // verification above decide, which is what it is for. More rows are asked for
  // here because "contains" answers with neighbours: "Office" matches every
  // office there is, and the one that IS this name has to be among them.
  if (!res.rows.some(is)) {
    const loose = await ask(client, endpoint, { ...where, [`${field}__ic`]: want }, LOOSE_LIMIT);
    if (loose.rows && loose.rows.some(is)) res = loose;
  }
  const real = res.rows.filter(is);
  if (!real.length) return none(`nothing in the record has ${asked}`);
  if (real.length > 1) {
    return ambiguous(real, `${real.length} records have ${asked}: ${real.map(label).join(' and ')}. `
      + 'Nothing here can tell them apart, so none of them is claimed.');
  }
  const row = real[0];
  const hit = found(row, 'unique-name', `the record already holds ${label(row)} under ${asked}`);
  return { ...hit, carried: uidOn(row) || null };
}

module.exports = { findRack, findDevice, byId, byName, disagreements, RACKS, DEVICES };
