import { useEffect, useRef, useState } from 'react';
import { useLocation, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { useAuth } from '../AuthContext.jsx';
import { TopBar } from '../components/TopBar.jsx';
import { Page } from '../components/Page.jsx';
import { TextButton } from '../components/Button.jsx';
import { Sheet } from '../components/Sheet.jsx';
import { ListRow } from '../components/ListRow.jsx';
import { EmptyState, ErrorLine } from '../components/Text.jsx';
import { TabNav } from '../components/NavBar.jsx';
import { SwitchGroups, useScanSwitches } from '../components/SwitchInfo.jsx';
import { RackNameSheet } from '../components/RackNameSheet.jsx';
import { SomethingWrong } from '../components/SomethingWrong.jsx';
import { useToast } from '../components/Toast.jsx';
import useScan from '../hooks/useScan';
import useJSON from '../hooks/useJSON';
import { enc } from '../utils/http';
import { isManager, timeOf, dateOf, personName } from '../utils/format';
import { photoOf } from '../utils/rack';
import { getItem, setItem } from '../utils/safeStorage';
import { OverviewTab, DevicesTab, ConnectionsTab, FlagsTab, TopologyTab, ReportTab } from './result/Tabs.jsx';
import styles from './ResultPage.module.css';

export const RESULT_TABS = ['overview', 'devices', 'connections', 'switches', 'flags', 'topology', 'report'];

/**
 * The result of one scan, with its own tabs in the navigation's place:
 * Overview (the port finder: the photo, a device and a port to choose, the
 * port ringed and zoomed to with its cable and other end, the key numbers
 * in one line), Devices (the rack top unit first), Connections (every port
 * and its cable, by device), Switches (RackTrack's switch screen for this
 * rack), and under More: Topology (the cables drawn) and Report (everything
 * about the rack, with the PDF and CSV).
 *
 * The result opens as soon as the devices and ports are read. The server then
 * reads the labels, names the rack and decides its height in the background;
 * a quiet line says so, and the tabs are read again when that is done. The
 * header says how the rack was named, with a way to change it. "Which rack
 * is this?" opens by itself only when the server has finished and says it
 * could not name the rack.
 */
export default function ResultPage() {
  const { scanId } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const toast = useToast();
  const { user } = useAuth();
  const [params, setParams] = useSearchParams();
  const tab = RESULT_TABS.includes(params.get('tab')) ? params.get('tab') : 'overview';
  const scan = useScan(scanId);
  const { result, error, rackName, siteName, site, askName, settling, settled } = scan;
  const detail = useJSON(`/api/scans/${enc(scanId)}/detail`);
  const sheetCheck = useJSON(`/api/scans/${enc(scanId)}/sheet-check`);
  const [more, setMore] = useState(false);
  const [idSheet, setIdSheet] = useState(false);
  const [wrong, setWrong] = useState(false);
  const asked = useRef(false);
  const switches = useScanSwitches(tab === 'switches' ? scanId : null);

  useEffect(() => { if (location.state?.notice) toast(location.state.notice, 5000); }, [location.state, toast]);
  // The rack's name and the labels arrived after the first answer: read the
  // tabs again so they carry them.
  const { reload: reloadDetail } = detail;
  const { reload: reloadSheet } = sheetCheck;
  useEffect(() => { if (settled) { reloadDetail(); reloadSheet(); } }, [settled, reloadDetail, reloadSheet]);
  // Asked once per rack, and only when the server could not name the rack:
  // coming back to this page from a port, a device or the close-ups does not
  // ask again (Change the rack name stays in the menu).
  useEffect(() => {
    const key = `rt.askedRackName.${scanId}`;
    if (askName && site && !asked.current && !getItem(key)) { asked.current = true; setItem(key, '1'); setIdSheet(true); }
  }, [askName, site, scanId]);

  const title = rackName || detail.data?.rackLabel || 'Unidentified rack';
  const photo = result ? photoOf(result, scanId) : null;
  const reportName = `${String(rackName || scanId).replace(/[^A-Za-z0-9_-]/g, '_')}-report`;
  const d = detail.data;
  const setTab = (t) => setParams(t === 'overview' ? {} : { tab: t }, { replace: true });
  // The Overview's chosen device and port live in the address, so a link can open them.
  const pickDevice = params.get('device');
  const pickPort = params.get('port');
  const choose = (deviceId, portNo) => setParams(Object.fromEntries([
    ['device', deviceId != null && deviceId !== '' ? String(deviceId) : null],
    ['port', deviceId != null && portNo != null ? String(portNo) : null],
  ].filter(([, v]) => v != null)), { replace: true });
  const openDevice = (dev) => navigate(`/results/${enc(scanId)}/device/${enc(dev.deviceId ?? dev.index)}`);



  if (error && !result) {
    return (
      <>
        <TopBar backTo="/history" />
        <Page>
          <EmptyState text={error.status === 404 ? 'This scan could not be found. It may have been deleted.' : error.message} action={<TextButton to="/history">Go to History</TextButton>} />
        </Page>
      </>
    );
  }

  const when = result?.timestamp || d?.takenAt;
  const sub = [siteName, when ? `read ${dateOf(when)} at ${timeOf(when)}` : null, d?.by ? `by ${personName(d.by)}` : null].filter(Boolean).join(', ');
  const tabs = [
    { value: 'overview', label: 'Overview', icon: 'image' },
    { value: 'devices', label: 'Devices', icon: 'chip' },
    { value: 'connections', label: 'Connections', icon: 'cable' },
    { value: 'switches', label: 'Switches', icon: 'switch' },
    { value: 'flags', label: 'Flags', icon: 'alert' },
    { value: 'topology', label: 'Topology', icon: 'topology' },
    { value: 'report', label: 'Report', icon: 'report' },
  ];
  // "R01 - read from the rail label", with Change; or, when it could not be
  // named and the question was put away, one line to name it.

  return (
    <>
      <TopBar title={title} sub={sub || undefined} backTo="/scan" onMore={() => setMore(true)} moreLabel="Rack actions" />
      <TabNav items={tabs} value={tab} onChange={setTab} label="Result" />
      <Page className={styles.page}>
        <ErrorLine>{detail.error ? detail.error.message : ''}</ErrorLine>
        <div className={styles.body}>
          {tab === 'overview' ? (
            <OverviewTab
              scanId={scanId}
              photo={photo}
              detail={d}
              sheet={sheetCheck.data}
              sheetError={sheetCheck.error}
              deviceId={pickDevice}
              portNo={pickPort}
              onChoose={choose}
              manager={isManager(user?.role)}
              rackName={title}
              qualityWarning={result?.qualityWarning ? (result.qualityWarningMsg || 'This photo may not be clear enough.') : null}
              settling={settling}
              onCapChanged={() => { detail.reload(); sheetCheck.reload(); }}
              onWrong={() => setWrong(true)}
              onFlags={() => setTab('flags')}
            />
          ) : null}
          {tab === 'devices' ? <DevicesTab detail={d} onOpen={openDevice} /> : null}
          {tab === 'switches' ? <SwitchGroups groups={[{ key: 'r', rack: d?.rackLabel || null, res: switches, detail: d }]} /> : null}
          {tab === 'connections' ? <ConnectionsTab scanId={scanId} detail={d} photo={photo} onChanged={() => { detail.reload(); sheetCheck.reload(); }} /> : null}
          {tab === 'flags' ? <FlagsTab detail={d} sheet={sheetCheck.data} photo={photo} /> : null}
          {tab === 'topology' ? <TopologyTab detail={d} onOpen={openDevice} /> : null}
          {tab === 'report' ? <ReportTab scanId={scanId} detail={d} sheet={sheetCheck.data} photo={photo} rackName={title} reportName={reportName} when={when} by={d?.by} /> : null}
        </div>
      </Page>
      <Sheet open={more} onClose={() => setMore(false)} title={title}>
        <ListRow compact title="Take close-ups" to={`/results/${enc(scanId)}/zones`} />
        <ListRow compact title="Map two racks" to="/map" />
        {site ? <ListRow compact title={askName ? 'Name this rack' : 'Change rack name'} onClick={() => { setMore(false); setIdSheet(true); }} /> : null}
      </Sheet>
      <SomethingWrong
        open={wrong}
        onClose={() => setWrong(false)}
        racks={[{ scanId, label: title, devices: d?.devices || [] }]}
        onRackName={() => setIdSheet(true)}
        onChanged={() => { detail.reload(); sheetCheck.reload(); }}
      />
      <RackNameSheet
        open={idSheet}
        onClose={() => setIdSheet(false)}
        scanId={scanId}
        site={site}
        title={askName ? 'Which rack is this?' : 'Change the rack name'}
        why={askName ? 'Its label could not be read on the rail, and nothing read on it matched the Excel dataset.' : null}
        onDone={(identity) => { if (identity) scan.setIdentity(identity); detail.reload(); toast('Rack saved.'); }}
      />
    </>
  );
}
