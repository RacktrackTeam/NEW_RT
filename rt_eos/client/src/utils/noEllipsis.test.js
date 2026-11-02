/* Nothing in this app cuts text short.
 *
 * The owner's rule, 23 September 2026, after a page came back with a button
 * cut short to "...": never show dots in place of words, anywhere in the
 * app. A label that does not fit wraps onto a second line; a long
 * unbroken identifier wraps mid-word rather than running off the screen.
 *
 * This is the guard. It reads the source itself rather than rendering
 * anything, because truncation is written in three different places and only
 * one of them is visible in a component test:
 *
 *   text-overflow: ellipsis   in a stylesheet
 *   -webkit-line-clamp        which also ends a paragraph in dots
 *   'x'.slice(0, n) + an ellipsis in the JavaScript that builds a label
 *
 * If you are here because this failed: do not add the file to the allow-list.
 * Let the words wrap - `overflow-wrap: anywhere` is the replacement, and it
 * keeps a long id inside the screen without hiding any of it.
 */
import { describe, test, expect } from 'vitest';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';

const ROOT = 'src';
/* Screens nobody can reach any more. They are kept so the work is not lost,
   they are not built into the app, and rewriting their layout would be an
   edit with no reader. */
const GONE = ['src/pages/_archive'];

function walk(dir, out = []) {
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (GONE.some((g) => path.startsWith(g))) continue;
    if (statSync(path).isDirectory()) walk(path, out);
    else out.push(path);
  }
  return out;
}

const files = walk(ROOT);
const read = (f) => readFileSync(f, 'utf8');

describe('no words are cut short', () => {
  test('no stylesheet ends a line in dots', () => {
    const guilty = files
      .filter((f) => f.endsWith('.css'))
      .filter((f) => /text-overflow\s*:\s*ellipsis|-webkit-line-clamp/.test(read(f)));
    expect(guilty).toEqual([]);
  });

  test('no component sets one in its own style attribute', () => {
    const guilty = files
      .filter((f) => f.endsWith('.jsx'))
      .filter((f) => !f.endsWith('.test.jsx'))
      .filter((f) => /textOverflow\s*:\s*'ellipsis'|WebkitLineClamp/.test(read(f)));
    expect(guilty).toEqual([]);
  });

  test('no label is built by cutting a string and adding dots', () => {
    const guilty = files
      .filter((f) => /\.jsx?$/.test(f) && !/\.test\.jsx?$/.test(f))
      .filter((f) => /slice\([^)]*\)\s*\+\s*['"`](\u2026|\.\.\.)/.test(read(f)));
    expect(guilty).toEqual([]);
  });
});
