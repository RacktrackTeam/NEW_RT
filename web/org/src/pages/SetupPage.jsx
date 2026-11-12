import { useCallback, useEffect, useId, useImperativeHandle, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useAuth } from '../AuthContext.jsx';
import { TopBar } from '../components/TopBar.jsx';
import { Button, TextButton, Actions, Links } from '../components/Button.jsx';
import { Field, SelectField } from '../components/Field.jsx';
import { ListRow } from '../components/ListRow.jsx';
import { Grey, SectionLabel, EmptyState, ErrorLine, Loading, Facts } from '../components/Text.jsx';
import { Tick, Seg } from '../components/Controls.jsx';
import { useOrgSettings } from '../hooks/useOrgSettings';
import {
  STEPS, STEP_KEYS, stepOf, stepLocked, firstIncompleteStep, mandatoryDone, remaining, summaryOf,
  browserTimezone, timezones, countries, composeAddress, dcHasSpoc, dcRulesAccepted, fmtDate,
  required, vEmail, vUsername, vPassword, proposeUsername,
} from '../utils/orgSettings';
import { setupDecision } from '../utils/setupGuard';
import styles from './SetupPage.module.css';

/*
 * The first-run setup: a popup container, one step at a time, Back and Next
 * and nothing else to move by. No step bar, no step numbers. A mandatory
 * field has a red asterisk and nothing else; its message shows once Next is
 * pressed with it empty. Everything saves as it goes (hooks/useOrgSettings).
 *
 * Phone: the container fills the screen under a top bar. Laptop: a white
 * panel over a scrim (the one place a centred box is allowed: it is a popup).
 */

export const RULE = 'You accept how RackTrack works: what the camera reads is kept as a record of the rack as it was photographed, and a person checks it before it is relied on.';

const STOP = {
  org: 'Fill in the marked fields to continue.',
  sites: 'Fill in the marked fields and give each site a SPOC to continue.',
  rules: 'Tick I accept to continue.',
  review: 'Finish what is still needed to continue.',
};

const same = (a, b) => String(a ?? '') === String(b ?? '');
const has = (v) => v != null && String(v).trim() !== '';
const toOptions = (list) => list.map((z) => ({ value: z, label: z }));
const countryOptions = (list) => list.map((c) => ({ value: c.code, label: c.name }));
const ownEnter = (fn) => (e) => { if (e.key === 'Enter') { e.preventDefault(); fn(); } };

/* A write through the hook answers null when it fails and keeps the reason
   beside its mark; the mark is read when drawing, so the words are the
   server's own. */
function useFail(s) {
  const [fail, setFail] = useState(null);
  const text = fail ? (s.marks?.[fail.key]?.error?.message || fail.text) : null;
  const set = useCallback((key, fallbackText) => setFail({ key, text: fallbackText }), []);
  const clear = useCallback(() => setFail(null), []);
  return [text, set, clear];
}

/* ---- Organization ---------------------------------------------------- */

function OrgStep({ s, handle, showAll }) {
  const p = s.orgProfile || {};
  const fallbackTz = useMemo(() => browserTimezone(), []);
  const [f, setF] = useState(() => ({
    timezone: p.timezone || fallbackTz,
    country: p.country || '',
    primary_contact_name: p.primary_contact_name || '',
    primary_contact_email: p.primary_contact_email || '',
  }));
  const [touched, setTouched] = useState({});
  const saved = useRef({ ...p });
  const zones = useMemo(() => toOptions(timezones(f.timezone, fallbackTz)), [f.timezone, fallbackTz]);
  const cs = useMemo(() => countryOptions(countries(f.country)), [f.country]);
  const errs = {
    timezone: required(f.timezone, 'Pick a time zone'),
    country: required(f.country, 'Pick a country'),
    primary_contact_name: required(f.primary_contact_name, 'Enter the name of the person to contact'),
    primary_contact_email: required(f.primary_contact_email, 'Enter their email') || vEmail(f.primary_contact_email.trim()),
  };
  const show = (k) => ((showAll || touched[k]) ? errs[k] : null);

  const commit = (keys, over = {}) => {
    const next = { ...f, ...over };
    setTouched((t) => ({ ...t, ...Object.fromEntries(keys.map((k) => [k, true])) }));
    const body = {};
    keys.forEach((k) => {
      const v = typeof next[k] === 'string' ? next[k].trim() : next[k];
      if (k === 'primary_contact_email' && v && vEmail(v)) return;
      if (!same(v, saved.current[k])) body[k] = v;
    });
    if (!Object.keys(body).length) return;
    saved.current = { ...saved.current, ...body };
    s.saveOrg(body);
  };

  /* The browser's time zone is saved once when the record has none, so what
     is shown is what is held. The country is never guessed. */
  useEffect(() => {
    if (s.orgId == null || has(p.timezone) || !has(f.timezone)) return;
    commit(['timezone']);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useImperativeHandle(handle, () => ({
    validate: () => {
      commit(Object.keys(errs));
      return !Object.values(errs).some(Boolean);
    },
  }));

  const set = (k) => (e) => { const v = e.target.value; setF((x) => ({ ...x, [k]: v })); };
  return (
    <>
      {s.isOwner && s.orgs.length > 1 ? (
        <SelectField
          label="Organization"
          value={s.orgId == null ? '' : String(s.orgId)}
          options={s.orgs.map((o) => ({ value: String(o.id), label: o.name }))}
          onChange={(e) => s.pickOrg(e.target.value)}
        />
      ) : null}
      <Facts pairs={[['Organization', p.name || 'Not named yet']]} className={styles.facts} />
      <SelectField label="Time zone" required value={f.timezone} options={zones} error={show('timezone')}
        onChange={(e) => { const v = e.target.value; setF((x) => ({ ...x, timezone: v })); commit(['timezone'], { timezone: v }); }} />
      <SelectField label="Country" required value={f.country} placeholder="Pick a country" options={cs} error={show('country')}
        onChange={(e) => { const v = e.target.value; setF((x) => ({ ...x, country: v })); commit(['country'], { country: v }); }} />
      <Field label="Primary contact name" required value={f.primary_contact_name} autoComplete="off" error={show('primary_contact_name')}
        onChange={set('primary_contact_name')} onBlur={() => commit(['primary_contact_name'])} />
      <Field label="Primary contact email" required type="email" inputMode="email" value={f.primary_contact_email} autoComplete="off" error={show('primary_contact_email')}
        onChange={set('primary_contact_email')} onBlur={() => commit(['primary_contact_email'])} />
      {s.marks?.org?.state === 'failed' ? <ErrorLine>{`Not saved. ${s.marks.org.error?.message || 'Try again.'}`}</ErrorLine> : null}
    </>
  );
}

/* ---- Sites ------------------------------------------------------------- */

const FAC_KEYS = ['code', 'address_line1', 'address_line2', 'city', 'region', 'postcode', 'country', 'provider', 'access_notes', 'hours'];

function Spoc({ dc, s, showAll }) {
  const hasSpoc = dcHasSpoc(dc);
  const ap = dc.approver;
  const [changing, setChanging] = useState(false);
  const candidates = (s.members || []).filter((m) => m.active !== 0 && m.active !== false
    && (Number(m.tenant_id) === Number(dc.id) || m.role === 'org_admin' || m.role === 'org_owner')
    && Number(m.id) !== Number(ap?.user_id));
  const [mode, setMode] = useState('new');
  const [acc, setAcc] = useState({ username: '', email: '', password: '' });
  const [show, setShow] = useState(false);
  const [tried, setTried] = useState(false);
  const [fail, setFail, clearFail] = useFail(s);
  const [busy, setBusy] = useState(false);
  const username = acc.username || proposeUsername('', acc.email);
  const errs = {
    username: required(username, 'Enter a username') || vUsername(username.trim()),
    email: required(acc.email, 'Enter their email') || vEmail(acc.email.trim()),
    password: required(acc.password, 'Enter a temporary password') || vPassword(acc.password),
  };
  const shown = (k) => (tried ? errs[k] : null);
  const set = (k) => (e) => { const v = e.target.value; setAcc((x) => ({ ...x, [k]: v })); clearFail(); };

  const create = async () => {
    setTried(true);
    if (Object.values(errs).some(Boolean) || busy) return;
    setBusy(true); clearFail();
    const r = await s.createSpocAccount(dc, { username: username.trim(), email: acc.email.trim().toLowerCase(), password: acc.password });
    setBusy(false);
    if (r) { setChanging(false); setAcc({ username: '', email: '', password: '' }); setTried(false); }
    else setFail(`spoc:${dc.id}`, 'The account could not be made. Try again.');
  };
  const pick = async (id) => {
    if (!id) return;
    setBusy(true); clearFail();
    const r = await s.setApprover(dc, { user_id: Number(id) });
    setBusy(false);
    if (r) setChanging(false);
    else setFail(`people:${dc.id}`, 'That person could not be made the SPOC. Try again.');
  };

  if (hasSpoc && !changing) {
    const who = ap?.username || ap?.email || 'Set';
    return (
      <ListRow compact title={who} meta={ap?.username && ap?.email ? `${ap.email}, has an account` : 'Has an account'}
        right={<TextButton small onClick={() => setChanging(true)}>Change</TextButton>} />
    );
  }
  return (
    <div className={styles.spoc}>
      {candidates.length ? (
        <Seg label="SPOC account" className={styles.seg} value={mode} onChange={setMode}
          options={[{ value: 'new', label: 'New account' }, { value: 'pick', label: 'Someone with an account' }]} />
      ) : null}
      {mode === 'pick' && candidates.length ? (
        <SelectField label="Person" required value="" placeholder="Pick a person" disabled={busy}
          options={candidates.map((m) => ({ value: String(m.id), label: `${m.username}${m.email ? `, ${m.email}` : ''}` }))}
          onChange={(e) => pick(e.target.value)} />
      ) : (
        <>
          <Grey>A site manager account for the person in charge here.</Grey>
          <Field label="Username" required value={username} autoComplete="off" spellCheck="false" error={shown('username')} onChange={set('username')} onKeyDown={ownEnter(create)} />
          <Field label="Email" required type="email" inputMode="email" value={acc.email} autoComplete="off" error={shown('email')} onChange={set('email')} onKeyDown={ownEnter(create)} />
          <Field label="Temporary password" required type={show ? 'text' : 'password'} value={acc.password} autoComplete="new-password"
            hint="8 or more, with a capital, a small letter, a digit and a symbol." error={shown('password')} onChange={set('password')} onKeyDown={ownEnter(create)}
            suffix={<TextButton small onClick={() => setShow((v) => !v)}>{show ? 'Hide' : 'Show'}</TextButton>} />
          <Links><TextButton onClick={create} disabled={busy}>{busy ? 'Making the account' : 'Create the account'}</TextButton></Links>
        </>
      )}
      <ErrorLine>{fail}</ErrorLine>
      {changing ? <Links><TextButton small onClick={() => setChanging(false)}>Keep the SPOC as it is</TextButton></Links> : null}
      {showAll && !hasSpoc ? <ErrorLine>Give this site a SPOC: make an account or pick someone.</ErrorLine> : null}
    </div>
  );
}

function SiteBlock({ dc, s, showAll, register }) {
  const fallback = useMemo(() => browserTimezone(), []);
  const d = dc.datacentre || {};
  const fac0 = dc.profile?.facility || {};
  const org = s.orgProfile || {};
  const [name, setName] = useState(dc.name || '');
  const [nameErr, setNameErr] = useState(null);
  const [nameFail, setNameFail, clearNameFail] = useFail(s);
  const [why, setWhy, clearWhy] = useFail(s);
  const [f, setF] = useState(() => ({
    address_line1: fac0.address_line1 ?? (FAC_KEYS.some((k) => has(fac0[k])) ? '' : (d.address ?? '')),
    address_line2: fac0.address_line2 ?? '',
    city: fac0.city ?? '',
    region: fac0.region ?? '',
    postcode: fac0.postcode ?? '',
    country: fac0.country ?? org.country ?? '',
    provider: fac0.provider ?? '',
    hours: fac0.hours ?? '',
    access_notes: fac0.access_notes ?? '',
    timezone: d.timezone || org.timezone || fallback,
  }));
  const [more, setMore] = useState(() => has(fac0.provider) || has(fac0.hours) || has(fac0.access_notes));
  const [touched, setTouched] = useState({});
  const [asking, setAsking] = useState(false);
  const [going, setGoing] = useState(false);
  const savedDc = useRef({ address: d.address ?? '', timezone: d.timezone ?? '' });
  const savedFac = useRef(JSON.stringify(Object.fromEntries(FAC_KEYS.map((k) => [k, fac0[k] ?? '']))));
  const zones = useMemo(() => toOptions(timezones(f.timezone, fallback)), [f.timezone, fallback]);
  const cs = useMemo(() => countryOptions(countries(f.country)), [f.country]);
  useEffect(() => { setName(dc.name || ''); }, [dc.name]);

  const errs = {
    address_line1: required(f.address_line1, 'Enter the street address'),
    country: required(f.country, 'Pick a country'),
    timezone: required(f.timezone, 'Pick a time zone'),
  };
  const show = (k) => ((showAll || touched[k]) ? errs[k] : null);
  const set = (k) => (e) => { const v = e.target.value; setF((x) => ({ ...x, [k]: v })); };

  /* The facility section is replaced whole; the one-line address on the site
     record is made from it, so the two never differ. */
  const commit = (fields, over = {}) => {
    const next = { ...f, ...over };
    setTouched((t) => ({ ...t, ...Object.fromEntries(fields.map((k) => [k, true])) }));
    const fac = Object.fromEntries(FAC_KEYS.map((k) => [k, String((k in next ? next[k] : fac0[k]) ?? '').trim()]));
    const facJson = JSON.stringify(fac);
    if (facJson !== savedFac.current) {
      savedFac.current = facJson;
      s.saveSection(dc, 'facility', Object.fromEntries(Object.entries(fac).map(([k, v]) => [k, v === '' ? null : (k === 'country' ? v.toUpperCase() : v)])));
    }
    const body = {};
    const address = has(fac.address_line1) ? composeAddress(fac) : '';
    if (address && address !== savedDc.current.address) body.address = address;
    if (next.timezone && !same(next.timezone, savedDc.current.timezone)) body.timezone = next.timezone;
    if (!Object.keys(body).length) return;
    savedDc.current = { ...savedDc.current, ...body };
    s.saveDatacentre(dc, body);
  };

  const saveName = async () => {
    const v = name.trim();
    if (v === (dc.name || '')) return;
    if (v.length < 2) { setNameErr('Enter the site name'); return; }
    setNameErr(null); clearNameFail();
    const r = await s.renameDatacentre(dc, v);
    if (!r) setNameFail(`datacentres:${dc.id}`, 'The name could not be saved. Try again.');
  };
  const remove = async () => {
    setGoing(true); clearWhy();
    const r = await s.removeDatacentre(dc);
    if (!r) { setWhy(`datacentres:${dc.id}`, 'The site could not be removed. Try again.'); setGoing(false); setAsking(false); }
  };

  useEffect(() => register(dc.id, {
    validate: () => {
      commit(['address_line1', 'country', 'timezone']);
      return !Object.values(errs).some(Boolean) && dcHasSpoc(dc);
    },
  }));

  return (
    <section className={styles.site} aria-label={dc.name}>
      <Field label="Site name" required value={name} autoComplete="off" error={nameErr || nameFail} onChange={(e) => { setName(e.target.value); setNameErr(null); clearNameFail(); }} onBlur={saveName} />
      <Field label="Street address" required value={f.address_line1} autoComplete="street-address" error={show('address_line1')} onChange={set('address_line1')} onBlur={() => commit(['address_line1'])} />
      <Field label="Address line 2" value={f.address_line2} autoComplete="off" onChange={set('address_line2')} onBlur={() => commit(['address_line2'])} />
      <div className={styles.pair}>
        <Field label="City" value={f.city} autoComplete="address-level2" onChange={set('city')} onBlur={() => commit(['city'])} />
        <Field label="Region" value={f.region} autoComplete="address-level1" onChange={set('region')} onBlur={() => commit(['region'])} />
      </div>
      <div className={styles.pair}>
        <Field label="Postcode" value={f.postcode} autoComplete="postal-code" onChange={set('postcode')} onBlur={() => commit(['postcode'])} />
        <SelectField label="Country" required value={f.country} placeholder="Pick a country" options={cs} error={show('country')}
          onChange={(e) => { const v = e.target.value; setF((x) => ({ ...x, country: v })); commit(['country'], { country: v }); }} />
      </div>
      <SelectField label="Site time zone" required value={f.timezone} options={zones} error={show('timezone')}
        onChange={(e) => { const v = e.target.value; setF((x) => ({ ...x, timezone: v })); commit(['timezone'], { timezone: v }); }} />
      {more ? (
        <>
          <Field label="Provider" value={f.provider} autoComplete="off" onChange={set('provider')} onBlur={() => commit(['provider'])} />
          <Field label="Opening hours" value={f.hours} autoComplete="off" onChange={set('hours')} onBlur={() => commit(['hours'])} />
          <Field label="Access notes" multiline rows={3} value={f.access_notes} onChange={set('access_notes')} onBlur={() => commit(['access_notes'])} />
        </>
      ) : (
        <Links><TextButton small onClick={() => setMore(true)}>Add building details</TextButton></Links>
      )}
      {dc.loadError ? <ErrorLine>{dc.loadError.message}</ErrorLine> : null}

      <SectionLabel as="h3">SPOC, the person in charge here</SectionLabel>
      <Spoc dc={dc} s={s} showAll={showAll} />

      {asking ? (
        <div className={styles.ask}>
          <p>{`Remove ${dc.name}? Everything set up for this site goes with it. This cannot be undone.`}</p>
          <Links>
            <TextButton danger onClick={remove} disabled={going}>{going ? 'Removing' : 'Yes, remove it'}</TextButton>
            <TextButton onClick={() => setAsking(false)} disabled={going}>Keep it</TextButton>
          </Links>
        </div>
      ) : (
        <Links className={styles.removeLink}><TextButton small danger onClick={() => { setAsking(true); clearWhy(); }}>Remove this site</TextButton></Links>
      )}
      <ErrorLine>{why}</ErrorLine>
    </section>
  );
}

function SitesStep({ s, handle, showAll }) {
  const dcs = s.dcs;
  const blocks = useRef(new Map());
  const register = useCallback((id, h) => { blocks.current.set(id, h); }, []);
  const [slot, setSlot] = useState(() => !dcs.length);
  const [name, setName] = useState('');
  const [addErr, setAddErr] = useState(null);
  const [addFail, setAddFail, clearAddFail] = useFail(s);
  const [adding, setAdding] = useState(false);
  const { loadMembers } = s;
  useEffect(() => { loadMembers(); }, [loadMembers]);
  useEffect(() => { if (!dcs.length) setSlot(true); }, [dcs.length]);

  const add = async () => {
    const v = name.trim();
    if (v.length < 2) { setAddErr('Enter the site name'); return; }
    setAdding(true); setAddErr(null); clearAddFail();
    const r = await s.addDatacentre(v);
    setAdding(false);
    if (r) { setName(''); setSlot(false); }
    else setAddFail('datacentres', 'The site could not be added. Try again.');
  };

  useImperativeHandle(handle, () => ({
    validate: () => {
      if (!dcs.length) { setAddErr('Enter the site name and add it'); return false; }
      if (slot && name.trim()) { setAddErr('Add this site, or remove it'); return false; }
      const results = dcs.map((d) => blocks.current.get(d.id)?.validate() ?? false);
      return results.every(Boolean);
    },
  }));

  return (
    <>
      {dcs.map((dc) => <SiteBlock key={dc.id} dc={dc} s={s} showAll={showAll} register={register} />)}
      {slot ? (
        <div className={styles.newSite}>
          <Field label={dcs.length ? 'Another site' : 'Site name'} required value={name} autoComplete="off" error={addErr || addFail} disabled={adding}
            onChange={(e) => { setName(e.target.value); setAddErr(null); clearAddFail(); }} onKeyDown={ownEnter(add)} />
          <Links>
            <TextButton onClick={add} disabled={adding}>{adding ? 'Adding' : 'Add site'}</TextButton>
            {dcs.length ? <TextButton onClick={() => { setSlot(false); setName(''); setAddErr(null); }}>Remove</TextButton> : null}
          </Links>
        </div>
      ) : (
        <Links className={styles.addMore}><TextButton onClick={() => setSlot(true)}>Add a site</TextButton></Links>
      )}
    </>
  );
}

/* ---- Rules --------------------------------------------------------------- */

function RulesStep({ s, handle, showAll }) {
  const dcs = s.dcs;
  const pending = dcs.filter((d) => !dcRulesAccepted(d));
  const all = dcs.length > 0 && !pending.length;
  const [busy, setBusy] = useState(false);
  const failed = dcs.filter((d) => s.marks?.[`rules:${d.id}`]?.state === 'failed' && !dcRulesAccepted(d));
  const when = dcs.map((d) => d.rules?.accepted_at).filter(Boolean).sort().pop();
  useImperativeHandle(handle, () => ({ validate: () => all }));
  const accept = async (on) => {
    if (!on || busy) return;
    setBusy(true);
    try { for (const d of pending) await s.acceptRules(d); } finally { setBusy(false); }
  };
  if (!dcs.length) return <EmptyState text="Add a site first. The rules are accepted for each site." />;
  return (
    <>
      <p className={styles.rule}>{RULE}</p>
      {dcs.length > 1 ? <Grey>One tick covers all {dcs.length} sites.</Grey> : null}
      <Tick label="I accept" meta={all && when ? `Accepted ${fmtDate(when)}.` : undefined} checked={all || busy} disabled={all || busy} onChange={accept} />
      {failed.length ? <ErrorLine>{`Not saved for ${failed.map((d) => d.name).join(', ')}. Tick again.`}</ErrorLine> : null}
      {showAll && !all ? <ErrorLine>Tick I accept to continue.</ErrorLine> : null}
    </>
  );
}

/* ---- Review -------------------------------------------------------------- */

function ReviewStep({ s, handle, goto }) {
  const done = mandatoryDone(s.model);
  const still = remaining(s.model).required;
  useImperativeHandle(handle, () => ({ validate: () => mandatoryDone(s.model) }));
  return (
    <>
      {STEPS.filter((st) => st.kind === 'required' && st.key !== 'review').map((st) => (
        <ListRow key={st.key} title={st.title} meta={summaryOf(st.key, s.model)}
          right={<TextButton small onClick={() => goto(st.key)}>Edit</TextButton>} />
      ))}
      {done ? <Grey className={styles.mt16}>Everything needed is done.</Grey> : (
        <>
          <SectionLabel as="h3">Still needed</SectionLabel>
          <ul className={styles.still}>
            {still.map((x) => <li key={x.key}><TextButton onClick={() => goto(x.step)}>{x.label}</TextButton></li>)}
          </ul>
        </>
      )}
    </>
  );
}

function DoneStep() {
  return (
    <>
      <p className={styles.rule}>Setup is done. You can scan a rack now.</p>
      <Grey>You can change any of this later under Organization settings.</Grey>
    </>
  );
}

const SECTION = { org: OrgStep, sites: SitesStep, rules: RulesStep, review: ReviewStep, done: DoneStep };

/* ---- The container -------------------------------------------------------- */

export default function SetupPage() {
  const { user, refreshUser, logout } = useAuth();
  const navigate = useNavigate();
  const s = useOrgSettings();
  const [sp] = useSearchParams();
  const gated = setupDecision(user) === 'setup';
  const [gatedAtStart] = useState(gated);
  const [step, setStep] = useState(null);
  const [showAll, setShowAll] = useState(false);
  const [stop, setStop] = useState(null);
  const [finishing, setFinishing] = useState(false);
  const handle = useRef(null);
  const bodyRef = useRef(null);
  const titleRef = useRef(null);
  const titleId = useId();

  /* The step to open on is decided once: the one the link asked for, else the
     first with mandatory work left. A save never moves the page. */
  useEffect(() => {
    if (step !== null || s.loading || s.error) return;
    const asked = sp.get('step');
    setStep(STEP_KEYS.includes(asked) && asked !== 'done' ? asked : firstIncompleteStep(s.model));
  }, [s.loading, s.error, step, sp, s.model]);

  useLayoutEffect(() => { if (step) titleRef.current?.focus(); }, [step]);

  const close = useCallback(() => navigate('/settings'), [navigate]);
  const signOut = useCallback(() => { logout(); navigate('/login', { replace: true }); }, [logout, navigate]);

  useEffect(() => {
    if (gated) return undefined;
    const onKey = (e) => { if (e.key === 'Escape' && !document.querySelector('[role="dialog"][aria-modal="true"]')) close(); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [gated, close]);

  const def = stepOf(step) || STEPS[0];
  const i = STEP_KEYS.indexOf(def.key);
  const around = (dir) => {
    for (let j = i + dir; j >= 0 && j < STEPS.length; j += dir) if (!stepLocked(STEP_KEYS[j], s.model)) return STEP_KEYS[j];
    return null;
  };
  const prev = around(-1);
  const last = def.kind === 'done';

  const goto = useCallback((k) => {
    setStep(k); setShowAll(false); setStop(null);
    if (bodyRef.current) bodyRef.current.scrollTop = 0;
  }, []);

  const onNext = () => {
    if (document.activeElement && typeof document.activeElement.blur === 'function') document.activeElement.blur();
    const ok = handle.current?.validate ? handle.current.validate() : true;
    if (!ok) {
      setShowAll(true);
      setStop(def.key === 'sites' && !s.dcs.length ? 'Add a site to continue.' : STOP[def.key] || STOP.org);
      if (bodyRef.current) bodyRef.current.scrollTop = 0;
      return;
    }
    const n = around(1);
    if (n) goto(n);
  };
  const finish = async () => {
    if (finishing) return;
    setFinishing(true);
    try { await refreshUser(); } finally { setFinishing(false); }
    navigate(gatedAtStart ? '/' : '/settings', { replace: true });
  };
  /* Enter in a field is Next (or Done), unless the field has its own button. */
  const onKey = (e) => {
    if (e.key !== 'Enter' || e.shiftKey || e.metaKey || e.ctrlKey || e.altKey || e.defaultPrevented) return;
    if (e.target?.tagName !== 'INPUT') return;
    e.preventDefault();
    if (last) finish(); else onNext();
  };

  const Section = SECTION[def.key];
  let body;
  if (s.error) {
    body = (
      <>
        <ErrorLine>{`Setup could not be loaded. ${s.error.message}`}</ErrorLine>
        <TextButton onClick={s.refresh}>Try again</TextButton>
      </>
    );
  } else if (!step || (s.loading && !s.orgProfile)) {
    body = <Loading />;
  } else if (s.orgId == null && !s.isOwner) {
    body = <EmptyState text="This account is not linked to an organization. Ask the owner to add you to one." />;
  } else {
    body = (
      <>
        {stop ? <ErrorLine className={styles.stop}>{stop}</ErrorLine> : null}
        <Section key={def.key === 'org' ? `org-${s.orgId}-${s.orgProfile ? 1 : 0}` : def.key} s={s} handle={handle} showAll={showAll} goto={goto} />
      </>
    );
  }

  const quit = gated
    ? <TextButton onClick={signOut}>Sign out</TextButton>
    : <TextButton onClick={close}>Close</TextButton>;

  return (
    <>
      <div className={styles.phoneOnly}>
        <TopBar title="Organization setup" back={!gated} onBack={close} backTo="/settings" right={gated ? quit : null} />
      </div>
      <div className={styles.veil}>
        <div className={styles.panel} role="dialog" aria-labelledby={titleId} onKeyDown={onKey}>
          <header className={styles.head}>
            <h2 id={titleId} ref={titleRef} tabIndex={-1} className={styles.title}>{step ? def.title : 'Organization setup'}</h2>
            {last ? null : <span className={styles.lapOnly}>{quit}</span>}
          </header>
          {step && def.lead ? <Grey className={styles.lead}>{def.lead}</Grey> : null}
          <div className={styles.body} ref={bodyRef}>{body}</div>
          <footer className={styles.foot}>
            {last ? (
              <Button size="auto" busy={finishing} onClick={finish}>Done</Button>
            ) : (
              <Actions className={styles.acts}>
                <TextButton onClick={() => prev && goto(prev)} disabled={!prev || !step}>Back</TextButton>
                <Button size="auto" onClick={onNext} disabled={!step || !!s.error}>Next</Button>
              </Actions>
            )}
          </footer>
        </div>
      </div>
    </>
  );
}
