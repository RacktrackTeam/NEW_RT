/* The owner's rule, 28 Sep 2026: "no SVG - only real and truth". No drawn
   illustrations, pictures of racks or stand-in drawings anywhere in the app;
   real photos and plain words instead. Rack elevations and port cells are
   HTML and CSS drawn from real data.

   29 Sep 2026: standard UI icons (navigation, buttons, rows) are allowed, the
   way RackTrack has them, from ONE component only - components/Icon.jsx.

   29 Sep 2026, later: the Topology tab of a rack result draws its devices and
   cables from real data, in ONE component - components/TopologyDiagram.jsx.
   Any other <svg> anywhere fails this test.

   1 Oct 2026: the one rack and the two-rack topology are both drawn by
   components/TopologyDiagram.jsx (racks on one floor line, the cables as
   lines); pages/result/RackTopology.jsx draws nothing itself any more. */
import { describe, test, expect } from 'vitest';
import { readFileSync, readdirSync, statSync, existsSync } from 'node:fs';
import { join } from 'node:path';

const ICONS = join('src', 'components', 'Icon.jsx');
const TOPOLOGY = join('src', 'components', 'TopologyDiagram.jsx');

function walk(dir, out = []) {
  if (!existsSync(dir)) return out;
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) walk(path, out);
    else out.push(path);
  }
  return out;
}

describe('no SVG pictures', () => {
  test('no .svg files in the app or its public folder', () => {
    expect([...walk('src'), ...walk('public')].filter((f) => f.toLowerCase().endsWith('.svg'))).toEqual([]);
  });
  test('no inline <svg> in the source, except the icon component and the topology diagram', () => {
    const guilty = walk('src')
      .filter((f) => /\.(jsx?|css|html)$/.test(f) && !f.endsWith('.test.js') && !f.endsWith('.test.jsx'))
      .filter((f) => f !== ICONS && f !== TOPOLOGY)
      .filter((f) => /<svg|image\/svg|\.svg['")]|<path\b|<circle\b|<rect\b/i.test(readFileSync(f, 'utf8')));
    expect(guilty).toEqual([]);
  });
  test('the icon component holds only small line glyphs: no images, text or embedded pictures', () => {
    const src = readFileSync(ICONS, 'utf8');
    expect(src).not.toMatch(/<image\b|<text\b|<foreignObject|data:image|href=/i);
    expect(src.match(/<svg\b/g)).toHaveLength(1);
    expect(src).toMatch(/viewBox="0 0 24 24"/);
  });
});

describe('the topology diagram', () => {
  test.each([TOPOLOGY])('%s draws from data only: no images, embedded pictures or links out', (file) => {
    if (!existsSync(file)) return;
    const src = readFileSync(file, 'utf8');
    expect(src).not.toMatch(/<image\b|<foreignObject|data:image|href=/i);
    expect(src.match(/<svg\b/g)).toHaveLength(1);
  });
});
