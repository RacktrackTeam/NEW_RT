import { useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { TopBar } from '../components/TopBar.jsx';
import { Page, Stick } from '../components/Page.jsx';
import { Button, TextButton } from '../components/Button.jsx';
import { Grey, ErrorLine, EmptyState, Facts, SectionLabel } from '../components/Text.jsx';
import { ListRow } from '../components/ListRow.jsx';
import { useToast } from '../components/Toast.jsx';
import { useIsLaptop } from '../hooks/useIsLaptop.js';
import { postJSON, notBuilt } from '../utils/http';
import { plural } from '../utils/format';
import styles from './CheckWorkbookPage.module.css';

const NOT_BUILT = 'The Excel dataset is not available on this server yet.';

/* What each sheet of the manager's workbook is used for, in the order it is read. */
const SHEET_USE = {
  A_Z_CONNECTION_MATRIX: 'The cables',
  RACK_INVENTORY: 'The racks',
  DEVICE_INVENTORY: 'The devices',
  PORT_INVENTORY: 'The ports',
  LABEL_RULES: 'How labels read',
  LOOKUPS: 'The allowed values',
  README: 'For people only, not imported',
  FIELD_DICTIONARY: 'For people only, not imported',
};

function NoUpload() {
  return (
    <>
      <TopBar title="Check the workbook" backTo="/matrix" />
      <Page>
        <EmptyState
          text="There is no uploaded workbook to check. Import it from the Excel dataset page."
          action={<TextButton to="/matrix">Go to the Excel dataset</TextButton>}
        />
      </Page>
    </>
  );
}

export default function CheckWorkbookPage() {
  const location = useLocation();
  const upload = location.state?.upload;
  if (!upload || !upload.uploadId) return <NoUpload />;
  return <CheckWorkbook upload={upload} fileName={location.state?.fileName} />;
}

/** One problem as a line: where it is, then what it is. */
const whereOf = (p) => [p.sheet, p.rowNo != null ? `row ${p.rowNo}` : null].filter(Boolean).join(', ');

function CheckWorkbook({ upload, fileName }) {
  const navigate = useNavigate();
  const toast = useToast();
  const laptop = useIsLaptop();
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const counts = upload.counts || {};
  const problems = Array.isArray(upload.problems) ? upload.problems : [];
  const errors = problems.filter((p) => p.level === 'error');
  const warnings = problems.filter((p) => p.level !== 'error');
  const more = Math.max(0, (counts.problems || 0) - problems.length);
  const sheets = Array.isArray(upload.sheets) ? upload.sheets : [];
  const revisions = Array.isArray(upload.revisions) ? upload.revisions : [];

  const save = async () => {
    setBusy(true); setError('');
    try {
      const out = await postJSON('/api/matrix/commit', { uploadId: upload.uploadId });
      const n = out?.counts?.cables;
      toast(n != null ? `Workbook imported. ${plural(n, 'cable')}.` : 'Workbook imported.');
      navigate('/matrix', { replace: true });
    } catch (e) {
      setError(notBuilt(e) ? NOT_BUILT : e.message);
    } finally {
      setBusy(false);
    }
  };

  const saveBtn = <Button size={laptop ? 'auto' : undefined} onClick={save} busy={busy}>Import workbook</Button>;
  const notChecked = (counts.cables || 0) - (counts.checked || 0);
  const shownName = fileName || upload.fileName;

  return (
    <>
      <TopBar title="Check the workbook" backTo="/matrix" />
      <Page stick={!laptop}>
        {shownName ? <Grey m0 className={styles.file}>{shownName}</Grey> : null}
        <p className={styles.lead}>
          {errors.length
            ? `${plural(counts.errors || errors.length, 'problem')} must be fixed before those cables can be checked. You can import now and fix the workbook later.`
            : 'Every cable can be checked. Import it when the numbers below look right.'}
        </p>

        <Facts
          pairs={[
            ['Cables', notChecked > 0 ? `${counts.cables} (${counts.checked} checked, ${notChecked} planned, spare or not active)` : String(counts.cables ?? 0)],
            ['Racks', String(counts.racks ?? 0)],
            ['Devices', String(counts.devices ?? 0)],
            ['Ports', String(counts.ports ?? 0)],
            ['Label rules', String(counts.labelRules ?? 0)],
            revisions.length ? ['Revision', revisions.join(', ')] : null,
          ]}
        />

        {errors.length ? (
          <>
            <SectionLabel as="h2">{`Must be fixed to check these cables (${counts.errors ?? errors.length})`}</SectionLabel>
            {errors.map((p, i) => <ListRow key={`e${i}`} compact title={p.reason} meta={whereOf(p)} />)}
          </>
        ) : null}
        {warnings.length ? (
          <>
            <SectionLabel as="h2">{`Worth tidying (${counts.warnings ?? warnings.length})`}</SectionLabel>
            {warnings.map((p, i) => <ListRow key={`w${i}`} compact title={p.reason} meta={whereOf(p)} />)}
          </>
        ) : null}
        {more ? <Grey>{`And ${plural(more, 'more line')} in the workbook.`}</Grey> : null}

        {sheets.length ? (
          <>
            <SectionLabel as="h2">Sheets</SectionLabel>
            <Facts pairs={sheets.map((s) => [s.name, `${SHEET_USE[s.name] || 'Not used'}${s.used ? `, ${plural(s.rows, 'row')}` : ''}`])} />
          </>
        ) : null}

        {laptop ? (
          <div className={styles.save}>
            <ErrorLine>{error}</ErrorLine>
            {saveBtn}
          </div>
        ) : null}
      </Page>
      {!laptop ? (
        <Stick>
          <ErrorLine>{error}</ErrorLine>
          {saveBtn}
        </Stick>
      ) : null}
    </>
  );
}
