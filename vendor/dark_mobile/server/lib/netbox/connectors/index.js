/**
 * The connector registry.
 *
 * One place that knows every target we can export to. A new CMDB is a new file
 * here and one line in ALL; nothing else in the app changes. Each module is the
 * same shape: { type, label, fields, validate, test, export }.
 */
const netbox = require('./netbox');
const rest = require('./rest');
const servicenow = require('./servicenow');
const { detect } = require('./detect');

const ALL = [netbox, rest, servicenow];
const byType = Object.fromEntries(ALL.map((c) => [c.type, c]));

/** The catalogue the UI renders: what targets exist and what each needs. */
const types = () => ALL.map((c) => ({ type: c.type, label: c.label, fields: c.fields }));

const get = (type) => byType[type] || null;

/**
 * Which of these an address is, worked out rather than asked for.
 *
 * The catalogue above says what we can talk to; this says which one somebody
 * has. It needs no credentials, so it can run while the person is still typing
 * the address, and its answer is a suggestion the sign-in then confirms.
 */
module.exports = { ALL, byType, types, get, detect };
