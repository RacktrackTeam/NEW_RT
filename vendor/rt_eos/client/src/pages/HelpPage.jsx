import { TopBar } from '../components/TopBar.jsx';
import { Page } from '../components/Page.jsx';
import { ActionRow, ActionRows } from '../components/ActionRow.jsx';
import { SectionLabel } from '../components/Text.jsx';
import { Disclosure } from '../components/Ui.jsx';
import styles from './HelpPage.module.css';

const TOPIC_ICONS = ['camera', 'lock'];

export const HELP = [
  ['Scanning a rack', 'Get the whole rack in the frame in good light and take one photo. For a rack too tall for one photo, use Stitch: 3 photos from the top down for most racks.'],
  ['Reading cable tags', 'Tags are small, so take close-ups: pick the device, hold the phone 20 to 30 cm away and take one photo per block of ports.'],
  ['Checking against your Excel', 'Your site manager uploads the patching Excel once. Every scan is then checked against it: missing cables, wrong ports and cables not in the Excel are flagged.'],
  ['Two racks', 'Map two racks checks every cable that runs between them, at both ends.'],
  ['Reports', 'Each rack and each pair of racks has a report to read on screen, and to save as PDF or CSV.'],
];

/** The longer pages, each one tap away. */
export const TOPICS = [
  ['/help/camera', 'What the camera can tell today'],
  ['/help/data', 'How your data is handled'],
];

/** Each question folded to one row; the longer pages one tap away. */
export default function HelpPage() {
  return (
    <>
      <TopBar title="Help" />
      <Page>
        {HELP.map(([h, t]) => (
          <Disclosure key={h} title={h}>
            <p className={styles.lim}>{t}</p>
          </Disclosure>
        ))}
        <SectionLabel>More</SectionLabel>
        <ActionRows>
          {TOPICS.map(([to, t], i) => <ActionRow key={to} quiet icon={TOPIC_ICONS[i]} to={to} title={t} />)}
          <ActionRow quiet icon="mail" to="/contact" title="Contact support" />
        </ActionRows>
      </Page>
    </>
  );
}
