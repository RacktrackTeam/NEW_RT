/*
 * The physical topology of one rack, from the detail route's answer only:
 * the devices read in the photo, top unit first, and the cables between them
 * or leaving the rack. Nothing is invented: a link exists only where a label
 * was read on a port or the Excel dataset puts a cable on it.
 *
 *   links    one per cable: from a device and port of this rack, to another
 *            device of this rack (internal) or to a device in another rack
 *            (external). `seen` when the photo shows it plugged in; a cable
 *            the dataset has on an empty port is a link that is not seen.
 *   outside  the devices at the far end of external links, grouped by rack.
 *   loose    ports plugged in whose other end nobody knows.
 */

const norm = (s) => String(s ?? '').toUpperCase().replace(/[^A-Z0-9]/g, '');

function portOf(v) {
  const m = String(v ?? '').match(/(\d+)(?!.*\d)/);
  return m ? Number(m[1]) : null;
}

export function topologyOf(detail) {
  const devices = (detail?.devices || []).slice().sort((a, b) => (b.unitTop ?? -1) - (a.unitTop ?? -1));
  const here = norm(detail?.rackLabel);
  const byName = new Map();
  for (const d of devices) {
    for (const n of [d.name, d.sheetName, d.labelText]) if (n && !byName.has(norm(n))) byName.set(norm(n), d);
  }
  const links = [];
  const seenKeys = new Set();
  const outside = new Map();
  let loose = 0;

  const add = (from, port, other, info) => {
    if (!other || !other.device) return false;
    const inRack = (!other.rack || !here || norm(other.rack) === here) ? byName.get(norm(other.device)) : null;
    const otherPort = other.port != null && other.port !== '' ? String(other.port) : null;
    let to;
    if (inRack) {
      to = { kind: 'device', deviceId: inRack.deviceId, device: inRack.name, port: otherPort, unit: inRack.unitTop ?? null, rack: detail?.rackLabel || null };
    } else {
      const key = `${norm(other.rack)}|${norm(other.device)}`;
      if (!outside.has(key)) outside.set(key, { key, device: other.device, rack: other.rack || null, unit: other.unit ?? null });
      to = { kind: 'outside', key, device: other.device, port: otherPort, unit: other.unit ?? null, rack: other.rack || null };
    }
    // One cable is one link, however many of its ends were read.
    const ends = [`${from.deviceId}:${port}`, to.kind === 'device' ? `${to.deviceId}:${portOf(to.port)}` : `${to.key}:${norm(to.port)}`].sort();
    const key = info.label ? `L:${norm(info.label)}` : `E:${ends.join('|')}`;
    if (seenKeys.has(key)) {
      const had = links.find((l) => l.key === key);
      if (had && info.seen) had.seen = true;
      return true;
    }
    seenKeys.add(key);
    links.push({
      key,
      id: `k${links.length + 1}`,
      from: { deviceId: from.deviceId, device: from.name, port, unit: from.unitTop ?? null, rack: detail?.rackLabel || null },
      to,
      label: info.label || null,
      type: info.type || null,
      colour: info.colour || null,
      connector: info.connector || null,
      seen: !!info.seen,
      status: info.status || null,
      source: info.source,
    });
    return true;
  };

  for (const d of devices) {
    for (const p of d.ports || []) {
      const plugged = p.state === 'connected';
      const info = {
        label: p.expected?.cableLabel || p.label?.text || null,
        type: p.expected?.cableType || null,
        colour: p.cableColor || null,
        connector: p.cableConnector || null,
        seen: plugged,
        status: p.status,
        source: p.expected ? 'dataset' : 'label',
      };
      let placed = false;
      if (p.expected?.other) placed = add(d, p.portNo, p.expected.other, info);
      else if (p.labelBelongsTo) {
        // The label read names a cable of the dataset; its other end is the
        // end that is not this device.
        const c = p.labelBelongsTo;
        const other = norm(c.a?.device) === norm(d.sheetName || d.name) ? c.z : c.a;
        placed = add(d, p.portNo, other, { ...info, label: c.cableLabel, source: 'label' });
      }
      if (!placed && plugged) loose += 1;
    }
  }
  for (const c of detail?.cables || []) {
    if (!c.to || c.from?.portNo == null) continue;
    const d = devices.find((x) => x.deviceId === c.from.deviceId);
    if (!d) continue;
    add(d, c.from.portNo, c.to, { label: c.sheetLabel || c.label, type: c.type, colour: c.colour, seen: true, status: c.status, source: 'label' });
  }

  return { devices, links, outside: [...outside.values()], loose };
}

/** "SW-CORE-01 port 1, U40, R01" */
export function endWords(end) {
  if (!end) return '';
  const what = [end.device, end.port != null && end.port !== '' ? `port ${end.port}` : null].filter(Boolean).join(' ');
  return [what, end.unit != null ? `U${end.unit}` : null, end.rack].filter(Boolean).join(', ');
}
