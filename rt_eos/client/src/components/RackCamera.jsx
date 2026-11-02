import { useEffect, useState } from 'react';
import { useCamera, useFrameQuality } from '../hooks/useCamera';
import styles from './RackCamera.module.css';

const MODE_WORD = { photo: 'Photo', stitch: 'Stitch', video: 'Video' };

/**
 * The back camera, full screen. Photo closes on the shot; stitch collects
 * until Done; video records until stopped. For a photo the shutter waits
 * until the frame is sharp, filled and lit, and the hint says which is not.
 * `title` replaces the mode word at the top (the Map names the rack).
 */
export function RackCamera({ mode = 'photo', shots = 0, onShot, onVideo, onClose, title, closeUp = false }) {
  const cam = useCamera({ enabled: true });
  const q = useFrameQuality(cam.videoRef, cam.ready && mode === 'photo');
  const allGood = q.sharp && q.framed && q.lit;
  const [secs, setSecs] = useState(0);
  useEffect(() => {
    if (!cam.recording) return undefined;
    setSecs(0);
    const id = setInterval(() => setSecs((s) => s + 1), 1000);
    return () => clearInterval(id);
  }, [cam.recording]);
  const hint = cam.error ? cam.error
    : mode === 'video' ? (cam.recording ? `Recording ${Math.floor(secs / 60)}:${String(secs % 60).padStart(2, '0')}. Press to stop.` : 'Press to start. Walk slowly along the racks.')
      : mode === 'stitch' ? (shots ? `Photo ${shots + 1}: the next part of the rack, going down.` : 'Photo 1: the top of the rack.')
        : !cam.ready ? 'Starting the camera.'
          : allGood ? (closeUp ? 'Ports level and sharp. Take it.' : 'Whole rack in the frame, straight on.')
            : !q.framed ? (closeUp ? 'Move closer so the ports fill the frame.' : 'Move closer so the rack fills the frame.')
              : !q.lit ? 'Find better light.'
                : 'Hold still so the picture is sharp.';
  const canShoot = cam.ready && !cam.error && (mode !== 'photo' || allGood);
  const shutter = async () => {
    if (mode === 'video') {
      if (cam.recording) { const f = await cam.stopRecording(); if (f) onVideo?.(f); } else cam.startRecording();
      return;
    }
    const f = await cam.capture();
    if (f) onShot?.(f);
  };
  return (
    <div className={styles.cam} role="dialog" aria-modal="true" aria-label={title ? `Camera, ${title}` : 'Camera'}>
      <div className={styles.camTop}>
        <button type="button" className={styles.camWord} onClick={() => (cam.recording ? null : onClose())}>Close</button>
        <span className={styles.camMode}>{title || MODE_WORD[mode]}</span>
        {mode === 'stitch' ? <button type="button" className={styles.camWord} onClick={onClose}>{shots ? `Done (${shots})` : 'Done'}</button> : <span className={styles.camWord} />}
      </div>
      <div className={styles.vf}>
        {!cam.error ? <video ref={cam.videoRef} className={styles.video} playsInline muted autoPlay /> : null}
        {!cam.error ? <div className={`${styles.guide} ${cam.recording ? styles.rec : ''} ${allGood && mode === 'photo' ? styles.good : ''}`} aria-hidden="true" /> : null}
        <p className={styles.hint} role="status">{hint}</p>
      </div>
      <div className={styles.camBottom}>
        <button
          type="button"
          className={`${styles.shutter} ${mode === 'video' ? styles.shutVideo : ''} ${cam.recording ? styles.shutRec : ''}`}
          onClick={shutter}
          disabled={!canShoot && !cam.recording}
          aria-label={mode === 'video' ? (cam.recording ? 'Stop recording' : 'Start recording') : 'Take photo'}
        />
      </div>
    </div>
  );
}

export default RackCamera;
