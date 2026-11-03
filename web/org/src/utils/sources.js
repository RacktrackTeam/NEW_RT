/*
 * Where a hardware detail came from and how sure that source was, in words.
 * The server gives each field { source, confidence, note } (server
 * lib/field_sources.js); these turn it into "read from the label, 82% sure".
 */

export const SOURCE_WORD = {
  photo: 'seen on the photo',
  photo_text: 'read from the label',
  excel: 'from your Excel dataset',
  datasheet: "from the maker's datasheet",
  person: 'set by a person',
};

/** Short name of each source, for the Help page and tables. */
export const SOURCE_NAME = {
  photo: 'Photo',
  photo_text: 'Photo text (OCR)',
  excel: 'Excel dataset',
  datasheet: 'Datasheet',
  person: 'Person',
};

const pct = (c) => `${Math.round(c * 100)}% sure`;

/** "read from the label, 82% sure"; "from your Excel dataset"; null for no field. */
export function sourceWords(field) {
  // Where a detail came from and how sure the reading is are kept on the
  // server for the report files and support, but not shown to the user.
  if (field !== undefined) return null;
  if (!field || !field.source) return null;
  const where = SOURCE_WORD[field.source] || field.source;
  if (typeof field.confidence === 'number') return `${where}, ${pct(field.confidence)}`;
  if (field.source === 'photo' || field.source === 'photo_text') return `${where}, no score for it`;
  return where;
}

/** "D-Link - read from the label, 82% sure", or the value alone when there is no field. */
export function withSource(value, field) {
  const w = sourceWords(field);
  return w ? `${value} - ${w}` : String(value);
}

/** True when a field is shaky enough to be shown in the warning colour. */
export const isShaky = (field) => typeof field?.confidence === 'number' && field.confidence < 0.65;

/** How a label read matched the Excel dataset, or null for an exact match. */
export function matchWords(how) {
  if (how !== undefined) return null;
  if (how === 'normalised') return 'matched after correcting the reading';
  if (how === 'one_edit') return 'matched after correcting the reading (one character)';
  return null;
}
