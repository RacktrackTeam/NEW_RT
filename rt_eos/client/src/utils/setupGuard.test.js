import { describe, test, expect } from 'vitest';
import { setupDecision, isSetupAdmin, setupUnknown } from './setupGuard.js';

const admin = (setup, role = 'org_admin') => ({ id: 1, role, setup });
const member = (setup, role = 'member') => ({ id: 2, role, tenant_id: 7, setup });

describe('setupDecision', () => {
  test('an admin whose organization still needs setup is sent to /setup', () => {
    expect(setupDecision(admin({ needsSetup: true, blocked: false, reason: null }))).toBe('setup');
    expect(setupDecision(admin({ needsSetup: true, blocked: false, reason: null }, 'owner'))).toBe('setup');
  });

  test('an admin whose organization is set up goes straight through', () => {
    expect(setupDecision(admin({ needsSetup: false, blocked: false, reason: null }))).toBe('ok');
    expect(setupDecision(admin({ needsSetup: false, blocked: false, reason: null }, 'owner'))).toBe('ok');
  });

  test('a member or site manager is never gated, whatever the field says', () => {
    expect(setupDecision(member({ needsSetup: false, blocked: true, reason: 'being set up' }))).toBe('ok');
    expect(setupDecision(member({ needsSetup: false, blocked: true, reason: 'being set up' }, 'site_manager'))).toBe('ok');
    expect(setupDecision(member({ needsSetup: true, blocked: false, reason: null }))).toBe('ok');
    expect(setupDecision(member({ needsSetup: true, blocked: true, reason: null }))).toBe('ok');
  });

  test('a stale blocked flag on an admin does not stop the setup redirect', () => {
    expect(setupDecision(admin({ needsSetup: true, blocked: true, reason: 'being set up' }))).toBe('setup');
    expect(setupDecision(admin({ needsSetup: false, blocked: true, reason: 'being set up' }))).toBe('ok');
  });

  test('a record without the setup field is treated as ready', () => {
    expect(setupDecision({ id: 1, role: 'org_admin' })).toBe('ok');
    expect(setupDecision({ id: 2, role: 'member', setup: null })).toBe('ok');
    expect(setupDecision({ id: 2, role: 'member', setup: 'yes' })).toBe('ok');
    expect(setupDecision(null)).toBe('ok');
    expect(setupDecision(undefined)).toBe('ok');
  });
});

describe('isSetupAdmin', () => {
  test('owner and org_admin only', () => {
    expect(isSetupAdmin({ role: 'owner' })).toBe(true);
    expect(isSetupAdmin({ role: 'org_admin' })).toBe(true);
    expect(isSetupAdmin({ role: 'site_manager' })).toBe(false);
    expect(isSetupAdmin({ role: 'member' })).toBe(false);
    expect(isSetupAdmin(null)).toBe(false);
  });
});

describe('setupUnknown', () => {
  test('true only for a signed-in record that has never carried the field', () => {
    expect(setupUnknown({ id: 1, role: 'member' })).toBe(true);
    expect(setupUnknown({ id: 1, role: 'member', setup: { needsSetup: false, blocked: false } })).toBe(false);
    expect(setupUnknown({ id: 1, role: 'member', setup: null })).toBe(false);
    expect(setupUnknown(null)).toBe(false);
  });
});
