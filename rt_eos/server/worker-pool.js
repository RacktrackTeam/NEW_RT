// Pool of long-running Python workers.
//
// Each worker is spawned with `py -u -m pipeline.worker` and keeps all YOLO
// models resident in memory. Requests travel over newline-delimited JSON:
//   stdin : {"id", "command", ...}
//   stdout: {"id", "ok", ...}
// stderr is operator logs (we forward to console.error).
//
// The pool fans requests out to the first free worker, queues when all busy,
// and auto-respawns workers that die.

const { spawn } = require('child_process');
const { EventEmitter } = require('events');
const { randomUUID } = require('crypto');
const path = require('path');

// Lazy-load observability so this module can also be required from non-server
// contexts. When present we use structured logging + worker-event counters.
let _o11y = null;
function o11y() {
  if (_o11y === null) {
    try { _o11y = require('./lib/observability'); }
    catch { _o11y = false; }
  }
  return _o11y || null;
}
function wlog(level, fields, msg) {
  const o = o11y();
  if (o) o.logger[level](fields, msg);
  else console[level === 'error' || level === 'fatal' ? 'error' : (level === 'warn' ? 'warn' : 'log')](
    `[${fields.worker !== undefined ? `worker ${fields.worker}` : 'pool'}] ${msg}`
  );
}
function wcount(event) {
  const o = o11y();
  if (o) o.metrics.workerEvents.labels(event).inc();
}

// ── Fatal (process-level) worker errors ──────────────────────────────
//
// A worker that answers {ok:false} is normally still healthy: the REQUEST
// failed, the process did not. CUDA is the exception. Once a CUDA context is
// invalidated — driver TDR reset, sleep/resume, GPU dropping off the bus — every
// later call in that process returns the same error forever. Nothing short of a
// new process recovers it.
//
// The pool used to hand such a worker straight back to the rotation, because an
// {ok:false} reply is a well-formed response and only `exit` and `timeout`
// recycled anything. So a single GPU fault became an unbroken wall of identical
// failures until someone restarted the server — production logged nine
// consecutive "CUDA error: unknown error" scans from one fault, and would have
// logged nine hundred. Worse, the four workers share one GPU, so a device-level
// fault poisons all of them at the same instant.
//
// Treat these as fatal to the process: kill the worker so the existing exit
// handler respawns it with a fresh context, and retry the request elsewhere.
const FATAL_WORKER_ERROR = new RegExp([
  'CUDA error',
  'CUDA kernel errors',
  'CUDA out of memory',
  'CUDA driver',
  'no CUDA-capable device',
  'device-side assert',
  'CUBLAS_STATUS',
  'CUDNN_STATUS',
  'cuDNN error',
  'illegal memory access',
  'unspecified launch failure',
  'misaligned address',
  'no kernel image is available',
].join('|'), 'i');

/** True when an {ok:false} error means the WORKER is unusable, not the request. */
function isFatalWorkerError(message) {
  return FATAL_WORKER_ERROR.test(String(message || ''));
}

// One retry per request: enough to ride out a poisoned worker, not enough to
// stampede the pool when the GPU is genuinely gone.
const MAX_RETRIES = 1;
// Recycle budget window. If the GPU is dead rather than merely wedged, every
// respawned worker fails the same way, and unbounded recycling would thrash the
// box reloading models (~10s each) instead of failing fast.
const RECYCLE_WINDOW_MS = 5 * 60 * 1000;

class Worker extends EventEmitter {
  constructor(pythonCmd, pythonArgs, cwd, index, env) {
    super();
    this.index = index;
    this.busy = false;
    this.ready = false;
    this.pending = new Map(); // id -> {resolve, reject}
    this.stdoutBuf = '';

    this.proc = spawn(pythonCmd, pythonArgs, { cwd, env });

    this.proc.stdout.on('data', (chunk) => this._onStdout(chunk));
    this.proc.stderr.on('data', (chunk) => {
      const s = chunk.toString().trimEnd();
      if (!s) return;
      // Python writes ALL its diagnostics to stderr, including "[worker] ready"
      // on every start. Logging the lot at warn meant a healthy boot produced
      // dozens of warnings — 56 of the 66 warnings in a 400-row sample were
      // workers announcing they had started — which buries the warnings that
      // matter and makes the Console look like something is wrong when it is
      // not. Classify by content: only genuine failures are warnings.
      // No leading \b: Python exception names are compounds, so "RuntimeError"
      // and "ConnectionRefusedError" must match. Erring toward warn is correct
      // here — a false warning is noticed and dismissed, a missed error is not.
      const looksBad = /(error|exception|traceback|failed|fatal|cannot|refused|denied|critical|warn)/i.test(s);
      wlog(looksBad ? 'warn' : 'info', { worker: index, kind: 'worker.stderr' }, s);
    });
    this.proc.on('exit', (code, signal) => {
      wcount('exit');
      wlog('warn', { worker: index, code, signal, kind: 'worker.exit' },
        `worker ${index} exited code=${code} signal=${signal}`);
      for (const { reject } of this.pending.values()) {
        reject(new Error('worker exited mid-request'));
      }
      this.pending.clear();
      this.ready = false;
      this.emit('exit', { code, signal });
    });
    this.proc.on('error', (err) => {
      wcount('spawn_error');
      wlog('error', { worker: index, err: err.message, kind: 'worker.spawn_error' },
        `worker ${index} spawn error: ${err.message}`);
      // A failed spawn (e.g. python3 not on PATH) emits 'error' and 'close' but
      // NEVER 'exit'. Without this, the dead worker stayed in the pool marked
      // not-ready, the respawn/backoff path never ran, and every queued request
      // hung forever with no response and no error — the server looked healthy
      // while /api/analyze, /api/detect and /api/select silently stopped working.
      for (const { reject } of this.pending.values()) {
        reject(new Error(`worker failed to start: ${err.message}`));
      }
      this.pending.clear();
      this.ready = false;
      this.emit('exit', { code: null, signal: null });
    });
    wcount('spawn');
  }

  _onStdout(chunk) {
    this.stdoutBuf += chunk.toString();
    let idx;
    while ((idx = this.stdoutBuf.indexOf('\n')) !== -1) {
      const line = this.stdoutBuf.slice(0, idx).trim();
      this.stdoutBuf = this.stdoutBuf.slice(idx + 1);
      if (!line) continue;

      let msg;
      try { msg = JSON.parse(line); }
      catch {
        wcount('bad_json');
        wlog('error', { worker: this.index, kind: 'worker.bad_json', line: line.slice(0, 200) },
          `bad JSON on stdout`);
        continue;
      }

      if (msg.ready === true) {
        this.ready = true;
        this.emit('ready');
        continue;
      }
      const pending = this.pending.get(msg.id);
      if (pending) {
        this.pending.delete(msg.id);
        this.busy = false;
        pending.resolve(msg);
        this.emit('free', this);
      }
    }
  }

  dispatch(command, params, timeoutMs) {
    return new Promise((resolve, reject) => {
      if (!this.ready) return reject(new Error('worker not ready'));
      const id = randomUUID();
      this.busy = true;
      let timer = null;
      const clear = () => { if (timer) { clearTimeout(timer); timer = null; } };
      // Wrap so a stdout reply (or mid-request exit) also cancels the timer.
      this.pending.set(id, {
        resolve: (v) => { clear(); resolve(v); },
        reject:  (e) => { clear(); reject(e); },
      });
      if (timeoutMs && timeoutMs > 0) {
        timer = setTimeout(() => {
          if (!this.pending.has(id)) return;
          this.pending.delete(id);
          this.busy = false;
          wcount('timeout');
          wlog('error', { worker: this.index, kind: 'worker.timeout', command, timeoutMs },
            `worker ${this.index} request '${command}' timed out after ${timeoutMs}ms — recycling worker`);
          reject(new Error(`pipeline worker timed out after ${timeoutMs}ms`));
          this.kill(); // a hung worker won't recover — kill it; the pool respawns it
        }, timeoutMs);
      }
      const payload = JSON.stringify({ id, command, ...params }) + '\n';
      this.proc.stdin.write(payload, (err) => {
        if (err) {
          clear();
          this.pending.delete(id);
          this.busy = false;
          reject(err);
        }
      });
    });
  }

  kill() {
    this.ready = false;        // prevent _drain from picking us mid-shutdown
    try { this.proc.kill('SIGTERM'); } catch { /* already dead */ }
  }
}


class WorkerPool extends EventEmitter {
  constructor({ size = 1, pythonCmd, pythonArgs, cwd, env, requestTimeoutMs }) {
    super();
    this.size = size;
    this.pythonCmd = pythonCmd;
    this.pythonArgs = pythonArgs;
    this.cwd = cwd;
    this.env = env;
    // Per-request wall-clock cap so a hung/OOM inference can't pin a request
    // (and its worker) forever. Overridable via env; 0 disables.
    this.requestTimeoutMs = requestTimeoutMs != null
      ? requestTimeoutMs
      : Number(process.env.RACKTRACK_WORKER_TIMEOUT_MS || 120000);
    this.workers = [];
    this.queue = []; // [{command, params, resolve, reject, attempts}]
    this._shuttingDown = false;
    this._crashCounts = {}; // index -> consecutive fast-exit count (backoff)
    // Timestamps of recent fatal-error recycles, and how many we allow inside
    // RECYCLE_WINDOW_MS. Two full rebuilds of the pool is enough to recover a
    // wedged GPU; past that the device itself is down and churning workers only
    // makes the box slower to say so.
    this._recycles = [];
    this._recycleBudget = Math.max(4, size * 2);

    for (let i = 0; i < size; i++) this._spawn(i);
  }

  _spawn(index) {
    if (this._shuttingDown) return;  // don't respawn after shutdown
    const w = new Worker(this.pythonCmd, this.pythonArgs, this.cwd, index, this.env);
    w._spawnedAt = Date.now();
    w.on('ready', () => {
      wcount('ready');
      wlog('info', { worker: index, kind: 'worker.ready' },
        `worker ${index} ready`);
      this._drain();
    });
    w.on('free', () => this._drain());
    w.on('exit', () => {
      this.workers = this.workers.filter(x => x !== w);
      if (this._shuttingDown) return;
      // Crash-loop guard: if a worker dies quickly (< 15s), it's likely a
      // permanent fault (missing model, bad dep, OOM). Back off exponentially
      // (2s → 30s cap) instead of respawning every 2s forever. A worker that
      // ran healthily resets the counter.
      const lived = Date.now() - (w._spawnedAt || 0);
      if (lived < 15000) this._crashCounts[index] = (this._crashCounts[index] || 0) + 1;
      else this._crashCounts[index] = 0;
      const n = this._crashCounts[index];
      const delay = Math.min(30000, 2000 * Math.pow(2, Math.max(0, n - 1)));
      if (n >= 1) {
        wlog('warn', { worker: index, crashes: n, delay, kind: 'worker.backoff' },
          `worker ${index} died fast (${n}x) — respawning in ${delay}ms`);
      }
      setTimeout(() => this._spawn(index), delay);
    });
    this.workers.push(w);
  }

  _drain() {
    while (this.queue.length > 0) {
      const free = this.workers.find(x => x.ready && !x.busy);
      if (!free) return;
      const task = this.queue.shift();
      free.dispatch(task.command, task.params, this.requestTimeoutMs)
        .then((res) => this._onResult(free, task, res), task.reject);
    }
  }

  /** Recycle budget check — trims the window, then reports headroom. */
  _mayRecycle() {
    const now = Date.now();
    this._recycles = this._recycles.filter(t => now - t < RECYCLE_WINDOW_MS);
    return this._recycles.length < this._recycleBudget;
  }

  /**
   * Inspect a worker's reply before handing it to the caller. Ordinary
   * {ok:false} answers pass straight through; a fatal device error costs the
   * worker its life (see FATAL_WORKER_ERROR) and buys the request one more
   * attempt on a healthy one.
   *
   * Always runs as a promise continuation, never synchronously inside _drain's
   * loop, so re-entering _drain() here is safe.
   */
  _onResult(worker, task, res) {
    if (!res || res.ok !== false || !isFatalWorkerError(res.error)) {
      return task.resolve(res);
    }
    wcount('fatal_error');
    const err = String(res.error || '').slice(0, 200);

    if (!this._mayRecycle()) {
      // Respawning has stopped helping, which means the GPU is down rather than
      // wedged. Surface the error instead of rebuilding workers that will only
      // fail the same way.
      wlog('error', { worker: worker.index, kind: 'worker.device_down', err },
        `worker ${worker.index} hit a fatal device error and the recycle budget is spent — ` +
        `the GPU looks down, not wedged; failing fast instead of respawning`);
      return task.resolve(res);
    }

    this._recycles.push(Date.now());
    wlog('error', { worker: worker.index, kind: 'worker.poisoned', err },
      `worker ${worker.index} hit a fatal device error — its CUDA context is unusable for the ` +
      `rest of the process; killing it so a fresh one replaces it`);
    worker.kill(); // the 'exit' handler respawns with a clean context

    if ((task.attempts || 0) >= MAX_RETRIES) return task.resolve(res);
    task.attempts = (task.attempts || 0) + 1;
    wcount('retry');
    // Front of the queue: this request has already waited once through a full
    // inference, and the caller is still holding the HTTP connection open.
    this.queue.unshift(task);
    this._drain();
  }

  request(command, params) {
    return new Promise((resolve, reject) => {
      if (this._shuttingDown) return reject(new Error('worker pool shutting down'));
      this.queue.push({ command, params, resolve, reject });
      this._drain();
    });
  }

  async shutdown() {
    this._shuttingDown = true;
    // Reject anything still queued so callers stop hanging on shutdown.
    while (this.queue.length > 0) {
      const t = this.queue.shift();
      try { t.reject(new Error('worker pool shutting down')); } catch (_) {}
    }
    for (const w of this.workers) w.kill();
  }
}

module.exports = { WorkerPool, isFatalWorkerError };
