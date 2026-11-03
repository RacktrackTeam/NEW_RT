/**
 * The pool's handling of a worker whose CUDA context has died.
 *
 * Production logged nine consecutive scans failing with the identical
 * "CUDA error: unknown error" and would have logged nine hundred: the worker
 * answered {ok:false} rather than crashing, so the pool - which recycled only
 * on exit and timeout - handed the same dead context to every request that
 * followed. These tests pin the three behaviours that fix it: a poisoned worker
 * is replaced and the request retried, a genuinely dead GPU stops the churn
 * instead of respawning forever, and an ordinary request failure still costs
 * the worker nothing.
 */

const { test, after } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const os = require('node:os');

const { WorkerPool, isFatalWorkerError } = require('../worker-pool');

const FAKE_WORKER = path.join(__dirname, 'fixtures', 'flaky_cuda_worker.js');
const pools = [];

after(async () => {
  for (const p of pools) { try { await p.shutdown(); } catch { /* already down */ } }
  setImmediate(() => process.exit(0));
});

/** A pool of fake workers in the given mode. Node stands in for Python. */
function makePool(mode, size = 1) {
  const stateFile = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'rt-pool-')), 'gen');
  const pool = new WorkerPool({
    size,
    pythonCmd: process.execPath,
    pythonArgs: [FAKE_WORKER],
    cwd: path.join(__dirname, '..'),
    env: { ...process.env, FAKE_WORKER_MODE: mode, FAKE_WORKER_STATE: stateFile },
    requestTimeoutMs: 20000,
  });
  pool._stateFile = stateFile;
  pools.push(pool);
  return pool;
}

const generationsSpawned = (pool) => Number(fs.readFileSync(pool._stateFile, 'utf8')) || 0;

test('a CUDA fault recycles the worker and the retry succeeds on a fresh context', async () => {
  const pool = makePool('flaky');

  const res = await pool.request('analyze', { image_path: 'x.jpg' });

  // The caller never sees the fault: the poisoned worker was replaced and the
  // request re-ran on the new process. Before the fix this resolved ok:false,
  // and so did every scan after it.
  assert.equal(res.ok, true, 'request should succeed after the recycle');
  assert.equal(res.generation, 2, 'should have been served by the respawned worker');
  assert.equal(generationsSpawned(pool), 2, 'exactly one replacement worker');
});

test('a dead GPU stops recycling once the budget is spent, rather than churning forever', async () => {
  const pool = makePool('always');

  const res = await pool.request('analyze', { image_path: 'x.jpg' });

  // Retries are bounded, so the error surfaces instead of the request hanging
  // while the pool rebuilds workers that will all fail identically.
  assert.equal(res.ok, false);
  assert.match(res.error, /CUDA error/);
  assert.ok(generationsSpawned(pool) <= 1 + pool._recycleBudget,
    `spawns (${generationsSpawned(pool)}) should stay inside the recycle budget`);
});

test('an ordinary request failure leaves the worker in the pool', async () => {
  const pool = makePool('quality');

  const first = await pool.request('quality_check', { image_path: 'blurry.jpg' });
  const second = await pool.request('quality_check', { image_path: 'blurry.jpg' });

  assert.equal(first.ok, false);
  assert.equal(second.ok, false);
  // Same process served both: a bad photo is not a reason to throw away a
  // worker and pay the ~10s model reload.
  assert.equal(first.generation, 1);
  assert.equal(second.generation, 1);
  assert.equal(generationsSpawned(pool), 1, 'no respawn for a request-level failure');
});

test('isFatalWorkerError separates device faults from request failures', () => {
  const fatal = [
    'CUDA error: unknown error CUDA kernel errors might be asynchronously reported',
    'CUDA out of memory. Tried to allocate 2.00 GiB',
    'CUDA driver version is insufficient for CUDA runtime version',
    'RuntimeError: no CUDA-capable device is detected',
    'CUDA error: device-side assert triggered',
    'cuDNN error: CUDNN_STATUS_NOT_INITIALIZED',
    'an illegal memory access was encountered',
    'unspecified launch failure',
  ];
  for (const m of fatal) assert.ok(isFatalWorkerError(m), `should be fatal: ${m}`);

  const benign = [
    'Please upload a clearer photo of the rack',
    'device_unit_map.json missing',
    'device_index out of range',
    'could not read original image',
    'No Site chosen for this scan',
    '',
    null,
    undefined,
  ];
  for (const m of benign) assert.ok(!isFatalWorkerError(m), `should be benign: ${m}`);
});
