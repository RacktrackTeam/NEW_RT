/* RT-EOS's Topology tab (2D diagram + 3D scene), unchanged, mounted inside the RackTrack app's Elevation tab.
   The component is RT-EOS client/src/pages/result/RackTopology.jsx; this file only gives it a router and a mount point,
   and names its generated class names, so the app can size the diagram and the scene to the phone's screen. */
import React from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import RackTopology from './src/RackTopology.jsx';
import styles from './src/RackTopology.module.css';
import { AnalysingView } from 'rteos/components/AnalysingView.jsx';

const roots = new WeakMap();
window.RTEOSTopology = {
  classes: { root: styles.root, top: styles.top, summary: styles.summary, view: styles.view, scene: styles.scene, hint: styles.hint, caption: styles.caption, note: styles.note, filtersToggle: styles.filtersToggle },
  mount(el, detail, onOpen) {
    let root = roots.get(el);
    if (!root) { root = createRoot(el); roots.set(el, root); }
    root.render(<MemoryRouter><RackTopology detail={detail} onOpen={onOpen} /></MemoryRouter>);
  },
};
// RT-EOS's Analysing screen (components/AnalysingView.jsx), exactly as it is: the video being read between two rails with a
// beam sweeping down it, the mark, the stage line. props = { racks: [{ key, word, files: File[], state }], finished, label }.
window.RTEOSAnalysing = {
  mount(el, props) {
    let root = roots.get(el);
    if (!root) { root = createRoot(el); roots.set(el, root); }
    root.render(<AnalysingView {...props} />);
  },
  unmount(el) { const root = roots.get(el); if (root) { root.unmount(); roots.delete(el); } },
};
