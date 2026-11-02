import { describe, test, expect, afterEach } from 'vitest';
import { screen, fireEvent, waitFor, within } from '@testing-library/react';
import CheckPage, { portNumber, problemSentence } from './CheckPage.jsx';
import { renderScreen, mockApi, USERS } from '../test/render.jsx';

const end = (device, port) => ({ device, port });
const CABLES = [
  { cableLabel: 'EOS-F-0141', status: 'matched', a: { expected: end('sw-a1', 'Et15'), found: end('sw-a1', 'Et15'), status: 'matched' }, z: { expected: end('PP-24 A', '3'), found: end('PP-24 A', '3'), status: 'matched' } },
  { cableLabel: 'EOS-F-0143', status: 'wrong_port', a: { expected: end('sw-a1', 'Et17'), found: end('sw-a1', 'Et18'), status: 'wrong_port' }, z: { expected: end('PP-24 A', '5'), found: end('PP-24 A', '5'), status: 'matched' } },
  { cableLabel: 'EOS-F-0202', status: 'missing_cable', a: { expected: end('sw-a1', 'Et40'), found: null, status: 'missing_cable' }, z: { expected: end('PP-24 A', '20'), found: null, status: 'missing_cable' } },
  { cableLabel: 'EOS-F-0219', status: 'not_found', a: { expected: end('sw-a1', 'Et44'), found: null, status: 'not_found' }, z: { expected: end('PP-24 A', '9'), found: end('PP-24 A', '9'), status: 'matched' } },
  { cableLabel: 'EOS-F-0999', status: 'not_in_sheet', a: { expected: null, found: null }, z: { expected: null, found: end('PP-24 B', '22'), status: 'not_in_sheet' } },
];
const CHECK = {
  id: 7, at: new Date().toISOString(), by: 'Barry', aScanId: 'RK-AAAA1111', zScanId: 'RK-BBBB2222', aRack: 'R01-A12', zRack: 'R02-B07', matrixImportId: 11,
  counts: { matched: 351, wrongPort: 14, notInSheet: 9, notFound: 6, missingCable: 3 },
  accuracy: { labelsRead: 369, labelsExpected: 384, readRate: 0.961, unitPlacementOk: 11, unitPlacementTotal: 12 },
  cables: CABLES,
};

function setLaptop(on) {
  window.matchMedia = on ? () => ({ matches: true, addEventListener() {}, removeEventListener() {} }) : undefined;
}

afterEach(() => { setLaptop(false); });

describe('CheckPage helpers', () => {
  test('portNumber reads the last number of a port name', () => {
    expect(portNumber('Et18')).toBe(18);
    expect(portNumber('Ethernet1/18')).toBe(18);
    expect(portNumber('7')).toBe(7);
    expect(portNumber('')).toBeNull();
  });
  test('each problem is one plain sentence', () => {
    expect(problemSentence(CABLES[1])).toBe('A-end: in Et18, sheet says Et17');
    expect(problemSentence(CABLES[2])).toBe('A-end: Et40 is empty');
    expect(problemSentence(CABLES[3])).toBe('A-end: label on Et44 not readable');
    expect(problemSentence(CABLES[4])).toBe('Z-end: PP-24 B 22');
  });
});

describe('CheckPage', () => {
  test('a cable printed on its tag is not a problem, and is counted', async () => {
    const onTag = { cableLabel: 'CBL-B12-03', status: 'on_tag',
      a: { expected: end('nvs-sgkc-sis-sw05b', 'Gi1-11'), found: null, status: 'not_found' },
      z: { expected: end('nvs-sgkc-sis-sw05a', 'Gi1-11'), found: { ...end('nvs-sgkc-sis-sw05a', '11'), source: 'tag', read: 'nvs-sgkc-sis-sw05a Gi1-11 B11', inRack: 'B12' }, status: 'on_tag' } };
    expect(problemSentence(onTag)).toBe('A-end: label on Gi1-11 not readable');
    expect(problemSentence({ ...onTag, a: { ...onTag.a, status: 'on_tag', found: { ...end('nvs-sgkc-sis-sw05b', '11'), inRack: 'B12' } } })).toBe("A-end: printed on the cable's tag in B12");
    mockApi({ 'GET /api/checks/8': { ...CHECK, id: 8, cables: [CABLES[0], onTag], counts: { matched: 1, onTag: 1, wrongPort: 0, notInSheet: 0, notFound: 0, missingCable: 0 } } }, USERS.site_manager);
    renderScreen(<CheckPage />, { path: '/checks/8', pattern: '/checks/:checkId', user: USERS.site_manager });
    expect(await screen.findByRole('heading', { name: 'R01-A12 to R02-B07' })).toBeTruthy();
    expect(screen.getAllByText('1').map((n) => n.parentElement.textContent)).toEqual(['1 Matched', '1 Printed on the tag']);
    expect(screen.getByText('Problems, 0')).toBeTruthy();
    expect(screen.getByText('Every cable matched the sheet.')).toBeTruthy();
  });

  test('shows the title, counts, problems and the accuracy line', async () => {
    mockApi({ 'GET /api/checks/7': CHECK }, USERS.site_manager);
    renderScreen(<CheckPage />, { path: '/checks/7', pattern: '/checks/:checkId', user: USERS.site_manager });
    expect(await screen.findByRole('heading', { name: 'R01-A12 to R02-B07' })).toBeTruthy();
    expect(screen.getByText(/^5 cables, checked \d\d:\d\d$/)).toBeTruthy();
    expect(screen.getByText('351').parentElement.textContent).toBe('351 Matched');
    expect(screen.getByText('14').parentElement.textContent).toBe('14 Wrong port');
    expect(screen.getByText('Problems, 4')).toBeTruthy();
    expect(screen.getByText('Read accuracy on this task: 96.1%')).toBeTruthy();
    // A phone shows the first three problems and See all.
    expect(screen.getByText('A-end: in Et18, sheet says Et17')).toBeTruthy();
    expect(screen.queryByText('Z-end: PP-24 B 22')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'See all' }));
    expect(screen.getByText('Z-end: PP-24 B 22')).toBeTruthy();
  });

  const SCANS = {
    'GET /api/scan/RK-AAAA1111': { rackId: 'RK-AAAA1111', originalImageUrl: '/api/scans/RK-AAAA1111/files/photo.jpg', devices: [{ class_name: 'Switch', box: [0, 0, 100, 20], units: ['u39'] }] },
    'GET /api/ocr/labels/RK-AAAA1111': { deviceLabels: [{ device_index: 0, label: 'sw-a1', conf: 0.9 }] },
    'GET /api/scan/RK-BBBB2222': { rackId: 'RK-BBBB2222', originalImageUrl: '/api/scans/RK-BBBB2222/files/photo.jpg', devices: [{ class_name: 'Patch Panel', box: [0, 0, 100, 20], units: ['u42'] }, { class_name: 'Patch Panel', box: [0, 30, 100, 50], units: ['u41'] }] },
    'GET /api/ocr/labels/RK-BBBB2222': { deviceLabels: [{ device_index: 0, label: 'PP-24 A', conf: 0.9 }, { device_index: 1, label: 'PP-24 B', conf: 0.9 }] },
  };

  test('each end is the real rack photo, and a problem outlines its device', async () => {
    mockApi({ 'GET /api/checks/7': CHECK, ...SCANS }, USERS.site_manager);
    renderScreen(<CheckPage />, { path: '/checks/7', pattern: '/checks/:checkId', user: USERS.site_manager });
    const photos = await screen.findAllByRole('img');
    expect(photos.map((i) => i.getAttribute('src')).join(' ')).toContain('/api/scans/RK-AAAA1111/files/photo.jpg');
    expect(document.querySelector('svg:not(.appIcon)')).toBeNull();
    fireEvent.click(screen.getByText('EOS-F-0143', { selector: '.mono' }));
    expect(await screen.findByText(/sw-a1 Et18/)).toBeTruthy();
  });

  test('an end with two devices gets a device switch', async () => {
    mockApi({ 'GET /api/checks/7': CHECK, ...SCANS }, USERS.site_manager);
    renderScreen(<CheckPage />, { path: '/checks/7', pattern: '/checks/:checkId', user: USERS.site_manager });
    const sel = await screen.findByLabelText('Z-end device');
    expect(sel.value).toBe('PP-24 A');
    expect(screen.queryByLabelText('A-end device')).toBeNull();
    fireEvent.change(sel, { target: { value: 'PP-24 B' } });
    expect(screen.getByLabelText('Z-end device').value).toBe('PP-24 B');
  });

  test('Download results opens the two files', async () => {
    mockApi({ 'GET /api/checks/7': CHECK }, USERS.member);
    renderScreen(<CheckPage />, { path: '/checks/7', pattern: '/checks/:checkId', user: USERS.member });
    fireEvent.click(await screen.findByRole('button', { name: 'Download results' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByRole('link', { name: 'Report for head office (PDF)' }).getAttribute('href')).toContain('/api/checks/7/report.pdf');
    expect(within(dialog).getByRole('link', { name: 'One row per cable end (CSV)' }).getAttribute('href')).toContain('/api/checks/7/export.csv');
  });

  test('Run the check again posts the same two scans and opens the new check', async () => {
    let body = null;
    mockApi({
      'GET /api/checks/7': CHECK,
      'GET /api/checks/8': { ...CHECK, id: 8, zRack: 'R02-B08' },
      'POST /api/checks': (url, init) => { body = JSON.parse(init.body); return { ...CHECK, id: 8 }; },
    }, USERS.site_manager);
    renderScreen(<CheckPage />, { path: '/checks/7', pattern: '/checks/:checkId', user: USERS.site_manager });
    fireEvent.click(await screen.findByRole('button', { name: 'More' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Run the check again' }));
    await waitFor(() => expect(body).toEqual({ aScanId: 'RK-AAAA1111', zScanId: 'RK-BBBB2222' }));
    expect(await screen.findByRole('heading', { name: 'R01-A12 to R02-B08' })).toBeTruthy();
  });

  test('a laptop lists every problem and puts the big counts up top', async () => {
    setLaptop(true);
    mockApi({ 'GET /api/checks/7': CHECK }, USERS.site_manager);
    renderScreen(<CheckPage />, { path: '/checks/7', pattern: '/checks/:checkId', user: USERS.site_manager });
    await screen.findByText('Problems, 4');
    expect(screen.getByText('Z-end: PP-24 B 22')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'See all' })).toBeNull();
    expect(screen.getByText('5 cables, checked', { exact: false })).toBeTruthy();
    expect(screen.getByText('A-end, R01-A12')).toBeTruthy();
  });

  test('a server without checks says so in one sentence', async () => {
    mockApi({}, USERS.site_manager);
    renderScreen(<CheckPage />, { path: '/checks/7', pattern: '/checks/:checkId', user: USERS.site_manager });
    expect(await screen.findByText('Label checks are not available on this server yet.')).toBeTruthy();
  });
});
