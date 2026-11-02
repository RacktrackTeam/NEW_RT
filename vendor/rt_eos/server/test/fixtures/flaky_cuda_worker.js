/**
 * Stand-in for `py -u -m pipeline.worker`, speaking the same newline-delimited
 * JSON protocol, so the pool's CUDA-recycle path can be exercised with no
 * Python, no GPU and no model weights.
 *
 * Every spawn bumps a counter in FAKE_WORKER_STATE, which is how the test tells
 * the first (poisoned) process apart from the respawn that replaces it — the
 * whole point of the fix is that a new process gets a clean context.
 *
 * FAKE_WORKER_MODE:
 *   flaky   — generation 1 returns a CUDA fault, later generations succeed
 *   always  — every generation returns a CUDA fault (a GPU that is truly down)
 *   quality — an ordinary request-level failure that must NOT recycle anything
 */

const fs = require('fs');

const stateFile = process.env.FAKE_WORKER_STATE;
const mode = process.env.FAKE_WORKER_MODE || 'flaky';

// `node --test test/` runs every .js under test/, fixtures included. Without
// FAKE_WORKER_STATE we are being executed by the runner rather than spawned as
// a worker, and there is nothing to do — the pool test spawns us explicitly.
if (!stateFile) return;

let generation = 0;
try { generation = Number(fs.readFileSync(stateFile, 'utf8')) || 0; } catch { /* first spawn */ }
generation += 1;
fs.writeFileSync(stateFile, String(generation));

// The exact text production logged, tail included — the pool must classify the
// real string, not a tidied-up version of it.
const CUDA_ERROR =
  'CUDA error: unknown error CUDA kernel errors might be asynchronously reported at some ' +
  'other API call, so the stacktrace below might be incorrect. For debugging consider ' +
  'passing CUDA_LAUNCH_BLOCKING=1';

function replyTo(req) {
  if (mode === 'quality') {
    return { id: req.id, ok: false, error: 'Please upload a clearer photo of the rack.', generation };
  }
  if (mode === 'always' || generation === 1) {
    return { id: req.id, ok: false, error: CUDA_ERROR, generation };
  }
  return { id: req.id, ok: true, generation };
}

process.stdout.write(JSON.stringify({ ready: true }) + '\n');

let buf = '';
process.stdin.on('data', (chunk) => {
  buf += chunk.toString();
  let i;
  while ((i = buf.indexOf('\n')) !== -1) {
    const line = buf.slice(0, i).trim();
    buf = buf.slice(i + 1);
    if (!line) continue;
    let req;
    try { req = JSON.parse(line); } catch { continue; }
    process.stdout.write(JSON.stringify(replyTo(req)) + '\n');
  }
});
