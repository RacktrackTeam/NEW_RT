import { forwardRef, useEffect, useState } from 'react';
import { apiUrl, assetTokenGeneration, ensureFreshAssetToken } from '../utils/api';

/**
 * An image the server serves behind the sign-in: scan photos, crops,
 * thumbnails. <img> cannot send a header, so the URL carries the short-lived
 * asset token; when it has expired the image asks for a new one once and
 * tries again, instead of staying broken.
 */
export const AssetImg = forwardRef(function AssetImg({ src, alt = '', onError, ...rest }, ref) {
  const [gen, setGen] = useState(assetTokenGeneration);
  const [retried, setRetried] = useState(false);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    const on = () => setGen(assetTokenGeneration());
    window.addEventListener('rt:asset-token', on);
    return () => window.removeEventListener('rt:asset-token', on);
  }, []);
  useEffect(() => { setRetried(false); setFailed(false); }, [src]);
  if (!src) return null;
  const url = /^(blob:|data:)/.test(src) ? src : apiUrl(src);
  const handleError = (e) => {
    if (!retried && !/^(blob:|data:)/.test(src)) {
      setRetried(true);
      ensureFreshAssetToken({ force: true }).then(() => setGen(assetTokenGeneration()));
      return;
    }
    setFailed(true);
    onError?.(e);
  };
  return (
    <img
      ref={ref}
      key={`${gen}-${retried}`}
      src={url}
      alt={alt}
      data-failed={failed || undefined}
      onError={handleError}
      {...rest}
    />
  );
});

export default AssetImg;
