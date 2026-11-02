import { useCallback, useEffect, useState } from 'react';
import { useMatrixUpload as useUpload, useSiteManagerName } from '../hooks/useMatrixUpload.jsx';
import { useAuth } from '../AuthContext.jsx';
import { TopBar } from '../components/TopBar.jsx';
import { Page } from '../components/Page.jsx';
import { Button, TextButton, Actions } from '../components/Button.jsx';
import { SearchBox } from '../components/Field.jsx';
import { ListRow } from '../components/ListRow.jsx';
import { Grey, EmptyState, ErrorLine, Loading, Facts, SectionLabel } from '../components/Text.jsx';
import { Sheet } from '../components/Sheet.jsx';
import { useToast } from '../components/Toast.jsx';
import { CheckPairSheet } from '../components/CheckPairSheet.jsx';
import { DownloadLink } from '../components/DownloadLink.jsx';
import { useJSON } from '../hooks/useJSON.js';
import { useIsLaptop } from '../hooks/useIsLaptop.js';
import { getJSON, deleteJSON, notBuilt, enc } from '../utils/http';
import { isManager, plural, shortDate, dateOf, whenShort } from '../utils/format';
import styles from './MatrixPage.module.css';

const NOT_BUILT = 'The Excel dataset is not available on this server yet.';
const PAGE = 50;

const pick = (o, ...keys) => {
  for (const k of keys) if (o && o[k] != null && o[k] !== '') return o[k];
  return null;
};

const personOf = (p) => (p && typeof p === 'object' ? (p.name || p.username || '') : (p || ''));

/** One import summary, whichever spelling the server used. */
export function normImport(i) {
  if (!i) return null;
  return {
    id: pick(i, 'id', 'importId', 'import_id'),
    fileName: pick(i, 'fileName', 'file_name') || 'Excel dataset',
    sheetName: pick(i, 'sheetName', 'sheet_name'),
    uploadedBy: personOf(pick(i, 'uploadedBy', 'uploaded_by')),
    uploadedAt: pick(i, 'uploadedAt', 'uploaded_at'),
    rowCount: pick(i, 'rowCount', 'row_count'),
    cableCount: pick(i, 'cableCount', 'cable_count'),
    problemCount: pick(i, 'problemCount', 'problem_count'),
    checkedCount: pick(i, 'checkedCount', 'checked_count'),
    revisions: Array.isArray(i.revisions) ? i.revisions : [],
    status: pick(i, 'status'),
  };
}

/** One cable line, whichever spelling the server used. */
export function normCable(c) {
  return {
    rowNo: pick(c, 'rowNo', 'row_no'),
    cableLabel: pick(c, 'cableLabel', 'cable_label') || '',
    aRack: pick(c, 'aRack', 'a_rack'),
    aUnit: pick(c, 'aUnit', 'a_unit'),
    aDevice: pick(c, 'aDevice', 'a_device'),
    aPort: pick(c, 'aPort', 'a_port'),
    zRack: pick(c, 'zRack', 'z_rack'),
    zUnit: pick(c, 'zUnit', 'z_unit'),
    zDevice: pick(c, 'zDevice', 'z_device'),
    zPort: pick(c, 'zPort', 'z_port'),
    cableType: pick(c, 'cableType', 'cable_type'),
    connector: pick(c, 'connector'),
    cableColor: pick(c, 'cableColor', 'cable_color'),
    notes: pick(c, 'notes'),
    labelA: pick(c, 'labelA', 'label_a'),
    labelZ: pick(c, 'labelZ', 'label_z'),
    labelAlias: pick(c, 'labelAlias', 'label_alias'),
    checked: c?.checked !== false,
    recordStatus: pick(c, 'recordStatus', 'record_status'),
    expectedState: pick(c, 'expectedState', 'expected_state'),
    connectionId: pick(c, 'connectionId', 'connection_id'),
    cableId: pick(c, 'cableId', 'cable_id'),
    sourceRevision: pick(c, 'sourceRevision', 'source_revision'),
    aEnd: c?.aEnd && typeof c.aEnd === 'object' ? c.aEnd : null,
    zEnd: c?.zEnd && typeof c.zEnd === 'object' ? c.zEnd : null,
    raw: c?.raw && typeof c.raw === 'object' ? c.raw : null,
  };
}

const TEMPLATE_NAME = 'RackTrack_A_Z_Source_of_Truth_Upload_Matrix.xlsx';
const templateLink = <DownloadLink path="/api/matrix/template" name={TEMPLATE_NAME}>Download the template</DownloadLink>;

const listOf = (d, key) => (Array.isArray(d) ? d : Array.isArray(d?.[key]) ? d[key] : []);

function EmptyMatrix({ user, mgr }) {
  const upload = useUpload();
  const managerName = useSiteManagerName(user, !mgr);
  const site = user?.tenant?.name;
  const start = site ? `No Excel dataset for ${site} yet.` : 'No Excel dataset for this site yet.';
  const sentence = mgr
    ? `${start} Upload the manager's workbook (.xlsx), every sheet filled in from the template.`
    : `${start} Ask ${managerName || 'your site manager'} to upload it.`;
  return (
    <>
      <TopBar title="Excel dataset" />
      <Page>
        <EmptyState
          className={styles.empty}
          text={sentence}
          action={mgr ? <Button size="auto" onClick={upload.choose} busy={upload.busy}>Import workbook</Button> : null}
        />
        {mgr ? <div className={styles.template}>{templateLink}</div> : null}
        {mgr ? upload.input : null}
        <ErrorLine>{upload.error}</ErrorLine>
      </Page>
    </>
  );
}

/** What the workbook expects at one end, in a few lines. */
function endPairs(c, e) {
  const E = e.toUpperCase();
  const end = c[`${e}End`] || {};
  const yes = (v) => (v && v !== 'N/A' ? v : null);
  return [
    [`${E}-end rack`, c[`${e}Rack`] || '-'],
    [`${E}-end unit`, c[`${e}Unit`] ? `U${c[`${e}Unit`]}${end.ruHeight && end.ruHeight !== '1' ? `, ${end.ruHeight} units high` : ''}` : '-'],
    [`${E}-end device`, c[`${e}Device`] || '-'],
    [`${E}-end port`, [c[`${e}Port`], end.portType].filter(Boolean).join(', ') || '-'],
    end.panelName ? [`${E}-end patch panel`, [end.panelName, end.panelPosition && `position ${end.panelPosition}`].filter(Boolean).join(', ')] : null,
    end.opticType ? [`${E}-end optic`, end.opticType] : null,
    yes(end.expectedOccupied) ? [`${E}-end occupied`, end.expectedOccupied] : null,
    yes(end.expectedDustCap) ? [`${E}-end dust cap`, end.expectedDustCap] : null,
    c[`label${E}`] ? [`${E}-end label`, <span key={`l${e}`} className="mono">{c[`label${E}`]}</span>] : null,
  ];
}

function CableSheet({ cable, onClose }) {
  const c = cable;
  const pairs = c ? [
    c.rowNo != null && ['Row', String(c.rowNo)],
    ['Cable label', <span key="l" className="mono">{c.cableLabel || '-'}</span>],
    !c.checked ? ['Checked', `No, ${[c.recordStatus !== 'Active' && c.recordStatus, c.expectedState !== 'Connected' && c.expectedState].filter(Boolean).join(', ') || 'not active'}`] : null,
    c.cableId ? ['Cable ID', c.cableId] : null,
    c.connectionId ? ['Connection ID', c.connectionId] : null,
    c.labelAlias ? ['Label alias', <span key="al" className="mono">{c.labelAlias}</span>] : null,
    ...endPairs(c, 'a'),
    ...endPairs(c, 'z'),
    ['Cable type', c.cableType || '-'],
    c.connector ? ['Connector', c.connector] : null,
    c.cableColor ? ['Colour', c.cableColor] : null,
    c.sourceRevision ? ['Revision', c.sourceRevision] : null,
    ['Notes', c.notes || '-'],
  ] : [];
  const raw = c?.raw ? Object.entries(c.raw).filter(([k]) => k) : [];
  return (
    <Sheet open={!!c} onClose={onClose} title={c ? `Cable ${c.cableLabel}` : ''}>
      <Facts pairs={pairs} />
      {raw.length ? (
        <>
          <SectionLabel as="h3">The line in the file</SectionLabel>
          <Facts pairs={raw.map(([k, v]) => [k, v == null || v === '' ? '-' : String(v)])} />
        </>
      ) : null}
      <Actions><TextButton onClick={onClose}>Close</TextButton></Actions>
    </Sheet>
  );
}

const MORE_TITLES = {
  menu: 'Excel dataset',
  details: 'Details',
  history: 'Earlier versions',
  problems: 'Rows that need checking',
  racks: 'Racks',
  devices: 'Devices',
  ports: 'Ports',
  checks: 'Earlier label checks',
  delete: 'Delete this Excel dataset',
};

function MoreSheet({ open, onClose, mgr, imp, counts, problems, onCheckPair, onDeleted }) {
  const toast = useToast();
  const [view, setView] = useState('menu');
  const [history, setHistory] = useState(null);
  const [checks, setChecks] = useState(null);
  const [items, setItems] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => { if (open) { setView('menu'); setError(''); } }, [open]);

  const load = (next) => {
    setView(next); setError('');
    if (next === 'racks' || next === 'devices' || next === 'ports') {
      setItems(null);
      getJSON(`/api/matrix/inventory?kind=${next}`)
        .then((d) => setItems(listOf(d, 'items')))
        .catch((e) => { setItems([]); setError(notBuilt(e) ? NOT_BUILT : e.message); });
      return;
    }
    const path = next === 'history' ? '/api/matrix/history' : next === 'checks' ? '/api/checks' : null;
    if (!path) return;
    const set = next === 'history' ? setHistory : setChecks;
    set(null);
    getJSON(path)
      .then((d) => set(next === 'history' ? listOf(d, 'imports').map(normImport) : listOf(d, 'checks')))
      .catch((e) => {
        set([]);
        setError(notBuilt(e) ? (next === 'checks' ? 'Label checks are not available on this server yet.' : 'Earlier versions are not available on this server yet.') : e.message);
      });
  };

  const remove = async () => {
    setBusy(true); setError('');
    try {
      await deleteJSON(`/api/matrix/${enc(imp.id)}`);
      toast('Excel dataset deleted.');
      onClose();
      onDeleted();
    } catch (e) {
      setError(notBuilt(e) ? NOT_BUILT : e.message);
    } finally {
      setBusy(false);
    }
  };

  const back = <Actions><TextButton onClick={() => { setView('menu'); setError(''); }}>Back</TextButton></Actions>;

  return (
    <Sheet open={open} onClose={onClose} title={MORE_TITLES[view]}>
      {view === 'menu' ? (
        <div>
          <ListRow title="Check a rack pair" onClick={() => { onClose(); onCheckPair(); }} />
          <ListRow title="Details" onClick={() => load('details')} />
          <ListRow title="Rows that need checking" right={String(counts.problems ?? problems.length)} onClick={() => load('problems')} />
          <ListRow title="Racks" right={String(counts.racks ?? 0)} onClick={() => load('racks')} />
          <ListRow title="Devices" right={String(counts.devices ?? 0)} onClick={() => load('devices')} />
          <ListRow title="Ports" right={String(counts.ports ?? 0)} onClick={() => load('ports')} />
          <ListRow title="Earlier versions" onClick={() => load('history')} />
          <ListRow title="Earlier label checks" onClick={() => load('checks')} />
          {mgr ? <ListRow title={<span className={styles.danger}>Delete this Excel dataset</span>} onClick={() => load('delete')} /> : null}
        </div>
      ) : null}

      {view === 'details' ? (
        <div>
          <Facts
            pairs={[
              ['File', imp.fileName],
              imp.sheetName ? ['Sheet', imp.sheetName] : null,
              ['Imported', imp.uploadedAt ? dateOf(imp.uploadedAt) : '-'],
              ['Imported by', imp.uploadedBy || '-'],
              imp.rowCount != null ? ['Rows in the file', String(imp.rowCount)] : null,
              counts.cables != null ? ['Cables', counts.checked != null && counts.checked !== counts.cables ? `${counts.cables} (${counts.checked} checked)` : String(counts.cables)] : null,
              counts.racks != null ? ['Racks', String(counts.racks)] : null,
              counts.devices != null ? ['Devices', String(counts.devices)] : null,
              counts.ports != null ? ['Ports', String(counts.ports)] : null,
              imp.revisions.length ? ['Revision', imp.revisions.join(', ')] : null,
              ['Rows that need checking', String(counts.problems ?? problems.length)],
            ]}
          />
          <div className={styles.template}>{templateLink}</div>
          {back}
        </div>
      ) : null}

      {view === 'history' ? (
        <div>
          {history == null ? <Loading /> : null}
          {history && !history.length && !error ? <Grey>No earlier versions.</Grey> : null}
          {(history || []).map((h, i) => (
            <ListRow
              key={h.id ?? i}
              title={h.fileName}
              meta={[h.cableCount != null && plural(h.cableCount, 'cable'), h.uploadedAt && `imported ${dateOf(h.uploadedAt)}`, h.uploadedBy && `by ${h.uploadedBy}`].filter(Boolean).join(', ')}
              right={imp && h.id === imp.id ? 'In use' : null}
            />
          ))}
          <ErrorLine>{error}</ErrorLine>
          {back}
        </div>
      ) : null}

      {view === 'problems' ? (
        <div>
          {!problems.length ? <Grey>Every row was read.</Grey> : null}
          {problems.map((p, i) => (
            <ListRow
              key={`${p.rowNo}-${i}`}
              compact
              title={p.reason}
              meta={[p.level === 'error' ? 'Must be fixed' : 'Worth tidying', p.sheet, p.rowNo != null && `row ${p.rowNo}`].filter(Boolean).join(', ')}
            />
          ))}
          {back}
        </div>
      ) : null}

      {view === 'racks' || view === 'devices' || view === 'ports' ? (
        <div>
          {items == null ? <Loading /> : null}
          {items && !items.length && !error ? <Grey>{`The workbook has no ${view}.`}</Grey> : null}
          {(items || []).map((x, i) => (
            <ListRow
              key={`${x.rowNo}-${i}`}
              compact
              title={view === 'racks' ? x.rackName : view === 'devices' ? x.deviceName : [x.deviceId, x.portName || x.portNumber].filter(Boolean).join(' ')}
              meta={(view === 'racks'
                ? [x.heightU && `${x.heightU} units`, x.expectedLabel && `label ${x.expectedLabel}`, x.site]
                : view === 'devices'
                  ? [x.rackId, x.ruStart && `U${x.ruStart}${x.ruHeight && x.ruHeight !== '1' ? ` to U${Number(x.ruStart) + Number(x.ruHeight) - 1}` : ''}`, x.role, x.model]
                  : [x.portType, x.expectedOccupied && `occupied ${x.expectedOccupied}`, x.expectedDustCap && x.expectedDustCap !== 'N/A' && `dust cap ${x.expectedDustCap}`, x.expectedLabel]
              ).filter(Boolean).join(', ')}
            />
          ))}
          <ErrorLine>{error}</ErrorLine>
          {back}
        </div>
      ) : null}

      {view === 'checks' ? (
        <div>
          {checks == null ? <Loading /> : null}
          {checks && !checks.length && !error ? <Grey>No label checks yet.</Grey> : null}
          {(checks || []).map((c) => {
            const n = c.counts || {};
            const problemsN = ['wrongPort', 'notInSheet', 'notFound', 'missingCable'].reduce((s, k) => s + (Number(n[k]) || 0), 0);
            return (
              <ListRow
                key={c.id}
                to={`/checks/${enc(c.id)}`}
                title={`${c.aRack || 'A-end'} to ${c.zRack || 'Z-end'}`}
                meta={[whenShort(c.at), personOf(c.by)].filter(Boolean).join(', ')}
                right={c.counts ? plural(problemsN, 'problem') : null}
              />
            );
          })}
          <ErrorLine>{error}</ErrorLine>
          {back}
        </div>
      ) : null}

      {view === 'delete' ? (
        <div>
          <Grey>{`This removes ${imp?.fileName || 'the Excel dataset'} for everyone at the site. Label checks already run stay as they are.`}</Grey>
          <ErrorLine>{error}</ErrorLine>
          <Actions>
            <Button size="auto" variant="danger" onClick={remove} busy={busy}>Delete this Excel dataset</Button>
            <TextButton onClick={() => setView('menu')}>Cancel</TextButton>
          </Actions>
        </div>
      ) : null}
    </Sheet>
  );
}

/** A cable end as "device port". */
const endOf = (device, port) => [device, port].filter(Boolean).join(' ') || '-';

function LoadedMatrix({ summary, mgr, laptop, reload }) {
  const imp = normImport(summary.import);
  const problems = listOf(summary, 'problems').map((p) => ({ rowNo: pick(p, 'rowNo', 'row_no'), reason: p.reason || 'needs checking', level: p.level || 'error', sheet: p.sheet || null }));
  const counts = {
    cables: imp.cableCount ?? summary.counts?.cables ?? null,
    checked: summary.counts?.checked ?? imp.checkedCount ?? null,
    racks: summary.counts?.racks ?? null,
    devices: summary.counts?.devices ?? null,
    ports: summary.counts?.ports ?? null,
    problems: imp.problemCount ?? summary.counts?.problems ?? null,
  };
  const upload = useUpload();

  const [q, setQ] = useState('');
  const [query, setQuery] = useState('');
  const [list, setList] = useState({ cables: [], total: 0, loading: true, error: '' });
  const [more, setMore] = useState(false);
  const [open, setOpen] = useState(null);
  const [pairOpen, setPairOpen] = useState(false);
  const [moreOpen, setMoreOpen] = useState(false);

  // Debounce the search box.
  useEffect(() => {
    const t = setTimeout(() => setQuery(q.trim()), 300);
    return () => clearTimeout(t);
  }, [q]);

  const pathFor = useCallback(
    (offset) => `/api/matrix/cables?q=${enc(query)}&device=&limit=${PAGE}&offset=${offset}`,
    [query],
  );

  useEffect(() => {
    let gone = false;
    setList((s) => ({ ...s, loading: true, error: '' }));
    getJSON(pathFor(0))
      .then((d) => { if (!gone) setList({ cables: listOf(d, 'cables').map(normCable), total: Number(d?.total) || 0, loading: false, error: '' }); })
      .catch((e) => { if (!gone) setList({ cables: [], total: 0, loading: false, error: notBuilt(e) ? NOT_BUILT : e.message }); });
    return () => { gone = true; };
  }, [pathFor, imp?.id]);

  const loadMore = async () => {
    setMore(true);
    try {
      const d = await getJSON(pathFor(list.cables.length));
      setList((s) => ({ ...s, cables: s.cables.concat(listOf(d, 'cables').map(normCable)), total: Number(d?.total) || s.total }));
    } catch (e) {
      setList((s) => ({ ...s, error: e.message }));
    } finally {
      setMore(false);
    }
  };

  const shown = list.cables;
  const hasMore = list.total > list.cables.length;
  const who = [
    imp.fileName,
    imp.uploadedAt && `imported ${shortDate(imp.uploadedAt)}${imp.uploadedBy ? ` by ${imp.uploadedBy}` : ''}`,
  ].filter(Boolean).join(', ');

  return (
    <>
      <TopBar title="Excel dataset" onMore={() => setMoreOpen(true)} />
      <Page>
        <div className={styles.head}>
          <div className={styles.headText}>
            {counts.cables != null ? <p className={styles.count}>{plural(counts.cables, 'cable')}</p> : null}
            <Grey m0 className={styles.who}>{`${who}.`}</Grey>
          </div>
          {mgr ? (
            <Button size="auto" className={styles.replace} onClick={upload.choose} busy={upload.busy}>
              Import a new workbook
            </Button>
          ) : null}
        </div>
        {mgr ? upload.input : null}
        <ErrorLine>{upload.error}</ErrorLine>

        <SearchBox className={styles.search} value={q} onChange={setQ} placeholder="Search: cable, device, port, or sw1 p7, pp1 10a, u13" />

        {list.loading ? <Loading /> : null}
        <ErrorLine>{list.error}</ErrorLine>
        {!list.loading && !list.error && !shown.length ? (
          <Grey>{query ? 'No cable matches. Try another label, device or port.' : 'The Excel dataset has no cables.'}</Grey>
        ) : null}

        {!list.loading && shown.length ? (laptop ? (
          <table className={styles.tbl}>
            <thead>
              <tr><th>Cable label</th><th>A-end</th><th>Z-end</th></tr>
            </thead>
            <tbody>
              {shown.map((c, i) => (
                <tr key={`${i}-${c.rowNo ?? ''}-${c.cableLabel}`} className={styles.tr} onClick={() => setOpen(c)}>
                  <td className="mono">
                    <button type="button" className={styles.cellBtn} onClick={(e) => { e.stopPropagation(); setOpen(c); }}>{c.cableLabel || '-'}</button>
                  </td>
                  <td>{endOf(c.aDevice, c.aPort)}</td>
                  <td>{endOf(c.zDevice, c.zPort)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <div>
            {shown.map((c, i) => (
              <ListRow
                key={`${i}-${c.rowNo ?? ''}-${c.cableLabel}`}
                compact
                title={<span className="mono">{c.cableLabel || '-'}</span>}
                meta={`${endOf(c.aDevice, c.aPort)} to ${endOf(c.zDevice, c.zPort)}`}
                onClick={() => setOpen(c)}
              />
            ))}
          </div>
        )) : null}

        {hasMore && !list.loading ? (
          <div className={styles.more}>
            <TextButton onClick={loadMore} disabled={more}>{more ? 'Loading' : 'Load more'}</TextButton>
            <Grey m0>{`Showing ${list.cables.length} of ${list.total}.`}</Grey>
          </div>
        ) : null}
      </Page>

      <CableSheet cable={open} onClose={() => setOpen(null)} />
      <CheckPairSheet open={pairOpen} onClose={() => setPairOpen(false)} />
      <MoreSheet
        open={moreOpen}
        onClose={() => setMoreOpen(false)}
        mgr={mgr}
        imp={imp}
        counts={counts}
        problems={problems}
        onCheckPair={() => setPairOpen(true)}
        onDeleted={reload}
      />
    </>
  );
}

export default function MatrixPage() {
  const { user } = useAuth();
  const mgr = isManager(user?.role);
  const laptop = useIsLaptop();
  const { data, error, loading, reload } = useJSON('/api/matrix');

  if (loading && !data) {
    return (<><TopBar title="Excel dataset" /><Page><Loading /></Page></>);
  }
  if (error) {
    return (
      <>
        <TopBar title="Excel dataset" />
        <Page>
          {notBuilt(error) ? <Grey>{NOT_BUILT}</Grey> : <ErrorLine>{error.message}</ErrorLine>}
          {!notBuilt(error) ? <TextButton onClick={reload}>Try again</TextButton> : null}
        </Page>
      </>
    );
  }
  if (!data?.import) return <EmptyMatrix user={user} mgr={mgr} />;
  return <LoadedMatrix summary={data} mgr={mgr} laptop={laptop} reload={reload} />;
}
