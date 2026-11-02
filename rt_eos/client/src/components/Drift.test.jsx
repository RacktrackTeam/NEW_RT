import { describe, expect, test } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { DriftLine, driftLines } from './Drift.jsx';

// The server's answer for one port (lib/drift.js), as in the teammate's example.
const DRIFT = {
  result: 'drift', device: 'SP-R1-U13-SW02', port: '23', excelRow: 2, mismatches: ['cableColor'],
  fields: [
    { field: 'device', label: 'Device', expected: 'SP-R1-U13-SW02', actual: 'SP-R1-U13-SW02', result: 'match' },
    { field: 'status', label: 'Connection status', expected: 'Connected', actual: 'Connected', result: 'match' },
    { field: 'connector', label: 'Connector type', expected: null, actual: 'RJ-45', result: 'not_in_excel' },
    { field: 'cableColor', label: 'Cable colour', expected: 'Blue', actual: 'Red', result: 'mismatch' },
  ],
};

describe('Drift', () => {
  test('a match is one green line, Matched', () => {
    const { container } = render(<DriftLine drift={{ ...DRIFT, result: 'no_drift', mismatches: [] }} />);
    expect(container.textContent).toBe('Matched');
    expect(container.querySelector('details')).toBeNull();
  });

  test('a drift says Drift detected, and the dropdown lists only what differs', () => {
    render(<DriftLine drift={DRIFT} />);
    const summary = screen.getByText('Drift detected');
    fireEvent.click(summary);
    const item = screen.getByRole('listitem');
    expect(item.textContent).toBe('Cable colour: Excel Blue, read Red');
    expect(screen.queryByText(/Connector type/)).toBeNull();
    expect(driftLines(DRIFT)).toHaveLength(1);
  });

  test('nothing without a result; a port the photo could not settle is not checked', () => {
    const { container } = render(<DriftLine drift={null} />);
    expect(container.textContent).toBe('');
    render(<DriftLine drift={{ ...DRIFT, result: 'not_checked' }} />);
    expect(screen.getByText(/Not checked/)).toBeTruthy();
  });
});
