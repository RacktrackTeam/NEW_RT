import { useState } from 'react';
import { useParams } from 'react-router-dom';
import { TopBar } from '../components/TopBar.jsx';
import { Page } from '../components/Page.jsx';
import { TextButton, Actions } from '../components/Button.jsx';
import { Sheet } from '../components/Sheet.jsx';
import { AssetImg } from '../components/AssetImg.jsx';
import { DeviceCorrections } from '../components/Corrections.jsx';
import { Loading, ErrorLine } from '../components/Text.jsx';
import { Section, DataTable, Disclosure } from '../components/Ui.jsx';
import { Status } from '../components/Status.jsx';
import { ActionRow, ActionRows } from '../components/ActionRow.jsx';
import { useDeviceCard, ReadFacts, OpticsList } from '../components/DeviceInfo.jsx';
import { MakeModelEdit } from '../components/MakeModel.jsx';
import useScan from '../hooks/useScan';
import useJSON from '../hooks/useJSON';
import { enc } from '../utils/http';
import { sourceWords, matchWords } from '../utils/sources';
import { PORT_STATUS, cableText, endText } from '../utils/ports';
import { closeUpWhy } from './result/Tabs.jsx';
import styles from './DevicePage.module.css';

/**
 * One device of a scan: its picture cut from the rack photo, the make and
 * model read off it, the optics its SFP ports need, and every port.
 */
const SWITCH_GEAR = new Set(['Switch', 'Router', 'Firewall', 'Gateway']);

/** "State seen on the photo, 71% sure; label read from the label, 64% sure" for one port. */
export function portSureWords(p) {
  const f = p.fields || {};
  return [
    f.state ? `State ${sourceWords(f.state)}` : null,
    f.cable ? `cable ${sourceWords(f.cable)}` : null,
    f.label ? `label ${sourceWords(f.label)}` : null,
  ].filter(Boolean).join('; ') || null;
}

export default function DevicePage() {
  const { scanId, deviceId } = useParams();
  const scan = useScan(scanId);
  const { card, error, reload: reloadCard } = useDeviceCard(scanId, deviceId);
  const [editMM, setEditMM] = useState(false);
  const detail = useJSON(`/api/scans/${enc(scanId)}/detail`);
  const optics = useJSON(`/api/scans/${enc(scanId)}/sfp-advice`);
  const [fix, setFix] = useState(false);
  const d = (detail.data?.devices || []).find((x) => String(x.deviceId) === String(deviceId)) || null;
  const listed = scan.devices.find((x) => String(x.deviceId) === String(deviceId) || String(x.index) === String(deviceId)) || null;
  const make = card?.make || d?.make || null;
  const model = card?.model || d?.model || null;
  // Optics are for switching gear only.
  const gear = card ? card.switchGear !== false : SWITCH_GEAR.has(d?.type);
  const name = d?.name || card?.name || 'Device';
  const unit = d?.unitTop != null ? (d.unitBottom !== d.unitTop ? `U${d.unitBottom}-${d.unitTop}` : `U${d.unitTop}`) : card?.unitTop ? `U${card.unitTop}` : null;
  const ports = d?.ports || [];
  const mine = (optics.data?.items || []).filter((it) => String(it.deviceId) === String(deviceId));

  if (error?.status === 404 && error.message !== 'Not found') {
    return (
      <>
        <TopBar title="Device not found" backTo={`/results/${enc(scanId)}?tab=devices`} />
        <Page>
          <p className={styles.lead}>That device is not on this scan. It may have been read again since.</p>
          <TextButton to={`/results/${enc(scanId)}?tab=devices`}>See the devices of this scan</TextButton>
        </Page>
      </>
    );
  }

  return (
    <>
      <TopBar
        title={name}
        sub={[unit, d?.type || card?.type, scan.rackName ? `rack ${scan.rackName}` : null].filter(Boolean).join(', ')}
        backTo={`/results/${enc(scanId)}?tab=devices`}
      />
      <Page>
        <p className={styles.fixLine}><TextButton small onClick={() => setFix(true)} disabled={!listed}>Correct this reading</TextButton></p>
        {!card && !error ? <Loading /> : null}
        <ErrorLine>{error && error.status !== 404 ? error.message : ''}</ErrorLine>

        <div className={styles.grid}>
          <div className={styles.left}>
            {card?.cropUrl ? <AssetImg src={card.cropUrl} alt={`${name}, front`} className={styles.crop} /> : null}
            <Section title="What is known of it">
              {(card || d) && /switch/i.test(String(d?.type || card?.type || '')) && (!make || !model) && !editMM ? (
                <MakeModelEdit prompt scanId={scanId} deviceId={deviceId} make={make} model={model} onSaved={() => { reloadCard(); detail.reload(); }} />
              ) : null}
              {editMM ? <MakeModelEdit scanId={scanId} deviceId={deviceId} make={make} model={model} onSaved={() => { setEditMM(false); reloadCard(); detail.reload(); }} onCancel={() => setEditMM(false)} /> : null}
              <ReadFacts showMakeModel={/switch/i.test(String(d?.type || card?.type || ''))} make={make} model={model} type={d?.type || card?.type} unit={unit} heightU={d?.heightU || card?.heightU} labelText={d?.labelText || card?.labelText} sheetName={d?.sheetName} fields={{ ...(d?.fields || {}), ...(card?.fields || {}) }} />
              {/switch/i.test(String(d?.type || card?.type || '')) && make && model && !editMM ? <div className={styles.helpLink}><TextButton small onClick={() => setEditMM(true)}>Change make and model</TextButton></div> : null}
            </Section>
            {gear ? (
              <Disclosure title="Optics to buy" count={mine.length || null} sub="For the SFP and fibre ports read on this device">
                {optics.loading && !optics.data ? <Loading /> : <OpticsList items={mine} />}
              </Disclosure>
            ) : null}
          </div>
          <div className={styles.right}>
            {/* A whole-rack photo too far to read this device's ports or caps: one line, and the close-up. */}
            {d?.closeUp ? (
              <ActionRows className={styles.actions}>
                <ActionRow icon="camera" to={`/results/${enc(scanId)}/zones?device=${enc(deviceId)}`} title={d.closeUp.text} sub={closeUpWhy(d.closeUp, d.ports?.length)} />
              </ActionRows>
            ) : null}
            <Disclosure
              title="Ports"
              count={ports.length || null}
              sub={d ? `${d.counts.connected} of ${d.counts.ports} plugged in` : null}
            >
              {detail.loading && !detail.data ? <Loading /> : null}
              <DataTable
                caption={`Ports of ${name}`}
                rows={ports}
                rowKey={(p) => p.portNo}
                empty={detail.data ? 'No ports were read on this device.' : null}
                columns={[
                  { key: 'n', label: 'Port', main: true, render: (p) => `Port ${p.portNo}` },
                  { key: 'type', label: 'Type', render: (p) => p.type },
                  { key: 'cable', label: 'Cable', render: (p) => cableText(p) || (p.state === 'empty' ? 'Empty' : null) },
                  { key: 'label', label: 'Label', render: (p) => (p.label ? <span><span className="mono">{p.label.text}</span>{matchWords(p.label.match) ? <span className={styles.src}> {matchWords(p.label.match)}</span> : null}</span> : null) },
                  { key: 'exp', label: 'Sheet', render: (p) => (p.expected ? `${p.expected.cableLabel} to ${endText(p.expected.other)}` : null) },
                  { key: 'st', label: 'Status', render: (p) => (PORT_STATUS[p.status]?.tone ? <Status kind={p.status}>{PORT_STATUS[p.status].word}</Status> : PORT_STATUS[p.status]?.word) },
                ]}
              />
            </Disclosure>
            {ports.length ? (
              <ActionRows className={styles.actions}>
                <ActionRow icon="image" to={`/results/${enc(scanId)}?device=${enc(deviceId)}`} title="Find a port on the photo" sub="Opens the rack photo on this device" />
                <ActionRow icon="camera" to={`/results/${enc(scanId)}/zones?device=${enc(deviceId)}`} title="Read cable labels" sub="Take close-ups of these ports" />
              </ActionRows>
            ) : null}
          </div>
        </div>
      </Page>
      <Sheet open={fix} onClose={() => setFix(false)} title={`Correct ${name}`}>
        {listed ? <DeviceCorrections scanId={scanId} device={listed} onChanged={(patch) => { scan.patchDevice(listed.index, patch); detail.reload(); }} /> : null}
        <Actions><TextButton onClick={() => setFix(false)}>Done</TextButton></Actions>
      </Sheet>
    </>
  );
}
