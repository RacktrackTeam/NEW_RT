/**
 * Where each hardware detail came from, and how sure that source was (E8).
 *
 * Every field a screen shows about a device or a port is given one
 * { source, confidence, note } so the screen can say it plainly:
 * "Make: D-Link - read from the label, 80% sure".
 *
 *   source      photo       the detector found it on the rack photo (boxes,
 *                           units, device kind, port kind, port state, cable)
 *               photo_text  text read off the photo (OCR): the make and model
 *                           on a faceplate, a label, the rack's rail label
 *               excel       the site's Excel dataset (patching matrix)
 *               datasheet   the maker's public datasheet
 *               person      a person set or corrected it
 *   confidence  0..1 for the source's own score, or null when the source has
 *               none (the Excel dataset and a person are taken as written;
 *               a unit placed by the unit grid has only its device's score).
 *               It is never made up: a field with no score says so.
 *   note        a few words on what the score is, when it is not obvious.
 *
 * The numbers used:
 *   device kind, unit, height   the device box's detection score
 *   make                        OCR match score (match_conf of
 *                               engine/ocr_devices.json: how well the text read
 *                               matched a maker), else its reading score (ocr_conf)
 *   model                       the same, when the OCR gave the model; a model
 *                               found by re-reading the raw text carries the
 *                               reading score (ocr_conf) only
 *   port kind, port state       the port box's detection score
 *   cable                       the cable classifier's score (cable_confidence)
 *   label                       the OCR score of that label
 *   rack name                   the rack naming ladder's own confidence
 */
'use strict';

const SOURCES = ['photo', 'photo_text', 'excel', 'datasheet', 'person'];

const num = (v) => (typeof v === 'number' && Number.isFinite(v) ? Math.max(0, Math.min(1, v)) : null);
const field = (source, confidence = null, note = null) => ({ source, confidence: num(confidence), ...(note ? { note } : {}) });

/**
 * The fields of one device.
 *   raw       the engine's device (device_unit_map.json)
 *   ocr       its row of engine/ocr_devices.json, or null
 *   override  its entry of engine/device_overrides.json (a person's make and model), or null
 *   make, model, labelText, labelConfidence, units   as scan_view gives them
 */
function deviceFields({ raw = {}, ocr = null, override = null, make = null, model = null, labelText = null, labelConfidence = null, units = [] } = {}) {
  const det = num(raw.confidence);
  const corrected = raw.class_name_source === 'user_corrected';
  const out = {
    type: corrected ? field('person', null, 'corrected by a person') : field('photo', det, 'detection score of the device'),
    unit: units.length ? field('photo', det, 'placed by the unit grid; detection score of the device') : null,
    height: units.length ? field('photo', det, 'units the device covers on the photo') : null,
    make: null,
    model: null,
    label: labelText ? field('photo_text', labelConfidence, 'reading score of the label') : null,
  };
  if (make) {
    if (override && override.make) out.make = field('person', null, 'entered by a person');
    else if (ocr && ocr.make) out.make = field('photo_text', num(ocr.match_conf) || num(ocr.ocr_conf), num(ocr.match_conf) ? 'how well the text read matched the maker' : 'reading score of the text');
    else out.make = field('photo_text', null);
  }
  if (model) {
    if (override && override.model) out.model = field('person', null, 'entered by a person');
    else if (ocr && ocr.model) out.model = field('photo_text', num(ocr.match_conf) || num(ocr.ocr_conf), num(ocr.match_conf) ? 'how well the text read matched the model' : 'reading score of the text');
    else if (ocr) out.model = field('photo_text', num(ocr.ocr_conf), 'found in the text read on the faceplate; reading score of that text');
    else out.model = field('photo_text', null);
  }
  return out;
}

/** The fields of one port. p: the scan_view port; raw: the engine's port; fixedType: a person's port type or null. */
function portFields({ p, raw = {}, fixedType = null } = {}) {
  const det = num(raw.confidence);
  const out = {
    type: fixedType ? field('person', null, 'corrected by a person') : p.type ? field('photo', det, 'detection score of the port') : null,
    state: p.stateSource === 'person' ? field('person', null, 'confirmed or corrected by a person')
      : p.stateSource === 'close-up' ? field('photo', null, 'seen on a close-up photo')
        : field('photo', det, 'detection score of the port'),
    cable: p.state === 'connected' && (p.cableConnector || p.cableColor) ? field('photo', num(raw.cable_confidence), 'cable classifier score') : null,
  };
  return out;
}

/** A label read on a port, and whether the Excel dataset knew it. */
function labelField(label) {
  if (!label) return null;
  return field('photo_text', label.confidence, label.match && label.match !== 'exact'
    ? 'matched to the Excel dataset after correcting the reading' : 'reading score of the label');
}

const NAMING_SOURCE = { rail_label: 'photo_text', device_labels: 'excel', layout: 'excel', person: 'person' };
const NAMING_NOTE = {
  rail_label: 'read from the rack rail label',
  device_labels: 'the labels read on the devices, looked up in the Excel dataset',
  layout: 'the layout of the devices, compared with the Excel dataset',
  person: 'named by a person',
};

/** The rack name's field from the scan's rackNaming (lib/rack_naming.js). */
function rackNameField(naming) {
  if (!naming || !naming.name) return null;
  const source = NAMING_SOURCE[naming.method] || 'person';
  return field(source, source === 'person' ? null : naming.confidence, NAMING_NOTE[naming.method] || null);
}

module.exports = { SOURCES, deviceFields, portFields, labelField, rackNameField, field };
