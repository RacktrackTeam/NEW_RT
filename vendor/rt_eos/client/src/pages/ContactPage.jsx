import { useRef, useState } from 'react';
import { useLocation } from 'react-router-dom';
import { TopBar, useBack } from '../components/TopBar.jsx';
import { Page, Measure } from '../components/Page.jsx';
import { Button, TextButton } from '../components/Button.jsx';
import { Field } from '../components/Field.jsx';
import { ListRow } from '../components/ListRow.jsx';
import { Grey, ErrorLine, EmptyState } from '../components/Text.jsx';
import { useToast } from '../components/Toast.jsx';
import { postForm, postJSON } from '../utils/http';
import { fileSize } from '../utils/format';
import { SUPPORT_EMAIL } from './SignInPage.jsx';
import styles from './ContactPage.module.css';

const MAX_FILES = 5;
const MAX_BYTES = 10 * 1024 * 1024;
const ACCEPT = 'image/*,application/pdf,text/plain,text/csv,application/json,.csv,.txt,.json,.pdf';

/** Send a message to support, with files if needed. */
export default function ContactPage() {
  const location = useLocation();
  const toast = useToast();
  const goBack = useBack('/you');
  const [subject, setSubject] = useState('');
  const [message, setMessage] = useState('');
  const [files, setFiles] = useState([]);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [sent, setSent] = useState(false);
  const fileRef = useRef(null);
  const total = files.reduce((s, f) => s + f.size, 0);

  const add = (list) => {
    const next = [...files, ...Array.from(list || [])];
    if (next.length > MAX_FILES) { setError(`Attach ${MAX_FILES} files at most.`); return; }
    if (next.reduce((s, f) => s + f.size, 0) > MAX_BYTES) { setError('The files come to more than 10 MB. Remove one or send it separately.'); return; }
    setError('');
    setFiles(next);
  };

  const send = async (e) => {
    e.preventDefault();
    if (message.trim().length < 5) { setError('Write a few words about what happened.'); return; }
    setBusy(true); setError('');
    const context = { path: location.state?.from || null, app: 'rt-eos', userAgent: navigator.userAgent };
    try {
      if (files.length) {
        const form = new FormData();
        form.append('subject', subject.trim());
        form.append('message', message.trim());
        form.append('context', JSON.stringify(context));
        files.forEach((f) => form.append('attachments', f));
        await postForm('/api/support/contact', form);
      } else {
        await postJSON('/api/support/contact', { subject: subject.trim(), message: message.trim(), context: JSON.stringify(context) });
      }
      setSent(true);
      toast('Message sent.');
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <TopBar title="Contact support" />
      <Page>
        <Measure wide>
          {sent ? (
            <EmptyState text="Your message is with support. We reply within a few hours, to the email on your account." action={<TextButton onClick={goBack}>Back</TextButton>} />
          ) : (
            <form onSubmit={send} noValidate>
              <Grey>
                We reply within a few hours. Or email <TextButton href={`mailto:${SUPPORT_EMAIL}`} small>{SUPPORT_EMAIL}</TextButton>.
              </Grey>
              <Field label="Subject" placeholder="Optional" value={subject} onChange={(e) => setSubject(e.target.value)} maxLength={200} />
              <Field label="Message" required multiline value={message} onChange={(e) => { setMessage(e.target.value); setError(''); }} maxLength={5000} />
              {files.map((f, i) => (
                <ListRow key={`${f.name}-${i}`} title={f.name} meta={fileSize(f.size)} right={<TextButton small onClick={() => setFiles(files.filter((_, k) => k !== i))}>Remove</TextButton>} />
              ))}
              {files.length < MAX_FILES ? <p className={styles.mt8}><TextButton onClick={() => fileRef.current?.click()}>Add files</TextButton></p> : null}
              <Grey>{files.length} of {MAX_FILES} files, {(total / (1024 * 1024)).toFixed(1)} of 10 MB.</Grey>
              <input ref={fileRef} type="file" multiple accept={ACCEPT} className="visually-hidden" tabIndex={-1} aria-hidden="true" onChange={(e) => { add(e.target.files); e.target.value = ''; }} />
              <ErrorLine>{error}</ErrorLine>
              <Button type="submit" busy={busy} className={styles.send}>Send message</Button>
              <p className={styles.mt16}><TextButton to="/help">Open Help</TextButton></p>
            </form>
          )}
        </Measure>
      </Page>
    </>
  );
}
