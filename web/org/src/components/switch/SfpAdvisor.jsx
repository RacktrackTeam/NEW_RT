import { useState } from 'react';
import { TextButton } from '../Button.jsx';
import { Grey } from '../Text.jsx';
import styles from './SfpAdvisor.module.css';

// The SFP modules that fit one switch: RackTrack's curated catalogue
// (components/SfpAdvisor.jsx at commit 625ebdce), shown as a short list.
//
// RackTrack asked /api/sfp/analyze, which searched retailer sites; that web
// search is not part of this app, so the advice is always the catalogue for
// the switch's slot type. The slot type comes from the server (routes/detail.js
// sfpOf: the datasheet's port list, the ports the photo read, and the
// model's own speed hints). Nothing identified the switch: it says so and
// stops. No SFP or fibre cage anywhere: one quiet line.

// Common SFP slot types, from RackTrack's utils/sfpDatabase.js.
export const SFP_SLOT_TYPES = {
  SFP: { name: 'SFP', desc: 'SFP/LC', speed: '1G', maxSlots: 48 },
  'SFP+': { name: 'SFP+', desc: 'SFP+/LC', speed: '10G', maxSlots: 48 },
  SFP28: { name: 'SFP28', desc: 'SFP28/LC', speed: '25G', maxSlots: 48 },
  XFP: { name: 'XFP', desc: 'XFP/LC', speed: '10G', maxSlots: 48 },
  QSFP: { name: 'QSFP', desc: 'QSFP', speed: '40G', maxSlots: 4 },
  'QSFP+': { name: 'QSFP+', desc: 'QSFP+', speed: '40G', maxSlots: 4 },
  QSFP28: { name: 'QSFP28', desc: 'QSFP28', speed: '100G', maxSlots: 4 },
  'QSFP-DD': { name: 'QSFP-DD', desc: 'QSFP-DD', speed: '400G', maxSlots: 4 },
  OSFP: { name: 'OSFP', desc: 'OSFP', speed: '400G/800G', maxSlots: 4 },
};

// CURATED CATALOG - every entry below was verified live against the
// manufacturer's own product page on 2026-05-28. URL and price are real. If
// Mikrotik renames a slug, an entry here may need refreshing - but
// everything links to the manufacturer (no third-party reseller), so the
// destination is always the actual product.
export const CURATED_CATALOG = {
  // SFP+ TRANSCEIVERS only - these are the modules that go INTO an
  // SFP+ port. DAC / AOC cables (which terminate in SFP+ ends and
  // replace the module+fiber pair) are listed separately so the user
  // sees them as cabling, not as port-fillers.
  'SFP+': [
    { brand: 'Mikrotik', partNumber: 'S+85DLC03D',  type: 'SR',  speed: '10G',       maxDistance: '300m',  wavelength: '850nm',
      price: '$59', sourceUrl: 'https://mikrotik.com/product/Splus85DLC03D',
      imageUrl: 'https://cdn.mikrotik.com/web-assets/rb_images/813_lg.webp' },
    { brand: 'Mikrotik', partNumber: 'S+31DLC10D',  type: 'LR',  speed: '10G',       maxDistance: '10km',  wavelength: '1310nm',
      price: '$69', sourceUrl: 'https://mikrotik.com/product/s_31dlc10d',
      imageUrl: 'https://cdn.mikrotik.com/web-assets/rb_images/2514_lg.webp' },
    { brand: 'Mikrotik', partNumber: 'S+RJ10',      type: 'T',   speed: '10G',       maxDistance: '30m',
      price: '$65', sourceUrl: 'https://mikrotik.com/product/s_rj10',
      imageUrl: 'https://cdn.mikrotik.com/web-assets/rb_images/1351_lg.webp' },
    { brand: 'Mikrotik', partNumber: 'XS+85LC01D',  type: 'SR',  speed: '10G/25G',   maxDistance: '100m',  wavelength: '850nm',
      price: '$59', sourceUrl: 'https://mikrotik.com/product/xs_85lc01d',
      imageUrl: 'https://cdn.mikrotik.com/web-assets/rb_images/2345_lg.webp' },
  ],
  'QSFP+': [
    { brand: 'Mikrotik', partNumber: 'XQ+DA0001',   type: 'DAC', speed: '40/100G', maxDistance: '1m',
      sourceUrl: 'https://mikrotik.com/product/xq_da0001' },
    { brand: 'Mikrotik', partNumber: 'XQ+85MP01D',  type: 'SR4', speed: '40/100G', maxDistance: '100m', wavelength: '850nm',
      sourceUrl: 'https://mikrotik.com/product/xq_85mp01d' },
  ],
  'QSFP28': [
    { brand: 'Mikrotik', partNumber: 'XQ+DA0001',   type: 'DAC', speed: '100G', maxDistance: '1m',
      sourceUrl: 'https://mikrotik.com/product/xq_da0001' },
    { brand: 'Mikrotik', partNumber: 'XQ+DA0003',   type: 'DAC', speed: '100G', maxDistance: '3m',
      sourceUrl: 'https://mikrotik.com/product/xq_da0003' },
    { brand: 'Mikrotik', partNumber: 'XQ+85MP01D',  type: 'SR4', speed: '100G', maxDistance: '100m', wavelength: '850nm',
      sourceUrl: 'https://mikrotik.com/product/xq_85mp01d' },
    { brand: 'Mikrotik', partNumber: 'XQ+31LC02D',  type: 'LR',  speed: '100G', maxDistance: '2km',  wavelength: '1310nm',
      sourceUrl: 'https://mikrotik.com/product/xq_31lc02d' },
    { brand: 'Mikrotik', partNumber: 'XQ+31LC10D',  type: 'LR',  speed: '100G', maxDistance: '10km', wavelength: '1310nm',
      sourceUrl: 'https://mikrotik.com/product/xq_31lc10d' },
  ],
  'SFP': [
    { brand: 'Mikrotik', partNumber: 'XS+DA0001',   type: 'DAC', speed: '1G/10G/25G', maxDistance: '1m',
      price: '$29', sourceUrl: 'https://mikrotik.com/product/xs_da0001',
      imageUrl: 'https://cdn.mikrotik.com/web-assets/rb_images/2003_lg.webp' },
    { brand: 'Mikrotik', partNumber: 'XS+85LC01D',  type: 'SR',  speed: '1G/10G/25G', maxDistance: '100m', wavelength: '850nm',
      sourceUrl: 'https://mikrotik.com/product/xs_85lc01d' },
    { brand: 'Mikrotik', partNumber: 'XS+31LC10D',  type: 'LR',  speed: '1G/10G/25G', maxDistance: '10km', wavelength: '1310nm',
      sourceUrl: 'https://mikrotik.com/product/xs_31lc10d' },
  ],
};

// Pre-terminated cables (DAC/AOC) - these REPLACE the module+fiber
// combo by plugging into the SFP+ port directly on both ends. We list
// them separately from the transceiver modules so the user sees a
// clear distinction: "what goes in the port" vs "the cable to run".
export const CURATED_CABLES = {
  'SFP+': [
    { brand: 'Mikrotik', partNumber: 'XS+DA0001', type: 'DAC', speed: '10G', maxDistance: '1m',
      price: '$29', sourceUrl: 'https://mikrotik.com/product/xs_da0001',
      imageUrl: 'https://cdn.mikrotik.com/web-assets/rb_images/2003_lg.webp' },
    { brand: 'Mikrotik', partNumber: 'XS+DA0003', type: 'DAC', speed: '10G', maxDistance: '3m',
      price: '$39', sourceUrl: 'https://mikrotik.com/product/xs_da0003',
      imageUrl: 'https://cdn.mikrotik.com/web-assets/rb_images/2002_lg.webp' },
    { brand: 'Mikrotik', partNumber: 'S+AO0005',  type: 'AOC', speed: '10G', maxDistance: '5m',
      price: '$49', sourceUrl: 'https://mikrotik.com/product/s_ao0005',
      imageUrl: 'https://cdn.mikrotik.com/web-assets/rb_images/1729_lg.webp' },
  ],
  'QSFP+':  [
    { brand: 'Mikrotik', partNumber: 'XQ+DA0001', type: 'DAC', speed: '40G', maxDistance: '1m',
      sourceUrl: 'https://mikrotik.com/product/xq_da0001' },
  ],
  'QSFP28': [
    { brand: 'Mikrotik', partNumber: 'XQ+DA0001', type: 'DAC', speed: '100G', maxDistance: '1m',
      sourceUrl: 'https://mikrotik.com/product/xq_da0001' },
    { brand: 'Mikrotik', partNumber: 'XQ+DA0003', type: 'DAC', speed: '100G', maxDistance: '3m',
      sourceUrl: 'https://mikrotik.com/product/xq_da0003' },
  ],
  'SFP': [
    { brand: 'Mikrotik', partNumber: 'XS+DA0001', type: 'DAC', speed: '1G/10G/25G', maxDistance: '1m',
      price: '$29', sourceUrl: 'https://mikrotik.com/product/xs_da0001',
      imageUrl: 'https://cdn.mikrotik.com/web-assets/rb_images/2003_lg.webp' },
  ],
};

// How many rows a list shows before the rest fold away.
const SHOWN = 3;

/** One catalogue row: the type pill, the part number with its distance and speed, the price, and Buy on the maker's page. */
function Row({ m }) {
  const meta = [m.maxDistance, m.speed].filter(Boolean).join(' · ');
  return (
    <li className={styles.row}>
      <span className={styles.type}>{m.type ? <span className={styles.pill}>{m.type}</span> : null}</span>
      <span className={styles.name}>
        <span className={styles.part}>{m.partNumber}</span>
        {meta ? <span className={styles.meta}>{meta}</span> : null}
      </span>
      <span className={styles.price}>{m.price || ''}</span>
      {m.sourceUrl ? <TextButton small href={m.sourceUrl} target="_blank" rel="noreferrer" aria-label={`Buy ${m.partNumber}`} className={styles.buy}>Buy</TextButton> : <span />}
    </li>
  );
}

/** The first rows, and the rest behind one quiet toggle. */
function Rows({ items, label, moreTitle }) {
  const [all, setAll] = useState(false);
  const rest = items.length - SHOWN;
  return (
    <>
      <ul className={styles.rows} aria-label={label}>{(all ? items : items.slice(0, SHOWN)).map((m) => <Row key={m.partNumber} m={m} />)}</ul>
      {rest > 0 ? (
        <button type="button" className={styles.more} aria-expanded={all} onClick={() => setAll((v) => !v)}>
          {all ? 'Show fewer' : `${moreTitle} (${rest})`}
        </button>
      ) : null}
    </>
  );
}

/**
 * The SFP modules for one switch.
 *   vendor, model  - the switch's make and model ('' when not read)
 *   slotType       - 'SFP' | 'SFP+' | 'SFP28' | 'QSFP+' | 'QSFP28' | 'QSFP-DD' | null
 *   total, free    - the SFP cages the photo saw, and of those the ones not plugged in
 */
export function SfpModules({ vendor, model, slotType, total, free }) {
  if (!vendor || !model) return <Grey m0 className={styles.quiet}>Enter the make and model to see which modules fit.</Grey>;
  if (!slotType) return <Grey m0 className={styles.quiet}>No SFP cages on this switch.</Grey>;
  const slot = SFP_SLOT_TYPES[slotType] || { name: slotType, speed: null };
  const key = String(slotType).toUpperCase();
  const modules = CURATED_CATALOG[key] || [];
  const cables = CURATED_CABLES[key] || [];
  const cages = total > 0 ? `${total} ${total === 1 ? 'cage' : 'cages'}, ${free ?? 0} free` : null;
  return (
    <div className={styles.sfp}>
      <p className={styles.slot}>
        <span className={styles.slotPill}>{slot.name}</span>
        {slot.speed ? <span className={styles.slotSpeed}>{slot.speed}</span> : null}
        {cages ? <span className={styles.slotCount}>{cages}</span> : null}
      </p>
      {modules.length ? <Rows items={modules} label="Modules that fit" moreTitle="More modules" /> : <Grey m0 className={styles.quiet}>No modules are listed for {slot.name} yet.</Grey>}
      {cables.length ? (
        <>
          <p className={styles.subLabel}>Pre-terminated cables</p>
          <Rows items={cables} label="Pre-terminated cables" moreTitle="More cables" />
        </>
      ) : null}
    </div>
  );
}

export default SfpModules;
