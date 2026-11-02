/**
 * graphMail takes its whole configuration from the environment (<repo>/.env).
 * Checks isConfigured() only: no token request and no mail is ever sent.
 * The values are fake; npm test keeps the live .env out of the run.
 */
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const path = require('path');

const KEYS = ['MS_TENANT_ID', 'MS_CLIENT_ID', 'MS_CLIENT_SECRET',
  'MS_REFRESH_TOKEN_SUPPORT', 'MS_REFRESH_CLIENT_ID_SUPPORT'];

function fresh(values) {
  for (const k of KEYS) delete process.env[k];
  Object.assign(process.env, values);
  delete require.cache[require.resolve('../lib/graphMail')];
  return require('../lib/graphMail');
}

test('graphMail: not configured without a tenant', () => {
  const g = fresh({ MS_REFRESH_TOKEN_SUPPORT: 'x', MS_REFRESH_CLIENT_ID_SUPPORT: 'c' });
  assert.equal(g.isConfigured(), false);
});

test('graphMail: a support refresh token plus tenant is enough', () => {
  const g = fresh({ MS_TENANT_ID: 't', MS_REFRESH_TOKEN_SUPPORT: 'x', MS_REFRESH_CLIENT_ID_SUPPORT: 'c' });
  assert.equal(g.isConfigured(), true);
  assert.equal(g.SENDERS.support.address, process.env.MAIL_FROM_SUPPORT || 'support@racktrack.ai');
});

test('graphMail: the refresh token falls back to MS_CLIENT_ID for its client', () => {
  assert.equal(fresh({ MS_TENANT_ID: 't', MS_REFRESH_TOKEN_SUPPORT: 'x' }).isConfigured(), false);
  assert.equal(fresh({ MS_TENANT_ID: 't', MS_CLIENT_ID: 'c', MS_REFRESH_TOKEN_SUPPORT: 'x' }).isConfigured(), true);
});

test('graphMail: app-only credentials count as configured', () => {
  const g = fresh({ MS_TENANT_ID: 't', MS_CLIENT_ID: 'c', MS_CLIENT_SECRET: 's' });
  assert.equal(g.isConfigured(), true);
});

test('graphMail: reads nothing from the old token folder', () => {
  const src = fs.readFileSync(path.join(__dirname, '..', 'lib', 'graphMail.js'), 'utf8');
  assert.equal(/['"]token['"]/.test(src), false);
});

test.after(() => { for (const k of KEYS) delete process.env[k]; });
