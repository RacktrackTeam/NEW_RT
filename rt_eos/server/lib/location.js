/**
 * Where a photo was taken, against where the organisation's Sites are.
 *
 * Indoors a phone knows its position to tens of metres at best, which is far
 * too coarse to tell one rack from the next and plenty to tell one datacentre
 * from another. So this answers one question and no other: was the photograph
 * taken at the Site the scan is filed under, at a different Site of the same
 * organisation, or nowhere near any of them?
 *
 * That matters because every other rung of the rack ladder looks for the rack
 * inside the scan's Site. A scan filed under Hyderabad DC1 but photographed in
 * Bengaluru DC1 can still read a label like "Rack 1" and match Hyderabad's
 * Rack 1, a different rack in a different city. The location is what can say
 * so.
 *
 * Pure: no database, no clock. The ladder passes the Sites in.
 */

/** How close counts as "at the Site", before the reading's own accuracy. */
const SITE_RADIUS_M = 300;
/** A reading worse than this says nothing useful about which building. */
const MAX_USEFUL_ACCURACY_M = 2000;

const EARTH_M = 6371008.8;
const rad = (d) => (d * Math.PI) / 180;

/** Great-circle distance in metres. */
function distanceM(a, b) {
  const dLat = rad(b.lat - a.lat);
  const dLng = rad(b.lng - a.lng);
  const h = Math.sin(dLat / 2) ** 2
    + Math.cos(rad(a.lat)) * Math.cos(rad(b.lat)) * Math.sin(dLng / 2) ** 2;
  return 2 * EARTH_M * Math.asin(Math.min(1, Math.sqrt(h)));
}

const num = (v) => (v === null || v === undefined || v === '' ? null : Number(v));
const valid = (p) => p && Number.isFinite(num(p.lat)) && Number.isFinite(num(p.lng))
  && Math.abs(num(p.lat)) <= 90 && Math.abs(num(p.lng)) <= 180;

/** A distance a person reads: "240 m", "3.2 km", "512 km". */
function said(m) {
  if (m < 1000) return `${Math.round(m / 10) * 10} m`;
  if (m < 10000) return `${(m / 1000).toFixed(1)} km`;
  return `${Math.round(m / 1000)} km`;
}

/**
 * The verdict.
 *
 *   capture  { lat, lng, accuracyM } from the phone, or null
 *   site     { id, name, lat, lng } the Site the scan is filed under
 *   others   the organisation's other Sites, same shape
 *
 * Returns { verdict, note, distanceM?, accuracyM?, site?, nearest? } where the
 * verdict is one of:
 *   'here'      taken at the scan's own Site
 *   'elsewhere' taken at another of the organisation's Sites
 *   'away'      taken nowhere near any Site that has a location
 *   'unknown'   nothing to go on - no reading, a useless one, or no Site location
 */
function judge(capture, site, others = []) {
  if (!valid(capture)) {
    return { verdict: 'unknown', note: 'The phone did not share where the photo was taken.' };
  }
  const accuracyM = Number.isFinite(num(capture.accuracyM)) ? num(capture.accuracyM) : null;
  if (accuracyM !== null && accuracyM > MAX_USEFUL_ACCURACY_M) {
    return { verdict: 'unknown', accuracyM,
      note: `The phone's location was only good to ${said(accuracyM)}, too rough to say which Site this is.` };
  }
  const radius = Math.max(SITE_RADIUS_M, 2 * (accuracyM || 0));
  const at = { lat: num(capture.lat), lng: num(capture.lng) };
  // The reading itself travels with the verdict. A person looking at a rack
  // asked where the photograph was taken and got a verdict with no position in
  // it; the two coordinates are what they meant.
  const took = { lat: at.lat, lng: at.lng, accuracyM };

  const here = valid(site) ? distanceM(at, { lat: num(site.lat), lng: num(site.lng) }) : null;
  if (here !== null && here <= radius) {
    return { verdict: 'here', distanceM: Math.round(here), accuracyM, at: took, site: site.name,
      note: `Taken at ${site.name}, ${said(here)} from its address.` };
  }

  const near = others.filter(valid)
    .map((o) => ({ ...o, d: distanceM(at, { lat: num(o.lat), lng: num(o.lng) }) }))
    .filter((o) => o.d <= radius)
    .sort((a, b) => a.d - b.d)[0];
  if (near) {
    return { verdict: 'elsewhere', distanceM: here !== null ? Math.round(here) : null, accuracyM, at: took,
      site: site && site.name, nearest: { id: near.id, name: near.name, distanceM: Math.round(near.d) },
      note: `Taken at ${near.name}, not ${site ? site.name : 'the Site this scan is filed under'}`
        + `${here !== null ? `, which is ${said(here)} away` : ''}. `
        + 'Check which Site this scan belongs to before trusting a match.' };
  }
  if (here === null) {
    return { verdict: 'unknown', accuracyM, at: took,
      note: `${site ? site.name : 'This Site'} has no location set, so where the photo was taken cannot be checked against it.` };
  }
  return { verdict: 'away', distanceM: Math.round(here), accuracyM, at: took, site: site.name,
    note: `Taken ${said(here)} from ${site.name}, and not at any other Site of this organisation.` };
}

module.exports = { judge, distanceM, said, SITE_RADIUS_M, MAX_USEFUL_ACCURACY_M };
