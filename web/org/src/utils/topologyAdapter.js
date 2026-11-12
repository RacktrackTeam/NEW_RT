/*
 * The scan's detail answer, reshaped into the topology the Topology tab draws
 * (the same shape RackTrack's topology screen has always drawn from):
 *
 *   { rackName, rackId, u_size, stats, neighbors, loose,
 *     devices: [{ name, class, class_label, model, u_position, u_size, in_rack,
 *                 deviceId, ports: [{ name, label, kind, connected, is_uplink }] }],
 *     edges:   [{ cable_id, cable_label, cable_type, connector, color, length,
 *                 seen, src: { device, port }, dst: { device, port } }] }
 *
 * Nothing is invented. Devices are the ones read on the photo; ports are the
 * ports read on them, with the state the photo gives (connected true, empty
 * false, not known null). Cables are topologyOf's links: a label read on a
 * port, or a cable the Excel dataset puts on a device of this rack. A far end
 * that is not a device read on this photo becomes an "other end" device kept
 * outside the rack drawing, with only what the Excel dataset says about it.
 * No length, address or uplink role is filled in, because nothing reads them.
 */
import { topologyOf } from './topology';

const norm = (s) => String(s ?? '').toUpperCase().replace(/[^A-Z0-9]/g, '');

/** "Patch Panel" -> "patch_panel": the class keys the drawing's colours use. */
export const classKey = (type) => String(type ?? '').trim().toLowerCase().replace(/[\s-]+/g, '_');

function portKind(p) {
  const k = String(p.kind || '').toLowerCase();
  const t = String(p.type || '').toLowerCase();
  if (k === 'sfp' || t.startsWith('sfp') || t.startsWith('qsfp')) return 'sfp';
  if (k === 'console' || t === 'console') return 'console';
  if (k === 'usb' || t === 'usb') return 'usb';
  return 'main';
}

function connectedOf(state) {
  if (state === 'connected') return true;
  if (state === 'empty') return false;
  return null;
}

/** A port name from the Excel dataset ("Port1", "12") read as a port number, or null ("57A"). */
function numberOf(name) {
  const m = String(name ?? '').trim().match(/^[A-Za-z]*\s*(\d+)$/);
  return m ? Number(m[1]) : null;
}

export function rtTopologyOf(detail) {
  if (!detail) return null;
  const t = topologyOf(detail);
  const here = norm(detail.rackLabel);
  const used = new Set();
  const unique = (base) => {
    let n = String(base || 'Device');
    if (!used.has(n)) { used.add(n); return n; }
    let i = 2;
    while (used.has(`${n} (${i})`)) i += 1;
    n = `${n} (${i})`;
    used.add(n);
    return n;
  };

  const nameById = new Map();
  const portsById = new Map();
  const devices = [];
  for (const d of t.devices) {
    const cls = classKey(d.type) || 'unidentified';
    const name = unique(d.name || d.type);
    nameById.set(d.deviceId, name);
    const size = Math.max(1, d.heightU || 1);
    const bottom = d.unitBottom ?? (d.unitTop != null ? d.unitTop - size + 1 : null);
    const seenNames = new Set();
    const ports = (d.ports || []).map((p) => {
      const kind = portKind(p);
      let pname = String(p.portNo);
      if (seenNames.has(pname)) pname = `${kind} ${p.portNo}`;
      seenNames.add(pname);
      return {
        name: pname,
        label: kind === 'sfp' ? `SFP ${p.portNo}` : String(p.portNo),
        portNo: p.portNo,
        kind,
        connected: connectedOf(p.state),
        is_uplink: false,
        connector: p.cableConnector || null,
        cable_color: p.cableColor || null,
        label_read: p.label?.text || null,
      };
    });
    portsById.set(d.deviceId, { ports: d.ports || [], mapped: ports });
    // Make and model are said for switches only, the way the rest of the
    // result speaks; a model that only repeats the name says nothing.
    const model = cls === 'switch'
      ? [d.make, d.model && d.model !== d.name ? d.model : null].filter(Boolean).join(' ') || null
      : null;
    devices.push({
      name,
      deviceId: d.deviceId,
      class: cls,
      class_label: null,
      model,
      summary: null,
      u_position: bottom,
      u_size: size,
      in_rack: d.unitTop != null,
      ports,
    });
  }

  // Far ends that are not a device read on this photo.
  const outsideName = new Map();
  const outsidePorts = new Map();
  for (const o of t.outside) {
    const sameRack = !o.rack || !here || norm(o.rack) === here;
    const name = unique(o.device);
    outsideName.set(o.key, name);
    outsidePorts.set(name, []);
    devices.push({
      name,
      deviceId: null,
      class: 'outside',
      class_label: o.rack ? (sameRack ? 'Not in the photo' : `Rack ${o.rack}`) : 'Outside the rack',
      model: o.unit != null ? `U${o.unit}` : null,
      summary: null,
      u_position: null,
      u_size: 1,
      in_rack: false,
      rack: o.rack || null,
      ports: [],
    });
  }

  const resolvePort = (deviceId, raw, label) => {
    const entry = portsById.get(deviceId);
    if (!entry) return raw == null ? '' : String(raw);
    if (label) {
      const i = entry.ports.findIndex((p) => p.label?.text && norm(p.label.text) === norm(label));
      if (i >= 0) return entry.mapped[i].name;
    }
    const n = numberOf(raw);
    if (n != null) {
      const i = entry.ports.findIndex((p) => p.portNo === n);
      if (i >= 0) return entry.mapped[i].name;
    }
    return raw == null ? '' : String(raw);
  };

  const edges = t.links.map((l) => {
    const srcName = nameById.get(l.from.deviceId);
    const srcPort = resolvePort(l.from.deviceId, l.from.port, null);
    let dst;
    if (l.to.kind === 'device') {
      dst = { device: nameById.get(l.to.deviceId), port: resolvePort(l.to.deviceId, l.to.port, l.label) };
    } else {
      const name = outsideName.get(l.to.key);
      const port = l.to.port == null ? '' : String(l.to.port);
      const list = outsidePorts.get(name);
      if (port && list && !list.includes(port)) list.push(port);
      dst = { device: name, port };
    }
    return {
      cable_id: l.id,
      cable_label: l.label || null,
      cable_type: l.type || '',
      connector: l.connector || null,
      color: l.colour || null,
      length: null,
      seen: !!l.seen,
      status: l.status || null,
      src: { device: srcName, port: srcPort },
      dst,
    };
  });

  // An other-end device lists the ports the Excel dataset names on it, state not known.
  for (const d of devices) {
    if (d.class !== 'outside') continue;
    d.ports = (outsidePorts.get(d.name) || []).map((p) => ({ name: p, label: p, kind: 'main', connected: null, is_uplink: false }));
  }

  // Racks at the far end of a cable, for the neighbouring racks in the 3D view.
  const racks = new Map();
  for (const l of t.links) {
    if (l.to.kind !== 'outside' || !l.to.rack || norm(l.to.rack) === here) continue;
    racks.set(l.to.rack, (racks.get(l.to.rack) || 0) + 1);
  }
  const neighbors = [...racks.entries()].map(([name, count], i) => ({ side: i % 2 === 0 ? 'left' : 'right', name, cable_count: count }));

  const tops = t.devices.map((d) => d.unitTop || 0);
  const uSize = Math.max(detail.unitsTotal || 0, ...tops, 1);

  return {
    rackName: detail.rackLabel || 'This rack',
    rackId: detail.scanId || '',
    u_size: uSize,
    devices,
    edges,
    neighbors,
    loose: t.loose,
    hasMatrix: !!detail.matrix,
    stats: {
      edge_count: edges.length,
      seen_count: edges.filter((e) => e.seen).length,
      device_count_in_rack: devices.filter((d) => d.in_rack).length,
    },
  };
}
