import { describe, test, expect, afterEach } from 'vitest';
import { screen, fireEvent, waitFor, act, within } from '@testing-library/react';
import { useLocation } from 'react-router-dom';
import MatrixPage from './MatrixPage.jsx';
import { renderScreen, mockApi, USERS } from '../test/render.jsx';

const IMPORT = { id: 11, fileName: 'EOS_CRXL_Hall2_v7.xlsx', sheetName: 'Patching', uploadedBy: 'Barry', uploadedAt: '2026-09-26T10:00:00Z', rowCount: 390, cableCount: 384, problemCount: 2 };
const SUMMARY = { import: IMPORT, counts: { cables: 384, devices: 6, problems: 2 }, problems: [{ rowNo: 14, reason: 'no A-end port' }, { rowNo: 90, reason: 'label used twice' }] };
const CABLES = [
  { rowNo: 4, cableLabel: 'EOS-F-0141', aRack: 'R01-A12', aDevice: 'sw-a1', aPort: 'Et15', zRack: 'R02-B07', zDevice: 'PP-24 A', zPort: '3', cableType: 'OS2 LC-LC', raw: { 'Cable ID': 'EOS-F-0141', Media: 'OS2 LC-LC' } },
  { rowNo: 5, cableLabel: 'EOS-F-0142', aRack: 'R01-A12', aDevice: 'sw-a1', aPort: 'Et16', zRack: 'R02-B07', zDevice: 'PP-24 A', zPort: '4', cableType: 'OS2 LC-LC' },
  { row_no: 6, cable_label: 'EOS-F-0200', a_rack: 'R03-A01', a_device: 'sw-c1', a_port: 'Et1', z_rack: 'R02-B07', z_device: 'PP-24 B', z_port: '1', cable_type: 'OM4 MPO-12' },
];

function Where() {
  const l = useLocation();
  return <p data-testid="where">{`${l.pathname} ${JSON.stringify(l.state)}`}</p>;
}

function cablesHandler({ total = 3 } = {}) {
  return (url) => {
    const u = new URL(url, 'http://x');
    const q = (u.searchParams.get('q') || '').toLowerCase();
    const list = CABLES.filter((c) => !q || JSON.stringify(c).toLowerCase().includes(q));
    return { total: q ? list.length : total, cables: list };
  };
}

function setLaptop(on) {
  window.matchMedia = on ? () => ({ matches: true, addEventListener() {}, removeEventListener() {} }) : undefined;
}

afterEach(() => { setLaptop(false); });

describe('MatrixPage, empty', () => {
  test('a site manager sees the upload button and the upload goes to the check screen', async () => {
    const api = mockApi({
      'GET /api/matrix': { import: null, counts: { cables: 0, devices: 0, problems: 0 }, problems: [] },
      'POST /api/matrix/upload': { uploadId: 'up1', sheets: [{ name: 'Patching', headerRow: 3, headers: ['Cable ID'], sample: [], rowCount: 1 }], guess: {} },
    }, USERS.site_manager);
    renderScreen(<MatrixPage />, { path: '/matrix', user: USERS.site_manager, extra: [['/matrix/check', <Where key="w" />]] });
    expect(await screen.findByText("No Excel dataset for Rack Build Facility yet. Upload the manager's workbook (.xlsx), every sheet filled in from the template.")).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Import workbook' })).toBeTruthy();
    const file = new File(['x'], 'hall2.xlsx');
    fireEvent.change(screen.getByTestId('matrix-file'), { target: { files: [file] } });
    const where = await screen.findByTestId('where');
    expect(where.textContent).toContain('/matrix/check');
    expect(where.textContent).toContain('"uploadId":"up1"');
    expect(where.textContent).toContain('"fileName":"hall2.xlsx"');
    const call = api.calls.find((c) => c.method === 'POST' && c.path === '/api/matrix/upload');
    expect(call.body instanceof FormData).toBe(true);
    expect(call.body.get('file').name).toBe('hall2.xlsx');
  });

  test('a file of the wrong kind is refused before it is sent', async () => {
    const api = mockApi({ 'GET /api/matrix': { import: null } }, USERS.site_manager);
    renderScreen(<MatrixPage />, { path: '/matrix', user: USERS.site_manager });
    await screen.findByRole('button', { name: 'Import workbook' });
    fireEvent.change(screen.getByTestId('matrix-file'), { target: { files: [new File(['x'], 'old.xls')] } });
    expect((await screen.findByRole('alert')).textContent).toContain('Save an old .xls file as .xlsx first.');
    expect(api.calls.some((c) => c.path === '/api/matrix/upload')).toBe(false);
  });

  test('a technician is told who to ask and gets no upload button', async () => {
    mockApi({
      'GET /api/matrix': { import: null },
      'GET /api/sites/2/overview': { site: { id: 2 }, racks: [], people: [{ id: 2, name: 'Barry', role: 'site_manager' }] },
    }, USERS.member);
    renderScreen(<MatrixPage />, { path: '/matrix', user: USERS.member });
    expect(await screen.findByText('No Excel dataset for Rack Build Facility yet. Ask Barry to upload it.')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Import workbook' })).toBeNull();
  });

  test('a technician falls back to "your site manager" when no name is known', async () => {
    mockApi({ 'GET /api/matrix': { import: null } }, USERS.member);
    renderScreen(<MatrixPage />, { path: '/matrix', user: USERS.member });
    expect(await screen.findByText(/Ask your site manager to upload it\./)).toBeTruthy();
  });

  test('a server without the matrix says so in one sentence', async () => {
    mockApi({}, USERS.site_manager);
    renderScreen(<MatrixPage />, { path: '/matrix', user: USERS.site_manager });
    expect(await screen.findByText('The Excel dataset is not available on this server yet.')).toBeTruthy();
  });
});

describe('MatrixPage, loaded', () => {
  test('shows the count, one line on the file and one row per cable; a row opens the full line', async () => {
    mockApi({ 'GET /api/matrix': SUMMARY, 'GET /api/matrix/cables': cablesHandler() }, USERS.member);
    renderScreen(<MatrixPage />, { path: '/matrix', user: USERS.member });
    expect(await screen.findByText('EOS-F-0141')).toBeTruthy();
    expect(screen.getByText('EOS-F-0200')).toBeTruthy();
    expect(screen.getAllByRole('heading', { level: 1 }).map((h) => h.textContent)).toEqual(['Excel dataset']);
    expect(screen.getByText('384 cables')).toBeTruthy();
    expect(screen.getByText('EOS_CRXL_Hall2_v7.xlsx, imported 26 Sep by Barry.')).toBeTruthy();
    expect(screen.getByText('sw-a1 Et15 to PP-24 A 3')).toBeTruthy();
    expect(screen.queryByText(/need checking/)).toBeNull();
    expect(screen.queryByRole('button', { name: 'Import a new workbook' })).toBeNull();
    fireEvent.click(screen.getByText('EOS-F-0141'));
    const dialog = await screen.findByRole('dialog');
    expect(dialog.textContent).toContain('Cable EOS-F-0141');
    expect(dialog.textContent).toContain('The line in the file');
    expect(dialog.textContent).toContain('Cable ID');
  });

  test('a manager has one button to import a new file, which goes to the check screen', async () => {
    mockApi({
      'GET /api/matrix': SUMMARY,
      'GET /api/matrix/cables': cablesHandler(),
      'POST /api/matrix/upload': { uploadId: 'up2', sheets: [], guess: {} },
    }, USERS.site_manager);
    renderScreen(<MatrixPage />, { path: '/matrix', user: USERS.site_manager, extra: [['/matrix/check', <Where key="w" />]] });
    await screen.findByText('EOS-F-0141');
    expect(screen.getByRole('button', { name: 'Import a new workbook' })).toBeTruthy();
    fireEvent.change(screen.getByTestId('matrix-file'), { target: { files: [new File(['a,b'], 'v8.xlsx')] } });
    expect((await screen.findByTestId('where')).textContent).toContain('"uploadId":"up2"');
  });

  test('the search box asks the server after a pause', async () => {
    const api = mockApi({ 'GET /api/matrix': SUMMARY, 'GET /api/matrix/cables': cablesHandler() }, USERS.member);
    renderScreen(<MatrixPage />, { path: '/matrix', user: USERS.member });
    await screen.findByText('EOS-F-0141');
    fireEvent.change(screen.getByLabelText('Search: cable, device, port, or sw1 p7, pp1 10a, u13'), { target: { value: 'sw-c1' } });
    await waitFor(() => expect(api.calls.some((c) => c.url.includes('q=sw-c1'))).toBe(true));
    await waitFor(() => expect(screen.queryByText('EOS-F-0141')).toBeNull());
    expect(screen.getByText('EOS-F-0200')).toBeTruthy();
  });

  test('Load more asks for the next page', async () => {
    const api = mockApi({ 'GET /api/matrix': SUMMARY, 'GET /api/matrix/cables': cablesHandler({ total: 120 }) }, USERS.member);
    renderScreen(<MatrixPage />, { path: '/matrix', user: USERS.member });
    fireEvent.click(await screen.findByRole('button', { name: 'Load more' }));
    await waitFor(() => expect(api.calls.some((c) => c.url.includes('offset=3'))).toBe(true));
  });

  test('a laptop shows a three-column table', async () => {
    setLaptop(true);
    mockApi({ 'GET /api/matrix': SUMMARY, 'GET /api/matrix/cables': cablesHandler() }, USERS.site_manager);
    renderScreen(<MatrixPage />, { path: '/matrix', user: USERS.site_manager });
    await screen.findByText('EOS-F-0141');
    const table = screen.getByRole('table');
    expect(within(table).getAllByRole('columnheader').map((h) => h.textContent)).toEqual(['Cable label', 'A-end', 'Z-end']);
    expect(within(table).getAllByText('sw-a1 Et15').length).toBe(1);
  });

  test('the three dots: details, problems, and a manager can delete after a confirm', async () => {
    let deleted = null;
    const api = mockApi({
      'GET /api/matrix': SUMMARY,
      'GET /api/matrix/cables': cablesHandler(),
      'DELETE /api/matrix/*': (url) => { deleted = url; return { ok: true }; },
    }, USERS.site_manager);
    renderScreen(<MatrixPage />, { path: '/matrix', user: USERS.site_manager });
    await screen.findByText('EOS-F-0141');
    fireEvent.click(screen.getByRole('button', { name: 'More' }));
    fireEvent.click(await screen.findByText('Details'));
    const details = screen.getByRole('dialog');
    expect(details.textContent).toContain('Patching');
    expect(details.textContent).toContain('Devices6');
    fireEvent.click(within(details).getByRole('button', { name: 'Back' }));
    fireEvent.click(screen.getByText('Rows that need checking'));
    expect(await screen.findByText('no A-end port')).toBeTruthy();
    expect(screen.getByText('Must be fixed, row 14')).toBeTruthy();
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Back' }));
    fireEvent.click(await screen.findByText('Delete this Excel dataset'));
    const confirm = await screen.findByRole('button', { name: 'Delete this Excel dataset' });
    expect(deleted).toBeNull();
    await act(async () => { fireEvent.click(confirm); });
    await waitFor(() => expect(deleted).toBe('/api/matrix/11'));
    expect(api.calls.filter((c) => c.path === '/api/matrix').length).toBeGreaterThan(1);
  });

  test('the three dots list the workbook racks, and the details offer the template', async () => {
    mockApi({
      'GET /api/matrix': { ...SUMMARY, counts: { ...SUMMARY.counts, racks: 2 } },
      'GET /api/matrix/cables': cablesHandler(),
      'GET /api/matrix/inventory': { total: 2, items: [
        { rowNo: 2, rackName: 'A01', heightU: '42', expectedLabel: 'A01', site: 'HYD-DC1' },
        { rowNo: 3, rackName: 'Z01', heightU: '', expectedLabel: '', site: '' },
      ] },
    }, USERS.member);
    renderScreen(<MatrixPage />, { path: '/matrix', user: USERS.member });
    await screen.findByText('EOS-F-0141');
    fireEvent.click(screen.getByRole('button', { name: 'More' }));
    fireEvent.click(await screen.findByText('Racks'));
    expect(await screen.findByText('A01')).toBeTruthy();
    expect(screen.getByText('42 units, label A01, HYD-DC1')).toBeTruthy();
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Back' }));
    fireEvent.click(await screen.findByText('Details'));
    expect(within(screen.getByRole('dialog')).getByText('Download the template')).toBeTruthy();
  });

  test('a technician sees no delete, can check a rack pair and open earlier checks', async () => {
    mockApi({
      'GET /api/matrix': SUMMARY,
      'GET /api/matrix/cables': cablesHandler(),
      'GET /api/checks': [{ id: 5, aRack: 'R01-A12', zRack: 'R02-B07', at: '2026-09-26T10:58:00Z', by: 'Barry', counts: { matched: 351, wrongPort: 14, notInSheet: 9, notFound: 6, missingCable: 3 } }],
    }, USERS.member);
    renderScreen(<MatrixPage />, { path: '/matrix', user: USERS.member });
    await screen.findByText('EOS-F-0141');
    fireEvent.click(screen.getByRole('button', { name: 'More' }));
    await screen.findByText('Earlier versions');
    expect(screen.getByText('Check a rack pair')).toBeTruthy();
    expect(screen.queryByText('Delete this Excel dataset')).toBeNull();
    fireEvent.click(screen.getByText('Earlier label checks'));
    const link = await screen.findByRole('link', { name: /R01-A12 to R02-B07/ });
    expect(link.getAttribute('href')).toBe('/checks/5');
    expect(link.textContent).toContain('32 problems');
  });
});
