/**
 * The drift report: one page a person can send.
 *
 * Everything the drift check found about one rack, in the order somebody who was
 * not there would ask it: which rack, compared with what and when; how much of
 * it agrees; what differs, in plain words with the detail under it; what the
 * record holds that the scan did not see; and where the check has got to - who
 * holds it and under which ServiceNow incident.
 *
 * Plain HTML with its own styles and no script, so it opens anywhere, prints to
 * one PDF, and can be attached to an incident as it is. Light only. Nothing here
 * decides or writes: it is a rendering of a plan that already exists.
 */

const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const WORD = { create: 'Not in the record', update: 'Different from the record', rebind: 'Listed under an older id' };
const MEANS = {
  create: 'Seen in the rack, but the record does not list it on that shelf.',
  update: 'The record lists it, but some details are different.',
  rebind: 'The record lists it under an id RackTrack used before.',
};
const STATE = {
  draft: 'Compared, not sent', open: 'Compared, not sent', submitted: 'Sent to the SPOC', triage: 'Needs an admin',
  assigned: 'Assigned', accepted: 'Accepted by the assignee', in_progress: 'Being checked at the rack', pending: 'On hold',
  resolved: 'Checked, finding recorded', verification_pending: 'Waiting for a second scan', approval_pending: 'Waiting for approval',
  approved: 'Approved', write_in_progress: 'Writing to the record', written: 'Written to the record', completed: 'Done',
  applied: 'Written to the record', rejected: 'Rejected', rework: 'Sent back', cancelled: 'Cancelled',
};
// RackTrack's own keys. They are how the app finds a record again, not something anybody saw.
const INTERNAL = new Set(['racktrack_uid', 'racktrack_bound', 'recordId']);

const shelfOf = (item) => {
  const m = String(item.uid || '').match(/:u(\d{1,2})$/i) || String(item.name || '').match(/\bU(\d{1,2})\b/);
  return m ? Number(m[1]) : null;
};
/* `marks` is what the rack is called in the names of the things in it: the
   name a person gave it, and the scan's own id, which is what the device
   names carry while nobody has identified the rack. */
function plainName(name, ...marks) {
  let out = String(name || '');
  for (const mark of marks.filter(Boolean)) {
    if (out.endsWith(mark)) out = out.slice(0, -mark.length).trim();
  }
  return out.replace(/\s+U\d{1,2}$/, '').trim() || String(name || '');
}

/** The three groups a reader wants, from a plan as lib/netbox/plans reads it. */
function groups(plan) {
  const items = plan.items || [];
  const orphans = plan.orphans || [];
  const byBox = new Map(orphans.filter((o) => o.seen && o.matchedBox).map((o) => [o.matchedBox, o]));
  const housekeeping = (i) => i.action === 'rebind' && !i.fromUid;
  const different = items.filter((i) => i.decidable && !housekeeping(i));
  const differentUids = new Set(different.map((i) => i.uid));
  const matching = items
    .filter((i) => i.type === 'Device' && !differentUids.has(i.uid))
    .map((i) => ({ item: i, record: byBox.get(i.uid) || null }))
    .filter((m) => m.record || m.item.netboxId != null || ['noop', 'rebind'].includes(m.item.action));
  const notSeen = orphans.filter((o) => !o.seen);
  return { different, matching, notSeen };
}

function build(plan, { tickets = [], siteName = null, spaceName = null, generatedAt = new Date() } = {}) {
  const rack = plan.rackName && !/^RK-[0-9A-F]{6,}$/i.test(plan.rackName) ? plan.rackName : null;
  const title = rack || 'Unidentified rack';
  const g = groups(plan);
  const state = STATE[plan.state] || STATE[plan.status] || String(plan.state || plan.status || '');
  // A check sent to the SPOC has one incident, and every ticket of it carries a
  // copy of that one. An older check raised one per item, and lists them all.
  const whole = tickets.map((t) => t.external).find((e) => e && e.planLevel && e.number);
  const incidents = whole ? [whole.number] : tickets.map((t) => t.external && t.external.number).filter(Boolean);
  const when = (iso) => { const d = new Date(iso); return Number.isNaN(d.getTime()) ? '' : d.toUTCString().replace(' GMT', ' UTC'); };

  const diffRows = g.different.map((i) => {
    const lines = Object.entries(i.diff || {}).filter(([f]) => !INTERNAL.has(f)).map(([f, v]) => {
      const from = v && typeof v === 'object' && 'from' in v ? v.from : null;
      const to = v && typeof v === 'object' && 'to' in v ? v.to : v;
      return `<div class="d"><span>${esc(f)}</span> record: <b>${esc(from ?? ' - ')}</b> &nbsp; seen: <b>${esc(to ?? ' - ')}</b></div>`;
    }).join('');
    const t = tickets.find((x) => x.itemUid === i.uid);
    const held = t ? `<div class="d">With ${esc(t.assignee || 'nobody yet')}${t.external && t.external.number && !t.external.planLevel ? ` - ${esc(t.external.number)}` : ''}${t.status ? ` - ${esc(String(t.status).replace(/_/g, ' '))}` : ''}</div>` : '';
    const found = t && t.finding ? `<div class="d ok">Finding: ${esc(t.finding)}</div>` : '';
    const sh = shelfOf(i);
    return `<tr><td class="u">${sh != null ? `U${sh}` : ''}</td><td><b>${esc(plainName(i.name, rack, plan.rackId))}</b><div class="m">${esc(i.type)}</div></td>
      <td><span class="chip warn">${esc(WORD[i.action] || i.action)}</span><div class="m">${esc(MEANS[i.action] || '')}</div>${lines}${held}${found}</td></tr>`;
  }).join('');

  const matchRows = g.matching.map(({ item, record }) => {
    const sh = shelfOf(item);
    return `<tr><td class="u">${sh != null ? `U${sh}` : ''}</td><td><b>${esc(plainName(item.name, rack, plan.rackId))}</b></td>
      <td><span class="chip ok">Matches</span> <span class="m">${esc(record ? record.name : 'the record')}</span></td></tr>`;
  }).join('');

  const unseenRows = g.notSeen.map((o) => `<tr><td class="u">${o.position != null ? `U${esc(o.position)}` : ''}</td><td><b>${esc(o.name)}</b></td>
      <td><span class="chip">In the record, not seen in the photo</span></td></tr>`).join('');

  return `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Drift report - ${esc(title)}</title>
<style>
  :root{--ink:#14171c;--body:#2c323b;--muted:#5a6371;--faint:#89919e;--rule:#dfe3e8;--soft:#edeff2;--good:#1c6b3c;--gsoft:#e9f2ec;--warn:#87610a;--wsoft:#fbf3e0;}
  *{box-sizing:border-box} body{margin:0;background:#fff;color:var(--body);font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif}
  .wrap{padding:24px clamp(16px,4vw,48px) 48px}
  .eyebrow{font:600 11px/1 ui-monospace,Menlo,monospace;letter-spacing:.12em;text-transform:uppercase;color:var(--faint);margin:0 0 6px}
  h1{margin:0 0 4px;font-size:26px;letter-spacing:-.015em;color:var(--ink)} .sub{margin:0;color:var(--muted);font-size:14px}
  .kv{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));border:1px solid var(--rule);border-radius:3px;margin:18px 0 0;overflow:hidden}
  .kv div{padding:10px 14px;box-shadow:0 0 0 .5px var(--soft)} .kv b{display:block;font:600 10.5px/1.2 ui-monospace,Menlo,monospace;letter-spacing:.08em;text-transform:uppercase;color:var(--faint);margin-bottom:3px}
  .nums{display:grid;grid-template-columns:repeat(3,1fr);gap:1px;background:var(--rule);border:1px solid var(--rule);border-radius:3px;overflow:hidden;margin:16px 0 0}
  .nums div{background:#fff;padding:14px} .nums b{display:block;font-size:26px;color:var(--ink);font-variant-numeric:tabular-nums} .nums span{font-size:13px;color:var(--muted)}
  h2{margin:28px 0 8px;font-size:16px;color:var(--ink)} .lede{margin:0 0 8px;font-size:13.5px;color:var(--muted)}
  table{width:100%;border-collapse:collapse;border:1px solid var(--rule);border-radius:3px;font-size:14px}
  td{padding:9px 12px;border-top:1px solid var(--soft);vertical-align:top} tr:first-child td{border-top:0}
  td.u{width:52px;font:600 12px ui-monospace,Menlo,monospace;color:var(--muted);white-space:nowrap}
  .m{font-size:12.5px;color:var(--muted)} .d{font-size:13px;color:var(--muted);margin-top:3px} .d span{font-family:ui-monospace,Menlo,monospace;color:var(--faint);margin-right:6px} .d.ok{color:var(--good)}
  .chip{display:inline-block;font:600 10.5px/1 ui-monospace,Menlo,monospace;letter-spacing:.05em;text-transform:uppercase;padding:4px 7px;border-radius:2px;border:1px solid var(--rule);color:var(--muted);background:#fff}
  .chip.ok{color:var(--good);background:var(--gsoft);border-color:#c7decf} .chip.warn{color:var(--warn);background:var(--wsoft);border-color:#e8d7ae}
  .none{padding:12px 14px;border:1px solid var(--rule);border-radius:3px;color:var(--muted);font-size:14px}
  .foot{margin-top:32px;padding-top:12px;border-top:1px solid var(--rule);font:11.5px/1.7 ui-monospace,Menlo,monospace;color:var(--faint)}
  @media print{.wrap{padding:0} h2{break-after:avoid} tr{break-inside:avoid}}
</style></head><body><div class="wrap">
  <p class="eyebrow">Drift report</p>
  <h1>${esc(title)}</h1>
  <p class="sub">${esc([siteName, spaceName].filter(Boolean).join(' - ') || 'Site not recorded')}</p>
  <div class="kv">
    <div><b>Compared with</b>${esc(plan.netboxUrl ? 'NetBox' : 'the record system')}</div>
    <div><b>Compared</b>${esc(when(plan.createdAt))}</div>
    <div><b>By</b>${esc(plan.createdBy || plan.by || ' - ')}</div>
    <div><b>Where it stands</b>${esc(state)}</div>
    <div><b>ServiceNow</b>${incidents.length ? esc([...new Set(incidents)].join(', ')) : 'no incident raised'}</div>
    <div><b>Check number</b>${esc(plan.id)}</div>
  </div>
  <div class="nums">
    <div><b>${g.matching.length}</b><span>match the record</span></div>
    <div><b>${g.different.length}</b><span>different from the record</span></div>
    <div><b>${g.notSeen.length}</b><span>in the record, not seen</span></div>
  </div>

  <h2>Different from the record</h2>
  <p class="lede">What the photograph shows that the record does not agree with. Nothing here has been changed in the record.</p>
  ${diffRows ? `<table>${diffRows}</table>` : '<div class="none">Nothing differs.</div>'}

  <h2>Matching the record</h2>
  <p class="lede">Seen in the rack on the shelf where the record has it.</p>
  ${matchRows ? `<table>${matchRows}</table>` : '<div class="none">Nothing was matched.</div>'}

  <h2>In the record, not seen in the photograph</h2>
  <p class="lede">The record says these are in this rack. The photograph did not show them - often because they sit behind cables or have no face to read. Nothing is removed from the record.</p>
  ${unseenRows ? `<table>${unseenRows}</table>` : '<div class="none">Everything in the record was seen.</div>'}

  ${plan.submittedNote ? `<h2>Note from the technician</h2><div class="none">${esc(plan.submittedNote)}</div>` : ''}
  <div class="foot">RackTrack drift report - check ${esc(plan.id)} - generated ${esc(when(generatedAt))}</div>
</div></body></html>`;
}

module.exports = { build, groups, plainName };
