/**
 * What the server is still working on for a scan after its first answer,
 * by key ("labels:RK-1234ABCD"), so a screen can say "still reading" rather
 * than "nothing was read". Process memory only: a restart forgets it, and
 * the files on disk say what was finished.
 */
'use strict';

const keys = new Map(); // key -> started at (ms)

function mark(key) { keys.set(String(key), Date.now()); }
function clear(key) { keys.delete(String(key)); }
function is(key) { return keys.has(String(key)); }
function list() { return [...keys.keys()]; }

module.exports = { mark, clear, is, list, labelsKey: (rackId) => `labels:${String(rackId || '').toUpperCase()}` };
