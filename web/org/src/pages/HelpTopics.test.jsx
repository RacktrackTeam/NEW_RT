import { describe, expect, test } from 'vitest';
import { fireEvent, screen } from '@testing-library/react';
import { renderScreen, mockApi, USERS } from '../test/render.jsx';
import HelpPage from './HelpPage.jsx';
import { HelpSourcesPage, HelpDataPage, HelpCameraPage } from './HelpTopics.jsx';
import { sourceWords, withSource, matchWords } from '../utils/sources';

describe('Help', () => {
  test('the Help page links the two longer pages, and not the technical sources page', async () => {
    mockApi({}, USERS.member);
    renderScreen(<HelpPage />, { path: '/help' });
    expect(await screen.findByText('How your data is handled')).toBeTruthy();
    expect(screen.queryByText('Which details come from the photo and which from your Excel')).toBeNull();
    expect(screen.getByText('How your data is handled')).toBeTruthy();
    expect(screen.getByText('What the camera can tell today')).toBeTruthy();
  });

  test('where each detail comes from: every field, its source and how sure', async () => {
    mockApi({}, USERS.member);
    renderScreen(<HelpSourcesPage />, { path: '/help/sources' });
    for (const f of ['Unit (RU)', 'Height', 'Device kind', 'Make', 'Model', 'Port kind', 'Port state (plugged in or empty)', 'Cable (connector and colour)', 'Label on a cable or device', 'Rack name']) {
      expect(await screen.findByRole('button', { name: f })).toBeTruthy();
    }
    // Folded: a question opens to its answer.
    fireEvent.click(screen.getByRole('button', { name: 'Make' }));
    expect(screen.getByText('How well the text read matched a maker; otherwise the reading score.')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /The five sources/ }));
    expect(screen.getByText('Photo text (OCR)')).toBeTruthy();
    expect(screen.getByText('Excel dataset')).toBeTruthy();
  });

  test('how your data is handled says it plainly: the server, never train, retention, what leaves', async () => {
    mockApi({}, USERS.member);
    renderScreen(<HelpDataPage />, { path: '/help/data' });
    for (const b of await screen.findAllByRole('button', { expanded: false })) fireEvent.click(b);
    expect(await screen.findByText(/Photos are sent to the RackTrack server, read there/)).toBeTruthy();
    expect(screen.getByText(/kept apart for your organization and each site/)).toBeTruthy();
    expect(screen.getByText(/New organizations start at 90 days/)).toBeTruthy();
    expect(screen.getByText(/only a make and model name, to find its datasheet/)).toBeTruthy();
    expect(screen.queryByText(/Microsoft Graph/)).toBeNull();
    expect(screen.getByRole('button', { name: 'Deleting your data' })).toBeTruthy();
  });

  test('what the camera can tell today names the gaps plainly', async () => {
    mockApi({}, USERS.member);
    renderScreen(<HelpCameraPage />, { path: '/help/camera' });
    expect(await screen.findByText('What it reads from a photo')).toBeTruthy();
    expect(screen.getByText('What it cannot tell yet')).toBeTruthy();
    for (const b of screen.getAllByRole('button', { expanded: false })) fireEvent.click(b);
    expect(screen.getByText(/RJ45, SFP, QSFP, OSFP/)).toBeTruthy();
    expect(screen.getByText(/SFP\+ or SFP28 cannot be told from the photo/)).toBeTruthy();
    expect(screen.getByText(/empty fibre or SFP-type port has its dust cap on/)).toBeTruthy();
  });
});

describe('Sources in words', () => {
  test('a field shows its value only, not where it came from', () => {
    expect(withSource('D-Link', { source: 'photo_text', confidence: 0.82 })).toBe('D-Link');
    expect(sourceWords({ source: 'excel', confidence: null })).toBeNull();
    expect(sourceWords({ source: 'photo', confidence: null })).toBeNull();
    expect(sourceWords(null)).toBeNull();
    expect(matchWords('normalised')).toBeNull();
    expect(matchWords('exact')).toBeNull();
  });
});
