import { describe, test, expect } from 'vitest';
import {
  STEPS, stepDone, stepLocked, progress, remaining, firstIncompleteStep, mandatoryDone, stepState,
  composeAddress, vEmail, vPhone, vUsername, vPassword, proposeUsername, siteReady, spocOf, summaryOf,
} from './orgSettings.js';

const org = { name: 'Northwind Colocation', short_code: 'NORTHWIN', timezone: 'Europe/Amsterdam', country: 'NL' };
const dc = (over = {}) => ({
  id: 11, name: 'Rotterdam DC1', datacentre: { address: '12 Harbour Road, Netherlands', timezone: 'Europe/Amsterdam' },
  approver: { user_id: 5, username: 'priya.nair', email: 'priya@northwind.example' }, rules: { accepted_at: '2026-09-14 10:12:00' },
  completeness: { mandatory: { location: true, approver: true, rules: true }, canScan: true },
  profile: { contacts: [{ name: 'Priya Nair', role: 'spoc', email: 'priya@northwind.example', phone: null }], facility: {} },
  ...over,
});

describe('the steps', () => {
  test('are in the order the stepper shows them, the closing step last', () => {
    expect(STEPS.map((s) => s.key)).toEqual(['org', 'sites', 'rules', 'review', 'done']);
    expect(STEPS.filter((s) => s.kind === 'required').map((s) => s.key)).toEqual(['org', 'sites', 'rules', 'review']);
    expect(STEPS[STEPS.length - 1].kind).toBe('done');
    expect(STEPS.filter((s) => s.kind === 'optional').length).toBe(0);
  });

  test('the closing step is only true once every required item is', () => {
    const done = { org, dcs: [dc()] };
    expect(stepDone('review', done)).toBe(true);
    expect(stepDone('done', done)).toBe(true);
    const half = { org: { ...org, country: '' }, dcs: [dc()] };
    expect(stepDone('review', half)).toBe(false);
    expect(stepDone('done', half)).toBe(false);
  });

  test('the closing step is never the one the stepper opens on', () => {
    expect(firstIncompleteStep({ org, dcs: [dc()] })).toBe('review');
    expect(firstIncompleteStep({ org, dcs: [] })).toBe('sites');
  });

  test('a site is ready when it says where it is and who its SPOC is', () => {
    expect(siteReady(dc())).toBe(true);
    expect(siteReady(dc({ datacentre: { address: null, timezone: 'Europe/Amsterdam' } }))).toBe(false);
    expect(siteReady(dc({ approver: null, completeness: { mandatory: { location: true, approver: false, rules: true } } }))).toBe(false);
  });

  test('the rules are locked until a site exists; the sites step is where one is made', () => {
    const empty = { org, dcs: [] };
    expect(stepLocked('rules', empty)).toBe(true);
    expect(stepLocked('sites', empty)).toBe(false);
    expect(stepLocked('org', empty)).toBe(false);
    expect(stepLocked('rules', { org, dcs: [dc()] })).toBe(false);
    expect(stepState('rules', empty, 'org')).toBe('locked');
    expect(stepState('org', empty, 'org')).toBe('on');
  });
});

describe('progress', () => {
  test('counts the four required items and no optional ones', () => {
    const p = progress({ org, dcs: [dc()] });
    expect(p.required.total).toBe(4);
    expect(p.required.done).toBe(4);
    expect(p.optional.total).toBe(0);
    expect(mandatoryDone({ org, dcs: [dc()] })).toBe(true);
  });

  test('a missing country keeps the organization step open', () => {
    const model = { org: { ...org, country: '' }, dcs: [dc()] };
    expect(stepDone('org', model)).toBe(false);
    expect(firstIncompleteStep(model)).toBe('org');
    expect(remaining(model).required.map((x) => x.key)).toEqual(['org']);
  });

  test('the short code is the server\'s to make, so a record without one is still complete', () => {
    expect(stepDone('org', { org: { ...org, short_code: '' }, dcs: [] })).toBe(true);
  });

  test('an organization with no site still needs everything after the profile', () => {
    const model = { org, dcs: [] };
    expect(firstIncompleteStep(model)).toBe('sites');
    expect(remaining(model).required.map((x) => x.key)).toEqual(['sites', 'spoc', 'rules']);
  });

  test('every site must be done, not just one', () => {
    const model = { org, dcs: [dc(), dc({ id: 12, name: 'Amsterdam DC2', approver: null, profile: { contacts: [] }, completeness: { mandatory: { location: true, approver: false, rules: true }, canScan: false } })] };
    expect(stepDone('sites', model)).toBe(false);
    expect(firstIncompleteStep(model)).toBe('sites');
    expect(remaining(model).required[0].label).toBe('A SPOC with an account at every site');
    expect(summaryOf('sites', model)).toContain('Rotterdam DC1: SPOC Priya Nair');
    expect(summaryOf('sites', model)).toContain('Amsterdam DC2: no SPOC account');
  });

  test('a site with no location is not done, whatever else it has', () => {
    const model = { org, dcs: [dc({ datacentre: { address: null, timezone: 'Europe/Amsterdam' } })] };
    expect(stepDone('sites', model)).toBe(false);
    expect(remaining(model).required.map((x) => x.key)).toEqual(['sites']);
    expect(summaryOf('sites', model)).toBe('Rotterdam DC1: no location');
  });

  test('the review is the first step once nothing required is missing', () => {
    expect(firstIncompleteStep({ org, dcs: [dc()] })).toBe('review');
  });

  test('the SPOC is the contact with that role; a site from before has none', () => {
    expect(spocOf(dc()).name).toBe('Priya Nair');
    expect(spocOf(dc({ profile: { contacts: [{ name: 'Door', role: 'on_site' }] } }))).toBeNull();
    expect(summaryOf('sites', { org, dcs: [dc({ profile: { contacts: [] } })] })).toBe('Rotterdam DC1: SPOC priya.nair');
  });
});

describe('field helpers', () => {
  test('the one-line address is composed from the facility fields', () => {
    expect(composeAddress({ address_line1: '12 Harbour Road', postcode: '3011 AA', country: 'NL' })).toBe('12 Harbour Road, 3011 AA, Netherlands');
    expect(composeAddress({ address_line1: 'Rack Row 1', address_line2: '', city: 'Rotterdam', region: '', postcode: '3011 AA', country: 'NL' })).toBe('Rack Row 1, Rotterdam, 3011 AA, Netherlands');
    expect(composeAddress({})).toBe('');
  });

  test('emails and phones are checked before they are sent', () => {
    expect(vEmail('priya@northwind.example')).toBeNull();
    expect(vEmail('priya@')).not.toBeNull();
    expect(vPhone('+31 10 000 0000')).toBeNull();
    expect(vPhone('call me')).not.toBeNull();
    expect(vPhone('')).toBeNull();
  });

  test('a user name is proposed from the name, else the email, and never forced', () => {
    expect(proposeUsername('Priya Nair', 'p.nair@northwind.example')).toBe('priya.nair');
    expect(proposeUsername('', 'ops@northwind.example')).toBe('ops');
    expect(proposeUsername('Jo', 'jo.k@northwind.example')).toBe('jo.k');
    expect(proposeUsername('', '')).toBe('');
    expect(vUsername('priya.nair')).toBeNull();
    expect(vUsername('p n')).not.toBeNull();
  });

  test('a password is held to the rule the server holds it to, in the same words', () => {
    expect(vPassword('Harbour@2026')).toBeNull();
    expect(vPassword('short')).toBe('Password must be at least 8 characters');
    expect(vPassword('harbour@2026')).toBe('Password must contain an uppercase letter');
    expect(vPassword('Harbour2026x')).toBe('Password must contain a special character');
    expect(vPassword('')).toBeNull();
  });
});
