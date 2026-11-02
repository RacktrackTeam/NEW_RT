/**
 * Join the camera scan with what the switches said about themselves.
 *
 * The camera knows WHERE a device sits (its U position) but guesses at what it
 * is. A managed switch knows exactly what it is (model, serial, every port)
 * but has no idea where it sits. Neither can be matched to the other
 * automatically with certainty, because the one shared handle -- how many
 * ports -- is not unique when a rack holds two identical switches. So the
 * match is proposed here by port count and CONFIRMED by a human in Review.
 *
 * Once a switch is matched to a rack position AND somebody has confirmed that box
 * at the rack, this overwrites the camera's guesses with the switch's own facts
 * (Evidence.SNMP), keeps the U position the camera gave (the switch cannot know
 * it), and builds cables from LLDP where both switches are placed in confirmed
 * boxes. A match nobody confirmed is shown on the screen and in the report, and
 * put into no record: a port-count agreement is not a serial number, and writing
 * one into a customer's DCIM converts our uncertainty into their fact.
 *
 * Nothing is invented. A field the switch did not state stays exactly as the
 * camera left it, and a cable whose two ends cannot both be resolved is not
 * drawn.
 */
const crypto = require('crypto');

const {
  Evidence, observed, Manufacturer, DeviceType, Interface, Cable, Termination,
} = require('./model');
const switches = require('./switches');
const identity = require('./identity');
const fingerprint = require('./fingerprint');
const bindings = require('./bindings');
const switchRecord = require('./switch_record');

const slug = (s) => String(s || '').toLowerCase().replace(/[^a-z0-9]+/g, '-')
  .replace(/^-+|-+$/g, '') || 'x';

// Chassis ids and MACs arrive with assorted separators; compare on hex only.
const normId = (x) => String(x || '').toLowerCase().replace(/[^0-9a-f]/g, '');

const clone = (o) => JSON.parse(JSON.stringify(o));

/** NetBox interface type, chosen from speed and whether it reads as fibre. */
function ifTypeFor(i) {
  const s = Number(i.speedMbps) || 0;
  const fibre = /sfp|fibre|fiber|base-?x/i.test(i.type || '');
  if (s >= 10000) return '10gbase-x-sfpp';
  if (s >= 1000) return fibre ? '1000base-x-sfp' : '1000base-t';
  if (s >= 100) return '100base-tx';
  if (s > 0) return '10base-t';
  return '1000base-t';
}

/** Physical port count a switch reported. */
const portsOf = (sw) => (sw.reading?.counts?.interfaces
  ?? sw.reading?.interfaces?.length ?? 0);

/** Every switch filed against this rack, paired with its last reading. */
function gatherSwitches(rackId) {
  return switches.list(rackId).map((record) => ({
    record,
    reading: switches.loadData(record.id),
  }));
}

/** The camera's devices, flattened for matching and for the Review screen. */
function cameraDevices(snapshot) {
  const typeOf = (uid) => snapshot.deviceTypes.find((t) => t.uid === uid);
  const mfrName = (uid) => (snapshot.manufacturers.find((m) => m.uid === uid)?.name || '');
  return (snapshot.devices || []).map((d) => {
    const type = typeOf(d.deviceTypeUid);
    return {
      uid: d.uid,
      name: d.name,
      position: d.position,
      cvClass: d.provenance?.cvClass || '',
      // Passive boxes are carried, so the Review screen can still draw the whole
      // rack, and flagged, so neither picker offers one as a switch. A screen
      // that offers what the server refuses turns a correction into an error.
      passive: isPassive(d.provenance?.cvClass),
      // The rectangle on the rack photo, so the Network screen can offer the
      // picture as a way of choosing rather than only a list of names.
      box: Array.isArray(d.provenance?.box) ? d.provenance.box : null,
      portCount: (snapshot.interfaces || []).filter((i) => i.deviceUid === d.uid).length,
      // Every socket the camera saw on this box, with whether it holds a cable.
      // Read here and nowhere later: reconcile() replaces a matched device's
      // interfaces with the switch's own, so after that the camera's side of
      // the comparison no longer exists in the snapshot.
      sockets: fingerprint.socketsOf({
        interfaces: (snapshot.interfaces || []).filter((i) => i.deviceUid === d.uid),
      }),
      model: type?.model || '',
      // The make OCR read off the faceplate, carried on the device's type.
      make: mfrName(type?.manufacturerUid),
      serial: d.serial || null,
    };
  });
}

/**
 * Normalise a make/model for comparison. "Unknown" and a bare "enterprise
 * <number>" (an SNMP vendor we could not name) both read as absent, so they
 * neither match nor penalise: an unnamed vendor is not a conflicting one.
 */
const norm = (s) => {
  const t = String(s || '').toLowerCase().replace(/[^a-z0-9]/g, '');
  if (t === 'unknown' || /^enterprise\d+$/.test(t)) return '';
  return t;
};

/**
 * Score one (switch, camera-device) pairing on three independent signals:
 * the model each claims, the make (OCR off the photo vs the switch's own
 * vendor), and how close their port counts are. Returns the score and a
 * plain-language reason, so the human verifying sees why it was proposed.
 *
 * None of these three is an identity. A model agreement is a shape agreement,
 * and two identical switches in one rack agree with each other exactly as well
 * as either agrees with itself. So a score is rank 8 evidence (standard section
 * 6, Inferred) and can never be better than 'possible'; what stops it being
 * acted on as a fact is the margin test in suggest, not the number itself.
 */
function scorePair(sw, dev) {
  const why = [];
  let score = 0;
  let model = 'nomodel';
  let make = 'unknown';

  const swModel = norm(sw.model);
  const devModel = norm(dev.model);
  if (swModel && devModel && !devModel.startsWith('unidentified') && swModel === devModel) {
    score += 200; why.push(`same model ${sw.model}`); model = 'model';
  }

  const swMake = norm(sw.vendor);
  const devMake = norm(dev.make);
  if (swMake && devMake) {
    if (swMake === devMake) { score += 100; why.push(`both ${sw.vendor}`); make = 'same'; }
    else { score -= 60; why.push(`make differs (${dev.make} vs ${sw.vendor})`); make = 'differs'; }
  }

  // A stack answers with every member's ports at once, and the camera draws one
  // box per member chassis, so the box a person is looking at holds a SHARE of
  // them. Compare against both and take whichever is closer, or a 2-member stack
  // of 24-port switches matches no box in its own rack.
  const shares = sw.members > 1
    ? [{ n: sw.ports, member: false }, { n: Math.round(sw.ports / sw.members), member: true }]
    : [{ n: sw.ports, member: false }];
  let near = null;
  for (const s of shares) {
    const diff = Math.abs(dev.portCount - s.n);
    if (!near || diff < near.diff) near = { ...s, diff };
  }
  const tol = Math.max(4, Math.round(near.n * 0.25));
  let ports;
  if (near.diff === 0) {
    score += 60; ports = '0';
    why.push(`exact ${near.n} ports`);
  } else if (near.diff <= tol) {
    score += 40 - near.diff * 2; ports = String(near.diff);
    why.push(`ports nearly match, ${dev.portCount} against ${near.n}`);
  } else {
    score -= 10; ports = 'far';
    why.push(`ports do not match, ${dev.portCount} against ${near.n}`);
  }

  // Rung 5 of the ladder rules a candidate OUT on its shape, it does not merely
  // score it down. A box cannot be a switch whose panel it could not hold.
  //
  // The two bounds are not symmetric, because the camera's errors are not. It
  // undercounts: a bottom row hidden behind a cable bundle is read as unknown,
  // so a 48 port switch can honestly come back as 24. It does not invent
  // sockets it cannot see, so a box showing far MORE sockets than the switch
  // has is not that switch.
  //
  // Found on a real rack: a 52 port D-Link was proposed for a 10 port box
  // because both were read as D-Link, while the 52 port box beside it carried
  // no readable maker. Make outranked shape, which is the wrong way round.
  const tooMany = dev.portCount > Math.ceil(near.n * 1.15) + 1;
  const tooFew = dev.portCount > 0 && dev.portCount < Math.floor(near.n * 0.45);
  if (near.n > 0 && dev.portCount > 0 && (tooMany || tooFew)) {
    const sockets = (n) => `${n} socket${n === 1 ? '' : 's'}`;
    return {
      score: -Infinity,
      why: tooMany
        ? `${sockets(dev.portCount)}, more than this switch has`
        : `${sockets(dev.portCount)}, too few for a ${near.n} port switch`,
      shape: 'ruled-out',
      ruledOut: true,
    };
  }

  // What this candidate was judged on, which is what the tie test compares. A
  // raw score gap is the wrong instrument: an exact port agreement scores 60 and
  // a two-port miss scores 36, so any fixed margin near that 24-point gap turns
  // "one right answer and one near miss" into "cannot tell", and camera port
  // counts are approximate. Two candidates are tied when they were judged on the
  // SAME evidence, which is what "cannot be told apart" actually means.
  //
  // One clause reaches the screen: the strongest signal, not the whole tally.
  // Three signals joined with commas read as the matcher showing its arithmetic,
  // which is not what a person deciding between two boxes needs.
  //
  // `stack` is not a reason and is not part of that clause. It is a fact about the
  // hardware - this box holds one unit of several - and the person may still have
  // to place the rest, so it is carried out as its own line rather than lost with
  // the tally that used to hold it.
  return {
    score,
    why: why[0] || '',
    shape: `${model}|${make}|${ports}`,
    stack: near.member ? sw.members : 0,
  };
}

/**
 * Boxes a managed switch can never be.
 *
 * A switch answers SNMP; a patch panel, a power strip, a power supply, a blanking
 * plate and a UPS have nothing to answer it with. The engine's own label set
 * (pipeline/detection.py) is the list this has to cover: it emits UPS, PSU and
 * Closed Unit as well as the three that were named here, and a confirm onto a UPS
 * box put a switch's serial and management address on that row and re-proposed it
 * on every later scan of the rack.
 *
 * Compared case-insensitively on purpose: one list, read by the matcher and by
 * the route, and neither gets to disagree about capital letters.
 */
const PASSIVE_CLASS = new Set([
  'Patch Panel', 'PDU', 'Empty', 'UPS', 'PSU', 'Closed Unit', 'Cable Manager',
]);
const PASSIVE_KEYS = new Set([...PASSIVE_CLASS].map((c) => c.toLowerCase()));
const isPassive = (cvClass) => PASSIVE_KEYS.has(String(cvClass || '').trim().toLowerCase());

// A score has to clear this before it counts as a candidate at all: at least a
// port-count agreement or a make match.
const FLOOR = 20;

/**
 * How many shelves a device of each class usually occupies.
 *
 * Taken from the "Device Size and Rack Unit Occupancy" table in
 * docs/reference/rack-planning-guide.html, and used ONLY to write a note on the
 * reason. It is not a veto and it does not change a score.
 *
 * It was a veto, and the veto was wrong. The guide says in the same table that
 * "these are representative examples only. Actual rack unit occupancy must be
 * verified against the specific equipment manufacturer's specifications", and it
 * lists a network switch as 1U to 2U - which no chassis switch obeys. As a
 * ceiling it deleted every Catalyst 4500, every Nexus and every box whose span
 * the detector merged from two shelves, and the reason text blamed the box size
 * so nobody could see the ceiling was the cause. Worse, it removed candidates
 * before the tie test, so a wrong exclusion turned "I cannot tell which of these
 * two" into one confident answer.
 *
 * The open-ended rows in that table ("2U-4U+", "2U-6U+") are capped at 8 here.
 */
const RU_BY_CLASS = new Map([
  ['patch panel', [1, 1]],
  ['cable manager', [1, 1]],
  ['switch', [1, 2]],
  ['network switch', [1, 2]],
  ['router', [1, 2]],
  ['firewall', [1, 2]],
  ['kvm', [1, 2]],
  ['server', [1, 4]],
  ['rack server', [1, 4]],
  ['storage', [2, 8]],
  ['storage system', [2, 8]],
  ['ups', [2, 8]],
]);

/**
 * The shelves each box in this photograph occupies.
 *
 * The engine's unit list is the direct answer and is per device; the device
 * type's height is a fallback, and only a fallback, because a type is shared by
 * model and its height is whichever box created it first.
 */
function spanByUid(snapshot) {
  const heightOf = new Map((snapshot.deviceTypes || []).map((t) => [t.uid, Number(t.uHeight)]));
  const out = new Map();
  for (const d of snapshot.devices || []) {
    const units = (Array.isArray(d.provenance?.cvUnits) ? d.provenance.cvUnits : [])
      .map((u) => parseInt(String(u).replace(/\D/g, ''), 10))
      .filter((n) => Number.isFinite(n))
      .sort((a, b) => a - b);
    const fromUnits = units.length ? units[units.length - 1] - units[0] + 1 : 0;
    const fromType = heightOf.get(d.deviceTypeUid);
    out.set(d.uid, fromUnits || (Number.isFinite(fromType) && fromType > 0 ? fromType : 1));
  }
  return out;
}

/**
 * A note when a box is a different size from what its class usually is.
 *
 * Recorded on the reason and nothing else, so a person reading the report can see
 * that the box is unusual for what the camera called it, and decide. It removes
 * no candidate and moves no score: the figures are representative, and the camera
 * class is a guess at a box whose span the detector may have merged.
 */
function sizeAdvice(boxUnits, cvClass) {
  const units = Number(boxUnits) > 0 ? Number(boxUnits) : 1;
  const range = RU_BY_CLASS.get(String(cvClass || '').trim().toLowerCase());
  if (!range || units <= range[1]) return null;
  return `it is ${units} rack units tall - check it is one box and not two`;
}

/**
 * How many physical units a reading genuinely describes, and how many it claims.
 *
 * `stackMembers` on a reading is the number of rows the device called a chassis
 * (collect.js filters entPhysicalClass 3), which is NOT a measurement: a single
 * switch that lists two chassis rows would be read as a two-unit stack. So it is
 * only believed as a member count where the rows carry DISTINCT serial numbers,
 * which is a stack answering as itself. The raw row count is kept for the note.
 */
function stackSizeOf(reading) {
  const rows = Number(reading?.identity?.stackMembers) > 0
    ? Number(reading.identity.stackMembers) : 1;
  const members = Array.isArray(reading?.identity?.members) ? reading.identity.members : [];
  const serials = new Set(members
    .map((m) => String(m && m.serial ? m.serial : '').toLowerCase().replace(/[^a-z0-9]+/g, ''))
    .filter((s) => s && !identity.isJunkValue(s)));
  return { rows, members: serials.size >= 2 ? serials.size : 1 };
}

/**
 * What the camera saw of one box, small enough to store with a binding.
 *
 * The point of keeping it is 10.3: when a person confirms a box and a later
 * photograph shows a different box at that shelf, that is a hardware replacement
 * and has to be reported as one, not bound silently to the old answer.
 */
function printOf(dev) {
  if (!dev) return null;
  return {
    position: dev.position ?? null,
    cvClass: dev.cvClass || '',
    ports: Number(dev.portCount) || 0,
    make: dev.make || '',
    model: dev.model || '',
    serial: dev.serial || null,
  };
}

/**
 * A short fingerprint of one detection result.
 *
 * Standard 10.1: a position is observed fresh in each scan. A confirmation is a
 * fresh observation only while the photograph it was made against is still the
 * one on the screen, so a binding carries this stamp and the matcher compares it.
 * A new photograph, or a re-run that read the rack differently, changes the stamp
 * and the confirmation becomes a rank 4 recollection: shown, never written.
 */
function snapshotStamp(snapshot) {
  const rows = cameraDevices(snapshot || {}).map((d) => {
    const p = printOf(d);
    return [d.uid, p.position, p.cvClass, p.ports, p.make, p.model, p.serial].join('~');
  }).sort();
  const head = [
    String((snapshot?.racks || [])[0]?.uid || ''),
    String(snapshot?.scannedAt || ''),
  ].join('~');
  return crypto.createHash('sha1').update([head, ...rows].join('\n')).digest('hex').slice(0, 16);
}

/**
 * Does this photograph contradict what was confirmed? A plain sentence when it
 * does, null when it does not.
 *
 * Only on a fact, never on camera noise: a shelf that moved, a serial read off
 * the box that is now a different serial, a model that is now a different model.
 * A port count that shifted by one is the camera, not the rack.
 */
function contradiction(was, now) {
  if (!was || !now) return null;
  const realModel = (m) => { const t = norm(m); return t.startsWith('unidentified') ? '' : t; };
  if (was.position != null && now.position != null && Number(was.position) !== Number(now.position)) {
    return `the box moved from U${was.position} to U${now.position}`;
  }
  const wasSerial = norm(was.serial);
  const nowSerial = norm(now.serial);
  if (wasSerial && nowSerial && wasSerial !== nowSerial) {
    return `that box now reads serial ${now.serial}, not ${was.serial}`;
  }
  const wasModel = realModel(was.model);
  const nowModel = realModel(now.model);
  if (wasModel && nowModel && wasModel !== nowModel) {
    return `that box now reads as ${now.model}, not ${was.model}`;
  }
  return null;
}

/** Stable ordering, numbers as numbers, so the answer never depends on input order. */
function cmpId(a, b) {
  const x = Number(a);
  const y = Number(b);
  if (Number.isFinite(x) && Number.isFinite(y) && x !== y) return x - y;
  return String(a).localeCompare(String(b));
}

/**
 * Propose which box each switch is, and say nothing when it cannot be told.
 *
 * Three passes, in this order, because the order is the whole design:
 *
 *   1. A binding wins outright. Somebody stood at the rack and said this switch
 *      is this box; no amount of port counting overrules that. The binding is
 *      found by the SET of hardware aliases the switch published, so a reading
 *      that names a serial and a reading that names a chassis address both find
 *      the same one (lib/netbox/identity.js explains why that matters).
 *   2. Every remaining switch is scored against every remaining box, with a size
 *      veto first. A score is rank 8 evidence and tops out at 'possible'.
 *   3. Anything two switches or two boxes cannot be told apart goes blank, with
 *      a reason that says what would settle it.
 *
 * Deterministic: switches are worked in id order and candidates are ranked by
 * score then uid, so reversing the switch list cannot change a single answer.
 *
 * Returns { matches, reasons } with the field names it always had. Each reason
 * now also carries the evidence it rests on, its confidence under standard
 * section 7, how many boxes were in the candidate set (8.3), the margin over
 * the runner-up, whether it came from a binding, and any notes worth showing.
 *
 * `opts.scope` is the bindings scope, which the route computes from the scan's
 * tenant and rack id. It is REQUIRED: a guessed scope reads an empty file and
 * quietly turns a confirmed box into an unidentified one, which is the worst
 * failure this module has, so there is no default.
 * `opts.scanId` and `opts.snapshotStamp` say which photograph this is, so a
 * confirmation made against it counts as a fresh observation (10.1) and one made
 * against an earlier one is recalled and marked.
 */
function suggest(snapshot, sws, opts = {}) {
  const scope = opts.scope;
  if (!scope) {
    throw new Error('reconcile.suggest needs opts.scope, the bindings scope for this scan '
      + '(bindings.scopeOf({ tenantId, rackId })). There is no safe default: a guessed scope '
      + 'reads an empty bindings file and reports a confirmed box as unidentified.');
  }
  const stamp = opts.snapshotStamp || snapshotStamp(snapshot);
  const scanId = opts.scanId === undefined || opts.scanId === null ? null : String(opts.scanId);
  const spans = spanByUid(snapshot);
  const devices = cameraDevices(snapshot)
    .filter((d) => !d.passive)
    .map((d) => ({
      ...d,
      // The switch's own port count is the truer number, so compare the
      // camera's detected ports to it, not the reverse.
      ports: d.portCount,
      vendor: d.make,
      units: spans.get(d.uid) || 1,
    }));
  const deviceByUid = new Map(devices.map((d) => [d.uid, d]));

  // Worked in id order, and the keys are created in id order too, so reversing
  // the switch list cannot change the answer or even the shape of it.
  const order = [...sws].sort((a, b) => cmpId(a.record.id, b.record.id));
  const matches = {};
  const reasons = {};
  for (const sw of order) { matches[sw.record.id] = null; reasons[sw.record.id] = null; }

  const facts = new Map();     // swId -> what the switch published about itself
  for (const sw of order) {
    const stack = stackSizeOf(sw.reading);
    facts.set(sw.record.id, {
      id: sw.record.id,
      label: sw.record.label,
      read: Boolean(sw.reading),
      ports: portsOf(sw),
      vendor: sw.reading?.system?.vendor || sw.reading?.identity?.manufacturer || null,
      model: sw.reading?.identity?.model || null,
      // The two handles the customer's record is joined on (switch_record.js):
      // what the switch says it is, and where it answered.
      serial: sw.reading?.identity?.serial || null,
      host: sw.record.host || null,
      sysName: sw.reading?.system?.sysName || null,
      // The corroborated member count, not the row count. See stackSizeOf.
      members: stack.members,
      chassisRows: stack.rows,
      aliases: sw.reading ? identity.aliasesOf({ ...sw.reading, host: sw.record.host }) : [],
    });
  }

  // ── 1. bindings ────────────────────────────────────────────────────────────
  const claimedBy = new Map();   // devUid -> swId, boxes a binding has taken
  const proposals = new Map();   // swId -> { devUid, reason }
  const notes = new Map();       // swId -> lines to carry onto whatever reason it ends with
  const recalledAt = new Map();  // swId -> a shelf a binding remembers but this photo lost
  const addNote = (id, line) => {
    const held = notes.get(id) || [];
    held.push(line);
    notes.set(id, held);
  };

  for (const sw of order) {
    const f = facts.get(sw.record.id);
    const hit = f.aliases.length
      ? bindings.find(scope, f.aliases, { switchId: f.id, weak: true })
      : null;
    if (!hit) continue;

    // A binding that shares only a name or a management address is not this
    // switch (4.3, 4.4), and saying nothing about it is how a person's answer
    // goes missing without a word. Name it, and say what to re-read.
    if (hit.weak) {
      addNote(f.id, hit.refutedBy === 'serial'
        ? 'a different switch now answers at this address. Confirm the box for the one '
          + 'that is there now'
        : 'this reading gives no serial to match an earlier confirmation. Read it again, '
          + 'or confirm the box here');
      continue;
    }

    const dev = deviceByUid.get(hit.binding.deviceUid);
    if (!dev) {
      // 10.1: the position is observed fresh in each scan, and this photograph
      // does not show that box. The recollection is a note and a tie-breaker, not
      // a veto: scoring still runs, where before this the switch went permanently
      // blank and the box it used to match lost its model, serial and cables.
      addNote(f.id, `it was confirmed as a box this photograph does not show`
        + `${hit.binding.position != null ? ` (U${hit.binding.position})` : ''}`);
      if (hit.binding.position != null) {
        recalledAt.set(f.id, { position: Number(hit.binding.position), at: hit.binding.at });
      }
      continue;
    }

    const clash = contradiction(hit.binding.boxPrint, printOf(dev));
    if (clash) {
      // 10.2 a move, 10.3 a replacement. Either way the photograph disagrees with
      // the confirmation, and binding to it anyway would write one switch's facts
      // onto another switch's shelf.
      reasons[f.id] = blank(
        `${clash}. Confirm the box again before anything is written`,
        { candidateCount: 0, notes: notes.get(f.id) || [] },
      );
      continue;
    }

    if (claimedBy.has(dev.uid)) {
      reasons[f.id] = blank(
        'this switch is listed twice, and the other one is already confirmed as this box. '
        + 'Remove the duplicate',
        { candidateCount: 0, notes: notes.get(f.id) || [] },
      );
      continue;
    }
    claimedBy.set(dev.uid, f.id);

    // Fresh or recalled, and it decides everything downstream.
    //
    // A confirmation made against THIS photograph is the person's own
    // observation of what is on the screen: rank 1, confirmed, written.
    // A confirmation made against an earlier photograph is a recollection:
    // rank 4, probable, shown and never written. Standard 10.1 is explicit that
    // an earlier position must not be presented as current, and it is right -
    // two identical switches swapped between two shelves produce exactly this
    // situation, and nothing in a photograph of identical boxes can tell.
    const firsthand = (Array.isArray(hit.binding.evidence) ? hit.binding.evidence : [])
      .filter((e) => e && Number(e.rank) >= 1 && Number(e.rank) <= 3);
    const fresh = Boolean(scanId && hit.binding.scanId && String(hit.binding.scanId) === scanId
      && hit.binding.snapshotStamp && hit.binding.snapshotStamp === stamp
      && firsthand.length);
    const ev = fresh ? firsthand : [identity.evidence('remembered',
      `confirmed as this box on ${hit.binding.at}, and this photograph does not contradict it`,
      { by: hit.binding.by || null, at: hit.binding.at || null })];
    proposals.set(f.id, {
      devUid: dev.uid,
      reason: {
        deviceUid: dev.uid,
        confidence: identity.confidenceOf(ev),
        why: fresh
          ? `confirmed at the rack as ${dev.name}`
          : `confirmed as ${dev.name} on ${String(hit.binding.at || '').slice(0, 10)}. `
            + "Confirm it again to write the switch's model, serial and ports onto the box",
        evidence: ev,
        candidateCount: 1,
        margin: null,
        fromBinding: true,
        fresh,
        notes: notes.get(f.id) || [],
      },
    });
  }

  /* ── 2. the record ─────────────────────────────────────────────────────────
     The customer's own record for this rack holds both halves of the answer:
     which serial and which management address sit on which shelf. A switch
     that answers with that serial, or at that address, IS the device on that
     shelf - and the photograph drew a box there. That is rank 3, `modelled`:
     below a person standing at the rack, above every score in section 3.

     Given only when the caller has fetched the record for the rack this scan
     was bound to (opts.records). Without it this rung does nothing and the
     scoring below runs exactly as it always did. */
  const fromRecord = switchRecord.placeByRecord({
    devices,
    facts: [...facts.values()].filter((f) => f.read).map((f) => ({
      id: f.id, label: f.label, serial: f.serial, host: f.host, sysName: f.sysName,
    })),
    records: opts.records || [],
    taken: new Set(claimedBy.keys()),
  });
  for (const [id, line] of fromRecord.notes) for (const l of line) addNote(id, l);
  for (const [id, proposal] of fromRecord.placed) {
    if (proposals.has(id) || reasons[id]) continue;   // a person outranks the record
    claimedBy.set(proposal.deviceUid, id);
    proposals.set(id, {
      devUid: proposal.deviceUid,
      reason: { ...proposal, notes: notes.get(id) || [] },
    });
  }

  // ── 3. scoring ─────────────────────────────────────────────────────────────
  for (const sw of order) {
    const f = facts.get(sw.record.id);
    if (proposals.has(f.id) || reasons[f.id]) continue;
    if (!f.read) {
      reasons[f.id] = blank('it has not been read yet. Read it, then place it',
        { candidateCount: 0, notes: notes.get(f.id) || [] });
      continue;
    }

    // The one physical impossibility worth refusing on, and it is about the RACK,
    // not about any one box: a stack of N separate chassis needs N boxes to sit
    // in, and the camera draws one box per chassis. Demanding a single box N
    // shelves tall - which is what this used to do - meant a real stack matched
    // nothing in a real rack, ever. Only a corroborated member count counts.
    const free = devices.filter((d) => !claimedBy.has(d.uid));
    if (f.members > 1 && free.length < f.members) {
      reasons[f.id] = blank(
        `it is a stack of ${f.members} units and only ${free.length} `
        + `box${free.length === 1 ? '' : 'es'} here could hold one. `
        + 'Scan the whole rack, or place it by hand',
        { candidateCount: free.length, notes: notes.get(f.id) || [] },
      );
      continue;
    }

    const candidates = [];
    for (const dev of free) {
      const { score, why, shape, stack, ruledOut } = scorePair(f, dev);
      // A box struck off for its shape is not announced at all. Saying so per box
      // put four paragraphs of arithmetic on a screen whose job is to show one
      // switch and one box, and one of those paragraphs named the very box the
      // switch had been matched to. Counting them instead only proved the matcher
      // had been busy; what a person needs is the one line below.
      if (ruledOut) continue;
      if (score < FLOOR) continue;
      candidates.push({ devUid: dev.uid, name: dev.name, score, why, shape, stack,
        position: dev.position,
        // Carried so the cable comparison can run on a tie without going
        // back to the snapshot, which by then may no longer hold them.
        sockets: dev.sockets || [],
        // Held, not announced: only the box actually proposed is worth a note.
        advice: sizeAdvice(dev.units, dev.cvClass) });
    }
    candidates.sort((a, b) => b.score - a.score || String(a.devUid).localeCompare(String(b.devUid)));

    // One note, about the box a person is being shown - not one per box that was
    // considered. Anything about the alternatives is the matcher's working.
    if (candidates.length && candidates[0].stack > 1) {
      addNote(f.id, `this box holds one unit of a stack of ${candidates[0].stack}`);
    }
    if (candidates.length && candidates[0].advice) addNote(f.id, candidates[0].advice);

    if (f.chassisRows > 1 && f.members === 1) {
      addNote(f.id, `it reports ${f.chassisRows} units but gives only one serial, `
        + 'so it is placed as one box');
    }

    if (!candidates.length) {
      reasons[f.id] = blank('no box in this photo has the right number of ports. '
        + 'Pick the box by hand, or scan the rack again',
        { candidateCount: 0, notes: notes.get(f.id) || [] });
      continue;
    }

    const top = candidates[0];
    const second = candidates[1] || null;
    const margin = second ? top.score - second.score : null;
    // Judged on the same evidence means told apart by nothing. Two boxes both at
    // an exact port count are a tie; an exact count against a two-port miss is
    // not, however close the two scores happen to land.
    const tied = candidates.filter((c) => c.shape === top.shape);

    // A shelf this switch was confirmed at before, when the box that was there is
    // gone from this photograph, is rank 4 memory: it settles a tie honestly and
    // it is still never written. Standard 10.1 keeps the old position as a source
    // and forbids presenting it as current, which is exactly this.
    const memory = recalledAt.get(f.id) || null;
    const remembered = memory
      ? tied.filter((c) => c.position != null && Number(c.position) === memory.position)
      : [];

    // The cables, rung 6. Everything above has tied: same model, same make,
    // same port count, and no confirmation to remember. Two switches of one
    // model rarely carry the same pattern of cables, and the camera saw which
    // sockets on each box hold one. This is the only rung that can separate
    // two identical unlabelled switches, and it is why it exists.
    //
    // It settles WHICH box. It does not raise how sure we are: the evidence is
    // still inference from behaviour, so the box is proposed and a person still
    // confirms before anything of the switch's is written onto it.
    const byCable = tied.length > 1 && remembered.length !== 1
      ? fingerprint.rankBoxes(sw.reading, tied.map((c) => ({ uid: c.devUid, name: c.name, sockets: c.sockets })))
      : null;
    const cabled = byCable && byCable.settled
      ? tied.find((c) => c.devUid === byCable.best.uid)
      : null;

    if (tied.length > 1 && remembered.length !== 1 && !cabled) {
      const names = tied.map((c) => c.name).join(' and ');
      reasons[f.id] = blank(
        `${names} cannot be told apart. Read the serial off one, or confirm the box at the rack`,
        {
          candidateCount: candidates.length,
          margin,
          notes: [...(notes.get(f.id) || []), ...(byCable ? [byCable.why] : [])],
        },
      );
      continue;
    }

    const pick = cabled || (remembered.length === 1 ? remembered[0] : top);
    // Rank 8, and the count that matters to standard 6.2 is how many boxes this
    // evidence could not tell apart - which the tie test has just made one.
    const ev = [identity.evidence('inferred',
      cabled ? `${pick.why}, and ${byCable.why}` : pick.why,
      { candidateCount: cabled ? 1 : tied.length })];
    if (memory && pick.position != null && Number(pick.position) === memory.position) {
      ev.push(identity.evidence('remembered',
        `this switch was confirmed at U${memory.position} on ${String(memory.at || '').slice(0, 10)}`,
        { at: memory.at || null }));
    }
    proposals.set(f.id, {
      devUid: pick.devUid,
      reason: {
        deviceUid: pick.devUid,
        confidence: identity.confidenceOf(ev),
        // When the cables settled it, say so and say only that: it is the whole
        // reason this box was chosen over another that agreed on everything else,
        // and the agreement itself told the two apart not at all.
        why: cabled
          ? 'the cables on this box match the switch'
          : (candidates.length === 1
            ? `${pick.why}, and it is the only box it could be`
            : pick.why),
        evidence: ev,
        candidateCount: cabled ? 1 : candidates.length,
        margin: cabled ? byCable.margin : margin,
        fromBinding: false,
        fresh: false,
        notes: notes.get(f.id) || [],
      },
    });
  }

  // ── 3. one box, one switch ─────────────────────────────────────────────────
  const claimants = new Map();
  for (const [swId, p] of proposals) {
    const a = claimants.get(p.devUid) || [];
    a.push(swId);
    claimants.set(p.devUid, a);
  }
  for (const ids of claimants.values()) {
    if (ids.length === 1) continue;
    // A binding cannot be in here - a bound box is never offered to scoring -
    // but if one ever were, the person's answer is the one that stands.
    const keep = ids.find((x) => proposals.get(x).reason.fromBinding) ?? null;
    for (const swId of ids) {
      if (swId === keep) continue;
      const others = ids.filter((x) => x !== swId).map((x) => facts.get(x).label || x);
      proposals.delete(swId);
      reasons[swId] = blank(
        `${others.join(' and ')} could be this box too. Confirm each switch at the rack`,
        { candidateCount: 1, notes: notes.get(swId) || [] },
      );
    }
  }

  for (const [swId, p] of proposals) {
    matches[swId] = p.devUid;
    reasons[swId] = p.reason;
  }
  // A switch that never reached scoring (no reading, no candidates) still carries
  // whatever pass 1 wanted to tell the person.
  for (const [swId, lines] of notes) {
    if (reasons[swId] && !reasons[swId].notes.length) reasons[swId].notes = lines;
  }
  return { matches, reasons };
}

/**
 * A reason for proposing nothing.
 *
 * Blank is a real answer here, not a failure (standard 7.3), so it carries the
 * same fields a match does and says what would settle it.
 */
function blank(why, { candidateCount = 0, margin = null, notes = [] } = {}) {
  return {
    deviceUid: null,
    confidence: 'unidentified',
    why,
    evidence: [],
    candidateCount,
    margin,
    fromBinding: false,
    fresh: false,
    notes: [...notes],
  };
}

/**
 * The confidence each posted match actually carries.
 *
 * A match is only as good as the reason that produced it, and a hand placement
 * that disagrees with the reason is evidence of nothing: somebody moved a
 * dropdown, which is worth storing and showing and is not worth writing into a
 * customer's database. So a posted uid that is not the one the matcher reasoned
 * about is 'unidentified' here, whatever the matcher concluded about its own.
 */
function levelsFor(reasons, matches) {
  const out = {};
  for (const [swId, uid] of Object.entries(matches || {})) {
    const r = reasons ? reasons[swId] : null;
    out[swId] = uid && r && r.deviceUid === uid ? r.confidence : 'unidentified';
  }
  return out;
}

/**
 * Apply matches to the camera snapshot and return the merged result.
 *
 * `matches` maps a switch id to a device uid (or null for "not in this rack").
 *
 * `opts.levels` is the confidence of each match, from levelsFor. It gates the
 * ENRICHMENT only - the model, the serial, the device type, the real ports, the
 * cables - because those are facts about hardware, and writing a guessed serial
 * into a customer's DCIM converts our uncertainty into their fact (standard 7.2,
 * and identity.writable is the single place that decides). The device row itself,
 * its name and its shelf are written exactly as before, so the photo-to-record
 * link that works today is untouched whatever the binding level says.
 *
 * Required, and it throws without it: a default would either write everything
 * (the bug) or write nothing (a silent loss), and neither is something a caller
 * should get by forgetting an argument.
 */
function reconcile(base, sws, matches, opts = {}) {
  if (!opts || !opts.levels) {
    throw new Error('reconcile.reconcile needs opts.levels: the confidence of each match, from '
      + 'reconcile.levelsFor(reasons, matches). Only a confirmed match may write hardware facts.');
  }
  const levels = opts.levels;
  const snap = clone(base);
  if (!snap.cables) snap.cables = [];
  const changes = [];
  const withheld = [];

  const deviceByUid = new Map(snap.devices.map((d) => [d.uid, d]));
  const typeByUid = new Map(snap.deviceTypes.map((t) => [t.uid, t]));
  const mfrUids = new Set(snap.manufacturers.map((m) => m.uid));
  const byId = new Map(sws.map((s) => [s.record.id, s]));

  const ensureManufacturer = (name) => {
    if (!name) return null;
    const uid = `mfr:${slug(name)}`;
    if (!mfrUids.has(uid)) {
      snap.manufacturers.push(Manufacturer(observed(uid, Evidence.SNMP),
        { name, slug: slug(name) }));
      mfrUids.add(uid);
    }
    return uid;
  };

  // ── enrich each matched device from its switch ────────────────────────────
  const written = new Set();
  for (const sw of sws) {
    const devUid = matches[sw.record.id];
    if (!devUid || !sw.reading) continue;
    const dev = deviceByUid.get(devUid);
    if (!dev) continue;

    // The gate. Everything below this line is a hardware fact.
    const level = levels[sw.record.id] || 'unidentified';
    if (!identity.writable(level)) {
      withheld.push({
        switchId: sw.record.id,
        switch: sw.record.label || `switch ${sw.record.id}`,
        device: dev.name,
        confidence: level,
        why: 'not written yet: confirm the box at the rack to write the model, '
          + 'serial and ports',
      });
      continue;
    }
    written.add(devUid);

    const r = sw.reading;
    const ident = r.identity || {};
    const vendor = ident.manufacturer || r.system?.vendor || null;
    const mfrUid = ensureManufacturer(vendor);

    if (ident.model) {
      const typeUid = `dtype:${slug(ident.model)}`;
      if (!typeByUid.has(typeUid)) {
        const t = DeviceType(observed(typeUid, Evidence.SNMP), {
          manufacturerUid: mfrUid || typeByUid.get(dev.deviceTypeUid)?.manufacturerUid || '',
          model: ident.model, slug: slug(ident.model), uHeight: 1,
        });
        snap.deviceTypes.push(t);
        typeByUid.set(typeUid, t);
      }
      dev.deviceTypeUid = typeUid;
      dev.evidence = Evidence.SNMP;
      changes.push({ device: dev.name, field: 'model', was: null, now: ident.model });
    } else if (mfrUid) {
      // No ENTITY model (e.g. this D-Link), but we at least know the make.
      const t = typeByUid.get(dev.deviceTypeUid);
      if (t) t.manufacturerUid = mfrUid;
      changes.push({ device: dev.name, field: 'manufacturer', was: null, now: vendor });
    }

    if (ident.serial) {
      dev.serial = ident.serial;
      dev.evidence = Evidence.SNMP;
      changes.push({ device: dev.name, field: 'serial', was: null, now: ident.serial });
    }

    dev.customFields = { ...(dev.customFields || {}), managementIp: sw.record.host };
    if (ident.firmwareRev) dev.customFields.firmware = ident.firmwareRev;
    dev.provenance = {
      ...(dev.provenance || {}),
      switchId: sw.record.id, snmpHost: sw.record.host,
      sysName: r.system?.sysName || null, sysDescr: r.system?.sysDescr || null,
      stackMembers: ident.stackMembers || 1,
    };

    // Real ports replace the camera's port guesses entirely.
    snap.interfaces = snap.interfaces.filter((i) => i.deviceUid !== devUid);
    // A MAC several ports report is the switch's own chassis address, not any
    // one port's: the TP-Links give all 28 ports the same one. Writing it on
    // every port would be wrong in the record and 28 "changes" in the plan.
    const macCount = new Map();
    for (const i of r.interfaces || []) if (i.mac) macCount.set(i.mac, (macCount.get(i.mac) || 0) + 1);
    for (const i of r.interfaces || []) {
      snap.interfaces.push(Interface(
        observed(`if:${devUid}:${i.ifIndex}`, Evidence.SNMP,
          { ifIndex: i.ifIndex, oper: i.operStatus, speedMbps: i.speedMbps }),
        {
          deviceUid: devUid, name: i.name, type: ifTypeFor(i),
          description: i.alias || '', mac: i.mac && macCount.get(i.mac) === 1 ? i.mac : null,
          enabled: i.adminStatus !== 'down', label: null,
        }));
    }
    changes.push({ device: dev.name, field: 'ports', was: null,
      now: `${(r.interfaces || []).length} ports` });
  }

  // ── cables from LLDP, only between switches we can place ───────────────────
  const byChassis = new Map();
  const bySysName = new Map();
  for (const s of sws) {
    if (!s.reading) continue;
    if (s.reading.localChassisId) byChassis.set(normId(s.reading.localChassisId), s);
    const nm = s.reading.system?.sysName;
    if (nm) { const a = bySysName.get(nm) || []; a.push(s); bySysName.set(nm, a); }
  }
  const remoteSwitchOf = (n) => {
    if (n.chassisId) {
      const hit = byChassis.get(normId(n.chassisId));
      if (hit) return hit;
    }
    if (n.remoteSysName) {
      const a = bySysName.get(n.remoteSysName);
      if (a && a.length === 1) return a[0]; // unique name only; no guessing
    }
    return null;
  };
  const ifUid = (devUid, name) => {
    if (!name) return null;
    const hit = snap.interfaces.find((i) => i.deviceUid === devUid && i.name === name);
    return hit ? hit.uid : null;
  };

  const cables = [];
  const seen = new Set();
  const unresolved = [];

  for (const sw of sws) {
    const devUid = matches[sw.record.id];
    if (!devUid || !sw.reading || !written.has(devUid)) continue;

    for (const n of sw.reading.neighbours || []) {
      const remote = remoteSwitchOf(n);
      if (!remote || remote.record.id === sw.record.id) continue;
      const remoteDevUid = matches[remote.record.id];
      if (!remoteDevUid) {
        unresolved.push({ from: sw.record.label, seen: n.remoteSysName || null,
          why: 'the switch at the other end has no box yet' });
        continue;
      }
      // A cable is a statement that two named boxes are joined. Drawing one to a
      // box nobody confirmed asserts the far end as a fact, so it waits too.
      if (!written.has(remoteDevUid)) {
        unresolved.push({ from: sw.record.label, seen: n.remoteSysName || null,
          why: 'the box at the far end has not been confirmed at the rack yet' });
        continue;
      }

      const localIf = ifUid(devUid, n.localPortName) || ifUid(devUid, `port ${n.localPort}`);
      const remoteName = n.remotePortDesc || n.remotePortId;
      const remoteIf = ifUid(remoteDevUid, remoteName);
      if (!localIf || !remoteIf) {
        unresolved.push({ from: sw.record.label, seen: n.remoteSysName || remoteName,
          why: 'the port it named is not one of the ports we read' });
        continue;
      }

      const key = [localIf, remoteIf].sort().join('::');
      if (seen.has(key)) continue;
      seen.add(key);

      const mutual = (remote.reading.neighbours || []).some((rn) => {
        const back = remoteSwitchOf(rn);
        return back && back.record.id === sw.record.id;
      });

      const cable = Cable(
        observed(`cable:${slug(key)}`, mutual ? Evidence.LLDP_BOTH : Evidence.LLDP_ONE,
          { via: 'lldp', localPort: n.localPortName, remotePort: remoteName }),
        { a: Termination('dcim.interface', localIf), b: Termination('dcim.interface', remoteIf),
          type: 'cat6' });
      snap.cables.push(cable);
      cables.push({
        from: `${deviceByUid.get(devUid).name} · ${n.localPortName}`,
        to: `${deviceByUid.get(remoteDevUid).name} · ${remoteName}`,
        evidence: cable.evidence,
      });
    }
  }

  const summary = {
    switchesTotal: sws.length,
    matched: Object.values(matches).filter(Boolean).length,
    // What was matched on the screen but kept out of the record, and why. This is
    // the honest half of the answer: the join is shown, the facts wait for a
    // person to confirm the box.
    written: written.size,
    withheld,
    unmatched: sws.filter((s) => !matches[s.record.id]).length,
    serials: changes.filter((c) => c.field === 'serial').length,
    models: changes.filter((c) => c.field === 'model').length,
    cables: cables.length,
    cablesProven: cables.filter((c) => c.evidence === Evidence.LLDP_BOTH).length,
    changes,
    cableList: cables,
    unresolved,
  };
  return { snapshot: snap, summary };
}

/**
 * The whole picture the Review screen needs: the camera's devices, each
 * switch's headline facts, and the current or suggested matching.
 */
/**
 * Where the rack's photo can be fetched from, or null when none was kept.
 *
 * Adoption stores the absolute path of whatever file was on disk; the served
 * route is /outputs/<rackId>/<file>, so take the name and rebuild the path.
 */
function rackImageUrl(base, rackId) {
  const stored = (base.racks || [])[0]?.provenance?.image || '';
  const file = String(stored).split(/[\\/]/).pop();
  if (!file) return null;
  return `/outputs/${encodeURIComponent(rackId)}/${encodeURIComponent(file)}`;
}

function view(base, rackId, storedMatches, opts = {}) {
  const sws = gatherSwitches(rackId);
  const auto = suggest(base, sws, opts);   // opts.records, when the caller has them
  const matches = { ...(storedMatches || auto.matches) };
  // A person standing at the rack outranks a saved matching, always. Without
  // this the two stores drift apart: a plain save could point a switch at one box
  // while the binding named another, the screen showed one and the export wrote
  // the other, and the next re-detect silently swapped them back.
  for (const [swId, r] of Object.entries(auto.reasons)) {
    if (r && r.fromBinding && r.fresh && r.deviceUid) matches[swId] = r.deviceUid;
  }
  const levels = levelsFor(auto.reasons, matches);
  const { summary } = reconcile(base, sws, matches, { levels });
  return {
    // The photo the camera read, as a URL this rack's owner may fetch. The
    // snapshot carries wherever the file sat on disk when it was adopted;
    // only its name survives into a path the client can ask for.
    image: rackImageUrl(base, rackId),
    devices: cameraDevices(base),
    switches: sws.map((s) => ({
      id: s.record.id,
      label: s.record.label,
      host: s.record.host,
      read: Boolean(s.reading),
      model: s.reading?.identity?.model || null,
      serial: s.reading?.identity?.serial || null,
      vendor: s.reading?.system?.vendor || null,
      sysName: s.reading?.system?.sysName || null,
      // The maker's own MIB gives these where the standard one is silent —
      // and the Switches tab wants them, because a firmware version read off
      // the box beats one read off a photograph of the box.
      hardware: s.reading?.identity?.hardwareRev || null,
      firmware: s.reading?.identity?.firmwareRev || null,
      uptimeSeconds: s.reading?.system?.uptimeSeconds ?? null,
      ports: portsOf(s),
      neighbours: s.reading?.counts?.neighbours ?? 0,
      matchedTo: matches[s.record.id] || null,
      // Why the auto-matcher proposed this, for the user to verify against.
      autoMatch: auto.reasons[s.record.id] || null,
      // Whether what is on the screen for this switch is written into the record,
      // so a screen never has to guess from the confidence word.
      written: identity.writable(levels[s.record.id] || 'unidentified'),
    })),
    matches,
    reasons: auto.reasons,
    levels,
    summary,
    suggested: !storedMatches,
  };
}

// slug is shared with the writer, which has to undo it to find the hash-based
// uid a keyed cable was written under: the two must agree on one rule.
// PASSIVE_CLASS is shared with the reconcile route, which has to refuse a posted
// match onto a patch panel or a PDU: one list, in one place.
module.exports = {
  gatherSwitches, cameraDevices, suggest, reconcile, view, slug,
  /* The pairing score, exported for the standalone mapping demonstrator
     (routes/mapping.js) so it shows the product's own rule rather than a
     second implementation of it: model, make and port count, with the shape
     of a box able to rule a switch out entirely. */
  scorePair,
  // The record rung, exported so a route can read the rack's rows once and
  // hand them in, and so it can be tested on its own.
  placeByRecord: switchRecord.placeByRecord, recordsFrom: switchRecord.recordsFrom,
  levelsFor, snapshotStamp, printOf, isPassive, PASSIVE_CLASS,
  spanByUid,
};
