import { useCallback, useEffect, useState } from 'react';
import { Button, TextButton } from './Button.jsx';
import { Grey, Loading, EmptyState } from './Text.jsx';
import { SubTabs } from './Ui.jsx';
import { AssetImg } from './AssetImg.jsx';
import { MakeModelEdit } from './MakeModel.jsx';
import { SfpModules } from './switch/SfpAdvisor.jsx';
import DeviceLabelCapture from './switch/DeviceLabelCapture.jsx';
import useJSON from '../hooks/useJSON';
import { enc } from '../utils/http';
import { plural } from '../utils/format';
import styles from './SwitchInfo.module.css';

/* The Switches tab: one white card per switch (pills choose the switch when
   a rack has two or more). Top to bottom: the device picture, its name with
   the make and model and the unit and rack under it, the key numbers, the
   specifications from the maker's datasheet, and the SFP modules that fit.

   Physical layer only: this app never talks to a switch. The data comes from
   GET /api/scans/:scanId/switches (routes/detail.js), which lists switches
   only and looks up the specifications on the server. */

export const unitOf = (s) => (s.unitTop == null ? null : s.unitBottom != null && s.unitBottom !== s.unitTop ? `U${Math.min(s.unitBottom, s.unitTop)}-${Math.max(s.unitBottom, s.unitTop)}` : `U${s.unitTop}`);
export const makeModel = (s) => [s.make, s.model].filter(Boolean).join(' ');

/** Switches only: a router, firewall, gateway or patch panel is never listed here. */
export const switchesIn = (res) => (res.data?.switches || []).filter((s) => /switch/i.test(String(s.type || '')));

// The label reading is asked about every 3 s for up to 4 minutes.
const POLL_MS = 3_000;
const POLL_TRIES = 80;

/**
 * The switches of one scan, as the server reads them. The labels are read
 * after the scan, and the specifications are looked up in the background,
 * so it asks again while any of that is still going.
 * Returns the useJSON result plus `stalled` once it has stopped asking.
 */
export function useScanSwitches(scanId) {
  const res = useJSON(scanId ? `/api/scans/${enc(scanId)}/switches` : null);
  const [tries, setTries] = useState(0);
  const list = switchesIn(res);
  const busy = list.some((s) => s.awaitingLabel || s.specsStatus === 'looking');
  const { reload } = res;
  useEffect(() => {
    if (!busy || tries >= POLL_TRIES) return undefined;
    const t = setTimeout(() => { setTries((n) => n + 1); reload(); }, POLL_MS);
    return () => clearTimeout(t);
  }, [busy, tries, reload, res.data]);
  const recheck = useCallback(() => { setTries(0); reload(); }, [reload]);
  return { ...res, stalled: busy && tries >= POLL_TRIES, recheck };
}

// A raw traceback or transport error is a server's signal, not a sentence
// for the person holding the phone.
function looksLikeBackendNoise(msg) {
  if (!msg || typeof msg !== 'string') return false;
  const m = msg.toLowerCase();
  return (
    m.includes('expecting value') || m.includes('traceback') || m.includes('jsondecode')
    || m.includes('line 1 column') || m.startsWith('http ') || m.includes('econnrefused')
    || m.includes('etimedout') || m.includes('pipeline.') || m.includes('timed out')
    || m.includes('spawn ') || m.includes('python exited') || m.includes('exit code')
  );
}

// The main figures of a datasheet, in this order, under short words; the
// unit of a bare number goes after it ("48", "1U", "370 W").
const MAIN_FIGURES = [
  ['Ports', 'Ports'],
  ['Port config', 'Port config'],
  ['Max port speed (Gbps)', 'Max port speed', ' Gbps'],
  ['Switching capacity (Gbps)', 'Switching capacity', ' Gbps'],
  ['Forwarding rate (Mpps)', 'Forwarding rate', ' Mpps'],
  ['PoE', 'PoE'],
  ['PoE budget (W)', 'PoE budget', ' W'],
  ['Layer', 'Layer'],
  ['Rack units', 'Rack units', 'U'],
  ['Network OS', 'Network OS'],
];
const withUnit = (v, unit) => (unit && /^[\d.,]+$/.test(String(v).trim()) ? `${String(v).trim()}${unit}` : String(v));
// A datasheet value longer than this, or a label that is a sentence, is page
// prose the lookup swept up, not a figure.
const FIGURE_MAX = 160;
const LABEL_MAX = 48;
const isFigure = (k, v) => String(k).trim().length <= LABEL_MAX && v != null && v !== '' && String(v).trim().length <= FIGURE_MAX;

/**
 * The datasheet as two lists: the main figures, and everything else. The
 * server sends the full table; an answer kept from before it did carries
 * only the main figures.
 */
export function specFigures(specs) {
  if (!specs) return { main: [], rest: [] };
  const table = specs.table && Object.keys(specs.table).length ? specs.table : null;
  if (!table) {
    return {
      main: [
        specs.portCount != null ? ['Ports', String(specs.portCount)] : null,
        specs.portTypes?.length ? ['Port config', specs.portTypes.map((t) => `${t.count}x ${t.type}`).join(' + ')] : null,
        specs.power ? ['Power', specs.power] : null,
        specs.heightU != null ? ['Rack units', `${specs.heightU}U`] : null,
      ].filter(Boolean),
      rest: [],
    };
  }
  const used = new Set();
  const main = [];
  for (const [key, label, unit] of MAIN_FIGURES) {
    const v = table[key];
    if (!isFigure(key, v)) continue;
    used.add(key);
    main.push([label, withUnit(v, unit)]);
  }
  const rest = Object.entries(table).filter(([k, v]) => !used.has(k) && isFigure(k, v)).map(([k, v]) => [k, String(v).trim()]);
  return { main, rest };
}

// The switch's SFP slot type, from the server (routes/detail.js sfpOf). An
// answer from before the server sent it falls back to the datasheet's port
// list and the ports the photo read, so a switch with cages is never told it
// needs no SFP.
function slotTypeOf(sw) {
  if (sw.sfp) return sw.sfp.slotType || null;
  const types = [...(sw.specs?.portTypes || []), ...(sw.ports?.byType || [])].map((t) => String(t.type || '').toUpperCase());
  if (types.some((t) => t.includes('QSFP'))) return 'QSFP+';
  if (types.some((t) => t.includes('SFP'))) return 'SFP';
  return null;
}

/** Label and value pairs, one per hairline row. */
function FigureList({ rows }) {
  return (
    <dl className={styles.list}>
      {rows.map(([k, v]) => (
        <div key={k} className={styles.listRow}>
          <dt>{k}</dt>
          <dd>{v}</dd>
        </div>
      ))}
    </dl>
  );
}

// How many specification rows show before "Show all specifications".
const SPECS_SHOWN = 6;

/** The datasheet rows: the main figures first, the first six shown, the rest one tap away. */
function SpecList({ rows }) {
  const [all, setAll] = useState(false);
  const left = rows.length - SPECS_SHOWN;
  return (
    <>
      <FigureList rows={all ? rows : rows.slice(0, SPECS_SHOWN)} />
      {left > 0 ? (
        <button type="button" className={styles.more} aria-expanded={all} onClick={() => setAll((v) => !v)}>
          {all ? 'Show fewer' : `Show all specifications (${rows.length})`}
        </button>
      ) : null}
    </>
  );
}

/** What a close-up read, over the editor it prefilled: the person confirms it. */
function ReadNote({ read, onPick }) {
  const got = !!(read.make || read.model);
  const alternates = (read.alternates || []).filter((a) => a.make || a.model);
  return (
    <div className={styles.readNote}>
      <Grey m0>{got ? 'Read from your photo. Check it before saving.' : 'That photo could not be read either. Enter the make and model, or try a closer photo.'}</Grey>
      {alternates.length ? (
        <p className={styles.alts}>
          <span className={styles.altsWord}>Or did you mean</span>
          {alternates.map((a, i) => (
            <TextButton key={`${a.make}-${a.model}-${i}`} small onClick={() => onPick(a)}>{[a.make, a.model].filter(Boolean).join(' ')}</TextButton>
          ))}
        </p>
      ) : null}
    </div>
  );
}

// "48 RJ45 · 4 SFP+ · 12 empty": the ports by type under the key numbers.
function portsLine(ports) {
  const types = (ports.byType || []).filter((t) => t.count > 0).map((t) => (t.type === 'Not read' ? `${t.count} type not read` : `${t.count} ${t.type}`));
  const empty = ports.total > 0 && ports.empty != null ? `${ports.empty} empty` : null;
  return [...types, empty].filter(Boolean).join(' · ');
}

/** One switch: the header, the key numbers, the specifications and the SFP modules. */
function SwitchCard({ sw, rack, onChanged, stalled }) {
  const [editing, setEditing] = useState(false);
  const [capturing, setCapturing] = useState(false);
  // A close-up's reading prefills the editor and stops there.
  const [read, setRead] = useState(null);
  const [noThumb, setNoThumb] = useState(false);

  const make = sw.make || '';
  const model = sw.model || '';
  const f = sw.fields || {};
  const typed = (f.make?.source === 'person' && !!make) || (f.model?.source === 'person' && !!model);
  const awaiting = !!sw.awaitingLabel && !typed && !make && !model;
  const identified = !!(make && model);
  const unit = unitOf(sw);
  const specs = sw.specs || null;

  const name = sw.name || sw.sheetName || 'Switch';
  const modelLine = identified ? `${make} ${model}`
    : awaiting ? 'Reading the label'
      : make ? `${make}, model not read` : model ? `${model}, make not read` : 'Make and model not read';
  const where = [unit, rack ? `Rack ${rack}` : null, sw.sheetName && sw.sheetName !== name ? sw.sheetName : null].filter(Boolean).join(' · ');

  // The key numbers.
  const ports = sw.ports || {};
  const sfpFromSheet = (specs?.portTypes || []).filter((t) => /SFP/i.test(String(t.type || ''))).reduce((n, t) => n + (t.count || 0), 0);
  const figures = [
    { label: 'Ports', value: ports.total ?? '-' },
    { label: 'Connected', value: ports.connected ?? '-' },
    sw.sfp?.total > 0 ? { label: 'Free SFP', value: sw.sfp.free ?? '-' } : { label: 'SFP cages', value: sfpFromSheet },
    { label: 'Rack units', value: specs?.heightU ?? sw.heightU ?? '-' },
  ];
  const byType = portsLine(ports);

  // The specifications.
  const { main, rest } = specFigures(specs);
  const specRows = [...main, ...rest];
  const datasheetUrl = specs?.productUrl || specs?.datasheetUrl || null;
  let specNote = null;
  if (!specs) {
    if (!identified) specNote = 'Enter the make and model to see specifications.';
    else if (sw.specsStatus === 'looking') specNote = stalled ? 'The datasheet lookup did not finish.' : 'Looking up the datasheet';
    else specNote = 'No datasheet found for this model.';
  }
  const specWhy = !specs && identified && sw.specsStatus !== 'looking' && sw.specsNote && !looksLikeBackendNoise(sw.specsNote) ? sw.specsNote : null;

  const startEdit = () => { setRead(null); setEditing(true); };
  const stopEdit = () => { setEditing(false); setRead(null); };
  const saved = () => { stopEdit(); if (onChanged) onChanged(); };
  const thumb = sw.cropUrl && !noThumb;

  return (
    <article className={styles.card} aria-label={identified ? `${make} ${model}` : name}>
      <div className={styles.head}>
        {thumb ? (
          <AssetImg
            src={sw.cropUrl}
            alt={`${name} on the rack photo`}
            className={styles.thumb}
            onError={() => setNoThumb(true)}
            {...(sw.photoUrl && Array.isArray(sw.box) ? { 'data-zoom-src': sw.photoUrl, 'data-zoom-box': sw.box.join(',') } : {})}
          />
        ) : null}
        <div className={styles.headText}>
          <h3 className={styles.title}>{name}</h3>
          <p className={identified ? styles.model : styles.modelMissing}>{modelLine}</p>
          {where ? <p className={styles.where}>{where}</p> : null}
        </div>
        {identified && !editing ? <TextButton small className={styles.change} aria-label="Change make and model" onClick={startEdit}>Change</TextButton> : null}
      </div>

      {!identified && !editing ? (
        <div className={styles.acts}>
          <Button size="sm" onClick={startEdit}>Enter make and model</Button>
          <Button size="sm" variant="secondary" onClick={() => setCapturing(true)}>Scan the device label</Button>
        </div>
      ) : null}
      {editing ? (
        <div className={styles.edit}>
          {read ? <ReadNote read={read} onPick={(a) => setRead({ ...read, make: a.make || read.make, model: a.model || read.model })} /> : null}
          <MakeModelEdit scanId={sw.scanId} deviceId={sw.deviceId} make={read?.make || make} model={read?.model || model} onSaved={saved} onCancel={stopEdit} />
          {!identified ? <p className={styles.editLink}><TextButton small onClick={() => setCapturing(true)}>{read ? 'Take another photo' : 'Scan the device label instead'}</TextButton></p> : null}
        </div>
      ) : null}

      <div className={styles.figBlock}>
        <dl className={styles.figs} aria-label="Key numbers">
          {figures.map((k) => (
            <div key={k.label} className={styles.fig}>
              <dt>{k.label}</dt>
              <dd>{k.value}</dd>
            </div>
          ))}
        </dl>
        {byType ? <p className={styles.byType}>{byType}</p> : null}
      </div>

      <div className={styles.cols}>
        <div className={styles.col}>
          <div className={styles.secHead}>
            <h4 className={styles.secTitle}>Specifications</h4>
            {specs && datasheetUrl ? <TextButton small href={datasheetUrl} target="_blank" rel="noreferrer" className={styles.secLink}>Datasheet</TextButton> : null}
          </div>
          {specs ? (
            specRows.length ? <SpecList rows={specRows} /> : <Grey m0 className={styles.quiet}>The datasheet gives no figures for this model.</Grey>
          ) : (
            <>
              <Grey m0 className={styles.quiet}>
                {specNote}
                {stalled && sw.specsStatus === 'looking' ? <> <TextButton small onClick={onChanged}>Check again</TextButton></> : null}
              </Grey>
              {specWhy ? <Grey m0 className={styles.why}>{specWhy}</Grey> : null}
              {sw.lookupsOff && identified ? <Grey m0 className={styles.why}>Online lookups are off for your organization.</Grey> : null}
            </>
          )}
        </div>
        <div className={styles.col}>
          <div className={styles.secHead}>
            <h4 className={styles.secTitle}>SFP modules</h4>
          </div>
          <SfpModules vendor={make} model={model} slotType={slotTypeOf(sw)} total={sw.sfp?.total} free={sw.sfp?.free} />
        </div>
      </div>

      {capturing ? (
        <DeviceLabelCapture
          deviceLabel={[unit, sw.name].filter(Boolean).join(' ')}
          onIdentified={(r) => { setCapturing(false); setRead(r); setEditing(true); }}
          onManualEntry={() => { setCapturing(false); setEditing(true); }}
          onCancel={() => setCapturing(false)}
        />
      ) : null}
    </article>
  );
}

const pillLabel = (sw, i) => [unitOf(sw), sw.sheetName || (sw.make && sw.model ? sw.model : null) || sw.name || `Switch ${i + 1}`].filter(Boolean).join(' ');

/**
 * Every switch of one rack: pills to choose one when there are two or more
 * (none for a single switch), and the chosen one's card.
 */
export function SwitchPicker({ switches, rack, onChanged, stalled }) {
  const [idx, setIdx] = useState(0);
  const safe = Math.min(idx, switches.length - 1);
  const active = switches[safe];
  return (
    <div className={styles.picker}>
      {switches.length > 1 ? (
        <SubTabs label="Switches in this rack" value={safe} onChange={setIdx} className={styles.pills}
          options={switches.map((sw, i) => ({ value: i, label: pillLabel(sw, i) }))} />
      ) : null}
      {active ? <SwitchCard key={`${active.scanId}-${active.deviceId}`} sw={active} rack={rack} onChanged={onChanged} stalled={stalled} /> : null}
    </div>
  );
}

/* One rack's switches: finding them, could not load, none found, or the
   pills and the card with a quiet line while the labels are read. */
function SwitchInfoBody({ res, rack, onChanged }) {
  const list = switchesIn(res);
  const again = res.recheck || res.reload;
  if (res.loading && !res.data) return <Loading label="Finding switches" />;
  if (res.error && !res.data) return <EmptyState text="The switches could not be loaded." action={<TextButton small onClick={again}>Try again</TextButton>} />;
  if (!list.length) return <EmptyState text="No switches found in this rack. Scan again with the switches in clear view." action={<TextButton small onClick={again}>Check again</TextButton>} />;
  const reading = list.some((s) => s.awaitingLabel && !s.make && !s.model);
  return (
    <>
      <SwitchPicker switches={list} rack={rack} onChanged={onChanged} stalled={res.stalled} />
      {reading ? (
        <Grey className={styles.note}>
          {res.stalled ? 'The device labels could not be read. Enter the make and model on the card. ' : 'Reading the device labels.'}
          {res.stalled ? <TextButton small onClick={again}>Check again</TextButton> : null}
        </Grey>
      ) : null}
    </>
  );
}

/**
 * The Switches tab: the switches of one or more racks, each rack under its
 * own heading when there are two. groups: [{ key, kicker, rack, res (useScanSwitches) }].
 */
export function SwitchGroups({ groups }) {
  const reloadAll = useCallback(() => groups.forEach((g) => (g.res.recheck || g.res.reload)()), [groups]);
  return (
    <div className={groups.length > 1 ? `${styles.root} ${styles.two}` : styles.root}>
      {groups.map((g) => {
        const list = switchesIn(g.res);
        return (
          <section key={g.key} className={styles.group} aria-label={g.kicker || g.rack || 'Switches'}>
            {groups.length > 1 ? (
              <div className={styles.groupHead}>
                <p className={styles.kicker}>{g.kicker}</p>
                <h2 className={styles.rack}>{g.rack || 'Unnamed rack'}</h2>
                {g.res.data && list.length ? <p className={styles.count}>{plural(list.length, 'switch', 'switches')}</p> : null}
              </div>
            ) : null}
            <SwitchInfoBody res={g.res} rack={g.rack} onChanged={reloadAll} />
          </section>
        );
      })}
    </div>
  );
}

export default SwitchGroups;
