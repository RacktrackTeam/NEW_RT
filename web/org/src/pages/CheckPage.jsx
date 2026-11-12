import { useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { TopBar } from '../components/TopBar.jsx';
import { Page, Stick, Bar, Spacer } from '../components/Page.jsx';
import { Button, TextButton, Actions } from '../components/Button.jsx';
import { SelectField } from '../components/Field.jsx';
import { ListRow } from '../components/ListRow.jsx';
import { Status } from '../components/Status.jsx';
import { Grey, SectionHead, ErrorLine, Loading } from '../components/Text.jsx';
import { Sheet } from '../components/Sheet.jsx';
import { PhotoWithReadings } from '../components/PhotoWithReadings.jsx';
import { DownloadLink } from '../components/DownloadLink.jsx';
import { useJSON } from '../hooks/useJSON.js';
import { useIsLaptop } from '../hooks/useIsLaptop.js';
import { getJSON, postJSON, notBuilt, enc } from '../utils/http';
import { listDevices, deviceName, deviceTag, photoOf } from '../utils/rack';
import { plural, timeOf, shortDate, percent, toDate } from '../utils/format';
import styles from './CheckPage.module.css';

const NOT_BUILT = 'Label checks are not available on this server yet.';

/* The six counts, in the board's order, with their tone. */
const COUNTS = [
  ['matched', 'Matched', 'ok'],
  ['onTag', 'Printed on the tag', 'ok'],
  ['wrongPort', 'Wrong port', 'bad'],
  ['notInSheet', 'Not in sheet', 'warn'],
  ['notFound', 'Not found', 'warn'],
  ['missingCable', 'Missing cable', 'bad'],
  ['uncapped', 'No dust cap', 'warn'],
  ['opticNoCap', 'Optic fitted, cap off', 'warn'],
];

const STATUS_KEY = { matched: 'matched', on_tag: 'onTag', wrong_port: 'wrongPort', not_in_sheet: 'notInSheet', not_found: 'notFound', missing_cable: 'missingCable', uncapped: 'uncapped' };
/* An end that agrees with the sheet: read on the device, or printed on the cable's tag. */
const FINE = new Set(['matched', 'on_tag']);

const ENDS = ['a', 'z'];
const END_WORD = { a: 'A-end', z: 'Z-end' };

const lower = (s) => String(s || '').toLowerCase().replace(/[\s-]+/g, '_');

/** The port number in "Et18", "Ethernet1/18" or "18". */
export function portNumber(port) {
  const m = String(port ?? '').match(/(\d+)(?!.*\d)/);
  return m ? Number(m[1]) : null;
}

const endOf = (cable, e) => cable?.[e] || {};
const endStatus = (cable, e) => lower(endOf(cable, e).status || cable.status);
const deviceAt = (cable, e) => endOf(cable, e).found?.device || endOf(cable, e).expected?.device || null;

/** One plain sentence for a cable that did not match. */
export function problemSentence(cable) {
  const status = lower(cable.status);
  const hasData = (e) => endOf(cable, e).found || endOf(cable, e).expected;
  const bad = ENDS.find((e) => hasData(e) && endOf(cable, e).status && !FINE.has(endStatus(cable, e)))
    || ENDS.find((e) => {
      const { found, expected } = endOf(cable, e);
      return (found && expected && (found.port !== expected.port || found.device !== expected.device)) || (!found && expected);
    })
    || ENDS.find(hasData)
    || 'a';
  const { found, expected } = endOf(cable, bad);
  const w = END_WORD[bad];
  const st = endOf(cable, bad).status ? endStatus(cable, bad) : status;
  const where = (x) => (x ? [x.device, x.port].filter((v) => v != null && v !== '').join(' ') : '');
  if (st === 'wrong_port' && found && expected) {
    const sameDevice = found.device === expected.device;
    return sameDevice
      ? `${w}: in ${found.port}, sheet says ${expected.port}`
      : `${w}: in ${where(found)}, sheet says ${where(expected)}`;
  }
  if (st === 'missing_cable') return `${w}: ${expected?.port ?? where(found)} is empty`;
  if (st === 'not_found') return expected?.port != null ? `${w}: label on ${expected.port} not readable` : 'Label not read at either end';
  if (st === 'not_in_sheet') return `${w}: ${where(found || expected)}`;
  if (st === 'on_tag') return `${w}: printed on the cable's tag${found?.inRack ? ` in ${found.inRack}` : ''}`;
  if (st === 'uncapped') return `${w}: ${where(found || expected)} empty, cap off`;
  return `${w}: ${where(found || expected) || 'check this cable'}`;
}

/** The devices seen at one end, the busiest first. */
function devicesAt(cables, e) {
  const n = new Map();
  cables.forEach((c) => {
    const d = deviceAt(c, e);
    if (d) n.set(d, (n.get(d) || 0) + 1);
  });
  return [...n.entries()].sort((x, y) => y[1] - x[1]).map(([d]) => d);
}

function Counts({ counts, laptop }) {
  return (
    <p className={`${styles.counts} ${laptop ? styles.lc : ''}`}>
      {COUNTS.filter(([k]) => counts[k] != null).map(([k, word, tone]) => (
        <span key={k}><b className={styles[tone]}>{counts[k]}</b> {word}</span>
      ))}
    </p>
  );
}

/* The scan of one end: its real photo and the devices read on it. */
function useEndScan(scanId) {
  const [state, setState] = useState({ result: null, devices: [] });
  useEffect(() => {
    if (!scanId) return undefined;
    let gone = false;
    Promise.all([
      getJSON(`/api/scan/${enc(scanId)}`),
      getJSON(`/api/ocr/labels/${enc(scanId)}`).catch(() => null),
    ]).then(([result, labels]) => {
      if (!gone) setState({ result, devices: listDevices(result?.devices || [], labels) });
    }).catch(() => {});
    return () => { gone = true; };
  }, [scanId]);
  return state;
}

const same = (a, b) => !!a && !!b && String(a).trim().toLowerCase() === String(b).trim().toLowerCase();

/** The scan device a cable end names, matched by the label read on it or its name. */
export function matchDevice(devices, name) {
  if (!name) return null;
  return devices.find((d) => same(d.labelText, name)) || devices.find((d) => same(deviceName(d), name))
    || devices.find((d) => d.labelText && String(d.labelText).toLowerCase().includes(String(name).toLowerCase())) || null;
}

const TONE = { wrong_port: 'bad', missing_cable: 'bad', not_found: 'warn', not_in_sheet: 'warn', uncapped: 'warn' };

/* One end: the real rack photo, its devices outlined, problems in colour. */
function End({ e, scanId, rack, devices, device, onDevice, cables, selected }) {
  const { result, devices: seen } = useEndScan(scanId);
  const sel = cables.find((c) => c.cableLabel === selected);
  const worst = {};
  cables.forEach((c) => {
    const st = endStatus(c, e);
    const d = matchDevice(seen, deviceAt(c, e));
    if (d && TONE[st] && worst[d.index] !== 'bad') worst[d.index] = TONE[st];
  });
  const current = matchDevice(seen, device);
  const readings = seen.map((d) => ({
    id: d.index, box: d.box, tag: deviceTag(d),
    status: worst[d.index] ? { word: '', tone: worst[d.index] } : null,
  }));
  const selEnd = sel ? endOf(sel, e) : null;
  const where = selEnd ? (selEnd.found || selEnd.expected) : null;
  return (
    <div className={styles.end}>
      <p className={styles.endlab}>{`${END_WORD[e]}, ${rack || device || 'no rack'}`}</p>
      {devices.length > 1 ? (
        <SelectField compact className={styles.devSel} label={`${END_WORD[e]} device`} value={device || ''} onChange={(ev) => onDevice(ev.target.value)} options={devices} />
      ) : null}
      {result ? (
        <div className={styles.endPhoto}>
          <PhotoWithReadings
            src={photoOf(result, scanId)}
            alt={`${END_WORD[e]} rack photo`}
            readings={readings}
            selectedId={current?.index}
            tags={false}
            onSelect={(id) => { const d = seen.find((x) => x.index === id); const name = devices.find((n) => same(n, d?.labelText) || same(n, deviceName(d))); if (name) onDevice(name); }}
          />
        </div>
      ) : <Grey>{scanId ? 'Opening the photo of this rack.' : 'No photo for this end.'}</Grey>}
      {sel && where ? (
        <p className={styles.selLine}><span className="mono">{sel.cableLabel}</span>: {[where.device, where.port].filter(Boolean).join(' ')} <Status kind={endStatus(sel, e)} /></p>
      ) : null}
    </div>
  );
}

function Downloads({ id }) {
  return (
    <div className={styles.downloads}>
      <DownloadLink path={`/api/checks/${enc(id)}/report.pdf`} name={`label-check-${id}.pdf`}>Report for head office (PDF)</DownloadLink>
      <DownloadLink path={`/api/checks/${enc(id)}/export.csv`} name={`label-check-${id}.csv`}>One row per cable end (CSV)</DownloadLink>
    </div>
  );
}

export default function CheckPage() {
  const { checkId } = useParams();
  const laptop = useIsLaptop();
  const { data, error, loading, reload } = useJSON(`/api/checks/${enc(checkId)}`);

  if (loading && !data) return (<><TopBar title="Label check" backTo="/matrix" /><Page><Loading /></Page></>);
  if (error || !data) {
    return (
      <>
        <TopBar title="Label check" backTo="/matrix" />
        <Page>
          {notBuilt(error) ? <Grey>{NOT_BUILT}</Grey> : <ErrorLine>{error?.message || 'The check could not be read. Try again.'}</ErrorLine>}
          {!notBuilt(error) ? <TextButton onClick={reload}>Try again</TextButton> : null}
        </Page>
      </>
    );
  }
  return <Check check={data} laptop={laptop} />;
}

function Check({ check, laptop }) {
  const navigate = useNavigate();
  const cables = useMemo(() => (Array.isArray(check.cables) ? check.cables : []).map((c) => ({
    ...c, cableLabel: c.cableLabel ?? c.cable_label ?? '', status: lower(c.status),
  })), [check.cables]);

  const counts = useMemo(() => {
    const tally = {};
    cables.forEach((c) => { const k = STATUS_KEY[c.status]; if (k) tally[k] = (tally[k] || 0) + 1; });
    const from = check.counts || {};
    const snake = (k) => k.replace(/[A-Z]/g, (m) => `_${m.toLowerCase()}`);
    const out = {};
    COUNTS.forEach(([k]) => {
      const v = from[k] ?? from[snake(k)] ?? tally[k];
      if (v != null) out[k] = v;
      else if (k !== 'uncapped' && k !== 'opticNoCap' && k !== 'onTag') out[k] = 0;
    });
    return out;
  }, [check.counts, cables]);

  const devs = useMemo(() => ({ a: devicesAt(cables, 'a'), z: devicesAt(cables, 'z') }), [cables]);
  const [device, setDevice] = useState(() => ({ a: devs.a[0] || null, z: devs.z[0] || null }));
  const [selected, setSelected] = useState(null);
  const [allOpen, setAllOpen] = useState(false);
  const [dlOpen, setDlOpen] = useState(false);
  const [moreOpen, setMoreOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [runError, setRunError] = useState('');

  const select = (label, fromEnd) => {
    setSelected(label);
    const other = fromEnd === 'a' ? 'z' : 'a';
    const c = cables.find((x) => x.cableLabel === label);
    const d = c && deviceAt(c, other);
    if (d && devs[other].includes(d)) setDevice((s) => ({ ...s, [other]: d }));
  };

  const problems = cables.filter((c) => !FINE.has(c.status));
  // Cables whose label matched only after the reading was corrected (O for 0 ...).
  const shown = laptop || allOpen ? problems : problems.slice(0, 3);

  const at = toDate(check.at);
  const today = at && at.toDateString() === new Date().toDateString();
  const when = at ? (today ? timeOf(at) : `${shortDate(at)} ${timeOf(at)}`) : '';
  const title = `${check.aRack || 'A-end'} to ${check.zRack || 'Z-end'}`;
  const sub = [plural(cables.length, 'cable'), when && `checked ${when}`].filter(Boolean).join(', ');
  const rate = check.accuracy?.readRate ?? check.accuracy?.read_rate;
  const accuracy = rate != null ? `Read accuracy on this task: ${percent(rate, 1)}` : null;

  const runAgain = async () => {
    setBusy(true); setRunError('');
    try {
      const out = await postJSON('/api/checks', { aScanId: check.aScanId, zScanId: check.zScanId });
      const id = out?.id ?? out?.check?.id;
      setMoreOpen(false);
      if (id != null) navigate(`/checks/${enc(id)}`);
    } catch (e) {
      setRunError(notBuilt(e) ? NOT_BUILT : e.message);
    } finally {
      setBusy(false);
    }
  };

  const downloadBtn = <Button size={laptop ? 'auto' : undefined} onClick={() => setDlOpen(true)}>Download results</Button>;
  const list = (
    <div className={laptop ? styles.cols2 : undefined}>
      {shown.map((c, i) => (
        <ListRow
          key={`${c.cableLabel}-${i}`}
          title={<span className="mono">{c.cableLabel || '-'}</span>}
          meta={problemSentence(c)}
          right={<Status kind={c.status} />}
          onClick={() => {
            const e = ENDS.find((x) => !FINE.has(endStatus(c, x))) || 'a';
            const d = deviceAt(c, e);
            if (d && devs[e].includes(d)) setDevice((s) => ({ ...s, [e]: d }));
            select(c.cableLabel, e);
          }}
        />
      ))}
    </div>
  );

  return (
    <>
      <TopBar title={title} sub={laptop ? sub : undefined} backTo="/matrix" onMore={() => setMoreOpen(true)} />
      <Page stick={!laptop}>
        {!laptop ? <Grey m0 className={styles.sub}>{sub}</Grey> : null}
        <Counts counts={counts} laptop={laptop} />

        {cables.length ? (
          <>
            <div className={styles.azpair}>
              {ENDS.map((e) => (
                <End
                  key={e}
                  e={e}
                  rack={e === 'a' ? check.aRack : check.zRack}
                  scanId={e === 'a' ? check.aScanId : check.zScanId}
                  devices={devs[e]}
                  device={device[e]}
                  onDevice={(d) => setDevice((s) => ({ ...s, [e]: d }))}
                  cables={cables}
                  selected={selected}
                />
              ))}
            </div>
            <Grey>Red outline: a wrong port or missing cable on that device. Amber: a label not found or not in the sheet.</Grey>
          </>
        ) : <Grey>No cables were checked. Take the zone close-ups on both racks, then run the check again.</Grey>}

        {laptop ? (
          <Bar className={styles.probBar}>
            <h2 className={styles.st2}>{`Problems, ${problems.length}`}</h2>
            <Spacer />
            {accuracy ? <Grey m0>{accuracy}</Grey> : null}
            {downloadBtn}
          </Bar>
        ) : (
          <SectionHead
            title={`Problems, ${problems.length}`}
            action={problems.length > 3 ? <TextButton small onClick={() => setAllOpen((v) => !v)}>{allOpen ? 'Show fewer' : 'See all'}</TextButton> : null}
          />
        )}
        {problems.length ? list : <Grey>Every cable matched the sheet.</Grey>}
        {!laptop && accuracy ? <Grey className={styles.acc}>{accuracy}</Grey> : null}
      </Page>
      {!laptop ? <Stick>{downloadBtn}</Stick> : null}

      <Sheet open={dlOpen} onClose={() => setDlOpen(false)} title="Download results">
        <Downloads id={check.id} />
        <Actions><TextButton onClick={() => setDlOpen(false)}>Close</TextButton></Actions>
      </Sheet>

      <Sheet open={moreOpen} onClose={() => setMoreOpen(false)} title={title}>
        <Downloads id={check.id} />
        <div className={styles.again}>
          <TextButton onClick={runAgain} disabled={busy}>{busy ? 'Running the check' : 'Run the check again'}</TextButton>
        </div>
        <ErrorLine>{runError}</ErrorLine>
      </Sheet>
    </>
  );
}
