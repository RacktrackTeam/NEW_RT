/**
 * Put back into the record what the network already said.
 *
 * The office rack showed the gap plainly. The CMDB rows carry a name, a role,
 * a type and a shelf, and the two fields that could place a switch without
 * asking anybody - the serial and the management address - are empty on every
 * row, because nobody typed them and nothing ever wrote them: a camera cannot
 * read a serial off a front panel, and a photograph carries no addresses. Yet
 * the switches themselves publish both, and the app has been reading them over
 * SNMP all along and keeping them on its own side of the fence.
 *
 * So this proposes the write that closes the loop: the serial a switch states
 * about itself, onto the row that switch is - or, for a switch that publishes
 * no serial, the address it answered at, which is the only other thing that
 * can place a switch without asking anybody. It proposes nothing else, and it
 * proposes nothing at all unless the link between the switch and the row is
 * one the ladder allows a write from (standard section 7: rank 1, 2 or 3).
 *
 *   A person confirming "this switch is that box" is rank 1.
 *   The record naming the shelf a box sits on is rank 3.
 *   A score built from port counts is rank 8, and can never write.
 *
 * That is why nothing here derives its own link. It is handed the pairs and it
 * checks the rank on each one, so the only way to a write is through evidence
 * somebody else already earned.
 *
 * It also refuses to overwrite. A row that already carries a serial is left
 * exactly as it is and reported as a disagreement when the switch says
 * something else - a CMDB that disagrees with the network is a finding, not a
 * field to be silently corrected.
 *
 * Nothing here talks to NetBox. It is given both sides and returns what would
 * be written, so it can be tested without either.
 */
const identity = require('./identity');

const clean = (v) => {
  const s = identity.normalise(v);
  return s && !identity.isJunkValue(s) ? s : '';
};

/** An address in one shape, the way lib/netbox/switch_record compares them. */
const addr = (v) => String(v == null ? '' : v).trim().replace(/\/\d+$/, '').replace(/:\d+$/, '').toLowerCase();

/**
 * What the network knows and the record does not.
 *
 * @param {object[]} records  rows as lib/netbox/switch_record.recordsFrom gives them
 * @param {object[]} facts    switch readings: { id, label, serial, host, sysName }
 * @param {object[]} pairs    [{ switchId, recordId, confidence, why }] - the links,
 *                            each carrying the confidence the evidence earned
 * @returns {{ writes: object[], held: object[] }}
 *          writes: { recordId, recordName, field, value, switchId, switchLabel, why }
 *          held:   the ones that will not be written, each with the reason
 */
function proposeWrites({ records = [], facts = [], pairs = [] }) {
  const writes = [];
  const held = [];
  const rowOf = new Map(records.map((r) => [String(r.id), r]));
  const swOf = new Map(facts.map((f) => [String(f.id), f]));

  for (const pair of pairs) {
    const row = rowOf.get(String(pair.recordId));
    const sw = swOf.get(String(pair.switchId));
    if (!row || !sw) continue;

    const common = { recordId: row.id, recordName: row.name, switchId: sw.id, switchLabel: sw.label };

    if (!identity.writable(pair.confidence)) {
      held.push({
        ...common, field: 'serial', value: sw.serial || null,
        reason: `the link between them is ${pair.confidence || 'unidentified'}, and only a link the `
          + 'ladder calls confirmed may write into the record',
      });
      continue;
    }

    /* A switch that gives no serial can still be pinned by the address it
       answered at: an address is unique on the network the record describes,
       which is what makes it an identity rather than a resemblance. It is the
       second of the two things lib/netbox/switch_record can place a switch by,
       and it is offered only when there is no serial to offer instead. */
    if (!clean(sw.serial)) {
      if (!addr(sw.host)) {
        held.push({ ...common, field: 'serial', value: null,
          reason: 'this switch gives no serial number and no address, so there is nothing to put on the row' });
        continue;
      }
      if (addr(row.primaryIp)) {
        held.push({
          ...common, field: 'address', value: sw.host,
          reason: addr(row.primaryIp) === addr(sw.host)
            ? 'the record already holds this address'
            : `the record holds ${row.primaryIp} and the switch answered at ${sw.host} - a disagreement `
              + 'between the record and the network is a finding, not a field to overwrite',
          disagrees: addr(row.primaryIp) !== addr(sw.host),
        });
        continue;
      }
      writes.push({
        ...common,
        field: 'address',
        value: addr(sw.host),
        why: `${sw.label} answered at this address, and ${pair.why || 'the link to this row is confirmed'}`,
      });
      continue;
    }

    if (clean(row.serial)) {
      held.push({
        ...common, field: 'serial', value: sw.serial,
        reason: clean(row.serial) === clean(sw.serial)
          ? 'the record already holds this serial'
          : `the record holds ${row.serial} and the switch says ${sw.serial} - a disagreement between the `
            + 'record and the network is a finding, not a field to overwrite',
        disagrees: clean(row.serial) !== clean(sw.serial),
      });
      continue;
    }

    writes.push({
      ...common,
      field: 'serial',
      value: String(sw.serial).trim(),
      why: `${sw.label} states this serial about itself, and ${pair.why || 'the link to this row is confirmed'}`,
    });
  }

  return { writes, held };
}

module.exports = { proposeWrites };
