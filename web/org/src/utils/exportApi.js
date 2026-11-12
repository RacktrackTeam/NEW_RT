import { Capacitor } from '@capacitor/core';

/**
 * Putting a file in the person's hands: saved on a computer, on the share
 * sheet on a phone. The results page uses shareBlob for the picture of a
 * located port.
 */

/**
 * Put a file where the person can get at it.
 *
 * Returns the note to show, or null when the person dismissed the share sheet
 * themselves (that is not an error and should not read as one).
 *
 * A browser downloads it. A packaged app cannot: the WebView ignores blob:
 * URLs and <a download> entirely, and `navigator.share` in it will not take
 * files - so the app used to tell people "this phone cannot save files from
 * inside the app, open RackTrack on a computer", which is what the owner hit
 * on 23 September 2026 trying to download a file.
 *
 * It can, though. The file is written into the app's own documents with
 * Capacitor's Filesystem and then handed to the system share sheet by its
 * URI, which is how a phone saves to Files, mails it or sends it on. A note
 * pointing to a computer is the last resort, for a device where even that is
 * refused.
 */
export async function saveBlob(blob, name) {
  const native = Capacitor.isNativePlatform();

  if (!native) {
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    return { tone: 'good', text: `Saved ${name}.` };
  }

  // Android: into the phone's Download folder, where the Files app and the
  // Downloads list show it, and nothing else. It used to be written to the
  // app's documents and then handed to the share sheet every time, so a tester
  // pressing Download got a share sheet and no file they could find (tester
  // round, 24 September 2026). iOS keeps the share sheet below: on an iPhone
  // that IS how a file is saved to Files.
  if (Capacitor.getPlatform() === 'android') {
    const saved = await saveToDownloads(blob, name);
    if (saved) return { tone: 'good', text: `Saved to Download/${saved}.` };
  }

  // iPhone: into the app's own Documents, which the Files app shows under
  // On My iPhone > RackTrack (Info.plist turns that on). Download means
  // saved, not the share sheet (QA list, 5 October 2026).
  if (Capacitor.getPlatform() === 'ios') {
    try {
      const { Filesystem, Directory } = await import('@capacitor/filesystem');
      await Filesystem.writeFile({ path: name, data: await asBase64(blob), directory: Directory.Documents, recursive: true });
      return { tone: 'good', text: `Saved ${name} to Files, On My iPhone, RackTrack.` };
    } catch { /* fall back to the share sheet below */ }
  }

  // The file itself, written where the app may write, then shared by its URI.
  try {
    const { Filesystem, Directory } = await import('@capacitor/filesystem');
    const { Share } = await import('@capacitor/share');
    const data = await asBase64(blob);
    const write = await Filesystem.writeFile({
      path: name, data, directory: Directory.Documents, recursive: true,
    });
    try {
      await Share.share({ title: name, url: write.uri, dialogTitle: `Save ${name}` });
      return { tone: 'good', text: `${name} is ready - save it to Files or send it on.` };
    } catch {
      // They closed the share sheet. The file is still written, and saying so
      // is better than saying nothing happened.
      return { tone: 'good', text: `${name} is saved in the app's Documents.` };
    }
  } catch { /* the plugins are not in this build - try what the WebView offers */ }

  // Older builds: the share sheet, if this WebView will take a file at all.
  try {
    const file = new File([blob], name, { type: blob.type || 'application/octet-stream' });
    if (typeof navigator.share === 'function' && navigator.canShare && navigator.canShare({ files: [file] })) {
      try {
        await navigator.share({ files: [file], title: name });
        return { tone: 'good', text: `${name} is on the share sheet - save it to Files or send it on.` };
      } catch (e) {
        if (e && e.name === 'AbortError') return null;
      }
    }
  } catch { /* File or share unavailable - fall through */ }

  return {
    tone: 'bad',
    text: 'This phone would not save the file. Open RackTrack in a browser on a computer and download it from there - the same button is there.',
  };
}

/**
 * Write a file into Android's shared Download folder. Returns the name it was
 * saved under - with " (2)" and so on when that name is taken - or null when
 * the phone refused, and the caller falls back to the share sheet.
 *
 * Android 11 and later let an app create its own files there with no
 * permission. Android 10 and earlier need the storage permission, which is
 * asked for once, the first time.
 */
async function saveToDownloads(blob, name) {
  let Filesystem; let Directory;
  try { ({ Filesystem, Directory } = await import('@capacitor/filesystem')); } catch { return null; }
  const data = await asBase64(blob);
  const dot = name.lastIndexOf('.');
  const stem = dot > 0 ? name.slice(0, dot) : name;
  const ext = dot > 0 ? name.slice(dot) : '';
  const attempt = async () => {
    for (let n = 1; n <= 50; n += 1) {
      const candidate = n === 1 ? name : `${stem} (${n})${ext}`;
      const path = `Download/${candidate}`;
      let taken = false;
      try { await Filesystem.stat({ path, directory: Directory.ExternalStorage }); taken = true; } catch { taken = false; }
      if (taken) continue;
      try {
        await Filesystem.writeFile({ path, data, directory: Directory.ExternalStorage, recursive: true });
        return candidate;
      } catch (e) {
        // A file of that name left by an earlier install cannot be seen with
        // stat but cannot be written over either: try the next name.
        if (/exist/i.test(String((e && e.message) || ''))) continue;
        throw e;
      }
    }
    return null;
  };
  try {
    return await attempt();
  } catch {
    // Android 10 and earlier: ask for storage once, then try again.
    try {
      const p = await Filesystem.requestPermissions();
      if (p && p.publicStorage === 'granted') return await attempt();
    } catch { /* no permission to ask for on this version */ }
    return null;
  }
}

/**
 * Hand a file to the phone's own share sheet - WhatsApp, mail, Drive - with
 * nothing set up on our side. On a computer it is shared where the browser
 * can, and downloaded where it cannot.
 */
export async function shareBlob(blob, name) {
  if (Capacitor.isNativePlatform()) {
    const { Filesystem, Directory } = await import('@capacitor/filesystem');
    const { Share } = await import('@capacitor/share');
    const write = await Filesystem.writeFile({
      path: name, data: await asBase64(blob), directory: Directory.Cache, recursive: true,
    });
    try {
      await Share.share({ title: name, url: write.uri, dialogTitle: `Share ${name}` });
    } catch (e) {
      if (e && /cancel/i.test(e.message || '')) return null;
      throw e;
    }
    return { tone: 'good', text: `${name} shared.` };
  }
  const file = new File([blob], name, { type: blob.type || 'application/octet-stream' });
  if (typeof navigator.share === 'function' && navigator.canShare && navigator.canShare({ files: [file] })) {
    try {
      await navigator.share({ files: [file], title: name });
      return { tone: 'good', text: `${name} shared.` };
    } catch (e) {
      if (e && e.name === 'AbortError') return null;
    }
  }
  return saveBlob(blob, name);
}

/** A blob as base64, which is what Filesystem.writeFile takes. */
function asBase64(blob) {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onerror = () => reject(r.error || new Error('The file could not be read.'));
    r.onload = () => {
      const out = String(r.result || '');
      resolve(out.slice(out.indexOf(',') + 1));
    };
    r.readAsDataURL(blob);
  });
}
