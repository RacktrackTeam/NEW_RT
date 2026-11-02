import { useState } from 'react';
import styles from './PortsCheck.module.css';

/**
 * Ports: what is cabled in the photograph against what the switch and NetBox
 * say is connected.
 *
 * The drift check compares boxes. This is the same question one level down, and
 * it answers with three words per port - what the camera saw, what the switch
 * reports, what the record holds - and says whether they agree. It decides
 * nothing and sends nothing; it is context for whoever reads the check.
 *
 * Closed by default, with one number on the closed line - how many ports were
 * read - because most racks have more ports than anybody wants to scroll past on
 * the way to the Send button. The three figures used to sit on that line, where
 * they read as a second strip of numbers beside the one the page already had;
 * they are a sentence inside now. Opened, the ports that do not agree come
 * first; the ones that do sit behind a second line, because "21 ports match" is
 * all most people need of them.
 */

const PHOTO = { cabled: 'cable', empty: 'no cable' };
const SWITCH = { up: 'up', down: 'down' };
const NETBOX = { connected: 'connected', not_connected: 'not connected' };
const said = (words, value) => words[value] || 'not known';

// The server names a box in words ("Switch on shelf U18"). Should a key ever
// arrive instead, it is RackTrack's own and is never shown.
const deviceWords = (row) => (row.device && !/^(dev|nb|if|rack):/i.test(row.device) ? row.device : 'A device');
const portWords = (row) => (row.port != null ? `port ${row.port}` : row.portName ? `port ${row.portName}` : 'a port');

function Rows({ rows, differs = false }) {
  return (
    <ul className={styles.rows}>
      {rows.map((row, i) => (
        <li key={`${row.deviceUid || row.device}:${row.port ?? row.portName ?? i}`} className={styles.row}>
          <span className={styles.where}>{deviceWords(row)} - {portWords(row)}</span>
          <span className={styles.cells}>
            <span>Photo: {said(PHOTO, row.camera)}</span>
            <span>Switch: {said(SWITCH, row.switch)}</span>
            <span>NetBox: {said(NETBOX, row.netbox)}</span>
          </span>
          {row.why && <span className={differs ? styles.whyNot : styles.why}>{row.why}</span>}
        </li>
      ))}
    </ul>
  );
}

export default function PortsCheck({ ports, open: alwaysOpen = false }) {
  const [open, setOpen] = useState(false);
  const [showMatched, setShowMatched] = useState(false);
  if (!ports || ports.ok === false) return null;

  const rows = Array.isArray(ports.rows) ? ports.rows : [];
  if (rows.length === 0 && !ports.note) return null;
  const differ = rows.filter((r) => r.verdict === 'mismatch');
  const agree = rows.filter((r) => r.verdict === 'match');
  const count = (key, fallback) => (Number.isFinite(Number(ports.summary?.[key])) ? Number(ports.summary[key]) : fallback);
  const matched = count('match', agree.length);
  const notMatched = count('mismatch', differ.length);
  const notKnown = count('unknown', rows.length - agree.length - differ.length);
  // How many ports this check read, which is the one number the closed row needs.
  const total = matched + notMatched + notKnown;

  return (
    <section className={styles.ports} aria-label="Ports">
      {!alwaysOpen && (
        <button type="button" className={styles.top} aria-expanded={open} onClick={() => setOpen((v) => !v)}>
          <span className={styles.name}>Ports</span>
          <span className={styles.count}>{total}</span>
          <span className={styles.chevron} aria-hidden="true" />
        </button>
      )}

      {(alwaysOpen || open) && (
        <div className={styles.inside}>
          <p className={styles.sub}>Cables in the photo against what the switch and NetBox say.</p>
          <p className={styles.tally}>Matched {matched}, not matched {notMatched}, not known {notKnown}.</p>
          {ports.note && <p className={styles.note}>{ports.note}</p>}
          {differ.length > 0 && <Rows rows={differ} differs />}
          {agree.length > 0 && (
            <>
              <button type="button" className={styles.fold} aria-expanded={showMatched}
                      onClick={() => setShowMatched((v) => !v)}>
                Matched ports ({agree.length})<span className={styles.chevron} aria-hidden="true" />
              </button>
              {showMatched && <Rows rows={agree} />}
            </>
          )}
        </div>
      )}
    </section>
  );
}
