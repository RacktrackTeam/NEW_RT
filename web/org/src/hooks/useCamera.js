import { useCallback, useEffect, useRef, useState } from 'react';

function pickRecorderMime() {
  const candidates = ['video/mp4', 'video/webm;codecs=vp9', 'video/webm;codecs=vp8', 'video/webm'];
  for (const t of candidates) {
    try { if (typeof MediaRecorder !== 'undefined' && MediaRecorder.isTypeSupported(t)) return t; } catch { /* keep looking */ }
  }
  return '';
}

/**
 * The back camera as a live picture, for the scan screen and the close-ups.
 *
 * capture() takes a JPEG of exactly what the preview shows (the preview crops
 * the camera frame to its own shape, so the saved photo is what was framed).
 * startRecording() and stopRecording() make a video file. `error` is a plain
 * sentence when there is no camera or permission was refused; the screen
 * then offers the files instead.
 */
export function useCamera({ enabled = true } = {}) {
  const videoRef = useRef(null);
  const streamRef = useRef(null);
  const recorderRef = useRef(null);
  const chunksRef = useRef([]);
  const resolveRef = useRef(null);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState(null);
  const [recording, setRecording] = useState(false);

  useEffect(() => {
    if (!enabled) return undefined;
    let gone = false;
    setError(null);
    (async () => {
      if (!navigator.mediaDevices?.getUserMedia) {
        setError('No camera here. Pick a photo from your files.');
        return;
      }
      try {
        const stream = await navigator.mediaDevices.getUserMedia({
          // As sharp as the camera gives: the reader needs the pixels (a 1080p
          // frame is about 2 megapixels, a phone photo 12).
          video: { facingMode: { ideal: 'environment' }, width: { ideal: 3840 }, height: { ideal: 2160 } },
          audio: false,
        });
        if (gone || !videoRef.current) { stream.getTracks().forEach((t) => t.stop()); return; }
        streamRef.current = stream;
        const v = videoRef.current;
        v.srcObject = stream;
        v.onloadedmetadata = () => { v.play().catch(() => {}); if (!gone) setReady(true); };
      } catch {
        if (!gone) setError('The camera is not available. Allow camera access, or pick a photo from your files.');
      }
    })();
    return () => {
      gone = true;
      const r = recorderRef.current;
      if (r && r.state !== 'inactive') { try { r.stop(); } catch { /* already stopped */ } }
      streamRef.current?.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
      setReady(false);
    };
  }, [enabled]);

  const previewFrame = useCallback(() => new Promise((resolve) => {
    const video = videoRef.current;
    if (!video || !video.videoWidth) { resolve(null); return; }
    const vW = video.videoWidth;
    const vH = video.videoHeight;
    const rect = video.getBoundingClientRect();
    const dW = rect.width || vW;
    const dH = rect.height || vH;
    const scale = Math.max(dW / vW, dH / vH);
    const srcW = Math.min(vW, Math.round(dW / scale));
    const srcH = Math.min(vH, Math.round(dH / scale));
    const canvas = document.createElement('canvas');
    canvas.width = srcW;
    canvas.height = srcH;
    canvas.getContext('2d').drawImage(video, Math.round((vW - srcW) / 2), Math.round((vH - srcH) / 2), srcW, srcH, 0, 0, srcW, srcH);
    canvas.toBlob((blob) => {
      resolve(blob ? new File([blob], `photo_${Date.now()}.jpg`, { type: 'image/jpeg' }) : null);
    }, 'image/jpeg', 0.92);
  }), []);

  // The photo at the camera's full resolution where the browser can take one
  // (ImageCapture, on Android), else the frame the preview shows.
  const capture = useCallback(async () => {
    const track = streamRef.current?.getVideoTracks?.()[0];
    if (track && typeof window !== 'undefined' && typeof window.ImageCapture === 'function') {
      try {
        const ic = new window.ImageCapture(track);
        const caps = await ic.getPhotoCapabilities().catch(() => null);
        const settings = caps?.imageWidth?.max && caps?.imageHeight?.max
          ? { imageWidth: caps.imageWidth.max, imageHeight: caps.imageHeight.max } : {};
        const blob = await ic.takePhoto(settings);
        if (blob && blob.size) return new File([blob], `photo_${Date.now()}.jpg`, { type: blob.type || 'image/jpeg' });
      } catch { /* fall back to the preview frame */ }
    }
    return previewFrame();
  }, [previewFrame]);

  const startRecording = useCallback(() => {
    const stream = streamRef.current;
    if (!stream || typeof MediaRecorder === 'undefined') {
      setError('This browser cannot record video. Pick a video from your files.');
      return false;
    }
    const mime = pickRecorderMime();
    let rec;
    try { rec = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined); } catch {
      setError('Video is not available here. Pick a video from your files.');
      return false;
    }
    chunksRef.current = [];
    rec.ondataavailable = (e) => { if (e.data && e.data.size) chunksRef.current.push(e.data); };
    rec.onstop = () => {
      const type = rec.mimeType || mime || 'video/webm';
      const blob = new Blob(chunksRef.current, { type });
      chunksRef.current = [];
      const file = blob.size ? new File([blob], `video_${Date.now()}.${type.includes('mp4') ? 'mp4' : 'webm'}`, { type }) : null;
      resolveRef.current?.(file);
      resolveRef.current = null;
    };
    rec.start();
    recorderRef.current = rec;
    setRecording(true);
    return true;
  }, []);

  /** Stops the recording; resolves with the video file. */
  const stopRecording = useCallback(() => new Promise((resolve) => {
    const r = recorderRef.current;
    recorderRef.current = null;
    setRecording(false);
    if (!r || r.state === 'inactive') { resolve(null); return; }
    resolveRef.current = resolve;
    try { r.stop(); } catch { resolve(null); }
  }), []);

  return { videoRef, ready, error, recording, capture, startRecording, stopRecording };
}

/**
 * Sharpness, framing and light of what the camera sees, about three times a
 * second. The shutter waits until all three are right; the hint says which
 * one is not.
 */
export function useFrameQuality(videoRef, active) {
  const [q, setQ] = useState({ sharp: false, framed: false, lit: false });
  useEffect(() => {
    if (!active) return undefined;
    const canvas = document.createElement('canvas');
    const id = setInterval(() => {
      const video = videoRef.current;
      if (!video || !video.videoWidth) return;
      const sw = 192;
      const sh = Math.max(1, Math.round((sw * video.videoHeight) / video.videoWidth));
      canvas.width = sw;
      canvas.height = sh;
      const ctx = canvas.getContext('2d', { willReadFrequently: true });
      if (!ctx) return;
      ctx.drawImage(video, 0, 0, sw, sh);
      const { data } = ctx.getImageData(0, 0, sw, sh);
      const gray = new Float32Array(sw * sh);
      let luma = 0;
      for (let i = 0, j = 0; i < data.length; i += 4, j += 1) {
        const y = 0.299 * data[i] + 0.587 * data[i + 1] + 0.114 * data[i + 2];
        gray[j] = y;
        luma += y;
      }
      const mean = luma / (sw * sh);
      const x0 = Math.floor(sw * 0.15); const x1 = Math.floor(sw * 0.85);
      const y0 = Math.floor(sh * 0.08); const y1 = Math.floor(sh * 0.92);
      let s = 0; let s2 = 0; let n = 0; let edges = 0;
      for (let y = y0 + 1; y < y1 - 1; y += 1) {
        for (let x = x0 + 1; x < x1 - 1; x += 1) {
          const i = y * sw + x;
          const v = -4 * gray[i] + gray[i - 1] + gray[i + 1] + gray[i - sw] + gray[i + sw];
          s += v; s2 += v * v; n += 1;
          if (Math.abs(v) > 40) edges += 1;
        }
      }
      if (!n) return;
      const m = s / n;
      setQ({ sharp: s2 / n - m * m > 60, framed: edges / n > 0.035, lit: mean > 35 && mean < 235 });
    }, 350);
    return () => clearInterval(id);
  }, [videoRef, active]);
  return q;
}

export default useCamera;
