/* What a scan says about a rack, in the words and shapes the screens use. */
import { enc } from './http';

/* The tallest rack anybody builds is 58U; a bigger number is a misread. */
const MAX_RACK_UNITS = 58;

export function unitNumber(label) {
  const m = String(label ?? '').match(/\d+/);
  if (!m) return null;
  const n = Number(m[0]);
  return n >= 1 && n <= MAX_RACK_UNITS ? n : null;
}

/** ["u29","u30"] -> "U29-30"; ["u39"] -> "U39"; nothing -> "". */
export function unitsText(units = []) {
  const nums = [...new Set((units || []).map(unitNumber).filter((n) => n !== null))].sort((a, b) => a - b);
  if (!nums.length) return '';
  const lo = nums[0];
  const hi = nums[nums.length - 1];
  return lo === hi ? `U${lo}` : `U${lo}-${hi}`;
}

/** The top unit of a device, for ordering top to bottom. */
export function topUnit(dev) {
  const nums = (dev?.units || []).map(unitNumber).filter((n) => n !== null);
  return nums.length ? Math.max(...nums) : null;
}

/* Rows the pipeline adds for empty shelves; drawn nowhere and never listed. */
export const HIDDEN_TYPES = new Set(['Empty', 'Closed Unit', 'Unidentified']);

/* Kinds with ports a person looks up. */
export const PORT_TYPES = new Set(['Switch', 'Patch Panel', 'Router', 'Gateway', 'Firewall', 'PDU']);

// The kinds a person can set a device to. "Empty" and "Closed Unit" (a blank
// panel) make the unit an empty one on the rack; the value is the class name
// the server stores, the label what a person reads.
export const DEVICE_CLASS_OPTIONS = [
  'Switch', 'Patch Panel', 'Firewall', 'Router', 'Server', 'Storage', 'Load Balancer',
  'Modem', 'Controller', 'Recorder', 'Amplifier', 'Gateway', 'KVM', 'Cable Manager', 'Shelf',
  'PDU', 'PSU', 'UPS',
  { value: 'Closed Unit', label: 'Blank panel' },
  { value: 'Empty', label: 'Empty (no device)' },
  { value: 'Other', label: 'Other device' },
];
/** The options without the device's current kind (strings or {value, label}). */
export const classOptionsExcept = (current) => DEVICE_CLASS_OPTIONS.filter((c) => (typeof c === 'object' ? c.value : c) !== current);

export const PORT_COUNT_OPTIONS = [8, 12, 16, 24, 48];

export const CABLE_COLOR_OPTIONS = [
  'Black', 'Blue', 'Brown', 'Green', 'Grey', 'Orange', 'Pink', 'Red', 'White', 'Yellow', 'Violet', 'Aqua',
];

/* Values must match the server's port-type list. */
export const PORT_TYPE_OPTIONS = [
  { value: 'RJ45', label: 'RJ45' }, { value: 'SFP', label: 'SFP' }, { value: 'QSFP', label: 'QSFP' },
  { value: 'FC', label: 'FC' }, { value: 'LC', label: 'LC' }, { value: 'SC', label: 'SC' },
  { value: 'CONSOLE', label: 'Console' }, { value: 'AUX', label: 'Aux' },
  { value: 'MANAGEMENT_PORT', label: 'Management port' }, { value: 'USB_A', label: 'USB A' },
  { value: 'USB_B', label: 'USB B' }, { value: 'USB_C', label: 'USB C' },
];

export function portTypeWord(t) {
  if (!t) return '';
  if (t === 'empty') return 'Empty slot';
  const hit = PORT_TYPE_OPTIONS.find((o) => o.value === t);
  return hit ? hit.label : String(t).replace(/_/g, ' ');
}

/* The four kinds of port the pipeline counts. */
export const PORT_CATEGORIES = [
  { value: 'main', label: 'RJ45' },
  { value: 'sfp', label: 'SFP' },
  { value: 'console', label: 'Console' },
  { value: 'other', label: 'Other' },
];

export function portCount(dev, cat = 'main') {
  if (!dev) return 0;
  const v = cat === 'main' ? (dev.port_count != null ? dev.port_count : dev.ports)
    : cat === 'sfp' ? dev.sfp_ports
      : cat === 'console' ? dev.console_ports
        : cat === 'other' ? dev.other_ports : 0;
  if (Array.isArray(v)) return v.length;
  return typeof v === 'number' ? v : 0;
}

export const totalPorts = (dev) => PORT_CATEGORIES.reduce((s, c) => s + portCount(dev, c.value), 0);

export const categoriesOf = (dev) => PORT_CATEGORIES.filter((c) => portCount(dev, c.value) > 0);

/** "24 RJ45, 2 SFP" */
export function portSummary(dev) {
  return categoriesOf(dev).map((c) => `${portCount(dev, c.value)} ${c.label}`).join(', ');
}

/**
 * The devices a screen lists: every real device, with its 1-based index (the
 * number the server's port routes take) and its id for the newer routes.
 */
export function listDevices(devices = [], labels = null) {
  const reclass = new Map((labels?.reclassifications || []).map((r) => [Number(r.device_index), r]));
  return (devices || []).map((d, i) => {
    // The labels route counts devices from 0; the port routes from 1.
    const ocr = (labels?.deviceLabels || []).find((x) => Number(x.device_index) === i);
    const r = reclass.get(i);
    const cls = r?.class_name && r.class_name !== d.class_name ? r.class_name : d.class_name;
    return {
      ...d,
      class_name: cls,
      make: d.make || r?.brand || null,
      index: i + 1,
      deviceId: d.deviceId ?? d.device_id ?? d.id ?? i + 1,
      unitText: unitsText(d.units),
      labelText: ocr?.label && (ocr.conf ?? 1) >= 0.4 ? ocr.label : null,
    };
  }).filter((d) => !HIDDEN_TYPES.has(d.class_name));
}

/** What to call a device: make and model when read, else its kind. */
export function deviceName(dev) {
  if (!dev) return '';
  if (dev.model) return [dev.make, dev.model].filter(Boolean).join(' ');
  if (dev.make) return `${dev.make} ${dev.class_name || ''}`.trim();
  return dev.name || dev.class_name || 'Device';
}

/** "U39 Arista 7280R3 - sw-a1" for the tag drawn on the photo. */
export function deviceTag(dev) {
  const name = deviceName(dev);
  const label = dev?.labelText && dev.labelText !== name ? ` - ${dev.labelText}` : '';
  return [dev?.unitText, `${name}${label}`].filter(Boolean).join(' ');
}

export const isPortDevice = (dev) => !!dev && (PORT_TYPES.has(dev.class_name) || totalPorts(dev) > 0);

/** A scan's own files (contract: /api/scans/:scanId/files/...). */
export const scanFile = (scanId, name) => `/api/scans/${enc(scanId)}/files/${name}`;

/** The photo of a scan: the path the server gave, else the files route. */
export function photoOf(result, scanId) {
  return result?.photoUrl || result?.originalImageUrl || (scanId ? scanFile(scanId, 'photo.jpg') : null);
}

/** The small picture for a list: the server's thumbnail, else the photo it named. */
export function thumbOf(item) {
  if (!item) return null;
  return item.thumbUrl || item.image || (item.scanId || item.rackId ? scanFile(item.scanId || item.rackId, 'thumb.jpg') : null);
}

/** The name a person reads for a scan: the rack's name, never the RK- id. */
export function rackNameOf(item) {
  return item?.rackLabel || item?.rackName || item?.name || 'Unidentified rack';
}

export const scanIdOf = (item) => item?.scanId || item?.rackId || item?.id || null;
