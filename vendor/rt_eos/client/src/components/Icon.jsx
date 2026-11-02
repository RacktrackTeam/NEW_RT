/* The one icon set of the app: small line glyphs for the navigation, buttons
 * and rows, the same family RackTrack uses. Inline SVG so there is no icon
 * font to fail. This file is the only place an svg element may be written (the
 * noSvg test holds that line): icons only, never a picture of a rack, a photo
 * stand-in or any other drawing. Unknown names render nothing.
 *
 * Sized in em: set font-size on the parent to size the glyph.
 */

const P = {
  home: <><path d="M3 9.5L12 3l9 6.5V20a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1z" /><path d="M9 21v-9h6v9" /></>,
  camera: <><path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z" /><circle cx="12" cy="13" r="4" /></>,
  two_racks: <><rect x="3" y="3" width="7" height="18" rx="1" /><rect x="14" y="3" width="7" height="18" rx="1" /><path d="M10 8h4" /></>,
  switch: <><rect x="2" y="7" width="20" height="10" rx="2" /><path d="M6 12h.01M9.5 12h.01M13 12h.01M16.5 12h1.5" /></>,
  person: <><circle cx="12" cy="8" r="3.7" /><path d="M4.8 20c0-3.7 3.2-5.8 7.2-5.8s7.2 2.1 7.2 5.8" /></>,
  arrow_back: <><path d="M19 12H5" /><path d="M12 19l-7-7 7-7" /></>,
  chevron_right: <path d="M9 5l7 7-7 7" />,
  more: <><circle cx="5" cy="12" r="1.4" fill="currentColor" stroke="none" /><circle cx="12" cy="12" r="1.4" fill="currentColor" stroke="none" /><circle cx="19" cy="12" r="1.4" fill="currentColor" stroke="none" /></>,
  upload: <><path d="M12 16V4" /><path d="M7 9l5-5 5 5" /><path d="M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3" /></>,
  download: <><path d="M12 4v12" /><path d="M7 11l5 5 5-5" /><path d="M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3" /></>,
  sheet: <><rect x="3.5" y="3.5" width="17" height="17" rx="2" /><path d="M3.5 9h17M3.5 14.5h17M9.5 3.5v17" /></>,
  image: <><rect x="3.5" y="4.5" width="17" height="15" rx="2.5" /><circle cx="9" cy="10" r="1.8" /><path d="M4.5 17.5l4.6-4.6 3.3 3.3 2.4-2.3 4.7 4.6" /></>,
  video: <><rect x="3" y="6" width="13" height="12" rx="2" /><path d="M16 10.5l5-3v9l-5-3z" /></>,
  layers: <><path d="M12 3l9 5-9 5-9-5z" /><path d="M3 13l9 5 9-5" /></>,
  zoom: <><circle cx="11" cy="11" r="6.5" /><path d="M16 16l4.5 4.5" /></>,
  filter: <path d="M3.5 5h17l-6.5 7.6V19l-4 1.6v-8z" />,
  zoom_in: <><circle cx="11" cy="11" r="6.5" /><path d="M16 16l4.5 4.5" /><path d="M8 11h6M11 8v6" /></>,
  zoom_out: <><circle cx="11" cy="11" r="6.5" /><path d="M16 16l4.5 4.5" /><path d="M8 11h6" /></>,
  fit_screen: <><path d="M4 9V5a1 1 0 0 1 1-1h4M15 4h4a1 1 0 0 1 1 1v4M20 15v4a1 1 0 0 1-1 1h-4M9 20H5a1 1 0 0 1-1-1v-4" /></>,
  port: <><rect x="3" y="7" width="18" height="10" rx="2" /><path d="M7 11v2M11 11v2M15 11v2" /></>,
  history: <><path d="M3.5 12a8.5 8.5 0 1 0 2.6-6.1" /><path d="M3 4v4h4" /><path d="M12 7.5V12l3 2" /></>,
  list: <><path d="M9 6h11M9 12h11M9 18h11" /><path d="M4 6h.01M4 12h.01M4 18h.01" /></>,
  group: <><circle cx="9" cy="8" r="3.2" /><path d="M3 20a6 6 0 0 1 12 0" /><path d="M16.2 5.4a3.2 3.2 0 0 1 0 5.9" /><path d="M17.3 14.4A6 6 0 0 1 21 20" /></>,
  settings: <><circle cx="12" cy="12" r="3.2" /><path d="M12 2.6v2.2M12 19.2v2.2M21.4 12h-2.2M4.8 12H2.6M18.6 5.4l-1.6 1.6M7 17l-1.6 1.6M18.6 18.6L17 17M7 7L5.4 5.4" /></>,
  chart: <><path d="M3 3v18h18" /><path d="M7 15l4-5 3 3 4-6" /></>,
  help: <><circle cx="12" cy="12" r="9" /><path d="M9.2 9a2.9 2.9 0 0 1 5.6 1c0 2-2.8 2.6-2.8 4" /><circle cx="12" cy="17.4" r="1" fill="currentColor" stroke="none" /></>,
  mail: <><rect x="3" y="5" width="18" height="14" rx="2" /><path d="M3.6 6.7l8.4 5.8 8.4-5.8" /></>,
  lock: <><rect x="4.5" y="10.5" width="15" height="10" rx="2" /><path d="M8 10.5V7.5a4 4 0 0 1 8 0v3" /></>,
  logout: <><path d="M15 4h3a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-3" /><path d="M10 17l5-5-5-5" /><path d="M15 12H3" /></>,
  check: <path d="M20 6L9 17l-5-5" />,
  alert: <><path d="M12 3.5l9.5 16.5h-19z" /><path d="M12 10v4.5" /><circle cx="12" cy="17.3" r="0.9" fill="currentColor" stroke="none" /></>,
  link: <><path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1" /><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1" /></>,
  chip: <><rect x="6" y="6" width="12" height="12" rx="2" /><path d="M9 2.5V6M15 2.5V6M9 18v3.5M15 18v3.5M2.5 9H6M2.5 15H6M18 9h3.5M18 15h3.5" /></>,
  close: <path d="M6 6l12 12M18 6L6 18" />,
  // The sign-in screens' glyphs, as RackTrack draws them.
  eye: <><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" /><circle cx="12" cy="12" r="3" /></>,
  eye_off: <><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24" /><path d="M1 1l22 22" /></>,
  alert_circle: <><circle cx="12" cy="12" r="10" /><path d="M12 8v4M12 16h.01" /></>,
  info: <><circle cx="12" cy="12" r="10" /><path d="M12 16v-4M12 8h.01" /></>,
  units: <><rect x="5" y="3" width="14" height="18" rx="1.5" /><path d="M5 7.5h14M5 12h14M5 16.5h14" /></>,
  az: <><path d="M4 12h16" /><path d="M8 8l-4 4 4 4" /><path d="M16 8l4 4-4 4" /></>,
  cable: <><path d="M5 3v5a3 3 0 0 0 3 3h8a3 3 0 0 1 3 3v7" /><rect x="3" y="2" width="4" height="3" rx="0.5" /><rect x="17" y="19" width="4" height="3" rx="0.5" /></>,
  topology: <><rect x="9" y="2.5" width="6" height="5" rx="1" /><rect x="2.5" y="16.5" width="6" height="5" rx="1" /><rect x="15.5" y="16.5" width="6" height="5" rx="1" /><path d="M12 7.5v4.5M5.5 16.5V12h13v4.5" /></>,
  report: <><path d="M6 2.5h8.5L19 7v13.5a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1v-17a1 1 0 0 1 1-1z" /><path d="M14 2.5V7h5" /><path d="M8.5 12h7M8.5 15.5h7M8.5 19h4" /></>,
};

/** Names this set covers. */
export const ICON_NAMES = Object.keys(P);

export default function Icon({ name, className = '', style, ...rest }) {
  const path = P[name];
  if (!path) return null;
  return (
    <svg
      className={`appIcon ${className}`.trim()}
      style={style}
      width="1em"
      height="1em"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      {...rest}
    >
      {path}
    </svg>
  );
}
