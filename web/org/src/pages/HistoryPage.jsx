import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { TopBar } from '../components/TopBar.jsx';
import { Page } from '../components/Page.jsx';
import { Button, TextButton } from '../components/Button.jsx';
import { SearchBox } from '../components/Field.jsx';
import { Sheet } from '../components/Sheet.jsx';
import { ListRow } from '../components/ListRow.jsx';
import { SectionLabel, EmptyState, ErrorLine, Loading, Grey } from '../components/Text.jsx';
import { RackThumb, Thumbs } from '../components/RackThumb.jsx';
import AssetImg from '../components/AssetImg.jsx';
import Status from '../components/Status.jsx';
import useJSON from '../hooks/useJSON';
import { dayLabel, timeOf, toDate, personName } from '../utils/format';
import { rackNameOf, scanIdOf, thumbOf } from '../utils/rack';
import styles from './HistoryPage.module.css';

const SORTS = [
  { value: 'new', label: 'Newest first' },
  { value: 'old', label: 'Oldest first' },
  { value: 'name', label: 'Rack name' },
];
const AGES = [
  { value: 0, label: 'All time' },
  { value: 7, label: 'Last 7 days' },
  { value: 30, label: 'Last 30 days' },
  { value: 90, label: 'Last 90 days' },
];
const PAGE = 48;

const collator = new Intl.Collator(undefined, { numeric: true, sensitivity: 'base' });

const idKey = (v) => String(v || '').toUpperCase();
const when = (s) => toDate(s?.timestamp || s?.takenAt)?.getTime() || 0;

/**
 * Two racks mapped together come back as two scans, each naming the other
 * (pair: { with, side, at }). They become one item: side a, side z, the
 * later of the two times. A pair is only folded when both scans are in the
 * list and each names the other; anything else stays a single scan.
 */
export function foldPairs(scans) {
  const list = Array.isArray(scans) ? scans : [];
  const byId = new Map(list.map((s) => [idKey(scanIdOf(s)), s]));
  const used = new Set();
  const out = [];
  for (const s of list) {
    const id = idKey(scanIdOf(s));
    if (used.has(id)) continue;
    const other = s.pair?.with ? byId.get(idKey(s.pair.with)) : null;
    const back = other?.pair?.with && idKey(other.pair.with) === id;
    if (other && back && idKey(scanIdOf(other)) !== id && !used.has(idKey(scanIdOf(other)))) {
      const [a, z] = s.pair.side === 'z' ? [other, s] : [s, other];
      used.add(id); used.add(idKey(scanIdOf(other)));
      const later = when(a) >= when(z) ? a : z;
      out.push({
        kind: 'pair',
        a,
        z,
        key: `pair-${scanIdOf(a)}-${scanIdOf(z)}`,
        name: `${rackNameOf(a)} and ${rackNameOf(z)}`,
        timestamp: later.timestamp || later.takenAt,
        by: a.by || a.username || z.by || z.username,
        status: a.status || z.status || (a.qualityWarning || z.qualityWarning ? 'check' : null),
      });
      continue;
    }
    used.add(id);
    out.push(s);
  }
  return out;
}

const nameOf = (s) => (s.kind === 'pair' ? s.name : rackNameOf(s));
const haystack = (s) => (s.kind === 'pair'
  ? `${rackNameOf(s.a)} ${rackNameOf(s.z)} ${s.a.rackId || ''} ${s.z.rackId || ''}`
  : `${rackNameOf(s)} ${s.rackId || ''}`);

/** Pairs folded, then search, age and order applied; grouped by day. */
export function shapeHistory(scans, { q = '', age = 0, sort = 'new', now = Date.now() } = {}) {
  const needle = q.trim().toLowerCase();
  let list = foldPairs(scans).filter((s) => {
    if (needle && !haystack(s).toLowerCase().includes(needle)) return false;
    if (age) {
      const t = toDate(s.timestamp || s.takenAt)?.getTime() || 0;
      if (now - t > age * 86400000) return false;
    }
    return true;
  });
  const time = (s) => toDate(s.timestamp || s.takenAt)?.getTime() || 0;
  if (sort === 'old') list = list.slice().sort((a, b) => time(a) - time(b));
  else if (sort === 'name') list = list.slice().sort((a, b) => collator.compare(nameOf(a), nameOf(b)));
  else list = list.slice().sort((a, b) => time(b) - time(a));
  return list;
}

/* Two racks mapped together: both pictures side by side, one name, one link. */
function PairThumb({ item }) {
  const { a, z } = item;
  const meta = [timeOf(item.timestamp), personName(item.by)].filter(Boolean).join(', ');
  return (
    <Link to={`/map/${encodeURIComponent(scanIdOf(a))}/${encodeURIComponent(scanIdOf(z))}`} className={styles.pair}>
      <span className={styles.pics}>
        {[a, z].map((s) => (
          <span key={scanIdOf(s)} className={styles.pic}>
            {thumbOf(s) ? <AssetImg src={thumbOf(s)} alt="" className={styles.img} loading="lazy" /> : null}
          </span>
        ))}
      </span>
      <span className={styles.name}>{item.name}</span>
      {meta ? <span className={styles.meta}>{meta}</span> : null}
      {item.status ? <Status kind={item.status} className={styles.st} /> : null}
    </Link>
  );
}

function groupByDay(list) {
  const groups = [];
  for (const s of list) {
    const label = dayLabel(s.timestamp || s.takenAt);
    const g = groups[groups.length - 1];
    if (g && g.label === label) g.items.push(s);
    else groups.push({ label, items: [s] });
  }
  return groups;
}

/**
 * Every rack as a picture, newest first, grouped by day. A technician sees
 * his own scans; a site manager the whole site (the server decides).
 */
export default function HistoryPage() {
  const { data, error, loading } = useJSON('/api/scans');
  const [q, setQ] = useState('');
  const [sort, setSort] = useState('new');
  const [age, setAge] = useState(0);
  const [shown, setShown] = useState(PAGE);
  const [sheet, setSheet] = useState(false);
  const scans = useMemo(() => (Array.isArray(data?.scans) ? data.scans : []), [data]);
  const list = useMemo(() => shapeHistory(scans, { q, age, sort }), [scans, q, age, sort]);
  const visible = list.slice(0, shown);
  const groups = sort === 'name' ? [{ label: 'By rack name', items: visible }] : groupByDay(visible);
  const sortLabel = [SORTS.find((s) => s.value === sort)?.label, age ? AGES.find((a) => a.value === age)?.label : null].filter(Boolean).join(', ');

  return (
    <>
      <TopBar title="History" backTo="/scan" />
      <Page>
        {scans.length ? (
          <div className={styles.head}>
            <SearchBox value={q} onChange={(v) => { setQ(v); setShown(PAGE); }} placeholder="Search by rack name" className={styles.search} />
            <Button variant="secondary" size="auto" className={styles.sort} onClick={() => setSheet(true)}>{sortLabel}</Button>
          </div>
        ) : null}
        {loading ? <Loading /> : null}
        <ErrorLine>{error ? error.message : ''}</ErrorLine>
        {data && !scans.length ? (
          <EmptyState text="No scans yet. Every rack you scan is kept here." action={<Button size="auto" to="/scan">Scan a rack</Button>} />
        ) : null}
        {scans.length && !list.length ? (
          <EmptyState text="No racks match." action={<TextButton onClick={() => { setQ(''); setAge(0); }}>Clear the search</TextButton>} />
        ) : null}
        {groups.map((g) => (
          <section key={g.label}>
            <SectionLabel>{g.label}</SectionLabel>
            <Thumbs className={styles.thumbs}>
              {g.items.map((s) => (s.kind === 'pair' ? <PairThumb key={s.key} item={s} /> : (
                <RackThumb
                  key={scanIdOf(s)}
                  src={thumbOf(s)}
                  name={rackNameOf(s)}
                  meta={[timeOf(s.timestamp || s.takenAt), personName(s.by || s.username)].filter(Boolean).join(', ')}
                  status={s.status || (s.qualityWarning ? 'check' : null)}
                  to={`/results/${encodeURIComponent(scanIdOf(s))}`}
                />
              )))}
            </Thumbs>
          </section>
        ))}
        {list.length > shown ? (
          <p className={styles.more}>
            <TextButton onClick={() => setShown((n) => n + PAGE)}>Show more</TextButton>
            <Grey as="span" m0> {shown} of {list.length}</Grey>
          </p>
        ) : null}
      </Page>
      <Sheet open={sheet} onClose={() => setSheet(false)} title="Order and age">
        {SORTS.map((s) => (
          <ListRow key={s.value} compact title={s.label} onClick={() => { setSort(s.value); }} right={sort === s.value ? 'Chosen' : null} aria-pressed={sort === s.value} />
        ))}
        <SectionLabel>Taken</SectionLabel>
        {AGES.map((a) => (
          <ListRow key={a.value} compact title={a.label} onClick={() => { setAge(a.value); setShown(PAGE); }} right={age === a.value ? 'Chosen' : null} aria-pressed={age === a.value} />
        ))}
        <Button onClick={() => setSheet(false)}>Done</Button>
      </Sheet>
    </>
  );
}
