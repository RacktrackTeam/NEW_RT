import { useCallback, useEffect, useState } from 'react';
import { apiUrl, authFetch } from '../utils/api';
import { getItem, setItem } from '../utils/safeStorage';
import { useAuth } from '../AuthContext.jsx';

// Which Site a scan is for. The server says which Sites this person may scan
// for (one for a technician, every Site of the organisation for its admins)
// and the choice goes with every upload, so the rack is matched inside that
// Site and nowhere else. The phone is never asked where it is.
//
// One hook because more than one screen uploads a scan (Scan a rack, Scan two
// racks) and they have to agree: the same list, the same remembered choice,
// the same rule for when a scan waits. Each draws the one SitePicker over it.
//
// A server without the list, a failed request and an empty list all end the
// same way: no picker, no `siteId`, and the scan goes as it always did.

// The Site the technician last scanned for, remembered per person: two people
// sharing a phone do not share a site.
const SITE_KEY_PREFIX = 'rt.scan.site.';

// What the server's "Site not found" means to the person holding the phone.
export const SITE_REFUSED = 'That site is not one you can scan for. Choose another.';
export const isSiteRefused = (res, data) => res.status === 404 && data?.error === 'Site not found';

export function useScanSite() {
  const { user } = useAuth();
  const siteKey = user?.id != null ? SITE_KEY_PREFIX + user.id : null;
  const [sites, setSites] = useState([]);
  const [siteId, setSiteId] = useState('');
  // True once the list has answered, whatever it said. Until then nobody can
  // tell whether a site will have to be chosen - the guided tour waits on it.
  const [asked, setAsked] = useState(false);
  useEffect(() => {
    let cancelled = false;
    authFetch(apiUrl('/api/scan-sites'))
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (cancelled || !d || !Array.isArray(d.sites)) return;
        const list = d.sites.filter((s) => s && s.id != null);
        if (!list.length) return;
        const has = (id) => id != null && id !== '' && list.some((s) => String(s.id) === String(id));
        const stored = siteKey ? getItem(siteKey) : null;
        setSites(list);
        // One Site is the answer, whatever was remembered. Otherwise the last
        // choice if it is still on the list, else the server's suggestion.
        setSiteId(list.length === 1 ? String(list[0].id)
          : has(stored) ? String(stored)
            : has(d.preselect) ? String(d.preselect) : '');
      })
      .catch(() => { /* no picker - scanning is never blocked on it */ })
      .finally(() => { if (!cancelled) setAsked(true); });
    return () => { cancelled = true; };
  }, [siteKey]);
  const chooseSite = useCallback((id) => {
    setSiteId(id);
    if (siteKey) setItem(siteKey, id);
  }, [siteKey]);
  // Several Sites and none chosen is the one thing that holds a scan back.
  const needsSite = sites.length > 1 && !siteId;
  return { sites, siteId, chooseSite, needsSite, asked };
}
