/**
 * Who a device is, taken from what its own hardware published.
 *
 * The mistake this module exists to prevent: keying a device on ONE field. Our
 * own office proved why. The phone read a TP-Link and got a serial from the
 * maker's private tree; the server read the same switch over LLDP and got a
 * chassis address instead. Key on "the best field available" and one switch
 * becomes two devices, because the two readings never agree on which field is
 * best. Key on the SET of everything each reading published and they overlap,
 * so one switch stays one switch.
 *
 * So an identity here is a set of aliases, each written as "kind:value":
 *
 *   serial   a serial number, from the physical inventory or the maker's tree
 *   chassis  the chassis address the device publishes over LLDP
 *   bridge   the bridge base address it publishes as a switch
 *   mac      the hardware address of its management interface
 *   sysname  the name it calls itself - NOT an identity, standard 4.4
 *   host     the management address we reached it on - provisional, 4.3
 *
 * Two identities are the same device when they share a STRONG alias: serial,
 * chassis, bridge or mac. A shared name or a shared management address is never
 * enough, because both are configuration and both get reused.
 *
 * Pure: no files, no network, no clock. Everything here is a function of its
 * arguments, so it can be reasoned about and tested exhaustively.
 *
 * The rules are docs/design/rack-binding-standard.md. Section numbers are cited
 * where a rule comes straight from it.
 */

// ── the identity ladder, standard 4.1 ───────────────────────────────────────
//
// The rung each kind of alias sits on. Lower is stronger. Rungs 1 and 2 are
// both serial numbers (the physical inventory, then the maker's private tree)
// and the reading does not distinguish them, so both are 'serial' at rung 1.
// A published name is on no rung at all: 4.4 forbids it as an identity even
// when it looks like a good one.
const LADDER = Object.freeze({
  serial: 1,   // 4.1 rungs 1 and 2 - the factory's own number
  chassis: 3,  // 4.1 rung 3 - the chassis address published over LLDP
  bridge: 4,   // 4.1 rung 4 - the bridge base address
  mac: 5,      // 4.1 rung 5 - the management interface's hardware address
  host: 6,     // 4.1 rung 6 - the management address, provisional only
});

/** Every alias kind this module will mint, strongest first. */
const KINDS = Object.freeze(['serial', 'chassis', 'bridge', 'mac', 'sysname', 'host']);

/**
 * The three kinds that are all the same 48-bit address on almost every switch.
 *
 * A switch has one base address and publishes it as its LLDP chassis id, as its
 * bridge base address and as its management interface's hardware address,
 * depending on which table you asked. Compared as "kind:value" strings those are
 * three different aliases, so one switch read two ways read as two devices -
 * which is the exact mistake this module exists to prevent. So a value that is
 * plainly a MAC is compared across these three kinds by value, and the rung
 * reported is the WEAKER of the two, because a shared address proves no more
 * than the weaker of the two claims about it did.
 */
const HW_KINDS = Object.freeze(new Set(['chassis', 'bridge', 'mac']));

/** Twelve hex digits and nothing else: a 48-bit address, however it was spelled. */
const MAC_FORM = /^[0-9a-f]{12}$/;

/**
 * LLDP subtype 4 is macAddress. It is the only subtype that makes a chassis id
 * an address, and 4.1 rung 3 is an address. A vendor that publishes subtype
 * local(7) or chassisComponent(1) is publishing a model string, a stack name or
 * a hostname, which two physically different units share - so it is not an
 * identity, and minting one from it merges two switches into one.
 */
const LLDP_SUBTYPE_MAC = 4;

/**
 * The kinds that prove identity on their own.
 *
 * 'host' is on the ladder but excluded on purpose: 4.3 says a management
 * address is provisional and must never be written to a system of record as an
 * identity. 'sysname' is excluded by 4.4.
 */
const STRONG = Object.freeze(new Set(['serial', 'chassis', 'bridge', 'mac']));

// ── evidence ranks, standard section 6 ──────────────────────────────────────
//
// All eight, kept whole so the vocabulary does not have to be invented again
// when the later ones get a producer. This version can only mint four of them,
// and evidence() refuses the rest rather than letting a rank be claimed by a
// code path that cannot honestly support it.
const EVIDENCE_RANK = Object.freeze({
  confirmed: 1,   // a person on site placed this device at this shelf
  reported: 2,    // the hardware states its own rack and shelf
  modelled: 3,    // the system of record says so
  remembered: 4,  // bound here before by rank 1 to 3, uncontradicted
  read: 5,        // a serial or asset tag read off the box matches an identity
  ordered: 6,     // stack or slot position aligned to the visual order of boxes
  adjacent: 7,    // a neighbour or stack peer of a device already bound here
  inferred: 8,    // port count, size or model agreement, nothing else
});

/**
 * Ranks no code path in this version may produce.
 *
 * reported  needs the device to publish its own rack and shelf. Nothing we read
 *           over SNMP today carries it.
 * read      needs a serial or asset tag recognised in the photograph. The OCR
 *           pass reads make and model, not serials.
 * ordered   needs the physical inventory's position-within-parent, which none of
 *           the hardware measured so far publishes.
 * adjacent  needs a device already bound in this rack to reason outwards from.
 *
 * 'modelled' has no producer here either - it would come from NetBox, and this
 * slice never asks NetBox anything - but it is not blocked, because a later
 * change that does ask is entitled to mint it.
 */
const NOT_PRODUCED = Object.freeze(new Set(['reported', 'read', 'ordered', 'adjacent']));

/** Rank 7 is membership of this rack, not a shelf. Written down so it is not
 * quietly used as a position later. */
const RANK_7_IS_MEMBERSHIP = true;

// ── normalising a published value ───────────────────────────────────────────

/**
 * Values that are not values.
 *
 * Hardware fills a field it has no answer for rather than leaving it out, so
 * every one of these arrives in a serial number field somewhere.
 */
const JUNK = Object.freeze(new Set([
  'unknown', 'na', 'none', 'null', 'nil', 'nul', 'empty', 'blank',
  'notavailable', 'notapplicable', 'notset', 'notspecified', 'unspecified',
  'undefined', 'default', 'noserial', 'nosn', 'serialnumber', 'systemserialnumber',
  'chassisserialnumber', 'tobefilledbyoem', 'tobefilledbyoem123456789',
  'xxxxxxxx', 'test', 'sample',
]));

/**
 * Vendor names, so a field holding nothing but the maker's name is rejected.
 *
 * Same list the collector's enterprise table names, plus the few spellings the
 * same makers use in an inventory field. A serial that reads "TP-Link" tells us
 * who made it and nothing about which one it is.
 */
const VENDOR_NAMES = Object.freeze(new Set([
  'cisco', 'ciscosystems', 'hp', 'hpe', 'hewlettpackard', 'aruba', 'arubanetworks',
  'h3c', 'huawei', 'juniper', 'junipernetworks', 'dlink', 'extreme',
  'extremenetworks', 'foundry', 'netgear', 'dell', 'dellemc', 'dellforce10',
  'arista', 'aristanetworks', 'mikrotik', 'zyxel', 'ubiquiti', 'fortinet',
  'accton', 'nortel', 'broadcom', 'linksys', 'netsnmp', 'checkpoint', 'tplink',
  'tplinkomada', 'cambium', 'brocade', 'ruckus', 'edgecore', 'tenda', 'trendnet',
  'alliedtelesis', 'unknownvendor',
]));

/**
 * Down to comparable characters: lowercase, separators gone.
 *
 * This is the whole reason "C8:78:7D:3D:E5:30" and "c8787d3de530" are one
 * value: the same switch publishes the same address with different separators
 * depending on which MIB you asked.
 */
const strip = (v) => String(v ?? '').toLowerCase().replace(/[^a-z0-9]+/g, '');

/**
 * A management address or a hostname keeps its dots and colons.
 *
 * Stripping them would turn 10.10.1.1 and 1.0.101.1 into the same string, which
 * is how one device becomes another device.
 */
const hostForm = (v) => String(v ?? '').trim().toLowerCase()
  .replace(/^\[|\]$/g, '')
  .replace(/\s+/g, '')
  .replace(/\.$/, '');

/** A name keeps its shape too, minus the whitespace and the case. */
const nameForm = (v) => String(v ?? '').trim().toLowerCase().replace(/\s+/g, ' ');

/**
 * Is this value junk, whatever field it arrived in?
 *
 * Judged on the stripped form, so "n/a", "N/A" and "NA" are one answer, and so
 * that a spaced-out vendor name is still a vendor name.
 */
function isJunkValue(raw) {
  const s = strip(raw);
  if (!s) return true;
  if (s.length < 3) return true;              // nothing this short identifies anything
  if (JUNK.has(s)) return true;
  if (VENDOR_NAMES.has(s)) return true;
  if (/^0+$/.test(s)) return true;            // all zeros, including 00:00:00:00:00:00
  if (/^f+$/.test(s)) return true;            // the broadcast address
  if (/^enterprise\d+$/.test(s)) return true; // an SNMP vendor we could not name
  return false;
}

/**
 * One alias, or null when the value does not deserve to be one.
 *
 * Returns the string "kind:value" that everything else in the system compares
 * on, so there is exactly one place where an alias is spelled.
 */
function alias(kind, raw) {
  if (!KINDS.includes(kind)) return null;
  if (isJunkValue(raw)) return null;
  const value = kind === 'host' ? hostForm(raw)
    : kind === 'sysname' ? nameForm(raw)
      : strip(raw);
  if (!value) return null;
  return `${kind}:${value}`;
}

/** The kind half of an alias, or '' if it is not one. */
const kindOf = (a) => {
  const s = String(a ?? '');
  const i = s.indexOf(':');
  return i > 0 ? s.slice(0, i) : '';
};

/** The rung an alias kind sits on, or null for one that is not an identity. */
const rankOf = (kind) => (Object.prototype.hasOwnProperty.call(LADDER, kind) ? LADDER[kind] : null);

/** Is this alias one that can prove identity by itself? */
const isStrong = (a) => STRONG.has(kindOf(a));

/** The value half of an alias, or '' if it is not one. */
const valueOf = (a) => {
  const s = String(a ?? '');
  const i = s.indexOf(':');
  return i > 0 ? s.slice(i + 1) : '';
};

/**
 * The string two aliases are compared on.
 *
 * Identical to the alias itself for every kind but the three hardware-address
 * ones, where a MAC-shaped value collapses to one key so chassis:X, bridge:X and
 * mac:X are one address. A serial that happens to look like a MAC keeps its own
 * namespace: a serial is not an address and must never merge with one.
 */
function matchKey(a) {
  const kind = kindOf(a);
  const value = valueOf(a);
  if (HW_KINDS.has(kind) && MAC_FORM.test(value)) return `hw:${value}`;
  return `${kind}:${value}`;
}

// ── the identity of a reading ────────────────────────────────────────────────

/**
 * Every alias one switch reading published, sorted, de-duplicated.
 *
 * Sorted so two readings of the same switch produce byte-identical arrays and a
 * stored identity can be compared without caring what order the fields arrived
 * in. A stack contributes one serial per member (standard 5.2): a reading that
 * saw member 2 and a reading that saw member 1 then still share the chassis
 * address and resolve to one device.
 *
 * Accepts the shape lib/netbox/reader.js stores, plus the management host, which
 * lives on the switch record rather than the reading - pass it as `host` on the
 * object, as reconcile does.
 */
function aliasesOf(reading) {
  const r = reading && typeof reading === 'object' ? reading : {};
  const ident = r.identity && typeof r.identity === 'object' ? r.identity : {};
  const system = r.system && typeof r.system === 'object' ? r.system : {};
  const out = new Set();
  const add = (kind, value) => { const a = alias(kind, value); if (a) out.add(a); };

  // A serial field holding the device's own model number is not a serial, and
  // the model is on the same reading, so there is no excuse for believing it.
  // Two GS724Tv4 switches both reporting "GS724Tv4" as their serial would
  // otherwise be one device at rung 1, and a binding made on one of them would
  // be applied to the other as a fact.
  const shapes = new Set([ident.model, ident.hardwareRev, ident.derivedModel]
    .map((v) => strip(v)).filter(Boolean));
  const addSerial = (value, ownModel = null) => {
    const s = strip(value);
    if (!s) return;
    if (shapes.has(s) || (ownModel && strip(ownModel) === s)) return;
    add('serial', value);
  };

  addSerial(ident.serial);
  for (const m of Array.isArray(ident.members) ? ident.members : []) {
    addSerial(m && m.serial, m && m.model);
  }
  // Only an address is rung 3. A chassis id the device published under any other
  // LLDP subtype is a name or a model string, which two units share.
  const subtype = r.localChassisIdSubtype ?? ident.localChassisIdSubtype ?? null;
  const chassisValue = strip(r.localChassisId);
  const chassisIsAddress = subtype === null || subtype === undefined || subtype === ''
    ? MAC_FORM.test(chassisValue)
    : Number(subtype) === LLDP_SUBTYPE_MAC;
  if (chassisIsAddress) add('chassis', r.localChassisId);
  // No collector publishes a bridge base address or a management MAC into the
  // reading yet. Read from every place one would plausibly land so the day it
  // does, identity picks it up without another change here.
  add('bridge', r.bridgeAddress ?? ident.bridgeAddress ?? system.bridgeAddress);
  add('mac', r.mgmtMac ?? ident.mgmtMac ?? system.mgmtMac);
  // A switch that publishes no serial, no chassis id and no bridge address still
  // carries the hardware addresses of its own ports, and no other unit has them.
  // The lowest one stands for the unit, at the rung of a management address. It
  // is what lets a person confirm a D-Link DGS-1210 at all: that switch answers
  // SNMP with no serial and no LLDP chassis id, so its confirmation was refused
  // every time while the two switches beside it were kept.
  if (![...out].some(isStrong)) {
    const lowest = (Array.isArray(r.interfaces) ? r.interfaces : [])
      .map((i) => strip(i && (i.mac ?? i.physAddress)))
      .filter((m) => MAC_FORM.test(m) && !/^0{12}$/.test(m) && !/^f{12}$/.test(m))
      .sort()[0];
    if (lowest) add('mac', lowest);
  }
  add('sysname', system.sysName);
  add('host', r.host ?? r.mgmtHost);

  return [...out].sort();
}

/** A clean alias list from anything a caller hands us. */
const asAliases = (v) => (Array.isArray(v) ? v : [])
  .map((a) => String(a ?? '').trim())
  .filter((a) => a.includes(':'));

/**
 * Are these two identities the same device?
 *
 * True only on a shared strong alias, and it reports which one and which rung
 * it sits on, so the answer can be shown to a person and argued with. When
 * several aliases are shared the strongest wins, which makes the answer
 * independent of the order either list came in.
 *
 * Two things stop a shared alias being taken at face value:
 *
 *   - the three hardware-address kinds are compared by value, so one base
 *     address published as a chassis id here and a management MAC there is one
 *     address. The rung reported is the weaker of the two;
 *   - a shared alias weaker than a serial is REFUTED when both sides publish
 *     serials and none of them agree. Two different serial numbers are positive
 *     evidence of two different devices, whatever a shared chassis string says,
 *     and a vendor publishing a stack name as its chassis id is exactly how two
 *     units come to share one.
 *
 * A refusal says so: { same: false, refutedBy: 'serial' }, so a caller can tell
 * "nothing in common" from "these are provably not the same".
 */
function sameDevice(aliasesA, aliasesB) {
  const listA = asAliases(aliasesA);
  const listB = asAliases(aliasesB);

  // matchKey -> the weakest kind either side spelled it as, so a chassis id on
  // one side and a management MAC on the other report as 'mac', rung 5.
  const mine = new Map();
  for (const a of listA) {
    if (!STRONG.has(kindOf(a))) continue;
    const key = matchKey(a);
    const prev = mine.get(key);
    if (!prev || rankOf(kindOf(a)) > rankOf(kindOf(prev))) mine.set(key, a);
  }

  let best = null;
  for (const b of listB) {
    const kindB = kindOf(b);
    if (!STRONG.has(kindB)) continue;
    const held = mine.get(matchKey(b));
    if (!held) continue;
    // The weaker of the two claims about the same value is what it proves.
    const kind = rankOf(kindOf(held)) > rankOf(kindB) ? kindOf(held) : kindB;
    const rank = rankOf(kind);
    if (best === null || rank < best.rank) best = { same: true, by: kind, rank, alias: b };
  }
  if (!best) return { same: false, by: null, rank: null, alias: null, refutedBy: null };

  if (best.rank > LADDER.serial) {
    const serialsA = listA.filter((a) => kindOf(a) === 'serial');
    const serialsB = listB.filter((a) => kindOf(a) === 'serial');
    if (serialsA.length && serialsB.length && !serialsA.some((a) => serialsB.includes(a))) {
      return { same: false, by: null, rank: null, alias: null, refutedBy: 'serial' };
    }
  }
  return { ...best, refutedBy: null };
}

// ── evidence and confidence ────────────────────────────────────────────────

/**
 * One piece of evidence for a binding.
 *
 * The only way to mint one. A rank this version cannot honestly produce throws
 * here rather than being written into a record and believed later.
 *
 * `extra` carries whatever that rank needs to be checked: a rank 8 entry must
 * say how many boxes its evidence could not tell apart, because that is what
 * decides whether it is 'possible' or nothing at all.
 */
function evidence(source, why, extra = {}) {
  const key = String(source || '').trim().toLowerCase();
  if (!Object.prototype.hasOwnProperty.call(EVIDENCE_RANK, key)) {
    throw new Error(`unknown evidence source: ${source}`);
  }
  if (NOT_PRODUCED.has(key)) {
    throw new Error(`nothing in this version can produce ${key} evidence (rank ${EVIDENCE_RANK[key]})`);
  }
  return { source: key, rank: EVIDENCE_RANK[key], why: String(why || ''), ...extra };
}

/**
 * The one confidence level a list of evidence earns, standard section 7.
 *
 *   confirmed     rank 1, 2 or 3          written back
 *   probable      rank 4, 5, 6 or 7       written back, marked
 *   possible      rank 8, one candidate   never written
 *   unidentified  anything else           never written
 *
 * A rank 8 entry MUST carry `candidateCount`: how many boxes this evidence
 * could not tell apart. Exactly one is 'possible'; two or more is nothing,
 * because 6.2 forbids binding on rank 8 where more than one device fits. An
 * entry that does not say gets 'unidentified': we cannot claim there was one
 * candidate if nobody counted.
 *
 * Note this is not the same number as the candidate SET size that 8.3 asks to
 * be recorded and reported. That one is on the reason the matcher returns.
 */
function confidenceOf(evidenceList) {
  const list = (Array.isArray(evidenceList) ? evidenceList : [])
    .filter((e) => e && typeof e === 'object');
  const ranks = list.map((e) => Number(e.rank)).filter((n) => Number.isFinite(n));
  if (ranks.some((r) => r >= 1 && r <= 3)) return 'confirmed';
  if (ranks.some((r) => r >= 4 && r <= 7)) return 'probable';
  const inferred = list.filter((e) => Number(e.rank) === 8);
  if (inferred.length && inferred.some((e) => Number(e.candidateCount) === 1)) return 'possible';
  return 'unidentified';
}

/**
 * The weak aliases two identities share, strongest first.
 *
 * Never an identity - that is what 4.3 and 4.4 forbid - but it is the difference
 * between "this switch matches nothing we hold" and "this switch matches a
 * confirmation made under a serial it did not publish this time". The second is
 * worth telling the person, because it names what to re-read.
 */
function weakOverlap(aliasesA, aliasesB) {
  const have = new Set(asAliases(aliasesA));
  // A management address is rung 6 and a published name is on no rung at all
  // (4.4), so the address comes first of the two.
  const order = (a) => rankOf(kindOf(a)) ?? 99;
  return asAliases(aliasesB)
    .filter((a) => have.has(a) && KINDS.includes(kindOf(a)) && !STRONG.has(kindOf(a)))
    .sort((a, b) => order(a) - order(b));
}

/**
 * Is a binding at this confidence allowed into a system of record?
 *
 * Standard 7.2 allows probable as well, marked at the destination. This version
 * writes CONFIRMED ONLY, because there is nowhere in the snapshot to carry the
 * mark: a serial written on a device row is read as a fact by everything
 * downstream, and "probable" would arrive as one. Probable and possible are
 * shown on the screen and in the report and written nowhere.
 */
const writable = (confidence) => confidence === 'confirmed';

module.exports = {
  aliasesOf, rankOf, sameDevice, confidenceOf, evidence, weakOverlap,
  alias, kindOf, valueOf, matchKey, isStrong, isJunkValue, writable,
  // The one place a hardware value is put into comparable form. Exported so a
  // lookup against the customer's record compares serials the same way a
  // binding does, rather than growing a second spelling of the same rule.
  normalise: strip,
  LADDER, KINDS, STRONG, HW_KINDS, EVIDENCE_RANK, NOT_PRODUCED, RANK_7_IS_MEMBERSHIP,
};
