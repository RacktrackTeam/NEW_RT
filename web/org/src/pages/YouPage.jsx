import { ORG_ONLY, toApp } from '../utils/orgOnly';
import { useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../AuthContext.jsx';
import { TopBar } from '../components/TopBar.jsx';
import { Page } from '../components/Page.jsx';
import { Button, TextButton, Actions } from '../components/Button.jsx';
import { Sheet } from '../components/Sheet.jsx';
import { Avatar } from '../components/Avatar.jsx';
import { Grey, ErrorLine } from '../components/Text.jsx';
import { ActionRow, ActionRows } from '../components/ActionRow.jsx';
import { useJSON } from '../hooks/useJSON.js';
import { postJSON } from '../utils/http';
import { isAdmin, isManager, roleWord, plural, personName } from '../utils/format';
import styles from './YouPage.module.css';

/**
 * The grey value on the Excel dataset row, from GET /api/matrix: the number
 * of cables, "Not imported" when there is none, nothing while loading or
 * when the server has no dataset route.
 */
export function datasetValue(summary) {
  if (!summary) return null;
  if (!summary.import) return 'Not imported';
  const n = summary.import.cableCount ?? summary.import.cable_count ?? summary.counts?.cables;
  return n != null ? plural(n, 'cable') : null;
}

/**
 * The places each role can reach from You, as plain groups of rows with no
 * headings: [[label, to, icon, value]]. Everyone sees History, Help and
 * Contact support; a site manager also the Excel dataset and the site; an
 * admin the organization settings; the owner the console.
 */
export function placesFor(role, { siteName, orgName, dataset } = {}) {
  const work = [
    ORG_ONLY ? null : ['History', '/history', 'history', null],
    isManager(role) && !ORG_ONLY ? ['Excel dataset', '/matrix', 'sheet', dataset || null] : null,
    isManager(role) ? ['Site and people', '/site', 'group', siteName || null] : null,
    isAdmin(role) ? ['Organization settings', '/settings', 'settings', orgName || null] : null,
    role === 'owner' ? ['Console', '/console', 'chart', null] : null,
  ].filter(Boolean);
  const help = [
    ['Help', '/help', 'help', null],
    ['Contact support', '/contact', 'mail', null],
  ];
  return (ORG_ONLY ? [work] : [work, help]).filter((g) => g.length);      // the /org/ build: help and contact are the app's; a technician has no row there
}

/** The items behind the three dots: [label, key]. The same for every role. */
export function menuFor() {
  return [
    ['Change picture', 'picture'],
    ['Change password', 'password'],
    ['Sign out', 'out'],
  ];
}

/* A photo from the gallery, cut to a 256 px square JPEG before it is sent. */
function squareJpeg(file) {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => {
      const s = Math.min(img.naturalWidth, img.naturalHeight);
      const c = document.createElement('canvas');
      c.width = 256; c.height = 256;
      c.getContext('2d').drawImage(img, (img.naturalWidth - s) / 2, (img.naturalHeight - s) / 2, s, s, 0, 0, 256, 256);
      URL.revokeObjectURL(url);
      resolve(c.toDataURL('image/jpeg', 0.85));
    };
    img.onerror = () => { URL.revokeObjectURL(url); reject(new Error('That picture could not be read. Try another one.')); };
    img.src = url;
  });
}

/**
 * You: a profile header (picture, name, role, organization and site) and the
 * places the role can go, as plain grouped rows with no section titles.
 * Picture, password and signing out sit behind the three dots in the bar.
 */
export default function YouPage() {
  const { user, logout, refreshUser } = useAuth();
  const navigate = useNavigate();
  const [menu, setMenu] = useState(false);
  const [pic, setPic] = useState(false);
  const [out, setOut] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const fileRef = useRef(null);
  const role = user?.role;
  const name = personName(user?.name || user?.displayName || user?.username);
  const { data: matrix } = useJSON(isManager(role) && !ORG_ONLY ? '/api/matrix' : null);   // the /org/ build has no Excel dataset page, so it does not ask for one
  const orgName = user?.organization?.name || (role === 'owner' ? 'Every organization' : '');
  const siteName = user?.tenant?.name || (isAdmin(role) ? 'Every site' : '');
  const line = [roleWord(role), orgName, siteName].filter(Boolean).join(', ');
  const groups = placesFor(role, { siteName: user?.tenant?.name, orgName: user?.organization?.name, dataset: datasetValue(matrix) });

  const save = async (fn) => {
    setBusy(true); setErr('');
    try { await fn(); await refreshUser(); setPic(false); } catch (e) { setErr(e.message); } finally { setBusy(false); }
  };
  const removePhoto = () => save(() => postJSON('/api/auth/avatar-photo', { photo: null }));
  const upload = async (file) => {
    if (!file) return;
    save(async () => postJSON('/api/auth/avatar-photo', { photo: await squareJpeg(file) }));
  };
  const pick = (key) => {
    setMenu(false);
    if (key === 'picture') setPic(true);
    else if (key === 'password') navigate('/forgot-password', { state: { email: user?.email, from: '/you' } });
    else if (key === 'out') setOut(true);
  };

  return (
    <>
      <TopBar title="You" back={false} onMore={() => setMenu(true)} />
      <Page>
        <div className={styles.me}>
          <Avatar user={user} size="xl" className={styles.avatar} />
          <div className={styles.who}>
            <p className={styles.name}>{name}</p>
            {line ? <p className={styles.line}>{line}</p> : null}
            {user?.username && user.username.toLowerCase() !== String(name).toLowerCase() ? <p className={styles.line}>{`@${user.username}`}</p> : null}
          </div>
        </div>

        {groups.map((items) => (
          <ActionRows key={items[0][1]} className={styles.group}>
            {items.map(([label, to, icon, value]) => (
              <ActionRow key={to} quiet icon={icon} to={to} title={label} right={value} />
            ))}
          </ActionRows>
        ))}
      </Page>

      <Sheet open={menu} onClose={() => setMenu(false)} label="More">
        <div className={styles.menu}>
          {menuFor().map(([label, key]) => (
            <button
              key={key}
              type="button"
              className={key === 'out' ? `${styles.mi} ${styles.miOut}` : styles.mi}
              onClick={() => pick(key)}
            >
              {label}
            </button>
          ))}
        </div>
      </Sheet>
      <Sheet open={pic} onClose={() => !busy && setPic(false)} title="Your picture">
        <Grey>Upload a photo of yourself. Without one, your initials are shown.</Grey>
        <ErrorLine>{err}</ErrorLine>
        <Actions>
          <Button size="auto" onClick={() => fileRef.current?.click()} busy={busy}>Upload a photo</Button>
          {user?.avatarPhoto ? <TextButton onClick={removePhoto} disabled={busy}>Remove photo</TextButton> : null}
          <TextButton onClick={() => setPic(false)} disabled={busy}>Close</TextButton>
        </Actions>
        <input ref={fileRef} type="file" accept="image/*" className="visually-hidden" tabIndex={-1} aria-hidden="true" onChange={(e) => { upload(e.target.files?.[0]); e.target.value = ''; }} />
      </Sheet>
      <Sheet open={out} onClose={() => setOut(false)} title="Sign out of RackTrack?">
        <Grey>You need your username and password to come back in.</Grey>
        <Actions>
          <Button size="auto" onClick={() => { logout(); if (ORG_ONLY) toApp(); else navigate('/login', { replace: true }); }}>Sign out</Button>
          <TextButton onClick={() => setOut(false)}>Stay signed in</TextButton>
        </Actions>
      </Sheet>
    </>
  );
}
