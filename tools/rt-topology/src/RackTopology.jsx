/*
 * The Topology tab of a rack result: the rack as a clean frame with its
 * devices as tiles and the cables as smooth curves between their ports
 * (components/TopologyDiagram.jsx, the same drawing as two racks read
 * together), the far ends that leave the rack as short lines to small tags,
 * the cable filters, the capacity colouring, the trace between two devices,
 * and the 3D scene (lazy-loaded) as the second view.
 *
 * It draws only from the scan's detail answer (the photo read plus the Excel
 * dataset), reshaped by utils/topologyAdapter.js. Where nothing is read - a
 * cable length, a management address, which port is an uplink - nothing is
 * shown.
 *
 * This file draws nothing itself: the lines are TopologyDiagram's.
 */
import { lazy, Component, Suspense, useCallback, useEffect, useMemo, useState } from 'react';
import { SubTabs, Disclosure, DataTable } from 'rteos/components/Ui.jsx';
import { TextButton } from 'rteos/components/Button.jsx';
import Icon from 'rteos/components/Icon.jsx';
import { Sheet } from 'rteos/components/Sheet.jsx';
import { useIsLaptop } from 'rteos/hooks/useIsLaptop.js';
import { getItem, setItem } from 'rteos/utils/safeStorage';
import { rtTopologyOf } from 'rteos/utils/topologyAdapter';
import { TopologyDiagram, TopologyLegend, KIND, kindKey } from './TopologyDiagram.jsx';
import styles from './RackTopology.module.css';

const TopologyScene3D = lazy(() => import('./RackTopologyScene3D.jsx'));

const cx = (...a) => a.filter(Boolean).join(' ');

const CLASS_LABEL = {
  switch: 'Switch',
  router: 'Router',
  patch_panel: 'Patch Panel',
  server: 'Server',
  firewall: 'Firewall',
  pdu: 'PDU',
  ups: 'UPS',
  unidentified: 'Not identified',
};

const clsOf = (dev) => String(dev?.class ?? dev?.class_name ?? '')
  .trim().toLowerCase().replace(/[\s-]+/g, '_');

/** The words for a device's kind: its own label, the known name, or the class in words. */
function classLabel(dev) {
  if (dev?.class_label) return dev.class_label;
  const c = clsOf(dev);
  if (CLASS_LABEL[c]) return CLASS_LABEL[c];
  const words = c.replace(/_/g, ' ').trim();
  return words ? words.charAt(0).toUpperCase() + words.slice(1) : 'Device';
}

/** "Switch, Arista 7280R3": the kind and, for a switch, its make and model. */
const kindLine = (dev) => [classLabel(dev), dev.model].filter(Boolean).join(', ');

/** "U39-U40" or "U42"; null when the unit was not read. */
function unitsOf(d) {
  if (!d.in_rack || d.u_position == null) return null;
  const p = Math.max(1, d.u_position), s = Math.max(1, d.u_size || 1);
  return s > 1 ? `U${p}-U${p + s - 1}` : `U${p}`;
}

/** A cable's name: the label on it, or plainly that none is known. */
const cableName = (c) => c?.cable_label || 'No label';

const VIEW_KEY = 'topology.view';

// Fiber by name or by its OM / OS grade (the same words the 3D scene colours as fiber).
const FIBER_RE = /fiber|fibre|mm|sm|single|om[1-5]|os[12]/;

/** Cat, fiber, DAC or other: the buckets the filter pills, the counts and the colours share. */
function cableKind(cable_type) {
  const t = (cable_type || '').toLowerCase();
  if (t.startsWith('cat')) return 'cat';
  if (FIBER_RE.test(t)) return 'fiber';
  if (/dac|twinax/.test(t)) return 'dac';
  return 'other';
}

// Seen on the photo, or only in the Excel dataset.
// The filters hold sets: an empty set is everything, so any number can be on at once.
const matchesType = (edge, set) => !set.size || set.has(cableKind(edge.cable_type));
const matchesSource = (edge, set) => !set.size || set.has(edge.seen ? 'seen' : 'excel');

const portWord = (p) => (p.connected === true ? 'connected' : p.connected === false ? 'free' : 'not known');

/** Free share of the ports whose state is known, or null when none is. */
function freeShare(dev) {
  const known = (dev.ports || []).filter((p) => p.connected === true || p.connected === false);
  if (!known.length) return null;
  return known.filter((p) => p.connected === false).length / known.length;
}

// Capacity: half or more free is fine, a quarter is getting full, less is nearly full.
const capTone = (pct) => (pct == null ? null : pct >= 0.5 ? 'ok' : pct >= 0.25 ? 'warn' : 'bad');

const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;

export default function RackTopology({ detail, onOpen }) {
  const topo = useMemo(() => rtTopologyOf(detail), [detail]);
  const laptop = useIsLaptop();
  const [selected, setSelected] = useState(null); // { kind: 'node' | 'cable', id }
  const [view, setView] = useState(() => (getItem(VIEW_KEY) === '3d' ? '3d' : '2d'));
  const [cableFilter, setCableFilter] = useState(() => new Set()); // any of 'cat' | 'fiber' | 'dac' | 'other'; empty = all
  const [sourceFilter, setSourceFilter] = useState(() => new Set()); // any of 'seen' | 'excel'; empty = all
  const [heatmap, setHeatmap] = useState(false);
  const [traceMode, setTraceMode] = useState(false);
  const [showFilters, setShowFilters] = useState(false);
  const [traceA, setTraceA] = useState(null);
  const [traceB, setTraceB] = useState(null);
  const [hoverInfo, setHoverInfo] = useState(null); // { name, dev, freePct, tier } from the 3D scene

  useEffect(() => { setItem(VIEW_KEY, view); }, [view]);

  // The same topology with the cables narrowed by the pills, so the rows, the
  // 3D scene and the details agree.
  const filteredTopo = useMemo(() => {
    if (!topo) return null;
    if (!cableFilter.size && !sourceFilter.size) return topo;
    return {
      ...topo,
      edges: topo.edges.filter((e) => matchesType(e, cableFilter) && matchesSource(e, sourceFilter)),
    };
  }, [topo, cableFilter, sourceFilter]);

  const cableCounts = useMemo(() => {
    const c = { all: 0, cat: 0, fiber: 0, dac: 0, other: 0, seen: 0, excel: 0 };
    if (!topo) return c;
    c.all = topo.edges.length;
    for (const e of topo.edges) {
      c[cableKind(e.cable_type)] += 1;
      if (e.seen) c.seen += 1; else c.excel += 1;
    }
    return c;
  }, [topo]);

  const freePctByDevice = useMemo(() => {
    const m = new Map();
    if (!topo) return m;
    for (const d of topo.devices) m.set(d.name, freeShare(d));
    return m;
  }, [topo]);

  const otherEnds = useMemo(() => (topo ? topo.devices.filter((d) => d.class === 'outside') : []), [topo]);

  // Cables grouped by device pair: 24 cables between a switch and a patch
  // panel are one link with count 24. `a` and `b` key the pair; `from` and
  // `to` read the way the first cable runs (from the device read on the photo).
  const aggEdges = useMemo(() => {
    if (!filteredTopo) return [];
    const m = new Map();
    for (const e of filteredTopo.edges) {
      const a = e.src.device, b = e.dst.device;
      const key = a < b ? `${a}::${b}` : `${b}::${a}`;
      if (!m.has(key)) m.set(key, { a: a < b ? a : b, b: a < b ? b : a, from: a, to: b, count: 0, cables: [] });
      const v = m.get(key);
      v.count += 1;
      v.cables.push(e);
    }
    return Array.from(m.values());
  }, [filteredTopo]);

  // The shortest cable path from A to B over the device pairs.
  const tracePath = useMemo(() => {
    if (!traceMode || !traceA || !traceB) return null;
    const adj = new Map();
    for (const e of aggEdges) {
      if (!adj.has(e.a)) adj.set(e.a, new Set());
      if (!adj.has(e.b)) adj.set(e.b, new Set());
      adj.get(e.a).add(e.b);
      adj.get(e.b).add(e.a);
    }
    if (traceA === traceB) return [traceA];
    const visited = new Set([traceA]);
    const queue = [[traceA]];
    while (queue.length) {
      const path = queue.shift();
      const node = path[path.length - 1];
      for (const next of (adj.get(node) || [])) {
        if (visited.has(next)) continue;
        visited.add(next);
        const newPath = [...path, next];
        if (next === traceB) return newPath;
        queue.push(newPath);
      }
    }
    return []; // no path
  }, [traceMode, traceA, traceB, aggEdges]);

  const traceEdgeKeys = useMemo(() => {
    if (!tracePath || tracePath.length < 2) return null;
    const keys = new Set();
    for (let i = 0; i < tracePath.length - 1; i++) {
      const a = tracePath[i], b = tracePath[i + 1];
      keys.add(a < b ? `${a}::${b}` : `${b}::${a}`);
    }
    return keys;
  }, [tracePath]);

  const tracePathSet = useMemo(() => (tracePath && tracePath.length > 0 ? new Set(tracePath) : null), [tracePath]);

  // In trace mode a tap on a device picks an end: the first, the second, then a new first.
  const handleSelect = (sel) => {
    if (traceMode && sel?.kind === 'node') {
      if (!traceA || (traceA && traceB)) { setTraceA(sel.id); setTraceB(null); }
      else if (sel.id !== traceA) { setTraceB(sel.id); }
      return;
    }
    setSelected(sel);
  };

  const toggleTrace = () => {
    setTraceMode((v) => !v);
    setTraceA(null);
    setTraceB(null);
    setSelected(null);
  };

  const clearSelection = useCallback(() => setSelected(null), []);

  const selectionInfo = useMemo(() => {
    if (!topo || !selected) return null;
    if (selected.kind === 'node') {
      const dev = topo.devices.find((d) => d.name === selected.id);
      if (!dev) return null;
      const peers = aggEdges
        .filter((e) => e.a === dev.name || e.b === dev.name)
        .map((e) => ({ peer: e.a === dev.name ? e.b : e.a, count: e.count }))
        .sort((x, y) => y.count - x.count);
      return { kind: 'node', dev, peers };
    }
    if (selected.kind === 'cable') {
      const cable = topo.edges.find((c) => c.cable_id === selected.id);
      return cable ? { kind: 'cable', cable } : null;
    }
    if (selected.kind === 'link') {
      const link = aggEdges.find((x) => `${x.a}::${x.b}` === selected.id);
      return link ? { kind: 'link', link } : null;
    }
    return null;
  }, [topo, selected, aggEdges]);

  if (!topo) return <div className={styles.root}><p className={styles.note}>Loading topology</p></div>;

  const seen = topo.stats.seen_count;
  const activeFilters = cableFilter.size + sourceFilter.size + (heatmap ? 1 : 0) + (traceMode ? 1 : 0);
  const total = topo.stats.edge_count;

  // Ports with a cable plugged in whose other end is not known (no label read, not in the Excel).
  const plugged = topo.loose || 0;
  let note = null;
  if (total === 0) {
    note = topo.hasMatrix
      ? `${plugged ? `${plural(plugged, 'port')} ${plugged === 1 ? 'has' : 'have'} a cable plugged in, but ` : ''}no cable label was read and the Excel dataset has no cable on these devices, so the other ends are not known. Take close-ups of a device to read its cable labels.`
      : `${plugged ? `${plural(plugged, 'port')} ${plugged === 1 ? 'has' : 'have'} a cable plugged in, but ` : ''}there is no Excel dataset and no cable label was read, so the other ends are not known. Import the Excel dataset or take close-ups of a device.`;
  } else if (topo.loose) {
    note = `${plural(topo.loose, 'more port')} plugged in with the other end not known.`;
  }

  let details = null;
  let detailsTitle = '';
  if (selectionInfo?.kind === 'node') {
    details = <DeviceDetails dev={selectionInfo.dev} peers={selectionInfo.peers} topo={filteredTopo} onOpen={onOpen} onClose={clearSelection} laptop={laptop} />;
    detailsTitle = selectionInfo.dev.name;
  } else if (selectionInfo?.kind === 'cable') {
    details = <CableDetails cable={selectionInfo.cable} topo={topo} onClose={clearSelection} laptop={laptop} />;
    detailsTitle = selectionInfo.cable.cable_label ? `Cable ${selectionInfo.cable.cable_label}` : 'Cable, no label';
  } else if (selectionInfo?.kind === 'link') {
    details = <LinkDetails link={selectionInfo.link} topo={topo} onClose={clearSelection} laptop={laptop} onCable={(id) => setSelected({ kind: 'cable', id })} />;
    detailsTitle = `${selectionInfo.link.from} to ${selectionInfo.link.to}`;
  }

  return (
    <div className={styles.root} data-testid="topology">
      <div className={styles.top}>
        <p className={styles.summary}>{plural(topo.devices.length, 'device')}{plugged ? ` · ${plural(plugged, 'port')} cabled` : ''}</p>
        <SubTabs
          className={styles.view}
          label="Topology view"
          options={[{ value: '2d', label: '2D' }, { value: '3d', label: '3D' }]}
          value={view}
          onChange={(v) => { setView(v); setSelected(null); setHoverInfo(null); }}
        />
      </div>

      {/* no filters on the phone (user, 2026-10-27): the drawing and the scene only; the app draws the kinds legend above */}

      {traceMode ? (
        <TraceLine traceA={traceA} traceB={traceB} tracePath={tracePath} clear={() => { setTraceA(null); setTraceB(null); }} />
      ) : null}

      {view === '3d' ? (
        <>
          <div className={styles.scene}>
            <SceneBoundary>
              <Suspense fallback={<p className={styles.sceneNote}>Starting the 3D view</p>}>
                <TopologyScene3D
                  topo={filteredTopo}
                  selected={selected}
                  setSelected={handleSelect}
                  aggEdges={aggEdges}
                  heatmap={heatmap}
                  freePctByDevice={freePctByDevice}
                  traceMode={traceMode}
                  traceA={traceA}
                  traceB={traceB}
                  tracePathSet={tracePathSet}
                  traceEdgeKeys={traceEdgeKeys}
                  onHoverDevice={setHoverInfo}
                />
              </Suspense>
            </SceneBoundary>
          </div>
          <p className={styles.hint} aria-live="polite">{sceneHint(hoverInfo, traceMode)}</p>
        </>
      ) : (
        <>
          <RackDiagram
            topo={filteredTopo}
            links={aggEdges}
            selected={selected}
            setSelected={handleSelect}
            heatmap={heatmap}
            freePctByDevice={freePctByDevice}
            traceMode={traceMode}
            traceA={traceA}
            traceB={traceB}
            tracePathSet={tracePathSet}
            traceEdgeKeys={traceEdgeKeys}
          />
          {traceMode ? null : <p className={styles.caption} aria-live="polite">{captionOf(selectionInfo)}</p>}
          
        </>
      )}

      {selectionInfo?.kind === 'node'
        ? <DeviceCard dev={selectionInfo.dev} card={detail?.cards?.[selectionInfo.dev.deviceId]} onOpen={onOpen} onClose={clearSelection} />
        : (laptop ? (details ? <div className={styles.card}>{details}</div> : null) : <Sheet open={!!details} onClose={clearSelection} title={detailsTitle}>{details}</Sheet>)}
    </div>
  );
}

/** The line under the 3D scene: the device under the pointer, or how to move the view. */
function sceneHint(info, traceMode) {
  if (info?.dev) {
    const ports = info.dev.ports || [];
    const on = ports.filter((p) => p.connected === true).length;
    return `${info.dev.name}, ${kindLine(info.dev)}${ports.length ? `, ${on}/${ports.length} connected` : ''}`;
  }
  return traceMode ? 'Tap two devices to trace the cable between them' : 'Drag to rotate, pinch or scroll to zoom';
}

/** A 3D scene that cannot start (no WebGL on this device) says so instead of blanking the tab. */
export class SceneBoundary extends Component {
  constructor(props) { super(props); this.state = { failed: false }; }
  static getDerivedStateFromError() { return { failed: true }; }
  render() {
    if (this.state.failed) {
      return <p className={styles.sceneNote}>The 3D view cannot start on this device. The 2D view shows the same rack.</p>;
    }
    return this.props.children;
  }
}

function Pill({ on, onClick, label, count, title }) {
  return (
    <button type="button" className={cx(styles.pill, on && styles.pillOn)} aria-pressed={on} onClick={onClick} title={title}>
      {label}
      {count != null ? <b>{count}</b> : null}
    </button>
  );
}

// The filters as one wrapping row of pills: the cable type, where the cable is
// known from, and the two tools. Any number can be on at once; with none on
// in a group, that group shows everything.
function Filters({ cableFilter, setCableFilter, sourceFilter, setSourceFilter, cableCounts, heatmap, setHeatmap, traceMode, toggleTrace }) {
  const flip = (set, setter, v) => { const n = new Set(set); if (n.has(v)) n.delete(v); else n.add(v); setter(n); };
  const types = [['cat', 'Cat'], ['fiber', 'Fiber'], ['dac', 'DAC'], ['other', 'Other']].filter(([k]) => cableCounts[k] || cableFilter.has(k));
  const any = cableFilter.size || sourceFilter.size || heatmap || traceMode;
  return (
    <div className={styles.filters}>
      <div className={styles.group} role="group" aria-label="Cable type">
        <Pill on={!cableFilter.size} onClick={() => setCableFilter(new Set())} label="All types" count={cableCounts.all} />
        {types.map(([k, label]) => <Pill key={k} on={cableFilter.has(k)} onClick={() => flip(cableFilter, setCableFilter, k)} label={label} count={cableCounts[k]} />)}
      </div>
      <div className={styles.group} role="group" aria-label="Where the cable is known from">
        <Pill on={sourceFilter.has('seen')} onClick={() => flip(sourceFilter, setSourceFilter, 'seen')} label="Seen" count={cableCounts.seen} />
        <Pill on={sourceFilter.has('excel')} onClick={() => flip(sourceFilter, setSourceFilter, 'excel')} label="Excel only" count={cableCounts.excel} />
      </div>
      <div className={styles.group} role="group" aria-label="Tools">
        <Pill on={heatmap} onClick={() => setHeatmap((v) => !v)} label="Capacity" title="Colour devices by free ports" />
        <Pill on={traceMode} onClick={toggleTrace} label="Trace" title="Tap two devices to trace the cable path between them" />
      </div>
      {any ? (
        <TextButton small onClick={() => { setCableFilter(new Set()); setSourceFilter(new Set()); if (heatmap) setHeatmap(false); if (traceMode) toggleTrace(); }}>Clear all</TextButton>
      ) : null}
    </div>
  );
}

// One line under the filters while trace is on: what to tap next, or the path found.
function TraceLine({ traceA, traceB, tracePath, clear }) {
  let text;
  if (!traceA) {
    text = 'Tap the first device.';
  } else if (!traceB) {
    text = <>From <b>{traceA}</b>, tap the second device.</>;
  } else if (!tracePath || tracePath.length === 0) {
    text = <>No cable path between <b>{traceA}</b> and <b>{traceB}</b>.</>;
  } else {
    const hops = tracePath.length - 1;
    const via = tracePath.slice(1, -1);
    text = <><b>{traceA}</b> to <b>{traceB}</b>: {plural(hops, 'hop')}{via.length ? ` via ${via.join(', ')}` : ''}</>;
  }
  return (
    <p className={styles.trace} role="status">
      <span>{text}</span>
      {(traceA || traceB) ? <TextButton small onClick={clear}>Clear</TextButton> : null}
    </p>
  );
}

const PROBLEM = new Set(['wrong_port', 'missing_cable']);
const GOOD = new Set(['matched', 'on_tag']);

/** The line colour of a link: red for any problem, green when matched, ink when seen, grey dashed when only in the Excel. */
function linkTone(cables) {
  if (cables.some((c) => PROBLEM.has(c.status))) return 'bad';
  if (cables.some((c) => GOOD.has(c.status))) return 'ok';
  if (cables.some((c) => c.seen)) return 'ink';
  return 'grey';
}

const LEGEND = { ok: ['ok', 'Matched'], ink: ['ink', 'Seen on the photo'], bad: ['bad', 'Problem'], grey: ['grey', 'Excel only', true] };

/**
 * The 2D view: the rack as a clean frame with its unit ruler, a tile per
 * device at its units, one curve per pair of devices with a cable between
 * them (a loop out on the right of the rack, from port dot to port dot, its
 * count on it), and the far ends that are not in the rack as curves out to
 * small pill tags on the left. A tap on a tile, a curve or a tag chooses it;
 * the rest fades.
 */
function RackDiagram({ topo, links, selected, setSelected, heatmap, freePctByDevice, traceMode, traceA, traceB, tracePathSet, traceEdgeKeys }) {
  const placed = (d) => d.class !== 'outside' && d.in_rack && d.u_position != null;
  const byName = useMemo(() => new Map(topo.devices.map((d) => [d.name, d])), [topo]);
  const rack = useMemo(() => ({
    key: 'r',
    name: topo.rackName,
    units: Math.max(1, topo.u_size || 1),
    devices: topo.devices.filter(placed).map((d) => {
      const p = Math.max(1, d.u_position), n = Math.max(1, d.u_size || 1);
      const units = unitsOf(d);
      const ports = (d.ports || []).length;
      return {
        id: d.name, name: d.name, top: p + n - 1, bottom: p, unit: units,
        kind: clsOf(d),
        sub: [classLabel(d), ports ? `${(d.ports || []).filter((p) => p.connected === true).length} of ${ports} ports cabled` : null].filter(Boolean).join(', '),
        ports: (d.ports || []).map((p) => p.connected),
        tone: heatmap ? capTone(freePctByDevice?.get(d.name)) : null,
        aria: `${d.name}, ${units || 'unit not read'}, ${classLabel(d)}`,
      };
    }),
  }), [topo, heatmap, freePctByDevice]);
  const keyOf = (name) => {
    const d = byName.get(name);
    return d && placed(d) ? `r|${name}` : `off:${name}`;
  };
  const endOf = (name, hollow) => {
    const d = byName.get(name);
    if (d && placed(d)) return { rack: 'r', device: name, hollow };
    const where = d ? classLabel(d) : 'Unit not read';
    return { off: { key: name, text: name, device: name, aria: `${name}, ${d?.class === 'outside' ? where : 'Unit not read'}` } };
  };
  const cables = useMemo(() => links.filter((l) => l.a !== l.b).map((l) => ({
    id: `${l.a}::${l.b}`,
    label: `${l.from} to ${l.to}, ${plural(l.count, 'cable')}`,
    count: l.count,
    tone: linkTone(l.cables),
    dashed: !l.cables.some((c) => c.seen),
    ends: [endOf(l.from, !l.cables.some((c) => c.seen)), endOf(l.to, !l.cables.some((c) => c.seen))],
  // eslint-disable-next-line react-hooks/exhaustive-deps
  })), [links, byName]);
  // the cables a sweep does see: the ones plugged into each device. Their other ends are not read, so each device's bundle
  // leaves the rack to a tag that says so (user, 2026-10-27: 'why don't I see any cables in the elevation')
  const looseCables = useMemo(() => topo.devices.filter(placed).map((d) => {
    const on = (d.ports || []).filter((p) => p.connected === true).length;
    return on ? { id: `loose::${d.name}`, label: `${plural(on, 'cable')} from ${d.name}, other ends not known`, count: on, tone: 'grey', dashed: true,
      ends: [endOf(d.name, false), { off: { key: 'loose', text: 'Other ends not read yet', device: null, aria: `${plural(on, 'cable')} from ${d.name}, other ends not known` } }] } : null;   // one shared tag: the rack keeps its width
  }).filter(Boolean), [topo]);

  let sel = null;
  if (selected?.kind === 'node') sel = { kind: 'device', key: keyOf(selected.id) };
  else if (selected?.kind === 'link') sel = { kind: 'cable', id: selected.id };
  else if (selected?.kind === 'cable') {
    const l = links.find((x) => x.cables.some((c) => c.cable_id === selected.id));
    if (l) sel = { kind: 'cable', id: `${l.a}::${l.b}` };
  }
  let focus = null;
  if (traceMode && tracePathSet && tracePathSet.size > 0) {
    focus = { devices: new Set([...tracePathSet].map(keyOf)), cables: new Set(traceEdgeKeys || []) };
  } else if (traceMode && (traceA || traceB)) {
    focus = { devices: new Set([traceA, traceB].filter(Boolean).map(keyOf)), cables: new Set() };
  }

  const loose = topo.devices.filter((d) => d.class !== 'outside' && !placed(d));
  const legend = ['ok', 'ink', 'bad', 'grey'].filter((t) => cables.some((c) => c.tone === t)).map((t) => LEGEND[t]);
  return (
    <>
      <TopologyDiagram
        racks={[rack]}
        cables={cables}
        selected={sel}
        focus={focus}
        onDevice={(s) => setSelected({ kind: 'node', id: s.id })}
        onCable={(s) => setSelected({ kind: 'link', id: s.id })}
        onClear={() => setSelected(null)}
        label={`Rack elevation of ${topo.rackName}`}
        testId="rack-diagram"
      />
      {loose.length ? (
        <p className={styles.loose}>
          <span>Unit not read:</span>
          {loose.map((d) => <TextButton key={d.name} small onClick={() => setSelected({ kind: 'node', id: d.name })}>{d.name}</TextButton>)}
        </p>
      ) : null}
      <TopologyLegend items={legend} />
    </>
  );
}

/** The one line under the drawing: the chosen device or link, or how to choose one. */
function captionOf(info) {
  if (info?.kind === 'node') {
    const n = info.peers.reduce((s, p) => s + p.count, 0);
    return <><b>{info.dev.name}</b> {unitsOf(info.dev) || classLabel(info.dev)}, {plural(n, 'cable')}</>;
  }
  if (info?.kind === 'link') return <><b>{info.link.from}</b> to <b>{info.link.to}</b>, {plural(info.link.count, 'cable')}</>;
  if (info?.kind === 'cable') return <><b>{cableName(info.cable)}</b> {info.cable.src.device} to {info.cable.dst.device}</>;
  return 'Tap a device to open it.';
}

function DetailsHead({ title, sub, onClose }) {
  return (
    <div className={styles.cardHead}>
      <div className={styles.cardText}>
        <h3 className={styles.cardT}>{title}</h3>
        {sub ? <p className={styles.sub}>{sub}</p> : null}
      </div>
      <TextButton small onClick={onClose}>Close</TextButton>
    </div>
  );
}

/** A chosen device: its facts as label and value rows, its ports folded under one row, and the way to its card. */
// The device card of the RackTrack app (user, 2026-10-27): the device's crop, its name, unit and kind, its ports in one line,
// and Open. It slides up over the drawing in the app's own look; the RT-EOS sheet is not used on the phone.
function Legend({ topo }) {
  const counts = new Map();
  for (const d of topo.devices) { if (d.class === 'outside') continue; const k = kindKey(clsOf(d)); counts.set(k, (counts.get(k) || 0) + 1); }
  const items = [...counts.entries()].sort((a, b) => b[1] - a[1]);
  if (!items.length) return null;
  return (
    <div className={styles.legend} aria-label="Kinds">
      {items.map(([k, n], i) => <span key={k} className={styles.legendItem} style={{ '--i': i }}><i style={{ background: (KIND[k] || KIND.unknown)[0] }} />{k === 'unknown' ? 'Not sure' : CLASS_LABEL_LOCAL[k] || k}<b>{n}</b></span>)}
    </div>
  );
}
const CLASS_LABEL_LOCAL = { switch: 'Switch', router: 'Router', firewall: 'Firewall', patch_panel: 'Patch panel', server: 'Server', pdu: 'PDU', ups: 'UPS', gateway: 'Gateway', storage: 'Storage', cable_manager: 'Cable manager', shelf: 'Shelf', closed_unit: 'Closed unit' };

function DeviceCard({ dev, card, onOpen, onClose }) {
  const c = card || {};
  return (
    <div className={styles.devcard} role="dialog" aria-label={dev.name}>
      {c.crop ? <img className={styles.devcardImg} src={c.crop} alt="" /> : null}
      <div className={styles.devcardBody}>
        <p className={styles.devcardEyebrow}>{[c.unit, c.size, c.kind].filter(Boolean).join(' · ')}</p>
        <h3 className={styles.devcardName}>{dev.name}</h3>
        {c.state ? <span className={cx(styles.devcardState, styles[`st_${c.stateKey}`])}><i />{c.state}</span> : null}
        {c.ports ? <p className={styles.devcardPorts}>{c.ports}</p> : null}
        <div className={styles.devcardActs}>
          {onOpen && dev.deviceId != null ? <button type="button" className={styles.devcardOpen} onClick={() => onOpen({ deviceId: dev.deviceId })}>Open device</button> : null}
          <button type="button" className={styles.devcardClose} onClick={onClose}>Close</button>
        </div>
      </div>
    </div>
  );
}

function DeviceDetails({ dev, peers, topo, onOpen, onClose, laptop }) {
  const ports = dev.ports || [];
  const on = ports.filter((p) => p.connected === true).length;
  const free = ports.filter((p) => p.connected === false).length;
  const unk = ports.length - on - free;
  const outside = dev.class === 'outside';
  const portsWord = !ports.length
    ? (outside ? 'None named in the Excel dataset' : 'No ports were read')
    : outside
      ? `${plural(ports.length, 'port')} named in the Excel dataset`
      : `${plural(ports.length, 'port')}: ${on} connected, ${free} free${unk ? `, ${unk} not known` : ''}`;
  const cablesWord = peers.length
    ? peers.map((p) => `${p.count} to ${p.peer}`).join(', ')
    : on ? `${plural(on, 'cable')} plugged in; where ${on === 1 ? 'it goes' : 'they go'} is not known yet` : 'No cable plugged in';
  return (
    <>
      {laptop ? <DetailsHead title={dev.name} sub={outside ? classLabel(dev) : kindLine(dev)} onClose={onClose} /> : null}
      <dl className={styles.facts}>
        <div><dt>Device</dt><dd>{dev.name}</dd></div>
        {outside ? (
          <>
            <div><dt>Where</dt><dd>{classLabel(dev)}</dd></div>
            <div><dt>Unit</dt><dd>{dev.model || 'Not known'}</dd></div>
          </>
        ) : (
          <>
            <div><dt>Unit</dt><dd>{unitsOf(dev) || 'Not read'}</dd></div>
            <div><dt>Kind</dt><dd>{kindLine(dev)}</dd></div>
          </>
        )}
        <div><dt>Ports</dt><dd>{portsWord}</dd></div>
        <div><dt>Cables</dt><dd>{cablesWord}</dd></div>
      </dl>
      {ports.length ? (
        <Disclosure title="Ports" count={ports.length} className={styles.disc}>
          <PortsTable dev={dev} topo={topo} />
        </Disclosure>
      ) : null}
      {(onOpen && dev.deviceId != null) || !laptop ? (
        <div className={styles.acts}>
          {onOpen && dev.deviceId != null ? <TextButton onClick={() => onOpen({ deviceId: dev.deviceId })}>Open device</TextButton> : null}
          {laptop ? null : <TextButton onClick={onClose}>Close</TextButton>}
        </div>
      ) : null}
    </>
  );
}

/** "SW-CORE-01 port SFP 1": a cable end in words, with the port's label as read. */
function endWords(byName, side) {
  if (!side?.device) return 'Not known';
  const d = byName.get(side.device);
  const p = d?.ports?.find((q) => q.name === side.port);
  const label = p ? (p.label || p.name) : side.port;
  return `${side.device}${label ? ` port ${label}` : ''}`;
}

/** A chosen cable: its two ends, what it is, and where it is known from. */
function CableDetails({ cable, topo, onClose, laptop }) {
  const byName = useMemo(() => new Map(topo.devices.map((d) => [d.name, d])), [topo]);
  const what = [cable.cable_type, cable.connector, cable.color].filter(Boolean).join(', ') || 'Type not known';
  return (
    <>
      {laptop ? <DetailsHead title={cable.cable_label ? `Cable ${cable.cable_label}` : 'Cable, no label'} sub={what} onClose={onClose} /> : null}
      <dl className={styles.facts}>
        <div><dt>From</dt><dd>{endWords(byName, cable.src)}</dd></div>
        <div><dt>To</dt><dd>{endWords(byName, cable.dst)}</dd></div>
        <div><dt>Cable</dt><dd>{what}</dd></div>
        <div><dt>Known from</dt><dd>{cable.seen ? 'Seen on the photo' : 'Excel only'}</dd></div>
      </dl>
      {laptop ? null : <div className={styles.acts}><TextButton onClick={onClose}>Close</TextButton></div>}
    </>
  );
}

/** A chosen link: the two devices, how many cables run between them, and each cable in a row that opens it. */
function LinkDetails({ link, topo, onClose, laptop, onCable }) {
  const byName = useMemo(() => new Map(topo.devices.map((d) => [d.name, d])), [topo]);
  const seen = link.cables.filter((c) => c.seen).length;
  const excel = link.count - seen;
  const count = `${link.count}${seen && excel ? `: ${seen} seen on the photo, ${excel} Excel only` : seen ? ', seen on the photo' : ', Excel only'}`;
  const portOn = (c, device) => {
    const side = c.src.device === device ? c.src : c.dst;
    const d = byName.get(device);
    const p = d?.ports?.find((q) => q.name === side.port);
    return p ? (p.label || p.name) : side.port || null;
  };
  const columns = [
    { key: 'cable', label: 'Cable', main: true, render: (c) => cableName(c) },
    { key: 'from', label: `On ${link.from}`, render: (c) => portOn(c, link.from) },
    { key: 'to', label: `On ${link.to}`, render: (c) => portOn(c, link.to) },
    { key: 'type', label: 'Type', render: (c) => c.cable_type || null },
    { key: 'seen', label: 'Known from', render: (c) => (c.seen ? 'Seen on the photo' : 'Excel only') },
  ];
  return (
    <>
      {laptop ? <DetailsHead title={`${link.from} to ${link.to}`} sub={plural(link.count, 'cable')} onClose={onClose} /> : null}
      <dl className={styles.facts}>
        <div><dt>From</dt><dd>{link.from}</dd></div>
        <div><dt>To</dt><dd>{link.to}</dd></div>
        <div><dt>Cables</dt><dd>{count}</dd></div>
      </dl>
      <Disclosure title="Cables" sub="Tap one to see it" count={link.count} className={styles.disc} defaultOpen>
        <DataTable columns={columns} rows={link.cables} rowKey={(c) => c.cable_id} onRow={(c) => onCable(c.cable_id)} caption={`Cables from ${link.from} to ${link.to}`} />
      </Disclosure>
      {laptop ? null : <div className={styles.acts}><TextButton onClick={onClose}>Close</TextButton></div>}
    </>
  );
}

/** A device's ports: the state read, the cable on each, and the other end. */
function PortsTable({ dev, topo }) {
  const byName = useMemo(() => new Map(topo.devices.map((d) => [d.name, d])), [topo]);
  // Keyed by device AND port: port "1" of one device is not port "1" of another.
  const byPort = useMemo(() => {
    const m = new Map();
    for (const e of topo.edges) {
      for (const side of ['src', 'dst']) {
        const k = `${e[side].device}::${e[side].port}`;
        if (!m.has(k)) m.set(k, []);
        m.get(k).push({ self: e[side], peer: side === 'src' ? e.dst : e.src, edge: e });
      }
    }
    return m;
  }, [topo]);
  const rows = useMemo(() => (dev.ports || []).map((p) => {
    const links = byPort.get(`${dev.name}::${p.name}`) || [];
    return { key: p.name, port: p, links, primary: links[0] };
  }), [dev, byPort]);

  const columns = [
    { key: 'port', label: 'Port', main: true, render: (r) => r.port.label || r.port.name },
    { key: 'status', label: 'Status', render: (r) => portWord(r.port) },
    {
      key: 'cable', label: 'Cable',
      render: (r) => (r.primary ? <>{cableName(r.primary.edge)}{r.primary.edge.seen ? null : <span className={styles.dim}> Excel only</span>}</> : null),
    },
    { key: 'connector', label: 'Connector', render: (r) => r.port.connector || r.primary?.edge.connector || null },
    { key: 'type', label: 'Type', render: (r) => r.primary?.edge.cable_type || null },
    {
      key: 'end', label: 'Other end',
      render: (r) => (r.primary
        ? `${endWords(byName, r.primary.peer)}${r.links.length > 1 ? ` and ${r.links.length - 1} more` : ''}`
        : null),
    },
  ];
  return <DataTable columns={columns} rows={rows} rowKey={(r) => r.key} caption={`Ports of ${dev.name}`} />;
}
