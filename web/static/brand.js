/* HOBAILabs — Brand / Ad mode (B1).
   Loaded AFTER main.js on /brand. Owns the brand-only UI; the engine
   (parse, frames, preview, progress, cost, render) is all main.js via hooks:
     window.buildBrandPayload()  → merged into the /run payload
     window.augmentBrandCard()   → adds the product-beat toggle per frame card
     window.showBrandMissing()   → renders the hard-block mandatories checklist
   All ad copy/claims are brand-supplied here; the AI never writes them. */
'use strict';
window.HOB_MODE = 'brand';

const bel = (id) => document.getElementById(id);
const bval = (id) => (bel(id)?.value || '').trim();
const bradio = (name) => document.querySelector(`input[name="${name}"]:checked`)?.value;

// Brand asset paths (filled by uploads → /upload-photo saves into the session dir).
const brandState = { logo_path: '', vo_audio_path: '', music_audio_path: '', product_assets: [] };

// ── Paste-the-brief → extract structured fields (parse-only) ───────────────
bel('b-extract')?.addEventListener('click', async () => {
  const text = bval('b-paste');
  if (!text) { alert('Paste the brand brief first.'); return; }
  bel('b-extract').disabled = true;
  bel('b-extract').textContent = 'Extracting…';
  try {
    const res = await fetch('/extract-brief', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    }).then(r => r.json());
    const f = res.fields || {};
    // Only fill EMPTY fields — never overwrite what the operator already typed.
    [['name','b-name'],['product','b-product'],['objective','b-objective'],
     ['key_message','b-key'],['cta_text','b-cta'],['cta_url','b-url'],['tagline','b-tagline']]
      .forEach(([k, id]) => { if (f[k] && bel(id) && !bel(id).value.trim()) bel(id).value = f[k]; });
  } catch (e) { alert('Extract failed: ' + e.message); }
  finally { bel('b-extract').disabled = false; bel('b-extract').textContent = 'Extract fields →'; }
});

// ── Brand asset uploads (reuse /upload-photo; saves into the session dir) ───
async function uploadBrandAsset(file, tag) {
  const fd = new FormData();
  fd.append('photo', file);
  fd.append('session_id', SESSION_ID);   // SESSION_ID is defined in main.js
  fd.append('frame_id', 'brand_' + tag);
  const res = await fetch('/upload-photo', { method: 'POST', body: fd }).then(r => r.json());
  return res.tmp_path || '';
}

function wireAssetUpload(inputId, statusId, key, isList) {
  bel(inputId)?.addEventListener('change', async (e) => {
    const files = Array.from(e.target.files || []);
    if (!files.length) return;
    bel(statusId).textContent = '⏳ Uploading…';
    try {
      if (isList) {
        brandState.product_assets = [];
        for (const file of files) brandState.product_assets.push(await uploadBrandAsset(file, 'product'));
        bel(statusId).innerHTML = `<span class="text-green">✓ ${brandState.product_assets.length} product file(s)</span>`;
      } else {
        brandState[key] = await uploadBrandAsset(files[0], key.replace('_path', ''));
        bel(statusId).innerHTML = `<span class="text-green">✓ ${files[0].name}</span>`;
      }
    } catch (err) { bel(statusId).innerHTML = `<span class="text-red">✗ ${err.message}</span>`; }
  });
}
wireAssetUpload('b-logo-input',    'b-logo-status',    'logo_path',        false);
wireAssetUpload('b-vo-input',      'b-vo-status',      'vo_audio_path',    false);
wireAssetUpload('b-music-input',   'b-music-status',   'music_audio_path', false);
wireAssetUpload('b-product-input', 'b-product-status', 'product_assets',   true);

// Show/hide brand-supplied audio uploads based on the per-project toggles.
function syncAudioToggles() {
  const voBrand = bradio('vo-mode') === 'brand_audio';
  const muBrand = bradio('music-mode') === 'brand_audio';
  if (bel('b-vo-upload'))    bel('b-vo-upload').style.display    = voBrand ? 'block' : 'none';
  if (bel('b-music-upload')) bel('b-music-upload').style.display = muBrand ? 'block' : 'none';
}
document.querySelectorAll('input[name="vo-mode"],input[name="music-mode"]')
  .forEach(r => r.addEventListener('change', syncAudioToggles));
syncAudioToggles();

// ── Per-frame product-beat toggle (injected into each frame card) ──────────
window.augmentBrandCard = (card, f) => {
  const row = document.createElement('label');
  row.style.cssText = 'display:flex;align-items:center;gap:6px;margin-top:8px;font-size:13px;cursor:pointer';
  row.innerHTML = `<input type="checkbox" data-pb="${f.frame_id}"> 🛍 Product beat `
    + `<span class="muted">(real product/logo shot — never AI-generated)</span>`;
  card.querySelector('.frame-card-body')?.appendChild(row);
  row.querySelector('input').addEventListener('change', (e) => {
    window.setFrameOverride(f.frame_id, 'product_beat', e.target.checked);
  });
};

// ── Mandatories hard-block checklist ───────────────────────────────────────
window.showBrandMissing = (missing) => {
  const box = bel('b-mandatories');
  if (!box) { alert('Missing: ' + missing.join(', ')); return; }
  box.style.display = 'block';
  box.innerHTML = `<b class="text-red">Cannot render — complete these:</b><ul style="margin:6px 0 0 18px">`
    + missing.map(m => `<li>${m}</li>`).join('') + `</ul>`;
  box.scrollIntoView({ behavior: 'smooth', block: 'center' });
};

// ── The payload merged into /run + /preview ────────────────────────────────
window.buildBrandPayload = () => ({
  mode: 'brand',
  brand: {
    name: bval('b-name'), product: bval('b-product'), objective: bval('b-objective'),
    key_message: bval('b-key'), cta_text: bval('b-cta'), cta_url: bval('b-url'),
    tagline: bval('b-tagline'),
    colors: [bval('b-color')].filter(Boolean),
    logo_path: brandState.logo_path,
    logo_bug: bel('b-logobug')?.checked || false,
    logo_corner: bval('b-logo-corner') || 'tr',
    product_assets: brandState.product_assets,
    announcer_script: bval('b-announcer'),
    vo_mode: bradio('vo-mode') || 'ai_draft',
    vo_audio_path: brandState.vo_audio_path,
    vo_voice_id: bval('b-vo-voice'),
    music_mode: bradio('music-mode') || 'ai',
    music_audio_path: brandState.music_audio_path,
    disclosure: true,
    mandatories: { logo: true, cta: true, disclosure: true, product_beat: true },
  },
});
