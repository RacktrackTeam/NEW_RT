import { useEffect, useState } from 'react';
import { TextButton } from './Button.jsx';
import { Facts, Grey } from './Text.jsx';
import { getJSON, enc, notBuilt } from '../utils/http';
import { portTypeWord } from '../utils/rack';
import styles from './DeviceInfo.module.css';

/**
 * What is known about one device model: its card from the server (crop,
 * make and model read, specifications, ports). The specifications are
 * looked up in the background on the server, so the card is asked again
 * while they say "looking".
 */
export function useDeviceCard(scanId, deviceId) {
  const [card, setCard] = useState(null);
  const [error, setError] = useState(null);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (!scanId || deviceId == null) return undefined;
    let gone = false;
    let timer = null;
    let tries = 0;
    setCard(null); setError(null);
    const load = async () => {
      try {
        const d = await getJSON(`/api/scans/${enc(scanId)}/devices/${enc(deviceId)}`);
        if (gone) return;
        setCard(d);
        if (d?.specsStatus === 'looking' && tries < 20) { tries += 1; timer = setTimeout(load, 3000); }
      } catch (e) { if (!gone) setError(e); }
    };
    load();
    return () => { gone = true; if (timer) clearTimeout(timer); };
  }, [scanId, deviceId, tick]);
  return { card, error, reload: () => setTick((t) => t + 1) };
}

/**
 * One value. Where it came from and how sure the reading was stay on the
 * server: people see the value only (no confidence text on screen).
 */
export function Sourced({ children, mono }) {
  return (
    <span className={styles.sourced}>
      <span className={mono ? 'mono' : undefined}>{children}</span>
    </span>
  );
}

/**
 * What is known of the device, each detail with its source and the real
 * confidence of that source (fields from the server, lib/field_sources.js).
 */
export function ReadFacts({ showMakeModel = true, make, model, type, unit, heightU, labelText, sheetName, fields = {} }) {
  const f = fields || {};
  return (
    <Facts pairs={[
      showMakeModel ? ['Make', make ? <Sourced key="m" field={f.make}>{make}</Sourced> : <span key="m" className={styles.grey}>Not read</span>] : null,
      showMakeModel ? ['Model', model ? <Sourced key="o" field={f.model}>{model}</Sourced> : <span key="o" className={styles.grey}>Not read</span>] : null,
      type ? ['Kind', <Sourced key="k" field={f.type}>{type}</Sourced>] : null,
      unit ? ['Unit', <Sourced key="u" field={f.unit}>{unit}</Sourced>] : null,
      heightU ? ['Height', <Sourced key="h" field={f.height}>{`${heightU}U`}</Sourced>] : null,
      labelText ? ['Label on it', <Sourced key="l" field={f.label} mono>{labelText}</Sourced>] : null,
      sheetName ? ['Name in the sheet', <Sourced key="s" field={f.sheetName}>{sheetName}</Sourced>] : null,
    ]} />
  );
}

const LOOKUPS_OFF = 'Online lookups are off for your organization, so nothing is looked up on the web. Your admin can turn them on in Organization settings.';

/** Specifications from the maker's datasheet, or why there are none. */
export function SpecFacts({ specs, status, error, heightU, lookupsOff }) {
  if (specs) {
    return (
      <Facts pairs={[
        specs.heightU || heightU ? ['Height', `${specs.heightU || heightU}U`] : null,
        specs.portCount != null ? ['Ports', String(specs.portCount)] : null,
        specs.portTypes?.length ? ['Port types', specs.portTypes.map((t) => `${t.count} ${portTypeWord(t.type) || t.type}`).join(', ')] : null,
        specs.power ? ['Power', specs.power] : null,
        specs.datasheetUrl ? ['Datasheet', <TextButton key="ds" href={specs.datasheetUrl} target="_blank" rel="noreferrer" small>Open the datasheet</TextButton>] : null,
        lookupsOff ? ['Lookups', <span key="lo" className={styles.grey}>Online lookups are off; this came from what the server already holds.</span>] : null,
      ]} />
    );
  }
  const note = {
    looking: 'Looking up the specifications.',
    not_found: 'No specifications were found for this model.',
    no_model: 'The model could not be read, so there are no specifications. Take a closer photo of the faceplate.',
  }[status] || (notBuilt(error) ? 'Specifications are not available on this server yet.' : error ? error.message : 'Looking up the specifications.');
  return (
    <>
      <Grey m0 className={styles.note}>{note}</Grey>
      {lookupsOff ? <Grey m0 className={styles.note}>{LOOKUPS_OFF}</Grey> : null}
    </>
  );
}

/** Optics and cables to buy for the fibre and SFP ports. items: sfp-advice items. */
export function OpticsList({ items }) {
  if (!items?.length) return <Grey m0 className={styles.note}>No SFP or fibre ports were read on this device, so there are no optics to buy.</Grey>;
  return (
    <ul className={styles.optics}>
      {items.map((it, i) => (
        <li key={`${it.portType}-${i}`} className={styles.optic}>
          <p className={styles.opticT}>{it.count} x {portTypeWord(it.portType) || it.portType}</p>
          <p><span className={styles.k}>Optic</span> {it.suggestion?.optic}</p>
          <p><span className={styles.k}>Cable</span> {it.suggestion?.cable}</p>
          {it.suggestion?.note ? <p className={styles.grey}>{it.suggestion.note}</p> : null}
        </li>
      ))}
    </ul>
  );
}
