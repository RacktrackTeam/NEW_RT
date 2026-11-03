/**
 * The Python interpreter the server starts for the scan pipeline.
 *
 * One answer for every caller (the worker pool, the one-shot pipeline scripts
 * and the image intake), so `npm run dev` works with no environment variable:
 *
 *   1. PYTHON_PATH, when set
 *   2. <root>/_cache/venv/bin/python, when that file exists
 *   3. python3 on PATH
 */
const fs = require('fs');
const path = require('path');

const REPO_ROOT = path.resolve(__dirname, '..', '..', '..');

let _resolved = null;

function pythonCmd() {
  if (_resolved) return _resolved;
  if (process.env.PYTHON_PATH) {
    _resolved = process.env.PYTHON_PATH;
    return _resolved;
  }
  const venv = path.join(REPO_ROOT, '_cache', 'venv', 'bin', 'python');     // RT-EOS's environment
  _resolved = fs.existsSync(venv) ? venv : 'python3';
  return _resolved;
}

module.exports = { pythonCmd };
