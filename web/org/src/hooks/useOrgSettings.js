import { useCallback, useEffect, useMemo, useState } from 'react';
import { useAuth } from '../AuthContext.jsx';
import { apiUrl, authFetch } from '../utils/api';

/**
 * Everything the first-run flow and the settings view read and write, held
 * once. The state lives on the server (/api/setup): every control saves as
 * it goes, the write's answer replaces what is on screen, and the session's
 * user record is re-read after each one because user.setup on /api/auth/me
 * is what the route gate reads, and the gate has to lift without a sign-out.
 *
 * Marks are the quiet "Saving / Saved / Not saved" beside a section, keyed
 * by section, and by section and site for the per-site ones. The older
 * routes and names here still say datacentre; a datacentre is a site.
 *
 * The owner has no organization of their own and sees every Site on the
 * platform, so for the owner the page works on one organization at a time:
 * the first active one unless another is picked.
 */

export const EMPTY_PROFILE = { contacts: [], vendors: [], conventions: {}, facility: {} };
export const EMPTY_ORG = { name: '', slug: '', short_code: '', timezone: '', country: '', primary_contact_name: '', primary_contact_email: '' };

const NONE = [];
const obj = (v) => (v && typeof v === 'object' && !Array.isArray(v) ? v : {});
const arr = (v) => (Array.isArray(v) ? v : []);

async function call(method, path, body) {
  const r = await authFetch(apiUrl(path), {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) { const e = new Error(d.error || `Request failed (${r.status})`); e.status = r.status; throw e; }
  return d;
}
const getJSON = (p) => call('GET', p);
const postJSON = (p, b) => call('POST', p, b);
const putJSON = (p, b) => call('PUT', p, b);
const patchJSON = (p, b) => call('PATCH', p, b);
const delJSON = (p) => call('DELETE', p);

/* The spaces tree flattened for a list. Only roots are made here (parent_id
   stays null); anything nested that arrived by import is shown indented. */
function flatten(tree, depth = 0, out = []) {
  for (const s of tree || []) { out.push({ ...s, depth }); if (s.children?.length) flatten(s.children, depth + 1, out); }
  return out;
}
function normProfile(p) {
  const x = obj(p);
  /* `facility` is newer than the rest of the section list: a server that
     predates it answers without one, and an empty object is the honest
     "nothing written" reading. */
  return { contacts: arr(x.contacts), vendors: arr(x.vendors), conventions: obj(x.conventions), facility: obj(x.facility) };
}
/** GET /api/setup/:siteId, as one record per datacentre. */
export function toDc(s) {
  return {
    id: s.tenant.id, name: s.tenant.name, slug: s.tenant.slug || null, organization_id: s.tenant.organization_id ?? null,
    datacentre: s.datacentre || {}, spaces: flatten(s.spaces), approver: s.approver || null, rules: s.rules || null,
    completeness: s.completeness || null, profile: normProfile(s.profile),
  };
}
function orgOf(r, seed) {
  const p = r && typeof r === 'object' && r.profile && typeof r.profile === 'object' ? r.profile : obj(r);
  const out = { ...EMPTY_ORG, ...seed };
  Object.keys(EMPTY_ORG).forEach((k) => { if (p[k] != null && p[k] !== '') out[k] = p[k]; });
  return out;
}
const sectionResult = (r, body) => (r?.data !== undefined ? r.data : body);
/* The fields of PUT /api/setup/org/:id/profile this page writes. The name
   lives on the organization record and the short code is derived by the
   server, so neither is sent; what an older setup saved in the fields that
   were taken out (website, phone, industry, logo) is left as it is. */
const ORG_FIELDS = ['timezone', 'country', 'primary_contact_name', 'primary_contact_email'];
function orgBody(next) {
  const body = {};
  ORG_FIELDS.forEach((k) => { const v = next[k]; if (v !== undefined) body[k] = v === '' ? null : v; });
  return body;
}

export function useOrgSettings() {
  const { user, refreshUser } = useAuth();
  const isOwner = user?.role === 'owner';
  const ownOrgId = user?.organization_id ?? null;

  const [orgs, setOrgs] = useState(null);            // owner only: every active organization
  const [pickedOrg, setPickedOrg] = useState(null);  // owner only: the one in play
  const [dcs, setDcs] = useState(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState(null);
  const [orgProfile, setOrgProfile] = useState(null);
  const [orgErr, setOrgErr] = useState(null);
  const [marks, setMarks] = useState({});
  const [members, setMembers] = useState(undefined);
  const [tick, setTick] = useState(0);

  const orgId = isOwner ? pickedOrg : ownOrgId;

  useEffect(() => {
    let gone = false;
    setLoading(true); setErr(null);
    (async () => {
      try {
        const [st, ownerR] = await Promise.all([
          getJSON('/api/setup/state'),
          isOwner && orgs === null ? getJSON('/api/dashboard/owner').catch(() => null) : null,
        ]);
        const list = Array.isArray(st.tenants) ? st.tenants : [];
        let snaps = await Promise.all(list.map(async (t) => {
          try { return toDc(await getJSON(`/api/setup/${t.id}`)); }
          catch (e) { return { id: t.id, name: t.name, slug: null, organization_id: null, datacentre: {}, spaces: [], approver: null, rules: null, completeness: t.completeness || null, profile: normProfile(null), loadError: e }; }
        }));
        if (gone) return;
        let inPlay = orgId;
        if (isOwner) {
          let all = orgs;
          if (ownerR) {
            all = (ownerR.organizations || []).filter((o) => !o.status || o.status === 'active').map((o) => ({ id: Number(o.id), name: o.name }));
            setOrgs(all);
          }
          all = all || [];
          if (inPlay == null) {
            /* The organization of the first Site, else the first organization. */
            inPlay = snaps.find((d) => d.organization_id != null)?.organization_id ?? all[0]?.id ?? null;
            setPickedOrg(inPlay);
          }
          snaps = snaps.filter((d) => Number(d.organization_id) === Number(inPlay));
        }
        setDcs(snaps);
        const seed = { name: isOwner ? (orgs || []).find((o) => o.id === inPlay)?.name || '' : (user?.organization?.name || ''), slug: user?.organization?.slug || '' };
        if (inPlay != null) {
          try { const r = await getJSON(`/api/setup/org/${inPlay}/profile`); if (!gone) { setOrgErr(null); setOrgProfile(orgOf(r, seed)); } }
          catch (e) { if (!gone) { setOrgErr(e.status === 404 ? null : e); setOrgProfile((o) => o || { ...EMPTY_ORG, ...seed }); } }
        } else {
          setOrgProfile({ ...EMPTY_ORG, ...seed });
        }
      } catch (e) { if (!gone) setErr(e); }
      finally { if (!gone) setLoading(false); }
    })();
    return () => { gone = true; };
  }, [orgId, isOwner, tick]); // eslint-disable-line react-hooks/exhaustive-deps
  const refresh = useCallback(() => setTick((t) => t + 1), []);
  const pickOrg = useCallback((id) => { setPickedOrg(id == null ? null : Number(id)); setDcs(null); setOrgProfile(null); setMembers(undefined); }, []);

  const mark = useCallback((key, state, error) => setMarks((m) => ({ ...m, [key]: { state, at: Date.now(), error: error || null } })), []);
  /* One write, marked: saving while it runs, saved when it answers, the
     error kept beside the mark when it fails. Returns the answer, or null. */
  const run = useCallback(async (key, fn) => {
    mark(key, 'saving');
    try { const r = await fn(); mark(key, 'saved'); return r; }
    catch (e) { mark(key, 'failed', e); return null; }
  }, [mark]);

  const patch = useCallback((id, fn) => setDcs((prev) => (prev || []).map((d) => (d.id === id ? fn(d) : d))), []);
  /* A write for one datacentre: the record it answered with goes into the
     datacentre, the completeness it answered with goes beside it, and the
     session is re-read so the gate can lift. */
  const write = useCallback(async (dc, fn, apply) => {
    const r = await fn();
    patch(dc.id, (d) => { const n = apply ? apply(d, r) : d; return r?.completeness ? { ...n, completeness: r.completeness } : n; });
    refreshUser();
    return r;
  }, [patch, refreshUser]);

  const saveOrg = useCallback((next) => run('org', async () => {
    if (orgId == null) throw new Error('No organization to save to');
    const merged = { ...EMPTY_ORG, ...(orgProfile || {}), ...next };
    setOrgProfile(merged);
    const body = orgBody(next);
    let r = null;
    if (Object.keys(body).length) r = await putJSON(`/api/setup/org/${orgId}/profile`, body);
    if (r && typeof r === 'object') setOrgProfile((o) => orgOf(r, o || merged));
    refreshUser();
    return r || { ok: true };
  }), [orgId, orgProfile, run, refreshUser]);

  /* A datacentre is a Site: made through the same route the console uses,
     in the organization in play, then read back as a snapshot. */
  const addDatacentre = useCallback((name) => run('datacentres', async () => {
    if (orgId == null) throw new Error('Pick an organization first');
    const r = await postJSON(`/api/orgs/${orgId}/sites`, { name });
    const id = r?.site?.id ?? r?.tenant?.id ?? r?.id;
    const snap = toDc(await getJSON(`/api/setup/${id}`));
    setDcs((d) => [...(d || []), snap]);
    refreshUser();
    return snap;
  }), [orgId, run, refreshUser]);

  /* Rename a site. The name is the organization's, not the setup snapshot's,
     so it goes to the same route the console uses and the snapshot is read
     back rather than guessed. */
  const renameDatacentre = useCallback((dc, name) => run(`datacentres:${dc.id}`, async () => {
    if (orgId == null) throw new Error('Pick an organization first');
    await patchJSON(`/api/orgs/${orgId}/sites/${dc.id}`, { name });
    const snap = toDc(await getJSON(`/api/setup/${dc.id}`));
    setDcs((d) => (d || []).map((x) => (x.id === dc.id ? snap : x)));
    refreshUser();
    return snap;
  }), [orgId, run, refreshUser]);

  /* Remove a site. The server refuses one that still holds people, racks,
     spaces, scans or invites, and says which - that message is what the
     screen shows, so nothing is guessed here. */
  const removeDatacentre = useCallback((dc) => run(`datacentres:${dc.id}`, async () => {
    if (orgId == null) throw new Error('Pick an organization first');
    await delJSON(`/api/orgs/${orgId}/sites/${dc.id}`);
    setDcs((d) => (d || []).filter((x) => x.id !== dc.id));
    refreshUser();
    return { ok: true };
  }), [orgId, run, refreshUser]);

  const saveDatacentre = useCallback((dc, body) => run(`datacentres:${dc.id}`, () => write(dc, () => putJSON(`/api/setup/${dc.id}/datacentre`, body), (d, r) => ({ ...d, datacentre: r.datacentre || d.datacentre }))), [run, write]);
  const setApprover = useCallback((dc, body) => run(`people:${dc.id}`, () => write(dc, () => putJSON(`/api/setup/${dc.id}/approver`, body), (d, r) => ({ ...d, approver: r.approver || null }))), [run, write]);
  const acceptRules = useCallback((dc) => run(`rules:${dc.id}`, () => write(dc, () => putJSON(`/api/setup/${dc.id}/rules`, { accepted: true }), (d, r) => ({ ...d, rules: r.rules || d.rules }))), [run, write]);
  /* A profile section for one site, replaced whole. */
  const putSection = useCallback((dc, section, body) => write(dc, () => putJSON(`/api/setup/${dc.id}/profile/${section}`, body), (d, r) => ({ ...d, profile: { ...d.profile, [section]: sectionResult(r, body) } })), [write]);
  const saveSection = useCallback((dc, section, body) => run(`${section}:${dc.id}`, () => putSection(dc, section, body)), [run, putSection]);

  /* Members of the organization, to tell whether a SPOC already has an
     account. Loaded on demand, once. undefined = not asked, null = loading,
     [] = answered. */
  const loadMembers = useCallback(() => {
    if (orgId == null || members !== undefined) return;
    setMembers(null);
    getJSON(`/api/orgs/${orgId}/members`).then((r) => setMembers(r.members || [])).catch(() => setMembers([]));
  }, [orgId, members]);
  const invite = useCallback((dc, email) => postJSON(`/api/sites/${dc.id}/invites`, { email, role: 'member' }), []);

  /* The SPOC's account: a site manager on that site, made through the same
     route the console uses, then named as the site's approver so the gate's
     approver fact points at a person who can sign in. */
  const createSpocAccount = useCallback((dc, { username, email, password }) => run(`spoc:${dc.id}`, async () => {
    const made = await postJSON(`/api/sites/${dc.id}/members`, { username, email, password, role: 'site_manager' });
    setMembers(undefined);
    return write(dc, () => putJSON(`/api/setup/${dc.id}/approver`, { user_id: made.member.id }), (d, r) => ({ ...d, approver: r.approver || null }));
  }), [run, write]);

  const model = useMemo(() => ({ org: orgProfile, dcs: dcs || NONE }), [orgProfile, dcs]);

  return {
    user, orgId, isOwner, orgs: orgs || NONE, pickOrg,
    loading, error: err, orgError: orgErr, refresh,
    model, dcs: dcs || NONE, orgProfile, marks,
    saveOrg, addDatacentre, saveDatacentre, renameDatacentre, removeDatacentre, setApprover, createSpocAccount, acceptRules, saveSection,
    members, loadMembers, invite,
  };
}
