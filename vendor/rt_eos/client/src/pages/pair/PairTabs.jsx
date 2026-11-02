import { lazy, Suspense, useEffect, useMemo, useRef, useState } from 'react';
import { AssetImg } from '../../components/AssetImg.jsx';
import { Button, TextButton } from '../../components/Button.jsx';
import { Sheet } from '../../components/Sheet.jsx';
import { Status } from '../../components/Status.jsx';
import { DownloadLink } from '../../components/DownloadLink.jsx';
import { KeyNumbers } from '../../components/KeyNumbers.jsx';
import { Facts, Loading, ErrorLine } from '../../components/Text.jsx';
import { Missing, SubTabs } from '../../components/Ui.jsx';
import { SceneBoundary } from '../result/RackTopology.jsx';
import { rtTopologyOf } from '../../utils/topologyAdapter';
import rtStyles from '../result/RackTopology.module.css';
import useJSON from '../../hooks/useJSON';
import { getJSON, postJSON, enc, notBuilt } from '../../utils/http';
import { shortDate, timeOf, NO_EXCEL_TEXT, personName } from '../../utils/format';
import { shortName } from '../../components/RackStencil.jsx';
import { PairRacks2D, planCables, STATUS_WORD } from './PairRacks2D.jsx';
import styles from './PairTabs.module.css';

const PairScene3D = lazy(() => import('../result/RackTopologyScene3D.jsx').then((m) => ({ default: m.PairTopologyScene3D })));

/**
 * The A to Z label check of two racks read together: the hook that runs it
 * (once by itself, again when the tags finish reading) and its tab.
 */
export function usePairCheck(aScanId, zScanId, hasMatrix, reading = false) {
  const list = useJSON('/api/checks');
  const [check, setCheck] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [tried, setTried] = useState(false);
  const wasReading = useRef(false);
  const stale = useRef(false);
  const last = useMemo(() => (Array.isArray(list.data) ? list.data : []).find((c) => c.aScanId === aScanId && c.zScanId === zScanId) || null, [list.data, aScanId, zScanId]);

  // What the last Run again changed, so the person sees it did run: the
  // number of cables whose result is not what it was, or none.
  const [changed, setChanged] = useState(null); // { n, at } after a Run again
  const statusOf = (c) => new Map((c?.cables || []).map((x) => [x.cableLabel, `${x.a?.status}|${x.z?.status}|${x.status}`]));
  const run = async ({ again = false } = {}) => {
    setBusy(true); setError('');
    try {
      const before = statusOf(check);
      const next = await postJSON('/api/checks', { aScanId, zScanId });
      setCheck(next);
      if (again) {
        const after = statusOf(next);
        let n = 0;
        for (const k of new Set([...before.keys(), ...after.keys()])) if (before.get(k) !== after.get(k)) n += 1;
        setChanged({ n, at: next?.at || new Date().toISOString() });
      }
    } catch (e) {
      setError(notBuilt(e) ? 'Label checks are not available on this server yet.' : e.message);
    } finally { setBusy(false); }
  };

  useEffect(() => {
    if (!last) return undefined;
    let gone = false;
    getJSON(`/api/checks/${enc(last.id)}`).then((c) => {
      if (gone) return;
      setCheck(c);
      // A check saved before the tag positions were kept has no pictures: run it again once.
      const old = (c?.cables || []).some((x) => ['a', 'z'].some((e) => x[e]?.found && !('side' in x[e].found)));
      if (old && !stale.current) { stale.current = true; run(); }
    }).catch(() => {});
    return () => { gone = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [last]);

  useEffect(() => {
    if (tried || !hasMatrix || reading || !list.data || last || check || busy) return;
    setTried(true);
    run();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tried, hasMatrix, reading, list.data, last, check, busy]);

  // The tags were still being read when the page opened: once they are in,
  // the check runs again by itself, so an earlier empty check is not left up.
  useEffect(() => {
    if (reading) { wasReading.current = true; return; }
    if (!wasReading.current || !hasMatrix || busy) return;
    wasReading.current = false;
    setTried(true);
    run();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reading, hasMatrix, busy]);

  return { check, busy, error, run, changed, loading: list.loading || (!!last && !check) };
}

const END_WORD = { a: 'A-end', z: 'Z-end' };
const ORDER = { wrong_port: 0, missing_cable: 1, not_found: 2, on_tag: 3, matched: 4 };
const HOW = { normalised: 'read with a small correction', one_edit: 'one character corrected', two_edits: 'two characters corrected', tail: 'short name on the tag', other_face: 'Port from the other face of its tag', only_cable: 'Port from the only Excel cable on that device not yet seen' };

const endLine = (e) => {
  // Rack, device and port with a dot between them, so the three read apart.
  const x = e?.expected || e?.found;
  if (!x) return null;
  return [x.rack, x.device, x.port].filter((v) => v != null && v !== '').join(' · ');
};

/* What the photo says about one end of a cable, against the Excel. */
function photoSays(end) {
  const st = end?.status;
  const read = end?.found?.read;
  if (st === 'matched') return { tone: 'ok', mark: '\u2713 ', text: read ? `Tag reads ${read}` : 'Read on the device' };
  if (st === 'on_tag') return { tone: 'ok', mark: '\u2713 ', text: read ? `Printed on the tag: ${read}` : 'Printed on the tag' };
  if (st === 'wrong_port') return { tone: 'bad', mark: '\u2717 ', text: read ? `Tag ${read} is on another port` : 'On another port' };
  if (st === 'missing_cable') return { tone: 'bad', mark: '\u2717 ', text: 'Port is empty on the photo' };
  if (st === 'not_found') return { tone: 'none', mark: '', text: 'Not read on the photo' };
  return { tone: 'none', mark: '', text: 'Not on these two racks' };
}

/* One plain sentence: do the Excel and the photo agree for this cable? */
function verdictLine(x) {
  const rackOf = (e) => x[e]?.expected?.rack || (e === 'a' ? 'From' : 'To');
  const ok = (e) => ['matched', 'on_tag'].includes(x[e]?.status);
  const bad = (e) => ['wrong_port', 'missing_cable'].includes(x[e]?.status);
  if (ok('a') && ok('z')) return 'The photo matches the Excel at both ends.';
  if (bad('a') || bad('z')) {
    const e = bad('a') ? 'a' : 'z';
    return x[e].status === 'missing_cable' ? `The Excel has a cable at the ${rackOf(e)} end, but the photo shows that port empty.` : `At the ${rackOf(e)} end the photo does not match the Excel.`;
  }
  const one = ok('a') ? 'a' : ok('z') ? 'z' : null;
  if (one) {
    const other = one === 'a' ? 'z' : 'a';
    const why = x[other]?.status ? 'was not read on the photo' : 'is in a rack not photographed';
    return `The photo matches the Excel at the ${rackOf(one)} end; the ${rackOf(other)} end ${why}.`;
  }
  return 'Neither end was read on the photos yet. A close-up of the tag would settle it.';
}

/* The natural size of a photo, once it has loaded. */
function useNaturalSize() {
  const [size, setSize] = useState(null);
  const onLoad = (e) => setSize({ w: e.currentTarget.naturalWidth, h: e.currentTarget.naturalHeight });
  return [size, onLoad];
}

/* One rack photo, with the tags the check used outlined. */
function RackShot({ src, name, boxes }) {
  const [size, onLoad] = useNaturalSize();
  return (
    <figure className={styles.azShot}>
      <div className={styles.azShotPic}>
        {src ? <AssetImg src={src} alt={`${name} rack photo`} onLoad={onLoad} /> : null}
        {size ? boxes.map((b, i) => (
          <span key={i} className={styles.azBox} style={{ left: `${(b[0] / size.w) * 100}%`, top: `${(b[1] / size.h) * 100}%`, width: `${((b[2] - b[0]) / size.w) * 100}%`, height: `${((b[3] - b[1]) / size.h) * 100}%` }} />
        )) : null}
      </div>
      <figcaption>{name}</figcaption>
    </figure>
  );
}

/* The part of a rack photo where a tag was read, enlarged. */
export function TagCrop({ src, box, alt }) {
  const [size, onLoad] = useNaturalSize();
  if (!src || !Array.isArray(box)) return null;
  const pad = 24;
  const x0 = Math.max(0, box[0] - pad);
  const y0 = Math.max(0, box[1] - pad);
  const w = box[2] - box[0] + 2 * pad;
  const h = box[3] - box[1] + 2 * pad;
  const W = 96;
  const H = 64;
  const k = Math.min(W / w, H / h);
  const style = size ? { width: size.w * k, height: size.h * k, left: -x0 * k + (W - w * k) / 2, top: -y0 * k + (H - h * k) / 2 } : { visibility: 'hidden' };
  return (
    <span className={styles.azCrop}>
      <AssetImg src={src} alt={alt} onLoad={onLoad} style={style} data-zoom-box={box.join(',')} />
    </span>
  );
}

/* One end of a cable in the detail sheet: what the Excel says, what was read. */
function EndFacts({ which, end, photos }) {
  const want = end?.expected;
  const got = end?.found;
  const rows = [
    ['Excel', want ? [want.device, want.port && `port ${want.port}`, want.rack && `rack ${want.rack}`].filter(Boolean).join(', ') : 'Not in the Excel'],
    ['Result', end?.status ? <Status kind={end.status}>{end.status === 'not_found' ? 'Not read' : undefined}</Status> : 'Not on these two racks'],
    got?.read ? ['Read on the tag', <span key="r" className="mono">{got.read}</span>] : null,
    got?.match && HOW[got.match] ? ['Note', HOW[got.match]] : null,
    got?.between ? ['Read', `Between port ${got.between[0]} and ${got.between[1]}`] : null,
    got?.inRack ? ['Tag seen in', got.inRack] : null,
  ].filter(Boolean);
  return (
    <div className={styles.azEnd}>
      <p className={styles.azEndKick}>{END_WORD[which]}</p>
      {got?.box && photos?.[got.side] ? <div className={styles.azEndPic}><TagCrop src={photos[got.side]} box={got.box} alt={`The tag of the ${END_WORD[which]}`} /></div> : null}
      <Facts pairs={rows} />
    </div>
  );
}

/**
 * The label check of the two racks, compact: the numbers, one row per cable
 * (problems first), each opening its detail; cables read on a tag that the
 * Excel does not have are offered to add, never shown as an error.
 */
export function AzTab({ a, z, photos = {}, pair, manager, reading = false, onAdded }) {
  const hasMatrix = !!(a?.matrix || z?.matrix);
  const { check, busy, error, run, changed, loading } = pair;
  const [open, setOpen] = useState(null);
  const [adding, setAdding] = useState({});
  const [addError, setAddError] = useState('');
  if (!a || !z) return <Loading />;
  if (!hasMatrix) {
    return (
      <Missing
        icon="sheet"
        title="No Excel file uploaded"
        text={NO_EXCEL_TEXT}
        action={manager ? <Button size="auto" to="/matrix">Upload the Excel file</Button> : <span className={styles.grey}>Ask your site manager to upload the Excel file.</span>}
      />
    );
  }
  if (reading) {
    return <Loading label="Reading the cable tags on the photos. This takes about a minute; the check runs when it is done." />;
  }
  if (!check) {
    return (
      <>
        <ErrorLine>{error}</ErrorLine>
        {busy || loading ? <Loading label="Checking the labels on both racks" /> : (
          <Missing icon="link" title="The label check has not run" text="Run it to look every label read on the two racks up in the Excel dataset." action={<Button size="auto" onClick={run}>Run the check</Button>} />
        )}
      </>
    );
  }
  const c = check.counts || {};
  const all = check.cables || [];
  const known = all.filter((x) => x.status !== 'not_in_sheet').sort((x, y) => (ORDER[x.status] ?? 9) - (ORDER[y.status] ?? 9));
  const fresh = all.filter((x) => x.status === 'not_in_sheet' && x.add);
  const siteId = a.siteId ?? z.siteId;
  const add = async (rows) => {
    setAddError('');
    const keys = rows.map((r) => r.cableLabel);
    setAdding((s) => ({ ...s, ...Object.fromEntries(keys.map((k) => [k, true])) }));
    try {
      for (const r of rows) await postJSON('/api/matrix/cables', { siteId, cable: r.add });
      await run();
      onAdded?.(rows.length);
    } catch (e) {
      setAddError(e.message);
    } finally {
      setAdding((s) => { const n = { ...s }; keys.forEach((k) => delete n[k]); return n; });
    }
  };
  const sel = open ? all.find((x) => x.cableLabel === open) : null;
  const boxesOf = (k) => known.flatMap((x) => ['a', 'z'].map((e) => x[e]?.found)).filter((f) => f?.side === k && Array.isArray(f.box)).map((f) => f.box);
  const crops = (x) => ['a', 'z'].map((e) => x[e]?.found).filter((f) => f && Array.isArray(f.box) && photos[f.side]);
  return (
    <div className={styles.az}>
      <div className={styles.azShots}>
        <RackShot src={photos.a} name={a.rackLabel || 'Rack A'} boxes={boxesOf('a')} />
        <RackShot src={photos.z} name={z.rackLabel || 'Rack Z'} boxes={boxesOf('z')} />
      </div>
      <KeyNumbers className={styles.azKpis} label="The check in numbers" items={[
        { label: 'Matched', value: c.matched ?? 0, tone: 'ok' },
        c.onTag ? { label: 'On the tag', value: c.onTag, tone: 'ok' } : null,
        { label: 'Wrong port', value: c.wrongPort ?? 0, tone: c.wrongPort ? 'bad' : undefined },
        { label: 'Missing', value: c.missingCable ?? 0, tone: c.missingCable ? 'bad' : undefined },
        c.notFound ? { label: 'Not read', value: c.notFound, tone: 'warn' } : null,
      ]} />
      <ErrorLine>{error}</ErrorLine>
      {known.length ? (
        <ul className={styles.azRows}>
          {known.map((x, i) => (
            <li key={`${x.cableLabel}-${i}`}>
              <button type="button" className={styles.azCard} onClick={() => setOpen(x.cableLabel)}>
                <span className={styles.azTop}>
                  <span className={`mono ${styles.azName}`}>{x.cableLabel || 'No label'}</span>
                  <Status kind={x.status} className={styles.azSt} />
                </span>
                <span className={styles.azSay}>{verdictLine(x)}</span>
                <span className={styles.azCmp} role="table" aria-label={`${x.cableLabel}: the Excel against the photo`}>
                  <span className={styles.azCmpHead} role="row"><span role="columnheader" /><span role="columnheader">In the Excel</span><span role="columnheader">On the photo</span></span>
                  {['a', 'z'].map((e) => {
                    const p = photoSays(x[e]);
                    return (
                      <span key={e} className={styles.azCmpRow} role="row">
                        <em role="rowheader" className={e === 'a' ? styles.tagFrom : styles.tagTo}>{e === 'a' ? 'From' : 'To'}</em>
                        <span role="cell">{endLine(x[e]) || 'Not in the Excel'}</span>
                        <span role="cell" className={p.tone === 'ok' ? styles.azOk : p.tone === 'bad' ? styles.azBad : styles.azNone}>
                          {p.mark ? <b aria-hidden="true">{p.mark}</b> : null}{p.text}
                        </span>
                      </span>
                    );
                  })}
                </span>
                {crops(x).length ? (
                  <span className={styles.azCrops}>
                    {crops(x).map((f, k) => <TagCrop key={k} src={photos[f.side]} box={f.box} alt={`Tag read: ${f.read}`} />)}
                  </span>
                ) : null}
              </button>
            </li>
          ))}
        </ul>
      ) : <p className={styles.grey}>No cable of the Excel was found on these two racks yet. Take close-ups of the ports, then run the check again.</p>}
      {fresh.length ? (
        <section className={styles.azNew} aria-label="New cables read on the tags">
          <div className={styles.azNewHead}>
            <h3>New cables read on the tags</h3>
            {manager && fresh.length > 1 ? <TextButton small onClick={() => add(fresh)} disabled={Object.keys(adding).length > 0}>Add all to Excel</TextButton> : null}
          </div>
          <ul className={styles.azRows}>
            {fresh.map((x, i) => (
              <li key={`${x.cableLabel}-${i}`} className={styles.azItem}>
                <span className={styles.azMain}>
                  <span className={`mono ${styles.azName}`}>{x.cableLabel}</span>
                  <span className={styles.azPath}>{[[x.add.a_rack, x.add.a_device, x.add.a_port].filter(Boolean).join(' '), [x.add.z_rack, x.add.z_device, x.add.z_port].filter(Boolean).join(' ')].filter(Boolean).join('  to  ')}</span>
                </span>
                {manager ? <Button size="sm" variant="secondary" onClick={() => add([x])} busy={!!adding[x.cableLabel]}>Add to Excel</Button> : null}
              </li>
            ))}
          </ul>
          <ErrorLine>{addError}</ErrorLine>
        </section>
      ) : null}
      <div className={styles.azFoot}>
        <span className={styles.grey}>Checked {shortDate(check.at)} at {timeOf(check.at)}</span>
        <TextButton small onClick={() => run({ again: true })} disabled={busy}>{busy ? 'Checking' : 'Run again'}</TextButton>
        {changed && !busy ? (
          <span className={styles.grey} role="status">
            {changed.n ? `Checked again: ${changed.n} cable${changed.n === 1 ? '' : 's'} changed.` : 'Checked again against the latest Excel and corrections: nothing changed.'}
          </span>
        ) : null}
        <DownloadLink path={`/api/checks/${enc(check.id)}/report.pdf`} name={`label-check-${check.id}.pdf`}>Download PDF</DownloadLink>
        <DownloadLink path={`/api/checks/${enc(check.id)}/export.csv`} name={`label-check-${check.id}.csv`}>Download CSV</DownloadLink>
      </div>
      <Sheet open={!!sel} onClose={() => setOpen(null)} title={sel ? sel.cableLabel : ''}>
        {sel ? (
          <>
            <p className={styles.azSheetSt}><Status kind={sel.status} /></p>
            <EndFacts which="a" end={sel.a} photos={photos} />
            <EndFacts which="z" end={sel.z} photos={photos} />
          </>
        ) : null}
      </Sheet>
    </div>
  );
}

/**
 * The report of two racks read together, to read on screen: both rack
 * photos, the label check in numbers and every cable end to end (From, To,
 * result); the same as PDF, CSV or JSON. Each rack's own report follows it
 * (PairPage renders those).
 */
export function PairReport({ a, z, photos = {}, check, busy }) {
  const name = (d, w) => d?.rackLabel || w;
  const pair = `${name(a, 'Rack A')} and ${name(z, 'Rack Z')}`;
  if (!check) return <Loading label={busy ? 'Checking the labels on both racks' : 'The two-rack report is ready once the label check has run'} />;
  const c = check.counts || {};
  const stem = `${String(pair).replace(/[^A-Za-z0-9_-]+/g, '-')}-label-check`;
  const base = `/api/checks/${enc(check.id)}`;
  const side = (x, e) => {
    const ex = x[e]?.expected;
    if (ex) return [ex.rack, ex.device, ex.port].filter(Boolean).join(' ');
    if (x.add) return (e === 'a' ? [x.add.a_rack, x.add.a_device, x.add.a_port] : [x.add.z_rack, x.add.z_device, x.add.z_port]).filter(Boolean).join(' ') || 'Not read on the tag';
    return '-';
  };
  const rows = (check.cables || []).filter((x) => x.status !== 'not_in_sheet' || x.add)
    .sort((x, y) => (ORDER[x.status] ?? 8) - (ORDER[y.status] ?? 8));
  const boxes = (k) => rows.flatMap((x) => ['a', 'z'].map((e) => x[e]?.found)).filter((f) => f?.side === k && Array.isArray(f.box)).map((f) => f.box);
  return (
    <section className={styles.rep} aria-label={`${pair}, both racks`}>
      <div className={styles.repTop}>
        <div>
          <h2 className={styles.repTitle}>{pair}</h2>
          <p className={styles.grey}>Label check, {shortDate(check.at)} at {timeOf(check.at)}{check.by ? `, by ${personName(check.by)}` : ''}</p>
        </div>
        <span className={styles.repDl}>
          <DownloadLink path={`${base}/report.pdf`} name={`${stem}.pdf`}>PDF</DownloadLink>
          <DownloadLink path={`${base}/export.csv`} name={`${stem}.csv`}>CSV</DownloadLink>
          <DownloadLink path={`${base}/report.json`} name={`${stem}.json`}>JSON</DownloadLink>
        </span>
      </div>
      <div className={styles.azShots}>
        <RackShot src={photos.a} name={name(a, 'Rack A')} boxes={boxes('a')} />
        <RackShot src={photos.z} name={name(z, 'Rack Z')} boxes={boxes('z')} />
      </div>
      <KeyNumbers className={styles.azKpis} label="The check in numbers" items={[
        { label: 'Matched', value: c.matched ?? 0, tone: 'ok' },
        c.onTag ? { label: 'On the tag', value: c.onTag, tone: 'ok' } : null,
        { label: 'Wrong port', value: c.wrongPort ?? 0, tone: c.wrongPort ? 'bad' : undefined },
        { label: 'Missing', value: c.missingCable ?? 0, tone: c.missingCable ? 'bad' : undefined },
        c.notFound ? { label: 'Not read', value: c.notFound, tone: 'warn' } : null,
      ]} />
      <div className={styles.repList} role="table" aria-label="Every cable end to end">
        <div className={`${styles.repRow} ${styles.repHeadRow}`} role="row">
          <span role="columnheader">Cable</span><span role="columnheader">From</span><span role="columnheader">To</span><span role="columnheader">Result</span>
        </div>
        {rows.map((x, i) => (
          <div key={`${x.cableLabel}-${i}`} className={styles.repRow} role="row">
            <span role="cell" className={`mono ${styles.repLabel}`}>{x.cableLabel}</span>
            <span role="cell" className={styles.repEnd}><em className={styles.repK}>From</em>{side(x, 'a')}</span>
            <span role="cell" className={styles.repEnd}><em className={styles.repK}>To</em>{side(x, 'z')}</span>
            <span role="cell" className={styles.repRes}>{x.status === 'not_in_sheet' ? <span className={styles.grey}>New, not in the Excel yet</span> : <Status kind={x.status} />}</span>
          </div>
        ))}
      </div>
    </section>
  );
}

/**
 * The topology of two racks read together: both racks on one floor line with
 * the cables between them, and every cable as one compact row under them; a
 * row and a line in the drawing choose the same cable. The 3D view of one
 * rack is a switch away (PairPage).
 */
/**
 * Both racks together: the same small 2D / 3D switch as one rack. 2D is the
 * two elevations with the cables between them; 3D is both racks side by side
 * in one scene. A tap on a rack (its frame or name) opens that rack alone.
 */
export function PairTopology({ a, z, check, view = '2d', onView, onOpenRack }) {
  const [selected, setSelected] = useState(null);
  const drawn = useMemo(() => planCables(a, z, check), [a, z, check]);
  const topoA = useMemo(() => (view === '3d' ? rtTopologyOf(a) : null), [a, view]);
  const topoZ = useMemo(() => (view === '3d' ? rtTopologyOf(z) : null), [z, view]);
  const rows = (check?.cables || []).map((x, i) => ({ x, id: `c${i}` })).filter(({ x }) => x.status !== 'not_in_sheet');
  const end = (x, e) => {
    const ex = x[e]?.expected;
    return ex ? [ex.rack, shortName(ex.device), ex.port].filter(Boolean).join(' ') : '-';
  };
  const onDrawing = new Set(drawn.map((c) => c.id));
  return (
    <div className={styles.topo}>
      <div className={rtStyles.top}>
        <SubTabs
          className={rtStyles.view}
          label="Topology view"
          options={[{ value: '2d', label: '2D' }, { value: '3d', label: '3D' }]}
          value={view}
          onChange={(v) => { setSelected(null); onView?.(v); }}
        />
      </div>
      {view === '3d' ? (
        <>
          <div className={rtStyles.scene} data-testid="pair-scene">
            {topoA && topoZ ? (
              <SceneBoundary>
                <Suspense fallback={<p className={rtStyles.sceneNote}>Starting the 3D view</p>}>
                  <PairScene3D
                    topoA={topoA}
                    topoZ={topoZ}
                    plan={drawn}
                    selectedCable={selected?.kind === 'cable' ? selected.id : null}
                    onSelectCable={(id) => setSelected(id ? { kind: 'cable', id } : null)}
                    onOpenRack={onOpenRack}
                  />
                </Suspense>
              </SceneBoundary>
            ) : <Loading />}
          </div>
          <p className={rtStyles.hint}>Tap a rack to open it alone. Drag to turn, pinch or scroll to zoom, double tap to fly in.</p>
        </>
      ) : (
        <PairRacks2D a={a} z={z} check={check} selected={selected} onSelect={setSelected} onOpenRack={onOpenRack} />
      )}
      {rows.length ? (
        <section aria-label="Cables">
          <h3 className={styles.connHead}>Cables</h3>
          <ul className={styles.topoCables}>
            {rows.map(({ x, id }) => {
              const on = selected?.kind === 'cable' && selected.id === id;
              const tone = x.status === 'matched' || x.status === 'on_tag' ? 'ok' : x.status === 'not_found' ? 'grey' : 'bad';
              const row = (
                <>
                  <i className={styles.topoKey} data-tone={tone} aria-hidden="true" />
                  <span className={styles.topoLine}><span>{end(x, 'a')}</span><span className={styles.topoArrow} aria-hidden="true">to</span><span>{end(x, 'z')}</span></span>
                  <span className={styles.topoSt} data-tone={tone}>{STATUS_WORD[x.status] || 'Not checked'}</span>
                </>
              );
              return (
                <li key={id}>
                  {onDrawing.has(id)
                    ? <button type="button" className={styles.topoRow} aria-pressed={on} onClick={() => setSelected(on ? null : { kind: 'cable', id })}>{row}</button>
                    : <span className={styles.topoRow}>{row}</span>}
                </li>
              );
            })}
          </ul>
        </section>
      ) : null}
    </div>
  );
}
