/**
 * Give each port the NetBox record that already carries its number.
 *
 * A port's identity is the number printed beside it. The uid the camera side
 * gives a port is built from its place in the detection list, which is not
 * stable: a second reading lists ports in another order, some ports come and go
 * between readings, and earlier writes may have created records under uids no
 * current reading uses. So NetBox can hold "45" on a switch under one uid while
 * this reading calls port 45 by another, and the write then asks NetBox for a
 * second "45" on that switch, which it refuses. On the demo rack's 52 port
 * switch that was nine refusals after every other fix.
 *
 * This runs once, when a snapshot is built, against NetBox as it is:
 *
 *   1. A port whose number one of our records on that device already carries
 *      takes that record's uid. Nothing to rename, nothing to create.
 *   2. Any other port keeps its own uid if no port took it in step 1 - its
 *      record, if it has one, is renamed to a number nobody holds, so the
 *      rename cannot collide - or else gets a new uid that no record carries,
 *      so it never takes over a port this reading did not see.
 *
 * It is done here, where the snapshot is made, and stored with it, so the plan
 * a person approves, the items they approve or reject, and the write that
 * follows all name every port the same way. Only our own records are
 * considered (the racktrack_uid field), only on the device the port belongs
 * to, and nothing is written: this changes the snapshot, not NetBox.
 */
const { UID_FIELD } = require('./netbox');

const idOf = (v) => (v && typeof v === 'object' ? v.id : v);

async function alignPortsToNetBox(snapshot, client) {
  const ports = (snapshot && snapshot.interfaces) || [];
  if (!client || !ports.length) return { snapshot, rebound: 0 };
  // Names first, the way the writer will write them. Two ports the camera read
  // as the same number are told apart ("46", "46-2") by the writer before it
  // writes; aligning before that compared "46" against a NetBox that already
  // held "46-2", and planned to create it a second time. Doing it here is the
  // same rename the writer would do, so it is idempotent there.
  require('./writer')._internal.uniqueInterfaceNames(snapshot, { warnings: [] });
  const rackKey = String(snapshot.rackUid || '').replace(/^rack:/, '');
  if (!rackKey) return { snapshot, rebound: 0 };

  let nbDevices;
  let nbPorts;
  try {
    const q = { [`cf_${UID_FIELD}__ic`]: rackKey };
    nbDevices = await client.paginate('/api/dcim/devices/', q);
    nbPorts = await client.paginate('/api/dcim/interfaces/', q);
  } catch {
    // NetBox not there: the snapshot keeps the camera's identities, and the
    // write's own temporary-name step still handles ports that swap names.
    return { snapshot, rebound: 0, skipped: 'NetBox did not answer' };
  }

  const deviceIdOf = new Map();
  for (const d of nbDevices) {
    const uid = (d.custom_fields || {})[UID_FIELD];
    if (uid) deviceIdOf.set(uid, d.id);
  }
  // Our port records on each NetBox device: printed number -> uid.
  const heldOn = new Map();
  const everyUid = new Set();
  for (const p of nbPorts) {
    const uid = (p.custom_fields || {})[UID_FIELD];
    if (!uid) continue;
    everyUid.add(uid);
    const dev = idOf(p.device);
    if (dev == null) continue;
    if (!heldOn.has(dev)) heldOn.set(dev, new Map());
    heldOn.get(dev).set(String(p.name), uid);
  }

  const byDevice = new Map();
  for (const p of ports) {
    if (!byDevice.has(p.deviceUid)) byDevice.set(p.deviceUid, []);
    byDevice.get(p.deviceUid).push(p);
  }

  const remap = new Map();
  for (const [deviceUid, list] of byDevice) {
    const held = heldOn.get(deviceIdOf.get(deviceUid));
    if (!held || !held.size) continue;
    const taken = new Set();
    const chosen = new Map();
    for (const p of list) {
      const uid = held.get(String(p.name));
      if (uid && !taken.has(uid)) { chosen.set(p, uid); taken.add(uid); }
    }
    for (const p of list) {
      if (chosen.has(p)) continue;
      let uid = p.uid;
      // Its own uid, unless a port took that record by number in step 1. A
      // fresh uid must not be one some unseen port still carries.
      for (let n = 1; taken.has(uid) || (uid !== p.uid && everyUid.has(uid)); n += 1) {
        uid = `${p.uid}.r${n}`;
      }
      chosen.set(p, uid);
      taken.add(uid);
    }
    for (const [p, uid] of chosen) if (uid !== p.uid) remap.set(p.uid, uid);
  }
  if (!remap.size) return { snapshot, rebound: 0 };

  const moved = (uid) => (remap.has(uid) ? remap.get(uid) : uid);
  const end = (t) => (t && t.uid && remap.has(t.uid) ? { ...t, uid: moved(t.uid) } : t);
  const out = {
    ...snapshot,
    interfaces: ports.map((p) => (remap.has(p.uid) ? { ...p, uid: moved(p.uid) } : p)),
    cables: (snapshot.cables || []).map((c) => ({ ...c, a: end(c.a), b: end(c.b) })),
    conflicts: (snapshot.conflicts || []).map((c) => (remap.has(c.subjectUid) ? { ...c, subjectUid: moved(c.subjectUid) } : c)),
  };
  return { snapshot: out, rebound: remap.size };
}

module.exports = { alignPortsToNetBox };
