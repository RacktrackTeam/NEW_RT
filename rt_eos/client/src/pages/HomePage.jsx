import { Link } from 'react-router-dom';
import { useAuth } from '../AuthContext.jsx';
import Icon from '../components/Icon.jsx';
import { Button } from '../components/Button.jsx';
import styles from './HomePage.module.css';

/**
 * The front door: the organization, one photograph of a whole cabled rack (a
 * public domain stock photo from rawpixel, CC0, not a customer's rack), one
 * line on what the app does and one button. Everything sits on one left edge.
 */
export default function HomePage() {
  const { user } = useAuth();
  const org = user?.organization?.name || 'RackTrack';

  return (
    <main className={styles.home}>
      <header className={styles.brand}>
        <img src="/logo.jpg" alt="RackTrack" data-nozoom="" className={styles.logo} width="40" height="40" />
        <span className={styles.org}>{org}</span>
      </header>
      <div className={styles.center}>
        <img src="/home-rack.jpg" alt="A rack of switches and patch panels, fully cabled" data-nozoom="" className={styles.photo} />
        <div className={styles.text}>
          <p className={styles.eyebrow}>{org}</p>
          <h1 className={styles.title}>Check a rack in one photo</h1>
          <p className={styles.sub}>RackTrack finds every device and port, reads the cable tags and checks them against your Excel, so a missing cable or a wrong port shows up before anyone leaves the rack.</p>
          <div className={styles.actions}>
            <Button to="/scan" className={styles.btn}><Icon name="camera" />Scan a rack</Button>
            <Link to="/map" className={styles.second}>Map two racks</Link>
          </div>
          <ul className={styles.reads} aria-label="What a scan reads">
            <li>Devices and rack units</li>
            <li>Ports, cables and dust caps</li>
            <li>Cable tags, checked against your Excel</li>
          </ul>
        </div>
      </div>
    </main>
  );
}
