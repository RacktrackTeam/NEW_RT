import { Capacitor } from '@capacitor/core';
import { apiUrl, authFetch } from '../utils/api';
import { saveBlob } from '../utils/exportApi';
import { useToast } from './Toast.jsx';
import { TextButton } from './Button.jsx';

/**
 * A plain link to a file the server makes (a report, an export). In a
 * browser it is an ordinary download link; in the phone app, which has no
 * download bar, the file is fetched and saved on the phone (Android's
 * Download folder, the iPhone's Files app); the share sheet only when that
 * is refused.
 */
export function DownloadLink({ path, name, children, small, className }) {
  const toast = useToast();
  if (!Capacitor.isNativePlatform()) {
    return <TextButton href={apiUrl(path)} download={name} small={small} className={className}>{children}</TextButton>;
  }
  const onClick = async () => {
    try {
      const res = await authFetch(apiUrl(path));
      if (!res.ok) throw new Error('The file could not be made. Try again.');
      const out = await saveBlob(await res.blob(), name);
      if (out?.text) toast(out.text);
    } catch (e) {
      toast(e.message || 'The file could not be saved.');
    }
  };
  return <TextButton onClick={onClick} small={small} className={className}>{children}</TextButton>;
}

export default DownloadLink;
