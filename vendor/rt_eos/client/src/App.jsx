import { lazy, Suspense, useEffect, useState } from 'react';
import { BrowserRouter, Navigate, Outlet, Route, Routes, useLocation, useNavigate } from 'react-router-dom';
import { App as CapApp } from '@capacitor/app';
import { Browser } from '@capacitor/browser';
import { Capacitor } from '@capacitor/core';
import { AuthProvider, useAuth } from './AuthContext.jsx';
import { ToastProvider } from './components/Toast.jsx';
import { PhotoZoom } from './components/PhotoZoom.jsx';
import ErrorBoundary from './components/ErrorBoundary.jsx';
import { Loading } from './components/Text.jsx';
import NavBar, { hasOwnTabs } from './components/NavBar.jsx';
import { isAuthCallbackUrl, parseAuthFragment } from './utils/socialSession';
import { setupDecision, setupUnknown } from './utils/setupGuard';
import { clearPendingScan, fetchScanJob, getPendingScan } from './utils/pendingScan';
import { isAdmin, isManager } from './utils/format';
import SignInPage from './pages/SignInPage.jsx';
import AcceptInvitePage from './pages/AcceptInvitePage.jsx';
import ForgotPasswordPage from './pages/ForgotPasswordPage.jsx';
import AuthCallbackPage from './pages/AuthCallbackPage.jsx';
import PendingPage from './pages/PendingPage.jsx';
import HomePage from './pages/HomePage.jsx';
import ScanPage from './pages/ScanPage.jsx';
import HistoryPage from './pages/HistoryPage.jsx';
import MapPage from './pages/MapPage.jsx';
import PairPage from './pages/PairPage.jsx';
import DevicePage from './pages/DevicePage.jsx';
import YouPage from './pages/YouPage.jsx';
import ResultPage from './pages/ResultPage.jsx';
import PortPage from './pages/PortPage.jsx';
import ZonesPage from './pages/ZonesPage.jsx';
import CheckPage from './pages/CheckPage.jsx';
import HelpPage from './pages/HelpPage.jsx';
import { HelpSourcesPage, HelpDataPage, HelpCameraPage } from './pages/HelpTopics.jsx';
import ContactPage from './pages/ContactPage.jsx';

// Screens few people open load on the way there, not at sign-in.
const MatrixPage = lazy(() => import('./pages/MatrixPage.jsx'));
const CheckWorkbookPage = lazy(() => import('./pages/CheckWorkbookPage.jsx'));
const SitePage = lazy(() => import('./pages/SitePage.jsx'));
const OrgSettingsPage = lazy(() => import('./pages/OrgSettingsPage.jsx'));
const SetupPage = lazy(() => import('./pages/SetupPage.jsx'));
const ConsolePage = lazy(() => import('./pages/ConsolePage.jsx'));

/* An organization that is not active (pending, rejected, switched off) holds
   its people on the waiting screen. The owner has no organization. */
export function orgNotActive(user) {
  const s = user?.organization?.status;
  return user?.role !== 'owner' && !!s && s !== 'active';
}

function Protected({ children, allow }) {
  const { isAuthed, user } = useAuth();
  const location = useLocation();
  if (!isAuthed) return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />;
  if (orgNotActive(user)) return <Navigate to="/pending" replace />;
  if (allow && !allow(user?.role)) return <Navigate to="/" replace />;
  return children;
}

/* An owner or organization admin whose organization still lacks a required
   item goes to the first-run setup instead of Home or Scan. Nobody else is
   ever held. A record that has never seen the field is read once first. */
function SetupGate({ children }) {
  const { user, refreshUser } = useAuth();
  const [checked, setChecked] = useState(false);
  const unknown = setupUnknown(user) && !checked;
  useEffect(() => {
    if (!unknown) return undefined;
    let gone = false;
    refreshUser().finally(() => { if (!gone) setChecked(true); });
    return () => { gone = true; };
  }, [unknown, refreshUser]);
  if (unknown) return <Loading />;
  if (setupDecision(user) === 'setup') return <Navigate to="/setup" replace />;
  return children;
}

/* Every signed-in screen sits under the main navigation (Home, Scan, Map,
   You). The first-run setup and the waiting screen do not, and an open
   result puts its own tabs in the navigation's place. */
function Shell() {
  const { isAuthed, user } = useAuth();
  const { pathname } = useLocation();
  const nav = isAuthed && !orgNotActive(user) && !hasOwnTabs(pathname);
  return (
    <>
      {nav ? <NavBar /> : null}
      <Outlet />
    </>
  );
}

function PendingRoute({ children }) {
  const { isAuthed, user } = useAuth();
  if (!isAuthed) return <Navigate to="/login" replace />;
  if (!orgNotActive(user)) return <Navigate to="/" replace />;
  return children;
}

/* Android's back button walks the app's own history. A screen that shows a
   view of its own (a sheet, a picked device) cancels the rt:back event to
   step out of that first. On the two start screens it leaves the app. */
function AndroidBack() {
  const navigate = useNavigate();
  const { pathname } = useLocation();
  useEffect(() => {
    if (!Capacitor.isNativePlatform()) return undefined;
    let sub;
    (async () => {
      sub = await CapApp.addListener('backButton', () => {
        const ev = new CustomEvent('rt:back', { cancelable: true });
        if (!window.dispatchEvent(ev)) return;
        if (pathname === '/' || pathname === '/login') CapApp.exitApp();
        else navigate(-1);
      });
    })();
    return () => { sub?.remove?.(); };
  }, [navigate, pathname]);
  return null;
}

/* The social sign-in comes back to the phone as a deep link; the web build
   lands on /auth/callback instead. */
function SocialDeepLink() {
  const navigate = useNavigate();
  const { adoptSession } = useAuth();
  useEffect(() => {
    if (!Capacitor.isNativePlatform()) return undefined;
    let sub;
    (async () => {
      sub = await CapApp.addListener('appUrlOpen', ({ url }) => {
        if (!isAuthCallbackUrl(url)) return;
        const result = parseAuthFragment(url);
        if (!result) return;
        Browser.close().catch(() => {});
        if (!result.ok) { navigate('/login', { replace: true, state: { socialError: result.message } }); return; }
        adoptSession(result.token, result.user);
        navigate('/', { replace: true });
      });
    })();
    return () => { sub?.remove?.(); };
  }, [adoptSession, navigate]);
  return null;
}

/* A scan that was being read when the phone put the app to sleep: ask the
   server what became of it and open its result. */
function PendingScanResumer() {
  const navigate = useNavigate();
  const { isAuthed } = useAuth();
  const [resuming, setResuming] = useState(false);
  useEffect(() => {
    let gone = false;
    let timer = null;
    let sub = null;
    let busy = false;
    const tryResume = () => {
      const pending = getPendingScan();
      if (!pending || !isAuthed || busy) return;
      busy = true;
      setResuming(true);
      const deadline = Date.now() + 45000;
      const poll = async () => {
        if (gone) return;
        const job = await fetchScanJob(pending.id);
        if (gone) return;
        if (job.status === 'done' && job.rackId) {
          clearPendingScan(); busy = false; setResuming(false);
          navigate(`/results/${encodeURIComponent(job.rackId)}`, { replace: true });
          return;
        }
        if (job.status === 'error' || job.status === 'missing') { clearPendingScan(); busy = false; setResuming(false); return; }
        if (Date.now() > deadline) { busy = false; setResuming(false); return; }
        timer = setTimeout(poll, 1500);
      };
      poll();
    };
    tryResume();
    if (Capacitor.isNativePlatform()) {
      (async () => { sub = await CapApp.addListener('resume', tryResume); })();
    }
    return () => { gone = true; if (timer) clearTimeout(timer); sub?.remove?.(); };
  }, [isAuthed, navigate]);
  if (!resuming) return null;
  return (
    <div role="status" aria-live="polite" style={{ position: 'fixed', inset: 0, zIndex: 90, background: '#fff', padding: '48px 16px' }}>
      <p style={{ fontSize: 22, fontWeight: 650 }}>Opening your scan</p>
      <p style={{ color: 'var(--grey)', fontSize: 13, marginTop: 4 }}>The rack you photographed has been read. It opens in a moment.</p>
      <Loading />
    </div>
  );
}

function Routed() {
  const { pathname } = useLocation();
  const manager = (role) => isManager(role);
  const admin = (role) => isAdmin(role);
  const owner = (role) => role === 'owner';
  return (
    <ErrorBoundary resetKey={pathname}>
      <Suspense fallback={<Loading />}>
        <Routes>
          <Route path="/login" element={<SignInPage />} />
          <Route path="/invite/:code" element={<AcceptInvitePage />} />
          <Route path="/forgot-password" element={<ForgotPasswordPage />} />
          <Route path="/auth/callback" element={<AuthCallbackPage />} />
          <Route path="/pending" element={<PendingRoute><PendingPage /></PendingRoute>} />

          <Route element={<Shell />}>
            <Route path="/" element={<Protected><SetupGate><HomePage /></SetupGate></Protected>} />
            <Route path="/scan" element={<Protected><SetupGate><ScanPage /></SetupGate></Protected>} />
            <Route path="/history" element={<Protected><HistoryPage /></Protected>} />
            <Route path="/map" element={<Protected><MapPage /></Protected>} />
            <Route path="/map/:aScanId/:zScanId" element={<Protected><PairPage /></Protected>} />
            <Route path="/you" element={<Protected><YouPage /></Protected>} />

            <Route path="/results/:scanId" element={<Protected><ResultPage /></Protected>} />
            <Route path="/results/:scanId/device/:deviceId" element={<Protected><DevicePage /></Protected>} />
            <Route path="/results/:scanId/port" element={<Protected><PortPage /></Protected>} />
            <Route path="/results/:scanId/zones" element={<Protected><ZonesPage /></Protected>} />
            <Route path="/checks/:checkId" element={<Protected><CheckPage /></Protected>} />

            <Route path="/matrix" element={<Protected><MatrixPage /></Protected>} />
            <Route path="/matrix/check" element={<Protected allow={manager}><CheckWorkbookPage /></Protected>} />
            <Route path="/site" element={<Protected allow={manager}><SitePage /></Protected>} />
            <Route path="/site/:siteId" element={<Protected allow={manager}><SitePage /></Protected>} />
            <Route path="/settings" element={<Protected allow={admin}><OrgSettingsPage /></Protected>} />
            <Route path="/console" element={<Protected allow={owner}><ConsolePage /></Protected>} />
            <Route path="/help" element={<Protected><HelpPage /></Protected>} />
            <Route path="/help/sources" element={<Protected><HelpSourcesPage /></Protected>} />
            <Route path="/help/data" element={<Protected><HelpDataPage /></Protected>} />
            <Route path="/help/camera" element={<Protected><HelpCameraPage /></Protected>} />
            <Route path="/contact" element={<Protected><ContactPage /></Protected>} />
          </Route>
          <Route path="/setup" element={<Protected allow={admin}><SetupPage /></Protected>} />
          {/* Old addresses people may have kept. */}
          <Route path="/signup" element={<Navigate to="/login" replace />} />
          <Route path="/profile" element={<Navigate to="/you" replace />} />
          <Route path="/dashboard" element={<Navigate to="/console" replace />} />
          <Route path="/logs" element={<Navigate to="/console" replace />} />
          <Route path="/organizations" element={<Navigate to="/settings" replace />} />
          <Route path="/results" element={<Navigate to="/" replace />} />
          <Route path="/switches" element={<Navigate to="/map" replace />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Suspense>
    </ErrorBoundary>
  );
}

export default function App() {
  return (
    <BrowserRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <AuthProvider>
        <ToastProvider>
          <AndroidBack />
          <SocialDeepLink />
          <PendingScanResumer />
          <Routed />
          <PhotoZoom />
        </ToastProvider>
      </AuthProvider>
    </BrowserRouter>
  );
}
