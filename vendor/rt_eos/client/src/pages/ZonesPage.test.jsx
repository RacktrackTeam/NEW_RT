import { describe, test, expect } from 'vitest';
import { screen, fireEvent, waitFor, within } from '@testing-library/react';
import ZonesPage, { labelTally, unitWord, startRange } from './ZonesPage.jsx';
import { renderScreen, mockApi, USERS } from '../test/render.jsx';

const ZONES = {
  devices: [
    {
      deviceId: 'd1', name: 'Arista 7280R3 - sw-a1', unit: 39, portCount: 48,
      zones: [
        { from: 1, to: 12, done: true, zoneId: 'z1' },
        { from: 13, to: 24, done: true, zoneId: 'z2' },
        { from: 25, to: 36, done: false },
        { from: 37, to: 48, done: false },
      ],
    },
    { device_id: 'd2', name: 'PP-24 A', unit: 'u42', port_count: 24, zones: [{ from: 1, to: 6, done: false }, { from: 7, to: 12, done: false }] },
  ],
  remaining: 4,
};
const LABELS = Array.from({ length: 13 }, (_, i) => ({ text: i === 12 ? '' : `EOS-F-02${i}`, confidence: 0.9, portNo: 25 + i, box: [0, 0, 1, 1] }));

const base = (extra = {}) => ({
  'GET /api/scans/RK-1/zones': ZONES,
  'GET /api/scan/RK-1': { rack: { label: 'R01-A12' } },
  ...extra,
});

const open = (path = '/results/RK-1/zones') => renderScreen(<ZonesPage />, { path, pattern: '/results/:scanId/zones', user: USERS.member });

describe('ZonesPage helpers', () => {
  test('unitWord and labelTally', () => {
    expect(unitWord(39)).toBe('U39');
    expect(unitWord('u42')).toBe('U42');
    expect(unitWord(null)).toBe('');
    expect(labelTally(LABELS)).toEqual({ read: 12, unclear: 1 });
    expect(labelTally([{ text: 'X', confidence: 0.3 }])).toEqual({ read: 0, unclear: 1 });
  });
});

describe('ZonesPage', () => {
  const pick = async (name = /Arista 7280R3 - sw-a1, U39/) => fireEvent.click(await screen.findByRole('button', { name }));

  test('asks which device first, with how many zones each has taken', async () => {
    mockApi(base(), USERS.member);
    open();
    expect(await screen.findByText('Which device?')).toBeTruthy();
    expect(screen.getByText('2 of 4')).toBeTruthy();
    expect(screen.getByText('0 of 2')).toBeTruthy();
    await waitFor(() => expect(screen.getByText('R01-A12')).toBeTruthy());
  });

  test('a device opens on its first zone not taken, with the steps and both ways to take it', async () => {
    mockApi(base(), USERS.member);
    open();
    await pick();
    expect(await screen.findByText('Ports 25 to 36')).toBeTruthy();
    expect(screen.getByText('Stand 20 to 30 cm from the device.')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Take close-up of 25-36' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Upload a photo' })).toBeTruthy();
    expect(screen.getByText('2 of 4 zones taken.')).toBeTruthy();
  });

  test('a close-up posts the photo with the device and range, then moves on', async () => {
    let form = null;
    mockApi(base({
      'POST /api/scans/RK-1/zones': (url, init) => { form = init.body; return { zoneId: 'z3', photoUrl: '/x.jpg', labels: LABELS, ports: [] }; },
    }), USERS.member);
    open();
    await pick();
    await screen.findByText('Ports 25 to 36');
    const photo = new File(['jpg'], 'close.jpg', { type: 'image/jpeg' });
    fireEvent.change(screen.getByTestId('zone-file'), { target: { files: [photo] } });
    await waitFor(() => expect(form).not.toBeNull());
    expect(form.get('image').name).toBe('close.jpg');
    expect(form.get('deviceId')).toBe('d1');
    expect(form.get('from')).toBe('25');
    expect(form.get('to')).toBe('36');
    expect(await screen.findByText('12 labels read, 1 not clear.')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Take close-up of 37-48' })).toBeTruthy();
    expect(screen.getByText('3 of 4 zones taken. 12 labels read, 1 not clear.')).toBeTruthy();
  });

  test('a taken zone can be removed from the strip', async () => {
    let deleted = null;
    mockApi(base({ 'DELETE /api/scans/RK-1/zones/*': (url) => { deleted = url; return { ok: true }; } }), USERS.member);
    open();
    await pick();
    fireEvent.click(await screen.findByRole('button', { name: 'Ports 1 to 12, taken' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByRole('button', { name: 'Take it again' })).toBeTruthy();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Remove this close-up' }));
    await waitFor(() => expect(deleted).toBe('/api/scans/RK-1/zones/z1'));
    expect(await screen.findByText('Close-up removed.')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Take close-up of 1-12' })).toBeTruthy();
    expect(screen.getByText('1 of 4 zones taken.')).toBeTruthy();
  });

  test('Change picks another device and range, and refuses a bad one', async () => {
    mockApi(base(), USERS.member);
    open();
    await pick();
    fireEvent.click(await screen.findByRole('button', { name: 'Change ports' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Device' }));
    const list = await screen.findByRole('dialog', { name: 'Choose a device' });
    fireEvent.click(within(list).getAllByRole('button').find((b) => b.textContent.includes('PP-24 A')));
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Choose a device' })).toBeNull());
    fireEvent.change(within(dialog).getByLabelText('From'), { target: { value: '7' } });
    fireEvent.change(within(dialog).getByLabelText('To'), { target: { value: '30' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Use this range' }));
    expect(within(dialog).getByRole('alert').textContent).toBe('This device has 24 ports. Enter a last port of 24 or less.');
    fireEvent.change(within(dialog).getByLabelText('To'), { target: { value: '12' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Use this range' }));
    expect(await screen.findByText('PP-24 A, U42')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Take close-up of 7-12' })).toBeTruthy();
  });

  test('?device= opens that device straight away', async () => {
    mockApi(base(), USERS.member);
    open('/results/RK-1/zones?device=d2');
    expect(await screen.findByText('PP-24 A, U42')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Take close-up of 1-6' })).toBeTruthy();
  });

  test('?device=&from=&to= opens the zone that holds that port, taken or not', async () => {
    mockApi(base(), USERS.member);
    const { unmount } = open('/results/RK-1/zones?device=d1&from=5&to=5');
    expect(await screen.findByRole('button', { name: 'Take close-up of 1-12' })).toBeTruthy();
    unmount();
    mockApi(base(), USERS.member);
    open('/results/RK-1/zones?device=d1&from=40&to=40');
    expect(await screen.findByRole('button', { name: 'Take close-up of 37-48' })).toBeTruthy();
  });

  test('startRange: the zone holding the ports, the ports alone, or the first open zone', () => {
    const dev = { portCount: 48, zones: [{ from: 1, to: 12, done: true }, { from: 25, to: 36, done: false }] };
    expect(startRange(dev, { from: 3, to: 3 })).toEqual({ from: 1, to: 12 });
    expect(startRange(dev, { from: 30, to: 30 })).toBeNull();
    expect(startRange(dev, { from: 14, to: 14 })).toEqual({ from: 14, to: 14 });
    expect(startRange(dev, { from: 50, to: 50 })).toBeNull();
    expect(startRange(dev, null)).toBeNull();
  });

  test('Choose another device goes back to the list', async () => {
    mockApi(base(), USERS.member);
    open('/results/RK-1/zones?device=d2');
    fireEvent.click(await screen.findByRole('button', { name: 'Choose another device' }));
    expect(await screen.findByText('Which device?')).toBeTruthy();
  });

  test('no devices, and a server without close-ups, each say one sentence', async () => {
    mockApi(base({ 'GET /api/scans/RK-1/zones': { devices: [], remaining: 0 } }), USERS.member);
    const { unmount } = open();
    expect(await screen.findByText('No devices with ports were read on this rack.')).toBeTruthy();
    unmount();
    mockApi({}, USERS.member);
    open();
    expect(await screen.findByText('Close-ups are not available on this server yet.')).toBeTruthy();
  });
});
