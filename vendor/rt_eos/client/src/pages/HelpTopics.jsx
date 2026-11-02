import { useAuth } from '../AuthContext.jsx';
import { isAdmin } from '../utils/format';
import { TopBar } from '../components/TopBar.jsx';
import { Page } from '../components/Page.jsx';
import { TextButton, Links } from '../components/Button.jsx';
import { Disclosure } from '../components/Ui.jsx';
import styles from './HelpTopics.module.css';

/*
 * The longer Help pages: where each detail comes from, how the pilot's data
 * is handled, and what the camera can tell today. Plain headings and short
 * lines; every statement here is what the app does, not what it will do.
 */

export const SOURCES = [
  ['Photo', 'The detector found it on the rack photo. The percentage is the detector\'s own score for that box.'],
  ['Photo text (OCR)', 'Text read off the photo: a faceplate, a label, the rack\'s rail. The percentage is the reading score, or for a make and model how well the text matched the maker\'s names.'],
  ['Excel dataset', 'Your patching matrix. It is taken as written, so it has no percentage.'],
  ['Datasheet', 'The maker\'s public datasheet, found by the online lookups. No percentage.'],
  ['Person', 'Somebody set or corrected it in the app. No percentage.'],
];

export const FIELDS = [
  ['Unit (RU)', 'Photo: the unit grid places the device box on the rack\'s units. When the numbers on the rails are read, those are used.', 'The device box\'s detection score.'],
  ['Height', 'Photo: how many units the device covers. The maker\'s datasheet when the photo gives none. The rack\'s own height comes from the rail numbers, the Excel dataset or the units counted.', 'The device box\'s detection score.'],
  ['Device kind', 'Photo (the detector). Person, when somebody corrected it.', 'The device box\'s detection score.'],
  ['Make', 'Photo text (OCR) of the faceplate. Person, when somebody entered it.', 'How well the text read matched a maker; otherwise the reading score.'],
  ['Model', 'Photo text (OCR) of the faceplate. Person, when somebody entered it.', 'How well the text matched a model. A model found in the raw text carries only the reading score.'],
  ['Port kind', 'Photo (the port detector). Person, when somebody corrected it.', 'The port box\'s detection score.'],
  ['Port state (plugged in or empty)', 'Photo, or a close-up photo. Person, when somebody confirmed or corrected it.', 'The port box\'s detection score. A close-up and a person have none.'],
  ['Cable (connector and colour)', 'Photo (the cable classifier).', 'The cable classifier\'s score.'],
  ['Label on a cable or device', 'Photo text (OCR), on the rack photo or a close-up.', 'The reading score. "Matched after correcting the reading" when, for example, an O was read for a 0.'],
  ['Where a cable goes', 'Excel dataset: the other end of the cable whose label was read, or whose port it is.', 'None: taken as written.'],
  ['Name in the sheet', 'Excel dataset, found by the label read on the device or by the unit it stands in.', 'None: taken as written.'],
  ['Rack name', 'The label on the rack rail (photo text); else the device labels looked up in the Excel dataset; else the layout compared with the Excel dataset; else a person.', 'The rack naming\'s own confidence. None when a person named it.'],
  ['Specifications', 'The maker\'s datasheet, through the online lookups.', 'None.'],
  ['Dust caps', 'Person: marked by hand on the port.', 'None.'],
];

/** "Which details come from the photo and which from your Excel" */
export function HelpSourcesPage() {
  return (
    <>
      <TopBar title="Where each detail comes from" backTo="/help" />
      <Page>
        <p className={styles.lead}>Which details come from the photo and which from your Excel dataset. Every detail on a device says where it came from and how sure that source was, for example: <span className={styles.eg}>Make: D-Link - read from the label, 82% sure</span>.</p>
        <Disclosure title="The five sources" count={SOURCES.length}>
          <dl className={styles.list}>
            {SOURCES.map(([k, v]) => (
              <div key={k} className={styles.row}><dt>{k}</dt><dd>{v}</dd></div>
            ))}
          </dl>
        </Disclosure>
        <Disclosure title="How sure is shown">
          <p className={styles.p}>The percentage is the source&apos;s own score, never one made up. Below 65% it is shown in amber: check it on the rack. When a source has no score, the screen says so.</p>
        </Disclosure>
        <h2 className={styles.h}>Each detail</h2>
        {FIELDS.map(([name, from, sure]) => (
          <Disclosure key={name} title={name}>
            <p className={styles.p}><span className={styles.k}>From</span>{from}</p>
            <p className={styles.p}><span className={styles.k}>How sure</span>{sure}</p>
          </Disclosure>
        ))}
        <Links className={styles.mt}>
          <TextButton to="/help/camera">What the camera can tell today</TextButton>
          <TextButton to="/help/data">How your data is handled</TextButton>
        </Links>
      </Page>
    </>
  );
}

export const DATA = [
  ['Where your photos go', 'Photos are sent to the RackTrack server, read there, and the result is shown on your phone.'],
  ['Where it is kept', 'On the RackTrack server, kept apart for your organization and each site: the photos, what was read, the close-ups, your corrections and your Excel.'],
  ['Who can see it', 'The people of your organization, on the sites they belong to, and the RackTrack team for support.'],
  ['Training', 'When "Never use for training" is on, nothing of your organization is used to train models. It is on for every new organization.'],
  ['How long it is kept', 'Scans older than the number of days your admin sets are deleted every day. New organizations start at 90 days.'],
  ['What leaves the server', 'Only when "Look up specifications online" is on, and only a make and model name, to find its datasheet. No photo, tag, rack name or Excel is sent. RackTrack never connects to any device in the rack.'],
  ['Deleting your data', 'An organization admin can delete every photo, scan, Excel and result of the organization under Organization settings.'],
];

/** "How your data is handled" */
export function HelpDataPage() {
  // Organization settings opens for an organization admin only; anyone else
  // would be sent back to Home, so the link is not shown to them.
  const { user } = useAuth();
  return (
    <>
      <TopBar title="How your data is handled" backTo="/help" />
      <Page>
        {DATA.map(([h, t]) => (
          <Disclosure key={h} title={h}>
            <p className={styles.p}>{t}</p>
          </Disclosure>
        ))}
        <Links className={styles.mt}>
          {isAdmin(user?.role) ? <TextButton to="/settings">Organization settings</TextButton> : null}
          <TextButton to="/contact">Contact support</TextButton>
        </Links>
      </Page>
    </>
  );
}

export const PORT_CLASSES = ['AUX', 'CONSOLE', 'MANAGEMENT_PORT', 'QSFP', 'RJ45', 'SFP', 'USB-A', 'USB-B', 'USB-C', 'FC', 'LC', 'SC'];

export const CAMERA_READS = [
  ['Devices and rack units', 'Each device in the photo, the rack unit it sits in and how many units it takes, and what kind of device it is (switch, patch panel, firewall, router, server, PDU and others).'],
  ['The rack name', 'From the label on the rack rail, else from the rack printed on the cable tags, else from the device labels.'],
  ['Ports', 'Every port on a device, its kind (RJ45, SFP, QSFP, OSFP, console, management and others) and whether a cable is plugged in.'],
  ['Cables', 'The colour and kind of each cable plugged in (copper or fibre).'],
  ['Cable tags', 'The printed tags on the cables: the device, port and rack each tag names. Close-ups read them best.'],
  ['Dust caps', 'Whether an empty fibre or SFP-type port has its dust cap on. A cap that is not clear on the photo is asked on the port card.'],
  ['Make and model', 'Only when it is printed on the device itself and readable on the photo.'],
];

export const CAMERA_CANNOT = [
  ['Small or handwritten text', 'Text only a few pixels tall on a whole-rack photo, and handwriting, are not read. Take a close-up of the device.'],
  ['Speed grades', 'Whether an SFP port is SFP+ or SFP28 cannot be told from the photo. It comes from the model name read on the device, or from your Excel dataset.'],
  ['A fitted optic', 'Telling a fitted optic from an empty cage is not trained yet.'],
  ['Dense patch panels', '96 and 144 port panels and MPO cassettes are not numbered yet.'],
];

/** "What the camera can tell today" */
export function HelpCameraPage() {
  return (
    <>
      <TopBar title="What the camera can tell today" backTo="/help" />
      <Page>
        <h2 className={styles.h}>What it reads from a photo</h2>
        {CAMERA_READS.map(([h, t]) => (
          <Disclosure key={h} title={h}>
            <p className={styles.p}>{t}</p>
          </Disclosure>
        ))}
        <h2 className={styles.h}>What it cannot tell yet</h2>
        {CAMERA_CANNOT.map(([h, t]) => (
          <Disclosure key={h} title={h}>
            <p className={styles.p}>{t}</p>
          </Disclosure>
        ))}
      </Page>
    </>
  );
}
