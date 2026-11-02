#!/usr/bin/env node
/**
 * Create a NEW organization (active) with an admin, a site, and N member
 * invites. ADDITIVE: does not touch existing orgs/users. Backs up auth.db.
 *
 *   Run:  cd server && node scripts/create-org.js "<Org Name>" [count] [<tunnelBase>]
 *     e.g. node scripts/create-org.js "THE TESTERS" 12 https://foo.trycloudflare.com
 */
'use strict';
const path = require('path');
const fs = require('fs');
const crypto = require('crypto');
const Database = require('better-sqlite3');
const bcrypt = require('bcryptjs');

const ORG_NAME = process.argv[2];
const COUNT    = Number(process.argv[3] || 12);
const BASE     = (process.argv[4] || '').replace(/\/+$/, '');
if (!ORG_NAME) { console.error('Usage: node scripts/create-org.js "<Org Name>" [count] [tunnelBase]'); process.exit(1); }

const DB_PATH = require('../lib/data_paths').AUTH_DB;
const db = new Database(DB_PATH);
db.pragma('foreign_keys = OFF');

// Backup
const stamp = new Date().toISOString().replace(/[:.]/g, '-');
fs.copyFileSync(DB_PATH, `${DB_PATH}.pre-create-org-${stamp}.bak`);

const slugBase = ORG_NAME.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'org';
const slug     = `${slugBase}-${crypto.randomBytes(2).toString('hex')}`;
const hash = (pw) => bcrypt.hashSync(pw, 10);
const owner = db.prepare("SELECT id FROM users WHERE role='owner' ORDER BY id LIMIT 1").get();
const ownerId = owner ? owner.id : null;

// No password in the code (rule 7): RT_SEED_PASSWORD, else a strong one generated here and shown once below.
const ADMIN = { username: `${slugBase}-admin`, email: `admin@${slugBase}.test`,
  password: process.env.RT_SEED_PASSWORD || `Rt-${crypto.randomBytes(6).toString('base64url')}a9Z` };
const SITE  = { name: `${ORG_NAME} Site`, slug: `${slugBase}-site-${crypto.randomBytes(2).toString('hex')}` };
const INVITE_TTL_MS = 90 * 24 * 60 * 60 * 1000;

const invites = [];
const run = db.transaction(() => {
  const org = db.prepare(
    `INSERT INTO organizations (slug, name, created_by, status) VALUES (?, ?, ?, 'active')`
  ).run(slug, ORG_NAME, ownerId);
  const orgId = org.lastInsertRowid;
  require('../lib/org_settings').applyPilotDefaults(db, orgId);

  db.prepare(
    `INSERT INTO users (email, username, password_hash, email_verified, role, organization_id)
     VALUES (?, ?, ?, 1, 'org_admin', ?)`
  ).run(ADMIN.email, ADMIN.username, hash(ADMIN.password), orgId);

  const site = db.prepare(
    `INSERT INTO tenants (slug, name, organization_id) VALUES (?, ?, ?)`
  ).run(SITE.slug, SITE.name, orgId);
  const siteId = site.lastInsertRowid;

  const expiresAt = Date.now() + INVITE_TTL_MS;
  const ins = db.prepare(
    `INSERT INTO invites (code, email, role, organization_id, tenant_id, invited_by, expires_at)
     VALUES (?, ?, 'member', ?, ?, ?, ?)`
  );
  const tag = Date.now().toString(36);
  for (let i = 1; i <= COUNT; i++) {
    const code = crypto.randomBytes(18).toString('base64url');
    const email = `${slugBase}-${tag}-${String(i).padStart(2, '0')}@racktrack.test`;
    ins.run(code, email, orgId, siteId, ownerId, expiresAt);
    invites.push({ n: i, code, link: BASE ? `${BASE}/invite/${code}` : `/invite/${code}` });
  }
  return { orgId, siteId };
});
const { orgId } = run();

console.log(`\n=== Created org "${ORG_NAME}" (id ${orgId}, slug ${slug}, active) ===`);
console.log(`Org Admin: org: ${ORG_NAME}   username: ${ADMIN.username}   password: ${ADMIN.password}`);
console.log(`Site: ${SITE.name}`);
console.log(`\n=== ${COUNT} member invite links ===`);
for (const inv of invites) console.log(`${String(inv.n).padStart(2, '0')}. ${inv.link}`);

// txt dump
const out = `${ORG_NAME} - Member Invite Links (org: ${ORG_NAME})\nEach link works once; the person sets their own username + password.\n\n`
  + invites.map(inv => `${String(inv.n).padStart(2, '0')}. ${inv.link}`).join('\n') + '\n';
const outPath = path.join(__dirname, '..', '..', `${slugBase.toUpperCase()}_INVITES.txt`);
fs.writeFileSync(outPath, out);
console.log(`\nWrote ${path.basename(outPath)}`);
db.close();
