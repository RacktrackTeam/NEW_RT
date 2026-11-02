import { describe, test, expect, vi, beforeEach } from 'vitest';
import { screen, fireEvent, waitFor, within } from '@testing-library/react';
import ScanPage from './ScanPage.jsx';
import ResultPage from './ResultPage.jsx';
import DevicePage from './DevicePage.jsx';
import MapPage from './MapPage.jsx';
import PairPage from './PairPage.jsx';
import { SwitchGroups, useScanSwitches } from '../components/SwitchInfo.jsx';
import { cablesBetween, findEnd, pairFlags } from '../utils/pair';
import { flagRows, findOtherEnd } from './result/Tabs.jsx';
import PortPage, { portProblem } from './PortPage.jsx';
import { mockApi, renderScreen, USERS } from '../test/render.jsx';

vi.mock('../utils/validateMedia', () => ({ validateMedia: vi.fn(async () => ({ ok: true })) }));
vi.mock('@capacitor/core', () => ({ Capacitor: { isNativePlatform: () => false, getPlatform: () => 'web' } }));
// No WebGL in jsdom: the 3D scenes stand in as plain elements that show what they were given.
vi.mock('./result/RackTopologyScene3D.jsx', () => ({
  default: ({ topo }) => <div data-testid="rack-scene-3d" data-rack={topo.rackName} />,
  PairTopologyScene3D: ({ topoA, topoZ, plan, onOpenRack }) => (
    <div data-testid="pair-scene-3d" data-racks={`${topoA.rackName}|${topoZ.rackName}`} data-cables={plan.map((c) => c.cableLabel).join('|')}>
      <button type="button" onClick={() => onOpenRack('z')}>Tap the second rack frame</button>
    </div>
  ),
}));
import { validateMedia } from '../utils/validateMedia';

const SITES = { ok: true, preselect: 2, sites: [{ id: 2, name: 'Rack Build Facility', racks: [{ id: 9, rackId: 'RK-OLD', name: 'R01-A12' }] }] };
const RESULT = {
  rackId: 'RK-1', scanId: 'RK-1', timestamp: new Date().toISOString(), originalImageUrl: '/api/scans/RK-1/files/photo.jpg',
  devices: [
    { class_name: 'Patch Panel', box: [10, 10, 500, 60], units: ['u42'], confidence: 0.95, port_count: 24 },
    { class_name: 'Switch', box: [10, 80, 500, 130], units: ['u39'], confidence: 0.61, port_count: 48, sfp_ports: 4 },
    { class_name: 'Empty', box: [10, 140, 500, 180], units: ['u30'], confidence: 0.3 },
  ],
};
const photo = (name) => new File(['x'], name, { type: 'image/jpeg' });

beforeEach(() => {
  URL.createObjectURL = () => 'blob:x';
  URL.revokeObjectURL = () => {};
  validateMedia.mockClear(); validateMedia.mockImplementation(async () => ({ ok: true })); });

const analyse = (name = /^Analyse/) => fireEvent.click(screen.getByRole('button', { name }));

/* A port card's two cable ends, as text. */
const ends = (card) => Array.from(card.querySelectorAll('[data-end]')).map((e) => e.textContent);

/* The device picker: a button labelled Device that opens a sheet of devices. */
const devicePicker = () => screen.getByRole('button', { name: 'Device' });
const chooseDevice = async (name, scope) => {
  fireEvent.click(scope ? within(scope).getByRole('button', { name: 'Device' }) : devicePicker());
  const sheet = await screen.findByRole('dialog', { name: 'Choose a device' });
  fireEvent.click(within(sheet).getAllByRole('button').find((b) => b.textContent.includes(name)));
  await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Choose a device' })).toBeNull());
};

describe('Scan', () => {
  test('a chosen photo is shown first, and read only when Analyse is pressed', async () => {
    const api = mockApi({ 'GET /api/scan-sites': SITES, 'POST /api/analyze': { rackId: 'RK-9', devices: [] } }, USERS.member);
    renderScreen(<ScanPage />, { path: '/scan', extra: [['/results/:id', <p key="r">Result screen</p>]] });
    expect(await screen.findByText('No photo yet')).toBeTruthy();
    expect(screen.getByRole('tab', { name: 'Photo' }).getAttribute('aria-selected')).toBe('true');
    expect(screen.getByRole('button', { name: 'Analyse' }).disabled).toBe(true);
    fireEvent.change(document.querySelector('input[type=file]'), { target: { files: [photo('rack.jpg')] } });
    expect(await screen.findByAltText('Rack, part 1')).toBeTruthy();
    expect(api.calls.some((c) => c.path === '/api/analyze')).toBe(false);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Analyse' }).disabled).toBe(false));
    analyse();
    expect(await screen.findByText('Result screen')).toBeTruthy();
    const call = api.calls.find((c) => c.path === '/api/analyze');
    expect(call.body.get('image').name).toBe('rack.jpg');
    expect(call.body.get('siteId')).toBe('2');
    expect(call.body.get('clientJobId')).toBeTruthy();
  });

  test('while it reads: the real photo, one stage line and the bar, no Cancel, and Done on the answer', async () => {
    let answer;
    mockApi({ 'GET /api/scan-sites': SITES, 'POST /api/analyze': () => new Promise((r) => { answer = r; }) }, USERS.member);
    renderScreen(<ScanPage />, { path: '/scan', extra: [['/results/:id', <p key="r">Result screen</p>]] });
    await screen.findByText('No photo yet');
    fireEvent.change(document.querySelector('input[type=file]'), { target: { files: [photo('rack.jpg')] } });
    await waitFor(() => expect(screen.getByRole('button', { name: 'Analyse' }).disabled).toBe(false));
    analyse();
    const view = await screen.findByRole('dialog', { name: 'Analysing the rack' });
    expect(within(view).getByAltText('Rack, being analysed').getAttribute('src')).toBe('blob:x');
    expect(within(view).getByRole('status').textContent).toBe('Finding the rack units');
    expect(within(view).getByRole('img', { name: 'RackTrack' }).getAttribute('src')).toBe('/logo.jpg');
    expect(within(view).getByRole('progressbar')).toBeTruthy();
    expect(view.textContent).not.toMatch(/minute|second|takes/i);
    expect(view.querySelector('svg, canvas')).toBeNull();
    expect(within(view).queryByRole('button')).toBeNull();
    // No rack height is claimed while it reads: no 42 on the rail.
    expect(view.textContent).not.toMatch(/\b42\b/);
    answer({ rackId: 'RK-9' });
    await waitFor(() => expect(within(view).getByRole('status').textContent).toBe('Done'));
    expect(await screen.findByText('Result screen')).toBeTruthy();
  });

  test('a photo that may not read well asks, and can be analysed anyway', async () => {
    validateMedia.mockImplementation(async () => ({ ok: false, retryable: true, error: 'The photo looks blurry.' }));
    const api = mockApi({ 'GET /api/scan-sites': SITES, 'POST /api/analyze': { rackId: 'RK-9' } }, USERS.member);
    renderScreen(<ScanPage />, { path: '/scan', extra: [['/results/:id', <p key="r">Result screen</p>]] });
    await screen.findByText('No photo yet');
    fireEvent.change(document.querySelector('input[type=file]'), { target: { files: [photo('a.jpg')] } });
    await waitFor(() => expect(screen.getByRole('button', { name: 'Analyse' }).disabled).toBe(false));
    analyse();
    const dialog = await screen.findByRole('dialog', { name: 'This may not read well' });
    expect(within(dialog).getByText('The photo looks blurry.')).toBeTruthy();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Analyse it anyway' }));
    await screen.findByText('Result screen');
    expect(api.calls.find((c) => c.path === '/api/analyze').body.get('skipQualityCheck')).toBe('1');
  });

  test('stitch collects 2 to 8 photos and sends them together', async () => {
    const api = mockApi({ 'GET /api/scan-sites': SITES, 'POST /api/stitch': { rackId: 'RK-7' } }, USERS.member);
    renderScreen(<ScanPage />, { path: '/scan', extra: [['/results/:id', <p key="r">Result screen</p>]] });
    await screen.findByText('No photo yet');
    fireEvent.click(screen.getByRole('tab', { name: 'Stitch' }));
    fireEvent.change(document.querySelector('input[type=file]'), { target: { files: [photo('top.jpg'), photo('bottom.jpg')] } });
    await waitFor(() => expect(screen.getByRole('button', { name: 'Analyse 2 photos' }).disabled).toBe(false));
    analyse('Analyse 2 photos');
    await screen.findByText('Result screen');
    expect(api.calls.find((c) => c.path === '/api/stitch').body.getAll('images').map((f) => f.name)).toEqual(['top.jpg', 'bottom.jpg']);
  });

  test('a video goes to the video reader and opens the first rack', async () => {
    const api = mockApi({ 'GET /api/scan-sites': SITES, 'POST /api/analyze-video': { ok: true, racks: [{ rackId: 'RK-A' }, { rackId: 'RK-B' }] } }, USERS.member);
    renderScreen(<ScanPage />, { path: '/scan', extra: [['/results/:id', <p key="r">Result screen</p>]] });
    await screen.findByText('No photo yet');
    fireEvent.click(screen.getByRole('tab', { name: 'Video' }));
    fireEvent.change(document.querySelector('input[type=file]'), { target: { files: [new File(['v'], 'pan.mp4', { type: 'video/mp4' })] } });
    await waitFor(() => expect(screen.getByRole('button', { name: 'Analyse the video' }).disabled).toBe(false));
    analyse('Analyse the video');
    await screen.findByText('Result screen');
    expect(api.calls.find((c) => c.path === '/api/analyze-video').body.get('video').name).toBe('pan.mp4');
  });
});

/* The detail route's answer for RK-1: one switch at U39 in rack R01, 2U. */
const port = (portNo, extra) => ({ portNo, kind: 'main', type: 'SFP', state: 'empty', stateSource: 'photo', cableConnector: null, cableColor: null, label: null, expected: null, status: 'empty', uncapped: null, ...extra });
const DETAIL = {
  scanId: 'RK-1', rackLabel: 'R01', siteId: 2, takenAt: new Date().toISOString(), by: 'mandar', zones: 1,
  matrix: { importId: 'm1', cableCount: 3 },
  units: [
    { unit: 42, kind: 'device', deviceId: 1, expected: 'PP-A01', expectedStatus: 'ok' },
    { unit: 41, kind: 'empty', deviceId: null, expected: null, expectedStatus: null },
    { unit: 40, kind: 'device', deviceId: 2, expected: null, expectedStatus: null },
    { unit: 39, kind: 'device', deviceId: 2, expected: 'SW-CORE-01', expectedStatus: 'ok' },
  ],
  devices: [
    { deviceId: 1, type: 'Patch Panel', name: 'PP-A01', units: [42], unitTop: 42, unitBottom: 42, heightU: 1, make: null, model: null, confidence: 0.95, sheetName: 'PP-A01', sheetStatus: 'ok', counts: { ports: 0, connected: 0, empty: 0, unknown: 0 }, ports: [] },
    { deviceId: 2, type: 'Switch', name: 'SW-CORE-01', units: [39, 40], unitTop: 40, unitBottom: 39, heightU: 2, make: 'Arista', model: '7280R3', confidence: 0.61, sheetName: 'SW-CORE-01', sheetStatus: 'ok', counts: { ports: 3, connected: 1, empty: 2, unknown: 0 },
      fields: { type: { source: 'photo', confidence: 0.61 }, make: { source: 'photo_text', confidence: 0.82 } },
      ports: [
        port(1, { state: 'connected', cableConnector: 'LC', cableColor: 'Aqua', label: { text: 'FC-001', source: 'close-up' }, expected: { cableLabel: 'FC-001', cableType: 'LC-LC OM4', end: 'a', other: { device: 'PP-B01', port: '1', rack: 'R02', unit: 30 } }, status: 'matched' }),
        port(2, { expected: { cableLabel: 'FC-002', cableType: 'LC-LC OM4', end: 'a', other: { device: 'PP-B01', port: '2', rack: 'R02', unit: 30 } }, status: 'missing_cable' }),
        port(3, { uncapped: { markedBy: 'mandar' }, status: 'uncapped' }),
      ] },
  ],
  cables: [{ label: 'FC-001', from: { deviceId: 2, device: 'SW-CORE-01', unit: 40, portNo: 1 }, to: { device: 'PP-B01', port: '1', rack: 'R02' }, type: 'LC-LC OM4', colour: 'Aqua', status: 'matched' }],
  counts: { units: 4, devices: 2, ports: 3, connected: 1, empty: 2, unknown: 0, cables: 1, labelsRead: 1 },
  flags: { wrongUnit: 0, missingCable: 1, unexpectedCable: 0, uncapped: 1 },
};
const SHEET = { rackLabel: 'R01', matrixImportId: 'm1', units: [], missing: [{ deviceId: 2, deviceName: 'SW-CORE-01', portNo: 2, cableLabel: 'FC-002', otherEnd: { device: 'PP-B01', port: '2', rack: 'R02', unit: 30 } }], unexpected: [], uncapped: [{ deviceId: 2, deviceName: 'SW-CORE-01', portNo: 3, markedBy: 'mandar' }] };
const RESULT_API = { 'GET /api/scan/RK-1': RESULT, 'GET /api/scans/RK-1/detail': DETAIL, 'GET /api/scans/RK-1/sheet-check': SHEET, 'GET /api/scan-sites': SITES };
const openResult = (path = '/results/RK-1', user = USERS.member) => renderScreen(<ResultPage />, { path, pattern: '/results/:scanId', user, extra: [['/results/:scanId/device/:deviceId', <p key="d">Device card</p>]] });

describe('Rack result', () => {
  test('overview is a port finder: choose the device, choose the port, and see what is on it', async () => {
    mockApi(RESULT_API, USERS.site_manager);
    openResult('/results/RK-1', USERS.site_manager);
    expect(await screen.findByRole('heading', { level: 1, name: 'R01' })).toBeTruthy();
    const img = screen.getByAltText(/rack photo/);
    expect(img.getAttribute('src')).toContain('/api/scans/RK-1/files/photo.jpg');
    // No summary line of counts under the photo.
    expect(await screen.findByLabelText('Device')).toBeTruthy();
    expect(screen.queryByText(/ports connected/)).toBeNull();
    expect(screen.queryByRole('link', { name: 'Download PDF' })).toBeNull();
    // No "Find a port" heading; reading cable labels is one link row away.
    expect(screen.queryByRole('heading', { name: 'Find a port' })).toBeNull();
    expect(screen.getByRole('link', { name: /Read cable labels/ }).getAttribute('href')).toBe('/results/RK-1/zones');
    // The device is chosen from a sheet, one row per device, top unit first.
    const pick = devicePicker();
    expect(pick.getAttribute('data-value')).toBe('');
    expect(pick.textContent).toMatch(/Choose a device/);
    fireEvent.click(pick);
    const sheet = await screen.findByRole('dialog', { name: 'Choose a device' });
    const rows = within(sheet).getAllByRole('button').filter((b) => b.hasAttribute('aria-pressed'));
    expect(rows.map((b) => b.textContent)).toEqual([
      'U42PP-A01Patch Panel · no ports read', 'U39-40SW-CORE-01Switch · Arista 7280R3 · 3 ports',
    ]);
    expect(rows.every((b) => b.getAttribute('aria-pressed') === 'false')).toBe(true);
    fireEvent.click(rows[1]);
    await waitFor(() => expect(devicePicker().getAttribute('data-value')).toBe('2'));
    expect(devicePicker().textContent).toMatch(/^U39-40SW-CORE-01/);
    // No grid of every port: one kind here, so just its number, then Find.
    expect(await screen.findByPlaceholderText('1 to 3')).toBeTruthy();
    expect(screen.queryByRole('group', { name: 'Ports of SW-CORE-01' })).toBeNull();
    const num = screen.getByLabelText('SFP port number');
    fireEvent.change(num, { target: { value: '9' } });
    fireEvent.click(screen.getByRole('button', { name: 'Find' }));
    expect(await screen.findByText('This switch has SFP ports 1-3.')).toBeTruthy();
    // A connected port: its status, the cable from this end to the other, one line of facts.
    fireEvent.change(num, { target: { value: '1' } });
    fireEvent.click(screen.getByRole('button', { name: 'Find' }));
    let card = await screen.findByRole('region', { name: 'Port 1 of SW-CORE-01' });
    expect(within(card).getByText('Matched')).toBeTruthy();
    expect(ends(card)).toEqual(['This endSW-CORE-01Port 1', 'Other endPP-B01Port 1, R02']);
    // The label on the cable line and in the facts; the reader's facts as rows.
    expect(within(card).getAllByText('FC-001').length).toBe(2);
    const rowsOf = (el) => Array.from(el.querySelectorAll('dl > div')).map((r) => [r.querySelector('dt').textContent, r.querySelector('dd').textContent]);
    expect(rowsOf(card)).toEqual(expect.arrayContaining([['Port', 'SFP'], ['On the photo', 'Connected, LC, Aqua'], ['Label read', 'FC-001'], ['This end', 'SW-CORE-01, port 1'], ['Other end', 'PP-B01 port 1, R02']]));
    expect(within(card).queryByText('Missing cable')).toBeNull();
    expect(card.textContent).not.toMatch(/Cable label in the Excel|Not clear on the photo|Excel dataset has|Other end not known/);
    expect(within(card).queryByRole('link')).toBeNull();
    // An empty port the Excel dataset has a cable on: the Missing cable badge, no sentence.
    fireEvent.change(screen.getByLabelText('SFP port number'), { target: { value: '2' } });
    fireEvent.submit(screen.getByLabelText('SFP port number').closest('form'));
    card = await screen.findByRole('region', { name: 'Port 2 of SW-CORE-01' });
    expect(within(card).getByText('Missing cable')).toBeTruthy();
    expect(ends(card)).toEqual(['This endSW-CORE-01Port 2', 'Other endPP-B01Port 2, R02']);
    expect(within(card).getAllByText('FC-002').length).toBe(2);
    // Previous and Next step through the ports of the same kind.
    fireEvent.click(within(card).getByRole('button', { name: 'Next port' }));
    expect(await screen.findByRole('region', { name: 'Port 3 of SW-CORE-01' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Previous port' }));
    card = await screen.findByRole('region', { name: 'Port 2 of SW-CORE-01' });
    expect(card.textContent).not.toMatch(/Excel dataset has|lists cable|Find on the photo|port lookup/);
    // No dust-cap marks to tick: the person is not asked.
    expect(within(card).queryByLabelText(/^Uncapped/)).toBeNull();
    expect(within(card).queryByLabelText(/Optic fitted without cap/)).toBeNull();
    // Report a problem is a row of its own.
    expect(screen.getByRole('button', { name: 'Report a problem' })).toBeTruthy();
    // The flags are one line away: it opens the Flags tab.
    expect(await screen.findByRole('button', { name: /flags? on this rack/ })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Rack actions' }));
    const more = await screen.findByRole('dialog', { name: 'R01' });
    expect(within(more).queryByText('Share the photo')).toBeNull();
    expect(within(more).getByText('Take close-ups')).toBeTruthy();
    expect(document.querySelector('svg:not(.appIcon)')).toBeNull();
  });

  test('something wrong: what is wrong, then the one thing to put right, through the correction routes', async () => {
    const api = mockApi({ ...RESULT_API, 'PUT /api/scans/RK-1/devices/2/unit': { ok: true }, 'PUT /api/scans/RK-1/ports/state': { ok: true }, 'POST /api/support/contact': { ok: true } }, USERS.member);
    openResult();
    fireEvent.click(await screen.findByRole('button', { name: 'Report a problem' }));
    let sheet = await screen.findByRole('dialog', { name: 'Report a problem' });
    expect(within(sheet).getAllByRole('button').map((b) => b.querySelector('span')?.textContent).filter(Boolean)).toEqual(['Device details', 'Rack unit', 'Port status', 'Rack name', 'Other issue']);
    fireEvent.click(within(sheet).getByText('Rack unit'));
    sheet = await screen.findByRole('dialog', { name: 'Rack unit' });
    await chooseDevice('SW-CORE-01', sheet);
    fireEvent.change(within(sheet).getByLabelText('Top unit'), { target: { value: '38' } });
    fireEvent.click(within(sheet).getAllByRole('button', { name: 'Save' }).at(-1));
    await waitFor(() => expect(api.calls.some((c) => c.path === '/api/scans/RK-1/devices/2/unit')).toBe(true));
    expect(JSON.parse(api.calls.find((c) => c.path === '/api/scans/RK-1/devices/2/unit').body)).toEqual({ unitTop: 38 });
    expect(await screen.findByText('Correction saved.')).toBeTruthy();
    // A port: device, type, number, then plugged in or empty.
    fireEvent.click(screen.getByRole('button', { name: 'Report a problem' }));
    fireEvent.click(within(await screen.findByRole('dialog', { name: 'Report a problem' })).getByText('Port status'));
    sheet = await screen.findByRole('dialog', { name: 'Port status' });
    await chooseDevice('SW-CORE-01', sheet);
    fireEvent.change(within(sheet).getByLabelText('SFP port number'), { target: { value: '3' } });
    fireEvent.click(within(sheet).getByRole('button', { name: 'Plugged in' }));
    await waitFor(() => expect(api.calls.some((c) => c.path === '/api/scans/RK-1/ports/state')).toBe(true));
    expect(JSON.parse(api.calls.find((c) => c.path === '/api/scans/RK-1/ports/state').body)).toEqual({ deviceId: 2, portNo: 3, state: 'connected' });
    // Something else goes to support with the scan attached.
    fireEvent.click(screen.getByRole('button', { name: 'Report a problem' }));
    fireEvent.click(within(await screen.findByRole('dialog', { name: 'Report a problem' })).getByText('Other issue'));
    sheet = await screen.findByRole('dialog', { name: 'Other issue' });
    fireEvent.change(within(sheet).getByLabelText('Description'), { target: { value: 'The blue cable on port 7 is not in the photo.' } });
    fireEvent.click(within(sheet).getByRole('button', { name: 'Send report' }));
    await waitFor(() => expect(api.calls.some((c) => c.path === '/api/support/contact')).toBe(true));
    expect(JSON.parse(api.calls.find((c) => c.path === '/api/support/contact').body).message).toMatch(/Scan: RK-1/);
  });

  test('overview opens on the device and port named in the address', async () => {
    mockApi(RESULT_API, USERS.member);
    openResult('/results/RK-1?device=2&port=2');
    const card = await screen.findByRole('region', { name: 'Port 2 of SW-CORE-01' });
    expect(within(card).getByText('Missing cable')).toBeTruthy();
    expect(devicePicker().getAttribute('data-value')).toBe('2');
  });

  test('the result\'s own tabs take the navigation\'s place, and there is no tab control at the top', async () => {
    mockApi({ ...RESULT_API, 'GET /api/scans/RK-1/switches': { scanId: 'RK-1', rackLabel: 'R01', matrix: true, switches: [SWITCH] } }, USERS.site_manager);
    openResult('/results/RK-1', USERS.site_manager);
    await screen.findByRole('heading', { level: 1, name: 'R01' });
    const nav = screen.getByRole('navigation', { name: 'Result' });
    // Four tabs and More: Switches is back beside Connections; Flags, Topology and Report sit under More.
    expect(within(nav).getAllByRole('tab').map((t) => t.textContent)).toEqual(['Overview', 'Devices', 'Connections', 'Switches']);
    expect(screen.getAllByRole('tablist')).toHaveLength(1);
    expect(document.body.classList.contains('has-nav')).toBe(true);
    fireEvent.click(within(nav).getByRole('tab', { name: 'Switches' }));
    expect(within(nav).getByRole('tab', { name: 'Switches' }).getAttribute('aria-selected')).toBe('true');
    expect(await screen.findByText('SFP modules')).toBeTruthy();
    fireEvent.click(screen.getByTestId('nav-more'));
    fireEvent.click(await screen.findByRole('button', { name: /Flags/ }));
    expect(await screen.findByRole('tablist', { name: 'Kind of flag' })).toBeTruthy();
    fireEvent.click(screen.getByTestId('nav-more'));
    fireEvent.click(await screen.findByRole('button', { name: /Topology/ }));
    expect(await screen.findByTestId('topology')).toBeTruthy();
    fireEvent.click(screen.getByTestId('nav-more'));
    fireEvent.click(await screen.findByRole('button', { name: /Report/ }));
    expect(await screen.findByRole('button', { name: 'Download the report' })).toBeTruthy();
    fireEvent.click(within(nav).getByRole('tab', { name: 'Devices' }));
    expect(within(nav).getByRole('tab', { name: 'Devices' }).getAttribute('aria-selected')).toBe('true');
  });

  test('overview without a matrix says so in one line, and how to fix it', async () => {
    mockApi({ ...RESULT_API, 'GET /api/scans/RK-1/sheet-check': { ...SHEET, matrixImportId: null, missing: [], uncapped: [] }, 'GET /api/scans/RK-1/detail': { ...DETAIL, matrix: null } }, USERS.site_manager);
    openResult('/results/RK-1', USERS.site_manager);
    expect(await screen.findByText(/No Excel file has been uploaded. Please upload an Excel file to compare and cross-check the physical rack information./)).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Upload the Excel file' }).getAttribute('href')).toBe('/matrix');
  });

  test('devices: one list from the top unit down, empty units folded into one line, no drawing of the rack', async () => {
    const units = [
      { unit: 42, kind: 'device', deviceId: 1 }, { unit: 41, kind: 'empty', deviceId: null },
      { unit: 40, kind: 'device', deviceId: 2 }, { unit: 39, kind: 'device', deviceId: 2 },
      ...[3, 2, 1].map((u) => ({ unit: u, kind: 'empty', deviceId: null })),
    ];
    mockApi({ ...RESULT_API, 'GET /api/scans/RK-1/detail': { ...DETAIL, units, unitsTotal: 42, heightNote: 'the rails are numbered up to U42' } }, USERS.member);
    openResult('/results/RK-1?tab=devices');
    const list = await screen.findByRole('list', { name: 'Devices, top unit first' });
    const rows = within(list).getAllByRole('listitem').map((li) => li.textContent);
    // One compact row per device: unit, name, kind and height (make and model
    // for switches), ports in use; empty units are left out of the list.
    expect(rows).toHaveLength(2);
    expect(rows[0]).toMatch(/^U42PP-A01Patch Panel · 1U/);
    expect(rows[1]).toMatch(/^U39-40SW-CORE-01Switch · 2U · Arista 7280R3/);
    expect(rows[1]).toMatch(/1\/3/);
    expect(rows[1]).not.toMatch(/read from|% sure/);
    expect(screen.getByRole('button', { name: /SW-CORE-01, U39-40, flagged, open/ })).toBeTruthy();
    expect(screen.getByText('Units used')).toBeTruthy();
    expect(document.querySelector('[aria-label="Rack elevation"]')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /PP-A01, U42, open/ }));
    expect(await screen.findByText('Device card')).toBeTruthy();
  });

  test('connections: a device, its port counts, its problems, and a port found by kind and number, shown on the photo', async () => {
    const boxed = { ...DETAIL, devices: DETAIL.devices.map((d) => (d.deviceId === 2 ? { ...d, box: [10, 80, 500, 130] } : d)) };
    mockApi({ ...RESULT_API, 'GET /api/scans/RK-1/detail': boxed }, USERS.member);
    openResult('/results/RK-1?tab=connections');
    expect(await screen.findByLabelText('Device')).toBeTruthy();
    // Only devices with ports are offered, so the switch is chosen already.
    expect(devicePicker().getAttribute('data-value')).toBe('2');
    fireEvent.click(devicePicker());
    const sheet = await screen.findByRole('dialog', { name: 'Choose a device' });
    const rows = within(sheet).getAllByRole('button').filter((b) => b.hasAttribute('aria-pressed'));
    expect(rows.map((b) => b.getAttribute('aria-pressed'))).toEqual(['true']);
    expect(rows[0].textContent).toMatch(/SW-CORE-01/);
    await chooseDevice('SW-CORE-01');
    expect(screen.getAllByText('Connected').length).toBeGreaterThan(0);
    // No list of every port: the problems of this device only, then a port by number.
    expect(screen.getByText('Problems on this device')).toBeTruthy();
    expect(screen.queryByRole('region', { name: /^Port \d+ of/ })).toBeNull();
    fireEvent.change(screen.getByLabelText('SFP port number'), { target: { value: '1' } });
    fireEvent.click(screen.getByRole('button', { name: 'Find' }));
    const card = await screen.findByRole('region', { name: 'Port 1 of SW-CORE-01' });
    expect(ends(card)).toEqual(['This endSW-CORE-01Port 1', 'Other endPP-B01Port 1, R02']);
    expect(within(card).getByText('Connected, LC, Aqua')).toBeTruthy();
    expect(within(card).getByText('Matched')).toBeTruthy();
    // The port is shown on the rack photo, inline.
    expect(within(card).getByAltText('Port 1 on the rack photo').getAttribute('src')).toContain('/api/scans/RK-1/files/photo.jpg');
    // The other end is on rack R02: a grey box, and no close-up action for it.
    expect(within(card).getByText('Not on this photo')).toBeTruthy();
    const shots = within(card).getAllByRole('button', { name: /^Close-up of the label/ });
    expect(shots.map((b) => b.getAttribute('aria-label'))).toEqual(['Close-up of the label, SW-CORE-01 port 1']);
    // The close-up for one port is a short sheet, not the close-ups screen: the block that holds the port, live or uploaded.
    fireEvent.click(shots[0]);
    const cu = await screen.findByRole('dialog', { name: 'Close-up of port 1' });
    expect(within(cu).getByText('SW-CORE-01: photograph ports 1 to 3 so port 1 and its label are sharp.')).toBeTruthy();
    expect(within(cu).getByRole('button', { name: 'Take a photo' })).toBeTruthy();
    expect(within(cu).getByRole('button', { name: 'Upload a photo' })).toBeTruthy();
    fireEvent.keyDown(document, { key: 'Escape' });
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Close-up of port 1' })).toBeNull());
    fireEvent.click(screen.getByRole('button', { name: /Port 2/ }));
    expect(await screen.findByRole('region', { name: 'Port 2 of SW-CORE-01' })).toBeTruthy();
    expect(screen.queryByRole('tablist', { name: 'Which ports' })).toBeNull();
  });

  test('connections: both ends zoomed on the photo, each with its own close-up action', async () => {
    // The switch's port 1 runs to PP-A01 port "7A" (a label read on its port 3); port 2 to "Port4".
    const pp = { ...DETAIL.devices[0], box: [10, 10, 500, 60], ports: [port(3, { box: [30, 20, 40, 30], label: { text: '7A' } }), port(4, { box: [50, 20, 60, 30] })] };
    const sw = { ...DETAIL.devices[1], box: [10, 80, 500, 130], ports: [
      port(1, { state: 'connected', box: [20, 90, 30, 100], expected: { cableLabel: 'C-1', end: 'a', other: { device: 'pp-a01', port: '7A', rack: 'R01' } }, status: 'matched' }),
      port(2, { state: 'connected', box: [40, 90, 50, 100], expected: { cableLabel: 'C-2', end: 'a', other: { device: 'PP-A01', port: 'Port4', rack: 'R01' } }, status: 'matched' }),
    ] };
    mockApi({ ...RESULT_API, 'GET /api/scans/RK-1/detail': { ...DETAIL, devices: [pp, sw] } }, USERS.member);
    openResult('/results/RK-1?tab=connections');
    await screen.findByLabelText('Device');
    await chooseDevice('SW-CORE-01');
    fireEvent.change(await screen.findByLabelText('SFP port number'), { target: { value: '1' } });
    fireEvent.click(screen.getByRole('button', { name: 'Find' }));
    let card = await screen.findByRole('region', { name: 'Port 1 of SW-CORE-01' });
    expect(within(card).getByAltText('Port 1 on the rack photo')).toBeTruthy();
    expect(within(card).getByAltText('Other end, PP-A01 port 3 on the rack photo')).toBeTruthy();
    expect(within(card).queryByText('Not on this photo')).toBeNull();
    expect(within(card).getAllByRole('button', { name: /^Close-up of the label/ }).map((b) => b.getAttribute('aria-label')))
      .toEqual(['Close-up of the label, SW-CORE-01 port 1', 'Close-up of the label, PP-A01 port 3']);
    fireEvent.click(within(card).getByRole('button', { name: 'Next port' }));
    card = await screen.findByRole('region', { name: 'Port 2 of SW-CORE-01' });
    expect(within(card).getByAltText('Other end, PP-A01 port 4 on the rack photo')).toBeTruthy();
  });

  test('findOtherEnd: by label code, by number, the device when the port is not read, null on another rack', () => {
    const pp = { deviceId: 1, name: 'SP-R1-U7-PP3', sheetName: 'SP-R1-U7-PP3', ports: [{ portNo: 2, label: { text: '53A' } }, { portNo: 4 }, { portNo: 40 }] };
    const d = { rackLabel: 'R1', devices: [pp] };
    expect(findOtherEnd(d, { device: 'sp r1 u7 pp3', port: '53A', rack: 'R1' }).port.portNo).toBe(2);
    expect(findOtherEnd(d, { device: 'SP-R1-U7-PP3', port: 'Port4' }).port.portNo).toBe(4);
    // A code the server placed by the site's convention lands on that port even with no label read.
    expect(findOtherEnd(d, { device: 'SP-R1-U7-PP3', port: '51A', portNo: 4 }).port.portNo).toBe(4);
    const code = findOtherEnd(d, { device: 'SP-R1-U7-PP3', port: '40A' });
    expect(code.device).toBe(pp);
    expect(code.port).toBeNull();
    expect(findOtherEnd(d, { device: 'SP-R1-U7-PP3', port: '2', rack: 'R2' })).toBeNull();
    expect(findOtherEnd(d, { device: 'PP9', port: '2' })).toBeNull();
  });

  test('connections: the dust cap the photo could not tell is asked in one tap, and the answer is kept', async () => {
    const dev = DETAIL.devices[1];
    const unsure = { ...DETAIL, devices: [{ ...dev, ports: [port(5, { state: 'empty', status: 'empty', capState: 'uncertain', capReason: 'no clear difference in row 1' })] }] };
    const api = mockApi({ ...RESULT_API, 'GET /api/scans/RK-1/detail': unsure, 'POST /api/scans/RK-1/ports/cap': { ok: true } }, USERS.member);
    openResult('/results/RK-1?tab=connections');
    fireEvent.change(await screen.findByLabelText('SFP port number'), { target: { value: '5' } });
    fireEvent.click(screen.getByRole('button', { name: 'Find' }));
    const card = await screen.findByRole('region', { name: 'Port 5 of SW-CORE-01' });
    expect(within(card).getByText('Check cap')).toBeTruthy();
    expect(within(card).getByText('Dust cap: not clear on the photo. Is it on?')).toBeTruthy();
    expect(card.textContent).not.toMatch(/no clear difference/);
    fireEvent.click(within(card).getByRole('button', { name: 'Cap off' }));
    await waitFor(() => expect(api.calls.some((c) => c.path === '/api/scans/RK-1/ports/cap')).toBe(true));
    expect(JSON.parse(api.calls.find((c) => c.path === '/api/scans/RK-1/ports/cap').body)).toEqual({ deviceId: 2, portNo: 5, flag: 'uncapped' });
  });

  test('connections: a plugged-in port with no label and no dataset says Connected and shows only what is known', async () => {
    const lone = { ...DETAIL, matrix: null, devices: [{ ...DETAIL.devices[1], ports: [port(5, { state: 'connected', status: 'connected' })] }] };
    mockApi({ ...RESULT_API, 'GET /api/scans/RK-1/detail': lone }, USERS.member);
    openResult('/results/RK-1?tab=connections');
    fireEvent.change(await screen.findByLabelText('SFP port number'), { target: { value: '5' } });
    fireEvent.click(screen.getByRole('button', { name: 'Find' }));
    const card = await screen.findByRole('region', { name: 'Port 5 of SW-CORE-01' });
    // Connected: the badge, and the "On the photo" row.
    expect(within(card).getAllByText('Connected').length).toBe(2);
    // The other end is not known: said in the box, nothing more.
    expect(ends(card)).toEqual(['This endSW-CORE-01Port 5', 'Other endNot read yetTake a close-up of its tag']);
    expect(within(card).getByText('SFP')).toBeTruthy();
    expect(card.textContent).not.toMatch(/Other end not known|Not clear on the photo|Cable: -|Excel dataset has/);
    // No box on the device, so no photo in the card.
    expect(within(card).queryByRole('img')).toBeNull();
  });

  test('topology: the rack as a clean frame of device tiles, the cables as curves from port dots, the numbers and filters, a device\'s details and the far ends', async () => {
    mockApi(RESULT_API, USERS.member);
    openResult('/results/RK-1?tab=topology');
    const screenEl = await screen.findByTestId('topology');
    // The key numbers, the 2D / 3D choice, and the filter pills with their counts.
    expect(within(screenEl).getByText('2 cables, 1 seen on the photo, 1 from the Excel only, 1 other end')).toBeTruthy();
    expect(within(screenEl).getByRole('tab', { name: '2D' }).getAttribute('aria-selected')).toBe('true');
    // The filters are folded away until asked for.
    expect(within(screenEl).queryByRole('button', { name: /^All types\s*2$/ })).toBeNull();
    fireEvent.click(within(screenEl).getByRole('button', { name: 'Filters' }));
    expect(within(screenEl).getByRole('button', { name: /^All types\s*2$/ })).toBeTruthy();
    expect(within(screenEl).getByRole('button', { name: /^Fiber\s*2$/ })).toBeTruthy();
    expect(within(screenEl).getByRole('button', { name: /^Seen\s*1$/ })).toBeTruthy();
    expect(within(screenEl).getByRole('button', { name: /^Excel only\s*1$/ })).toBeTruthy();
    // One drawing: a tile per device at its units, top first, and no number cards or rows.
    const rows = within(screenEl).getByRole('group', { name: 'Rack elevation of R01' });
    expect(within(rows).getByRole('group', { name: 'Rack R01' })).toBeTruthy();
    // The rack's title row names it and its units; a thin ruler numbers the units.
    expect(within(rows).getByText('R01')).toBeTruthy();
    expect(within(rows).getByText(/^\d+U$/)).toBeTruthy();
    expect(within(rows).getByText('1')).toBeTruthy();
    const blocks = within(rows).getAllByRole('button', { name: /, U\d/ }).map((b) => b.getAttribute('aria-label'));
    expect(blocks).toEqual(['PP-A01, U42, Patch Panel', 'SW-CORE-01, U39-U40, Switch']);
    // The far end of both cables is another rack's device: a small tag off the rack. The two cables are one
    // line between the pair, its count on it, solid because one was seen, red because one is missing.
    expect(within(rows).getByRole('button', { name: 'PP-B01, Rack R02' })).toBeTruthy();
    const link = await within(screenEl).findByRole('button', { name: 'SW-CORE-01 to PP-B01, 2 cables' });
    expect(link.querySelectorAll('path')[1].getAttribute('stroke-dasharray')).toBeNull();
    expect(link.getAttribute('data-tone')).toBe('bad');
    expect(link.querySelector('text').textContent).toBe('2');
    // Its end on the switch is a port dot on the tile's edge, filled because a cable of the pair was seen.
    expect(link.querySelectorAll('circle')).toHaveLength(1);
    expect(link.querySelector('circle').getAttribute('fill')).not.toBe('#ffffff');
    expect(within(screenEl).getAllByRole('button', { name: / to .*cable/ })).toHaveLength(1);
    expect(within(screenEl).getByLabelText('What the lines mean').textContent).toBe('Problem');
    expect(within(screenEl).getByText('Tap a cable or a device to follow it.')).toBeTruthy();
    // Nothing invented: no length, no address, no uplink.
    expect(screenEl.textContent).not.toMatch(/uplink|mgmt|\d+ ?m\b/);
    // A device: its facts in a sheet, and its ports folded under one row: the state, the cable on each, the other end.
    fireEvent.click(within(rows).getByRole('button', { name: /^SW-CORE-01, U39-U40/ }));
    const sheet = await screen.findByRole('dialog', { name: 'SW-CORE-01' });
    expect(within(sheet).getByText('Switch, Arista 7280R3')).toBeTruthy();
    expect(within(sheet).getByText('3 ports: 1 connected, 2 free')).toBeTruthy();
    expect(within(sheet).getByText('2 to PP-B01')).toBeTruthy();
    expect(within(sheet).queryByRole('table')).toBeNull();
    fireEvent.click(within(sheet).getByRole('button', { name: /^Ports\s*3$/ }));
    const table = within(sheet).getByRole('table');
    const trs = within(table).getAllByRole('row').slice(1).map((r) => r.textContent);
    expect(trs[0]).toMatch(/^SFP 1connected.*FC-001.*LC.*LC-LC OM4.*PP-B01 port 1/);
    expect(trs[1]).toMatch(/^SFP 2free.*FC-002 Excel only/);
    expect(trs[2]).toMatch(/^SFP 3free-/);
    expect(sheet.textContent).not.toMatch(/uplink|mgmt|\d+ ?m\b/);
    // Filtering to the cables seen leaves one, in the drawing and in the table.
    fireEvent.click(within(screenEl).getByRole('button', { name: /^Seen\s*1$/ }));
    await waitFor(() => expect(within(sheet).getByRole('table').textContent).not.toMatch(/FC-002/));
    expect(within(screenEl).queryByRole('button', { name: 'SW-CORE-01 to PP-B01, 2 cables' })).toBeNull();
    fireEvent.click(within(sheet).getByRole('button', { name: 'Close' }));
    // The line between the pair opens the link: its cables in rows, and a row opens that cable's own details.
    fireEvent.click(within(screenEl).getByRole('button', { name: 'SW-CORE-01 to PP-B01, 1 cable' }));
    expect(within(screenEl).getByRole('button', { name: 'SW-CORE-01 to PP-B01, 1 cable' }).getAttribute('aria-pressed')).toBe('true');
    expect(within(screenEl).getByRole('button', { name: 'SW-CORE-01 to PP-B01, 1 cable' }).getAttribute('data-tone')).toBe('ok');
    const linkSheet = await screen.findByRole('dialog', { name: 'SW-CORE-01 to PP-B01' });
    expect(within(linkSheet).getByText('1, seen on the photo')).toBeTruthy();
    const cableRows = within(within(linkSheet).getByRole('table')).getAllByRole('row').slice(1);
    expect(cableRows.map((r) => r.textContent)).toEqual(['FC-001SFP 11LC-LC OM4Seen on the photo']);
    fireEvent.click(cableRows[0]);
    const cableSheet = await screen.findByRole('dialog', { name: 'Cable FC-001' });
    expect(within(cableSheet).getByText('SW-CORE-01 port SFP 1')).toBeTruthy();
    expect(within(cableSheet).getByText('PP-B01 port 1')).toBeTruthy();
    expect(within(cableSheet).getByText('Seen on the photo')).toBeTruthy();
    fireEvent.click(within(cableSheet).getByRole('button', { name: 'Close' }));
    // Open goes to the device card.
    fireEvent.click(within(rows).getByRole('button', { name: /^SW-CORE-01, U39-U40/ }));
    fireEvent.click(within(await screen.findByRole('dialog', { name: 'SW-CORE-01' })).getByRole('button', { name: 'Open device' }));
    expect(await screen.findByText('Device card')).toBeTruthy();
  });

  test('topology: trace finds the cable path between two devices', async () => {
    mockApi(RESULT_API, USERS.member);
    openResult('/results/RK-1?tab=topology');
    const screenEl = await screen.findByTestId('topology');
    fireEvent.click(within(screenEl).getByRole('button', { name: 'Filters' }));
    fireEvent.click(within(screenEl).getByRole('button', { name: 'Trace' }));
    expect(within(screenEl).getByRole('button', { name: 'Trace' }).getAttribute('aria-pressed')).toBe('true');
    expect(within(screenEl).getByRole('status').textContent).toMatch(/^Tap the first device/);
    // In trace mode a tap picks an end; no details open.
    fireEvent.click(within(screenEl).getByRole('button', { name: /^SW-CORE-01, / }));
    expect(within(screenEl).getByRole('status').textContent).toMatch(/^From SW-CORE-01, tap the second device/);
    fireEvent.click(within(screenEl).getByRole('button', { name: 'PP-B01, Rack R02' }));
    expect(within(screenEl).getByRole('status').textContent).toMatch(/^SW-CORE-01 to PP-B01: 1 hop/);
    expect(screen.queryByRole('dialog')).toBeNull();
    fireEvent.click(within(screenEl).getByRole('button', { name: /^PP-A01, / }));
    fireEvent.click(within(screenEl).getByRole('button', { name: /^SW-CORE-01, / }));
    expect(within(screenEl).getByRole('status').textContent).toMatch(/No cable path between PP-A01 and SW-CORE-01/);
    // Trace off: the tap opens the details again.
    fireEvent.click(within(screenEl).getByRole('button', { name: 'Trace' }));
    expect(within(screenEl).queryByRole('status')).toBeNull();
    fireEvent.click(within(screenEl).getByRole('button', { name: /^PP-A01, / }));
    expect(await screen.findByRole('dialog', { name: 'PP-A01' })).toBeTruthy();
  });

  test('topology: with no cable known it still draws the rack and says so honestly', async () => {
    const bare = { ...DETAIL, matrix: null, cables: [], devices: DETAIL.devices.map((d) => ({ ...d, ports: d.ports.map((p) => ({ ...p, expected: null, label: null })) })) };
    mockApi({ ...RESULT_API, 'GET /api/scans/RK-1/detail': bare }, USERS.member);
    openResult('/results/RK-1?tab=topology');
    const screenEl = await screen.findByTestId('topology');
    expect(within(screenEl).getByText(/there is no Excel dataset and no cable label was read, so the other ends are not known/)).toBeTruthy();
    expect(within(screenEl).getByText(/^No cables known yet$|with a cable plugged in, other ends not known yet$/)).toBeTruthy();
    expect(within(screenEl).queryByRole('button', { name: 'PP-B01, Rack R02' })).toBeNull();
    // No cable is drawn (the icons in the buttons are not drawings).
    expect([...screenEl.querySelectorAll('path')].filter((p) => !p.closest('button'))).toHaveLength(0);
    // The rack is still drawn, every device in its block, and no line.
    const drawing = within(screenEl).getByRole('group', { name: 'Rack elevation of R01' });
    expect(within(drawing).getAllByRole('button').map((b) => b.getAttribute('aria-label'))).toEqual(['PP-A01, U42, Patch Panel', 'SW-CORE-01, U39-U40, Switch']);
  });

  test('report: the downloads first, a small photo and the key numbers, then three folded sections', async () => {
    mockApi(RESULT_API, USERS.member);
    openResult('/results/RK-1?tab=report');
    // One download button; its menu offers PDF, CSV and JSON.
    fireEvent.click(await screen.findByRole('button', { name: 'Download the report' }));
    const menu = screen.getByRole('menu');
    expect(within(menu).getByRole('link', { name: /^PDF/ }).getAttribute('href')).toContain('/api/scans/RK-1/report.pdf');
    expect(within(menu).getByRole('link', { name: /^CSV/ }).getAttribute('href')).toContain('/api/scans/RK-1/report.csv');
    expect(within(menu).getByRole('link', { name: /^JSON/ }).getAttribute('href')).toContain('/api/scans/RK-1/report.json');
    fireEvent.click(screen.getByRole('button', { name: 'Close' }));
    expect(screen.queryByRole('menu')).toBeNull();
    expect(screen.getAllByText(/Mandar/).length).toBeGreaterThan(0);
    expect(screen.getByText('1 connected, 2 empty')).toBeTruthy();
    // Folded by default: three rows with counts, nothing long on the screen.
    const heads = screen.getAllByRole('button', { expanded: false }).map((b) => b.textContent).filter((t) => /^(Units|Ports|Flags)/.test(t));
    expect(heads.map((t) => t.replace(/\d+$/, ''))).toEqual(['Units and devicesEvery unit from the top down', 'Ports and cablesBy device, 1 device with ports', 'FlagsAgainst the Excel dataset']);
    expect(screen.queryByRole('table')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /Units and devices/ }));
    const rows = within(screen.getByRole('table')).getAllByRole('row').slice(1).map((r) => Array.from(r.querySelectorAll('td')).map((c) => c.textContent));
    expect(rows.map((r) => [r[0], r[1]])).toEqual([['U42', 'PP-A01Patch Panel'], ['U41', 'Empty'], ['U40', 'SW-CORE-01Switch']]);
    fireEvent.click(screen.getByRole('button', { name: /Against the Excel dataset/ }));
    expect(screen.getByText('Empty. Cable FC-002 should be here, to PP-B01 port 2, R02.')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /Ports and cables/ }));
    fireEvent.click(screen.getByRole('button', { name: /SW-CORE-01/ }));
    expect(screen.getByText('LC, Aqua, FC-001')).toBeTruthy();
    // The photo is small; tapping it shows it large.
    fireEvent.click(screen.getByRole('button', { name: 'R01, rack photo, open it large' }));
    expect(await screen.findByAltText('R01, rack photo with the readings')).toBeTruthy();
  });

  test('a rack the server named opens with how it was named and a way to change it, and nothing asks', async () => {
    const identity = { ok: true, decision: 'matched', rule: 'record', rack: { source: 'known', id: 9, name: 'R01-A12', facilityId: null }, candidates: [],
      naming: { name: 'R01-A12', method: 'rail_label', words: 'read from the rail label', confidence: 0.94, ask: false } };
    mockApi({ ...RESULT_API, 'GET /api/scan/RK-1/identity': identity }, USERS.member);
    openResult();
    expect((await screen.findAllByText('R01-A12')).length).toBeGreaterThan(0);
    expect(screen.queryByText(/read from the rail label/)).toBeNull();
    expect(screen.queryByRole('dialog', { name: 'Which rack is this?' })).toBeNull();
    expect(screen.queryByText('Name this rack')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Rack actions' }));
    fireEvent.click(await screen.findByText('Change rack name'));
    expect(await screen.findByRole('dialog', { name: 'Change the rack name' })).toBeTruthy();
  });

  test('matched from the Excel dataset says so', async () => {
    const identity = { ok: true, decision: 'matched', rule: 'record', rack: { source: 'known', id: 9, name: 'R01-A12', facilityId: null }, candidates: [],
      naming: { name: 'R01-A12', method: 'layout', words: 'matched from the Excel dataset by its layout', ask: false } };
    mockApi({ ...RESULT_API, 'GET /api/scan/RK-1/identity': identity }, USERS.member);
    openResult();
    expect((await screen.findAllByText('R01-A12')).length).toBeGreaterThan(0);
    expect(screen.queryByText(/matched from the Excel dataset/)).toBeNull();
    expect(screen.queryByRole('dialog', { name: 'Which rack is this?' })).toBeNull();
  });

  test('"Which rack is this?" opens only when the server says it could not name the rack', async () => {
    const identity = { ok: true, decision: 'unknown', rule: null, rack: null, candidates: [],
      naming: { name: null, method: null, ask: true, why: 'no rack label was read on the rail; there is no Excel dataset for this site' } };
    const api = mockApi({ ...RESULT_API, 'GET /api/scan/RK-1/identity': identity, 'POST /api/scan/RK-1/identity/confirm': { ok: true, bound: { name: 'R07' }, identity: { ...identity, decision: 'matched', rack: { id: 3, name: 'R07' }, naming: { name: 'R07', method: 'person', words: 'named by a person', ask: false } } } }, USERS.member);
    openResult();
    const ask = await screen.findByRole('dialog', { name: 'Which rack is this?' });
    fireEvent.change(within(ask).getByLabelText('Or type its name'), { target: { value: 'R07' } });
    fireEvent.click(within(ask).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(api.calls.some((c) => c.method === 'POST' && c.path === '/api/scan/RK-1/identity/confirm')).toBe(true));
    expect((await screen.findAllByText('R07')).length).toBeGreaterThan(0);
  });

  test('a scan whose naming is still unknown asks nothing', async () => {
    mockApi(RESULT_API, USERS.member);
    openResult();
    await screen.findByRole('heading', { level: 1, name: 'R01' });
    expect(screen.queryByRole('dialog', { name: 'Which rack is this?' })).toBeNull();
  });

  test('flags: a flag opens a sheet that explains it, and nothing navigates away', async () => {
    const boxed = { ...DETAIL, notSeen: [{ name: 'FW-EDGE-01', unit: 'U41' }], devices: DETAIL.devices.map((d) => (d.deviceId === 2 ? { ...d, box: [10, 80, 500, 130], ports: d.ports.map((p) => ({ ...p, box: [20, 90, 40, 110] })) } : d)) };
    mockApi({ ...RESULT_API, 'GET /api/scans/RK-1/detail': boxed }, USERS.member);
    openResult('/results/RK-1?tab=flags');
    const list = await screen.findByRole('list', { name: 'Flags' });
    expect(screen.getByRole('tab', { name: /Not seen/ })).toBeTruthy();
    fireEvent.click(within(list).getByRole('button', { name: /SW-CORE-01 port 2/ }));
    const sheet = await screen.findByRole('dialog', { name: 'Missing cable' });
    expect(within(sheet).getByText('SW-CORE-01 port 2')).toBeTruthy();
    expect(within(sheet).getByText('The Excel lists a cable on this port, but the photo shows the port empty.')).toBeTruthy();
    const facts = Array.from(sheet.querySelectorAll('dl > div')).map((r) => [r.querySelector('dt').textContent, r.querySelector('dd').textContent]);
    expect(facts).toEqual([['Device', 'SW-CORE-01'], ['Port', '2, SFP'], ['Unit', 'U39-40'], ['Cable', 'FC-002'], ['Excel says', 'PP-B01 port 2, R02'], ['Seen on the photo', 'Empty']]);
    expect(within(sheet).getByAltText('SW-CORE-01 port 2 on the rack photo')).toBeTruthy();
    expect(within(sheet).getByText('Check the port. Plug the cable in, or correct the Excel if it was removed on purpose.')).toBeTruthy();
    // The address does not change: the Flags tab stays open under the sheet.
    expect(screen.getByRole('list', { name: 'Flags' })).toBeTruthy();
    expect(screen.queryByRole('heading', { name: /Find a port/ })).toBeNull();
    fireEvent.click(within(sheet).getByRole('button', { name: 'Close' }));
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Missing cable' })).toBeNull());
    // A device the Excel lists that the photo did not find.
    fireEvent.click(within(list).getByRole('button', { name: /FW-EDGE-01/ }));
    const ns = await screen.findByRole('dialog', { name: 'Not seen' });
    expect(within(ns).getByText('Check the rack. Take a clearer photo, or correct the Excel.')).toBeTruthy();
  });

  test('devices: a device the Excel lists but the photo did not find is a grey row at its unit', async () => {
    mockApi({ ...RESULT_API, 'GET /api/scans/RK-1/detail': { ...DETAIL, notSeen: [{ name: 'FW-EDGE-01', unit: 41 }] } }, USERS.member);
    openResult('/results/RK-1?tab=devices');
    const list = await screen.findByRole('list', { name: 'Devices, top unit first' });
    const rows = within(list).getAllByRole('listitem').map((li) => li.textContent);
    expect(rows[1]).toMatch(/^U41FW-EDGE-01Not seen on the photo/);
    expect(rows).toHaveLength(3);
  });

  test('the sheet check reads as rows', () => {
    const rows = flagRows({ units: [{ deviceId: 1, name: 'SW-1', status: 'wrong_unit', unitFound: 12, unitExpected: 18 }], missing: [], unexpected: [{ deviceId: 1, deviceName: 'SW-1', portNo: 4 }], uncapped: [] });
    expect(rows.map((r) => [r.kind, r.detail])).toEqual([['not_in_sheet', 'A cable is plugged in here that is not in the Excel.'], ['wrong_unit', 'In U12; the Excel says U18.']]);
    const missing = flagRows({ units: [], missing: [{ deviceId: 1, deviceName: 'SW-1', portNo: 7, cableLabel: 'C-7', otherEnd: { device: 'PP-9', port: '3', rack: 'R09' } }], unexpected: [], uncapped: [] });
    expect(missing).toEqual([{ key: 'm1-7', kind: 'missing_cable', deviceId: 1, portNo: 7, what: 'SW-1 port 7', detail: 'Empty. Cable C-7 should be here, to PP-9 port 3, R09.', on: 'SW-1 port 7', device: 'SW-1', cable: 'C-7', excel: 'PP-9 port 3, R09' }]);
  });
});

describe('Device card', () => {
  test('crop, make and model read, optics, ports and the two next steps; no specifications', async () => {
    mockApi({
      ...RESULT_API,
      'GET /api/scans/RK-1/devices/2': { deviceId: 2, make: 'Arista', model: '7280R3', confidence: 0.61, fields: { make: { source: 'photo_text', confidence: 0.82 }, model: { source: 'person', confidence: null } }, cropUrl: '/api/scans/RK-1/files/crops/device-2.jpg', specsStatus: 'found', specs: { heightU: 2, portCount: 52, portTypes: [{ type: 'SFP', count: 48 }], power: '2 x 600 W' } },
      'GET /api/scans/RK-1/sfp-advice': { items: [{ deviceId: 2, deviceName: 'SW-CORE-01', portType: 'SFP', count: 3, suggestion: { optic: 'SFP+ 10 Gbps: 10GBASE-SR', cable: 'LC-LC OM4' } }] },
    }, USERS.member);
    renderScreen(<DevicePage />, { path: '/results/RK-1/device/2', pattern: '/results/:scanId/device/:deviceId' });
    expect(await screen.findByAltText('SW-CORE-01, front')).toBeTruthy();
    expect(screen.queryByText('Specifications')).toBeNull();
    expect(screen.queryByText('2 x 600 W')).toBeNull();
    expect(screen.getByRole('link', { name: /Find a port on the photo/ }).getAttribute('href')).toBe('/results/RK-1?device=2');
    expect(screen.getByRole('link', { name: /Read cable labels/ }).getAttribute('href')).toBe('/results/RK-1/zones?device=2');
    expect(screen.getByAltText('SW-CORE-01, front').getAttribute('src')).toContain('crops/device-2.jpg');
    expect(screen.getByText('7280R3')).toBeTruthy();
    // Each detail says where it came from and how sure: never the box's score as "sure of the reading".
    expect(screen.queryByText(/82% sure/)).toBeNull();
    expect(screen.queryByText('set by a person')).toBeNull();
    expect(screen.queryByText(/Sure of the reading/)).toBeNull();
    fireEvent.click(await screen.findByRole('button', { name: /Optics to buy/ }));
    expect(screen.getByText('SFP+ 10 Gbps: 10GBASE-SR')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /^Ports/ }));
    expect(await screen.findByText('Port 2')).toBeTruthy();
  });

  test('on a server without the device route the page still opens from the scan', async () => {
    mockApi(RESULT_API, USERS.member);
    renderScreen(<DevicePage />, { path: '/results/RK-1/device/2', pattern: '/results/:scanId/device/:deviceId' });
    expect(await screen.findByRole('link', { name: /Find a port on the photo/ })).toBeTruthy();
    expect(screen.queryByText('Specifications')).toBeNull();
  });

  test('a patch panel has no make, model, specifications or optics', async () => {
    const api = mockApi({
      ...RESULT_API,
      'GET /api/scans/RK-1/devices/1': { deviceId: 1, type: 'Patch Panel', make: null, model: null, switchGear: false, specsStatus: 'not_switch', specs: null },
    }, USERS.member);
    renderScreen(<DevicePage />, { path: '/results/RK-1/device/1', pattern: '/results/:scanId/device/:deviceId' });
    expect((await screen.findAllByText(/Patch Panel/)).length).toBeGreaterThan(0);
    expect(screen.queryByText('Make and model not read - add them')).toBeNull();
    expect(screen.queryByText('Make')).toBeNull();
    expect(screen.queryByText('Specifications')).toBeNull();
    expect(screen.queryByText('Optics to buy')).toBeNull();
    // Nothing is looked up for a patch panel.
    expect(api.calls.some((c) => c.path.startsWith('/api/devices/'))).toBe(false);
  });
});

/* Rack R02 for the map: the panel at the other end of FC-001 and FC-002. */
const DETAIL_Z = {
  ...DETAIL, scanId: 'RK-2', rackLabel: 'R02', zones: 0,
  units: [{ unit: 30, kind: 'device', deviceId: 1, expected: 'PP-B01', expectedStatus: 'ok' }],
  devices: [{ deviceId: 1, type: 'Patch Panel', name: 'PP-B01', units: [30], unitTop: 30, unitBottom: 30, heightU: 1, sheetName: 'PP-B01', sheetStatus: 'ok', counts: { ports: 2, connected: 1, empty: 1, unknown: 0 },
    ports: [
      port(1, { type: 'LC', state: 'connected', label: null, expected: { cableLabel: 'FC-001', cableType: 'LC-LC OM4', end: 'z', other: { device: 'SW-CORE-01', port: 'Gi1/0/1', rack: 'R01' } }, status: 'not_found' }),
      port(2, { type: 'LC', expected: { cableLabel: 'FC-002', cableType: 'LC-LC OM4', end: 'z', other: { device: 'SW-CORE-01', port: 'Gi1/0/2', rack: 'R01' } }, status: 'missing_cable' }),
    ] }],
  cables: [],
};

const SWITCH = {
  scanId: 'RK-1', rackLabel: 'R01', deviceId: 2, type: 'Switch', name: 'SW-CORE-01', sheetName: 'SW-CORE-01', unitTop: 40, unitBottom: 39, heightU: 2,
  make: 'Arista', model: '7280R3', confidence: 0.9, labelText: 'SW-CORE-01', cropUrl: '/api/scans/RK-1/files/crops/device-2.jpg',
  specsStatus: 'found', specs: { heightU: 2, portCount: 52, portTypes: [{ type: 'SFP', count: 48 }], power: '2 x 600 W', datasheetUrl: 'https://example.test/ds.pdf', productUrl: 'https://example.test/ds.pdf',
    table: { Ports: '52', 'Port config': '48x 10G SFP+ + 4x 100G QSFP28', 'Max port speed (Gbps)': '100', 'Switching capacity (Gbps)': '4800', 'Forwarding rate (Mpps)': '2000', Layer: 'L3', 'Rack units': '2', 'Power max (W)': '2 x 600 W' } },
  positionKey: 'U39', awaitingLabel: false,
  ports: { total: 3, connected: 1, empty: 2, unknown: 0, byType: [{ type: 'SFP', count: 3 }] },
  sfp: { slotType: 'SFP+', total: 3, free: 2 },
  optics: [{ portType: 'SFP', count: 3, suggestion: { optic: 'SFP+ 10 Gbps: 10GBASE-SR', cable: 'LC-LC OM4' } }],
};
/* A second switch whose label was not read: no make, no model. */
const SWITCH_UNREAD = {
  scanId: 'RK-1', rackLabel: 'R01', deviceId: 5, type: 'Switch', name: 'Switch 5', sheetName: null, unitTop: 30, unitBottom: 30, heightU: 1,
  make: null, model: null, confidence: 0.7, labelText: null, cropUrl: null, fields: {},
  specsStatus: 'no_model', specs: null, positionKey: 'U30', awaitingLabel: false,
  ports: { total: 24, connected: 10, empty: 14, unknown: 0, byType: [{ type: 'RJ45', count: 24 }] },
  sfp: { slotType: null, total: 0, free: 0 }, optics: [],
};
const CHECK = {
  id: 'c1', at: new Date().toISOString(), by: 'barry', aScanId: 'RK-1', zScanId: 'RK-2', labelsFound: { a: 2, z: 0 },
  counts: { matched: 1, wrongPort: 1, missingCable: 0, notInSheet: 0, notFound: 0, uncapped: 1 },
  cables: [
    { cableLabel: 'FC-003', status: 'wrong_port', a: { expected: { device: 'SW-CORE-01', port: '3', rack: 'R01' }, found: { device: 'SW-CORE-01', port: '2' }, status: 'wrong_port' }, z: { expected: { device: 'PP-B01', port: '3', rack: 'R02' }, found: null, status: 'not_found' } },
    { cableLabel: 'FC-001', status: 'matched', a: { expected: { device: 'SW-CORE-01', port: '1', rack: 'R01' }, found: { device: 'SW-CORE-01', port: '1' }, status: 'matched' }, z: { expected: { device: 'PP-B01', port: '1', rack: 'R02' }, found: null, status: null } },
  ],
};
const FLAGS_A = { scanId: 'RK-1', rackLabel: 'R01', matrix: true, deviceCrops: { 2: '/api/scans/RK-1/files/crops/device-2.jpg' }, flags: [
  { kind: 'missing_cable', deviceId: 2, device: 'SW-CORE-01', unit: 40, portNo: 2, cableLabel: 'FC-002', otherEnd: { device: 'PP-B01', port: '2', rack: 'R02', unit: 30 }, cropUrl: '/api/scans/RK-1/files/crops/port-2-2.jpg' },
  { kind: 'uncapped', deviceId: 2, device: 'SW-CORE-01', unit: 40, portNo: 3, markedBy: 'mandar', cropUrl: '/api/scans/RK-1/files/crops/port-2-3.jpg' },
] };
const FLAGS_Z = { scanId: 'RK-2', rackLabel: 'R02', matrix: true, deviceCrops: {}, flags: [] };
const PAIR_API = {
  'GET /api/scan-sites': SITES,
  'GET /api/scans/RK-1/detail': DETAIL,
  'GET /api/scans/RK-2/detail': DETAIL_Z,
  'GET /api/scans/RK-1/switches': { scanId: 'RK-1', rackLabel: 'R01', matrix: true, switches: [SWITCH] },
  'GET /api/scans/RK-2/switches': { scanId: 'RK-2', rackLabel: 'R02', matrix: true, switches: [] },
  'GET /api/scans/RK-1/flags': FLAGS_A,
  'GET /api/scans/RK-2/flags': FLAGS_Z,
  'GET /api/checks': [],
  'POST /api/checks': CHECK,
};
/* The switch screen rendered on its own, as the two-rack page mounts it. */
function PairSwitches() {
  const swA = useScanSwitches('RK-1');
  const swZ = useScanSwitches('RK-2');
  return <SwitchGroups groups={[{ key: 'a', kicker: 'Rack A', rack: 'R01', res: swA }, { key: 'z', kicker: 'Rack Z', rack: 'R02', res: swZ }]} />;
}
const openSwitches = (user = USERS.site_manager) => renderScreen(<PairSwitches />, { path: '/map/RK-1/RK-2', pattern: '/map/:aScanId/:zScanId', user });
const openPair = (tab = '', user = USERS.site_manager) => renderScreen(<PairPage />, { path: `/map/RK-1/RK-2${tab ? `?tab=${tab}` : ''}`, pattern: '/map/:aScanId/:zScanId', user });

describe('Map two racks', () => {
  const pick = (region, name) => {
    fireEvent.click(within(screen.getByRole('region', { name: region })).getByRole('button', { name: /Upload/ }));
    fireEvent.change(screen.getByTestId('map-file'), { target: { files: [photo(name)] } });
  };

  test('two slots, Rack A and Rack Z, and one Analyse that needs both', async () => {
    const api = mockApi({ 'GET /api/scan-sites': SITES, 'POST /api/analyze': (url, init) => ({ rackId: init.body.get('image').name === 'a.jpg' ? 'RK-A' : 'RK-Z' }) }, USERS.member);
    renderScreen(<MapPage />, { path: '/map', extra: [['/map/:a/:z', <p key="p">Two-rack result</p>]] });
    expect(await screen.findByRole('region', { name: 'Rack A' })).toBeTruthy();
    expect(screen.getByRole('region', { name: 'Rack Z' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Analyse' }).disabled).toBe(true);
    expect(document.querySelector('select')).toBeNull();
    pick('Rack A', 'a.jpg');
    expect(await screen.findByAltText('Rack A, chosen for analysis')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Analyse' }).disabled).toBe(true);
    pick('Rack Z', 'z.jpg');
    await screen.findByAltText('Rack Z, chosen for analysis');
    await waitFor(() => expect(screen.getByRole('button', { name: 'Analyse' }).disabled).toBe(false));
    analyse();
    expect(await screen.findByText('Two-rack result')).toBeTruthy();
    const sent = api.calls.filter((c) => c.path === '/api/analyze');
    expect(sent.map((c) => c.body.get('image').name).sort()).toEqual(['a.jpg', 'z.jpg']);
    expect(sent.every((c) => c.body.get('siteId') === '2')).toBe(true);
    expect(validateMedia).toHaveBeenCalledTimes(2);
    expect(document.querySelector('svg:not(.appIcon)')).toBeNull();
  });

  test('while both read: two real photos side by side, each with its own state', async () => {
    const answers = {};
    mockApi({ 'GET /api/scan-sites': SITES, 'POST /api/analyze': (url, init) => new Promise((r) => { answers[init.body.get('image').name] = r; }) }, USERS.member);
    renderScreen(<MapPage />, { path: '/map', extra: [['/map/:a/:z', <p key="p">Two-rack result</p>]] });
    await screen.findByRole('region', { name: 'Rack A' });
    pick('Rack A', 'a.jpg');
    pick('Rack Z', 'z.jpg');
    await waitFor(() => expect(screen.getByRole('button', { name: 'Analyse' }).disabled).toBe(false));
    analyse();
    const view = await screen.findByRole('dialog', { name: 'Analysing both racks' });
    expect(within(view).getByAltText('Rack A, being analysed')).toBeTruthy();
    expect(within(view).getByAltText('Rack Z, being analysed')).toBeTruthy();
    expect(within(view).getAllByText('Analysing')).toHaveLength(2);
    expect(within(view).getByRole('status').textContent).toBe('Finding the rack units');
    expect(view.textContent).not.toMatch(/minute|second|takes/i);
    await waitFor(() => expect(answers['a.jpg'] && answers['z.jpg']).toBeTruthy());
    answers['a.jpg']({ rackId: 'RK-A' });
    await waitFor(() => expect(within(view).getByText('Read')).toBeTruthy());
    expect(within(view).getByText('Analysing')).toBeTruthy();
    answers['z.jpg']({ rackId: 'RK-Z' });
    await waitFor(() => expect(within(view).getByRole('status').textContent).toBe('Done'));
    expect(await screen.findByText('Two-rack result')).toBeTruthy();
  });

  test('while both read there is no Cancel: only the photos, the logo, the stage line and the bar', async () => {
    mockApi({ 'GET /api/scan-sites': SITES, 'POST /api/analyze': () => new Promise(() => {}) }, USERS.member);
    renderScreen(<MapPage />, { path: '/map', extra: [['/map/:a/:z', <p key="p">Two-rack result</p>]] });
    await screen.findByRole('region', { name: 'Rack A' });
    pick('Rack A', 'a.jpg');
    pick('Rack Z', 'z.jpg');
    await waitFor(() => expect(screen.getByRole('button', { name: 'Analyse' }).disabled).toBe(false));
    analyse();
    const view = await screen.findByRole('dialog', { name: 'Analysing both racks' });
    expect(within(view).queryByRole('button')).toBeNull();
    expect(within(view).getByRole('img', { name: 'RackTrack' })).toBeTruthy();
    expect(within(view).getByRole('status')).toBeTruthy();
    expect(within(view).getByRole('progressbar')).toBeTruthy();
  });

  test('after both are read the two are recorded as a pair, and a failed record still opens the result', async () => {
    const api = mockApi({ 'GET /api/scan-sites': SITES, 'POST /api/analyze': (url, init) => ({ rackId: init.body.get('image').name === 'a.jpg' ? 'RK-A' : 'RK-Z' }), 'POST /api/pairs': { status: 500, body: { error: 'Failed' } } }, USERS.member);
    renderScreen(<MapPage />, { path: '/map', extra: [['/map/:a/:z', <p key="p">Two-rack result</p>]] });
    await screen.findByRole('region', { name: 'Rack A' });
    pick('Rack A', 'a.jpg');
    pick('Rack Z', 'z.jpg');
    await waitFor(() => expect(screen.getByRole('button', { name: 'Analyse' }).disabled).toBe(false));
    analyse();
    expect(await screen.findByText('Two-rack result')).toBeTruthy();
    const pair = api.calls.filter((c) => c.path === '/api/pairs');
    expect(pair).toHaveLength(1);
    expect(pair[0].method).toBe('POST');
    expect(JSON.parse(pair[0].body)).toEqual({ aScanId: 'RK-A', zScanId: 'RK-Z' });
  });

  test('a chosen photo that will not show says so plainly, and says HEIC only for a HEIC file', async () => {
    mockApi({ 'GET /api/scan-sites': SITES }, USERS.member);
    renderScreen(<MapPage />, { path: '/map', extra: [['/map/:a/:z', <p key="p">Two-rack result</p>]] });
    const slotA = await screen.findByRole('region', { name: 'Rack A' });
    expect(within(slotA).getByRole('button', { name: /Upload/ })).toBeTruthy();
    pick('Rack A', 'rack-B12.jpeg');
    let img = await within(slotA).findByAltText('Rack A, chosen for analysis');
    expect(img.getAttribute('src')).toBe('blob:x');
    fireEvent.error(img);
    img = await waitFor(() => { const i = within(slotA).getByAltText('Rack A, chosen for analysis'); expect(i.getAttribute('src')).toMatch(/^data:/); return i; });
    fireEvent.error(img);
    expect(await within(slotA).findByText('Preview not available. It is read when you press Analyse.')).toBeTruthy();
    expect(within(slotA).getByText('rack-B12.jpeg')).toBeTruthy();
    expect(slotA.textContent).not.toMatch(/HEIC/);
    expect(within(slotA).getByRole('button', { name: 'Change photo' })).toBeTruthy();
    expect(within(slotA).queryByRole('button', { name: /Upload/ })).toBeNull();

    const slotZ = screen.getByRole('region', { name: 'Rack Z' });
    fireEvent.click(within(slotZ).getByRole('button', { name: /Upload/ }));
    fireEvent.change(screen.getByTestId('map-file'), { target: { files: [new File(['x'], 'rack-B07.heic', { type: 'image/heic' })] } });
    fireEvent.error(await within(slotZ).findByAltText('Rack Z, chosen for analysis'));
    await waitFor(() => expect(within(slotZ).getByAltText('Rack Z, chosen for analysis').getAttribute('src')).toMatch(/^data:/));
    fireEvent.error(within(slotZ).getByAltText('Rack Z, chosen for analysis'));
    expect(await within(slotZ).findByText('This phone cannot show a HEIC photo here. It is read when you press Analyse.')).toBeTruthy();
  });

  test('a photo that may not read well names its rack and can be analysed anyway', async () => {
    validateMedia.mockImplementation(async (f) => (f.name === 'z.jpg' ? { ok: false, retryable: true, error: 'The photo looks blurry.' } : { ok: true }));
    const api = mockApi({ 'GET /api/scan-sites': SITES, 'POST /api/analyze': (url, init) => ({ rackId: init.body.get('image').name === 'a.jpg' ? 'RK-A' : 'RK-Z' }) }, USERS.member);
    renderScreen(<MapPage />, { path: '/map', extra: [['/map/:a/:z', <p key="p">Two-rack result</p>]] });
    await screen.findByRole('region', { name: 'Rack A' });
    pick('Rack A', 'a.jpg');
    pick('Rack Z', 'z.jpg');
    await waitFor(() => expect(screen.getByRole('button', { name: 'Analyse' }).disabled).toBe(false));
    analyse();
    const dialog = await screen.findByRole('dialog', { name: 'Rack Z may not read well' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Analyse it anyway' }));
    await screen.findByText('Two-rack result');
    expect(api.calls.find((c) => c.path === '/api/analyze' && c.body.get('image').name === 'z.jpg').body.get('skipQualityCheck')).toBe('1');
  });
});

describe('Two-rack result', () => {
  test('the cables between two racks are found from both ends', () => {
    const c = cablesBetween(DETAIL, DETAIL_Z);
    expect(c.map((x) => [x.label, x.a.port.status, x.z.port.status])).toEqual([['FC-001', 'matched', 'not_found'], ['FC-002', 'missing_cable', 'missing_cable']]);
    expect(findEnd(DETAIL_Z, { device: 'PP-B01', port: '2', rack: 'R02' }).port.portNo).toBe(2);
    expect(findEnd(DETAIL_Z, { device: 'PP-B01', port: '2', rack: 'R09' })).toBeNull();
  });

  test('flags gather the sheet check of each rack and the label check, each with its rack and a picture', () => {
    const rows = pairFlags({ a: DETAIL, z: DETAIL_Z, flagsA: FLAGS_A, flagsZ: FLAGS_Z, check: CHECK });
    expect(rows.map((r) => [r.kind, r.rack, r.device, r.portNo])).toEqual([
      ['missing_cable', 'R01', 'SW-CORE-01', 2],
      ['wrong_port', 'R01', 'SW-CORE-01', 2],
      ['uncapped', 'R01', 'SW-CORE-01', 3],
      ['not_found', 'R02', 'PP-B01', 3],
    ]);
    expect(rows[0].detail).toBe('Empty. The Excel dataset has cable FC-002 here, to PP-B01 port 2, R02.');
    expect(rows[1].detail).toBe('Label FC-003 is on SW-CORE-01 port 2. The Excel dataset puts it on SW-CORE-01 port 3.');
    expect(rows[1].cropUrl).toContain('device-2.jpg');
  });

  test('only a rack the server could not name is asked about, by its side', async () => {
    const named = { ok: true, decision: 'matched', rack: { id: 1, name: 'R01' }, naming: { name: 'R01', method: 'rail_label', ask: false } };
    const unnamed = { ok: true, decision: 'unknown', rack: null, naming: { name: null, ask: true } };
    mockApi({ ...PAIR_API, 'GET /api/scan/RK-1/identity': named, 'GET /api/scan/RK-2/identity': unnamed }, USERS.member);
    openPair('', USERS.member);
    expect(await screen.findByRole('dialog', { name: 'Which rack is Rack Z?' })).toBeTruthy();
    expect(screen.queryByRole('dialog', { name: 'Which rack is Rack A?' })).toBeNull();
    expect(screen.getByText('Rack Z could not be named from its photo.')).toBeTruthy();
    expect(screen.queryByText('Rack A could not be named from its photo.')).toBeNull();
  });

  test('two racks both named: nothing asks', async () => {
    const named = (n) => ({ ok: true, decision: 'matched', rack: { id: 1, name: n }, naming: { name: n, method: 'device_labels', ask: false } });
    mockApi({ ...PAIR_API, 'GET /api/scan/RK-1/identity': named('R01'), 'GET /api/scan/RK-2/identity': named('R02') }, USERS.member);
    openPair('', USERS.member);
    await screen.findByRole('navigation', { name: 'Two racks' });
    await new Promise((r) => setTimeout(r, 50));
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(screen.queryByText(/could not be named/)).toBeNull();
  });

  test('the tabs, in order: A to Z, then the one-rack tabs with a Rack A / Rack Z switch', async () => {
    mockApi({ ...PAIR_API, 'GET /api/scan/RK-1': RESULT, 'GET /api/scans/RK-1/sheet-check': SHEET }, USERS.site_manager);
    openPair();
    expect(await screen.findByRole('heading', { level: 1, name: 'R01 and R02' })).toBeTruthy();
    const nav = screen.getByRole('navigation', { name: 'Two racks' });
    const tabs = within(nav).getAllByRole('tab').map((t) => t.textContent);
    expect(tabs.slice(0, 4)).toEqual(['A to Z', 'Overview', 'Devices', 'Connections']);
    expect(within(nav).getByRole('tab', { name: 'A to Z' }).getAttribute('aria-selected')).toBe('true');
    expect(screen.queryByRole('tablist', { name: 'Rack' })).toBeNull();
    fireEvent.click(within(nav).getByRole('tab', { name: 'Devices' }));
    const pick = await screen.findByRole('tablist', { name: 'Rack' });
    expect(within(pick).getAllByRole('tab').map((t) => t.getAttribute('aria-label'))).toEqual(['Rack A, R01', 'Rack Z, R02']);
    expect(within(pick).getByRole('tab', { name: 'Rack A, R01' }).getAttribute('aria-selected')).toBe('true');
    fireEvent.click(within(pick).getByRole('tab', { name: 'Rack Z, R02' }));
    expect(within(pick).getByRole('tab', { name: 'Rack Z, R02' }).getAttribute('aria-selected')).toBe('true');
  });

  test('switches: one card per switch: the picture, the name with the make and model and Change, the key numbers, the datasheet with the rest folded, and the SFP modules', async () => {
    mockApi(PAIR_API, USERS.site_manager);
    openSwitches();
    const card = await screen.findByRole('article', { name: 'Arista 7280R3' });
    // The header: the device picture, the name, the make and model under it, then the unit and the rack.
    expect(within(card).getByAltText('SW-CORE-01 on the rack photo').getAttribute('src')).toContain('crops/device-2.jpg');
    expect(within(card).getByRole('heading', { level: 3, name: 'SW-CORE-01' })).toBeTruthy();
    expect(within(card).getByText('Arista 7280R3')).toBeTruthy();
    expect(within(card).getByText('U39-40 · Rack R01')).toBeTruthy();
    expect(within(card).getByRole('button', { name: 'Change make and model' })).toBeTruthy();
    // No maker's login link, no chooser for a single switch.
    expect(within(card).queryByRole('link', { name: /Login portal/ })).toBeNull();
    expect(screen.queryByRole('tablist', { name: 'Switches in this rack' })).toBeNull();
    // The key numbers.
    expect(within(card).getByLabelText('Key numbers').textContent).toBe('Ports3Connected1Free SFP2Rack units2');
    expect(within(card).getByText('3 SFP · 2 empty')).toBeTruthy();
    // The datasheet: the main figures first, six shown, the rest one tap away, and the link.
    expect(within(card).getByText('48x 10G SFP+ + 4x 100G QSFP28')).toBeTruthy();
    expect(within(card).getByText('4800 Gbps')).toBeTruthy();
    expect(within(card).queryByText('2 x 600 W')).toBeNull();
    fireEvent.click(within(card).getByRole('button', { name: 'Show all specifications (8)' }));
    expect(within(card).getByText('Power max (W)')).toBeTruthy();
    expect(within(card).getByText('2 x 600 W')).toBeTruthy();
    expect(within(card).getByRole('link', { name: 'Datasheet' }).getAttribute('href')).toBe('https://example.test/ds.pdf');
    // The SFP modules: the slot line with the type, speed and cages, three rows, the rest folded, then the cables.
    expect(within(card).getByText('SFP+')).toBeTruthy();
    expect(within(card).getByText('3 cages, 2 free')).toBeTruthy();
    expect(within(card).getByText('S+85DLC03D')).toBeTruthy();
    expect(within(card).getByRole('link', { name: 'Buy S+85DLC03D' }).getAttribute('href')).toBe('https://mikrotik.com/product/Splus85DLC03D');
    expect(within(card).queryByText('XS+85LC01D')).toBeNull();
    fireEvent.click(within(card).getByRole('button', { name: 'More modules (1)' }));
    expect(within(card).getByText('XS+85LC01D')).toBeTruthy();
    expect(within(card).getByText('Pre-terminated cables')).toBeTruthy();
    expect(within(card).getByText('XS+DA0003')).toBeTruthy();
    // Nothing of RackTrack's look: no catalogue pictures, the device picture only.
    expect(within(card).queryByText(/TOP PICK/)).toBeNull();
    expect(card.querySelectorAll('img')).toHaveLength(1);
    // The other rack has none.
    expect(screen.getByText(/No switches found in this rack/)).toBeTruthy();
  });

  test('switches: pills choose the switch; one not read offers the make and model and the close-up', async () => {
    const api = mockApi({
      ...PAIR_API,
      'GET /api/scans/RK-1/switches': { scanId: 'RK-1', rackLabel: 'R01', matrix: true, switches: [SWITCH, SWITCH_UNREAD] },
      'PUT /api/scans/RK-1/devices/5/make-model': { ok: true },
    }, USERS.site_manager);
    openSwitches();
    const pills = await screen.findByRole('tablist', { name: 'Switches in this rack' });
    expect(within(pills).getAllByRole('tab').map((t) => t.textContent)).toEqual(['U39-40 SW-CORE-01', 'U30 Switch 5']);
    fireEvent.click(within(pills).getByRole('tab', { name: 'U30 Switch 5' }));
    const card = await screen.findByRole('article', { name: 'Switch 5' });
    expect(within(card).getByRole('heading', { level: 3, name: 'Switch 5' })).toBeTruthy();
    expect(within(card).getByText('Make and model not read')).toBeTruthy();
    expect(within(card).getByText('U30 · Rack R01')).toBeTruthy();
    expect(card.querySelector('img')).toBeNull();
    expect(within(card).getByRole('button', { name: 'Scan the device label' })).toBeTruthy();
    expect(within(card).queryByRole('button', { name: 'Change make and model' })).toBeNull();
    expect(within(card).getByText('Enter the make and model to see specifications.')).toBeTruthy();
    expect(within(card).getByText('Enter the make and model to see which modules fit.')).toBeTruthy();
    expect(within(card).getByLabelText('Key numbers').textContent).toBe('Ports24Connected10SFP cages0Rack units1');
    fireEvent.click(within(card).getByRole('button', { name: 'Enter make and model' }));
    fireEvent.change(within(card).getByLabelText('Make'), { target: { value: 'MikroTik' } });
    fireEvent.change(within(card).getByLabelText('Model'), { target: { value: 'CRS326-24G-2S+RM' } });
    fireEvent.click(within(card).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(api.calls.some((c) => c.method === 'PUT' && c.path === '/api/scans/RK-1/devices/5/make-model')).toBe(true));
    expect(JSON.parse(api.calls.find((c) => c.method === 'PUT').body)).toEqual({ make: 'MikroTik', model: 'CRS326-24G-2S+RM' });
  });

  test('switches: Change opens the same editor prefilled with the make and model read, and the card reloads after the save', async () => {
    const api = mockApi({ ...PAIR_API, 'PUT /api/scans/RK-1/devices/2/make-model': { ok: true } }, USERS.site_manager);
    openSwitches();
    const card = await screen.findByRole('article', { name: 'Arista 7280R3' });
    const before = api.calls.filter((c) => c.path === '/api/scans/RK-1/switches').length;
    fireEvent.click(within(card).getByRole('button', { name: 'Change make and model' }));
    expect(within(card).getByLabelText('Make').value).toBe('Arista');
    expect(within(card).getByLabelText('Model').value).toBe('7280R3');
    expect(within(card).queryByRole('button', { name: 'Change make and model' })).toBeNull();
    // Cancel keeps what was read.
    fireEvent.click(within(card).getByRole('button', { name: 'Cancel' }));
    expect(within(card).queryByLabelText('Model')).toBeNull();
    expect(api.calls.some((c) => c.method === 'PUT')).toBe(false);
    // Save goes through the same make-and-model route, and the card asks the server again.
    fireEvent.click(within(card).getByRole('button', { name: 'Change make and model' }));
    fireEvent.change(within(card).getByLabelText('Model'), { target: { value: '7280R3-48YC6' } });
    fireEvent.click(within(card).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(api.calls.some((c) => c.method === 'PUT' && c.path === '/api/scans/RK-1/devices/2/make-model')).toBe(true));
    expect(JSON.parse(api.calls.find((c) => c.method === 'PUT').body)).toEqual({ make: 'Arista', model: '7280R3-48YC6' });
    await waitFor(() => expect(api.calls.filter((c) => c.path === '/api/scans/RK-1/switches').length).toBeGreaterThan(before));
    await waitFor(() => expect(within(card).queryByLabelText('Model')).toBeNull());
  });

  test('switches: a switch whose datasheet is still being looked up says so; copper only means no SFP', async () => {
    const named = { ...SWITCH_UNREAD, make: 'MikroTik', model: 'CRS326-24G-2S+RM', specsStatus: 'looking' };
    mockApi({
      ...PAIR_API,
      'GET /api/scans/RK-1/switches': { scanId: 'RK-1', rackLabel: 'R01', matrix: true, switches: [named] },
    }, USERS.site_manager);
    openSwitches();
    const card = await screen.findByRole('article', { name: 'MikroTik CRS326-24G-2S+RM' });
    expect(within(card).getByText('Looking up the datasheet')).toBeTruthy();
    expect(within(card).getByText('No SFP cages on this switch.')).toBeTruthy();
    expect(within(card).getByRole('button', { name: 'Change make and model' })).toBeTruthy();
  });

  test('A to Z: the check runs by itself, one row per cable, problems first, a row opens both ends', async () => {
    const api = mockApi(PAIR_API, USERS.site_manager);
    openPair('az');
    expect(await screen.findByText('FC-003')).toBeTruthy();
    expect(JSON.parse(api.calls.find((c) => c.path === '/api/checks' && c.method === 'POST').body)).toEqual({ aScanId: 'RK-1', zScanId: 'RK-2' });
    const rows = screen.getAllByRole('button').filter((b) => /FC-00/.test(b.textContent));
    expect(rows.map((b) => b.textContent.match(/FC-\d+/)[0])).toEqual(['FC-003', 'FC-001']);
    expect(screen.getByRole('link', { name: 'Download PDF' }).getAttribute('href')).toContain('/api/checks/c1/report.pdf');
    expect(screen.getByRole('link', { name: 'Download CSV' }).getAttribute('href')).toContain('/api/checks/c1/export.csv');
    // Each card says, in a sentence and side by side, the Excel against the photo.
    expect(screen.getByText('At the R01 end the photo does not match the Excel.')).toBeTruthy();
    expect(screen.getByText('The photo matches the Excel at the R01 end; the R02 end is in a rack not photographed.')).toBeTruthy();
    const cmp = screen.getByRole('table', { name: 'FC-001: the Excel against the photo' });
    expect(within(cmp).getAllByRole('columnheader').map((h) => h.textContent)).toEqual(['', 'In the Excel', 'On the photo']);
    fireEvent.click(rows[0]);
    const sheet = await screen.findByRole('dialog');
    expect(within(sheet).getByText('A-end')).toBeTruthy();
    expect(within(sheet).getByText('Z-end')).toBeTruthy();
    expect(within(sheet).getByText('SW-CORE-01, port 3, rack R01')).toBeTruthy();
  });

  test('topology: both racks on one baseline, the cables between them as curves from port dot to port dot, a tap follows one, zoom and fit', async () => {
    mockApi(PAIR_API, USERS.member);
    openPair('topology', USERS.member);
    const racks = await screen.findByTestId('pair-racks');
    await waitFor(() => expect(racks.getAttribute('data-cables')).toBe('FC-003|FC-001'));
    const drawing = within(racks).getByRole('group', { name: 'R01 and R02, cables' });
    expect(within(drawing).getByRole('group', { name: 'Rack R01' })).toBeTruthy();
    expect(within(drawing).getByRole('group', { name: 'Rack R02' })).toBeTruthy();
    // Unit 1 of both racks stands on the same bottom (no line is drawn): each rack's frame ends at the same height.
    const bottoms = [...drawing.querySelectorAll('rect[data-bg]')].map((r) => Number(r.getAttribute('y')) + Number(r.getAttribute('height')));
    expect(bottoms).toHaveLength(2);
    expect(Math.abs(bottoms[0] - bottoms[1]) < 0.01).toBe(true);
    // A cable per line, coloured by its result; no number cards.
    const wrong = within(drawing).getByRole('button', { name: /^FC-003, R01 SW-CORE-01 3 to R02 PP-B01 3, Wrong port$/ });
    const ok = within(drawing).getByRole('button', { name: /^FC-001, .*Matched$/ });
    expect(wrong.getAttribute('data-tone')).toBe('bad');
    expect(ok.getAttribute('data-tone')).toBe('ok');
    expect(within(racks).getByLabelText('What the lines mean').textContent).toBe('MatchedProblem');
    // A dot at each end of a cable: filled where its tag was read, hollow where it is only expected.
    const fills = (g) => [...g.querySelectorAll('circle')].map((c) => (c.getAttribute('fill') === '#ffffff' ? 'hollow' : 'filled'));
    expect(fills(ok)).toEqual(['filled', 'hollow']);
    expect(fills(wrong)).toEqual(['filled', 'hollow']);
    // Zoom in and out with the small buttons; Fit goes back to the whole drawing.
    expect(drawing.getAttribute('data-zoom')).toBe('1.00');
    fireEvent.click(within(racks).getByRole('button', { name: 'Zoom in' }));
    expect(drawing.getAttribute('data-zoom')).toBe('1.50');
    fireEvent.click(within(racks).getByRole('button', { name: 'Fit' }));
    expect(drawing.getAttribute('data-zoom')).toBe('1.00');
    // A tap on a line chooses it: the others fade and one line says what it is; a second tap lets go.
    fireEvent.click(wrong);
    expect(wrong.getAttribute('aria-pressed')).toBe('true');
    expect(ok.getAttribute('class')).toMatch(/faded/);
    expect(within(racks).getByText('FC-003')).toBeTruthy();
    expect(within(racks).getByText(/R01 SW-CORE-01 3 to R02 PP-B01 3/)).toBeTruthy();
    fireEvent.click(wrong);
    expect(wrong.getAttribute('aria-pressed')).toBe('false');
    // A device: its cables stay, with a line naming it; the row under the drawing chooses the same cable.
    fireEvent.click(within(drawing).getByRole('button', { name: 'SW-CORE-01, U39-40' }));
    expect(within(racks).getByText(/R01 U39-40, 2 cables/)).toBeTruthy();
    const list = screen.getByRole('region', { name: 'Cables' });
    fireEvent.click(within(list).getAllByRole('button')[1]);
    expect(ok.getAttribute('aria-pressed')).toBe('true');
  });

  test('topology: one 2D / 3D switch and no rack buttons; 3D draws both racks in one scene; a tap on a rack opens it alone with the way back', async () => {
    mockApi(PAIR_API, USERS.member);
    openPair('topology', USERS.member);
    const racks = await screen.findByTestId('pair-racks');
    // The old choice of views is gone: no "Both racks" or per-rack 3D buttons.
    expect(screen.queryByRole('tab', { name: 'Both racks' })).toBeNull();
    expect(screen.queryByRole('tab', { name: /R01 3D|R02 3D/ })).toBeNull();
    const view = screen.getByRole('tablist', { name: 'Topology view' });
    expect(within(view).getAllByRole('tab').map((t) => t.textContent)).toEqual(['2D', '3D']);
    expect(within(view).getByRole('tab', { name: '2D' }).getAttribute('aria-selected')).toBe('true');
    // 2D: a tap on a rack's name opens that rack alone, with its own 2D / 3D switch and a button back.
    fireEvent.click(within(racks).getByRole('button', { name: 'Open R02' }));
    const back = await screen.findByRole('button', { name: 'Back to both racks' });
    const one = await screen.findByTestId('topology');
    expect(within(one).getByRole('group', { name: 'Rack elevation of R02' })).toBeTruthy();
    expect(within(one).getByRole('tab', { name: '3D' })).toBeTruthy();
    expect(screen.queryByTestId('pair-racks')).toBeNull();
    fireEvent.click(back);
    expect(await screen.findByTestId('pair-racks')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Back to both racks' })).toBeNull();
    // 3D: both racks in one scene, with the cables of the check; a tap on a rack there opens it alone too.
    fireEvent.click(within(screen.getByRole('tablist', { name: 'Topology view' })).getByRole('tab', { name: '3D' }));
    const scene = await screen.findByTestId('pair-scene-3d');
    expect(scene.getAttribute('data-racks')).toBe('R01|R02');
    expect(scene.getAttribute('data-cables')).toBe('FC-003|FC-001');
    fireEvent.click(within(scene).getByRole('button', { name: 'Tap the second rack frame' }));
    expect(await screen.findByRole('button', { name: 'Back to both racks' })).toBeTruthy();
    expect((await screen.findByTestId('rack-scene-3d')).getAttribute('data-rack')).toBe('R02');
  });

  test('report: the two-rack report to read (every cable From, To and result), its files, then each rack\'s own report', async () => {
    mockApi({ ...PAIR_API, 'GET /api/scan/RK-1': RESULT, 'GET /api/scans/RK-1/sheet-check': SHEET }, USERS.site_manager);
    openPair('report');
    const both = await screen.findByRole('region', { name: 'R01 and R02, both racks' });
    expect(within(both).getAllByRole('link').map((l) => l.getAttribute('href').replace(/^.*\/api/, '/api').split('?')[0])).toEqual(['/api/checks/c1/report.pdf', '/api/checks/c1/export.csv', '/api/checks/c1/report.json']);
    const rows = within(both).getAllByRole('row').slice(1).map((r) => within(r).getAllByRole('cell').map((c) => c.textContent));
    expect(rows[0]).toEqual(['FC-003', 'FromR01 SW-CORE-01 3', 'ToR02 PP-B01 3', 'Wrong port']);
    expect(screen.getByRole('region', { name: 'R01 alone' })).toBeTruthy();
    expect(screen.getByRole('region', { name: 'R02 alone' })).toBeTruthy();
  });

  test('A to Z: a cable read on a tag that the Excel does not have is offered to add, never listed as an error', async () => {
    const fresh = { cableLabel: 'SW-CORE-01 Gi1-0-9 R01', status: 'not_in_sheet', add: { cable_label: 'SW-CORE-01 Gi1-0-9 R01', a_device: 'SW-CORE-01', a_port: 'Gi1-0-9', a_rack: 'R01', z_device: 'PP-B01', z_port: '9', z_rack: 'R02' },
      a: { expected: null, found: { device: 'SW-CORE-01', read: 'SW-CORE-01 Gi1-0-9 R01' }, status: 'not_in_sheet' }, z: { expected: null, found: null, status: null } };
    const noise = { cableLabel: 'Fxszoicqiu7', status: 'not_in_sheet', add: null, a: { expected: null, found: { device: 'x' }, status: 'not_in_sheet' }, z: { status: null } };
    const api = mockApi({ ...PAIR_API, 'POST /api/checks': { ...CHECK, cables: [...CHECK.cables, fresh, noise] }, 'POST /api/matrix/cables': { rowNo: 9, cableLabel: fresh.cableLabel } }, USERS.site_manager);
    openPair('az');
    expect(await screen.findByText('New cables read on the tags')).toBeTruthy();
    expect(screen.queryByText('Fxszoicqiu7')).toBeNull();
    expect(screen.queryByText(/Not in the/)).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Add to Excel' }));
    await waitFor(() => expect(api.calls.some((c) => c.path === '/api/matrix/cables')).toBe(true));
    const body = JSON.parse(api.calls.find((c) => c.path === '/api/matrix/cables').body);
    expect(body).toEqual({ siteId: 2, cable: fresh.add });
  });

  test('A-Z without an Excel dataset says so and how to fix it', async () => {
    const api = mockApi({ ...PAIR_API, 'GET /api/scans/RK-1/detail': { ...DETAIL, matrix: null }, 'GET /api/scans/RK-2/detail': { ...DETAIL_Z, matrix: null } }, USERS.site_manager);
    openPair('az');
    expect(await screen.findByText('No Excel file uploaded')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Upload the Excel file' }).getAttribute('href')).toBe('/matrix');
    expect(api.calls.some((c) => c.path === '/api/checks' && c.method === 'POST')).toBe(false);
  });

  test('A-Z waits while the tags are still being read: no check yet, no rack name asked', async () => {
    const api = mockApi({ ...PAIR_API,
      'GET /api/scans/RK-1/detail': { ...DETAIL, rackLabel: null, reading: { labels: true } },
      'GET /api/scan/RK-1/identity': { rack: null, naming: { ask: true } },
    }, USERS.site_manager);
    openPair('az');
    expect(await screen.findByText(/Reading the cable tags on the photos/)).toBeTruthy();
    expect(screen.getByRole('heading', { name: 'Rack A and R02' })).toBeTruthy();
    expect(screen.queryByText('Rack A could not be named from its photo.')).toBeNull();
    expect(api.calls.some((c) => c.path === '/api/checks' && c.method === 'POST')).toBe(false);
  });

});

describe('Port lookup', () => {
  test('checks the number against the device, then finds the port', async () => {
    const api = mockApi({
      'GET /api/scan/RK-1': RESULT,
      'POST /api/select': { resultImageUrl: '/outputs/RK-1/images/5.png', rackImageUrl: '/outputs/RK-1/images/6.png', portInfo: { status: 'connected', port_type: 'RJ45', cable_color: 'Blue' } },
    }, USERS.member);
    renderScreen(<PortPage />, { path: '/results/RK-1/port?device=2', pattern: '/results/:scanId/port' });
    const field = await screen.findByLabelText('Port number');
    fireEvent.change(field, { target: { value: '49' } });
    fireEvent.click(screen.getByRole('button', { name: 'Find port' }));
    expect(screen.getByRole('alert').textContent).toBe('This device has 48 ports. Enter a number from 1 to 48.');
    fireEvent.change(field, { target: { value: '17' } });
    fireEvent.click(screen.getByRole('button', { name: 'Find port' }));
    expect(await screen.findByText('Cable plugged in')).toBeTruthy();
    expect(JSON.parse(api.calls.find((c) => c.path === '/api/select').body)).toEqual({ scanId: 'RK-1', device_index: 2, port: 17, port_category: 'main' });
    expect(screen.getByRole('heading', { level: 1 }).textContent).toBe('Port 17');
    expect(screen.getByAltText(/port 17 ringed/).getAttribute('src')).toContain('/outputs/RK-1/images/5.png');
  });

  test('a port number problem is one plain sentence', () => {
    expect(portProblem('1.5', 24)).toBe('Enter a whole port number.');
    expect(portProblem('3', 0)).toMatch(/not read/);
    expect(portProblem('3', 24)).toBeNull();
  });


});
