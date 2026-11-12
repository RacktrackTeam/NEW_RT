import { describe, test, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, act, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { Button, TextButton } from './Button.jsx';
import { Field, SelectField, SearchBox, CodeBoxes } from './Field.jsx';
import { ListRow } from './ListRow.jsx';
import { Status, statusOf } from './Status.jsx';
import { Sheet, DockSheet } from './Sheet.jsx';
import { ToastProvider, useToast } from './Toast.jsx';
import { Switch, Tick, Seg, Progress } from './Controls.jsx';
import { PhotoWithReadings, zoomTo } from './PhotoWithReadings.jsx';
import { RackThumb } from './RackThumb.jsx';
import { EmptyState, Facts, BigTitle } from './Text.jsx';
import { NavBar, navFor, hasOwnTabs } from './NavBar.jsx';
import { TopBar } from './TopBar.jsx';
import { RackElevation, PortCells, blocksOf } from './RackElevation.jsx';
import { DataTable, Legend } from './Ui.jsx';
import { Avatar } from './Avatar.jsx';
import { renderScreen, mockApi, USERS } from '../test/render.jsx';

beforeEach(() => { mockApi({}, USERS.member); });

describe('Button and TextButton', () => {
  test('the primary button is a real button that reports busy', () => {
    const onClick = vi.fn();
    render(<Button onClick={onClick} busy>Scan a rack</Button>);
    const b = screen.getByRole('button', { name: 'Scan a rack' });
    expect(b.disabled).toBe(true);
    expect(b.getAttribute('aria-busy')).toBe('true');
  });
  test('a text button with `to` is a link', () => {
    render(<MemoryRouter><TextButton to="/history">See all</TextButton></MemoryRouter>);
    expect(screen.getByRole('link', { name: 'See all' }).getAttribute('href')).toBe('/history');
  });
});

describe('Field', () => {
  test('a required field shows the red asterisk and its error', () => {
    render(<Field label="Port" required error="This device has 48 ports." value="49" onChange={() => {}} />);
    const input = screen.getByLabelText(/Port/);
    expect(input.getAttribute('aria-required')).toBe('true');
    expect(input.getAttribute('aria-invalid')).toBe('true');
    expect(screen.getByRole('alert').textContent).toBe('This device has 48 ports.');
  });
  test('select lists its options', () => {
    render(<SelectField label="Rack" value="b" onChange={() => {}} options={[{ value: 'a', label: 'R01' }, { value: 'b', label: 'R02' }]} />);
    expect(screen.getByLabelText('Rack').value).toBe('b');
  });
  test('search reports each change', () => {
    const onChange = vi.fn();
    render(<SearchBox value="" onChange={onChange} placeholder="Search by rack name" />);
    fireEvent.change(screen.getByLabelText('Search by rack name'), { target: { value: 'R01' } });
    expect(onChange).toHaveBeenCalledWith('R01');
  });
  test('code boxes take a pasted code', () => {
    const onChange = vi.fn();
    render(<CodeBoxes value="" onChange={onChange} />);
    fireEvent.change(screen.getByLabelText('Digit 1 of 6'), { target: { value: '481516' } });
    expect(onChange).toHaveBeenCalledWith('481516');
  });
});

describe('ListRow and Status', () => {
  test('a row with a link is one target', () => {
    render(<MemoryRouter><ListRow title="Excel dataset" meta="384 cables" to="/matrix" /></MemoryRouter>);
    expect(screen.getByRole('link').getAttribute('href')).toBe('/matrix');
  });
  test('status words carry their tone', () => {
    expect(statusOf('wrong_port')).toEqual({ word: 'Wrong port', tone: 'bad' });
    expect(statusOf('Not in sheet').tone).toBe('warn');
    render(<Status kind="matched" />);
    expect(screen.getByText('Matched')).toBeTruthy();
  });
});

describe('Sheet', () => {
  test('opens as a dialog and closes on Escape', () => {
    const onClose = vi.fn();
    render(<Sheet open onClose={onClose} title="Delete all pilot data?"><p>Gone for good.</p></Sheet>);
    expect(screen.getByRole('dialog', { name: 'Delete all pilot data?' })).toBeTruthy();
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).toHaveBeenCalled();
  });
  test('draws nothing when closed', () => {
    render(<Sheet open={false} title="x" />);
    expect(screen.queryByRole('dialog')).toBeNull();
  });
  test('the docked sheet grows from its grab line', () => {
    const onChange = vi.fn();
    render(<DockSheet expanded={false} onExpandedChange={onChange}><p>Devices</p></DockSheet>);
    fireEvent.click(screen.getByRole('button', { name: 'Show more' }));
    expect(onChange).toHaveBeenCalledWith(true);
  });
});

describe('Toast', () => {
  test('shows one line then goes', () => {
    vi.useFakeTimers();
    function Btn() { const t = useToast(); return <button type="button" onClick={() => t('Matrix saved. 384 cables.')}>go</button>; }
    render(<ToastProvider><Btn /></ToastProvider>);
    fireEvent.click(screen.getByText('go'));
    expect(screen.getByText('Matrix saved. 384 cables.')).toBeTruthy();
    act(() => { vi.advanceTimersByTime(3100); });
    expect(screen.queryByText('Matrix saved. 384 cables.')).toBeNull();
    vi.useRealTimers();
  });
});

describe('Switch, Tick, Seg, Progress', () => {
  test('switch flips', () => {
    const onChange = vi.fn();
    render(<Switch label="Never use for training" checked={false} onChange={onChange} />);
    fireEvent.click(screen.getByRole('switch'));
    expect(onChange).toHaveBeenCalledWith(true);
  });
  test('tick ticks', () => {
    const onChange = vi.fn();
    render(<Tick label="Uncapped" checked={false} onChange={onChange} />);
    fireEvent.click(screen.getByLabelText('Uncapped'));
    expect(onChange).toHaveBeenCalledWith(true);
  });
  test('seg selects', () => {
    const onChange = vi.fn();
    render(<Seg options={['Photo', 'Stitch', 'Video']} value="Photo" onChange={onChange} />);
    expect(screen.getByRole('tab', { name: 'Photo' }).getAttribute('aria-selected')).toBe('true');
    fireEvent.click(screen.getByRole('tab', { name: 'Stitch' }));
    expect(onChange).toHaveBeenCalledWith('Stitch');
  });
  test('progress says how far', () => {
    render(<Progress value={0.25} label="Worklist" />);
    expect(screen.getByRole('progressbar').getAttribute('aria-valuenow')).toBe('25');
  });
});

describe('PhotoWithReadings', () => {
  test('draws each reading on the photo and reports a tap', () => {
    const onSelect = vi.fn();
    render(
      <PhotoWithReadings
        src="/api/scans/RK-1/files/photo.jpg"
        size={{ w: 1000, h: 2000 }}
        readings={[
          { id: 1, box: [0, 0, 500, 100], tag: 'U42 PP-24 A' },
          { id: 2, box: [0, 200, 1000, 400], tag: 'U19 PP-24 C', status: { word: 'Wrong unit', tone: 'bad' } },
        ]}
        onSelect={onSelect}
      />,
    );
    const b = screen.getByRole('button', { name: 'U19 PP-24 C, Wrong unit' });
    expect(b.style.top).toBe('10%');
    expect(b.style.width).toBe('100%');
    fireEvent.click(b);
    expect(onSelect).toHaveBeenCalledWith(2);
  });
});

describe('RackThumb, text pieces, avatar', () => {
  test('a thumbnail links to its rack', () => {
    render(<MemoryRouter><RackThumb name="R01-A12" meta="10:42" status="check" to="/results/RK-1" src="/x.jpg" /></MemoryRouter>);
    expect(screen.getByRole('link').getAttribute('href')).toBe('/results/RK-1');
    expect(screen.getByText('Check')).toBeTruthy();
  });
  test('empty state is one sentence and one action', () => {
    render(<EmptyState text="No scans yet." action={<button type="button">Scan a rack</button>} />);
    expect(screen.getByText('No scans yet.')).toBeTruthy();
  });
  test('facts pair label and value', () => {
    render(<Facts pairs={[['Status', 'Cable plugged in']]} />);
    expect(screen.getByText('Status')).toBeTruthy();
  });
  test('big title with its grey line', () => {
    render(<BigTitle sub="EOS Pilot">Rack Build Facility</BigTitle>);
    expect(screen.getByRole('heading', { level: 1 }).textContent).toBe('Rack Build Facility');
  });
  test('avatar is the initials in text, never a drawing', () => {
    const { container } = render(<><Avatar user={{ username: 'mandar' }} /><Avatar user={{ username: 'priya.s' }} /></>);
    expect(screen.getByText('M')).toBeTruthy();
    expect(screen.getByText('PS')).toBeTruthy();
    expect(container.querySelector('svg:not(.appIcon)')).toBeNull();
  });
});

describe('navigation', () => {
  test('the nav bar has Home, Scan, Map, History (laptop) and You (phone), the profile at the end, the current one marked', () => {
    renderScreen(<NavBar />, { path: '/map' });
    const nav = screen.getByRole('navigation', { name: 'Main' });
    const links = within(nav).getAllByRole('link');
    expect(links.slice(0, 6).map((a) => a.textContent)).toEqual(['RackTrack', 'Home', 'Scan', 'Map', 'History', 'You']);
    expect(links[6].getAttribute('aria-label')).toMatch(/, your profile$/);
    expect(links[6].getAttribute('href')).toBe('/you');
    expect(within(nav).getByRole('link', { name: 'Map' }).getAttribute('aria-current')).toBe('page');
    expect(within(nav).getByRole('link', { name: 'Home' }).getAttribute('aria-current')).toBeNull();
    expect(document.body.classList.contains('has-nav')).toBe(true);
    expect(document.querySelector('nav svg:not(.appIcon)')).toBeNull();
  });
  test('an open result, one rack or two, puts its own tabs in the nav\'s place', () => {
    expect(hasOwnTabs('/results/RK-1')).toBe(true);
    expect(hasOwnTabs('/map/RK-1/RK-2')).toBe(true);
    expect(hasOwnTabs('/results/RK-1/device/2')).toBe(false);
    expect(hasOwnTabs('/map')).toBe(false);
    expect(hasOwnTabs('/scan')).toBe(false);
  });
    test('deeper screens belong to their place in the nav', () => {
    expect(navFor('/results/RK-1').label).toBe('Scan');
    expect(navFor('/checks/5').label).toBe('Map');
    expect(navFor('/settings').label).toBe('You');
    expect(navFor('/matrix/check').label).toBe('You');
  });
  test('a deeper bar has one back and its title', () => {
    const onMore = vi.fn();
    renderScreen(<TopBar title="R01-A12" sub="Rack Build Facility" onMore={onMore} />, { path: '/results/RK-1' });
    expect(screen.getAllByRole('button', { name: 'Back' }).length).toBe(1);
    fireEvent.click(screen.getByRole('button', { name: 'More' }));
    expect(onMore).toHaveBeenCalled();
  });
});

describe('rack elevation and tables', () => {
  const rows = [
    { unit: 3, kind: 'device', deviceId: 1, expected: 'SW-01', expectedStatus: 'ok' },
    { unit: 2, kind: 'device', deviceId: 1 },
    { unit: 1, kind: 'empty', deviceId: null, expected: 'PP-01', expectedStatus: 'missing' },
  ];
  test('units are grouped into blocks, a 2U device as one', () => {
    expect(blocksOf(rows).map((b) => [b.units, b.kind])).toEqual([[[3, 2], 'device'], [[1], 'empty']]);
  });
  test('the rack is drawn with divs: units, devices and what the sheet expects', () => {
    render(<RackElevation rows={rows} devices={[{ deviceId: 1, name: 'SW-01' }]} renderDevice={(d) => <b>{d.name}</b>} />);
    expect(screen.getByText('U2-3')).toBeTruthy();
    expect(screen.getByText('SW-01')).toBeTruthy();
    expect(screen.getByText('Sheet: PP-01')).toBeTruthy();
    expect(document.querySelector('svg:not(.appIcon)')).toBeNull();
  });
  test('port cells say their number and status, and report a tap', () => {
    const onPort = vi.fn();
    render(<PortCells ports={[{ portNo: 1, status: 'matched', statusWord: 'Matched' }, { portNo: 2, status: 'missing_cable', statusWord: 'Missing cable' }]} onPort={onPort} selected={2} />);
    fireEvent.click(screen.getByRole('button', { name: 'Port 1, Matched' }));
    expect(onPort).toHaveBeenCalledWith(expect.objectContaining({ portNo: 1 }));
    expect(screen.getByRole('button', { name: 'Port 2, Missing cable' }).getAttribute('aria-pressed')).toBe('true');
  });
  test('a table shows a dash for nothing and its empty sentence', () => {
    const { rerender } = render(<DataTable columns={[{ key: 'a', label: 'A', main: true }, { key: 'b', label: 'B' }]} rows={[{ a: 'x', b: null }]} />);
    expect(screen.getByText('x')).toBeTruthy();
    expect(screen.getByText('-')).toBeTruthy();
    rerender(<DataTable columns={[{ key: 'a', label: 'A' }]} rows={[]} empty="Nothing here." />);
    expect(screen.getByText('Nothing here.')).toBeTruthy();
  });
  test('the legend is in words', () => {
    render(<Legend items={[{ kind: 'matched', word: 'Matched' }, { kind: 'uncapped', word: 'Uncapped' }]} />);
    expect(screen.getByRole('list', { name: 'What the colours mean' }).textContent).toBe('MatchedUncapped');
  });
});

describe('PhotoWithReadings zoom', () => {
  test('a small box is enlarged up to five times and centred; a box near the edge keeps the photo filling the frame', () => {
    const nat = { w: 1000, h: 800 };
    const mid = zoomTo([490, 390, 510, 410], nat);
    expect(mid.k).toBe(5);
    expect(mid.style.transform).toBe('translate(0%, 0%) scale(5)');
    const edge = zoomTo([0, 0, 20, 20], nat);
    expect(edge.style.transformOrigin).toBe('10% 10%');
    expect(edge.style.transform).toBe('translate(40%, 40%) scale(5)');
    expect(zoomTo([0, 0, 1000, 800], nat)).toBeNull();
    expect(zoomTo(null, nat)).toBeNull();
  });

  test('zoomed to a box, the tags are put away and a ringed reading is drawn heavier', () => {
    const { container } = render(<PhotoWithReadings src="/x.jpg" size={{ w: 1000, h: 800 }} focus={[100, 100, 130, 120]}
      readings={[{ id: 1, box: [50, 50, 900, 200], tag: 'U40 SW-1' }, { id: 'p3', box: [100, 100, 130, 120], ring: true, status: { tone: 'bad' } }]} />);
    expect(screen.queryByText('U40 SW-1')).toBeNull();
    const layer = container.querySelector('[style*="scale"]');
    expect(layer).toBeTruthy();
    expect(container.querySelectorAll('[class*="ring"]').length).toBe(1);
  });
});
