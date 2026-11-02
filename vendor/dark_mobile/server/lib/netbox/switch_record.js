/**
 * Put a switch on the shelf the customer's own record already puts it on.
 *
 * Both halves of the answer exist and were never joined. The photograph knows
 * WHERE every box sits and guesses at what it is. A managed switch, asked over
 * SNMP, states exactly what it is - its serial, the address it answered on -
 * and knows nothing about where it sits. And in the middle sits the customer's
 * record, which holds both: NetBox says "this serial, this management address,
 * on U20 of this rack".
 *
 * So once the rack is identified (lib/rack_identity), the record for that rack
 * is the bridge:
 *
 *     switch answered at 10.10.1.21, serial FX2938401
 *         |  the record holds that serial on U20
 *         v
 *     U20  ->  the box the photograph drew at U20
 *
 * That is the owner's sentence on 23 September 2026: "when you found the
 * database rack of your physical rack you will know every unit position device
 * and correspondingly it will have ip, serial numbers and all, and you have the
 * same info in the logical layer network switches - using both, physical unit
 * rack to the network rack switch."
 *
 * The rule this module holds to is the one the rest of the identity code holds
 * to: never name the wrong one. So only two things place a switch, and both are
 * identities rather than resemblances -
 *
 *   serial   the switch's own serial equals the serial on the record
 *   address  the switch answered at the address the record calls its own
 *
 * A name agreement does not place anything. Two switches in one rack are
 * routinely called the same thing with a digit changed, and a name is a label
 * somebody typed; it is carried out as a note so a person can see it, and it
 * decides nothing.
 *
 * What comes back is rank 3 evidence, `modelled` - "the system of record says
 * so" - which is below a person confirming the box in front of them (rank 1)
 * and above every score built out of port counts and model strings (rank 8).
 * It proposes; the person at the rack still outranks it.
 *
 * Nothing here talks to NetBox or to a switch. It is given both sides and
 * joins them, so it can be tested without either.
 */
const identity = require('./identity');

/** Values a comparison must never treat as an identity. */
const clean = (v) => {
  const s = identity.normalise(v);
  return s && !identity.isJunkValue(s) ? s : '';
};

/** An address, in one shape. IPv4 with a mask or a port on it still compares. */
const addr = (v) => String(v == null ? '' : v)
  .trim()
  .replace(/\/\d+$/, '')       // 10.10.1.21/24
  .replace(/:\d+$/, '')        // 10.10.1.21:161
  .toLowerCase();

/** The shelf a record puts something on, as a number or null. */
const shelfOf = (r) => {
  const n = Number(r && r.position);
  return Number.isFinite(n) && n > 0 ? n : null;
};

/**
 * The box the photograph drew at this shelf.
 *
 * A device two units tall is drawn once, at its lowest unit, and the record
 * puts it at that same lowest unit - so an exact match is the common case. A
 * box whose span covers the shelf is accepted too, for the rack where the
 * record counts from the other end of a 2U device.
 */
function boxAtShelf(devices, shelf) {
  if (shelf === null) return null;
  const exact = devices.filter((d) => Number(d.position) === shelf);
  if (exact.length === 1) return exact[0];
  if (exact.length > 1) return null;           // two boxes on one shelf: say nothing
  const covering = devices.filter((d) => {
    const at = Number(d.position);
    const tall = Math.max(1, Number(d.units) || 1);
    return Number.isFinite(at) && shelf >= at && shelf < at + tall;
  });
  return covering.length === 1 ? covering[0] : null;
}

/**
 * Which record is this switch, if any.
 *
 * Returns { record, how, why } or null. `how` is 'serial' or 'address', and
 * both are identities: a serial is minted by the maker and an address is
 * unique on the network the record describes.
 */
function recordFor(fact, records) {
  const swSerial = clean(fact.serial);
  if (swSerial) {
    const hits = records.filter((r) => clean(r.serial) === swSerial);
    if (hits.length === 1) {
      return {
        record: hits[0],
        how: 'serial',
        why: `the record holds serial ${fact.serial} on U${shelfOf(hits[0])}`,
      };
    }
    if (hits.length > 1) return null;   // the record itself cannot tell them apart
  }

  const swHost = addr(fact.host);
  if (swHost) {
    const hits = records.filter((r) => addr(r.primaryIp) === swHost);
    if (hits.length === 1) {
      return {
        record: hits[0],
        how: 'address',
        why: `the record gives ${fact.host} to ${hits[0].name || 'this device'} on U${shelfOf(hits[0])}`,
      };
    }
    if (hits.length > 1) return null;
  }
  return null;
}

/**
 * Place every switch the record can place.
 *
 * `records` are the rack's devices as the customer's record holds them:
 * { id, name, serial, primaryIp, position }. `facts` is one entry per switch:
 * { id, label, serial, host, sysName }. `devices` are the camera's boxes:
 * { uid, name, position, units, passive }.
 *
 * `taken` is the set of box uids a stronger rung has already claimed - a
 * person's own confirmation - and is never overruled here.
 *
 * Returns { placed: Map(swId -> proposal), notes: Map(swId -> [line]) }.
 */
function placeByRecord({ devices = [], facts = [], records = [], taken = new Set() } = {}) {
  const placed = new Map();
  const notes = new Map();
  const note = (id, line) => {
    const held = notes.get(id) || [];
    held.push(line);
    notes.set(id, held);
  };
  if (!records.length || !facts.length) return { placed, notes };

  const usable = devices.filter((d) => !d.passive);
  const claimed = new Set(taken);

  for (const fact of facts) {
    const hit = recordFor(fact, records);
    if (!hit) {
      // A name agreement is worth saying out loud and worth nothing else.
      const sys = clean(fact.sysName);
      if (sys) {
        const named = records.filter((r) => clean(r.name) === sys);
        if (named.length === 1 && shelfOf(named[0]) !== null) {
          note(fact.id, `the record has a device of this name on U${shelfOf(named[0])}, `
            + 'but a name is not an identity. Read its serial, or confirm the box');
        }
      }
      continue;
    }

    const shelf = shelfOf(hit.record);
    if (shelf === null) {
      note(fact.id, `the record holds this switch (${hit.how}) but gives it no shelf`);
      continue;
    }

    const box = boxAtShelf(usable, shelf);
    if (!box) {
      note(fact.id, `the record puts this switch on U${shelf}, and this photograph `
        + 'shows no box there. Photograph the rack again, or confirm the box by hand');
      continue;
    }
    if (claimed.has(box.uid)) {
      note(fact.id, `the record puts this switch on U${shelf}, where another switch `
        + 'is already confirmed');
      continue;
    }
    claimed.add(box.uid);

    const ev = [identity.evidence('modelled', hit.why, {
      by: 'the record', netboxId: hit.record.id ?? null, position: shelf, how: hit.how,
    })];
    placed.set(fact.id, {
      deviceUid: box.uid,
      confidence: identity.confidenceOf(ev),
      why: hit.how === 'serial'
        ? `the record holds this serial on U${shelf}`
        : `the record gives this address to the device on U${shelf}`,
      evidence: ev,
      candidateCount: 1,
      margin: null,
      fromRecord: true,
      recordName: hit.record.name || null,
      position: shelf,
    });
  }

  return { placed, notes };
}

/**
 * The rack's devices, from what NetBox answered, in the shape above.
 *
 * Kept here so one spelling of the record's fields is read in one place: the
 * API gives the address as an object on `primary_ip`, and its `address` field
 * carries the mask.
 */
function recordsFrom(rows = []) {
  return (Array.isArray(rows) ? rows : []).map((r) => ({
    id: r.id ?? null,
    name: r.name || null,
    serial: r.serial || null,
    primaryIp: (r.primary_ip && (r.primary_ip.address || r.primary_ip.display))
      || (r.primary_ip4 && (r.primary_ip4.address || r.primary_ip4.display))
      || r.primaryIp || null,
    position: r.position ?? null,
    /* What the record says the row IS. Never an identity - two identical
       switches in one rack carry one model between them - but it narrows the
       candidates, and a reader deserves to see it. */
    model: (r.device_type && (r.device_type.model || r.device_type.display)) || r.model || null,
    /* How many shelves the row takes up. A 2U patch panel on U10 stands on
       U10 and U11, and a comparison that only looks at U10 reports the other
       half as something the record does not know about. */
    height: Number((r.device_type && r.device_type.u_height) || r.u_height || 1) || 1,
    make: (r.device_type && r.device_type.manufacturer
      && (r.device_type.manufacturer.name || r.device_type.manufacturer.display)) || r.make || null,
  })).filter((r) => r.id !== null || r.name);
}

module.exports = { placeByRecord, recordsFrom, boxAtShelf, recordFor, _internal: { clean, addr } };
