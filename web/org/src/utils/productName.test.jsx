/* The owner's rule, 29 Sep 2026: "Don't mention RT-EOS anywhere in the UI
   which the user sees - that name is for the internal developer team; the
   product name is RackTrack." Internal identifiers (com.racktrack.eos, folder
   names, comments) may keep it; nothing a person reads may. */
import { describe, test, expect, vi } from 'vitest';
import { readFileSync, readdirSync, statSync, existsSync } from 'node:fs';
import { join } from 'node:path';
import { screen } from '@testing-library/react';
import HomePage from '../pages/HomePage.jsx';
import YouPage from '../pages/YouPage.jsx';
import ScanPage from '../pages/ScanPage.jsx';
import SignInPage from '../pages/SignInPage.jsx';
import HelpPage from '../pages/HelpPage.jsx';
import { NavBar, TabNav } from '../components/NavBar.jsx';
import { mockApi, renderScreen, USERS } from '../test/render.jsx';

vi.mock('@capacitor/core', () => ({ Capacitor: { isNativePlatform: () => false, getPlatform: () => 'web' } }));

const INTERNAL = 'RT-EOS';

function walk(dir, out = []) {
  if (!existsSync(dir)) return out;
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) walk(path, out);
    else out.push(path);
  }
  return out;
}

/** The source with its comments taken out: what is left can reach a screen. */
const withoutComments = (src) => src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:])\/\/.*$/gm, '$1');

describe('the product is called RackTrack on every screen', () => {
  test('no rendered page says RT-EOS', async () => {
    mockApi({ 'GET /api/home': { site: { id: 2, name: 'Rack Build Facility' }, recent: [] }, 'GET /api/scan-sites': { ok: true, sites: [] } }, USERS.member);
    const pages = [
      [<HomePage key="h" />, '/'],
      [<YouPage key="y" />, '/you'],
      [<ScanPage key="s" />, '/scan'],
      [<HelpPage key="p" />, '/help'],
    ];
    for (const [ui, path] of pages) {
      const view = renderScreen(ui, { path, user: USERS.member });
      await new Promise((r) => setTimeout(r, 20));
      expect(document.body.textContent, path).not.toContain(INTERNAL);
      expect(document.title).not.toContain(INTERNAL);
      view.unmount();
    }
    const signedOut = renderScreen(<SignInPage />, { path: '/login', user: null });
    expect(document.body.textContent).not.toContain(INTERNAL);
    signedOut.unmount();
  });

  test('the navigation says RackTrack', () => {
    mockApi({}, USERS.member);
    renderScreen(<><NavBar /><TabNav items={[{ value: 'overview', label: 'Overview', icon: 'image' }]} value="overview" onChange={() => {}} label="Result" /></>, { path: '/', user: USERS.member });
    expect(screen.getAllByText('RackTrack').length).toBeGreaterThan(0);
    expect(document.body.textContent).not.toContain(INTERNAL);
  });

  test('no screen text in the source, the page title or the app names says RT-EOS', () => {
    const guilty = walk('src')
      .filter((f) => /\.(jsx?|css|html|json)$/.test(f) && !/\.test\.jsx?$/.test(f))
      .filter((f) => withoutComments(readFileSync(f, 'utf8')).includes(INTERNAL));
    expect(guilty).toEqual([]);
    for (const f of ['index.html', 'capacitor.config.json', 'ios/App/App/Info.plist', 'android/app/src/main/res/values/strings.xml']) {
      if (existsSync(f)) expect(readFileSync(f, 'utf8'), f).not.toContain(INTERNAL);
    }
  });
});
