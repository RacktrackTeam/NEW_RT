import { describe, test, expect, beforeEach } from 'vitest';
import { screen, fireEvent, within } from '@testing-library/react';
import { FlagsTab, ConnectionsTab, DevicesTab } from './Tabs.jsx';
import { mockApi, renderScreen } from '../../test/render.jsx';

/*
 * The weak spots of a whole-rack photo, on the screens: a device the photo
 * could not read closely enough asks for one close-up (one line, not a row of
 * "not clear" per port); a label read between two ports says both; a port
 * whose number was hidden on its tag says where the number came from.
 */
const port = (portNo, extra = {}) => ({
  portNo, kind: 'main', box: null, type: 'RJ45', state: 'unknown', stateSource: 'photo', confidence: 0.4,
  label: null, expected: null, tag: null, labelBelongsTo: null, status: 'unknown', uncapped: null, opticNoCap: null, capState: null, ...extra,
});
const DETAIL = {
  scanId: 'RK-W1', rackLabel: 'B12', matrix: { importId: 'x' }, units: [], notSeen: [],
  devices: [
    { deviceId: 2, type: 'Switch', name: 'DC-SW-01', unitTop: 20, unitBottom: 20, heightU: 1, ports: [], counts: { connected: 0, ports: 0 },
      closeUp: { why: 'no_ports', text: 'Take a close-up of DC-SW-01 to read its ports and caps' } },
    { deviceId: 3, type: 'Switch', name: 'nvs-sgkc-sis-sw05b', unitTop: 8, unitBottom: 8, heightU: 1, counts: { connected: 1, ports: 4 },
      closeUp: { why: 'ports', text: 'Take a close-up of nvs-sgkc-sis-sw05b to read its ports and caps' },
      ports: [
        port(1, { state: 'connected', status: 'matched', label: { text: 's-Sw05b Gi B12', source: 'flag', match: 'other_face', portFrom: 'other_face' }, expected: { cableLabel: 'C-1', other: { device: 'b08-reg-gwlc02', port: 'Gig05P', rack: 'B08' } } }),
        port(2, { labelsBetween: [{ text: 'FC-009', between: [2, 3], source: 'rack photo' }] }),
        port(3, { labelsBetween: [{ text: 'FC-009', between: [2, 3], source: 'rack photo' }] }),
        port(4),
      ] },
  ],
  cables: [
    { label: 'FC-009', labelSource: 'rack photo', status: 'not_found', from: { deviceId: 3, device: 'nvs-sgkc-sis-sw05b', portNo: null, between: [2, 3] }, to: null },
  ],
};
const SHEET = { rackLabel: 'B12', matrixImportId: 'x', missing: [], unexpected: [], units: [], uncapped: [], opticNoCap: [], capUnsure: [] };

beforeEach(() => { mockApi({}); });

describe('a device the rack photo could not read closely', () => {
  test('Flags: one close-up line per device, with the close-up action, and no flag counted for it', () => {
    renderScreen(<FlagsTab detail={DETAIL} sheet={SHEET} photo={null} />);
    const a = screen.getByRole('link', { name: /Take a close-up of DC-SW-01 to read its ports and caps/ });
    expect(a.getAttribute('href')).toBe('/results/RK-W1/zones?device=2');
    expect(screen.getByText('No port could be read on the rack photo.')).toBeTruthy();
    expect(screen.getByRole('link', { name: /Take a close-up of nvs-sgkc-sis-sw05b/ })).toBeTruthy();
    expect(screen.getByText('No flags')).toBeTruthy();
  });

  test('Devices: the row says it needs a close-up', () => {
    renderScreen(<DevicesTab detail={DETAIL} onOpen={() => {}} />);
    expect(screen.getAllByText(/Needs a close-up/).length).toBe(2);
  });

  test('Connections: a device with no port read is one line; a device read badly shows its line above the ports', () => {
    renderScreen(<ConnectionsTab scanId="RK-W1" detail={DETAIL} photo={null} />);
    expect(screen.getByRole('link', { name: /Take a close-up of DC-SW-01/ }).getAttribute('href')).toBe('/results/RK-W1/zones?device=2');
    expect(screen.getByRole('link', { name: /Take a close-up of nvs-sgkc-sis-sw05b/ })).toBeTruthy();
    expect(screen.queryAllByText(/not clear/i).filter((n) => !/rack photo/.test(n.textContent)).length).toBe(0);
  });
});

describe('labels and tags the photo half read', () => {
  test('a label between two ports is listed with both ports, and the port card says so', () => {
    renderScreen(<ConnectionsTab scanId="RK-W1" detail={DETAIL} photo={null} />);
    const row = screen.getByRole('button', { name: 'FC-009, between port 2 and 3' });
    fireEvent.click(row);
    const card = screen.getByRole('region', { name: /Port 2 of nvs-sgkc-sis-sw05b/ });
    expect(within(card).getByText('Label nearby')).toBeTruthy();
    expect(within(card).getByText('FC-009, between port 2 and 3')).toBeTruthy();
  });

  test('a port whose number was hidden on its tag says it came from the other face', () => {
    renderScreen(<ConnectionsTab scanId="RK-W1" detail={DETAIL} photo={null} />);
    fireEvent.change(screen.getByLabelText('RJ45 port number'), { target: { value: '1' } });
    fireEvent.click(screen.getByRole('button', { name: 'Find' }));
    const card = screen.getByRole('region', { name: /Port 1 of nvs-sgkc-sis-sw05b/ });
    expect(within(card).getByText('Hidden on the tag; taken from the other face of its tag')).toBeTruthy();
  });
});
