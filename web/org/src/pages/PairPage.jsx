import { useEffect, useRef, useState } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { useAuth } from '../AuthContext.jsx';
import { TopBar } from '../components/TopBar.jsx';
import { Page } from '../components/Page.jsx';
import { Button, TextButton } from '../components/Button.jsx';
import Icon from '../components/Icon.jsx';
import { EmptyState } from '../components/Text.jsx';
import { TabNav } from '../components/NavBar.jsx';
import { SwitchGroups, useScanSwitches } from '../components/SwitchInfo.jsx';
import { RackNameSheet } from '../components/RackNameSheet.jsx';
import { SomethingWrong } from '../components/SomethingWrong.jsx';
import { Sheet } from '../components/Sheet.jsx';
import { ListRow } from '../components/ListRow.jsx';
import useJSON from '../hooks/useJSON';
import { useScanSite } from '../hooks/useScanSite';
import { useToast } from '../components/Toast.jsx';
import { photoOf } from '../utils/rack';
import { setItem } from '../utils/safeStorage';
import { enc } from '../utils/http';
import { isManager, dateOf, timeOf } from '../utils/format';
import { usePairCheck, AzTab, PairReport, PairTopology } from './pair/PairTabs.jsx';
import { OverviewTab, DevicesTab, ConnectionsTab, FlagsTab, TopologyTab, ReportTab } from './result/Tabs.jsx';
import styles from './ResultPage.module.css';
import pairStyles from './pair/PairTabs.module.css';

export const PAIR_TABS = ['az', 'overview', 'devices', 'connections', 'switches', 'flags', 'topology', 'report'];
const PER_RACK = new Set(['overview', 'devices', 'connections', 'flags']);

/**
 * The two racks read together, after Map's Analyse: A to Z (the label check
 * of the cables between them), then the same tabs as one rack (Devices,
 * Connections, Switches, Flags, Topology, Report), each for Rack A or
 * Rack Z as chosen at the top.
 */
/**
 * Which of the two racks the one-rack tabs show: one track split in two
 * halves, the rack shown raised in white, each half naming its end (Rack A,
 * Rack Z) over the rack's own name.
 */
function RackSwitch({ racks, value, onChange }) {
  return (
    <div className={pairStyles.rackSwitch} role="tablist" aria-label="Rack">
      {racks.map((r) => {
        const on = r.value === value;
        return (
          <button key={r.value} type="button" role="tab" aria-selected={on} aria-label={`${r.end}, ${r.name}`}
            className={on ? `${pairStyles.rackHalf} ${pairStyles.rackHalfOn}` : pairStyles.rackHalf} onClick={() => onChange(r.value)}>
            <span className={pairStyles.rackEnd}>{r.end}</span>
            <span className={pairStyles.rackName}>{r.name}</span>
          </button>
        );
      })}
    </div>
  );
}

export default function PairPage() {
  const { aScanId, zScanId } = useParams();
  const { user } = useAuth();
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const toast = useToast();
  const tab = PAIR_TABS.includes(params.get('tab')) ? params.get('tab') : 'az';
  const side = params.get('rack') === 'z' ? 'z' : 'a';
  // Topology: both racks (2D or 3D), or one of them alone after a tap on it.
  const topoView = params.get('view') === '3d' ? '3d' : '2d';
  const oneRack = tab === 'topology' && params.get('one') === '1' && (params.get('rack') === 'a' || params.get('rack') === 'z');
  const setTab = (t) => setParams(Object.fromEntries([['tab', t === 'az' ? null : t], ['rack', PER_RACK.has(t) && side === 'z' ? 'z' : null]].filter(([, v]) => v)), { replace: true });
  const setSide = (k) => setParams(Object.fromEntries([['tab', tab === 'az' ? null : tab], ['rack', k === 'z' ? 'z' : null]].filter(([, v]) => v)), { replace: true });
  const scanA = useJSON(`/api/scan/${enc(aScanId)}`);
  const scanZ = useJSON(`/api/scan/${enc(zScanId)}`);
  const shA = useJSON(`/api/scans/${enc(aScanId)}/sheet-check`);
  const shZ = useJSON(`/api/scans/${enc(zScanId)}/sheet-check`);

  const A = useJSON(`/api/scans/${enc(aScanId)}/detail`);
  const Z = useJSON(`/api/scans/${enc(zScanId)}/detail`);
  const swA = useScanSwitches(aScanId);
  const swZ = useScanSwitches(zScanId);
  const a = A.data;
  const z = Z.data;
  const hasMatrix = !!(a?.matrix || z?.matrix);
  const idA = useJSON(`/api/scan/${enc(aScanId)}/identity`);
  const idZ = useJSON(`/api/scan/${enc(zScanId)}/identity`);
  // The text and the cable tags on a photo are read after its first answer;
  // while that runs the page looks again every few seconds, does not ask for
  // a rack name yet, and holds the label check.
  const reading = !!(a?.reading?.labels || z?.reading?.labels);
  useEffect(() => {
    if (!reading) return undefined;
    const t = setInterval(() => { A.reload(); Z.reload(); idA.reload(); idZ.reload(); }, 4000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reading]);
  const pair = usePairCheck(aScanId, zScanId, a && z ? hasMatrix : false, reading);
  const manager = isManager(user?.role);

  // Each rack was named by the server after its analysis. A side it could
  // not name is asked about, one at a time, and only that side.
  const { sites } = useScanSite();
  const siteOf = (scanId) => (sites || []).find((st) => (st.racks || []).some((r) => r && String(r.rackId) === String(scanId)))
    || (sites?.length === 1 ? sites[0] : null);
  const needs = (id) => !reading && !!id.data && !id.data.rack && !!id.data.naming?.ask;
  const unnamed = [
    needs(idA) ? { key: 'a', word: 'Rack A', scanId: aScanId, id: idA, detail: A } : null,
    needs(idZ) ? { key: 'z', word: 'Rack Z', scanId: zScanId, id: idZ, detail: Z } : null,
  ].filter(Boolean);
  const [asking, setAsking] = useState(null);
  const [more, setMore] = useState(false);
  const [wrong, setWrong] = useState(false);
  const [rename, setRename] = useState(null);
  const askedFor = useRef(new Set());
  const nextKey = unnamed.find((u) => !askedFor.current.has(u.key) && siteOf(u.scanId))?.key || null;
  useEffect(() => {
    if (asking || !nextKey) return;
    askedFor.current.add(nextKey);
    setAsking(nextKey);
  }, [asking, nextKey]);
  const askingSide = unnamed.find((u) => u.key === asking) || null;

  const gone = A.error?.status === 404 || Z.error?.status === 404;
  if (gone) {
    return (
      <>
        <TopBar title="Two racks" backTo="/map" />
        <Page>
          <EmptyState text="One of these two scans could not be found. It may have been deleted." action={<TextButton to="/map">Map two racks again</TextButton>} />
        </Page>
      </>
    );
  }

  const nameA = a?.rackLabel || 'Rack A';
  const nameZ = z?.rackLabel || 'Rack Z';
  const when = a?.takenAt || z?.takenAt;
  const tabs = [
    { value: 'az', label: 'A to Z', icon: 'az' },
    { value: 'overview', label: 'Overview', icon: 'image' },
    { value: 'devices', label: 'Devices', icon: 'chip' },
    { value: 'connections', label: 'Connections', icon: 'cable' },
    { value: 'switches', label: 'Switches', icon: 'switch' },
    { value: 'flags', label: 'Flags', icon: 'alert' },
    { value: 'topology', label: 'Topology', icon: 'topology' },
    { value: 'report', label: 'Report', icon: 'report' },
  ];
  // The rack the per-rack tabs show.
  const cur = side === 'z'
    ? { scanId: zScanId, d: z, D: Z, sh: shZ, photo: scanZ.data ? photoOf(scanZ.data, zScanId) : null, name: nameZ }
    : { scanId: aScanId, d: a, D: A, sh: shA, photo: scanA.data ? photoOf(scanA.data, aScanId) : null, name: nameA };
  const other = side === 'z'
    ? { detail: a, photo: scanA.data ? photoOf(scanA.data, aScanId) : null }
    : { detail: z, photo: scanZ.data ? photoOf(scanZ.data, zScanId) : null };
  const photos = { a: scanA.data ? photoOf(scanA.data, aScanId) : null, z: scanZ.data ? photoOf(scanZ.data, zScanId) : null };
  const openDevice = (dev) => navigate(`/results/${enc(cur.scanId)}/device/${enc(dev.deviceId ?? dev.index)}`);
  const changed = () => { cur.D.reload(); cur.sh.reload(); };

  return (
    <>
      <TopBar title={`${nameA} and ${nameZ}`} sub={when ? `Read ${dateOf(when)} at ${timeOf(when)}` : undefined} backTo="/map" onMore={() => setMore(true)} moreLabel="Actions for the two racks" />
      <TabNav items={tabs} value={tab} onChange={setTab} label="Two racks" />
      <Page className={styles.page}>
        {unnamed.map((u) => (siteOf(u.scanId) ? (
          <p key={u.key} className={styles.nameLine}>
            <span className={styles.named}>{u.word} could not be named from its photo.</span>
            <TextButton small onClick={() => setAsking(u.key)}>Name {u.word}</TextButton>
          </p>
        ) : null))}
        <div className={styles.body}>
          {tab === 'switches' ? (
            <SwitchGroups groups={[
              { key: 'a', kicker: 'Rack A', rack: a?.rackLabel || null, res: swA, detail: a },
              { key: 'z', kicker: 'Rack Z', rack: z?.rackLabel || null, res: swZ, detail: z },
            ]} />
          ) : null}
          {tab === 'az' ? <AzTab a={a} z={z} photos={photos} pair={pair} manager={manager} reading={reading} onAdded={(n) => toast(n === 1 ? 'Added to the Excel.' : `${n} cables added to the Excel.`)} /> : null}
          {PER_RACK.has(tab) ? (
            <RackSwitch value={side} onChange={setSide} racks={[{ value: 'a', end: 'Rack A', name: nameA }, { value: 'z', end: 'Rack Z', name: nameZ }]} />
          ) : null}
          {tab === 'overview' ? (
            <OverviewTab
              scanId={cur.scanId}
              photo={cur.photo}
              detail={cur.d}
              sheet={cur.sh.data}
              sheetError={cur.sh.error}
              deviceId={params.get('device')}
              portNo={params.get('port')}
              onChoose={(deviceId, portNo) => setParams(Object.fromEntries([['tab', 'overview'], ['rack', side === 'z' ? 'z' : null], ['device', deviceId != null && deviceId !== '' ? String(deviceId) : null], ['port', deviceId != null && portNo != null ? String(portNo) : null]].filter(([, v]) => v != null)), { replace: true })}
              manager={manager}
              rackName={cur.name}
              onCapChanged={changed}
              onWrong={() => setWrong(true)}
              onFlags={() => setTab('flags')}
            />
          ) : null}
          {tab === 'devices' ? <DevicesTab detail={cur.d} onOpen={openDevice} /> : null}
          {tab === 'connections' ? <ConnectionsTab scanId={cur.scanId} detail={cur.d} photo={cur.photo} onChanged={changed} others={[other]} /> : null}
          {tab === 'flags' ? <FlagsTab detail={cur.d} sheet={cur.sh.data} photo={cur.photo} /> : null}
          {tab === 'topology' && oneRack ? (
            <>
              <p className={pairStyles.backLine}>
                <Button size="sm" variant="secondary" className={pairStyles.backBtn} onClick={() => setParams(Object.fromEntries([['tab', 'topology'], ['view', topoView === '3d' ? '3d' : null]].filter(([, v]) => v)))}>
                  <Icon name="arrow_back" />
                  Back to both racks
                </Button>
              </p>
              <TopologyTab detail={cur.d} onOpen={openDevice} />
            </>
          ) : null}
          {tab === 'topology' && !oneRack ? (
            <PairTopology
              a={a}
              z={z}
              check={pair.check}
              view={topoView}
              onView={(v) => setParams(Object.fromEntries([['tab', 'topology'], ['view', v === '3d' ? '3d' : null]].filter(([, x]) => x)), { replace: true })}
              onOpenRack={(k) => { setItem('topology.view', topoView); setParams(Object.fromEntries([['tab', 'topology'], ['view', topoView === '3d' ? '3d' : null], ['rack', k], ['one', '1']].filter(([, x]) => x))); }}
            />
          ) : null}
          {tab === 'report' ? (
            <>
              <PairReport a={a} z={z} photos={photos} check={pair.check} busy={pair.busy} />
              {[{ k: 'a', scanId: aScanId, d: a, sh: shA, name: nameA }, { k: 'z', scanId: zScanId, d: z, sh: shZ, name: nameZ }].map((r) => (
                <section key={r.k} aria-label={`${r.name} alone`}>
                  <h2 className={pairStyles.repRack}>{r.name}</h2>
                  <ReportTab scanId={r.scanId} detail={r.d} sheet={r.sh.data} photo={photos[r.k]} rackName={r.name} reportName={`${String(r.name).replace(/[^A-Za-z0-9_-]/g, '_')}-report`} when={r.d?.takenAt} by={r.d?.by} />
                </section>
              ))}
            </>
          ) : null}
        </div>
      </Page>
      <Sheet open={more} onClose={() => setMore(false)} title={`${nameA} and ${nameZ}`}>
        <ListRow compact title="Report a problem" onClick={() => { setMore(false); setWrong(true); }} />
        <ListRow compact title="Map two racks again" to="/map" />
      </Sheet>
      <SomethingWrong
        open={wrong}
        onClose={() => setWrong(false)}
        racks={[
          { scanId: aScanId, label: nameA, devices: a?.devices || [] },
          { scanId: zScanId, label: nameZ, devices: z?.devices || [] },
        ]}
        onRackName={(scanId) => setRename(scanId)}
        onChanged={() => { A.reload(); Z.reload(); shA.reload(); shZ.reload(); }}
      />
      <RackNameSheet
        open={!!rename}
        onClose={() => setRename(null)}
        scanId={rename}
        site={rename ? siteOf(rename) : null}
        title="Change the rack name"
        why={null}
        onDone={() => { idA.reload(); idZ.reload(); A.reload(); Z.reload(); }}
      />
      <RackNameSheet
        open={!!askingSide}
        onClose={() => setAsking(null)}
        scanId={askingSide?.scanId}
        site={askingSide ? siteOf(askingSide.scanId) : null}
        title={askingSide ? `Which rack is ${askingSide.word}?` : 'Which rack is this?'}
        why="Its label could not be read on the rail, and nothing read on it matched the Excel dataset."
        onDone={() => { askingSide?.id.reload(); askingSide?.detail.reload(); }}
      />
    </>
  );
}
