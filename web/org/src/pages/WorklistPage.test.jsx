import { describe, test, expect, afterEach } from 'vitest';
import { screen, fireEvent, waitFor } from '@testing-library/react';
import WorklistPage, { sortItems } from './WorklistPage.jsx';
import { renderScreen, mockApi, USERS } from '../test/render.jsx';

const ITEMS = [
  { id: 'w2', scanId: 'RK-1', kind: 'label', question: 'Is this EOS-F-0164 in Et9?', cropUrl: '/api/scans/RK-1/files/crops/l2.jpg', current: 'EOS-F-0164', confidence: 0.62 },
  { id: 'w1', scanId: 'RK-1', kind: 'label', question: { port: 'Et22' }, cropUrl: '/api/scans/RK-1/files/crops/l1.jpg', current: 'EOS-F-0207', confidence: 0.58, note: 'The sheet expects EOS-F-0207 in Et22.', where: 'Et22, sw-a1, R01-A12' },
  { id: 'w3', scanId: 'RK-2', kind: 'device', question: 'Is this an Arista 7280R3?', cropUrl: '/api/scans/RK-2/files/crops/d1.jpg', current: 'Arista 7280R3', confidence: 0.71, options: ['Arista 7280R3', 'Arista 7050X3'] },
];

function setLaptop(on) {
  window.matchMedia = on ? () => ({ matches: true, addEventListener() {}, removeEventListener() {} }) : undefined;
}

afterEach(() => { setLaptop(false); });

describe('WorklistPage', () => {
  test('sortItems puts the least sure first', () => {
    expect(sortItems(ITEMS).map((i) => i.id)).toEqual(['w1', 'w2', 'w3']);
  });

  test('shows the least sure item first, with its question, crop and tag', async () => {
    mockApi({ 'GET /api/worklist': { items: ITEMS } }, USERS.member);
    renderScreen(<WorklistPage />, { path: '/worklist', user: USERS.member });
    const q = await screen.findByText((_, el) => el?.tagName === 'P' && el.textContent === 'Is this EOS-F-0207 in Et22?');
    expect(q.querySelector('b.mono').textContent).toBe('EOS-F-0207');
    expect(screen.getByText('1 of 3')).toBeTruthy();
    expect(screen.getByText('58% sure. The sheet expects EOS-F-0207 in Et22.')).toBeTruthy();
    expect(screen.getByText('Et22, sw-a1, R01-A12')).toBeTruthy();
    expect(screen.getByRole('img').getAttribute('src')).toContain('/api/scans/RK-1/files/crops/l1.jpg');
    expect(screen.getByLabelText('No dust cap')).toBeTruthy();
    expect(screen.getByRole('progressbar')).toBeTruthy();
  });

  test('confirm posts the answer, toasts and moves to the next', async () => {
    let seen = null;
    mockApi({
      'GET /api/worklist': { items: ITEMS },
      'POST /api/worklist/*': (url, init) => { seen = { url, body: JSON.parse(init.body) }; return { ok: true }; },
    }, USERS.member);
    renderScreen(<WorklistPage />, { path: '/worklist', user: USERS.member });
    await screen.findByText('1 of 3');
    fireEvent.click(screen.getByLabelText('No dust cap'));
    fireEvent.click(screen.getByRole('button', { name: 'Yes, confirm' }));
    await waitFor(() => expect(seen).toEqual({ url: '/api/worklist/w1', body: { action: 'confirm', uncapped: true } }));
    expect(await screen.findByText('Confirmed. 2 left.')).toBeTruthy();
    expect(screen.getByText('2 of 3')).toBeTruthy();
    expect(screen.getByText('Is this EOS-F-0164 in Et9?')).toBeTruthy();
  });

  test('correct with options posts the chosen value; the device item has no Uncapped tick', async () => {
    let body = null;
    mockApi({
      'GET /api/worklist': { items: [ITEMS[2]] },
      'POST /api/worklist/w3': (url, init) => { body = JSON.parse(init.body); return { ok: true }; },
    }, USERS.member);
    renderScreen(<WorklistPage />, { path: '/worklist', user: USERS.member });
    await screen.findByText('Is this an Arista 7280R3?');
    expect(screen.queryByLabelText('No dust cap')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'No, correct it' }));
    fireEvent.change(screen.getByLabelText('What it really is'), { target: { value: 'Arista 7050X3' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save correction' }));
    await waitFor(() => expect(body).toEqual({ action: 'correct', value: 'Arista 7050X3' }));
    expect(await screen.findByText('Corrected. 0 left.')).toBeTruthy();
    expect(screen.getByText('Nothing to confirm. Every reading has been checked.')).toBeTruthy();
  });

  test('skip posts skip and follows the next item the server names', async () => {
    let body = null;
    mockApi({
      'GET /api/worklist': { items: ITEMS },
      'POST /api/worklist/w1': (url, init) => { body = JSON.parse(init.body); return { ok: true, next: { id: 'w3' } }; },
    }, USERS.member);
    renderScreen(<WorklistPage />, { path: '/worklist', user: USERS.member });
    await screen.findByText('1 of 3');
    fireEvent.click(screen.getByRole('button', { name: 'Skip' }));
    await waitFor(() => expect(body).toEqual({ action: 'skip' }));
    expect(await screen.findByText('Skipped.')).toBeTruthy();
    expect(screen.getByText('Is this an Arista 7280R3?')).toBeTruthy();
  });

  test('?scanId= keeps only that scan\'s items', async () => {
    const api = mockApi({ 'GET /api/worklist': { items: ITEMS } }, USERS.member);
    renderScreen(<WorklistPage />, { path: '/worklist?scanId=RK-2', pattern: '/worklist', user: USERS.member });
    expect(await screen.findByText('Is this an Arista 7280R3?')).toBeTruthy();
    expect(screen.getByText('1 of 1')).toBeTruthy();
    expect(api.calls.some((c) => c.url === '/api/worklist?scanId=RK-2')).toBe(true);
  });

  test('a laptop shows the queue; tapping a crop jumps to it', async () => {
    setLaptop(true);
    mockApi({ 'GET /api/worklist': { items: ITEMS } }, USERS.site_manager);
    renderScreen(<WorklistPage />, { path: '/worklist', user: USERS.site_manager });
    expect(await screen.findByText('1 of 3, least sure first')).toBeTruthy();
    const cur = screen.getByRole('button', { name: /EOS-F-0207/ });
    expect(cur.getAttribute('aria-current')).toBe('true');
    fireEvent.click(screen.getByRole('button', { name: /Arista 7280R3 71%/ }));
    expect(screen.getByText('Is this an Arista 7280R3?')).toBeTruthy();
  });

  test('empty list and a missing route each say one sentence', async () => {
    mockApi({ 'GET /api/worklist': { items: [] } }, USERS.member);
    const { unmount } = renderScreen(<WorklistPage />, { path: '/worklist', user: USERS.member });
    expect(await screen.findByText('Nothing to confirm. Every reading has been checked.')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Back to Home' }).getAttribute('href')).toBe('/');
    unmount();
    mockApi({}, USERS.member);
    renderScreen(<WorklistPage />, { path: '/worklist', user: USERS.member });
    expect(await screen.findByText('The worklist is not available on this server yet.')).toBeTruthy();
  });
});
