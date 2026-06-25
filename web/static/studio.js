/* HOBAILabs — Studio Mode (MODE3).
   Loaded AFTER main.js on /studio. Owns the Studio-only UI; the engine
   (frames, preview, progress, cost, render) is all main.js via hooks:
     window.buildStudioPayload()  → merged into the /run payload
     window.augmentStudioCard()   → per-shot talent/product + negative/continuity
     window.applyStudioPlan()     → drops a planned frames[] into the editor
   AI may draft on-screen lines here; all copy stays editable before render. */
'use strict';
window.HOB_MODE = 'studio';

const sel = (id) => document.getElementById(id);
const sval = (id) => (sel(id)?.value || '').trim();

const studioState = { talents: [], products: [] };

// ── Identity library ───────────────────────────────────────────────────────
async function loadLibrary() {
  try {
    const [t, p] = await Promise.all([
      fetch('/api/talents').then(r => r.json()),
      fetch('/api/products').then(r => r.json()),
    ]);
    studioState.talents = t.talents || [];
    studioState.products = p.products || [];
  } catch (_) { /* keep empty */ }
  fillSelect('s-talent', studioState.talents, 'None — infer from brief');
  fillSelect('s-product', studioState.products, 'None');
  renderRefPreview();
}

function fillSelect(id, items, noneLabel) {
  const s = sel(id);
  if (!s) return;
  const cur = s.value;
  s.innerHTML = `<option value="">${noneLabel}</option>`
    + items.map(i => `<option value="${i.id}">${i.name}</option>`).join('');
  if ([...s.options].some(o => o.value === cur)) s.value = cur;
}

function renderRefPreview() {
  const t = studioState.talents.find(x => x.id === sval('s-talent'));
  const p = studioState.products.find(x => x.id === sval('s-product'));
  sel('s-talent-preview').innerHTML = t && t.ref_url
    ? `<img src="${t.ref_url}" style="width:64px;height:64px;object-fit:cover;border-radius:8px">`
      + ` <button class="btn-secondary btn-small" data-del-talent="${t.id}">🗑 Remove</button>` : '';
  sel('s-product-preview').innerHTML = p && p.ref_url
    ? `<img src="${p.ref_url}" style="width:64px;height:64px;object-fit:cover;border-radius:8px">`
      + ` <button class="btn-secondary btn-small" data-del-product="${p.id}">🗑 Remove</button>` : '';
}

sel('s-talent')?.addEventListener('change', renderRefPreview);
sel('s-product')?.addEventListener('change', renderRefPreview);

document.addEventListener('click', async (e) => {
  const dt = e.target.getAttribute?.('data-del-talent');
  const dp = e.target.getAttribute?.('data-del-product');
  if (dt) { await fetch(`/api/talents/${dt}`, { method: 'DELETE' }); await loadLibrary(); }
  if (dp) { await fetch(`/api/products/${dp}`, { method: 'DELETE' }); await loadLibrary(); }
});

async function addAsset(kind) {
  const isTalent = kind === 'talent';
  const fileEl = sel(isTalent ? 's-talent-file' : 's-product-file');
  const statusEl = sel(isTalent ? 's-talent-status' : 's-product-status');
  const file = fileEl?.files?.[0];
  if (!file) { statusEl.textContent = 'Choose a reference photo first.'; return; }
  const fd = new FormData();
  fd.append('photo', file);
  fd.append('name', sval(isTalent ? 's-talent-name' : 's-product-name'));
  if (isTalent) fd.append('descriptor', sval('s-talent-desc'));
  else {
    const raw = sval('s-product-specs');
    if (raw) fd.append('specs', JSON.stringify({ summary: raw }));
  }
  statusEl.textContent = '⏳ Saving…';
  try {
    const res = await fetch(isTalent ? '/api/talents' : '/api/products',
      { method: 'POST', body: fd }).then(r => r.json());
    if (res.error) throw new Error(res.error);
    statusEl.innerHTML = '<span class="text-green">✓ Saved</span>';
    await loadLibrary();
    const newId = (res.talent || res.product)?.id;
    if (newId) { sel(isTalent ? 's-talent' : 's-product').value = newId; renderRefPreview(); }
  } catch (err) { statusEl.innerHTML = `<span class="text-red">✗ ${err.message}</span>`; }
}
sel('s-talent-add')?.addEventListener('click', () => addAsset('talent'));
sel('s-product-add')?.addEventListener('click', () => addAsset('product'));

// ── Brief → shots ──────────────────────────────────────────────────────────
sel('s-plan-btn')?.addEventListener('click', async () => {
  const brief = sval('s-brief');
  if (!brief) { alert('Write a brief first.'); return; }
  const btn = sel('s-plan-btn');
  btn.disabled = true; btn.textContent = 'Planning…';
  sel('s-plan-status').textContent = '⏳ Designing your shots…';
  try {
    const res = await fetch('/api/studio/plan', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        brief, scope: sval('s-scope'), mood: sel('mood')?.value || '',
        talent_id: sval('s-talent'), product_id: sval('s-product'),
      }),
    }).then(r => r.json());
    if (res.error) throw new Error(res.error);
    const frames = res.frames || [];
    // Stamp the selected talent/product onto each frame so they ride the payload.
    const tid = sval('s-talent'), pid = sval('s-product');
    frames.forEach(f => {
      if (tid && f.uses_talent !== false) f.talent_id = tid;
      if (pid) f.product_id = pid;
    });
    window.applyStudioPlan(frames, []);
    sel('s-plan-status').innerHTML = `<span class="text-green">✓ ${frames.length} shots — edit any card below</span>`;
  } catch (err) {
    sel('s-plan-status').innerHTML = `<span class="text-red">✗ ${err.message}</span>`;
  } finally { btn.disabled = false; btn.textContent = '✨ Plan shots →'; }
});

// ── Per-shot controls injected into each frame card ─────────────────────────
window.augmentStudioCard = (card, f) => {
  const details = card.querySelector('.frame-card-details');
  if (!details) return;
  const wrap = document.createElement('div');
  wrap.style.cssText = 'margin-top:8px;border-top:1px dashed var(--border);padding-top:8px';
  const talentOpts = ['<option value="">— no talent —</option>']
    .concat(studioState.talents.map(t =>
      `<option value="${t.id}" ${t.id === (f.talent_id || '') ? 'selected' : ''}>${t.name}</option>`)).join('');
  const productOpts = ['<option value="">— no product —</option>']
    .concat(studioState.products.map(p =>
      `<option value="${p.id}" ${p.id === (f.product_id || '') ? 'selected' : ''}>${p.name}</option>`)).join('');
  wrap.innerHTML = `
    <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;font-size:12px">
      <label style="color:var(--text-dim)">👤 Talent</label>
      <select data-studio-talent="${f.frame_id}" style="font-size:12px">${talentOpts}</select>
      <label style="display:flex;align-items:center;gap:4px;cursor:pointer">
        <input type="checkbox" data-studio-pb="${f.frame_id}" ${f.product_beat ? 'checked' : ''}> 🛍 Product beat</label>
      <select data-studio-product="${f.frame_id}" style="font-size:12px">${productOpts}</select>
    </div>
    <input type="text" data-studio-neg="${f.frame_id}" placeholder="Negative prompt (what to avoid)"
      value="${(f.negative_prompt || '').replace(/"/g, '&quot;')}" style="margin-top:6px;font-size:12px">
    <input type="text" data-studio-lock="${f.frame_id}" placeholder="Continuity lock (outfit/styling that must not change)"
      value="${(f.continuity_lock || '').replace(/"/g, '&quot;')}" style="margin-top:6px;font-size:12px">`;
  details.appendChild(wrap);

  const set = (k, v) => window.setFrameOverride(f.frame_id, k, v);
  wrap.querySelector(`[data-studio-talent]`).addEventListener('change', e => set('talent_id', e.target.value));
  wrap.querySelector(`[data-studio-product]`).addEventListener('change', e => set('product_id', e.target.value));
  wrap.querySelector(`[data-studio-pb]`).addEventListener('change', e => set('product_beat', e.target.checked));
  wrap.querySelector(`[data-studio-neg]`).addEventListener('input', e => set('negative_prompt', e.target.value));
  wrap.querySelector(`[data-studio-lock]`).addEventListener('input', e => set('continuity_lock', e.target.value));
};

// ── Payload merged into /run + /preview ─────────────────────────────────────
window.buildStudioPayload = () => ({
  mode: 'studio',
  studio_scope: sval('s-scope'),
});

loadLibrary();
