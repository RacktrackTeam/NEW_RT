/**
 * Optics for a rack: for each device with fibre or SFP ports, which optic and
 * which cable fit them.
 *
 * The tables and the slot inference come from RackTrack's
 * pipeline/sfp_recommend.py (commit 625ebdce): SFP_STANDARDS,
 * CABLE_STANDARDS, infer_slot_from_model. Its web part (searching retailer
 * sites for modules and prices) is not brought back: RT-EOS answers from the
 * port type the photo read, the model read on the device, and the cable
 * types the site's patching matrix lists for that device.
 */
'use strict';

const { norm } = require('./scan_view');
const { isSwitchGear } = require('./device_lookup');

// IEEE / MSA facts, from sfp_recommend.py.
const SFP_STANDARDS = {
  SFP: { speed: '1 Gbps', optics: '1000BASE-SX (multimode) or 1000BASE-LX (single mode)' },
  'SFP+': { speed: '10 Gbps', optics: '10GBASE-SR (multimode) or 10GBASE-LR (single mode)' },
  SFP28: { speed: '25 Gbps', optics: '25GBASE-SR (multimode) or 25GBASE-LR (single mode)' },
  'QSFP+': { speed: '40 Gbps', optics: '40GBASE-SR4 (multimode, MPO) or 40GBASE-LR4 (single mode, LC)' },
  QSFP28: { speed: '100 Gbps', optics: '100GBASE-SR4 (multimode, MPO) or 100GBASE-LR4 (single mode, LC)' },
  'QSFP-DD': { speed: '400 Gbps', optics: '400GBASE-DR4 or SR8 (MPO)' },
  OSFP: { speed: '400 or 800 Gbps', optics: '400GBASE-DR4 or FR4, or 800G DR8 (MPO)' },
};
const CABLE_STANDARDS = {
  multimode: 'LC-LC duplex, OM3 or OM4 multimode (up to 300 m at 10G)',
  singlemode: 'LC-LC duplex, OS2 single mode (up to 10 km)',
  mpo: 'MPO-12 trunk, OM4 multimode (up to 150 m)',
};

/** From the model string's speed hints, as infer_slot_from_model did. */
function slotFromModel(make, model) {
  const s = `${make || ''} ${model || ''}`;
  if (/400G|QSFP-DD/i.test(s)) return 'QSFP-DD';
  if (/100G|QSFP28/i.test(s)) return 'QSFP28';
  if (/40G|QSFP\+?(?!28|DD)/i.test(s)) return 'QSFP+';
  if (/25G|SFP28/i.test(s)) return 'SFP28';
  if (/10G|SFP\+|XG/i.test(s)) return 'SFP+';
  // MikroTik and others write the cages into the model: 2S+ is two SFP+.
  if (/\dS\+/i.test(s)) return 'SFP+';
  return null;
}

const FIBRE_PORTS = new Set(['SFP', 'QSFP', 'OSFP', 'LC', 'SC', 'FC']);

function portKind(p) {
  const t = String(p.type || '').toUpperCase();
  if (FIBRE_PORTS.has(t)) return t;
  return p.kind === 'sfp' ? 'SFP' : null;
}

/** The cable types the sheet lists for one device, most used first. */
function sheetCableTypes(doc, name) {
  if (!doc || !name) return [];
  const n = new Map();
  for (const c of doc.cables || []) {
    if (norm(c.a_device) !== norm(name) && norm(c.z_device) !== norm(name)) continue;
    const t = String(c.cable_type || '').trim();
    if (t) n.set(t, (n.get(t) || 0) + 1);
  }
  return [...n.entries()].sort((a, b) => b[1] - a[1]);
}

function fibreMode(text) {
  const t = String(text || '').toUpperCase();
  if (/OS1|OS2|SMF|SINGLE/.test(t)) return 'singlemode';
  if (/OM[1-5]|MMF|MULTI/.test(t)) return 'multimode';
  if (/MPO|MTP/.test(t)) return 'mpo';
  return null;
}

/**
 * view: lib/scan_view.load (devices resolved against the sheet when there is
 * one); doc: the active matrix import or null.
 * Returns { items: [ { deviceName, portType, count, suggestion: { optic, cable, note } } ] },
 * for the switches, routers, firewalls and gateways of the rack only.
 */
function advise(view, doc, nameOf) {
  const items = [];
  for (const d of view.visible) {
    // Optics are advised for switching gear only, never a patch panel or a server.
    if (!isSwitchGear(d.type)) continue;
    const groups = new Map();
    for (const p of d.ports) {
      const k = portKind(p);
      if (k) groups.set(k, (groups.get(k) || 0) + 1);
    }
    if (!groups.size) continue;
    const name = nameOf(d);
    const types = sheetCableTypes(doc, d.sheetName);
    const sheetType = types.length ? types[0][0] : null;
    const mode = fibreMode(sheetType);
    for (const [portType, count] of groups) {
      let optic;
      let cable;
      let note;
      if (portType === 'LC' || portType === 'SC' || portType === 'FC') {
        optic = 'No optic: a passive fibre port';
        cable = sheetType || `${portType}-${portType} duplex patch cord, the same fibre type as the link`;
        note = sheetType ? `The sheet lists ${sheetType} for ${types[0][1]} ${types[0][1] === 1 ? 'cable' : 'cables'} on this device.` : 'No cable type for this device in the sheet.';
      } else {
        const slot = portType === 'OSFP' ? 'OSFP'
          : portType === 'QSFP' ? (slotFromModel(d.make, d.model) || 'QSFP+') : (slotFromModel(d.make, d.model) || 'SFP');
        const std = SFP_STANDARDS[slot] || SFP_STANDARDS.SFP;
        const pick = mode === 'singlemode' ? std.optics.split(' or ')[1] || std.optics : mode === 'multimode' ? std.optics.split(' or ')[0] : std.optics;
        optic = `${slot} ${std.speed}: ${pick}`;
        cable = sheetType || (mode ? CABLE_STANDARDS[mode] : `${CABLE_STANDARDS.multimode}; ${CABLE_STANDARDS.singlemode}`);
        note = [
          d.model ? null : 'The model was not read, so the speed is assumed from the port type.',
          sheetType ? `The sheet lists ${sheetType} for this device, so the optic follows that fibre.` : 'No cable type for this device in the sheet.',
        ].filter(Boolean).join(' ');
      }
      items.push({ deviceId: d.deviceId, deviceName: name, portType, count, suggestion: { optic, cable, note } });
    }
  }
  return { items };
}

module.exports = { advise, slotFromModel, fibreMode, SFP_STANDARDS };
