import { describe, test, expect } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { AuthProvider } from '../AuthContext.jsx';
import { ToastProvider } from '../components/Toast.jsx';
import CheckWorkbookPage from './CheckWorkbookPage.jsx';
import { mockApi, USERS } from '../test/render.jsx';

const UPLOAD = {
  uploadId: 'up-9',
  fileName: 'Hall2_design.xlsx',
  sheets: [
    { name: 'README', used: false, rows: 9 },
    { name: 'A_Z_CONNECTION_MATRIX', used: true, rows: 10 },
    { name: 'RACK_INVENTORY', used: true, rows: 2 },
  ],
  counts: { cables: 10, checked: 8, racks: 2, devices: 6, ports: 4, labelRules: 1, problems: 3, errors: 1, warnings: 2 },
  problems: [
    { level: 'error', sheet: 'A_Z_CONNECTION_MATRIX', rowNo: 5, reason: 'The A-end has no port, so this end cannot be checked.' },
    { level: 'warning', sheet: 'A_Z_CONNECTION_MATRIX', rowNo: 12, reason: 'Z_Rack_ID R03 is not in RACK_INVENTORY.' },
    { level: 'warning', sheet: 'PORT_INVENTORY', rowNo: 4, reason: 'Port_Type "SFP-X" is not one of the LOOKUPS values.' },
  ],
  revisions: ['Rev-1'],
};

function renderCheck(state, user = USERS.site_manager) {
  window.localStorage.clear();
  window.localStorage.setItem('rt_authUser', JSON.stringify(user));
  return render(
    <MemoryRouter initialEntries={[{ pathname: '/matrix/check', state }]}>
      <AuthProvider>
        <ToastProvider>
          <Routes>
            <Route path="/matrix/check" element={<CheckWorkbookPage />} />
            <Route path="/matrix" element={<p>Matrix screen</p>} />
          </Routes>
        </ToastProvider>
      </AuthProvider>
    </MemoryRouter>,
  );
}

describe('CheckWorkbookPage', () => {
  test('with no upload it says so and links back to the matrix', () => {
    mockApi({}, USERS.site_manager);
    renderCheck(undefined);
    expect(screen.getByText('There is no uploaded workbook to check. Import it from the Excel dataset page.')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Go to the Excel dataset' }).getAttribute('href')).toBe('/matrix');
  });

  test('shows what the workbook holds and its problems, errors first', () => {
    mockApi({}, USERS.site_manager);
    renderCheck({ upload: UPLOAD, fileName: 'Hall2_design.xlsx' });
    expect(screen.getByText('Hall2_design.xlsx')).toBeTruthy();
    expect(screen.getByText('1 problem must be fixed before those cables can be checked. You can import now and fix the workbook later.')).toBeTruthy();
    expect(screen.getByText('10 (8 checked, 2 planned, spare or not active)')).toBeTruthy();
    expect(screen.getByText('Rev-1')).toBeTruthy();
    expect(screen.getByText('Must be fixed to check these cables (1)')).toBeTruthy();
    expect(screen.getByText('Worth tidying (2)')).toBeTruthy();
    expect(screen.getByText('A_Z_CONNECTION_MATRIX, row 5')).toBeTruthy();
    expect(screen.getByText('For people only, not imported')).toBeTruthy();
    expect(screen.getByText('The cables, 10 rows')).toBeTruthy();
  });

  test('Import sends only the upload id and returns to the matrix', async () => {
    const api = mockApi({ 'POST /api/matrix/commit': { counts: { cables: 10 } } }, USERS.site_manager);
    renderCheck({ upload: UPLOAD });
    fireEvent.click(screen.getByRole('button', { name: 'Import workbook' }));
    await waitFor(() => expect(screen.getByText('Matrix screen')).toBeTruthy());
    const call = api.calls.find((c) => c.path === '/api/matrix/commit');
    expect(JSON.parse(call.body)).toEqual({ uploadId: 'up-9' });
  });

  test('a clean workbook says every cable can be checked', () => {
    mockApi({}, USERS.site_manager);
    renderCheck({ upload: { ...UPLOAD, problems: [], counts: { ...UPLOAD.counts, problems: 0, errors: 0, warnings: 0 } } });
    expect(screen.getByText('Every cable can be checked. Import it when the numbers below look right.')).toBeTruthy();
    expect(screen.queryByText(/Worth tidying/)).toBeNull();
  });
});
