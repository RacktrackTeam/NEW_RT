import { useEffect, useMemo, useState } from 'react';
import { apiUrl, authFetch } from '../utils/api';
import { lookupVendors, ouiKey } from '../utils/oui';
import { buildConnections } from '../utils/connections';
import styles from './ConnectionsView.module.css';

/*
 * Network > Connections: every switch port in use, what is on the other end,
 * and the cable the photograph shows in that socket (the owner, 25 September
 * 2026). The rows are worked out in utils/connections.js; this draws them.
 *
 * switches: [{ id, label, unit, reading }] - the phone's readings, and the
 * shelf each switch was placed on (its box's position), or null.
 */

/** The units a physical-layer row covers: "u10,u11" -> [10, 11]. */
const unitsOf = (s) => String(s || '').split(',').map((u) => Number(String(u).replace(/\D/g, ''))).filter(Boolean);

/** The sockets the camera saw on the switch at this shelf, or null. */
function photoAt(layer, unit) {
  const at = Number(String(unit ?? '').replace(/\D/g, ''));
  if (!layer || !at) return null;
  for (const u of layer.units || []) {
    if (!unitsOf(u.unit).includes(at)) continue;
    const devs = u.devices || [];
    const d = devs.find((x) => /switch/i.test(x.device_type || '')) || devs[0];
    if (d) return d.ports || [];
  }
  return null;
}

const HOW = {
  lldp: { tag: 'Reported', tip: 'Reported by the switch' },
  traffic: { tag: 'Traffic', tip: 'Found from network traffic' },
  likely: { tag: 'Likely', tip: 'Likely match from network traffic' },
  through: { tag: 'Via switch', tip: 'Reached through another switch' },
};

const speed = (m) => (!m ? null : m >= 1000 ? `${m / 1000} Gb` : `${m} Mb`);

function OtherEnd({ o }) {
  if (o.kind === 'switch') {
    return <span className={styles.end}><b>{o.name}</b>{o.port ? <span className={styles.mono}>{o.port}</span> : null}</span>;
  }
  if (o.kind === 'named') {
    return (
      <span className={styles.endList}>
        {o.names.map((n, i) => (
          <span key={`${n.name}-${i}`} className={styles.end}>
            <b>{n.name || 'Unnamed'}</b>{n.id ? <span className={styles.mono}>{n.id}</span> : null}
          </span>
        ))}
      </span>
    );
  }
  if (o.kind === 'through') return <span className={styles.end}><span className={styles.muted}>Leads to</span> <b>{o.names.join(', ')}</b></span>;
  if (o.kind === 'devices') {
    const first = o.devices[0];
    const rest = o.devices.length - 1;
    return (
      <span className={styles.end}>
        <b>{first.maker || 'Unknown maker'}</b>
        <span className={styles.mono}>{first.ip || first.mac}</span>
        {rest > 0 ? <span className={styles.more}>+{rest} more</span> : null}
      </span>
    );
  }
  return <span className={styles.muted}>No activity yet</span>;
}

const SWATCH = { white: '#ffffff', black: '#111111', blue: '#2563eb', red: '#dc2626', yellow: '#eab308', green: '#16a34a', grey: '#9ca3af', gray: '#9ca3af', orange: '#ea580c', purple: '#7c3aed', pink: '#ec4899' };

function Cable({ p }) {
  if (p.state === 'unplaced') return <span className={styles.muted}>Switch not placed in photo</span>;
  if (p.state === 'unseen') return <span className={styles.muted}>Not seen in photo</span>;
  if (p.state === 'empty') return <span className={styles.cableTxt}>Empty socket</span>;
  if (p.state === 'connected') {
    const sw = p.color ? SWATCH[String(p.color).toLowerCase()] : null;
    return (
      <span className={styles.cableTxt}>
        {sw ? <i className={styles.swatch} style={{ background: sw }} aria-hidden="true" /> : null}
        Plugged in{p.connector ? ` · ${p.connector}` : ''}{p.color ? ` · ${p.color}` : ''}
      </span>
    );
  }
  return <span className={styles.muted}>Unclear in photo</span>;
}

export default function ConnectionsView({ rackId, switches, onOpenSwitch }) {
  const [layer, setLayer] = useState(null);
  const [vendors, setVendors] = useState({});
  const [only, setOnly] = useState('all');     // a switch id, or 'all'
  const [which, setWhich] = useState('use');   // 'use' | 'all'

  // The photograph's sockets, per shelf. A rack without a physical layer yet
  // simply has no cable column to show.
  useEffect(() => {
    if (!rackId) return undefined;
    let gone = false;
    authFetch(apiUrl(`/api/scan/${encodeURIComponent(rackId)}/physical-layer`))
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => { if (!gone) setLayer(d); })
      .catch(() => {});
    return () => { gone = true; };
  }, [rackId]);

  const withPhoto = useMemo(() => switches.map((s) => ({ ...s, photo: s.unit != null ? photoAt(layer, s.unit) : null })), [switches, layer]);

  // Who made each device, from its MAC.
  const prefixes = useMemo(() => [...new Set(withPhoto.flatMap((s) => (s.reading?.attached || []).map((a) => ouiKey(a.mac))).filter(Boolean))], [withPhoto]);
  useEffect(() => {
    if (!prefixes.length) return undefined;
    let gone = false;
    lookupVendors(prefixes).then((v) => { if (!gone) setVendors(v); });
    return () => { gone = true; };
  }, [prefixes]);

  const rows = useMemo(() => buildConnections(withPhoto, { vendors, all: true }), [withPhoto, vendors]);
  const mine = only === 'all' ? rows : rows.filter((r) => r.switchId === only);
  const count = { use: mine.filter((r) => r.inUse).length, all: mine.length };
  const shown = mine.filter((r) => (which === 'use' ? r.inUse : true));
  const read = switches.filter((s) => s.reading);
  const unread = switches.length - read.length;

  const links = mine.filter((r) => r.other.kind === 'switch').length;
  const devices = mine.reduce((n, r) => n + (r.other.kind === 'devices' ? r.other.devices.length : r.other.kind === 'named' ? r.other.names.length : 0), 0);

  return (
    <section className={styles.wrap}>
      <header className={styles.head}>
        <div>
          <h2>Connections</h2>
          <p>Switch ports and what they connect to</p>
        </div>
      </header>

      <dl className={styles.stats}>
        <div><dt>Ports in use</dt><dd>{count.use}</dd></div>
        <div><dt>Switch links</dt><dd>{links}</dd></div>
        <div><dt>Devices seen</dt><dd>{devices}</dd></div>
      </dl>

      <div className={styles.controls}>
        <div className={styles.seg} role="tablist" aria-label="Which switch">
          <button type="button" role="tab" aria-selected={only === 'all'} className={only === 'all' ? styles.segOn : ''} onClick={() => setOnly('all')}>All</button>
          {read.map((s) => (
            <button key={s.id} type="button" role="tab" aria-selected={only === s.id} className={only === s.id ? styles.segOn : ''} onClick={() => setOnly(s.id)}>{s.label}</button>
          ))}
        </div>
        <div className={styles.tabs} role="tablist" aria-label="Which ports">
          {[['use', 'In use'], ['all', 'All ports']].map(([k, label]) => (
            <button key={k} type="button" role="tab" aria-selected={which === k} className={which === k ? styles.tabOn : ''} onClick={() => setWhich(k)}>
              {label}<span>{count[k]}</span>
            </button>
          ))}
        </div>
      </div>

      {unread > 0 && (
        <p className={styles.note}>{unread === 1 ? '1 switch has' : `${unread} switches have`} not been read yet.</p>
      )}
      {read.length === 0 ? (
        <p className={styles.empty}>Read a switch to see its connections.</p>
      ) : shown.length === 0 ? (
        <p className={styles.empty}>No ports in use.</p>
      ) : (
        <div className={styles.table} role="table" aria-label="Connections">
          <div className={styles.thead} role="row">
            <span role="columnheader">Switch port</span>
            <span role="columnheader">Link</span>
            <span role="columnheader">Other end</span>
            <span role="columnheader">Known by</span>
            <span role="columnheader">Cable in photo</span>
          </div>
          {shown.map((r) => (
            <div key={r.key} role="row" className={styles.row}>
              <button type="button" className={styles.from} onClick={() => onOpenSwitch && onOpenSwitch(r.switchId)} title="Open this switch">
                <b>{r.switchLabel}</b>
                <span className={styles.mono}>{r.port}</span>
                {r.unit != null ? <span className={styles.unit}>U{String(r.unit).replace(/\D/g, '')}</span> : null}
              </button>
              <span className={styles.link}>
                <i className={r.up ? styles.dotUp : styles.dotDown} aria-hidden="true" />
                {r.up ? (speed(r.speedMbps) || 'Up') : 'Down'}
              </span>
              <span className={styles.to}>
                <span className={styles.arrow} aria-hidden="true">→</span>
                <OtherEnd o={r.other} />
              </span>
              <span className={styles.how}>
                {r.other.how ? <span className={styles.tag} title={HOW[r.other.how].tip}>{HOW[r.other.how].tag}</span> : <span className={styles.muted}>-</span>}
              </span>
              <span className={styles.cable}><Cable p={r.photo} /></span>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
