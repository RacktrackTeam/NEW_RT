import { useCallback, useEffect, useState } from 'react';
import { TopBar } from '../components/TopBar.jsx';
import { Page, Two, Bar, Spacer } from '../components/Page.jsx';
import { Button, TextButton, Actions, Links } from '../components/Button.jsx';
import { ListRow } from '../components/ListRow.jsx';
import { Status } from '../components/Status.jsx';
import { Sheet } from '../components/Sheet.jsx';
import { Seg } from '../components/Controls.jsx';
import { SelectField, SearchBox } from '../components/Field.jsx';
import { Grey, SectionLabel, ErrorLine, Loading } from '../components/Text.jsx';
import { useToast } from '../components/Toast.jsx';
import useIsLaptop from '../hooks/useIsLaptop';
import { getJSON, postJSON, enc } from '../utils/http';
import { timeOf, whenShort, plural } from '../utils/format';
import styles from './ConsolePage.module.css';

const VIEWS = [{ value: 'activity', label: 'Activity' }, { value: 'log', label: 'Log' }];
const LEVELS = [{ value: '', label: 'All' }, { value: 'info', label: 'Info' }, { value: 'warn', label: 'Warnings' }, { value: 'error', label: 'Errors' }];

/* The tasks the pilot measures. Only what the server counts gets a number. */
const TASKS = ['Rack units', 'Device names', 'Port position', 'Label read'];

const ACTION = {
  'scan.create': 'read',
  'auth.login': 'signed in',
  'auth.logout': 'signed out',
  'invite.accept': 'joined from an invite',
  'auth.forgot_password': 'asked for a password reset',
};

/** One line for an event: "mandar read RK-9FFD84F1". */
export function eventLine(ev) {
  const who = ev.username || 'Someone';
  const what = ACTION[ev.action] || String(ev.action || 'did something').replace(/[._]/g, ' ');
  return [who, what, ev.action === 'scan.create' ? ev.target_id : null].filter(Boolean).join(' ');
}

const levelTone = (l) => (/error|fatal/i.test(l || '') ? 'failed' : /warn/i.test(l || '') ? 'check' : null);

function useRepeat(fn, ms, on) {
  useEffect(() => {
    if (!on) return undefined;
    const id = setInterval(fn, ms);
    return () => clearInterval(id);
  }, [fn, ms, on]);
}

function Activity({ live }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [more, setMore] = useState(false);
  const load = useCallback(() => {
    getJSON('/api/admin/dashboard').then((d) => { setData(d); setError(null); }).catch(setError);
  }, []);
  useEffect(() => { load(); }, [load]);
  useRepeat(load, 5000, live);
  if (error && !data) return <ErrorLine>{error.message}</ErrorLine>;
  if (!data) return <Loading />;
  const t = data.totals || {};
  const fb = data.feedback || {};
  const confirmed = (fb.right || 0) + (fb.wrong || 0);
  const events = (data.recent || []).slice(0, 12);
  return (
    <>
      <div className={styles.kpis}>
        <span><b>{t.scansToday ?? 0}</b> scans today</span>
        <span><b className={t.scansFail ? styles.bad : ''}>{t.scansFail ?? 0}</b> failed, all time</span>
        <span><b>{t.activeToday ?? 0}</b> people active today</span>
      </div>
      <Two eq>
        <div>
          <SectionLabel>Accuracy by task, confirmed by technicians</SectionLabel>
          <div className={styles.acc}>
            <span>Readings confirmed right</span>
            {fb.accuracy != null ? (
              <>
                <span className={styles.accbar}><i style={{ width: `${fb.accuracy}%` }} /></span>
                <b>{fb.accuracy}%</b>
                <Grey as="span" m0 className={styles.accNote}>{plural(confirmed, 'reading')} confirmed</Grey>
              </>
            ) : <Grey as="span" m0 className={styles.accWide}>Nothing confirmed yet</Grey>}
          </div>
          {TASKS.map((task) => (
            <div key={task} className={styles.acc}>
              <span>{task}</span>
              <Grey as="span" m0 className={styles.accWide}>Not measured yet</Grey>
            </div>
          ))}
          <div className={styles.acc}>
            <span>Dust cap</span>
            <Grey as="span" m0 className={styles.accWide}>Manual tick until a model exists</Grey>
          </div>
        </div>
        <div>
          <SectionLabel>Live</SectionLabel>
          {events.length ? events.map((ev, i) => (
            <ListRow key={`${ev.ts}-${i}`} compact title={eventLine(ev)} meta={[ev.org, whenShort(ev.ts)].filter(Boolean).join(', ')} right={ev.status === 'fail' ? <Status kind="failed" /> : null} />
          )) : <Grey>Nothing has happened yet.</Grey>}
          <p className={styles.mt16}><TextButton onClick={() => setMore(true)}>More figures</TextButton></p>
        </div>
      </Two>
      <Sheet open={more} onClose={() => setMore(false)} title="More figures">
        <SectionLabel>Top scanners</SectionLabel>
        {(data.topUsers || []).map((u) => <ListRow key={u.username} compact title={u.username} right={plural(u.scans, 'scan')} />)}
        <SectionLabel>By organization</SectionLabel>
        {(data.byOrg || []).map((o) => <ListRow key={o.org} compact title={o.org} right={plural(o.scans, 'scan')} />)}
        <SectionLabel>By site</SectionLabel>
        {(data.bySite || []).map((o) => <ListRow key={o.site} compact title={o.site} right={plural(o.scans, 'scan')} />)}
        <SectionLabel>Recent failures</SectionLabel>
        {(data.errors || []).length ? data.errors.map((e, i) => (
          <ListRow key={`${e.ts}-${i}`} compact title={eventLine(e)} meta={e.error} sub={whenShort(e.ts)} />
        )) : <Grey>No failures.</Grey>}
        <Actions><TextButton onClick={() => setMore(false)}>Close</TextButton></Actions>
      </Sheet>
    </>
  );
}

function Log({ live }) {
  const toast = useToast();
  const [level, setLevel] = useState('');
  const [q, setQ] = useState('');
  const [asked, setAsked] = useState('');
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [open, setOpen] = useState(null);
  const [detail, setDetail] = useState({});
  const [clear, setClear] = useState(false);
  const load = useCallback(() => {
    getJSON(`/api/logs?limit=300&level=${enc(level)}&q=${enc(asked)}`).then((d) => { setData(d); setError(null); }).catch(setError);
  }, [level, asked]);
  useEffect(() => { load(); }, [load]);
  useRepeat(load, 4000, live);
  const expand = async (id) => {
    setOpen(open === id ? null : id);
    if (detail[id]) return;
    try {
      const d = await getJSON(`/api/logs/${enc(id)}`);
      setDetail((m) => ({ ...m, [id]: d.log || d }));
    } catch (e) { setDetail((m) => ({ ...m, [id]: { error: e.message } })); }
  };
  const wipe = async () => {
    try {
      const r = await postJSON('/api/logs/clear', {});
      toast(`${plural(r?.removed ?? 0, 'line')} removed.`);
      setClear(false);
      load();
    } catch (e) { toast(e.message); }
  };
  const logs = data?.logs || [];
  return (
    <>
      <Bar>
        <SelectField compact label="Level" value={level} onChange={(e) => setLevel(e.target.value)} options={LEVELS} className={styles.level} />
        <form className={styles.search} onSubmit={(e) => { e.preventDefault(); setAsked(q.trim()); }}>
          <SearchBox value={q} onChange={(v) => { setQ(v); if (!v) setAsked(''); }} placeholder="Search the log" className={styles.box} />
        </form>
        <Spacer />
        <TextButton danger onClick={() => setClear(true)}>Clear the log</TextButton>
      </Bar>
      {error ? <ErrorLine>{error.message}</ErrorLine> : null}
      {!data && !error ? <Loading /> : null}
      {data ? <Grey>{data.total != null ? `${logs.length} of ${data.total} lines` : plural(logs.length, 'line')}</Grey> : null}
      {data && !logs.length ? <Grey>No lines match.</Grey> : null}
      {logs.map((l) => (
        <div key={l.id}>
          <ListRow
            compact
            title={l.msg || l.event || '(no message)'}
            meta={[timeOf(l.ts), l.method && l.url ? `${l.method} ${l.url}` : null, l.status ? `status ${l.status}` : null].filter(Boolean).join(', ')}
            right={levelTone(l.level_label) ? <Status kind={levelTone(l.level_label)}>{l.level_label}</Status> : <span className={styles.lvl}>{l.level_label}</span>}
            onClick={() => expand(l.id)}
            aria-expanded={open === l.id}
          />
          {open === l.id ? (
            <pre className={styles.detail}>{detail[l.id] ? JSON.stringify(detail[l.id], null, 2) : 'Opening'}</pre>
          ) : null}
        </div>
      ))}
      <Sheet open={clear} onClose={() => setClear(false)} title="Clear the whole log?">
        <Grey>Every line is removed for good. This cannot be undone.</Grey>
        <Actions>
          <Button size="auto" variant="danger" onClick={wipe}>Clear the log</Button>
          <TextButton onClick={() => setClear(false)}>Keep it</TextButton>
        </Actions>
      </Sheet>
    </>
  );
}

/**
 * The owner's view: today in three numbers, accuracy for each task the pilot
 * measures, and live activity. The server log is one switch away.
 */
export default function ConsolePage() {
  const laptop = useIsLaptop();
  const [view, setView] = useState('activity');
  const [live, setLive] = useState(false);
  const [tick, setTick] = useState(0);
  const seg = <Seg options={VIEWS} value={view} onChange={setView} label="Console view" className={styles.seg} />;
  return (
    <>
      <TopBar title="Console" right={laptop ? seg : null} />
      <Page>
        <Bar className={styles.head}>
          <Spacer />
          <Links className={styles.liveLinks}>
            <TextButton onClick={() => setLive((v) => !v)} aria-pressed={live}>{live ? 'Live: on' : 'Live: off'}</TextButton>
            <TextButton onClick={() => setTick((t) => t + 1)}>Refresh now</TextButton>
          </Links>
        </Bar>
        {!laptop ? seg : null}
        {view === 'activity' ? <Activity key={`a${tick}`} live={live} /> : <Log key={`l${tick}`} live={live} />}
      </Page>
    </>
  );
}
