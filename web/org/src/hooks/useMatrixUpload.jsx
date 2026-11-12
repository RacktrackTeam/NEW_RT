import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { getJSON, postForm, notBuilt, enc } from '../utils/http';

const NOT_BUILT = 'The Excel dataset is not available on this server yet.';
const MAX_BYTES = 10 * 1024 * 1024;
const OK_TYPES = /\.(xlsx|xlsm)$/i;
const listOf = (d, key) => (Array.isArray(d) ? d : Array.isArray(d?.[key]) ? d[key] : []);

/** Choose the workbook and send it; the check screen takes it from there. */
export function useMatrixUpload() {
  const navigate = useNavigate();
  const inputRef = useRef(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const choose = () => { setError(''); inputRef.current?.click(); };
  const onFile = async (e) => {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (!file) return;
    if (!OK_TYPES.test(file.name)) { setError('Choose the manager\'s workbook (.xlsx). Save an old .xls file as .xlsx first.'); return; }
    if (file.size > MAX_BYTES) { setError('That file is larger than 10 MB. Remove extra sheets and try again.'); return; }
    setBusy(true); setError('');
    try {
      const form = new FormData();
      form.append('file', file);
      const upload = await postForm('/api/matrix/upload', form);
      navigate('/matrix/check', { state: { upload, fileName: file.name } });
    } catch (err) {
      setError(notBuilt(err) ? NOT_BUILT : err.message);
    } finally {
      setBusy(false);
    }
  };
  const input = (
    <input
      ref={inputRef}
      type="file"
      accept=".xlsx,.xlsm"
      className="visually-hidden"
      aria-label="Workbook file"
      tabIndex={-1}
      onChange={onFile}
      data-testid="matrix-file"
    />
  );
  return { choose, busy, error, input };
}

/** Who to ask: the site manager's name when the site overview names one. */
export function useSiteManagerName(user, enabled) {
  const [name, setName] = useState('');
  const siteId = user?.tenant_id;
  useEffect(() => {
    if (!enabled || !siteId) return undefined;
    let gone = false;
    getJSON(`/api/sites/${enc(siteId)}/overview`)
      .then((d) => {
        const m = listOf(d, 'people').find((p) => p.role === 'site_manager');
        if (!gone && m) setName(m.name || m.username || '');
      })
      .catch(() => {});
    return () => { gone = true; };
  }, [enabled, siteId]);
  return name;
}

