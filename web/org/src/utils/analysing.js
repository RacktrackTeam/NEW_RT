/* The stages of a read, shown one at a time while /api/analyze runs. */
export const STAGES = [
  'Finding the rack units',
  'Finding the devices',
  'Reading the ports',
  'Following the cables',
  'Reading the labels',
];

/* How long Done stays on screen before the result opens. */
export const DONE_HOLD_MS = 450;

export const holdDone = () => new Promise((r) => setTimeout(r, DONE_HOLD_MS));

/**
 * How far along the read is. The server does not report its stages, so the
 * line moves with time, slows as it goes and never reaches the end on its
 * own: only `finished` (the answer has arrived) shows Done.
 */
export function stageAt(elapsedMs, finished) {
  if (finished) return { index: STAGES.length, progress: 1, text: 'Done' };
  const progress = 0.92 * (1 - Math.exp(-Math.max(0, elapsedMs) / 22000));
  const index = Math.min(STAGES.length - 1, Math.floor((progress / 0.92) * STAGES.length * 1.15));
  return { index, progress, text: STAGES[index] };
}
