/*
 * The 3D rack of the Topology tab: RackTrack's topology scene (lazy-loaded by
 * RackTopology.jsx, so three.js is fetched only when someone opens the 3D
 * view). Changes from the original are only where the data here differs: a
 * port whose state was not read is not drawn as plugged in, a cable whose far
 * port is not a port read on the photo ends at the side of that device, and
 * the devices kept above the rack are "other ends" of cables, not assumed
 * uplinks.
 *
 * The look is a studio render of real hardware: an open-frame rack in light
 * grey on a warm off-white ground, charcoal satin devices with real faceplate
 * detail (recessed jacks in blocks of six, SFP cages, vent grilles, drive
 * caddies, mounting ears), a small white sticker with the device name, tiny
 * per-port LEDs (green connected, amber a problem, unlit empty) and cables as
 * shaded tubes with a plug boot that hang and run to the side cable managers.
 * All faceplate text and fine detail is painted into one small canvas texture
 * per device and the repeated parts (jacks, LEDs, caddies, plug boots) are
 * instanced, so the draw calls stay low for a phone WebView.
 */
import { useMemo, useRef, useState, useEffect, useLayoutEffect } from 'react';
import { Canvas, useFrame, useThree } from '@react-three/fiber';
import { OrbitControls, RoundedBox } from '@react-three/drei';
import * as THREE from 'three';
import Icon from '../../components/Icon.jsx';
import { RoomEnvironment } from 'three/examples/jsm/environments/RoomEnvironment.js';
import { mergeGeometries } from 'three/examples/jsm/utils/BufferGeometryUtils.js';

// ── Scene palettes ──────────────────────────────────────────────────────────
// Picked per the app's [data-theme] attribute. The app is light only, so both
// keys hold the same studio look; "dark" is kept so the hook never fails.
const STUDIO = {
  bg:            '#eaece7',  // warm off-white studio ground
  fog:           '#eaece7',
  shadow:        '#2a2e2c',  // soft contact shadow under the rack
  shadowOpacity:  0.17,
  hardware:      '#414644',  // charcoal satin chassis
  hardwarePanel: '#3a3e3d',
  hardwareServer:'#454a48',
  hardwareOther: '#a4a9a5',  // other ends: not hardware in this rack
  frame:         '#d3d5d0',  // rack posts
  frameDark:     '#bfc2bd',
  cap:           '#e1e3df',
  ear:           '#dcdedb',  // mounting ears
  manager:       '#383c3b',  // vertical cable managers
  sticker:       '#e8eae6',
  stickerInk:    '#1f2322',
  faceInk:       '#a8ada9',  // tiny print on a faceplate
  jackFrame:     '#8e9390',
  jackHole:      '#0b0d0c',
  jackRim:       '#b4b9b6',  // RJ45 shield
  cageMetal:     '#c7cbc9',  // SFP cage sheet metal
  moduleMetal:   '#d9dcdd',  // a fitted transceiver
  led:           '#69d287',  // connected
  ledProblem:    '#f0a63c',  // a problem on that port
  ledOff:        '#262b29',
  select:        '#d08a3c',  // selection band
  hover:         '#e0b27a',
  ink:           '#1f2322',
  inkSoft:       '#6a706c',
  cableJacket:   '#79807d',  // a cable whose type is not known
  envIntensity:   0.55,
  ambientBoost:   1.0,
};
export const SCENE_PALETTES = {
  dark: STUDIO,
  light: STUDIO,
};

// Read the current scene palette from the document theme. Re-evaluates
// when the theme attribute changes so the canvas swaps palette live.
export function useScenePalette() {
  const read = () => {
    const t = typeof document !== 'undefined'
      ? document.documentElement.getAttribute('data-theme')
      : 'dark';
    return SCENE_PALETTES[t === 'light' ? 'light' : 'dark'];
  };
  const [palette, setPalette] = useState(read);
  useEffect(() => {
    if (typeof MutationObserver === 'undefined') return;
    const mo = new MutationObserver(() => setPalette(read()));
    mo.observe(document.documentElement, {
      attributes: true, attributeFilter: ['data-theme'],
    });
    return () => mo.disconnect();
  }, []);
  return palette;
}

// Capacity → color for the capacity band (green plenty free, amber some, red
// little). Null means the state of the ports is not known.
function capacityColor(freePct) {
  if (freePct === null || freePct === undefined) return '#8a8f8c';
  if (freePct >= 0.5)  return '#1d7f47';
  if (freePct >= 0.25) return '#d39a2c';
  return '#c4553d';
}

const CLASS_LABEL = {
  switch:      'Switch',
  router:      'Router',
  patch_panel: 'Patch Panel',
  server:      'Server',
  firewall:    'Firewall',
  pdu:         'PDU',
  ups:         'UPS',
  unidentified: 'Not identified',
};

// Class names arrive from the detector as "Switch" / "Patch Panel" / "Server"
// - Title Case, with spaces. Normalise both sides instead of trusting casing.
const clsOf = (dev) => String(dev?.class ?? dev?.class_name ?? '')
  .trim().toLowerCase().replace(/[\s-]+/g, '_');

function classLabel(dev) {
  if (dev?.class_label) return dev.class_label;
  const c = clsOf(dev);
  if (CLASS_LABEL[c]) return CLASS_LABEL[c];
  const words = c.replace(/_/g, ' ').trim();
  return words ? words.charAt(0).toUpperCase() + words.slice(1) : 'Device';
}

function tierOf(dev) {
  if (!dev.in_rack && clsOf(dev) === 'switch') return 'core';
  if (clsOf(dev) === 'switch')                 return 'distribution';
  if (clsOf(dev) === 'patch_panel')            return 'access';
  if (clsOf(dev) === 'server')                 return 'endpoint';
  return null;
}

// World-unit constants
export const U_HEIGHT   = 0.42;
export const DEV_DEPTH  = 1.4;
export const DEV_WIDTH  = 3.0;
const FRONT_Z    = DEV_DEPTH / 2;
export const FOV_DEG    = 38;
const SHELF_GAP  = 0.5;   // U-units of empty space between top of rack and the other-ends shelf

// Rack frame geometry (x measured from the rack centre line).
const EAR_W      = 0.12;                    // mounting ear width
const POST_W     = 0.22;                    // front post face width
const POST_X     = DEV_WIDTH / 2 + POST_W / 2;
const MGR_X      = DEV_WIDTH / 2 + POST_W + 0.13;  // vertical cable manager centre
const MGR_W      = 0.24;
const FRAME_W    = 2 * (MGR_X + MGR_W / 2 + 0.01);
const FRAME_D    = DEV_DEPTH + 0.30;
const REAR_Z     = -DEV_DEPTH / 2 - 0.08;
const BOOT_LEN   = 0.075;                   // plug boot length out of a jack
const PPU        = 384;                     // faceplate texture pixels per world unit
const MONO       = 'ui-monospace, "SF Mono", Menlo, Consolas, "Liberation Mono", monospace';

// Convert an integer-or-half U position to a chassis-relative Y.
// uPos=1 is the bottom slot, uPos=chassisU is the top slot.
function uCenterY(uPos, sizeU, chassisU) {
  return -chassisU * U_HEIGHT / 2 + (uPos - 1 + sizeU / 2) * U_HEIGHT;
}

// ── Small canvas helpers (procedural textures, nothing downloaded) ─────────
function makeCanvas(w, h) {
  const c = document.createElement('canvas');
  c.width = Math.max(1, Math.round(w));
  c.height = Math.max(1, Math.round(h));
  return c;
}
function finishTexture(c, srgb = true) {
  const t = new THREE.CanvasTexture(c);
  if (srgb) t.colorSpace = THREE.SRGBColorSpace;
  t.anisotropy = 4;
  t.needsUpdate = true;
  return t;
}
function rr(g, x, y, w, h, r) {
  const q = Math.max(0, Math.min(r, w / 2, h / 2));
  g.beginPath();
  g.moveTo(x + q, y);
  g.lineTo(x + w - q, y);
  g.quadraticCurveTo(x + w, y, x + w, y + q);
  g.lineTo(x + w, y + h - q);
  g.quadraticCurveTo(x + w, y + h, x + w - q, y + h);
  g.lineTo(x + q, y + h);
  g.quadraticCurveTo(x, y + h, x, y + h - q);
  g.lineTo(x, y + q);
  g.quadraticCurveTo(x, y, x + q, y);
  g.closePath();
}
// Fit a line of text to a width, shrinking then cutting with "...".
function fitText(g, text, maxW, sizes, weight = '500') {
  for (const s of sizes) {
    g.font = `${weight} ${s}px ${MONO}`;
    if (g.measureText(text).width <= maxW) return text;
  }
  let t = text;
  while (t.length > 1 && g.measureText(`${t}...`).width > maxW) t = t.slice(0, -1);
  return `${t}...`;
}

let glowTexture = null;
function getGlowTexture() {
  if (glowTexture || typeof document === 'undefined') return glowTexture;
  const c = makeCanvas(64, 64);
  const g = c.getContext('2d');
  if (!g) return null;
  const gr = g.createRadialGradient(32, 32, 0, 32, 32, 32);
  gr.addColorStop(0, 'rgba(255,255,255,1)');
  gr.addColorStop(0.22, 'rgba(255,255,255,0.6)');
  gr.addColorStop(1, 'rgba(255,255,255,0)');
  g.fillStyle = gr;
  g.fillRect(0, 0, 64, 64);
  glowTexture = finishTexture(c, false);
  return glowTexture;
}

let shadowTexture = null;
function getShadowTexture() {
  if (shadowTexture || typeof document === 'undefined') return shadowTexture;
  const c = makeCanvas(128, 128);
  const g = c.getContext('2d');
  if (!g) return null;
  const gr = g.createRadialGradient(64, 64, 4, 64, 64, 64);
  gr.addColorStop(0, 'rgba(255,255,255,1)');
  gr.addColorStop(0.45, 'rgba(255,255,255,0.55)');
  gr.addColorStop(1, 'rgba(255,255,255,0)');
  g.fillStyle = gr;
  g.fillRect(0, 0, 128, 128);
  shadowTexture = finishTexture(c, false);
  return shadowTexture;
}

// A mounting ear: light grey with two screw holes per U.
const earTextures = new Map();
function getEarTexture(sizeU, pal) {
  const key = `${sizeU}|${pal.ear}`;
  if (earTextures.has(key)) return earTextures.get(key);
  if (typeof document === 'undefined') return null;
  const pxU = 96;
  const c = makeCanvas(28, pxU * sizeU);
  const g = c.getContext('2d');
  if (!g) return null;
  g.fillStyle = pal.ear;
  g.fillRect(0, 0, c.width, c.height);
  g.fillStyle = 'rgba(0,0,0,0.10)';
  g.fillRect(0, 0, 2, c.height);
  for (let u = 0; u < sizeU; u++) {
    for (const f of [0.26, 0.74]) {
      const cy = u * pxU + f * pxU;
      g.fillStyle = '#9da29e';
      g.beginPath(); g.arc(14, cy, 7, 0, Math.PI * 2); g.fill();
      g.fillStyle = '#4a4f4c';
      g.beginPath(); g.arc(14, cy, 4.6, 0, Math.PI * 2); g.fill();
      g.fillStyle = 'rgba(255,255,255,0.35)';
      g.fillRect(11, cy - 0.8, 6, 1.6);
    }
  }
  const t = finishTexture(c);
  earTextures.set(key, t);
  return t;
}

// A front post: the cage-nut hole strip, and the unit numbers on the left post.
function makePostStrip(chassisU, totalU, side, numbers, pal) {
  if (typeof document === 'undefined') return null;
  const pxU = Math.max(32, Math.min(128, Math.floor(4096 / Math.max(1, chassisU))));
  const Wpx = Math.round(pxU * (POST_W / U_HEIGHT));
  const c = makeCanvas(Wpx, pxU * chassisU);
  const g = c.getContext('2d');
  if (!g) return null;
  g.fillStyle = pal.frame;
  g.fillRect(0, 0, c.width, c.height);
  const inner = side === 'left' ? Wpx : 0;
  const dir = side === 'left' ? -1 : 1;
  const holeX = inner + dir * Wpx * (EAR_W / 2 / POST_W);
  const numX = inner + dir * Wpx * 0.76;
  const s = pxU * 0.11;
  // edge shading so the post reads as a folded profile
  g.fillStyle = 'rgba(0,0,0,0.07)';
  g.fillRect(side === 'left' ? 0 : Wpx - 3, 0, 3, c.height);
  g.fillStyle = 'rgba(255,255,255,0.35)';
  g.fillRect(side === 'left' ? Wpx - 2 : 0, 0, 2, c.height);
  g.textAlign = 'center';
  g.textBaseline = 'middle';
  g.font = `500 ${Math.round(pxU * 0.2)}px ${MONO}`;
  // Units counted from the bottom; the shelf above the rack may end on a half U.
  const top = (u) => (chassisU - u) * pxU;   // canvas y of the top of unit u
  for (let u = 1; u <= Math.floor(chassisU); u++) {
    for (let j = 0; j < 3; j++) {
      const cy = top(u) + (j + 0.5) * pxU / 3;
      g.fillStyle = '#a9ada8';
      g.fillRect(holeX - s / 2 - 1, cy - s / 2 - 1, s + 2, s + 2);
      g.fillStyle = '#3c413f';
      g.fillRect(holeX - s / 2, cy - s / 2, s, s);
    }
    g.fillStyle = 'rgba(0,0,0,0.13)';
    g.fillRect(side === 'left' ? 3 : Wpx * 0.5, top(u) + pxU - 1, Wpx * 0.45, 1);
    if (numbers && u <= totalU) {
      g.fillStyle = pal.inkSoft;
      g.fillText(String(u), numX, top(u) + pxU / 2);
    }
  }
  return finishTexture(c);
}

// ── Instanced boxes / planes ────────────────────────────────────────────────
// items: [x, y, z, sx, sy, sz, color?, rotZ?]. One draw call for the whole list.
const BOX_GEO = new THREE.BoxGeometry(1, 1, 1);
const PLANE_GEO = new THREE.PlaneGeometry(1, 1);
const _m4 = new THREE.Matrix4();
const _pos = new THREE.Vector3();
const _quat = new THREE.Quaternion();
const _scl = new THREE.Vector3();
const _col = new THREE.Color();
const _zAxis = new THREE.Vector3(0, 0, 1);

// ── Port geometry, built once and reused by every instance ─────────────────
// All are unit sized (x, y in -0.5..0.5, depth 1 centred on z 0) and scaled
// per port, so a jack of any size is the same geometry.
//
// RJ45: the keyed opening - a wide body with the narrower latch slot centred
// on one edge (up here; the bottom row turns it 180 degrees).
function keyedOutline(m) {
  const pts = [
    [-0.5 - m, -0.5 - m], [0.5 + m, -0.5 - m], [0.5 + m, 0.2 + m], [0.21 + m, 0.2 + m],
    [0.21 + m, 0.5 + m], [-0.21 - m, 0.5 + m], [-0.21 - m, 0.2 + m], [-0.5 - m, 0.2 + m],
  ];
  return pts.map(([x, y]) => new THREE.Vector2(x, y));
}
function extrudeUnit(shape) {
  const g = new THREE.ExtrudeGeometry(shape, { depth: 1, bevelEnabled: false, curveSegments: 1 });
  g.translate(0, 0, -0.5);
  return g;
}
// the dark opening
const RJ_HOLE_GEO = extrudeUnit(new THREE.Shape(keyedOutline(0)));
// the thin metal shield rim around it, same keyed outline
const RJ_RIM_GEO = (() => {
  const shape = new THREE.Shape(keyedOutline(0.13));
  shape.holes.push(new THREE.Path(keyedOutline(0).reverse()));
  return extrudeUnit(shape);
})();
// SFP cage: a thin sheet-metal frame, with the spring latch tab on its top edge
const SFP_CAGE_GEO = (() => {
  const o = [[-0.5, -0.5], [0.5, -0.5], [0.5, 0.5], [0.13, 0.5], [0.13, 0.64], [-0.13, 0.64], [-0.13, 0.5], [-0.5, 0.5]];
  const shape = new THREE.Shape(o.map(([x, y]) => new THREE.Vector2(x, y)));
  const ix = 0.43, iy = 0.38;
  shape.holes.push(new THREE.Path([
    new THREE.Vector2(-ix, -iy), new THREE.Vector2(-ix, iy), new THREE.Vector2(ix, iy), new THREE.Vector2(ix, -iy),
  ]));
  return extrudeUnit(shape);
})();
// A fitted transceiver: silver module face, its pull tab (bail) below, and
// the dark LC duplex receptacles (coloured per vertex, one draw call).
const SFP_MODULE_GEO = (() => {
  const tint = (g, hex) => {
    const c = new THREE.Color(hex);
    const n = g.attributes.position.count;
    const a = new Float32Array(n * 3);
    for (let i = 0; i < n; i++) { a[i * 3] = c.r; a[i * 3 + 1] = c.g; a[i * 3 + 2] = c.b; }
    g.setAttribute('color', new THREE.BufferAttribute(a, 3));
    return g;
  };
  const face = tint(new THREE.BoxGeometry(0.84, 0.7, 1), '#ffffff');
  const bail = tint(new THREE.BoxGeometry(0.5, 0.1, 1.6), '#ffffff');
  bail.translate(0, -0.27, 0.8);
  const lcL = tint(new THREE.BoxGeometry(0.26, 0.36, 0.2), '#161918');
  lcL.translate(-0.17, 0.06, 0.42);
  const lcR = tint(new THREE.BoxGeometry(0.26, 0.36, 0.2), '#161918');
  lcR.translate(0.17, 0.06, 0.42);
  const parts = [face, bail, lcL, lcR];
  const g = mergeGeometries(parts);
  parts.forEach((x) => x.dispose());
  return g;
})();

// The gold contacts at the back of an RJ45 opening: eight stripes.
let pinTexture = null;
function getPinTexture() {
  if (pinTexture || typeof document === 'undefined') return pinTexture;
  const c = makeCanvas(64, 8);
  const g = c.getContext('2d');
  if (!g) return null;
  g.fillStyle = '#15130d';
  g.fillRect(0, 0, 64, 8);
  for (let i = 0; i < 8; i++) {
    g.fillStyle = '#c9a54a';
    g.fillRect(3 + i * 7.5, 0, 4, 8);
  }
  pinTexture = finishTexture(c);
  return pinTexture;
}

function Instances({ items, geometry = BOX_GEO, children }) {
  const ref = useRef();
  useLayoutEffect(() => {
    const m = ref.current;
    if (!m) return;
    items.forEach((it, i) => {
      _pos.set(it[0], it[1], it[2]);
      _scl.set(it[3], it[4], it[5]);
      _quat.setFromAxisAngle(_zAxis, it[7] || 0);
      _m4.compose(_pos, _quat, _scl);
      m.setMatrixAt(i, _m4);
      if (it[6]) m.setColorAt(i, _col.set(it[6]));
    });
    m.instanceMatrix.needsUpdate = true;
    if (m.instanceColor) m.instanceColor.needsUpdate = true;
    m.computeBoundingSphere();
  }, [items]);
  if (!items.length) return null;
  return (
    <instancedMesh key={items.length} ref={ref} args={[geometry, undefined, items.length]} dispose={null}>
      {children}
    </instancedMesh>
  );
}

// A line of small mono text painted to a canvas and shown on a plane.
function LabelPlane({ text, height, color, bg = null, position, anchorX = 'center', opacity = 1, weight = '500', ...events }) {
  const made = useMemo(() => {
    if (typeof document === 'undefined' || !text) return null;
    const px = 64;
    const pad = bg ? px * 0.38 : px * 0.06;
    const probe = makeCanvas(8, 8).getContext('2d');
    if (!probe) return null;
    probe.font = `${weight} ${px}px ${MONO}`;
    const tw = Math.ceil(probe.measureText(text).width);
    const c = makeCanvas(tw + pad * 2, px * 1.45);
    const g = c.getContext('2d');
    if (bg) {
      g.fillStyle = bg;
      rr(g, 0, 0, c.width, c.height, px * 0.12);
      g.fill();
    }
    g.font = `${weight} ${px}px ${MONO}`;
    g.fillStyle = color;
    g.textBaseline = 'middle';
    g.fillText(text, pad, c.height / 2 + px * 0.04);
    return { tex: finishTexture(c), aspect: c.width / c.height };
  }, [text, color, bg, weight]);
  useEffect(() => () => made?.tex.dispose(), [made]);
  if (!made) return null;
  const w = height * made.aspect;
  const x = anchorX === 'left' ? w / 2 : anchorX === 'right' ? -w / 2 : 0;
  return (
    <group position={position} {...events}>
      <mesh position={[x, 0, 0]}>
        <planeGeometry args={[w, height]} />
        <meshBasicMaterial map={made.tex} transparent opacity={opacity} depthWrite={false} toneMapped={false} />
      </mesh>
    </group>
  );
}

// Render simplified ghost racks to either side if the topology JSON tells us
// about neighbors. Looks for either:
//   topo.neighbors = [{ side: 'left'|'right', name, u_size }]
// or  topo.crossRackEdges = [{ peerRack, count }] - in which case we synthesize
// generic ghost racks. If nothing is provided this component renders nothing.
function NeighborRacks({ topo, chassisU, pal }) {
  const neighbors = useMemo(() => {
    if (Array.isArray(topo.neighbors) && topo.neighbors.length) {
      return topo.neighbors.map((n, i) => ({
        side: n.side || (i % 2 === 0 ? 'left' : 'right'),
        name: n.name || `RACK-${i+1}`,
        uSize: n.u_size || 18,
        count: n.cable_count || 0,
      }));
    }
    if (Array.isArray(topo.crossRackEdges) && topo.crossRackEdges.length) {
      return topo.crossRackEdges.map((cr, i) => ({
        side: i % 2 === 0 ? 'left' : 'right',
        name: cr.peerRack,
        uSize: 18,
        count: cr.count || 0,
      }));
    }
    return [];
  }, [topo]);

  const edges = useMemo(() => {
    const totalH = chassisU * U_HEIGHT * 0.85 + 0.5;
    return new THREE.EdgesGeometry(new THREE.BoxGeometry(2.4, totalH, 1.5));
  }, [chassisU]);
  useEffect(() => () => edges.dispose(), [edges]);

  if (!neighbors.length) return null;

  const totalH = chassisU * U_HEIGHT * 0.85 + 0.5;
  const offsetX = 6.5;

  return (
    <group>
      {neighbors.map((n, i) => {
        const dir = n.side === 'left' ? -1 : 1;
        const x = dir * (offsetX + (i >> 1) * 4.5);
        const bottom = -chassisU * U_HEIGHT / 2 - 0.25;
        return (
          <group key={`${n.name}-${i}`} position={[x, bottom + totalH / 2, -1.0]}>
            <mesh>
              <boxGeometry args={[2.4, totalH, 1.5]} />
              <meshStandardMaterial color={pal.frame} roughness={0.7} metalness={0.05}
                                    transparent opacity={0.32} depthWrite={false} />
            </mesh>
            <lineSegments geometry={edges}>
              <lineBasicMaterial color="#a9ada8" transparent opacity={0.9} />
            </lineSegments>
            {/* Connecting run from the main rack toward this neighbor */}
            <mesh position={[-dir * (offsetX / 2 + 0.2), -totalH / 2 + 0.5, 1.0]}>
              <boxGeometry args={[Math.max(0.5, offsetX - 3.4), 0.025, 0.025]} />
              <meshBasicMaterial color={pal.inkSoft} transparent opacity={0.45} />
            </mesh>
            <LabelPlane text={n.name} height={0.24} color={pal.ink} bg={pal.sticker}
                        position={[0, totalH / 2 + 0.22, 0.76]} />
            {n.count > 0 && (
              <LabelPlane text={`${n.count} ${n.count === 1 ? 'cable' : 'cables'}`} height={0.17}
                          color={pal.inkSoft} position={[0, 0, 0.76]} />
            )}
          </group>
        );
      })}
    </group>
  );
}

// Bundled cable trunk that runs down the rear corner of the rack - visual
// only (the real per-cable runs are drawn from the front ports).
function CableArm({ side, totalH }) {
  const x = side === 'left' ? -(POST_X + 0.02) : POST_X + 0.02;
  const z = REAR_Z - 0.09;
  const armH = totalH - 0.4;
  const bandColors = ['#7d8380', '#9aa09c', '#6a706c'];
  return (
    <group position={[x, 0, z]}>
      <mesh>
        <cylinderGeometry args={[0.065, 0.065, armH, 12]} />
        <meshStandardMaterial color="#3a3f3d" metalness={0.05} roughness={0.75} />
      </mesh>
      {bandColors.map((c, i) => {
        const yPos = -armH/2 + (i + 1) * (armH / (bandColors.length + 1));
        return (
          <mesh key={i} position={[0, yPos, 0]}>
            <cylinderGeometry args={[0.072, 0.072, 0.04, 14]} />
            <meshStandardMaterial color={c} metalness={0.1} roughness={0.6} />
          </mesh>
        );
      })}
    </group>
  );
}

// Front vertical cable manager - a charcoal channel beside each front post
// with fingers every U; the per-port cables run down inside it (matches
// CHANNEL_X / CHANNEL_Z in PortCables).
function CableManagerFront({ side, chassisU, pal }) {
  const sign = side === 'left' ? -1 : 1;
  const railH = chassisU * U_HEIGHT + 0.3;
  const fingers = useMemo(() => {
    const out = [];
    for (let k = 0; k <= chassisU; k++) {
      const y = -chassisU * U_HEIGHT / 2 + k * U_HEIGHT;
      out.push([sign * (MGR_X - MGR_W / 2 + 0.02), y, FRONT_Z + 0.06, 0.04, 0.05, 0.2]);
    }
    return out;
  }, [chassisU, sign]);
  const mat = <meshStandardMaterial color={pal.manager} metalness={0.3} roughness={0.55} />;
  return (
    <group>
      {/* back plate */}
      <mesh position={[sign * MGR_X, 0, FRONT_Z - 0.05]}>
        <boxGeometry args={[MGR_W, railH, 0.02]} />
        {mat}
      </mesh>
      {/* outer wall */}
      <mesh position={[sign * (MGR_X + MGR_W / 2 - 0.01), 0, FRONT_Z + 0.06]}>
        <boxGeometry args={[0.02, railH, 0.24]} />
        {mat}
      </mesh>
      <Instances items={fingers}>{mat}</Instances>
    </group>
  );
}

// Animated swing-open hinge for a side panel (faint, so the open frame and
// its depth still read through it).
function SwingPanel({ side, totalH, totalD, totalW, open, onClick, pal }) {
  const ref = useRef();
  const target = open ? -Math.PI * 0.6 * (side === 'left' ? 1 : -1) : 0;
  useFrame(() => {
    if (!ref.current) return;
    ref.current.rotation.y += (target - ref.current.rotation.y) * 0.12;
  });
  // Hinge pivot is at the FRONT edge of the side, so the panel swings outward.
  const hingeX = side === 'left' ? -totalW/2 + 0.012 : totalW/2 - 0.012;
  const hingeZ = totalD/2 - 0.012;
  const panelLen = totalD - 0.024;
  return (
    <group ref={ref} position={[hingeX, 0, hingeZ]}>
      <mesh
        position={[0, 0, -panelLen/2]}
        rotation={[0, side === 'left' ? Math.PI/2 : -Math.PI/2, 0]}
        onClick={(e) => { e.stopPropagation(); onClick && onClick(); }}
      >
        <planeGeometry args={[panelLen, totalH - 0.04]} />
        <meshStandardMaterial
          color={pal.frame}
          metalness={0.1}
          roughness={0.35}
          transparent
          opacity={0.10}
          depthWrite={false}
          side={THREE.DoubleSide}
        />
      </mesh>
      <mesh position={[0, 0, -0.03]}>
        <boxGeometry args={[0.02, totalH - 0.06, 0.04]} />
        <meshStandardMaterial color={pal.frameDark} metalness={0.3} roughness={0.5} />
      </mesh>
    </group>
  );
}

// ── Rack chassis: open frame, posts with the cage-nut strip, unit numbers ──
// A tap on the frame (not a drag that ended on it) when the rack can be opened.
function tapProps(fn) {
  if (!fn) return {};
  return {
    onClick: (e) => { e.stopPropagation(); if ((e.delta ?? 0) > 8) return; fn(); },
    onPointerOver: (e) => { e.stopPropagation(); document.body.style.cursor = 'pointer'; },
    onPointerOut: () => { document.body.style.cursor = ''; },
  };
}

function RackChassis({ totalU, chassisU, rackName, hasShelf, leftOpen, rightOpen,
                      toggleLeft, toggleRight, pal, onRackClick = null }) {
  const tap = tapProps(onRackClick);
  const totalH = chassisU * U_HEIGHT + 0.5;
  const totalW = FRAME_W;
  const totalD = FRAME_D;
  const uSpan = chassisU * U_HEIGHT;

  const stripL = useMemo(() => makePostStrip(chassisU, totalU, 'left', true, pal), [chassisU, totalU, pal]);
  const stripR = useMemo(() => makePostStrip(chassisU, totalU, 'right', false, pal), [chassisU, totalU, pal]);
  useEffect(() => () => { stripL?.dispose(); stripR?.dispose(); }, [stripL, stripR]);

  const frameMat = <meshStandardMaterial color={pal.frame} metalness={0.25} roughness={0.5} />;
  const darkMat = <meshStandardMaterial color={pal.frameDark} metalness={0.25} roughness={0.55} />;

  // Y of the shelf separator line (between the top unit and the other-ends shelf)
  const shelfSeparatorY = -chassisU * U_HEIGHT / 2 + (totalU + SHELF_GAP / 2) * U_HEIGHT;

  return (
    <group>
      <group {...tap}>
      {/* Front posts: face with the cage-nut strip, and a folded side flange */}
      {[-1, 1].map((s) => (
        <group key={`fp${s}`}>
          <mesh position={[s * POST_X, 0, FRONT_Z - 0.037]}>
            <boxGeometry args={[POST_W, totalH, 0.05]} />
            {frameMat}
          </mesh>
          <mesh position={[s * POST_X, 0, FRONT_Z - 0.0115]}>
            <planeGeometry args={[POST_W, uSpan]} />
            <meshStandardMaterial map={s < 0 ? stripL : stripR} metalness={0.2} roughness={0.55} />
          </mesh>
          <mesh position={[s * (POST_X + POST_W / 2 - 0.01), 0, FRONT_Z - 0.13]}>
            <boxGeometry args={[0.02, totalH, 0.16]} />
            {frameMat}
          </mesh>
          {/* Rear post */}
          <mesh position={[s * POST_X, 0, REAR_Z]}>
            <boxGeometry args={[POST_W, totalH, 0.05]} />
            {frameMat}
          </mesh>
          <mesh position={[s * (POST_X + POST_W / 2 - 0.01), 0, REAR_Z + 0.07]}>
            <boxGeometry args={[0.02, totalH, 0.14]} />
            {darkMat}
          </mesh>
          {/* Depth rails, top and bottom */}
          {[1, -1].map((t) => (
            <mesh key={t} position={[s * (POST_X + POST_W / 2 - 0.02), t * (totalH / 2 - 0.06), (FRONT_Z + REAR_Z) / 2]}>
              <boxGeometry args={[0.04, 0.06, FRONT_Z - REAR_Z]} />
              {darkMat}
            </mesh>
          ))}
        </group>
      ))}

      {/* Top cap and base plinth */}
      <RoundedBox args={[totalW, 0.12, totalD + 0.06]} radius={0.025} smoothness={2}
                  position={[0, totalH / 2 + 0.06, 0]}>
        <meshStandardMaterial color={pal.cap} metalness={0.15} roughness={0.5} />
      </RoundedBox>
      <mesh position={[0, -totalH / 2 - 0.075, 0]}>
        <boxGeometry args={[totalW, 0.15, totalD]} />
        <meshStandardMaterial color={pal.frameDark} metalness={0.15} roughness={0.6} />
      </mesh>
      <mesh position={[0, -totalH / 2 + 0.02, 0]}>
        <boxGeometry args={[DEV_WIDTH + 2 * POST_W, 0.04, totalD - 0.1]} />
        {darkMat}
      </mesh>
      </group>

      {/* Side panels - clickable, swing open on click */}
      <SwingPanel side="left"  totalH={totalH} totalD={totalD} totalW={totalW}
                  open={leftOpen}  onClick={toggleLeft} pal={pal} />
      <SwingPanel side="right" totalH={totalH} totalD={totalD} totalW={totalW}
                  open={rightOpen} onClick={toggleRight} pal={pal} />

      {/* Other-ends shelf separator line + label */}
      {hasShelf && (
        <group>
          <mesh position={[0, shelfSeparatorY, FRONT_Z + 0.002]}>
            <planeGeometry args={[DEV_WIDTH, 0.012]} />
            <meshBasicMaterial color={pal.inkSoft} transparent opacity={0.55} />
          </mesh>
          <LabelPlane text="OTHER ENDS" height={0.085} color={pal.inkSoft}
                      position={[-DEV_WIDTH / 2 + 0.02, shelfSeparatorY + 0.09, FRONT_Z + 0.003]}
                      anchorX="left" />
        </group>
      )}

      {/* Rear cable trunks */}
      <CableArm side="left"  totalH={totalH} />
      <CableArm side="right" totalH={totalH} />

      {/* Front vertical cable managers the per-port cables route through */}
      <CableManagerFront side="left"  chassisU={chassisU} pal={pal} />
      <CableManagerFront side="right" chassisU={chassisU} pal={pal} />

      {/* Rack name on a small sticker on the front of the top cap */}
      {rackName ? (
        <LabelPlane text={rackName} height={0.085} color={pal.stickerInk} bg={pal.sticker}
                    position={[-totalW / 2 + 0.16, totalH / 2 + 0.06, (totalD + 0.06) / 2 + 0.002]}
                    anchorX="left" {...tap} />
      ) : null}
    </group>
  );
}

// ── Cable color by physical type ───────────────────────────────────────────
// Four jacket tones: copper white, fiber pale blue-grey, DAC charcoal, and a
// mid grey for a cable whose type is not known.
function cableColor(cable_type, fallback = '#79807d') {
  const t = (cable_type || '').toLowerCase();
  if (t.includes('dac') || t.includes('twinax'))                    return '#303634';
  if (t.includes('sm') || t.includes('single') || t.includes('os1') || t.includes('os2') ||
      t.includes('om') || t.includes('mm') ||
      t.includes('fiber') || t.includes('fibre'))                   return '#c3ced7';
  if (t.startsWith('cat'))                                          return '#eef0ed';
  return fallback;
}

// ── Port layout ─────────────────────────────────────────────────────────────
// Every port read on a device gets a place on its faceplate, the way the
// hardware is built:
//   - switches (and routers, firewalls, any device with ports): jacks in two
//     rows, grouped in blocks of six columns, SFP cages on the right
//   - patch panels: one row of jacks (two rows past 24), blocks of six
//   - servers: a two-column NIC cluster on the right
// Each entry: { x, y, pw, ph, kind, led:[x,y], num:[x,y], text, block }.
function layoutPorts(dev, h) {
  const out = new Map();
  const ports = dev.ports || [];
  if (!ports.length) return out;
  const cls = clsOf(dev);
  if (cls === 'outside') return out;
  const L = -DEV_WIDTH / 2, R = DEV_WIDTH / 2;
  const text = (p) => String(p.portNo ?? p.label ?? p.name);
  const put = (p, x, y, pw, ph, kind, block, below) => {
    const ly = below ? y - ph / 2 - 0.019 : y + ph / 2 + 0.019;
    out.set(p.name, {
      x, y, pw, ph, kind, block, below,
      led: [x + pw * 0.30, ly],
      num: [x - pw * 0.22, ly],
      text: text(p),
    });
  };

  if (cls === 'server') {
    const cols = [R - 0.215, R - 0.115];
    const rows = Math.ceil(ports.length / 2);
    const pitchY = Math.min(0.088, (h - 0.06) / Math.max(1, rows));
    const top = (rows - 1) * pitchY / 2;
    ports.forEach((p, i) => {
      put(p, cols[i % 2], top - Math.floor(i / 2) * pitchY, 0.062, Math.min(0.05, pitchY * 0.58),
        p.kind === 'sfp' ? 'sfp' : 'rj', 'nic', false);
    });
    return out;
  }

  const sfp  = ports.filter((p) => p.kind === 'sfp');
  const aux  = ports.filter((p) => p.kind === 'console' || p.kind === 'usb');
  const main = ports.filter((p) => p.kind !== 'sfp' && p.kind !== 'console' && p.kind !== 'usb');
  const isPanel = cls === 'patch_panel';
  const left = L + 0.82 + (aux.length ? 0.14 : 0);
  // QSFP / OSFP cages are wider, when the data names them
  const wide = (p) => /qsfp|osfp/i.test([p.type, p.connector, p.label, p.name].filter(Boolean).join(' '));
  const sfpPitch = sfp.some(wide) ? 0.175 : 0.13;
  const sfpCols = Math.ceil(sfp.length / 2);
  const right = R - 0.10 - (sfpCols ? sfpCols * sfpPitch + 0.06 : 0);
  const ph = Math.min(0.06, h * 0.16);
  const rowY = [ph / 2 + 0.014, -(ph / 2 + 0.014)];

  if (main.length) {
    const rows = (!isPanel || main.length > 24) ? 2 : 1;
    const cols = Math.max(1, Math.ceil(main.length / rows));
    const blocks = Math.ceil(cols / 6);
    const GAP = 0.6;  // gap between blocks, in pitches
    const pitch = Math.min(0.094, (right - left) / (cols + (blocks - 1) * GAP));
    const pw = Math.min(0.072, pitch * 0.80);
    const jh = Math.min(ph, pw * 0.86);
    main.forEach((p, i) => {
      const row = rows === 1 ? 0 : (i < cols ? 0 : 1);
      const col = rows === 1 ? i : (row === 0 ? i : i - cols);
      const x = left + (col + Math.floor(col / 6) * GAP) * pitch + pitch / 2;
      const y = rows === 1 ? -0.01 : rowY[row];
      put(p, x, y, pw, jh, 'rj', `m${Math.floor(col / 6)}`, rows === 2 && row === 1);
    });
  }
  sfp.forEach((p, i) => {
    const col = Math.floor(i / 2), row = i % 2;
    const x = right + 0.06 + col * sfpPitch + sfpPitch / 2;
    put(p, x, rowY[row], wide(p) ? 0.15 : 0.1, Math.min(0.05, ph * 0.84), 'sfp', 'sfp', row === 1);
  });
  aux.forEach((p, i) => {
    put(p, L + 0.88, rowY[0] - i * 0.1, 0.058, Math.min(0.05, ph * 0.84), 'rj', `a${i}`, false);
  });
  return out;
}

// Returns Map<portName, [relX, relY]> on the device front face.
// relX/relY are device-local (relative to device center). Caller adds centerY.
export function computePortPositions(dev, h) {
  const map = new Map();
  for (const [name, p] of layoutPorts(dev, h)) map.set(name, [p.x, p.y]);
  return map;
}

// Where the sticker, vent grille, drive caddies and grille dots sit on a face.
function faceRegions(cls, h, sizeU, layout) {
  const L = -DEV_WIDTH / 2, R = DEV_WIDTH / 2;
  const labelH = Math.min(0.11, h * 0.32);
  const label = {
    x0: L + 0.07, w: DEV_WIDTH * 0.22, h: labelH,
    y: h > 0.6 ? h / 2 - 0.05 - labelH / 2 : 0.035,
  };
  const out = { label, vent: null, caddies: [], dots: null, outlets: null };
  let px0 = Infinity, px1 = -Infinity;
  for (const p of layout.values()) {
    px0 = Math.min(px0, p.x - p.pw / 2);
    px1 = Math.max(px1, p.x + p.pw / 2);
  }
  if (cls === 'outside') return out;
  if (cls === 'server') {
    const end = layout.size ? px0 - 0.05 : R - 0.08;
    const cw = 0.72, gap = 0.012, cols = 4;
    const rows = Math.min(4, Math.max(1, Math.round(sizeU)));
    const ch = (h - 0.05 - (rows - 1) * gap) / rows;
    const w = (cw - (cols - 1) * gap) / cols;
    const cx0 = end - cw;
    for (let r = 0; r < rows; r++) {
      for (let c = 0; c < cols; c++) {
        out.caddies.push({ x: cx0 + c * (w + gap) + w / 2, y: h / 2 - 0.025 - r * (ch + gap) - ch / 2, w, h: ch });
      }
    }
    out.vent = { x0: L + 0.80, x1: cx0 - 0.06, y0: -h / 2 + 0.03, y1: h / 2 - 0.03 };
    return out;
  }
  if (!layout.size) {
    if (cls === 'pdu') out.outlets = { x0: L + 0.86, x1: R - 0.12 };
    else out.dots = { x0: L + 0.86, x1: R - 0.12, y0: -h / 2 + 0.04, y1: h / 2 - 0.04 };
    return out;
  }
  if (R - 0.08 - px1 > 0.3) out.dots = { x0: px1 + 0.1, x1: R - 0.08, y0: -h / 2 + 0.05, y1: h / 2 - 0.05 };
  return out;
}

// Paint a faceplate: lip, brushed lines, port-block recesses, tiny port
// numbers, vent slots or grille dots, PDU outlets, the name sticker and the
// short model text.
function drawFace(g, Wpx, Hpx, dev, cls, h, layout, regions, pal) {
  const X = (x) => (x + DEV_WIDTH / 2) * PPU;
  const Y = (y) => (h / 2 - y) * PPU;
  const S = (v) => v * PPU;

  g.clearRect(0, 0, Wpx, Hpx);
  // brushed finish and the lighter lip on top, darker seam below
  g.fillStyle = 'rgba(255,255,255,0.022)';
  for (let yy = 4; yy < Hpx - 3; yy += 3) g.fillRect(0, yy, Wpx, 1);
  g.fillStyle = 'rgba(255,255,255,0.16)';
  g.fillRect(0, 0, Wpx, 2);
  g.fillStyle = 'rgba(0,0,0,0.30)';
  g.fillRect(0, Hpx - 2, Wpx, 2);

  // port-block recesses
  const blocks = new Map();
  for (const p of layout.values()) {
    const b = blocks.get(p.block) || { x0: Infinity, x1: -Infinity, y0: Infinity, y1: -Infinity };
    b.x0 = Math.min(b.x0, p.x - p.pw / 2, p.num[0] - 0.02);
    b.x1 = Math.max(b.x1, p.x + p.pw / 2, p.led[0] + 0.015);
    b.y0 = Math.min(b.y0, p.y - p.ph / 2, p.led[1] - 0.012);
    b.y1 = Math.max(b.y1, p.y + p.ph / 2, p.led[1] + 0.012);
    blocks.set(p.block, b);
  }
  for (const b of blocks.values()) {
    g.fillStyle = 'rgba(0,0,0,0.24)';
    rr(g, X(b.x0 - 0.018), Y(b.y1 + 0.014), S(b.x1 - b.x0 + 0.036), S(b.y1 - b.y0 + 0.028), S(0.008));
    g.fill();
  }
  // port numbers
  g.fillStyle = pal.faceInk;
  g.textAlign = 'center';
  g.textBaseline = 'middle';
  for (const p of layout.values()) {
    const fs = Math.max(6, S(Math.min(0.025, p.pw * 0.40)));
    g.font = `500 ${fs}px ${MONO}`;
    g.fillText(p.text, X(p.num[0]), Y(p.num[1]));
  }

  // vent slots (servers)
  if (regions.vent) {
    const v = regions.vent;
    g.fillStyle = 'rgba(0,0,0,0.22)';
    rr(g, X(v.x0), Y(v.y1), S(v.x1 - v.x0), S(v.y1 - v.y0), S(0.008));
    g.fill();
    g.fillStyle = 'rgba(6,8,7,0.92)';
    const step = S(0.021);
    for (let x = X(v.x0) + step * 0.6; x < X(v.x1) - step * 0.4; x += step) {
      rr(g, x, Y(v.y1) + S(0.016), S(0.0085), S(v.y1 - v.y0 - 0.032), S(0.004));
      g.fill();
    }
  }
  // grille dots (fan intakes on switches, plain devices)
  if (regions.dots && regions.dots.x1 > regions.dots.x0) {
    const d = regions.dots;
    g.fillStyle = 'rgba(6,8,7,0.85)';
    const step = 0.03;
    let row = 0;
    for (let y = d.y1 - step / 2; y > d.y0; y -= step * 0.86, row++) {
      for (let x = d.x0 + (row % 2 ? step / 2 : 0); x < d.x1; x += step) {
        g.beginPath(); g.arc(X(x), Y(y), S(0.0068), 0, Math.PI * 2); g.fill();
      }
    }
  }
  // PDU outlets
  if (regions.outlets) {
    const o = regions.outlets;
    const oh = Math.min(0.14, h * 0.55), ow = 0.11, pitch = 0.17;
    for (let x = o.x0 + ow / 2; x + ow / 2 < o.x1; x += pitch) {
      g.fillStyle = '#171a19';
      rr(g, X(x - ow / 2), Y(oh / 2), S(ow), S(oh), S(0.014)); g.fill();
      g.fillStyle = '#5b605d';
      g.fillRect(X(x - 0.028), Y(0.03), S(0.012), S(0.035));
      g.fillRect(X(x + 0.016), Y(0.03), S(0.012), S(0.035));
      g.beginPath(); g.arc(X(x), Y(-0.03), S(0.008), 0, Math.PI * 2); g.fill();
    }
  }

  // the name sticker, small dark mono text, on the left of the face
  const lb = regions.label;
  g.fillStyle = 'rgba(0,0,0,0.35)';
  rr(g, X(lb.x0) + 1, Y(lb.y + lb.h / 2) + 2, S(lb.w), S(lb.h), S(0.008)); g.fill();
  g.fillStyle = pal.sticker;
  rr(g, X(lb.x0), Y(lb.y + lb.h / 2), S(lb.w), S(lb.h), S(0.008)); g.fill();
  const pad = S(0.022);
  const big = S(Math.min(0.056, lb.h * 0.52));
  const name = fitText(g, String(dev.name || ''), S(lb.w) - pad * 2,
    [big, big * 0.88, big * 0.76, big * 0.66, big * 0.58], '600');
  g.fillStyle = pal.stickerInk;
  g.textAlign = 'left';
  g.textBaseline = 'middle';
  g.fillText(name, X(lb.x0) + pad, Y(lb.y) + 1);

  // short model / kind text under the sticker
  const model = (cls === 'outside' || !dev.in_rack) ? classLabel(dev) : (dev.model || classLabel(dev));
  if (model) {
    const my = lb.y - lb.h / 2 - 0.036;
    if (my > -h / 2 + 0.02) {
      const ms = S(0.03);
      const t = fitText(g, String(model).toUpperCase(), S(DEV_WIDTH * 0.24), [ms, ms * 0.85], '500');
      g.fillStyle = cls === 'outside' ? pal.ink : pal.faceInk;
      g.fillText(t, X(lb.x0) + 1, Y(my));
    }
  }
}

// ── Device - used for both in-rack and other-ends shelf devices ────────────
function DeviceBox({ dev, uPos, sizeU, chassisU, color, dimmed, selected, isCore,
                     highlightedPorts, problemPorts, cabledPorts, pal,
                     onClick, onHoverIn, onHoverOut }) {
  const [hover, setHover] = useState(false);
  const y = uCenterY(uPos, sizeU, chassisU);
  const h = sizeU * U_HEIGHT * 0.92;
  const w = DEV_WIDTH;
  const d = DEV_DEPTH;
  const fz = d / 2;
  const cls = clsOf(dev);
  const isOutside = cls === 'outside' || (isCore && !(dev.ports || []).length);

  const bodyColor = isOutside ? pal.hardwareOther
    : cls === 'patch_panel' ? pal.hardwarePanel
    : cls === 'server' ? pal.hardwareServer
    : pal.hardware;

  const layout = useMemo(() => layoutPorts(dev, h), [dev, h]);
  const regions = useMemo(() => faceRegions(cls, h, sizeU, layout), [cls, h, sizeU, layout]);

  const face = useMemo(() => {
    if (typeof document === 'undefined') return null;
    const Wpx = Math.round(DEV_WIDTH * PPU);
    const Hpx = Math.max(16, Math.round(h * PPU));
    const c = makeCanvas(Wpx, Hpx);
    const g = c.getContext('2d');
    if (!g) return null;
    drawFace(g, Wpx, Hpx, dev, isOutside ? 'outside' : cls, h, layout, regions, pal);
    return finishTexture(c);
  }, [dev, cls, isOutside, h, layout, regions, pal]);
  useEffect(() => () => face?.dispose(), [face]);

  const earTex = useMemo(() => getEarTexture(Math.max(1, Math.round(sizeU)), pal), [sizeU, pal]);
  const ears = useMemo(() => [
    [-(w / 2 + EAR_W / 2), 0, fz - 0.006, EAR_W, h, 0.012],
    [ (w / 2 + EAR_W / 2), 0, fz - 0.006, EAR_W, h, 0.012],
  ], [w, h, fz]);

  // Ports: RJ45 jacks (shield rim, keyed opening, gold contacts at the back)
  // and SFP cages (metal frame with latch tab, dark inside, or a fitted
  // silver module when the port is in use). The bottom row turns 180 degrees
  // so its latch slot faces down, as on real switches and patch panels.
  const jacks = useMemo(() => {
    const rims = [], holes = [], pins = [], cages = [], cageHoles = [], modules = [];
    const byName = new Map((dev.ports || []).map((pt) => [pt.name, pt]));
    for (const [name, p] of layout) {
      const rot = p.below ? Math.PI : 0;
      if (p.kind === 'sfp') {
        cages.push([p.x, p.y, fz + 0.007, p.pw, p.ph, 0.014, null, rot]);
        cageHoles.push([p.x, p.y, fz + 0.002, p.pw * 0.86, p.ph * 0.76, 0.004]);
        if (byName.get(name)?.connected === true || cabledPorts?.has(name)) {
          modules.push([p.x, p.y, fz + 0.006, p.pw, p.ph, 0.012, null, rot]);
        }
      } else {
        rims.push([p.x, p.y, fz + 0.007, p.pw, p.ph, 0.014, null, rot]);
        holes.push([p.x, p.y, fz + 0.003, p.pw * 0.985, p.ph * 0.985, 0.006, null, rot]);
        // contacts on the side away from the latch slot
        const s = p.below ? 1 : -1;
        pins.push([p.x, p.y + s * p.ph * 0.33, fz + 0.0062, p.pw * 0.7, p.ph * 0.16, 0.0005]);
      }
    }
    return { rims, holes, pins, cages, cageHoles, modules };
  }, [dev, layout, fz, cabledPorts]);

  const lights = useMemo(() => {
    const leds = [], glows = [], rings = [];
    for (const port of dev.ports || []) {
      const p = layout.get(port.name);
      if (!p) continue;
      const problem = problemPorts?.has(port.name);
      const lit = problem ? pal.ledProblem : (port.connected === true ? pal.led : null);
      leds.push([p.led[0], p.led[1], fz + 0.002, 0.021, 0.011, 0.004, lit || pal.ledOff]);
      if (lit) glows.push([p.led[0], p.led[1], fz + 0.0045, 0.08, 0.055, 1, lit]);
      if (highlightedPorts?.has(port.name)) {
        rings.push([p.x, p.y, fz + 0.0045, p.pw + 0.036, p.ph + 0.034, 0.009]);
      }
    }
    return { leds, glows, rings };
  }, [dev, layout, fz, pal, problemPorts, highlightedPorts]);

  const caddies = useMemo(() => {
    const bodies = [], handles = [];
    for (const c of regions.caddies) {
      bodies.push([c.x, c.y, fz + 0.006, c.w, c.h, 0.012]);
      handles.push([c.x + c.w * 0.34, c.y, fz + 0.013, c.w * 0.14, c.h * 0.78, 0.004]);
    }
    return { bodies, handles };
  }, [regions, fz]);

  const fullW = w + 2 * EAR_W + 0.03;
  const outline = useMemo(() => {
    const plane = new THREE.PlaneGeometry(fullW, h + 0.012);
    const edges = new THREE.EdgesGeometry(plane);
    plane.dispose();
    return edges;
  }, [fullW, h]);
  useEffect(() => () => outline.dispose(), [outline]);
  const band = selected ? pal.select : (hover ? null : color);
  const bandOpacity = selected ? 0.38 : 0.30;

  return (
    <group position={[0, y, 0]}>
      {/* Chassis - charcoal satin with rounded edges that catch a light lip */}
      <RoundedBox
        args={[w, h, d]}
        radius={Math.min(0.016, h * 0.08)}
        smoothness={2}
        onClick={(e) => { e.stopPropagation(); onClick && onClick(); }}
        onPointerOver={(e) => { e.stopPropagation(); setHover(true); onHoverIn && onHoverIn(); }}
        onPointerOut={() => { setHover(false); onHoverOut && onHoverOut(); }}
      >
        <meshStandardMaterial color={bodyColor} metalness={0.3} roughness={0.5} />
      </RoundedBox>

      {/* Faceplate detail and print */}
      {face ? (
        <mesh position={[0, 0, fz + 0.0006]}>
          <planeGeometry args={[w - 0.012, h - 0.004]} />
          <meshStandardMaterial map={face} transparent metalness={0.15} roughness={0.55}
                                polygonOffset polygonOffsetFactor={-1} depthWrite={false} />
        </mesh>
      ) : null}

      {/* Mounting ears with two screws per U */}
      <Instances items={ears}>
        <meshStandardMaterial map={earTex} metalness={0.25} roughness={0.45} />
      </Instances>

      {/* RJ45 jacks: shield rim, keyed dark opening, gold contacts */}
      <Instances items={jacks.rims} geometry={RJ_RIM_GEO}>
        <meshStandardMaterial color={pal.jackRim} metalness={0.65} roughness={0.35} />
      </Instances>
      <Instances items={jacks.holes} geometry={RJ_HOLE_GEO}>
        <meshStandardMaterial color={pal.jackHole} metalness={0.1} roughness={0.9} />
      </Instances>
      <Instances items={jacks.pins} geometry={PLANE_GEO}>
        <meshStandardMaterial map={getPinTexture()} metalness={0.6} roughness={0.35} />
      </Instances>
      {/* SFP cages: sheet-metal frame with latch tab, dark inside, fitted modules */}
      <Instances items={jacks.cages} geometry={SFP_CAGE_GEO}>
        <meshStandardMaterial color={pal.cageMetal} metalness={0.75} roughness={0.3} />
      </Instances>
      <Instances items={jacks.cageHoles}>
        <meshStandardMaterial color={pal.jackHole} metalness={0.1} roughness={0.9} />
      </Instances>
      <Instances items={jacks.modules} geometry={SFP_MODULE_GEO}>
        <meshStandardMaterial color={pal.moduleMetal} vertexColors metalness={0.8} roughness={0.28} />
      </Instances>

      {/* Drive caddies (servers) */}
      <Instances items={caddies.bodies}>
        <meshStandardMaterial color="#4c5250" metalness={0.3} roughness={0.5} />
      </Instances>
      <Instances items={caddies.handles}>
        <meshStandardMaterial color="#737976" metalness={0.4} roughness={0.45} />
      </Instances>

      {/* Per-port LEDs and their soft glow */}
      <Instances items={lights.leds}>
        <meshBasicMaterial color="#ffffff" toneMapped={false} />
      </Instances>
      <Instances items={lights.glows} geometry={PLANE_GEO}>
        <meshBasicMaterial color="#ffffff" map={getGlowTexture()} transparent opacity={0.55}
                           blending={THREE.AdditiveBlending} depthWrite={false} toneMapped={false} />
      </Instances>
      {/* Ports with a cable to the selected device */}
      <Instances items={lights.rings}>
        <meshBasicMaterial color={pal.select} toneMapped={false} />
      </Instances>

      {/* Selection: a warm translucent band over the whole unit; capacity
          colouring uses the same band in its own colour */}
      {band ? (
        <mesh position={[0, 0, fz + 0.03]} renderOrder={2}>
          <planeGeometry args={[fullW, h + 0.012]} />
          <meshBasicMaterial color={band} transparent opacity={bandOpacity}
                             depthWrite={false} toneMapped={false} />
        </mesh>
      ) : null}
      {hover && !selected ? (
        <group position={[0, 0, fz + 0.03]}>
          <mesh renderOrder={2}>
            <planeGeometry args={[fullW, h + 0.012]} />
            <meshBasicMaterial color={pal.hover} transparent opacity={0.16}
                               depthWrite={false} toneMapped={false} />
          </mesh>
          <lineSegments geometry={outline}>
            <lineBasicMaterial color={pal.hover} />
          </lineSegments>
        </group>
      ) : null}

      {/* Dimmed: a veil in the ground colour rather than fading every part */}
      {dimmed ? (
        <mesh position={[0, 0, 0.012]} renderOrder={1}>
          <boxGeometry args={[fullW, h + 0.01, d + 0.045]} />
          <meshBasicMaterial color={pal.bg} transparent opacity={0.62} depthWrite={false} toneMapped={false} />
        </mesh>
      ) : null}
    </group>
  );
}

// Stable string-hash → 0..1 (used for tiny per-cable route randomization)
function hash01(s) {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return ((h >>> 0) % 10000) / 10000;
}

// Plugs at the cable ends, instanced across the whole scene. Each cable is
// { id, color, focused, hasFocus, ends: [[point, portInfo], ...] }.
// RJ45: the clear plug body in the jack, the boot (a little wider than the
// opening) with its latch tab on the slot side, and the strain relief.
// SFP: an LC duplex pair in the module and the two fibre strands that join
// the jacket. Background cables fade toward the ground.
function bootItems(cables, selCableId, pal) {
  const plug = [], body = [], tab = [], relief = [], lc = [], strands = [];
  const bg = new THREE.Color(pal.bg);
  const white = new THREE.Color('#ffffff');
  const z0 = FRONT_Z;
  for (const c of cables) {
    const isSel = selCableId === c.id;
    const col = new THREE.Color(isSel ? pal.select : c.color);
    if (c.hasFocus && !c.focused && !isSel) col.lerp(bg, 0.7);
    const hex = `#${col.getHexString()}`;
    const clear = `#${col.clone().lerp(white, 0.55).getHexString()}`;
    for (const [p, info] of c.ends) {
      const pw = info?.pw ?? 0.06, ph = info?.ph ?? 0.05;
      if (info?.kind === 'sfp') {
        for (const sx of [-1, 1]) {
          const x = p.x + sx * pw * 0.17;
          lc.push([x, p.y, z0 + 0.03, pw * 0.25, ph * 0.5, 0.036, hex]);
          strands.push([x - sx * 0.004, p.y, z0 + 0.066, 0.008, 0.008, 0.04, hex]);
        }
        relief.push([p.x, p.y, z0 + 0.088, 0.03, 0.022, 0.022, hex]);
        continue;
      }
      const dir = info?.below ? -1 : 1;           // latch slot up, or down
      const by = p.y - dir * ph * 0.12;           // centre of the wide body
      plug.push([p.x, by, z0 + 0.012, pw * 0.9, ph * 0.6, 0.02, clear]);
      body.push([p.x, by, z0 + 0.04, pw * 1.06, ph * 0.8, 0.05, hex]);
      tab.push([p.x, p.y + dir * ph * 0.36, z0 + 0.034, pw * 0.36, ph * 0.2, 0.04, hex]);
      relief.push([p.x, by, z0 + 0.079, pw * 0.58, ph * 0.5, 0.03, hex]);
    }
  }
  return { plug, body, tab, relief, lc, strands };
}

function BootMeshes({ boots }) {
  return (
    <>
      <Instances items={boots.plug}>
        <meshStandardMaterial color="#ffffff" metalness={0} roughness={0.25} />
      </Instances>
      <Instances items={boots.body}>
        <meshStandardMaterial color="#ffffff" metalness={0} roughness={0.45} />
      </Instances>
      <Instances items={boots.tab}>
        <meshStandardMaterial color="#ffffff" metalness={0} roughness={0.45} />
      </Instances>
      <Instances items={boots.lc}>
        <meshStandardMaterial color="#ffffff" metalness={0} roughness={0.4} />
      </Instances>
      <Instances items={boots.strands}>
        <meshStandardMaterial color="#ffffff" metalness={0} roughness={0.5} />
      </Instances>
      <Instances items={boots.relief}>
        <meshStandardMaterial color="#ffffff" metalness={0} roughness={0.55} />
      </Instances>
    </>
  );
}

// ── Selection-driven port-accurate cables ─────────────────────────────────
// Every cable is drawn from its exact port to its exact far port: out of the
// jack through a plug boot, hanging under its own weight, into the side cable
// manager, along it, and back out to the far port. Jacket colour by type.
function PortCables({ selected, topo, positions, traceEdgeKeys, setSelected, cableJacketColor = '#79807d', pal }) {
  const cables = useMemo(() => {
    const out = [];
    const traceMode = traceEdgeKeys && traceEdgeKeys.size > 0;
    const selDev    = selected?.kind === 'node' ? selected.id : null;
    // When a single cable is selected, render the whole trunk between the
    // two devices it spans - so the user sees its siblings + the chosen one.
    const selCable  = selected?.kind === 'cable'
      ? topo.edges.find(e => e.cable_id === selected.id)
      : null;
    const trunkPair = selCable
      ? [selCable.src.device, selCable.dst.device].sort().join('::')
      : null;
    const hasFocus = !!(traceMode || selDev || trunkPair);

    const CHANNEL_X = MGR_X;                 // x of the side cable manager
    const CHANNEL_Z = FRONT_Z + 0.07;        // depth at which cables run in it
    const PORT_Z    = FRONT_Z + 0.004 + BOOT_LEN;  // where the jacket leaves the boot

    for (const e of topo.edges) {
      const a = e.src.device, b = e.dst.device;
      const key = a < b ? `${a}::${b}` : `${b}::${a}`;
      const inTrace = traceMode && traceEdgeKeys.has(key);
      const inSel   = !traceMode && selDev && (a === selDev || b === selDev);
      const inTrunk = trunkPair && key === trunkPair;
      const focused = inTrace || inSel || inTrunk;

      // In trace mode the user is asking for a *single* path - hide the rest.
      if (traceMode && !focused) continue;

      const srcDev = positions.get(a);
      const dstDev = positions.get(b);
      if (!srcDev || !dstDev) continue;

      // A port that is not one read on the photo has no place on the
      // faceplate: the cable ends at the side of the device instead.
      const EDGE_REL = [DEV_WIDTH * 0.46, 0];
      const srcRel = srcDev.portMap?.get(e.src.port) || EDGE_REL;
      const dstRel = dstDev.portMap?.get(e.dst.port) || EDGE_REL;

      const startV = new THREE.Vector3(srcRel[0], srcDev.centerY + srcRel[1], PORT_Z);
      const endV   = new THREE.Vector3(dstRel[0], dstDev.centerY + dstRel[1], PORT_Z);

      // Routing side: both ports on one half → that side's manager; else a
      // stable per-cable hash so a trunk fans between both managers.
      const sideThresh = DEV_WIDTH * 0.10;
      let sideDir;
      if (srcRel[0] < -sideThresh && dstRel[0] < -sideThresh)      sideDir = -1;
      else if (srcRel[0] > sideThresh && dstRel[0] > sideThresh)   sideDir = 1;
      else  sideDir = hash01(e.cable_id + 'side') >= 0.5 ? 1 : -1;

      const jx = (hash01(e.cable_id + 'x') - 0.5) * 0.12;
      const jz = (hash01(e.cable_id + 'z') - 0.5) * 0.07;
      const chX = sideDir * CHANNEL_X + jx;
      const chZ = CHANNEL_Z + jz;
      const sag0 = 0.035 + hash01(e.cable_id + 'd') * 0.035;

      // One leg: out of the boot, bending down a little under its weight,
      // then across the faceplate with a shallow sag into the manager.
      const leg = (v) => {
        const dx = chX - v.x;
        const sag1 = sag0 + Math.min(0.07, Math.abs(dx) * 0.03);
        const zr = (v.z + chZ) / 2 + 0.02;
        return [
          v,
          new THREE.Vector3(v.x, v.y - 0.01, v.z + 0.03),
          new THREE.Vector3(v.x + dx * 0.08, v.y - sag0, zr),
          new THREE.Vector3(v.x + dx * 0.55, v.y - sag1, zr),
          new THREE.Vector3(chX, v.y - sag0 * 0.6, chZ),
        ];
      };
      const legA = leg(startV);
      const legB = leg(endV).reverse();
      const dy = Math.abs(endV.y - startV.y);
      const midY = (legA[4].y + legB[0].y) / 2 - Math.min(0.06, dy * 0.015);
      const points = dy > 0.2
        ? [...legA, new THREE.Vector3(chX, midY, chZ + Math.min(0.03, dy * 0.008)), ...legB]
        : [...legA.slice(0, 4), new THREE.Vector3(chX, midY - 0.05, chZ), ...legB.slice(1)];

      const curve = new THREE.CatmullRomCurve3(points, false, 'centripetal');
      out.push({
        edge: e, curve, color: cableColor(e.cable_type, cableJacketColor),
        focused, hasFocus, startV, endV,
        id: e.cable_id,
        ends: [[startV, srcDev.portInfo?.get(e.src.port) || null], [endV, dstDev.portInfo?.get(e.dst.port) || null]],
      });
    }
    return out;
  }, [selected, topo, positions, traceEdgeKeys, cableJacketColor]);

  const selCableId = selected?.kind === 'cable' ? selected.id : null;

  const boots = useMemo(() => bootItems(cables, selCableId, pal), [cables, selCableId, pal]);

  if (!cables.length) return null;

  return (
    <group>
      {cables.map((c) => {
        const isSelected = selCableId === c.edge.cable_id;
        let radius, opacity, transparent;
        if (isSelected) {
          radius = 0.024; opacity = 1;    transparent = false;
        } else if (c.focused) {
          radius = 0.019; opacity = 1;    transparent = false;
        } else if (c.hasFocus) {
          radius = 0.013; opacity = 0.25; transparent = true;
        } else {
          radius = 0.017; opacity = 1;    transparent = false;
        }
        return (
          <mesh
            key={c.edge.cable_id}
            onClick={(e) => {
              e.stopPropagation();
              setSelected && setSelected({ kind: 'cable', id: c.edge.cable_id });
            }}
            onPointerOver={(e) => { e.stopPropagation(); document.body.style.cursor = 'pointer'; }}
            onPointerOut={() => { document.body.style.cursor = ''; }}
          >
            <tubeGeometry args={[c.curve, 64, radius, 8, false]} />
            <meshStandardMaterial
              color={isSelected ? pal.select : c.color}
              metalness={0}
              roughness={0.55}
              transparent={transparent}
              opacity={opacity}
              depthWrite={!transparent}
            />
          </mesh>
        );
      })}
      <BootMeshes boots={boots} />
    </group>
  );
}

// ── Lights: soft, even studio light, no shadow maps ────────────────────────
function Lights({ chassisU, ambientBoost = 1 }) {
  const topY = chassisU * U_HEIGHT / 2 + 2;
  return (
    <>
      <hemisphereLight args={['#ffffff', '#d4d6d0', 0.9 * ambientBoost]} />
      <ambientLight intensity={0.25 * ambientBoost} />
      {/* key from front, above */}
      <directionalLight position={[3, topY + 4, 12]} intensity={1.5} color="#ffffff" />
      {/* fill from the left */}
      <directionalLight position={[-9, 3, 6]} intensity={0.45} color="#f4f2ee" />
      {/* rim from behind so the frame separates from the ground */}
      <directionalLight position={[2, topY, -10]} intensity={0.5} color="#ffffff" />
    </>
  );
}

// A procedural studio environment for the satin reflections (no download).
function StudioEnvironment({ intensity = 0.55 }) {
  const { gl, scene } = useThree();
  useEffect(() => {
    const pm = new THREE.PMREMGenerator(gl);
    const room = new RoomEnvironment();
    const env = pm.fromScene(room, 0.04).texture;
    scene.environment = env;
    scene.environmentIntensity = intensity;
    room.traverse((o) => { if (o.geometry) o.geometry.dispose(); if (o.material) o.material.dispose(); });
    return () => {
      if (scene.environment === env) scene.environment = null;
      env.dispose();
      pm.dispose();
    };
  }, [gl, scene, intensity]);
  return null;
}

// Front-on, a little to the right and above, so the depth shows.
const VIEW_DIR = [0.16, 0.2, 1];
// Two racks: straight on, so both stand visibly on one floor.
const PAIR_VIEW_DIR = [0, 0.18, 1];

// Frame the scene once, front-on with a slight downward tilt, fitted to the
// canvas (a phone is width-bound, a laptop height-bound). The framing is kept
// as "home" for the double tap.
function FitCamera({ height, width, depth = FRAME_D, view = VIEW_DIR }) {
  const { camera, size, controls } = useThree();
  const done = useRef(false);
  useLayoutEffect(() => {
    if (done.current || !size.width || !size.height) return;
    done.current = true;
    const aspect = size.width / size.height;
    const vf = (camera.fov * Math.PI) / 180;
    const hf = 2 * Math.atan(Math.tan(vf / 2) * aspect);
    const dist = Math.max((height / 2) / Math.tan(vf / 2), (width / 2) / Math.tan(hf / 2)) * 1.04 + depth / 2;
    const dir = new THREE.Vector3(...view).normalize();
    camera.position.copy(dir.multiplyScalar(dist));
    camera.lookAt(0, 0, 0);
    camera.updateProjectionMatrix();
    camera.userData.home = { pos: camera.position.clone(), target: new THREE.Vector3(0, 0, 0) };
    if (controls) controls.update();
  }, [camera, size, controls, height, width, depth, view]);
  return null;
}

// Zoom that works everywhere: the page never takes a pinch over the canvas
// (Safari and WKWebView send their own gesture events), the +/- buttons ease
// the camera in or out, and a double tap flies to the point tapped, or back
// home when already close.
function CameraDirector({ apiRef }) {
  const { camera, controls, gl, scene, events } = useThree();
  const goal = useRef(null);
  const ray = useMemo(() => new THREE.Raycaster(), []);

  useFrame(() => {
    const g = goal.current;
    if (!g || !controls) return;
    camera.position.lerp(g.pos, 0.18);
    controls.target.lerp(g.target, 0.18);
    controls.update();
    if (camera.position.distanceTo(g.pos) < 0.005 && controls.target.distanceTo(g.target) < 0.005) goal.current = null;
  });

  useEffect(() => {
    if (!controls) return undefined;
    const stop = () => { goal.current = null; };
    controls.addEventListener('start', stop);
    return () => controls.removeEventListener('start', stop);
  }, [controls]);

  useEffect(() => {
    const clampLen = (len) => Math.min(controls?.maxDistance ?? 60, Math.max(controls?.minDistance ?? 1, len));
    const zoomBy = (f) => {
      if (!controls) return;
      const t = controls.target.clone();
      const dir = camera.position.clone().sub(t);
      const len = clampLen(dir.length() * f);
      goal.current = { pos: t.clone().add(dir.normalize().multiplyScalar(len)), target: t };
    };
    const home = () => {
      const h = camera.userData.home;
      if (h) goal.current = { pos: h.pos.clone(), target: h.target.clone() };
    };
    const doubleTap = (clientX, clientY) => {
      if (!controls) return;
      const h = camera.userData.home;
      const len = camera.position.distanceTo(controls.target);
      const homeLen = h ? h.pos.distanceTo(h.target) : len;
      if (len < homeLen * 0.5) { home(); return; }
      const r = gl.domElement.getBoundingClientRect();
      const ndc = new THREE.Vector2(((clientX - r.left) / r.width) * 2 - 1, -((clientY - r.top) / r.height) * 2 + 1);
      ray.setFromCamera(ndc, camera);
      const hit = ray.intersectObjects(scene.children, true)
        .find((x) => x.object.visible && !(x.object.material?.transparent && (x.object.material.opacity ?? 1) < 0.3));
      if (!hit) { home(); return; }
      const dir = camera.position.clone().sub(controls.target).normalize();
      const to = clampLen(Math.max(len * 0.4, 2.2));
      goal.current = { pos: hit.point.clone().add(dir.multiplyScalar(to)), target: hit.point.clone() };
    };
    if (apiRef) apiRef.current = { zoomBy, home };

    const el = events?.connected || gl.domElement;
    const canvas = gl.domElement;
    el.style.touchAction = 'none';
    canvas.style.touchAction = 'none';
    const prevent = (e) => { e.preventDefault(); };
    const pinchOnly = (e) => { if (e.touches && e.touches.length > 1) e.preventDefault(); };
    let last = null;
    let down = null;
    const onDown = (e) => { down = { x: e.clientX, y: e.clientY }; };
    const onUp = (e) => {
      if (e.pointerType === 'mouse') return;
      const moved = down ? Math.hypot(e.clientX - down.x, e.clientY - down.y) : 0;
      if (moved > 12) { last = null; return; }
      const now = performance.now();
      if (last && now - last.t < 320 && Math.hypot(e.clientX - last.x, e.clientY - last.y) < 30) {
        last = null;
        doubleTap(e.clientX, e.clientY);
      } else {
        last = { t: now, x: e.clientX, y: e.clientY };
      }
    };
    const onDbl = (e) => doubleTap(e.clientX, e.clientY);
    for (const n of ['gesturestart', 'gesturechange', 'gestureend']) el.addEventListener(n, prevent, { passive: false });
    el.addEventListener('touchmove', pinchOnly, { passive: false });
    el.addEventListener('pointerdown', onDown);
    el.addEventListener('pointerup', onUp);
    el.addEventListener('dblclick', onDbl);
    return () => {
      for (const n of ['gesturestart', 'gesturechange', 'gestureend']) el.removeEventListener(n, prevent);
      el.removeEventListener('touchmove', pinchOnly);
      el.removeEventListener('pointerdown', onDown);
      el.removeEventListener('pointerup', onUp);
      el.removeEventListener('dblclick', onDbl);
      if (apiRef) apiRef.current = null;
    };
  }, [camera, controls, gl, scene, events, ray, apiRef]);
  return null;
}

// Two small round buttons in a corner of a 3D view: zoom in and out.
const ZOOM_BTN = {
  width: 34, height: 34, padding: 0, borderRadius: '50%',
  border: '1px solid #d3d6d1', background: 'rgba(255,255,255,0.94)', color: '#1f2322',
  fontSize: 19, display: 'grid', placeItems: 'center', fontFamily: 'inherit', cursor: 'pointer',
  boxShadow: '0 1px 2px rgba(0,0,0,0.06)',
};
function ZoomButtons({ apiRef }) {
  return (
    <div style={{ position: 'absolute', right: 10, bottom: 10, display: 'flex', flexDirection: 'column', gap: 8, zIndex: 2 }}>
      <button type="button" aria-label="Zoom in" style={ZOOM_BTN} onClick={() => apiRef.current?.zoomBy(0.68)}><Icon name="zoom_in" /></button>
      <button type="button" aria-label="Zoom out" style={ZOOM_BTN} onClick={() => apiRef.current?.zoomBy(1.45)}><Icon name="zoom_out" /></button>
    </div>
  );
}

// The canvas both 3D views share: studio ground, light and orbit controls
// (one finger turns, two fingers pinch to zoom and pan; wheel or trackpad on
// a laptop), with the zoom buttons over it.
function SceneCanvas({ height, width, maxDistance, onMissed, palette, view = VIEW_DIR, children }) {
  const api = useRef(null);
  const fovRad = (FOV_DEG * Math.PI) / 180;
  const camDist = Math.max(7.5, (height * 1.1) / (2 * Math.tan(fovRad / 2)));
  return (
    <div style={{ position: 'relative', width: '100%', height: '100%' }}>
      <Canvas
        shadows={false}
        camera={{ position: [camDist * 0.16, camDist * 0.2, camDist], fov: FOV_DEG, near: 0.1, far: 200 }}
        gl={{ antialias: true, alpha: false, powerPreference: 'high-performance' }}
        dpr={[1, 2]}
        onCreated={({ gl }) => { gl.toneMapping = THREE.NeutralToneMapping; gl.toneMappingExposure = 1.0; }}
        onPointerMissed={onMissed}
        style={{ touchAction: 'none' }}
      >
        <color attach="background" args={[palette.bg]} />
        <fog attach="fog" args={[palette.fog, 50, 140]} />
        <StudioEnvironment intensity={palette.envIntensity} />
        <Lights chassisU={height / U_HEIGHT} ambientBoost={palette.ambientBoost} />
        {children}
        <OrbitControls
          target={[0, 0, 0]}
          enablePan
          enableZoom
          enableDamping
          dampingFactor={0.08}
          zoomSpeed={1.1}
          minDistance={1.6}
          maxDistance={Math.max(maxDistance, camDist * 1.8)}
          minPolarAngle={0.25}
          maxPolarAngle={Math.PI - 0.25}
          makeDefault
        />
        <FitCamera height={height} width={width} view={view} />
        <CameraDirector apiRef={api} />
        <AutoPanCursor />
      </Canvas>
      <ZoomButtons apiRef={api} />
    </div>
  );
}

// Switches the canvas to "hand pan" mode automatically when the user is zoomed
// in past a threshold - drag pans, cursor becomes a grab hand. When zoomed
// out, drag returns to orbit. Mounts inside <Canvas> so it has access to the
// active OrbitControls (via makeDefault).
function AutoPanCursor({ panThreshold = 9 }) {
  const { camera, controls, gl } = useThree();
  const lastZoom = useRef(null);
  useFrame(() => {
    if (!controls?.target) return;
    const d = camera.position.distanceTo(controls.target);
    const zoomed = d < panThreshold;
    if (zoomed === lastZoom.current) return;
    lastZoom.current = zoomed;
    if (zoomed) {
      controls.mouseButtons.LEFT  = THREE.MOUSE.PAN;
      controls.mouseButtons.RIGHT = THREE.MOUSE.ROTATE;
      controls.touches.ONE = THREE.TOUCH.PAN;
      controls.touches.TWO = THREE.TOUCH.DOLLY_ROTATE;
      gl.domElement.style.cursor = 'grab';
    } else {
      controls.mouseButtons.LEFT  = THREE.MOUSE.ROTATE;
      controls.mouseButtons.RIGHT = THREE.MOUSE.PAN;
      controls.touches.ONE = THREE.TOUCH.ROTATE;
      controls.touches.TWO = THREE.TOUCH.DOLLY_PAN;
      gl.domElement.style.cursor = '';
    }
  });
  return null;
}

// Pure layout: turn a topology JSON into per-device positions, chassis size,
// and the "has other-ends shelf" flag. Module-scope so single- and multi-rack
// scenes share one source of truth.
export function computeRackLayout(topo, totalU) {
  const map    = new Map();
  const inRack = [];
  const cores  = [];
  for (const d of topo.devices) {
    // Never drop a device - unknown classes (PDU, UPS, unidentified, …) still
    // get placed at their real U position.
    const tier = tierOf(d) || 'endpoint';
    if (d.in_rack) inRack.push({ d, tier });
    else           cores.push({ d, tier });
  }
  const coreCount = cores.length;
  const shelfU    = coreCount > 0 ? coreCount + SHELF_GAP : 0;
  const chassisU  = totalU + shelfU;

  for (const { d, tier } of inRack) {
    const u  = d.u_position || 1;
    const sz = d.u_size     || 1;
    const cy = uCenterY(u, sz, chassisU);
    const h  = sz * U_HEIGHT * 0.92;
    map.set(d.name, {
      uPos: u, sizeU: sz, isCore: false,
      centerY: cy,
      attach: [0, cy, FRONT_Z + 0.02],
      portMap: computePortPositions(d, h),
      portInfo: layoutPorts(d, h),
      tier, dev: d,
    });
  }
  cores.forEach(({ d, tier }, i) => {
    // First other end sits at u = totalU + 1 + SHELF_GAP (e.g. 19.5),
    // each subsequent one stacked one U above.
    const u  = totalU + 1 + SHELF_GAP + i;
    const sz = 1;
    const cy = uCenterY(u, sz, chassisU);
    const h  = sz * U_HEIGHT * 0.92;
    map.set(d.name, {
      uPos: u, sizeU: sz, isCore: true,
      centerY: cy,
      attach: [0, cy, FRONT_Z + 0.02],
      portMap: computePortPositions(d, h),
      portInfo: layoutPorts(d, h),
      tier, dev: d,
    });
  });
  return { positions: map, chassisU, hasShelf: coreCount > 0 };
}

// A cable status that means something is wrong on that port.
const isProblem = (s) => !!s && /missing|wrong|mismatch|bad|error|fault|conflict/i.test(String(s));

// One rack's scene contents - chassis + devices + cables + neighbor ghosts -
// inside a positioned <group>, drawn in this file's single-rack <Canvas>.
export function RackContent({
  topo, xOffset = 0,
  selected, setSelected, heatmap, freePctByDevice,
  traceMode, traceA, traceB, tracePathSet, traceEdgeKeys,
  onHoverDevice,
  showNeighbors = true,
  showFloorPool = true,
  // World-Y of the shared scene floor. When a multi-rack scene packs
  // several RackContent instances together, every rack's bottom should
  // land on the SAME floor - but each rack's chassisU may differ. When a
  // parent scene passes in floorY, we compute a group-Y offset so
  // rack-bottom = floorY + clearance regardless of chassisU.
  floorY = null,
  // Optional theme palette override. When omitted, uses the live theme.
  palette = null,
  // When given, a tap on the rack frame, top cap or name calls it (the
  // two-rack scene opens that rack alone).
  onRackClick = null,
}) {
  const livePalette = useScenePalette();
  const pal = palette || livePalette;
  const totalU = topo.u_size || 18;
  const traceActive = traceMode && tracePathSet && tracePathSet.size > 0;
  const [leftOpen,  setLeftOpen]  = useState(false);
  const [rightOpen, setRightOpen] = useState(false);

  const { positions, chassisU, hasShelf } = useMemo(
    () => computeRackLayout(topo, totalU), [topo, totalU]);

  // When a device is selected, mark which of its ports actually have cables.
  const highlightedPortsByDevice = useMemo(() => {
    const m = new Map();
    if (selected?.kind !== 'node') return m;
    const sel = selected.id;
    for (const e of topo.edges) {
      if (e.src.device === sel) {
        if (!m.has(sel)) m.set(sel, new Set());
        m.get(sel).add(e.src.port);
        if (!m.has(e.dst.device)) m.set(e.dst.device, new Set());
        m.get(e.dst.device).add(e.dst.port);
      } else if (e.dst.device === sel) {
        if (!m.has(sel)) m.set(sel, new Set());
        m.get(sel).add(e.dst.port);
        if (!m.has(e.src.device)) m.set(e.src.device, new Set());
        m.get(e.src.device).add(e.src.port);
      }
    }
    return m;
  }, [selected, topo]);

  // Ports at either end of a cable with a problem status light amber.
  const problemPortsByDevice = useMemo(() => {
    const m = new Map();
    for (const e of topo.edges) {
      if (!isProblem(e.status)) continue;
      for (const end of [e.src, e.dst]) {
        if (!m.has(end.device)) m.set(end.device, new Set());
        m.get(end.device).add(end.port);
      }
    }
    return m;
  }, [topo]);

  // Ports with a cable drawn to them (an SFP cage with a cable holds a module).
  const cabledPortsByDevice = useMemo(() => {
    const m = new Map();
    for (const e of topo.edges) {
      for (const end of [e.src, e.dst]) {
        if (!m.has(end.device)) m.set(end.device, new Set());
        m.get(end.device).add(end.port);
      }
    }
    return m;
  }, [topo]);

  const isDimmed = (n) => {
    if (traceActive) return !tracePathSet.has(n);
    if (traceMode && (traceA || traceB)) return n !== traceA && n !== traceB;
    return selected?.kind === 'node' && selected.id !== n;
  };

  // Y-shift so this rack's chassis bottom lands on a shared floor.
  const FLOOR_CLEARANCE = 0.4;
  const groupY = floorY != null
    ? floorY + FLOOR_CLEARANCE + chassisU * U_HEIGHT / 2
    : 0;
  const poolY = floorY != null
    ? floorY - groupY + 0.003
    : -chassisU * U_HEIGHT / 2 - 0.4 + 0.003;

  return (
    <group position={[xOffset, groupY, 0]}>
      <RackChassis
        totalU={totalU}
        chassisU={chassisU}
        rackName={topo.rackName}
        hasShelf={hasShelf}
        leftOpen={leftOpen}
        rightOpen={rightOpen}
        toggleLeft={() => setLeftOpen(v => !v)}
        toggleRight={() => setRightOpen(v => !v)}
        pal={pal}
        onRackClick={onRackClick}
      />

      {Array.from(positions.entries()).map(([name, p]) => {
        const freePct = freePctByDevice?.get(name);
        const color   = heatmap ? capacityColor(freePct) : null;
        const dimmed  = isDimmed(name);
        const isTraceEnd = traceMode && (name === traceA || name === traceB);
        const isSel   = isTraceEnd
                        || (traceActive && tracePathSet.has(name))
                        || (selected?.kind === 'node' && selected.id === name);
        const hlPorts = highlightedPortsByDevice.get(name) || null;
        return (
          <DeviceBox
            key={name}
            dev={p.dev} uPos={p.uPos} sizeU={p.sizeU} chassisU={chassisU}
            color={color} dimmed={dimmed} selected={isSel} isCore={p.isCore}
            highlightedPorts={hlPorts}
            problemPorts={problemPortsByDevice.get(name) || null}
            cabledPorts={cabledPortsByDevice.get(name) || null}
            pal={pal}
            onClick={() => setSelected({ kind: 'node', id: name })}
            onHoverIn={() => onHoverDevice && onHoverDevice({ name, dev: p.dev, freePct, tier: p.tier })}
            onHoverOut={() => onHoverDevice && onHoverDevice(null)}
          />
        );
      })}

      <PortCables
        selected={selected}
        setSelected={setSelected}
        topo={topo}
        positions={positions}
        traceEdgeKeys={traceEdgeKeys}
        cableJacketColor={pal.cableJacket}
        pal={pal}
      />

      {showNeighbors && <NeighborRacks topo={topo} chassisU={chassisU} pal={pal} />}

      {/* Soft contact shadow under the rack - anchors it on the plain ground
          (no floor, no grid), and travels with the rack in a multi-rack scene. */}
      {showFloorPool && (
        <mesh position={[0, poolY, 0]} rotation={[-Math.PI / 2, 0, 0]} renderOrder={-1}>
          <planeGeometry args={[FRAME_W * 1.35, FRAME_D * 2.1]} />
          <meshBasicMaterial color={pal.shadow} alphaMap={getShadowTexture()} transparent
                             opacity={pal.shadowOpacity} depthWrite={false} toneMapped={false} />
        </mesh>
      )}
    </group>
  );
}

export default function TopologyScene3D(props) {
  const { topo, setSelected } = props;
  const totalU = topo.u_size || 18;
  // Compute chassisU here (independently of RackContent) so we can size
  // camera + lights to it. computeRackLayout is cheap.
  const { chassisU } = useMemo(
    () => computeRackLayout(topo, totalU), [topo, totalU]);
  const palette = useScenePalette();
  return (
    <SceneCanvas
      height={chassisU * U_HEIGHT + 1.3}
      width={FRAME_W + 0.5}
      maxDistance={28}
      onMissed={() => setSelected(null)}
      palette={palette}
    >
      <RackContent {...props} xOffset={0} showFloorPool={true} palette={palette} />
    </SceneCanvas>
  );
}

// ── Two racks in one scene ─────────────────────────────────────────────────
// Both racks side by side on the same floor, each drawn as the single rack is
// (its own cables inside it), plus the cables of the label check: a cable
// between the two racks runs from its port up the facing cable manager,
// across a ladder tray above the gap and down the other rack to its port; an
// end in a rack that was not photographed is a short cable out of the outer
// side to a small floating tag with that end's name. A tap on a rack's frame,
// top cap or name opens that rack alone.

const PAIR_GAP = 1.9;   // between the two frames, under the tray
const TONE_JACKET = { ok: '#eef0ed', bad: '#e0a33a', grey: '#a3a9a5', ink: '#79807d' };

// A rack's model without the far ends kept above it: in the two-rack scene
// those are drawn as cables to the other rack or to a floating tag instead.
function withoutOutside(topo) {
  const devices = (topo?.devices || []).filter((d) => d.class !== 'outside');
  const names = new Set(devices.map((d) => d.name));
  return {
    ...topo,
    devices,
    edges: (topo?.edges || []).filter((e) => names.has(e.src.device) && names.has(e.dst.device)),
    neighbors: [],
    crossRackEdges: [],
  };
}

// Out of a jack, bending down a little under its weight, then across with a
// shallow sag into the cable manager at chX.
function cableLeg(v, chX, chZ, sag0) {
  const dx = chX - v.x;
  const sag1 = sag0 + Math.min(0.07, Math.abs(dx) * 0.03);
  const zr = (v.z + chZ) / 2 + 0.02;
  return [
    v,
    new THREE.Vector3(v.x, v.y - 0.01, v.z + 0.03),
    new THREE.Vector3(v.x + dx * 0.08, v.y - sag0, zr),
    new THREE.Vector3(v.x + dx * 0.55, v.y - sag1, zr),
    new THREE.Vector3(chX, v.y - sag0 * 0.6, chZ),
  ];
}

function PairCables({ racks, plan, trayY, selectedCable, onSelectCable, pal }) {
  const chZ = FRONT_Z + 0.07;
  const { cables, tags } = useMemo(() => {
    const endOf = (e) => {
      const r = racks[e.rack];
      if (!r) return null;
      const dev = r.t.devices.find((d) => d.deviceId != null && d.deviceId === e.device);
      const pos = dev ? r.L.positions.get(dev.name) : null;
      if (!pos) return null;
      const ports = dev.ports || [];
      const port = ports.find((pt) => pt.portNo === e.order && pt.kind !== 'sfp') || ports.find((pt) => pt.portNo === e.order);
      const info = port ? pos.portInfo?.get(port.name) || null : null;
      const toward = e.rack === 'a' ? 1 : -1;
      const rel = info ? [info.x, info.y] : [toward * DEV_WIDTH * 0.46, 0];
      return { rack: e.rack, info, v: new THREE.Vector3(r.x + rel[0], r.gy + pos.centerY + rel[1], FRONT_Z + 0.004 + BOOT_LEN) };
    };
    const out = [];
    const tagMap = new Map();
    for (const c of plan) {
      const ends = c.ends.map((e) => (e && !e.off ? endOf(e) : null));
      const jx = (hash01(`${c.id}x`) - 0.5) * 0.1;
      const jz = (hash01(`${c.id}z`) - 0.5) * 0.06;
      const sag0 = 0.035 + hash01(`${c.id}d`) * 0.035;
      const base = { id: c.id, tone: c.tone, label: c.label, color: TONE_JACKET[c.tone] || TONE_JACKET.ink, dashed: c.dashed };
      if (ends[0] && ends[1] && ends[0].rack !== ends[1].rack) {
        const [l, r] = ends[0].rack === 'a' ? ends : [ends[1], ends[0]];
        const xl = racks.a.x + MGR_X + jx;
        const xr = racks.z.x - MGR_X + jx;
        const legL = cableLeg(l.v, xl, chZ + jz, sag0);
        const legR = cableLeg(r.v, xr, chZ + jz, sag0).reverse();
        const ty = trayY + 0.04 + jz * 0.4;
        const climbL = legL[4].y < ty - 0.45 ? [new THREE.Vector3(xl, ty - 0.35, chZ + jz)] : [];
        const climbR = legR[0].y < ty - 0.45 ? [new THREE.Vector3(xr, ty - 0.35, chZ + jz)] : [];
        const pts = [
          ...legL, ...climbL,
          new THREE.Vector3(xl + 0.14, ty, chZ + jz),
          new THREE.Vector3((xl + xr) / 2, ty + 0.01, chZ + jz),
          new THREE.Vector3(xr - 0.14, ty, chZ + jz),
          ...climbR, ...legR,
        ];
        out.push({ ...base, curve: new THREE.CatmullRomCurve3(pts, false, 'centripetal'), ends: [[l.v, l.info], [r.v, r.info]] });
        continue;
      }
      const onIdx = ends[0] ? 0 : ends[1] ? 1 : -1;
      if (onIdx < 0) continue;
      const end = ends[onIdx];
      const other = c.ends[1 - onIdx];
      if (!other?.off) continue;   // both ends in one rack: drawn by that rack already
      const outward = end.rack === 'a' ? -1 : 1;
      const r = racks[end.rack];
      const k = `${end.rack}|${other.off.key}`;
      if (!tagMap.has(k)) tagMap.set(k, { key: k, text: other.off.text, out: outward, x: r.x + outward * (MGR_X + MGR_W / 2 + 0.75), ys: [], cables: [] });
      const tag = tagMap.get(k);
      tag.ys.push(end.v.y);
      tag.cables.push({ ...base, end, r, outward, jz, sag0, jx });
    }
    // Tags beside the ports they are cabled to, pushed apart where they meet.
    const tagList = [...tagMap.values()];
    for (const side of [-1, 1]) {
      const list = tagList.filter((t) => t.out === side);
      for (const t of list) t.y = t.ys.reduce((a, b) => a + b, 0) / t.ys.length;
      list.sort((a, b) => a.y - b.y);
      for (let i = 1; i < list.length; i++) list[i].y = Math.max(list[i].y, list[i - 1].y + 0.34);
    }
    for (const t of tagList) {
      for (const c of t.cables) {
        const chX = c.r.x + c.outward * MGR_X + c.jx;
        const leg = cableLeg(c.end.v, chX, chZ + c.jz, c.sag0);
        const tz = chZ + 0.12;
        const pts = [
          ...leg,
          new THREE.Vector3(chX + c.outward * 0.25, (leg[4].y + t.y) / 2, tz),
          new THREE.Vector3(t.x, t.y, tz),
        ];
        t.z = tz;
        out.push({ ...c, curve: new THREE.CatmullRomCurve3(pts, false, 'centripetal'), ends: [[c.end.v, c.end.info]] });
      }
    }
    return { cables: out, tags: tagList };
  }, [racks, plan, trayY, chZ]);

  const shown = useMemo(() => cables.map((c) => ({
    ...c,
    hasFocus: !!selectedCable,
    focused: selectedCable === c.id,
  })), [cables, selectedCable]);
  const boots = useMemo(() => bootItems(shown, selectedCable, pal), [shown, selectedCable, pal]);

  return (
    <group>
      {shown.map((c) => {
        const sel = c.id === selectedCable;
        const faded = c.hasFocus && !sel;
        const thin = c.dashed;
        const radius = sel ? 0.026 : faded ? 0.014 : 0.019;
        const transparent = faded || thin;
        return (
          <mesh
            key={c.id}
            onClick={(e) => { e.stopPropagation(); onSelectCable?.(sel ? null : c.id); }}
            onPointerOver={(e) => { e.stopPropagation(); document.body.style.cursor = 'pointer'; }}
            onPointerOut={() => { document.body.style.cursor = ''; }}
          >
            <tubeGeometry args={[c.curve, 140, radius, 8, false]} />
            <meshStandardMaterial
              color={sel ? pal.select : c.color}
              metalness={0}
              roughness={0.55}
              transparent={transparent}
              opacity={faded ? 0.25 : thin ? 0.7 : 1}
              depthWrite={!transparent}
            />
          </mesh>
        );
      })}
      <BootMeshes boots={boots} />
      {tags.map((t) => (
        <group key={t.key}>
          <mesh position={[t.x, t.y, t.z]}>
            <sphereGeometry args={[0.04, 16, 12]} />
            <meshStandardMaterial color={pal.inkSoft} roughness={0.5} />
          </mesh>
          <LabelPlane text={t.text} height={0.17} color={pal.stickerInk} bg={pal.sticker}
                      position={[t.x + t.out * 0.08, t.y, t.z]} anchorX={t.out < 0 ? 'right' : 'left'} />
        </group>
      ))}
    </group>
  );
}

// The ladder tray above the gap that the cables between the racks run on.
function CableTray({ x0, x1, y, z, pal }) {
  const rungs = useMemo(() => {
    const out = [];
    for (let x = x0 + 0.1; x < x1 - 0.05; x += 0.22) out.push([x, y - 0.012, z, 0.03, 0.02, 0.3]);
    return out;
  }, [x0, x1, y, z]);
  const mat = <meshStandardMaterial color={pal.frameDark} metalness={0.3} roughness={0.5} />;
  return (
    <group>
      {[-1, 1].map((s) => (
        <mesh key={s} position={[(x0 + x1) / 2, y, z + s * 0.16]}>
          <boxGeometry args={[x1 - x0, 0.06, 0.02]} />
          {mat}
        </mesh>
      ))}
      <Instances items={rungs}>{mat}</Instances>
    </group>
  );
}

export function PairTopologyScene3D({ topoA, topoZ, plan = [], selectedCable = null, onSelectCable, onOpenRack }) {
  const palette = useScenePalette();
  const tA = useMemo(() => withoutOutside(topoA), [topoA]);
  const tZ = useMemo(() => withoutOutside(topoZ), [topoZ]);
  const LA = useMemo(() => computeRackLayout(tA, tA.u_size || 18), [tA]);
  const LZ = useMemo(() => computeRackLayout(tZ, tZ.u_size || 18), [tZ]);
  const [sel, setSel] = useState(null);   // { side, value }: a device or cable inside one rack

  const maxC = Math.max(LA.chassisU, LZ.chassisU);
  const floorY = -maxC * U_HEIGHT / 2 - 0.4;
  const xOff = FRAME_W / 2 + PAIR_GAP / 2;
  const racks = useMemo(() => ({
    a: { t: tA, L: LA, x: -xOff, gy: floorY + 0.4 + LA.chassisU * U_HEIGHT / 2 },
    z: { t: tZ, L: LZ, x: xOff, gy: floorY + 0.4 + LZ.chassisU * U_HEIGHT / 2 },
  }), [tA, tZ, LA, LZ, xOff, floorY]);
  // Top of the taller rack's cap.
  const capTop = floorY + 0.4 + maxC * U_HEIGHT + 0.25 + 0.12;
  const trayY = capTop + 0.22;
  const hasTags = plan.some((c) => c.ends.some((e) => e?.off) && c.ends.some((e) => e && !e.off));
  const width = 2 * (xOff + FRAME_W / 2) + (hasTags ? 4.2 : 0.6);
  const height = maxC * U_HEIGHT + 2.6;
  const open = (side) => (onOpenRack ? () => onOpenRack(side) : null);
  const choose = (side) => (v) => { setSel(v ? { side, value: v } : null); if (v) onSelectCable?.(null); };
  const trayX = racks.z.x - MGR_X + 0.2;

  return (
    <SceneCanvas
      height={height}
      width={width}
      maxDistance={44}
      view={PAIR_VIEW_DIR}
      onMissed={() => { setSel(null); onSelectCable?.(null); }}
      palette={palette}
    >
      {['a', 'z'].map((side) => (
        <group key={side}>
          <RackContent
            topo={racks[side].t}
            xOffset={racks[side].x}
            floorY={floorY}
            selected={sel?.side === side ? sel.value : null}
            setSelected={choose(side)}
            showNeighbors={false}
            palette={palette}
            onRackClick={open(side)}
          />
          <LabelPlane text={racks[side].t.rackName || (side === 'a' ? 'Rack A' : 'Rack Z')} height={0.36} weight="600"
                      color={palette.ink} bg={palette.sticker}
                      position={[racks[side].x, capTop + 0.5, FRONT_Z * 0.2]} {...tapProps(open(side))} />
        </group>
      ))}
      <CableTray x0={-trayX} x1={trayX} y={trayY} z={FRONT_Z + 0.07} pal={palette} />
      <PairCables racks={racks} plan={plan} trayY={trayY} selectedCable={selectedCable}
                  onSelectCable={(id) => { setSel(null); onSelectCable?.(id); }} pal={palette} />
    </SceneCanvas>
  );
}
