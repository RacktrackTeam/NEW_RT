#!/usr/bin/env node
/**
 * Generate N fresh member invite codes for an existing organization and write
 * a plain-text links file. ADDITIVE — does not touch existing users/invites.
 *
 *   Run:  cd server && node scripts/gen-invites.js "<BASE_URL>" [count] [org-slug]
 *     e.g. node scripts/gen-invites.js https://pilot.example.com 15 eos-pilot
 *
 * The organization slug can be left out when the database holds exactly one
 * organization.
 */
'use strict';
const path = require('path');
const fs = require('fs');
const crypto = require('crypto');
const Database = require('better-sqlite3');

const BASE = (process.argv[2] || '').replace(/\/+$/, '');
const COUNT = Number(process.argv[3] || 15);
const ORG_SLUG = process.argv[4] || null;
const INVITE_TTL_MS = 90 * 24 * 60 * 60 * 1000; // 90 days

const DB_PATH = require('../lib/data_paths').AUTH_DB;
const db = new Database(DB_PATH);

let org;
if (ORG_SLUG) {
  org = db.prepare('SELECT id FROM organizations WHERE slug = ?').get(ORG_SLUG);
  if (!org) { console.error(`Org '${ORG_SLUG}' not found`); process.exit(1); }
} else {
  const all = db.prepare('SELECT id, slug FROM organizations').all();
  if (all.length !== 1) {
    console.error(`${all.length} organizations found - name one: node scripts/gen-invites.js <BASE_URL> [count] <org-slug>`);
    process.exit(1);
  }
  org = all[0];
}
const site = db.prepare('SELECT id FROM tenants WHERE organization_id = ? ORDER BY id LIMIT 1').get(org.id);
if (!site) { console.error('No site for org'); process.exit(1); }
const owner = db.prepare("SELECT id FROM users WHERE role = 'owner' ORDER BY id LIMIT 1").get()
           || db.prepare('SELECT id FROM users ORDER BY id LIMIT 1').get();

const expiresAt = Date.now() + INVITE_TTL_MS;
const ins = db.prepare(
  `INSERT INTO invites (code, email, role, organization_id, tenant_id, invited_by, expires_at)
   VALUES (?, ?, 'member', ?, ?, ?, ?)`
);
// Unique placeholder emails so a re-run never collides with an earlier batch.
const tag = Date.now().toString(36);
const links = [];
const insMany = db.transaction(() => {
  for (let i = 1; i <= COUNT; i++) {
    const code = crypto.randomBytes(18).toString('base64url');
    const email = `member-${tag}-${String(i).padStart(2, '0')}@invite.test`;
    ins.run(code, email, org.id, site.id, owner.id, expiresAt);
    links.push(BASE ? `${BASE}/invite/${code}` : `/invite/${code}`);
  }
});
insMany();

const out = links.map((l, i) => `${String(i + 1).padStart(2, '0')}. ${l}`).join('\n') + '\n';
const outPath = path.join(__dirname, '..', '..', 'testers', 'INVITES.txt');
fs.mkdirSync(path.dirname(outPath), { recursive: true });
fs.writeFileSync(outPath, out);
console.log(out);
console.log(`Wrote ${COUNT} links → ${path.basename(outPath)}`);
db.close();
