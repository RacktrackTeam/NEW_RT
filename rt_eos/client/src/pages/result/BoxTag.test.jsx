import { describe, test, expect } from 'vitest';
import { boxTag } from './Tabs.jsx';

describe('the tag on a device box on the photo', () => {
  test('unit and label, else unit and kind; never make and model', () => {
    expect(boxTag({ unitTop: 8, sheetName: 'nvs-sgkc-sis-sw05b', type: 'Switch', make: 'Cisco', model: 'C1200-16P-2G' })).toBe('U8 nvs-sgkc-sis-sw05b');
    expect(boxTag({ unitTop: 7, labelText: 'SP-R1-U7-PP3', type: 'Patch Panel' })).toBe('U7 SP-R1-U7-PP3');
    expect(boxTag({ unitTop: 6, unitBottom: 5, type: 'Router', make: 'Cisco', model: 'ASR1001-X' })).toBe('U5-6 Router');
    expect(boxTag({ type: 'Server' })).toBe('Server');
  });
});
