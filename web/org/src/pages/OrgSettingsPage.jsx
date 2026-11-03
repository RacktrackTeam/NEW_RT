import { ORG_ONLY } from '../utils/orgOnly';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useAuth } from '../AuthContext.jsx';
import { TopBar } from '../components/TopBar.jsx';
import { Page, Two } from '../components/Page.jsx';
import { Button, TextButton, Actions, Links } from '../components/Button.jsx';
import { Field, SelectField } from '../components/Field.jsx';
import { ListRow } from '../components/ListRow.jsx';
import { SectionLabel, Grey, EmptyState, ErrorLine, Loading } from '../components/Text.jsx';
import { Disclosure } from '../components/Ui.jsx';
import { Switch } from '../components/Controls.jsx';
import { Sheet } from '../components/Sheet.jsx';
import { useToast } from '../components/Toast.jsx';
import { useOrgSettings } from '../hooks/useOrgSettings';
import { getJSON, postJSON, putJSON, patchJSON, deleteJSON, notBuilt, enc } from '../utils/http';
import { plural } from '../utils/format';
import { timezones, countries, browserTimezone, required, vEmail, vUsername, vPassword } from '../utils/orgSettings';
import styles from './OrgSettingsPage.module.css';

/*
 * Organization settings (board screen 16): the organization and its sites,
 * the pilot data controls, and for the owner every organization on the
 * platform. The first-run setup stays a popup stepper at /setup.
 */

const STATUS_WORD = { active: 'Active', pending: 'Waiting for approval', inactive: 'Deactivated', rejected: 'Rejected' };
const statusWord = (s) => STATUS_WORD[s] || (s ? String(s) : 'Active');

/* ---- Organization -------------------------------------------------- */

function OrgSection({ s, isOwner, onRenamed }) {
  const p = s.orgProfile || {};
  const [f, setF] = useState({ name: '', primary_contact_name: '', primary_contact_email: '' });
  const [errs, setErrs] = useState({});
  const [nameFail, setNameFail] = useState(null);
  useEffect(() => {
    setF({ name: p.name || '', primary_contact_name: p.primary_contact_name || '', primary_contact_email: p.primary_contact_email || '' });
    setErrs({});
  }, [s.orgId, p.name, p.primary_contact_name, p.primary_contact_email]);
  const fallbackTz = useMemo(() => browserTimezone(), []);
  const zones = useMemo(() => timezones(p.timezone, fallbackTz).map((z) => ({ value: z, label: z })), [p.timezone, fallbackTz]);
  const cs = useMemo(() => countries(p.country).map((c) => ({ value: c.code, label: c.name })), [p.country]);
  const set = (k) => (e) => { const v = e.target.value; setF((x) => ({ ...x, [k]: v })); setErrs((x) => ({ ...x, [k]: null })); };
  const orgMark = s.marks?.org;

  const saveName = async () => {
    const name = f.name.trim();
    if (name === (p.name || '')) return;
    const bad = required(name, 'Enter the organization name');
    setErrs((x) => ({ ...x, name: bad }));
    if (bad) return;
    setNameFail(null);
    try {
      await patchJSON(`/api/orgs/${enc(s.orgId)}`, { name });
      onRenamed(name);
    } catch (e) { setNameFail(e.message); }
  };
  const saveContact = (k) => {
    const v = f[k].trim();
    if (v === (p[k] || '')) return;
    if (k === 'primary_contact_email') {
      const bad = vEmail(v);
      setErrs((x) => ({ ...x, [k]: bad }));
      if (bad) return;
    }
    s.saveOrg({ [k]: v });
  };

  return (
    <Disclosure title="Organization" sub={[p.name, p.timezone].filter(Boolean).join(', ') || 'Name, time zone, contact, sites'}>
      {isOwner ? (
        <Field label="Name" required value={f.name} onChange={set('name')} onBlur={saveName} error={errs.name || nameFail} autoComplete="organization" />
      ) : (
        <Field label="Name" value={p.name || ''} readOnly hint="Only the platform owner (RackTrack) can rename the organization." />
      )}
      <SelectField
        label="Time zone"
        required
        value={p.timezone || ''}
        placeholder={p.timezone ? undefined : 'Pick a time zone'}
        options={zones}
        onChange={(e) => { if (e.target.value) s.saveOrg({ timezone: e.target.value }); }}
      />
      <SelectField
        label="Country"
        value={p.country || ''}
        placeholder="Pick a country"
        options={cs}
        onChange={(e) => s.saveOrg({ country: e.target.value })}
      />
      <Field label="Primary contact name" value={f.primary_contact_name} onChange={set('primary_contact_name')} onBlur={() => saveContact('primary_contact_name')} autoComplete="off" />
      <Field label="Primary contact email" type="email" inputMode="email" value={f.primary_contact_email} onChange={set('primary_contact_email')} onBlur={() => saveContact('primary_contact_email')} error={errs.primary_contact_email} autoComplete="off" />
      {orgMark?.state === 'failed' ? <ErrorLine>{`Not saved. ${orgMark.error?.message || 'Try again.'}`}</ErrorLine> : null}
      <SitesBlock s={s} />
    </Disclosure>
  );
}

function SitesBlock({ s }) {
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState('');
  const [err, setErr] = useState(null);
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState(false);
  const failText = failed ? (s.marks?.datacentres?.error?.message || 'The site could not be added. Try again.') : null;
  const names = s.dcs.map((d) => d.name).join(', ');
  const add = async (e) => {
    e.preventDefault();
    const v = name.trim();
    const bad = required(v, 'Enter the site name') || (v.length < 2 ? 'Use at least 2 letters' : null);
    setErr(bad);
    if (bad) return;
    setBusy(true); setFailed(false);
    const r = await s.addDatacentre(v);
    setBusy(false);
    if (r) { setAdding(false); setName(''); }
    else setFailed(true);
  };
  return (
    <>
      <ListRow className={styles.sitesRow} title="Sites" meta={names || 'No site yet'} right={<TextButton to="/setup?step=sites">Edit</TextButton>} />
      {s.dcs.map((d) => (
        <ListRow key={d.id} compact title={d.name} meta={d.datacentre?.address || 'No address yet'} to={`/site/${enc(d.id)}`} />
      ))}
      <Links className={styles.links}>
        <TextButton onClick={() => { setAdding(true); setErr(null); setFailed(false); setName(''); }}>Add a site</TextButton>
      </Links>
      <Sheet open={adding} onClose={() => setAdding(false)} title="Add a site">
        <form onSubmit={add} noValidate>
          <Field label="Name" required value={name} onChange={(e) => { setName(e.target.value); setErr(null); setFailed(false); }} error={err || failText} autoComplete="off" />
          <Actions>
            <Button type="submit" size="auto" busy={busy}>Add site</Button>
            <TextButton onClick={() => setAdding(false)}>Cancel</TextButton>
          </Actions>
        </form>
      </Sheet>
    </>
  );
}

/* ---- Pilot data ---------------------------------------------------- */

function PilotData({ orgId, orgName }) {
  const toast = useToast();
  const [st, setSt] = useState({ data: null, error: null, loading: true });
  const [days, setDays] = useState('');
  const [dayErr, setDayErr] = useState(null);
  const [saveErr, setSaveErr] = useState(null);
  const [del, setDel] = useState(false);
  const [typed, setTyped] = useState('');
  const [busy, setBusy] = useState(false);
  const [delErr, setDelErr] = useState(null);

  useEffect(() => {
    if (orgId == null) { setSt({ data: null, error: null, loading: false }); return undefined; }
    let gone = false;
    setSt({ data: null, error: null, loading: true });
    getJSON(`/api/orgs/${enc(orgId)}/data-settings`)
      .then((d) => {
        if (gone) return;
        const data = {
          neverTrain: !!(d?.neverTrain ?? d?.never_train),
          retentionDays: d?.retentionDays ?? d?.retention_days ?? null,
          onlineLookups: (d?.onlineLookups ?? d?.online_lookups ?? true) !== false,
        };
        setSt({ data, error: null, loading: false });
        setDays(data.retentionDays == null ? '' : String(data.retentionDays));
      })
      .catch((error) => { if (!gone) setSt({ data: null, error, loading: false }); });
    return () => { gone = true; };
  }, [orgId]);

  const put = async (next) => {
    setSaveErr(null);
    const prev = st.data;
    setSt((x) => ({ ...x, data: next }));
    try {
      await putJSON(`/api/orgs/${enc(orgId)}/data-settings`, next);
    } catch (e) {
      setSt((x) => ({ ...x, data: prev }));
      setSaveErr(`Not saved. ${e.message}`);
    }
  };
  const saveDays = () => {
    const v = days.trim();
    if (v === '') {
      setDayErr(null);
      if (st.data.retentionDays != null) put({ ...st.data, retentionDays: null });
      return;
    }
    const n = Number(v);
    if (!/^\d+$/.test(v) || n < 1 || n > 3650) { setDayErr('Enter whole days from 1 to 3650, or leave it empty to keep data until you delete it.'); return; }
    setDayErr(null);
    if (n !== st.data.retentionDays) put({ ...st.data, retentionDays: n });
  };
  const doDelete = async (e) => {
    e.preventDefault();
    if (typed !== orgName) return;
    setBusy(true); setDelErr(null);
    try {
      const r = await postJSON(`/api/orgs/${enc(orgId)}/delete-pilot-data`, { confirm: typed });
      toast(`Deleted ${plural(r?.deletedScans ?? 0, 'scan')} and ${plural(r?.deletedFiles ?? 0, 'file')}.`);
      setDel(false);
    } catch (x) { setDelErr(x.message); }
    setBusy(false);
  };

  let body;
  if (orgId == null) body = <Grey>Pick an organization first.</Grey>;
  else if (st.loading) body = <Loading />;
  else if (st.error && notBuilt(st.error)) body = <Grey>Pilot data settings are not available on this server yet.</Grey>;
  else if (st.error) body = <ErrorLine>{`Pilot data settings could not be loaded. ${st.error.message}`}</ErrorLine>;
  else {
    body = (
      <>
        <Switch
          label="Never use for training"
          meta={`Photos and readings from ${orgName} are never used to train models.`}
          checked={st.data.neverTrain}
          onChange={(v) => put({ ...st.data, neverTrain: v })}
        />
        <Switch
          label="Look up specifications online"
          meta="Only the make and model names go to the makers' public pages and a web search. Off: nothing leaves the RackTrack server for these lookups."
          checked={st.data.onlineLookups}
          onChange={(v) => put({ ...st.data, onlineLookups: v })}
        />
        <Field
          label="Keep pilot data for"
          required
          inputMode="numeric"
          value={days}
          onChange={(e) => { setDays(e.target.value); setDayErr(null); }}
          onBlur={saveDays}
          suffix={<span className={styles.unit}>days</span>}
          hint="After this, photos and results are deleted."
          error={dayErr}
          className={styles.days}
        />
        <ErrorLine>{saveErr}</ErrorLine>
        {ORG_ONLY ? null : (                  /* the /org/ build: the scans are the app's; this store's delete would not touch them */
          <>
            <p className={styles.mt24}>
              <TextButton danger onClick={() => { setDel(true); setTyped(''); setDelErr(null); }}>Delete all pilot data</TextButton>
            </p>
            <Grey>{`Removes every photo, scan, Excel dataset and result for ${orgName}. Accounts stay.`}</Grey>
          </>
        )}
        {ORG_ONLY ? null : <p><TextButton small to="/help/data">How your data is handled</TextButton></p>}
      </>
    );
  }
  return (
    <section aria-label="Pilot data">
      <SectionLabel>Pilot data</SectionLabel>
      {body}
      <Sheet open={del} onClose={() => setDel(false)} title="Delete all pilot data?">
        <form onSubmit={doDelete} noValidate>
          <p className={styles.sentence}>{`Every photo, scan, Excel dataset and result for ${orgName} will be removed for good. This cannot be undone.`}</p>
          <Field label={`Type ${orgName} to confirm`} required value={typed} onChange={(e) => setTyped(e.target.value)} autoComplete="off" spellCheck="false" />
          <ErrorLine>{delErr}</ErrorLine>
          <Actions>
            <Button type="submit" variant="danger" size="auto" busy={busy} disabled={typed !== orgName}>Delete everything</Button>
            <TextButton onClick={() => setDel(false)}>Keep it</TextButton>
          </Actions>
        </form>
      </Sheet>
    </section>
  );
}

/* ---- Organizations (owner) ----------------------------------------- */

function OrgSheet({ org, onClose, onDone }) {
  const toast = useToast();
  const [view, setView] = useState('menu');
  const [name, setName] = useState('');
  const [typed, setTyped] = useState('');
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (org) { setView('menu'); setName(org.name || ''); setTyped(''); setErr(null); setBusy(false); } }, [org]);
  if (!org) return null;
  const active = !org.status || org.status === 'active';
  const doRun = async (fn, msg) => {
    setBusy(true); setErr(null);
    try { await fn(); toast(msg); onDone(); onClose(); } catch (x) { setErr(x.message); setBusy(false); }
  };
  let content;
  if (view === 'rename') {
    content = (
      <form noValidate onSubmit={(e) => { e.preventDefault(); const v = name.trim(); if (!v) { setErr('Enter the organization name'); return; } doRun(() => patchJSON(`/api/orgs/${enc(org.id)}`, { name: v }), 'Organization renamed.'); }}>
        <Field label="Organization name" required value={name} onChange={(e) => { setName(e.target.value); setErr(null); }} autoComplete="off" />
        <ErrorLine>{err}</ErrorLine>
        <Actions>
          <Button type="submit" size="auto" busy={busy}>Save</Button>
          <TextButton onClick={() => setView('menu')}>Cancel</TextButton>
        </Actions>
      </form>
    );
  } else if (view === 'deactivate') {
    content = (
      <>
        <p className={styles.sentence}>{`Deactivate ${org.name}? Its people cannot use the app until you reactivate it.`}</p>
        <ErrorLine>{err}</ErrorLine>
        <Actions>
          <Button size="auto" variant="danger" busy={busy} onClick={() => doRun(() => patchJSON(`/api/orgs/${enc(org.id)}`, { status: 'inactive' }), `${org.name} deactivated.`)}>Deactivate</Button>
          <TextButton onClick={() => setView('menu')}>Keep it on</TextButton>
        </Actions>
      </>
    );
  } else if (view === 'remove') {
    content = (
      <form noValidate onSubmit={(e) => { e.preventDefault(); if (typed !== org.name) return; doRun(() => deleteJSON(`/api/orgs/${enc(org.id)}`), `${org.name} removed.`); }}>
        <p className={styles.sentence}>{`${org.name}, its sites, people and scans will be removed for good. This cannot be undone.`}</p>
        <Field label={`Type ${org.name} to confirm`} required value={typed} onChange={(e) => setTyped(e.target.value)} autoComplete="off" spellCheck="false" />
        <ErrorLine>{err}</ErrorLine>
        <Actions>
          <Button type="submit" size="auto" variant="danger" busy={busy} disabled={typed !== org.name}>Remove organization</Button>
          <TextButton onClick={() => setView('menu')}>Keep it</TextButton>
        </Actions>
      </form>
    );
  } else {
    content = (
      <>
        <Grey m0>{statusWord(org.status)}</Grey>
        <div className={styles.menu}>
          <ListRow compact title="Rename" onClick={() => setView('rename')} />
          {org.status === 'pending' ? null : active
            ? <ListRow compact title="Deactivate" onClick={() => setView('deactivate')} />
            : <ListRow compact title="Reactivate" onClick={() => doRun(() => patchJSON(`/api/orgs/${enc(org.id)}`, { status: 'active' }), `${org.name} reactivated.`)} />}
          <ListRow compact title="Remove" titleClassName={styles.danger} onClick={() => setView('remove')} />
        </div>
        <ErrorLine>{err}</ErrorLine>
      </>
    );
  }
  return <Sheet open={!!org} onClose={onClose} title={org.name}>{content}</Sheet>;
}

function AddOrgSheet({ open, onClose, onDone }) {
  const toast = useToast();
  const EMPTY = { name: '', adminUsername: '', adminEmail: '', adminPassword: '' };
  const [f, setF] = useState(EMPTY);
  const [errs, setErrs] = useState({});
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(false);
  const [show, setShow] = useState(false);
  useEffect(() => { if (open) { setF(EMPTY); setErrs({}); setErr(null); setShow(false); } }, [open]); // eslint-disable-line react-hooks/exhaustive-deps
  const set = (k) => (e) => { const v = e.target.value; setF((x) => ({ ...x, [k]: v })); setErrs((x) => ({ ...x, [k]: null })); };
  const submit = async (e) => {
    e.preventDefault();
    const body = { name: f.name.trim(), adminUsername: f.adminUsername.trim(), adminEmail: f.adminEmail.trim().toLowerCase(), adminPassword: f.adminPassword };
    const bad = {
      name: required(body.name, 'Enter the organization name'),
      adminUsername: required(body.adminUsername, 'Enter a username') || vUsername(body.adminUsername),
      adminEmail: required(body.adminEmail, 'Enter an email') || vEmail(body.adminEmail),
      adminPassword: required(body.adminPassword, 'Enter a password') || vPassword(body.adminPassword),
    };
    setErrs(bad);
    if (Object.values(bad).some(Boolean)) return;
    setBusy(true); setErr(null);
    try { await postJSON('/api/orgs', body); toast(`${body.name} added.`); onDone(); onClose(); } catch (x) { setErr(x.message); }
    setBusy(false);
  };
  return (
    <Sheet open={open} onClose={onClose} title="Add an organization">
      <form onSubmit={submit} noValidate>
        <Field label="Organization name" required value={f.name} onChange={set('name')} error={errs.name} autoComplete="off" />
        <Field label="Admin username" required value={f.adminUsername} onChange={set('adminUsername')} error={errs.adminUsername} autoComplete="off" spellCheck="false" />
        <Field label="Admin email" required type="email" value={f.adminEmail} onChange={set('adminEmail')} error={errs.adminEmail} autoComplete="off" />
        <Field
          label="Admin password"
          required
          type={show ? 'text' : 'password'}
          value={f.adminPassword}
          onChange={set('adminPassword')}
          error={errs.adminPassword}
          autoComplete="new-password"
          suffix={<TextButton small onClick={() => setShow((v) => !v)}>{show ? 'Hide' : 'Show'}</TextButton>}
        />
        <ErrorLine>{err}</ErrorLine>
        <Actions>
          <Button type="submit" size="auto" busy={busy}>Add organization</Button>
          <TextButton onClick={onClose}>Cancel</TextButton>
        </Actions>
      </form>
    </Sheet>
  );
}

function Organizations({ onChanged }) {
  const toast = useToast();
  const [st, setSt] = useState({ orgs: null, pending: [], error: null });
  const [tick, setTick] = useState(0);
  const [open, setOpen] = useState(null);
  const [adding, setAdding] = useState(false);
  const [err, setErr] = useState(null);
  const reload = useCallback(() => { setTick((t) => t + 1); onChanged(); }, [onChanged]);
  useEffect(() => {
    let gone = false;
    Promise.all([getJSON('/api/dashboard/owner'), getJSON('/api/orgs/pending').catch(() => ({ pending: [] }))])
      .then(([d, p]) => { if (!gone) setSt({ orgs: d?.organizations || [], pending: p?.pending || [], error: null }); })
      .catch((error) => { if (!gone) setSt({ orgs: null, pending: [], error }); });
    return () => { gone = true; };
  }, [tick]);

  const act = async (o, what) => {
    setErr(null);
    try {
      await postJSON(`/api/orgs/${enc(o.id)}/${what}`, {});
      toast(what === 'approve' ? `${o.name} approved.` : `${o.name} rejected.`);
      reload();
    } catch (x) { setErr(x.message); }
  };

  /* The pending list names the admin who asked; the dashboard has the counts. */
  const rows = useMemo(() => {
    const list = (st.orgs || []).map((o) => ({ ...o }));
    for (const p of st.pending) {
      const hit = list.find((o) => Number(o.id) === Number(p.id));
      if (hit) Object.assign(hit, { status: 'pending', admin_username: p.admin_username, admin_email: p.admin_email });
      else list.push({ ...p, status: 'pending' });
    }
    return list;
  }, [st]);

  let body;
  if (st.error) body = <ErrorLine>{`The organizations could not be loaded. ${st.error.message}`}</ErrorLine>;
  else if (!st.orgs) body = <Loading />;
  else if (!rows.length) body = <EmptyState text="There is no organization yet." />;
  else {
    body = rows.map((o) => {
      const pending = o.status === 'pending';
      const meta = pending
        ? `Asked by ${o.admin_username || o.admin_email || 'an admin'}${o.admin_email && o.admin_username ? `, ${o.admin_email}` : ''}`
        : `${plural(o.sites ?? 0, 'site')}, ${plural(o.users ?? 0, 'person', 'people')}`;
      return (
        <ListRow
          key={o.id}
          title={o.name}
          meta={meta}
          right={(
            <span className={styles.orgRight}>
              {pending ? (
                <>
                  <TextButton onClick={() => act(o, 'approve')}>Approve</TextButton>
                  <TextButton onClick={() => act(o, 'reject')}>Reject</TextButton>
                </>
              ) : <span className={o.status && o.status !== 'active' ? styles.off : styles.word}>{statusWord(o.status)}</span>}
              <TextButton small onClick={() => setOpen(o)} aria-label={`Options for ${o.name}`}>Options</TextButton>
            </span>
          )}
        />
      );
    });
  }
  return (
    <Disclosure title="Organizations" className={styles.orgs} sub="Every organization on this server">
      <ErrorLine>{err}</ErrorLine>
      {body}
      <Links className={styles.links}>
        <TextButton onClick={() => setAdding(true)}>Add an organization</TextButton>
      </Links>
      <OrgSheet org={open} onClose={() => setOpen(null)} onDone={reload} />
      <AddOrgSheet open={adding} onClose={() => setAdding(false)} onDone={reload} />
    </Disclosure>
  );
}

/* ---- The screen ------------------------------------------------------ */

export default function OrgSettingsPage() {
  const { user } = useAuth();
  const s = useOrgSettings();
  const isOwner = user?.role === 'owner';
  const [renamed, setRenamed] = useState({});
  const orgName = renamed[s.orgId] || s.orgProfile?.name || (isOwner ? s.orgs.find((o) => Number(o.id) === Number(s.orgId))?.name : user?.organization?.name) || 'this organization';
  const onRenamed = useCallback((name) => setRenamed((m) => ({ ...m, [s.orgId]: name })), [s.orgId]);
  const refresh = s.refresh;
  const onOrgsChanged = useCallback(() => refresh(), [refresh]);

  let main;
  if (s.loading && !s.orgProfile) main = <Loading />;
  else if (s.error) {
    main = (
      <>
        <ErrorLine>{`Organization settings could not be loaded. ${s.error.message}`}</ErrorLine>
        <TextButton onClick={s.refresh}>Try again</TextButton>
      </>
    );
  } else if (s.orgId == null) {
    main = <EmptyState text={isOwner ? 'There is no organization yet. Add one below.' : 'This account is not linked to an organization.'} />;
  } else {
    const profile = { ...(s.orgProfile || {}), name: renamed[s.orgId] || s.orgProfile?.name || orgName };
    main = (
      <Two eq>
        <OrgSection s={{ ...s, orgProfile: profile }} isOwner={isOwner} onRenamed={onRenamed} />
        <PilotData orgId={s.orgId} orgName={orgName} />
      </Two>
    );
  }

  return (
    <>
      <TopBar title="Organization settings" backTo="/you" />
      <Page>
        {isOwner && s.orgs.length > 1 ? (
          <SelectField
            label="Organization"
            className={styles.picker}
            value={s.orgId == null ? '' : String(s.orgId)}
            options={s.orgs.map((o) => ({ value: String(o.id), label: renamed[o.id] || o.name }))}
            onChange={(e) => s.pickOrg(e.target.value)}
          />
        ) : null}
        {main}
        {isOwner ? <Organizations onChanged={onOrgsChanged} /> : null}
        <p className={styles.mt24}><TextButton to="/setup">Open first-run setup</TextButton></p>
      </Page>
    </>
  );
}
