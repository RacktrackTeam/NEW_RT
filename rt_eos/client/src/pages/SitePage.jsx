import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useAuth } from '../AuthContext.jsx';
import { TopBar } from '../components/TopBar.jsx';
import { Page, Stick, Bar, Spacer } from '../components/Page.jsx';
import { Button, TextButton, Actions } from '../components/Button.jsx';
import { Field, SelectField } from '../components/Field.jsx';
import { ListRow } from '../components/ListRow.jsx';
import { SectionLabel, Grey, EmptyState, ErrorLine, Loading } from '../components/Text.jsx';
import { PagedList } from '../components/Ui.jsx';
import { Sheet } from '../components/Sheet.jsx';
import { useToast } from '../components/Toast.jsx';
import { RackThumb, Thumbs } from '../components/RackThumb.jsx';
import Avatar from '../components/Avatar.jsx';
import { getJSON, postJSON, patchJSON, deleteJSON, notBuilt, enc } from '../utils/http';
import { publicOrigin } from '../utils/api';
import { isAdmin, plural, roleWord, whenShort } from '../utils/format';
import { rackNameOf, thumbOf } from '../utils/rack';
import { required, vEmail, vUsername, vPassword } from '../utils/orgSettings';
import styles from './SitePage.module.css';

/*
 * A site: its racks as pictures and the people who work there. The site
 * manager invites technicians from here and looks after their accounts
 * (behind each person's Options).
 *
 * Data: GET /api/sites/:siteId/overview (contract, new). Until the server
 * has it, the page is put together from /api/scan-sites (the site's name and
 * racks), /api/scans (their pictures) and /api/orgs/:orgId/members.
 */

const ROLE_OPTIONS = [
  { value: 'member', label: 'Technician' },
  { value: 'site_manager', label: 'Site manager' },
];

const isActive = (p) => p.active !== 0 && p.active !== false;
const isPending = (p) => !!(p.pending || p.invited || p.status === 'invited' || p.status === 'pending');
const nameOf = (p) => p.name || p.username || p.email || 'Someone';

function personMeta(p) {
  const role = roleWord(p.role);
  let what;
  if (isPending(p)) what = 'Invite sent, not joined yet';
  else if (p.last_scan || p.lastScan) what = `Last scan ${whenShort(p.last_scan || p.lastScan)}`;
  else what = 'No scans yet';
  return `${role}, ${what}${isActive(p) ? '' : ', Deactivated'}`;
}

/* What the person looking may do to someone on the list. */
export function allowed(viewer, p) {
  const none = { edit: false, identity: false, role: false, site: false, active: false, remove: false };
  if (!viewer || !p || Number(p.id) === Number(viewer.id) || p.role === 'owner' || isPending(p)) return none;
  if (viewer.role === 'site_manager') {
    if (p.role !== 'member') return none;
    // The server lets a site manager change a technician's password and switch them on or off.
    return { edit: true, identity: false, role: false, site: false, active: true, remove: true };
  }
  if (p.role === 'org_owner' && viewer.role !== 'owner') return none;                 // only the platform owner edits an organization owner
  if (viewer.role === 'org_admin' && p.role === 'org_admin') return none;
  if (!isAdmin(viewer.role)) return none;
  const top = p.role === 'org_admin' || p.role === 'org_owner';
  return { edit: true, identity: true, role: !top, site: !top, active: true, remove: true };
}

function rackFromOverview(r) {
  const scanId = r.scanId || r.scan_id || null;
  const check = r.status === 'check' || r.check === true || r.needsLook === true;
  const problems = Number(r.problems ?? r.needLook ?? 0) || 0;
  let meta;
  if (!scanId) meta = 'From the sheet';
  else if (check && problems) meta = `${problems} need a look`;
  else if (r.takenAt || r.taken_at) meta = `Read ${whenShort(r.takenAt || r.taken_at)}`;
  return {
    key: scanId || r.rackLabel || r.name,
    name: rackNameOf(r),
    scanId,
    thumb: scanId ? thumbOf({ ...r, scanId }) : null,
    meta,
    status: scanId ? (check ? 'check' : (r.status && r.status !== 'check' ? r.status : null)) : null,
  };
}

async function membersOf(orgId, siteId) {
  if (orgId == null) return [];
  const r = await getJSON(`/api/orgs/${enc(orgId)}/members`);
  return (r?.members || []).filter((m) => Number(m.tenant_id) === Number(siteId));
}

/* One read for the whole screen: the overview, or the old routes put together. */
export async function loadSite(siteId, user) {
  let ov = null;
  try {
    ov = await getJSON(`/api/sites/${enc(siteId)}/overview`);
  } catch (e) {
    if (!notBuilt(e)) throw e;
  }
  if (ov) {
    const site = ov.site || { id: siteId };
    const orgId = site.organization_id ?? site.organizationId ?? user?.organization_id ?? null;
    const members = await membersOf(orgId, siteId).catch(() => []);
    const byId = new Map(members.map((m) => [Number(m.id), m]));
    const people = (ov.people || []).map((p) => ({ ...(byId.get(Number(p.id)) || {}), ...p }));
    return {
      site: { id: site.id ?? siteId, name: site.name || 'Site' },
      orgId,
      orgName: site.organization?.name || ov.organization?.name || user?.organization?.name || '',
      racks: (ov.racks || []).map(rackFromOverview),
      people,
    };
  }
  // Not built yet: the site and its racks from the scan list, the people from the members list.
  const ss = await getJSON('/api/scan-sites');
  let site = (ss?.sites || []).find((x) => String(x.id) === String(siteId)) || null;
  let orgId = user?.organization_id ?? null;
  if (!site || orgId == null) {
    try {
      const snap = await getJSON(`/api/setup/${enc(siteId)}`);
      if (!site && snap?.tenant) site = { id: snap.tenant.id, name: snap.tenant.name, racks: [] };
      if (orgId == null) orgId = snap?.tenant?.organization_id ?? null;
    } catch { /* the owner may not reach this; the site list decides below */ }
  }
  if (!site) {
    const e = new Error('This site could not be found. Pick another site.');
    e.status = 404;
    e.noSite = true;
    throw e;
  }
  const [scans, people] = await Promise.all([
    getJSON('/api/scans').then((r) => r?.scans || []).catch(() => []),
    membersOf(orgId, siteId),
  ]);
  const byRack = new Map(scans.map((x) => [x.rackId, x]));
  const racks = (site.racks || []).map((r) => {
    const scan = r.rackId ? byRack.get(r.rackId) : null;
    return rackFromOverview({
      scanId: r.rackId || null,
      rackLabel: r.name || scan?.rackName || null,
      thumbUrl: scan?.thumbUrl || scan?.image || null,
      takenAt: scan?.timestamp || null,
    });
  });
  return { site: { id: site.id, name: site.name }, orgId, orgName: user?.organization?.name || '', racks, people };
}

/* ---- The sheets ---------------------------------------------------- */

function InviteSheet({ open, onClose, siteId, admin, onCreated }) {
  const toast = useToast();
  const [mode, setMode] = useState('invite');
  const [f, setF] = useState({ email: '', role: 'member', username: '', password: '' });
  const [show, setShow] = useState(false);
  const [errs, setErrs] = useState({});
  const [busy, setBusy] = useState(false);
  const [fail, setFail] = useState(null);
  const [link, setLink] = useState(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!open) return;
    setMode('invite'); setF({ email: '', role: 'member', username: '', password: '' });
    setErrs({}); setFail(null); setLink(null); setCopied(false); setShow(false);
  }, [open]);

  const set = (k) => (e) => { const v = e.target.value; setF((x) => ({ ...x, [k]: v })); setErrs((x) => ({ ...x, [k]: null })); };

  const makeLink = async (e) => {
    e.preventDefault();
    const email = f.email.trim();
    const bad = { email: required(email, 'Enter their email') || vEmail(email) };
    setErrs(bad);
    if (bad.email) return;
    setBusy(true); setFail(null);
    try {
      const d = await postJSON(`/api/sites/${enc(siteId)}/invites`, { email: email.toLowerCase(), role: admin ? f.role : 'member' });
      const path = d?.invite?.path || (d?.invite?.code ? `/invite/${d.invite.code}` : null);
      if (!path) throw new Error('The invite link could not be made. Try again.');
      setLink(publicOrigin() + path);
    } catch (x) { setFail(x.message); }
    setBusy(false);
  };

  const makeAccount = async (e) => {
    e.preventDefault();
    const username = f.username.trim();
    const email = f.email.trim();
    const bad = {
      username: required(username, 'Enter a username') || vUsername(username),
      email: required(email, 'Enter their email') || vEmail(email),
      password: required(f.password, 'Enter a temporary password') || vPassword(f.password),
    };
    setErrs(bad);
    if (Object.values(bad).some(Boolean)) return;
    setBusy(true); setFail(null);
    try {
      await postJSON(`/api/sites/${enc(siteId)}/members`, { username, email: email.toLowerCase(), password: f.password, role: admin ? f.role : 'member' });
      toast(`Account made for ${username}.`);
      onCreated();
      onClose();
    } catch (x) { setFail(x.message); }
    setBusy(false);
  };

  const copy = async () => {
    try { await navigator.clipboard.writeText(link); setCopied(true); } catch { setFail('The link could not be copied. Select it and copy it by hand.'); }
  };

  const roleField = admin
    ? <SelectField label="Role" value={f.role} onChange={set('role')} options={ROLE_OPTIONS} />
    : null;

  let body;
  if (link) {
    body = (
      <>
        <Grey>Send this link to {f.email.trim()}. It works once and ends in 7 days.</Grey>
        <Field label="Invite link" value={link} readOnly onFocus={(e) => e.target.select()} />
        <ErrorLine>{fail}</ErrorLine>
        <Actions>
          <Button size="auto" onClick={copy}>{copied ? 'Copied' : 'Copy link'}</Button>
          <TextButton onClick={onClose}>Done</TextButton>
        </Actions>
      </>
    );
  } else if (mode === 'invite') {
    body = (
      <form onSubmit={makeLink} noValidate>
        <Field label="Email" required type="email" inputMode="email" autoComplete="off" value={f.email} onChange={set('email')} error={errs.email} />
        {roleField}
        <Grey>They open the link and choose their own username and password.</Grey>
        <ErrorLine>{fail}</ErrorLine>
        <Actions>
          <Button type="submit" size="auto" busy={busy}>Create invite link</Button>
        </Actions>
        <p className={styles.or}><TextButton onClick={() => { setMode('create'); setErrs({}); setFail(null); }}>Or create the account now</TextButton></p>
      </form>
    );
  } else {
    body = (
      <form onSubmit={makeAccount} noValidate>
        <Field label="Username" required autoComplete="off" spellCheck="false" value={f.username} onChange={set('username')} error={errs.username} />
        <Field label="Email" required type="email" inputMode="email" autoComplete="off" value={f.email} onChange={set('email')} error={errs.email} />
        <Field
          label="Temporary password"
          required
          type={show ? 'text' : 'password'}
          autoComplete="new-password"
          value={f.password}
          onChange={set('password')}
          error={errs.password}
          hint="8 or more, with a capital, a small letter, a digit and a symbol."
          suffix={<TextButton small onClick={() => setShow((v) => !v)}>{show ? 'Hide' : 'Show'}</TextButton>}
        />
        {roleField}
        <ErrorLine>{fail}</ErrorLine>
        <Actions>
          <Button type="submit" size="auto" busy={busy}>Create account</Button>
          <TextButton onClick={() => { setMode('invite'); setErrs({}); setFail(null); }}>Send an invite instead</TextButton>
        </Actions>
      </form>
    );
  }
  return (
    <Sheet open={open} onClose={onClose} title={link ? 'Invite link ready' : mode === 'invite' ? 'Invite a technician' : 'Create an account'}>
      {body}
    </Sheet>
  );
}

function PersonSheet({ person, onClose, can, orgId, sites, onChanged }) {
  const toast = useToast();
  const [view, setView] = useState('menu');
  const [f, setF] = useState({});
  const [errs, setErrs] = useState({});
  const [busy, setBusy] = useState(false);
  const [fail, setFail] = useState(null);

  useEffect(() => {
    if (!person) return;
    setView('menu'); setErrs({}); setFail(null); setBusy(false);
    setF({ username: person.username || '', email: person.email || '', role: person.role === 'site_manager' ? 'site_manager' : 'member', siteId: person.tenant_id ? String(person.tenant_id) : '', password: '' });
  }, [person]);

  if (!person) return null;
  const who = nameOf(person);
  const active = isActive(person);
  const path = `/api/orgs/${enc(orgId)}/members/${enc(person.id)}`;
  const set = (k) => (e) => { const v = e.target.value; setF((x) => ({ ...x, [k]: v })); setErrs((x) => ({ ...x, [k]: null })); };

  const doRun = async (fn, done) => {
    setBusy(true); setFail(null);
    try { await fn(); toast(done); onChanged(); onClose(); } catch (x) { setFail(x.message); setBusy(false); }
  };

  const save = (e) => {
    e.preventDefault();
    const bad = {
      username: can.identity ? (required(f.username, 'Enter a username') || vUsername(f.username.trim())) : null,
      email: can.identity ? (required(f.email, 'Enter an email') || vEmail(f.email.trim())) : null,
      password: vPassword(f.password),
    };
    setErrs(bad);
    if (Object.values(bad).some(Boolean)) return;
    const body = {};
    if (can.identity) { body.username = f.username.trim(); body.email = f.email.trim().toLowerCase(); }
    if (can.role) body.role = f.role;
    if (can.site && f.siteId && String(f.siteId) !== String(person.tenant_id)) body.siteId = Number(f.siteId);
    if (f.password) body.password = f.password;
    if (!Object.keys(body).length) { setErrs({ password: 'Enter a new password, or close to keep it as it is.' }); return; }
    doRun(() => patchJSON(path, body), `${body.username || who} saved.`);
  };

  let content;
  if (view === 'edit') {
    content = (
      <form onSubmit={save} noValidate>
        {can.identity ? (
          <>
            <Field label="Username" required autoComplete="off" spellCheck="false" value={f.username} onChange={set('username')} error={errs.username} />
            <Field label="Email" required type="email" autoComplete="off" value={f.email} onChange={set('email')} error={errs.email} />
          </>
        ) : null}
        {can.role ? <SelectField label="Role" value={f.role} onChange={set('role')} options={ROLE_OPTIONS} /> : null}
        {can.site && sites.length > 1 ? (
          <SelectField label="Site" value={f.siteId} onChange={set('siteId')} options={sites.map((x) => ({ value: String(x.id), label: x.name }))} />
        ) : null}
        <Field label="New password" type="password" autoComplete="new-password" value={f.password} onChange={set('password')} error={errs.password} hint="Leave it empty to keep the password they have." />
        <ErrorLine>{fail}</ErrorLine>
        <Actions>
          <Button type="submit" size="auto" busy={busy}>Save</Button>
          <TextButton onClick={() => setView('menu')}>Cancel</TextButton>
        </Actions>
      </form>
    );
  } else if (view === 'remove') {
    content = (
      <>
        <p className={styles.sentence}>Remove {who}? The account is deleted. Their past scans stay.</p>
        <ErrorLine>{fail}</ErrorLine>
        <Actions>
          <Button size="auto" variant="danger" busy={busy} onClick={() => doRun(() => deleteJSON(path), `${who} removed.`)}>Remove {who}</Button>
          <TextButton onClick={() => setView('menu')}>Keep them</TextButton>
        </Actions>
      </>
    );
  } else {
    content = (
      <>
        <Grey m0>{personMeta(person)}</Grey>
        <div className={styles.menu}>
          {can.edit ? <ListRow compact title="Edit" onClick={() => setView('edit')} /> : null}
          {can.active ? (
            <ListRow
              compact
              title={active ? 'Deactivate' : 'Reactivate'}
              meta={active ? 'They cannot sign in until you reactivate them.' : 'They can sign in again.'}
              onClick={() => doRun(() => patchJSON(path, { active: !active }), active ? `${who} deactivated.` : `${who} reactivated.`)}
            />
          ) : null}
          {can.remove ? <ListRow compact title="Remove" titleClassName={styles.danger} onClick={() => setView('remove')} /> : null}
        </div>
        <ErrorLine>{fail}</ErrorLine>
      </>
    );
  }
  return (
    <Sheet open={!!person} onClose={onClose} title={view === 'edit' ? `Edit ${who}` : who}>
      {content}
    </Sheet>
  );
}

/* ---- The screen ------------------------------------------------------ */

export default function SitePage() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const params = useParams();
  const admin = isAdmin(user?.role);
  const siteId = params.siteId || (user?.tenant_id != null ? String(user.tenant_id) : null);
  const needPicker = admin && user?.tenant_id == null;

  const [sites, setSites] = useState([]);
  const [state, setState] = useState({ data: null, error: null, loading: !!siteId });
  const [tick, setTick] = useState(0);
  const [inviting, setInviting] = useState(false);
  const [person, setPerson] = useState(null);
  const reload = useCallback(() => setTick((t) => t + 1), []);

  /* The sites an admin can pick from, and move people between. */
  useEffect(() => {
    if (!admin) return undefined;
    let gone = false;
    (async () => {
      let list = [];
      try { list = (await getJSON('/api/scan-sites'))?.sites || []; } catch { list = []; }
      if (!list.length) {
        try { list = (await getJSON('/api/setup/state'))?.tenants || []; } catch { list = []; }
      }
      if (!gone) setSites(list.map((x) => ({ id: x.id, name: x.name })));
    })();
    return () => { gone = true; };
  }, [admin]);

  useEffect(() => {
    if (!siteId) { setState({ data: null, error: null, loading: false }); return undefined; }
    let gone = false;
    setState((st) => ({ ...st, loading: true, error: null }));
    loadSite(siteId, user)
      .then((data) => { if (!gone) setState({ data, error: null, loading: false }); })
      .catch((error) => { if (!gone) setState({ data: null, error, loading: false }); });
    return () => { gone = true; };
  }, [siteId, tick]); // eslint-disable-line react-hooks/exhaustive-deps

  const { data, error, loading } = state;
  const people = useMemo(() => data?.people || [], [data]);
  const racks = data?.racks || [];

  const picker = needPicker && sites.length ? (
    <SelectField
      label="Site"
      className={styles.picker}
      value={siteId || ''}
      placeholder={siteId ? undefined : 'Pick a site'}
      options={sites.map((x) => ({ value: String(x.id), label: x.name }))}
      onChange={(e) => { if (e.target.value) navigate(`/site/${enc(e.target.value)}`, { replace: true }); }}
    />
  ) : null;

  let body;
  if (!siteId) {
    body = needPicker
      ? <EmptyState text={sites.length ? 'Pick a site to see its racks and people.' : 'There is no site yet. Add one in Organization settings.'} action={sites.length ? null : <TextButton to="/settings">Organization settings</TextButton>} />
      : <EmptyState text="Your account is not linked to a site. Ask your organization admin to add you to one." />;
  } else if (loading && !data) {
    body = <Loading />;
  } else if (error) {
    body = error.noSite
      ? <EmptyState text={error.message} />
      : (
        <>
          <ErrorLine>{`The site could not be loaded. ${error.message}`}</ErrorLine>
          <TextButton onClick={reload}>Try again</TextButton>
        </>
      );
  } else if (data) {
    const sub = `${data.orgName ? `${data.orgName}. ` : ''}${plural(people.length, 'person', 'people')}, ${plural(racks.length, 'rack')}.`;
    const racksBlock = (
      <section aria-label="Racks">
        <SectionLabel>Racks</SectionLabel>
        {racks.length ? (
          <Thumbs className={styles.thumbs}>
            {racks.map((r) => (
              <RackThumb
                key={r.key}
                name={r.name}
                meta={r.meta}
                status={r.status}
                none={!r.scanId}
                src={r.thumb}
                to={r.scanId ? `/results/${enc(r.scanId)}` : undefined}
              />
            ))}
          </Thumbs>
        ) : <EmptyState text="No rack has been read at this site yet." />}
      </section>
    );
    const peopleBlock = (
      <section aria-label="People">
        <SectionLabel>People</SectionLabel>
        {people.length ? (
          <PagedList as="div" items={people} step={6}>
            {(p) => {
              const can = allowed(user, p);
              const any = can.edit || can.active || can.remove;
              return (
                <ListRow
                  key={p.id ?? p.email}
                  lead={<Avatar user={p} />}
                  title={nameOf(p)}
                  meta={personMeta(p)}
                  right={any ? (
                    <TextButton small onClick={() => setPerson(p)} aria-label={`Options for ${nameOf(p)}`}>Options</TextButton>
                  ) : null}
                />
              );
            }}
          </PagedList>
        ) : <EmptyState text="Nobody works at this site yet. Invite a technician to start." />}
      </section>
    );
    body = (
      <>
        <Bar className={styles.head}>
          {sub ? <Grey m0>{sub}</Grey> : null}
          <Spacer />
          <span className={styles.lapOnly}>
            <Button size="auto" onClick={() => setInviting(true)}>Invite a technician</Button>
          </span>
        </Bar>
        <div className={styles.stack}>
          {peopleBlock}
          {racksBlock}
        </div>
        <Stick className={styles.phoneOnly}>
          <Button onClick={() => setInviting(true)}>Invite a technician</Button>
        </Stick>
        <InviteSheet open={inviting} onClose={() => setInviting(false)} siteId={data.site.id} admin={admin} onCreated={reload} />
        <PersonSheet person={person} onClose={() => setPerson(null)} can={allowed(user, person)} orgId={data.orgId} sites={sites} onChanged={reload} />
      </>
    );
  }

  return (
    <>
      <TopBar title={data?.site?.name || 'Site and people'} backTo="/you" />
      <Page stick={!!data}>
        {picker}
        {body}
      </Page>
    </>
  );
}
