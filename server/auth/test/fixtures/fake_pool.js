// A stand-in for the Python worker pool, loaded via RACKTRACK_POOL_MODULE.
//
// The point is that /api/analyze can be driven end to end - multipart upload,
// image normalisation, rack id, tenant claim, response shape - with no Python,
// no GPU and no model weights. Before the injection seam existed the pool was
// a boolean: you could switch the pipeline off, which made every AI route 500,
// but you could not replace it, so the product's primary workflow had no
// automated coverage at all.
//
// Responses are the minimum shape the server actually reads. Where a test
// needs a different answer it sets FAKE_POOL_MODE.

const fs = require('fs');
const path = require('path');

// Four units, not two, and deliberately so: the server refuses a scan that
// resolves fewer than three rack units ("move back so the whole rack fits").
// A fixture under that threshold makes the happy-path test fail on a clean
// checkout and pass on a re-run, because a cached rack skips the gate.
const DEVICES = [
  { label: 'switch', unit: 1, confidence: 0.94, bbox: [10, 10, 100, 30] },
  { label: 'server', unit: 2, confidence: 0.88, bbox: [10, 40, 100, 70] },
  { label: 'server', unit: 3, confidence: 0.91, bbox: [10, 80, 100, 110] },
  { label: 'patch_panel', unit: 4, confidence: 0.79, bbox: [10, 120, 100, 150] },
];
const UNITS = [1, 2, 3, 4];

// The real pipeline writes device_unit_map.json into output_dir, and the
// server reads the file back rather than trusting the response - the
// "no devices detected" quality gate is driven entirely by what is on disk.
// A fake that only returned a payload would be quietly rejected there, so it
// writes the file too.
function analyzeResult(outputDir) {
  const result = {
    ok: true,
    devices: DEVICES,
    units_detected: UNITS,
    rack: { unit_count: 42 },
    ports: [],
  };
  if (outputDir) {
    fs.mkdirSync(outputDir, { recursive: true });
    fs.writeFileSync(
      path.join(outputDir, 'device_unit_map.json'),
      JSON.stringify({ devices: DEVICES, units_detected: UNITS, rack: result.rack }, null, 2));
  }
  return result;
}

async function request(command, payload = {}) {
  const mode = process.env.FAKE_POOL_MODE || 'ok';

  // Lets a test assert that a server-side fault is reported as a server fault
  // rather than being disguised as a bad photo, which is how a total scan
  // outage once presented itself as a support mystery.
  if (mode === 'throw') throw new Error('fake pool: simulated worker crash');
  if (mode === 'notok') return { ok: false, error: 'fake pool: simulated pipeline failure' };

  switch (command) {
    case 'quality_check':
      return { ok: true, metrics: { blur: 0.01, note: 'fake' } };
    case 'analyze':
      return analyzeResult(payload.output_dir);
    case 'detect_only':
      return { ok: true, devices: DEVICES };
    case 'select':
      return { ok: true, device: DEVICES[0], ports: [] };
    case 'extract_best_frame':
      return { ok: true, frame_path: payload.video_path };
    case 'split_video_racks': {
      // A pan along a pair of racks: the "video" itself is the first rack's frame,
      // and its negative stands in for a second, different rack.
      const second = `${payload.video_path}.rack2.jpg`;
      await require('sharp')(payload.video_path).negate().jpeg().toFile(second);
      return { ok: true, racks: [
        { position: 1, label: 'Rack 1', best_frame_path: payload.video_path, device_count: 1, score: 0.9, frame_index: 0 },
        { position: 2, label: 'Rack 2', best_frame_path: second, device_count: 1, score: 0.8, frame_index: 5 },
      ] };
    }
    default:
      // Unknown commands succeed emptily rather than throwing: a test for one
      // route should not fail because an unrelated background call was added.
      return { ok: true };
  }
}

module.exports = { request, shutdown: () => Promise.resolve() };
