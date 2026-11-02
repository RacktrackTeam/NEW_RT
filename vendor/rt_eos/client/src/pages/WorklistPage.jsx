import { useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { TopBar } from '../components/TopBar.jsx';
import { Page } from '../components/Page.jsx';
import { Button, TextButton, Links, Actions } from '../components/Button.jsx';
import { Field, SelectField } from '../components/Field.jsx';
import { Grey, EmptyState, ErrorLine, Loading } from '../components/Text.jsx';
import { Tick, Progress } from '../components/Controls.jsx';
import { AssetImg } from '../components/AssetImg.jsx';
import { useToast } from '../components/Toast.jsx';
import { useJSON } from '../hooks/useJSON.js';
import { useIsLaptop } from '../hooks/useIsLaptop.js';
import { postJSON, notBuilt, enc } from '../utils/http';
import styles from './WorklistPage.module.css';

const NOT_BUILT = 'The worklist is not available on this server yet.';

const confOf = (it) => {
  const c = Number(it?.confidence);
  if (!Number.isFinite(c)) return null;
  return c > 1 ? c / 100 : c;
};

/** Least sure first; items with no number go last. */
export function sortItems(items) {
  return items
    .map((it, i) => ({ it, i }))
    .sort((x, y) => {
      const a = confOf(x.it);
      const b = confOf(y.it);
      if (a == null && b == null) return x.i - y.i;
      if (a == null) return 1;
      if (b == null) return -1;
      return a - b || x.i - y.i;
    })
    .map(({ it }) => it);
}

const idOf = (it) => it?.id ?? it?.itemId ?? it?.item_id;
const whereOf = (it) => it?.where || it?.location || it?.place || null;
const noteOf = (it) => it?.note || it?.detail || it?.hint || it?.reason || '';

/** The question in words, with the reading and the port in bold when the item names them. */
function Question({ item }) {
  const q = item.question;
  if (typeof q === 'string' && q.trim()) return <p className={styles.q}>{q}</p>;
  const value = item.current ?? item.value;
  const port = q?.port ?? item.port ?? item.portName;
  if (value != null && port != null) {
    return <p className={styles.q}>Is this <b className="mono">{String(value)}</b> in <b>{String(port)}</b>?</p>;
  }
  if (value != null) return <p className={styles.q}>Is this <b className="mono">{String(value)}</b> right?</p>;
  return <p className={styles.q}>Is this reading right?</p>;
}

function Crop({ item, large }) {
  const where = whereOf(item);
  return (
    <div className={`${styles.crop} ${large ? styles.lg : ''}`}>
      {item.cropUrl ? <AssetImg src={item.cropUrl} alt={item.current ? `Crop showing ${item.current}` : 'Crop of the reading'} className={styles.cropImg} /> : null}
      {where ? <span className={styles.tag}>{where}</span> : null}
    </div>
  );
}

export default function WorklistPage() {
  const [params] = useSearchParams();
  const scanId = params.get('scanId');
  const path = scanId ? `/api/worklist?scanId=${enc(scanId)}` : '/api/worklist';
  const { data, error, loading, reload } = useJSON(path);

  if (loading && !data) return (<><TopBar title="Worklist" /><Page><Loading /></Page></>);
  if (error) {
    return (
      <>
        <TopBar title="Worklist" />
        <Page>
          {notBuilt(error) ? <Grey>{NOT_BUILT}</Grey> : <ErrorLine>{error.message}</ErrorLine>}
          {!notBuilt(error) ? <TextButton onClick={reload}>Try again</TextButton> : null}
        </Page>
      </>
    );
  }
  const raw = Array.isArray(data) ? data : Array.isArray(data?.items) ? data.items : [];
  const items = scanId ? raw.filter((it) => !it.scanId || String(it.scanId) === scanId) : raw;
  return <Worklist key={path} initial={items} />;
}

function Worklist({ initial }) {
  const toast = useToast();
  const laptop = useIsLaptop();
  const [items, setItems] = useState(() => sortItems(initial));
  const [done, setDone] = useState(0);
  const [pos, setPos] = useState(0);
  const [uncapped, setUncapped] = useState(false);
  const [correcting, setCorrecting] = useState(false);
  const [value, setValue] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const item = items[Math.min(pos, items.length - 1)] || null;
  const itemId = idOf(item);
  useEffect(() => {
    setUncapped(!!item?.uncapped);
    setCorrecting(false);
    setValue(item?.current != null ? String(item.current) : '');
    setError('');
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [itemId]);

  const queue = useMemo(() => items.slice(0, 12), [items]);

  if (!item) {
    return (
      <>
        <TopBar title="Worklist" />
        <Page>
          <EmptyState text="Nothing to confirm. Every reading has been checked." action={<TextButton to="/">Back to Home</TextButton>} />
        </Page>
      </>
    );
  }

  const tickable = item.kind === 'port' || item.kind === 'label';
  const conf = confOf(item);
  const n = done + 1;
  const outOf = done + items.length;
  const sub = laptop ? `${n} of ${outOf}, least sure first` : `${n} of ${outOf}`;
  const options = Array.isArray(item.options) ? item.options : null;

  const answer = async (action) => {
    if (action === 'correct' && !String(value).trim()) { setError('Enter what it really says, or choose Skip.'); return; }
    setBusy(true); setError('');
    const body = { action };
    if (action === 'correct') body.value = String(value).trim();
    if (tickable && action !== 'skip') body.uncapped = !!uncapped;
    try {
      const out = await postJSON(`/api/worklist/${enc(itemId)}`, body);
      const rest = items.filter((x) => idOf(x) !== itemId);
      let next = rest;
      let at = Math.min(pos, Math.max(0, rest.length - 1));
      const nx = out?.next;
      if (nx != null) {
        const nid = typeof nx === 'object' ? idOf(nx) : nx;
        const found = rest.findIndex((x) => String(idOf(x)) === String(nid));
        if (found >= 0) at = found;
        else if (typeof nx === 'object' && nid != null) { next = [...rest.slice(0, at), nx, ...rest.slice(at)]; }
      }
      setItems(next);
      setPos(at);
      setDone((d) => d + 1);
      const left = next.length;
      if (action === 'confirm') toast(`Confirmed. ${left} left.`);
      else if (action === 'correct') toast(`Corrected. ${left} left.`);
      else toast('Skipped.');
    } catch (e) {
      setError(notBuilt(e) ? NOT_BUILT : e.message);
    } finally {
      setBusy(false);
    }
  };

  const ask = (
    <div className={styles.ask}>
      <Question item={item} />
      <Grey>{[conf != null ? `${Math.round(conf * 100)}% sure.` : '', noteOf(item)].filter(Boolean).join(' ')}</Grey>
      {tickable ? <Tick label="No dust cap" checked={uncapped} onChange={setUncapped} /> : null}
      {correcting ? (
        <>
          {options ? (
            <SelectField
              label="What it really is"
              value={value}
              onChange={(e) => setValue(e.target.value)}
              options={options}
              placeholder="Choose"
            />
          ) : (
            <Field label="What it really says" value={value} onChange={(e) => setValue(e.target.value)} autoCapitalize="characters" spellCheck={false} />
          )}
          <ErrorLine>{error}</ErrorLine>
          <Actions>
            <Button size={laptop ? 'auto' : undefined} onClick={() => answer('correct')} busy={busy}>Save correction</Button>
            <TextButton onClick={() => { setCorrecting(false); setError(''); }}>Cancel</TextButton>
          </Actions>
        </>
      ) : (
        <>
          <ErrorLine>{error}</ErrorLine>
          {laptop ? (
            <Actions>
              <Button size="auto" onClick={() => answer('confirm')} busy={busy}>Yes, confirm</Button>
              <TextButton onClick={() => setCorrecting(true)}>No, correct it</TextButton>
              <TextButton onClick={() => answer('skip')} disabled={busy}>Skip</TextButton>
            </Actions>
          ) : (
            <>
              <Button className={styles.confirm} onClick={() => answer('confirm')} busy={busy}>Yes, confirm</Button>
              <Links center>
                <TextButton onClick={() => setCorrecting(true)}>No, correct it</TextButton>
                <TextButton onClick={() => answer('skip')} disabled={busy}>Skip</TextButton>
              </Links>
            </>
          )}
        </>
      )}
    </div>
  );

  return (
    <>
      <TopBar title="Worklist" sub={sub} />
      {!laptop ? <Progress value={outOf ? done / outOf : 0} label="Worklist progress" /> : null}
      <Page>
        {laptop ? (
          <>
            <div className={styles.wl}>
              <Crop item={item} large />
              {ask}
            </div>
            <div className={styles.queue} aria-label="Next readings">
              {queue.map((q, i) => {
                const c = confOf(q);
                const on = idOf(q) === itemId;
                return (
                  <button
                    key={idOf(q) ?? i}
                    type="button"
                    className={`${styles.qitem} ${on ? styles.on : ''}`}
                    aria-current={on || undefined}
                    onClick={() => setPos(i)}
                  >
                    <span className={styles.cropmini}>{q.cropUrl ? <AssetImg src={q.cropUrl} alt="" /> : null}</span>
                    <span className="mono">{q.current != null ? String(q.current) : 'Reading'}</span>
                    {c != null ? <span className={styles.g}>{`${Math.round(c * 100)}%`}</span> : null}
                  </button>
                );
              })}
            </div>
          </>
        ) : (
          <>
            <Crop item={item} />
            {ask}
          </>
        )}
      </Page>
    </>
  );
}
