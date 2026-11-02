import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../AuthContext.jsx';
import { TopBar } from '../components/TopBar.jsx';
import { Page } from '../components/Page.jsx';
import { Button, TextButton } from '../components/Button.jsx';
import { Grey } from '../components/Text.jsx';
import styles from './PendingPage.module.css';

const SAID = {
  pending: ['Waiting for approval', 'Your organization is waiting for RackTrack to approve it. This page opens the app by itself once it is approved.'],
  rejected: ['Not approved', 'Your organization was not approved. Write to support@racktrack.ai if you think this is wrong.'],
  inactive: ['Switched off', 'Your organization has been switched off. Ask your administrator or write to support@racktrack.ai.'],
};

/** Held here while the organization is not active; checks every 12 seconds. */
export default function PendingPage() {
  const { user, refreshUser, logout } = useAuth();
  const navigate = useNavigate();
  const [checking, setChecking] = useState(false);
  const status = user?.organization?.status || 'pending';
  const [title, text] = SAID[status] || SAID.inactive;

  const check = useCallback(async () => {
    setChecking(true);
    const u = await refreshUser();
    setChecking(false);
    if (u?.organization?.status === 'active') navigate('/', { replace: true });
  }, [refreshUser, navigate]);

  useEffect(() => {
    check();
    const id = setInterval(check, 12000);
    return () => clearInterval(id);
  }, [check]);

  return (
    <>
      <TopBar title={title} sub={user?.organization?.name} back={false} signedOut />
      <Page>
        <div className={styles.body}>
          <p className={styles.lead}>{text}</p>
          {status === 'pending' ? <Button onClick={check} busy={checking}>Check now</Button> : null}
          <Grey className={styles.mt24}>
            Signed in as {user?.username}. <TextButton small onClick={() => { logout(); navigate('/login', { replace: true }); }}>Sign out</TextButton>
          </Grey>
        </div>
      </Page>
    </>
  );
}
