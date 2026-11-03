import { describe, test, expect } from 'vitest';
import { STAGES, stageAt } from './analysing';

describe('the analysing stage line', () => {
  test('starts on the first stage and steps through them in order', () => {
    expect(stageAt(0, false).text).toBe('Finding the rack units');
    const seen = [];
    for (let t = 0; t <= 120000; t += 500) {
      const s = stageAt(t, false).text;
      if (seen[seen.length - 1] !== s) seen.push(s);
    }
    expect(seen).toEqual(STAGES);
  });
  test('never claims done before the answer arrives', () => {
    const late = stageAt(10 * 60 * 1000, false);
    expect(late.text).toBe('Reading the labels');
    expect(late.progress).toBeLessThan(0.95);
  });
  test('jumps to Done with a full bar when the answer arrives', () => {
    expect(stageAt(1200, true)).toEqual({ index: STAGES.length, progress: 1, text: 'Done' });
  });
});
