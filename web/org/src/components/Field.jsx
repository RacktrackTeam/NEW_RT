import { useId, useRef } from 'react';
import styles from './Field.module.css';

const cx = (...a) => a.filter(Boolean).join(' ');

function Label({ id, label, required }) {
  if (!label) return null;
  return (
    <label htmlFor={id} className={styles.fl}>
      {label}{required ? <span className={styles.req} aria-hidden="true">*</span> : null}
    </label>
  );
}

function Notes({ id, hint, error }) {
  return (
    <>
      {hint ? <span id={`${id}-hint`} className={styles.hint}>{hint}</span> : null}
      {error ? <span id={`${id}-err`} className={styles.err} role="alert">{error}</span> : null}
    </>
  );
}

/**
 * A text field: label above, red asterisk when required, accent ring on
 * focus, one grey hint and one red error line under it.
 * `suffix` sits inside the box on the right (a Show button, a unit).
 * `multiline` makes it a textarea.
 */
export function Field({ label, required, hint, error, suffix, multiline, className, inputRef, id: idProp, ...input }) {
  const auto = useId();
  const id = idProp || auto;
  const described = [hint && `${id}-hint`, error && `${id}-err`].filter(Boolean).join(' ') || undefined;
  const Tag = multiline ? 'textarea' : 'input';
  return (
    <div className={cx(styles.fld, className)}>
      <Label id={id} label={label} required={required} />
      <span className={cx(styles.in, multiline && styles.multi, error && styles.bad)}>
        <Tag
          id={id}
          ref={inputRef}
          className={styles.control}
          aria-required={required || undefined}
          aria-invalid={error ? true : undefined}
          aria-describedby={described}
          {...input}
        />
        {suffix ? <span className={styles.sfx}>{suffix}</span> : null}
      </span>
      <Notes id={id} hint={hint} error={error} />
    </div>
  );
}

/**
 * A select in the same box. options: [{ value, label }] or strings.
 */
export function SelectField({ label, required, hint, error, options = [], placeholder, className, id: idProp, compact, ...select }) {
  const auto = useId();
  const id = idProp || auto;
  return (
    <div className={cx(styles.fld, compact && styles.compact, className)}>
      <Label id={id} label={label} required={required} />
      <span className={cx(styles.in, styles.sel, error && styles.bad)}>
        <select id={id} className={styles.control} aria-required={required || undefined} aria-invalid={error ? true : undefined} {...select}>
          {placeholder != null ? <option value="">{placeholder}</option> : null}
          {options.map((o) => {
            const v = typeof o === 'object' ? o.value : o;
            const l = typeof o === 'object' ? o.label : o;
            return <option key={String(v)} value={v} disabled={typeof o === 'object' && o.disabled}>{l}</option>;
          })}
        </select>
        <span className={styles.caret} aria-hidden="true" />
      </span>
      <Notes id={id} hint={hint} error={error} />
    </div>
  );
}

/** The search box: a field with a magnifier, no label. */
export function SearchBox({ value, onChange, placeholder, label, className, ...rest }) {
  return (
    <span className={cx(styles.in, styles.srch, className)}>
      <input
        type="search"
        className={styles.control}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        aria-label={label || placeholder}
        {...rest}
      />
    </span>
  );
}

/**
 * Six boxes for a one-time code. Typing, pasting and the phone's autofill
 * all land in the right boxes; arrows and backspace move between them.
 */
export function CodeBoxes({ value, onChange, length = 6, label = 'Code', required, error, disabled }) {
  const refs = useRef([]);
  const digits = Array.from({ length }, (_, i) => value[i] || '');
  const setAt = (i, ch) => {
    const next = digits.slice();
    next[i] = ch;
    onChange(next.join('').slice(0, length));
  };
  const onInput = (i, e) => {
    const raw = String(e.target.value || '').replace(/\D/g, '');
    if (!raw) { setAt(i, ''); return; }
    if (raw.length > 1) {
      const merged = (digits.slice(0, i).join('') + raw).slice(0, length);
      onChange(merged);
      refs.current[Math.min(merged.length, length - 1)]?.focus();
      return;
    }
    setAt(i, raw);
    if (i < length - 1) refs.current[i + 1]?.focus();
  };
  const onKey = (i, e) => {
    if (e.key === 'Backspace' && !digits[i] && i > 0) { refs.current[i - 1]?.focus(); setAt(i - 1, ''); e.preventDefault(); }
    if (e.key === 'ArrowLeft' && i > 0) { refs.current[i - 1]?.focus(); e.preventDefault(); }
    if (e.key === 'ArrowRight' && i < length - 1) { refs.current[i + 1]?.focus(); e.preventDefault(); }
  };
  return (
    <div className={styles.fld}>
      <p className={styles.fl} id="code-label">{label}{required ? <span className={styles.req} aria-hidden="true">*</span> : null}</p>
      <div className={styles.code} role="group" aria-labelledby="code-label">
        {digits.map((d, i) => (
          <input
            key={i}
            ref={(el) => { refs.current[i] = el; }}
            className={cx(styles.codeBox, error && styles.bad)}
            inputMode="numeric"
            autoComplete={i === 0 ? 'one-time-code' : 'off'}
            aria-label={`Digit ${i + 1} of ${length}`}
            value={d}
            disabled={disabled}
            onChange={(e) => onInput(i, e)}
            onKeyDown={(e) => onKey(i, e)}
            onFocus={(e) => e.target.select()}
          />
        ))}
      </div>
      {error ? <span className={styles.err} role="alert">{error}</span> : null}
    </div>
  );
}

export default Field;
