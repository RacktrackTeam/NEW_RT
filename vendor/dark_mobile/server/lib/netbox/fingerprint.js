/**
 * The port fingerprint: rung 6 of the device ladder.
 *
 * A 48 port switch with cables in 31 of its sockets is wearing a 48 digit
 * number on its face. The live switch reports the same number as which of its
 * ports are up. Neither side needs a label, a serial or a model to be read:
 * the camera sees the cables, the switch reports the links, and the two
 * numbers are compared digit by digit.
 *
 * This is the rung that separates two identical unlabelled switches one shelf
 * apart, which is the case no other rung can settle and the case our own
 * office rack is full of. Two switches of the same model and firmware rarely
 * carry the same pattern, and when they do, the uplinks differ.
 *
 * The rules, from the plan (docs/netbox/match-and-reconcile-detailed.html,
 * "Cables and ports: the fingerprint"):
 *
 *   cabled  and up    agree
 *   empty   and down  agree
 *   cabled  and down  UNKNOWN, never a mismatch. A dark spare, a dead far end
 *                     and a port somebody shut down all look like this.
 *   empty   and up    a camera miss, or a port on the rear. Reported, and it
 *                     counts against the candidate, lightly.
 *   socket not visible (a bundle across the front) drops out entirely. It is
 *                     never read as empty.
 *
 * The uplinks are the sharpest digits: four cages, each populated or not, and
 * two switches rarely share all four. They are weighted accordingly.
 *
 * Nothing here decides anything on its own. It scores one candidate against
 * one box and says how sure it is; rank() orders the candidates and refuses
 * when the winner does not clearly beat the runner up. A tie is never broken
 * by picking the first one.
 */

/**
 * Interfaces that are not sockets on the front of the box.
 *
 * ifType from IANAifType-MIB: 24 loopback, 23 ppp, 131 tunnel, 135 l2vlan,
 * 136 l3ipvlan, 161 aggregate, 53 propVirtual, which is what several vendors
 * call the interface a switch answers on. Counting them made a 28 port switch
 * a 29 port switch, and a fingerprint compared against the wrong number of
 * digits is worse than no fingerprint.
 */
const SKIP_TYPE = new Set([23, 24, 53, 131, 135, 136, 161]);
const SKIP_TYPE_NAME = new Set(['loopback', 'virtual', 'multiplexor', 'tunnel']);

/**
 * ...and the ones that lie about their type. The TP-Link SG2428P reports
 * "Vlan-interface1" as ifType 6, ethernetCsmacd, the same type as the 28
 * sockets on its front. The name is the only thing that gives it away. This
 * is the same rule the phone reader already applies; it lives here too so a
 * reading taken by the server is measured the same way as one taken by a
 * phone.
 */
const NOT_A_SOCKET = /vlan|loopback|^lo\d|tunnel|null ?0|port-?channel|^po\d|aggregat|^ae\d/i;

/** Socket kinds that carry more information than a copper port. */
const UPLINK_TYPE = /sfp|qsfp|xfp|fibre|fiber|cage/i;
/** Sockets that are not part of the pattern at all. */
const NOT_IN_PATTERN = /console|aux|usb|mgmt|management|power/i;

/** An uplink digit is worth this many copper digits. */
const UPLINK_WEIGHT = 3;
/** An empty socket against a port that is up. Counted against, lightly. */
const MISS_PENALTY = 0.25;
/**
 * How far ahead the winner must be before this rung settles anything, as a
 * share of the best score. Below it the rung has narrowed the shortlist and
 * nothing more, which is exactly what the plan asks of a rung that does not
 * settle it.
 */
const MARGIN = 0.12;
/** Below this, agreement is no better than chance and the rung says nothing. */
const FLOOR = 0.6;

const lower = (v) => String(v ?? '').toLowerCase();

/** Whether an interface is a socket on the front of the box. */
function isSocket(i) {
  if (!i) return false;
  const t = i.type;
  if (typeof t === 'number' && SKIP_TYPE.has(t)) return false;
  if (typeof t === 'string') {
    const m = /^type (\d+)$/.exec(t.trim());
    if (m && SKIP_TYPE.has(Number(m[1]))) return false;
    if (SKIP_TYPE_NAME.has(lower(t))) return false;
  }
  return !NOT_A_SOCKET.test(String(i.name || ''));
}

/**
 * The sockets on the front of a switch, in the order the switch lists them.
 * ifIndex is the switch's own order and is the closest thing to left to right
 * that SNMP offers.
 */
function physicalPorts(reading) {
  const list = (reading && reading.interfaces) || [];
  return list.filter(isSocket).slice().sort((a, b) => {
    const x = Number(a.ifIndex ?? a.index ?? 0);
    const y = Number(b.ifIndex ?? b.index ?? 0);
    if (x !== y) return x - y;
    return String(a.name || '').localeCompare(String(b.name || ''));
  });
}

/** Is this port carrying a link right now? */
const isUp = (p) => lower(p.operStatus) === 'up' || p.operStatus === 1;
/** Did the switch actually say? A port it never reported on is not a "down". */
const answered = (p) => p.operStatus !== undefined && p.operStatus !== null && p.operStatus !== '';

/**
 * The sockets the camera saw on one box, in port order.
 *
 * Accepts either the physical layer report's device ports (port_number,
 * port_type, status) or the snapshot's interfaces for that device, whose
 * provenance carries the same status under a different name. Console, USB,
 * management and power sockets are dropped: they are not part of the pattern
 * a switch reports.
 */
function socketsOf(device) {
  const rows = (device && (device.ports || device.sockets || device.interfaces)) || [];
  const out = [];
  for (const r of rows) {
    const type = String(r.port_type || r.type || (r.provenance && r.provenance.category) || '');
    if (NOT_IN_PATTERN.test(type)) continue;
    const raw = r.status ?? (r.provenance && r.provenance.status);
    const status = raw === 'connected' || raw === 'empty' ? raw : 'unknown';
    out.push({
      n: Number(r.port_number ?? r.index ?? r.name ?? out.length + 1),
      type,
      status,
      uplink: UPLINK_TYPE.test(type),
      cable: r.cable || null,
    });
  }
  return out.sort((a, b) => a.n - b.n);
}

const weigh = (socket) => (socket.uplink ? UPLINK_WEIGHT : 1);

/**
 * Compare one box's sockets with one switch's ports.
 *
 * Returns the counts, the score and, when the two sides carry a different
 * number of sockets, says so rather than pretending to line them up. A score
 * is only meaningful against another score from the same box, which is what
 * rank() does with it.
 */
function compare(sockets, ports) {
  const seen = sockets.filter((s) => s.status !== 'unknown');
  const said = ports.filter(answered);
  const out = {
    alignment: 'position',
    sockets: sockets.length,
    ports: ports.length,
    agree: 0,
    darkCable: 0,
    miss: 0,
    notVisible: sockets.length - seen.length,
    weighed: 0,
    earned: 0,
    score: 0,
    why: '',
  };

  // Different numbers of sockets and ports: the two sides are not the same
  // face. Fall back to the count of cables against the count of links, which
  // is a far weaker signal, and say so.
  if (sockets.length !== ports.length || !sockets.length || !ports.length) {
    const cabled = seen.filter((s) => s.status === 'connected').length;
    const up = said.filter(isUp).length;
    out.alignment = 'count';
    const spread = Math.max(cabled, up) || 1;
    out.score = Math.max(0, 1 - Math.abs(cabled - up) / spread);
    out.agree = Math.min(cabled, up);
    out.why = `${cabled} cabled sockets against ${up} ports that are up, and the two sides count a different number of sockets (${sockets.length} against ${ports.length})`;
    return out;
  }

  for (let i = 0; i < sockets.length; i += 1) {
    const s = sockets[i];
    const p = ports[i];
    if (s.status === 'unknown') continue;       // a bundle across the front
    if (!answered(p)) continue;                 // the switch did not say
    const w = weigh(s);
    const cabled = s.status === 'connected';
    const up = isUp(p);
    if (cabled && up) { out.weighed += w; out.earned += w; out.agree += 1; continue; }
    if (!cabled && !up) { out.weighed += w; out.earned += w; out.agree += 1; continue; }
    if (cabled && !up) { out.darkCable += 1; continue; }  // unknown, never a mismatch
    out.weighed += w; out.earned -= w * MISS_PENALTY; out.miss += 1;
  }

  out.score = out.weighed > 0 ? Math.max(0, out.earned / out.weighed) : 0;
  const pct = Math.round(out.score * 100);
  out.why = `${out.agree} of ${out.agree + out.miss} sockets agree with the switch (${pct} per cent)`
    + (out.darkCable ? `, ${out.darkCable} cabled but not carrying a link` : '')
    + (out.miss ? `, ${out.miss} the camera did not see a cable on` : '')
    + (out.notVisible ? `, ${out.notVisible} hidden behind cables and not counted` : '');
  return out;
}

/**
 * Score every candidate switch against one box and order them.
 *
 * `settled` is true only when the winner clears the runner up by the margin
 * AND beats the floor. Otherwise this rung has shortened the list and said
 * nothing else, and the next rung, or a person, decides. Ties are never
 * broken by order: identical scores leave settled false whichever way the
 * list was sorted.
 */
function rank(sockets, candidates) {
  const scored = candidates.map((c) => ({
    id: c.id,
    reading: c.reading,
    result: compare(sockets, physicalPorts(c.reading)),
  }));
  // Sorted by score, then by id, so the order of the input never changes the
  // output. Two candidates with one score sort by id and still do not settle.
  scored.sort((a, b) => (b.result.score - a.result.score)
    || String(a.id).localeCompare(String(b.id)));

  const best = scored[0] || null;
  const runnerUp = scored[1] || null;
  const margin = best && runnerUp ? best.result.score - runnerUp.result.score : (best ? best.result.score : 0);
  const weak = best && best.result.alignment === 'count';
  const settled = Boolean(best)
    && best.result.score >= FLOOR
    && (!runnerUp || margin >= MARGIN * best.result.score)
    && !weak;

  let why;
  if (!best) why = 'no switch has been read for this rack yet';
  else if (weak) why = `${best.result.why}, so the pattern cannot be compared socket by socket`;
  else if (settled) why = best.result.why;
  else if (best.result.score < FLOOR) why = `no switch matches the cables on this box (best was ${best.result.why})`;
  else why = `two switches carry the same pattern of cables (${Math.round(best.result.score * 100)} per cent and `
    + `${Math.round(runnerUp.result.score * 100)} per cent), so this cannot tell them apart`;

  return {
    settled,
    best: settled ? best : null,
    shortlist: scored.filter((s) => s.result.score >= FLOOR).map((s) => s.id),
    margin,
    scored,
    why,
  };
}

/**
 * The same comparison the other way round: one switch against several boxes.
 *
 * rank() asks "which of these switches is this box". This asks "which of these
 * boxes is this switch", which is the question the matcher actually has when
 * two candidates have tied on everything else. The rules, the floor and the
 * margin are identical; only the direction differs.
 */
function rankBoxes(reading, boxes) {
  const ports = physicalPorts(reading);
  const scored = (boxes || []).map((b) => ({
    uid: b.uid,
    name: b.name,
    result: compare(b.sockets || [], ports),
  }));
  scored.sort((a, b) => (b.result.score - a.result.score)
    || String(a.uid).localeCompare(String(b.uid)));

  const best = scored[0] || null;
  const runnerUp = scored[1] || null;
  const margin = best && runnerUp ? best.result.score - runnerUp.result.score : (best ? best.result.score : 0);
  const weak = best && best.result.alignment === 'count';
  const settled = Boolean(best)
    && best.result.score >= FLOOR
    && (!runnerUp || margin >= MARGIN * best.result.score)
    && !weak;

  let why;
  if (!best) why = 'no box to compare the cables against';
  else if (weak) why = 'the camera and the switch count a different number of sockets, so the cables cannot be compared one by one';
  else if (settled) why = `the cables match: ${best.result.why}`;
  else if (best.result.score < FLOOR) why = 'no box in the photograph has the cables this switch reports';
  else why = `${best.name} and ${runnerUp.name} carry the same pattern of cables, so the cables cannot tell them apart either`;

  return { settled, best: settled ? best : null, margin, scored, why };
}

/**
 * What a settled fingerprint is worth. The plan puts the cable pattern under
 * "Probable": it picked a clear winner from behaviour, which is strong, but it
 * is not the switch naming itself and not a person. Only a serial, a code, a
 * beacon or a person reaches Confirmed.
 */
const CONFIDENCE = 'probable';

module.exports = {
  physicalPorts, socketsOf, compare, rank, rankBoxes, isSocket,
  UPLINK_WEIGHT, MISS_PENALTY, MARGIN, FLOOR, CONFIDENCE,
};
