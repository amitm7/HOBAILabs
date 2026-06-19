/* HOBAILabs Web UI */
'use strict';

// ── Session ID (persists for photo uploads across the form fill) ──────────
const SESSION_ID = crypto.randomUUID();

// State
let parsedFrames = [];          // [{frame_id, caption, duration, photo_spec, director_note}]
let frameOverrides = {};        // frame_id → {photo_spec, photo_tmp_path, director_note}
let parsedCast = [];            // [{id, label, gender, age_bracket}] from /parse-script
let speakerVoices = {};         // speaker_id → ElevenLabs voice_id (Cast voices panel)
let frameApprovals = {};        // frame_id → bool (approval gate; default approved)
let previewReady = false;       // true once Preview Stills has produced images
let framePreviewPath = {};      // frame_id → still server-path (for vision suggest)

// Camera vocabulary for the per-frame dropdown (matches agents/suggestions.CAMERA_MOVES).
const CAMERA_MOVES = [
  'static', 'slow push in', 'dolly in', 'dolly out', 'crane up', 'crane down',
  'tilt up', 'tilt down', 'arc left', 'arc right', '360 orbit', 'crash zoom in',
  'crash zoom out', 'overhead', 'dutch angle', 'Hitchcock zoom',
  'extreme close on eyes', 'handheld', 'bullet time', 'super 8mm', 'whip pan',
];
function cameraSelectHtml(f) {
  const cur = (f.motion_override || '').trim();
  let opts = CAMERA_MOVES.slice();
  if (cur && !opts.includes(cur)) opts = [cur, ...opts];   // preserve a custom/auto pick
  const optHtml = ['<option value="">✨ Auto (AI chooses at render)</option>']
    .concat(opts.map(m => `<option value="${escHtml(m)}" ${m === cur ? 'selected' : ''}>${escHtml(m)}</option>`))
    .join('');
  return `<select id="motion-${f.frame_id}" class="camera-select" style="font-size:13px">${optHtml}</select>`;
}
let postingCaption = '';        // Format B Caption: block for story-mode posting kit

// Live per-frame override setter (used by brand.js, which is a separate script).
window.setFrameOverride = (frameId, key, value) => {
  if (!frameOverrides[frameId]) frameOverrides[frameId] = {};
  frameOverrides[frameId][key] = value;
};
let generatedMusicPath = null;
let uploadedMusicPath = null;
let currentRunId = null;

// ── Model catalog (for the model dropdowns only — routing & pricing live
//    server-side; the estimate comes from POST /api/estimate) ───────────────
let _models = null;             // loaded once from /models  {models, routing, defaults}

async function loadModels() {
  if (_models) return _models;
  try { _models = await fetch('/models').then(r => r.json()); }
  catch (_) { _models = { models: {}, routing: {}, defaults: {} }; }
  return _models;
}

function populateFrameModelSelects() {
  if (!_models || !_models.models) return;
  parsedFrames.forEach(f => {
    const sel = el(`model-${f.frame_id}`);
    if (!sel || sel.dataset.filled) return;
    sel.dataset.filled = '1';
    Object.entries(_models.models).forEach(([id, m]) => {
      const opt = document.createElement('option');
      opt.value = id;
      opt.textContent = `${m.kind === 'image' ? '🖼' : '🎬'} ${id} — ${m.tier}`;
      sel.appendChild(opt);
    });
    const ov = frameOverrides[f.frame_id] || {};
    if (ov.model_override) sel.value = ov.model_override;
    sel.addEventListener('change', () => {
      if (!frameOverrides[f.frame_id]) frameOverrides[f.frame_id] = {};
      frameOverrides[f.frame_id].model_override = sel.value;
      renderCostEstimate();
    });
  });
}

let _modelSelectsPopulated = false;
function populateModelSelects() {
  if (_modelSelectsPopulated || !_models || !_models.models) return;
  _modelSelectsPopulated = true;
  const imgSel = el('image-model'), vidSel = el('video-model');
  Object.entries(_models.models).forEach(([id, m]) => {
    const opt = document.createElement('option');
    opt.value = id;
    opt.textContent = `${id} — ${m.tier}` + (m.strengths ? ` (${m.strengths.slice(0,2).join('/')})` : '');
    if (m.kind === 'image' && imgSel) imgSel.appendChild(opt);
    if (m.kind === 'video' && vidSel) vidSel.appendChild(opt.cloneNode(true));
  });
}

// ── Helpers ───────────────────────────────────────────────────────────────

function el(id) { return document.getElementById(id); }

function post(url, body) {
  return fetch(url, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body),
  }).then(r => r.json());
}

function postForm(url, formData) {
  return fetch(url, { method: 'POST', body: formData }).then(r => r.json());
}

// ── Music toggles ─────────────────────────────────────────────────────────

let voicesLoaded = false;

document.querySelectorAll('input[name="music-type"]').forEach(radio => {
  radio.addEventListener('change', () => {
    el('upload-music-sub').style.display   = radio.value === 'upload'      ? 'block' : 'none';
    el('generate-music-sub').style.display = radio.value === 'generate'    ? 'block' : 'none';
    el('voiceover-sub').style.display      = radio.value === 'voiceover'   ? 'block' : 'none';
    if (radio.value === 'voiceover' && !voicesLoaded) loadVoices();
  });
});

async function loadVoices() {
  el('voice-load-status').textContent = '⏳ Loading…';
  try {
    const res = await fetch('/voices').then(r => r.json());
    const voices = res.voices || [];
    const sel = el('voice-select');
    sel.innerHTML = voices.length
      ? voices.map(v =>
          `<option value="${v.voice_id}">${v.name}${v.category ? ' · ' + v.category : ''}</option>`
        ).join('')
      : '<option value="">No voices found — check ELEVENLABS_API_KEY</option>';
    el('voice-load-status').textContent = voices.length ? `${voices.length} voices` : '';
    voicesLoaded = true;
  } catch (err) {
    el('voice-load-status').textContent = '✗ Failed';
    el('voice-select').innerHTML = '<option value="">Could not load voices</option>';
  }
}

el('music-file-input').addEventListener('change', async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  el('music-upload-name').textContent = '⏳ Uploading…';

  const fd = new FormData();
  fd.append('photo', file);
  fd.append('session_id', SESSION_ID);
  fd.append('frame_id', 'music');

  const res = await postForm('/upload-photo', fd);
  if (res.tmp_path) {
    uploadedMusicPath = res.tmp_path;
    el('music-upload-name').textContent = '✓ ' + file.name;
  }
});

el('generate-music-btn').addEventListener('click', async () => {
  // Empty prompt → the server composes a proper Suno brief from the story
  // beats (genre + tempo + instruments + emotion arc).
  const prompt = el('music-prompt').value.trim();

  el('generate-music-btn').disabled = true;
  el('music-gen-status').innerHTML = '<span class="spinner"></span> Generating with Suno V5.5… (2–3 min)';

  try {
    const res = await fetch('/generate-music', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        prompt,
        session_id: SESSION_ID,
        captions: parsedFrames.map(f => f.caption || ''),
        mood: el('mood')?.value || '',
      }),
    }).then(r => r.json());

    if (res.music_path) {
      generatedMusicPath = res.music_path;
      el('music-gen-status').innerHTML = '<span class="text-green">✓ Music ready</span>' +
        (prompt ? '' : ` <span class="muted" style="font-size:11px">brief: ${(res.prompt_used || '').slice(0, 90)}…</span>`);
      // Preview via /output route isn't ideal — just confirm it's ready
      el('music-preview').style.display = 'none';
    } else {
      el('music-gen-status').innerHTML = `<span class="text-red">✗ ${res.error || 'Failed'}</span>`;
    }
  } catch (err) {
    el('music-gen-status').innerHTML = `<span class="text-red">✗ ${err.message}</span>`;
  } finally {
    el('generate-music-btn').disabled = false;
  }
});

// ── Folder upload (browse a whole folder — required for the hosted app, since
//    the server can't see a user's local disk) ──────────────────────────────
const _MEDIA_RE = /\.(jpe?g|png|webp|bmp|heic|heif|mp4|mov|avi|m4v|webm)$/i;

el('browse-folder-btn')?.addEventListener('click', () => el('folder-input').click());

const _BATCH_BYTES = 40 * 1024 * 1024;   // ~40MB per request — well under the server cap

el('folder-input')?.addEventListener('change', async (e) => {
  const media = Array.from(e.target.files || []).filter(f => _MEDIA_RE.test(f.name));
  if (!media.length) { alert('No images or videos found in that folder.'); return; }

  // Group files into size-bounded batches so a big folder uploads reliably.
  const batches = [];
  let cur = [], curBytes = 0;
  for (const f of media) {
    if (cur.length && curBytes + f.size > _BATCH_BYTES) { batches.push(cur); cur = []; curBytes = 0; }
    cur.push(f); curBytes += f.size;
  }
  if (cur.length) batches.push(cur);

  let uploaded = 0, assetsDir = '';
  el('assets-status').innerHTML = `<span class="muted">Uploading 0/${media.length}…</span>`;
  try {
    for (const batch of batches) {
      const fd = new FormData();
      fd.append('session_id', SESSION_ID);
      batch.forEach(f => fd.append('files', f, f.name));
      const r = await fetch('/upload-folder', { method: 'POST', body: fd });
      if (!r.ok) throw new Error(`server returned ${r.status} (file too large or rejected)`);
      const j = await r.json();
      if (j.error) throw new Error(j.error);
      assetsDir = j.assets_dir;
      uploaded += batch.length;
      el('assets-status').innerHTML = `<span class="muted">Uploading ${uploaded}/${media.length}…</span>`;
    }
    el('assets-dir').value = assetsDir;   // server path used by parse + generate
    el('assets-status').innerHTML = `<span class="text-green">✓ ${uploaded} files uploaded</span>`;
  } catch (err) {
    el('assets-status').innerHTML = `<span style="color:#b91c1c">Upload failed: ${err.message}</span>`;
  } finally {
    e.target.value = '';   // allow re-selecting the same folder
  }
});

// ── Server folder browser (typed paths confined to ASSETS_BROWSE_ROOT) ────

let _sbPath = '';   // current folder shown in the browser

async function sbNavigate(path) {
  const q = path ? `?path=${encodeURIComponent(path)}` : '';
  let res;
  try { res = await fetch(`/browse-dirs${q}`).then(r => r.json()); }
  catch (err) { el('sb-dirs').innerHTML = `<span style="color:#b91c1c">${err.message}</span>`; return; }
  if (res.error) {
    if (path) { sbNavigate(''); return; }  // bad start path → fall back to the root
    el('sb-dirs').innerHTML = `<span style="color:#b91c1c">${res.error}</span>`;
    return;
  }

  _sbPath = res.path;
  el('sb-path').textContent = res.path;
  el('sb-media-count').textContent = res.media_count ? `${res.media_count} media files` : '';
  el('sb-up').disabled = !res.parent;
  el('sb-up').dataset.parent = res.parent || '';
  el('sb-dirs').innerHTML = res.dirs.length
    ? res.dirs.map(d =>
        `<div class="sb-dir" data-dir="${d.replace(/"/g, '&quot;')}"
              style="padding:4px 6px;cursor:pointer;border-radius:4px">📁 ${d}</div>`
      ).join('')
    : '<span class="muted">No subfolders</span>';
  el('sb-dirs').querySelectorAll('.sb-dir').forEach(div =>
    div.addEventListener('click', () => sbNavigate(`${_sbPath}/${div.dataset.dir}`)));
}

el('browse-server-btn')?.addEventListener('click', () => {
  el('server-browser').style.display = 'block';
  sbNavigate(el('assets-dir').value.trim() || '');
});
el('sb-up')?.addEventListener('click', () => {
  const parent = el('sb-up').dataset.parent;
  if (parent) sbNavigate(parent);
});
el('sb-use')?.addEventListener('click', () => {
  el('assets-dir').value = _sbPath;
  el('server-browser').style.display = 'none';
  el('assets-status').innerHTML = `<span class="text-green">✓ folder selected — Parse Frames to match</span>`;
});
el('sb-close')?.addEventListener('click', () => {
  el('server-browser').style.display = 'none';
});

// ── Parse script ──────────────────────────────────────────────────────────

el('parse-btn').addEventListener('click', async () => {
  const script    = el('script-input').value.trim();
  const assetsDir = el('assets-dir').value.trim();
  if (!script) { alert('Paste a script first.'); return; }

  el('parse-btn').disabled = true;
  el('parse-btn').textContent = 'Parsing…';
  el('assets-status').textContent = '';

  try {
    const smartMatch = el('smart-match')?.checked || false;
    const res = await post('/parse-script', {
      script, assets_dir: assetsDir, smart_match: smartMatch,
      subject_name: el('subject-name')?.value.trim() || '',
      subject_description: el('subject-description')?.value.trim() || '',
    });
    if (res.error) { alert('Parse error: ' + res.error); return; }
    if (assetsDir) {
      const matched = (res.frames || []).filter(f => f.visual_path).length;
      el('assets-status').innerHTML = `<span class="text-green">✓ ${matched} photos matched</span>`;
    }
    parsedFrames = res.frames || [];
    parsedCast = res.cast || [];
    postingCaption = res.posting_caption || '';
    frameOverrides = {};
    speakerVoices = {};
    frameApprovals = {};
    previewReady = false;
    framePreviewPath = {};
    renderCastPanel(parsedCast);
    renderFrameCards(parsedFrames);
    renderPostingKitPanel();
    el('frames-card').style.display = 'block';
    el('frames-count').textContent = `${parsedFrames.length} frames`;
    // Auto-apply target duration if already set
    const presetSec = parseInt(el('target-sec').value);
    if (presetSec > 0) redistributeDurations(presetSec);
    else updateTotalDur();
    el('frames-card').scrollIntoView({ behavior: 'smooth', block: 'start' });
    // Show cost estimate (server-computed)
    await loadModels();
    populateModelSelects();
    populateFrameModelSelects();
    renderCostEstimate();
  } catch (err) {
    alert('Error: ' + err.message);
  } finally {
    el('parse-btn').disabled = false;
    el('parse-btn').textContent = 'Parse Frames →';
  }
});

// ── Target duration & redistribution ─────────────────────────────────────

// Highlight the matching preset button
function _syncPresetButtons(sec) {
  document.querySelectorAll('.dur-preset-btn').forEach(btn => {
    btn.classList.toggle('active', parseInt(btn.dataset.sec) === sec);
  });
}

// Distribute targetSec across frames proportionally by word count.
// Silent frames always get 2.5s. Captioned frames share the rest.
function redistributeDurations(targetSec) {
  if (!parsedFrames.length) return;

  const SILENT_DUR = 2.5;
  const MIN_DUR    = 3.0;
  const MAX_DUR    = 12.0;

  const silent    = parsedFrames.filter(f => !f.caption);
  const captioned = parsedFrames.filter(f => f.caption);
  const remaining = targetSec - silent.length * SILENT_DUR;

  if (captioned.length === 0 || remaining <= 0) {
    // Just clamp to auto values
    parsedFrames.forEach(f => {
      const inp = el(`dur-${f.frame_id}`);
      if (inp) inp.value = f.duration;
    });
    updateTotalDur();
    return;
  }

  const totalWords = captioned.reduce((s, f) => s + f.caption.split(' ').length, 0);

  // Proportional allocation clamped to [MIN_DUR, MAX_DUR]
  const raw = captioned.map(f => {
    const w = f.caption.split(' ').length;
    return Math.max(MIN_DUR, Math.min(MAX_DUR, remaining * w / totalWords));
  });

  // Scale so they sum to exactly `remaining` (handles clamping drift)
  const rawSum = raw.reduce((a, b) => a + b, 0);
  const scale  = remaining / rawSum;
  const final  = raw.map(v => Math.round(Math.max(MIN_DUR, Math.min(MAX_DUR, v * scale)) * 2) / 2);

  // Apply to inputs
  let ci = 0;
  parsedFrames.forEach(f => {
    const inp = el(`dur-${f.frame_id}`);
    if (!inp) return;
    inp.value = f.caption ? final[ci++] : SILENT_DUR;
  });

  updateTotalDur();
}

// Read all duration inputs and show live total
function updateTotalDur() {
  let total = 0;
  parsedFrames.forEach(f => {
    const inp = el(`dur-${f.frame_id}`);
    total += inp ? (parseFloat(inp.value) || 0) : (f.duration || 0);
  });
  const badge = el('total-dur-display');
  if (badge) {
    badge.textContent = `≈ ${total.toFixed(0)}s total`;
    badge.className = 'dur-total-badge';
  }
  renderTimelineStrip();
}

// Preset buttons
document.querySelectorAll('.dur-preset-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    const sec = parseInt(btn.dataset.sec);
    _syncPresetButtons(sec);
    if (sec === 0) {
      el('target-sec').value = '';
      // Restore auto durations
      parsedFrames.forEach(f => {
        const inp = el(`dur-${f.frame_id}`);
        if (inp) inp.value = f.duration;
      });
      updateTotalDur();
    } else {
      el('target-sec').value = sec;
      redistributeDurations(sec);
    }
  });
});

el('redistribute-btn').addEventListener('click', () => {
  const sec = parseInt(el('target-sec').value);
  if (sec > 0) {
    _syncPresetButtons(sec);
    redistributeDurations(sec);
  }
});

// ── Cost estimate (server-side — POST /api/estimate is the single source
//    of truth, backed by agents/pricing.py + the real model router) ─────────

let _estimateSeq = 0;
async function renderCostEstimate() {
  if (!parsedFrames.length) return;
  const seq = ++_estimateSeq;
  let b;
  try { b = await post('/api/estimate', buildPayload()); }
  catch (_) { return; }
  if (seq !== _estimateSeq || !b || b.error) return;  // stale or failed — keep last

  const pad = (s) => s + '&nbsp;'.repeat(Math.max(1, 26 - s.length));
  const lines = [];
  if (b.scene?.count)  lines.push(`${pad(`${b.scene.count} × Scene AI (GPT-4.1)`)}<b>$${b.scene.usd.toFixed(2)}</b>`);
  if (b.images?.count) lines.push(`${pad(`${b.images.count} × Image gen (fal.ai/GPT)`)}<b>$${b.images.usd.toFixed(2)}</b>`);
  if (b.edits?.count)  lines.push(`${pad(`${b.edits.count} × Image edits`)}<b>$${b.edits.usd.toFixed(2)}</b>`);
  if (b.animation?.count) lines.push(`${pad(`${b.animation.count} × ${b.animation.provider || 'video'} clips`)}<b>$${b.animation.usd.toFixed(2)}</b>`);
  if (b.lipsync?.count) {
    lines.push(`${pad(`${b.lipsync.count} × Lip sync (Hedra/Sync)`)}<b>$${b.lipsync.usd.toFixed(2)}</b>`);
    lines.push(`${pad('   + lip-sync voice (11Labs)')}<b>$${(b.lipsync_audio?.usd || 0).toFixed(2)}</b>`);
  }
  if (b.music?.usd) lines.push(`${pad('1 × Suno music')}<b>$${b.music.usd.toFixed(2)}</b>`);
  if (b.voice?.usd) lines.push(`${pad('Voice-over (11Labs)')}<b>$${b.voice.usd.toFixed(2)}</b>`);
  lines.push('─'.repeat(34));
  lines.push(`${pad('Total')}<b>~$${b.total.toFixed(2)} USD</b>`);
  if (b.multi_shot) lines.push(`<span style="color:var(--text-dim)">Multi-shot: upper bound — B-roll is picked at render time, so the real cost can only be lower.</span>`);

  el('cost-breakdown').innerHTML = lines.join('<br>');
  el('cost-card').style.display = 'block';
}

// Debounced variant for high-frequency inputs (duration typing etc.)
let _estTimer = null;
function scheduleCostEstimate() {
  clearTimeout(_estTimer);
  _estTimer = setTimeout(renderCostEstimate, 350);
}

// Recompute cost when quality/model/kling-mode/music/multi-shot changes
['quality', 'image-model', 'video-model', 'kling-mode', 'multi-shot'].forEach(id =>
  el(id)?.addEventListener('change', renderCostEstimate));
el('transition')?.addEventListener('change', renderTimelineStrip);
document.querySelectorAll('input[name="music-type"]').forEach(r =>
  r.addEventListener('change', renderCostEstimate)
);

// ── Lipsync voice loader ──────────────────────────────────────────────────

async function _populateLipsyncVoices(frame_id, selectedVoiceId = '') {
  const sel = el(`lipsync-voice-${frame_id}`);
  if (!sel) return;
  if (sel.dataset.loaded) {
    // Already loaded — just update selected value
    if (selectedVoiceId) sel.value = selectedVoiceId;
    return;
  }
  try {
    const res = await fetch('/voices').then(r => r.json());
    const voices = res.voices || [];
    sel.innerHTML = '<option value="">— use global voice —</option>' +
      voices.map(v =>
        `<option value="${v.voice_id}" ${v.voice_id === selectedVoiceId ? 'selected' : ''}>${v.name}</option>`
      ).join('');
    sel.dataset.loaded = '1';
  } catch (_) {}
}

// ── Cast voices (per-speaker voice assignment) ─────────────────────────────

let _voicesCache = null;
async function getVoices() {
  if (_voicesCache) return _voicesCache;
  try { _voicesCache = (await fetch('/voices').then(r => r.json())).voices || []; }
  catch (_) { _voicesCache = []; }
  return _voicesCache;
}

async function renderCastPanel(cast) {
  const panel = el('cast-panel');
  if (!panel) return;
  // Only worth showing when the script has more than one speaker.
  if (!cast || cast.length <= 1) { panel.style.display = 'none'; panel.innerHTML = ''; return; }

  const voices = await getVoices();
  const opts = ['<option value="">— default / auto by gender·age —</option>']
    .concat(voices.map(v => `<option value="${v.voice_id}">${v.name}</option>`)).join('');

  panel.style.display = 'block';
  panel.innerHTML =
    `<div style="font-size:13px;font-weight:600;margin-bottom:6px">🎭 Cast voices `
    + `<span class="muted" style="font-weight:400">— used for lip-sync & voice-over; pick a voice per speaker (optional)</span></div>`
    + cast.map(m => `
        <div style="display:flex;align-items:center;gap:8px;margin:4px 0;font-size:13px">
          <span style="min-width:140px">${m.label} <span class="muted">(${m.gender}/${m.age_bracket})</span></span>
          <select data-speaker="${m.id}" style="flex:1;font-size:12px">${opts}</select>
        </div>`).join('');

  panel.querySelectorAll('select[data-speaker]').forEach(sel => {
    sel.addEventListener('change', () => {
      const sid = sel.dataset.speaker;
      if (sel.value) speakerVoices[sid] = sel.value; else delete speakerVoices[sid];
    });
  });
}

// Build clickable suggestion chips that fill (and leave editable) a target input.
function suggestionChips(items, targetId) {
  if (!items || !items.length) return '';
  const chips = items.map(s =>
    `<button type="button" class="sug-chip" data-target="${targetId}" data-val="${escHtml(s)}">${escHtml(s)}</button>`
  ).join('');
  return `<div class="sug-row">${chips}</div>`;
}

// ── Render frame cards ────────────────────────────────────────────────────

function renderFrameCards(frames) {
  const container = el('frames-container');
  container.innerHTML = '';

  // Speaker <option>s from the detected cast (shown only when >1 speaker).
  const castForSelect = (parsedCast && parsedCast.length > 1) ? parsedCast : null;

  frames.forEach(f => {
    const isSilent = !f.caption;
    const card = document.createElement('div');
    card.className = 'frame-card';
    card.id = `card-${f.frame_id}`;

    // Determine initial visual type from photo_spec
    const initSpec    = f.photo_spec || (f.visual_path ? f.visual_path.split('/').pop() : '');
    const initLayout  = f.layout?.preset || '';
    const isVideo     = /\.(mp4|mov|avi|m4v|webm)$/i.test(f.visual_path || initSpec);
    const isMatched   = !!(f.visual_path) && !initSpec.startsWith('ai_');
    const initType    = initLayout === 'text_card' ? 'text_card'
                      : initSpec === 'ai_portrait' ? 'portrait'
                      : initSpec === 'ai_symbolic'  ? 'symbolic'
                      : isMatched                   ? 'matched'
                      : 'auto';
    const matchedName = isMatched ? (f.visual_path ? f.visual_path.split('/').pop() : initSpec) : '';

    // Visual preview: real photo/video → thumbnail; AI frames → labeled placeholder
    let previewHtml = '';
    if (isMatched && f.visual_path) {
      const mediaUrl = `/media?path=${encodeURIComponent(f.visual_path)}&t=${Date.now()}`;
      if (isVideo) {
        previewHtml = `
          <div class="frame-preview" id="preview-${f.frame_id}">
            <video src="${mediaUrl}#t=0.5" muted playsinline preload="metadata"
                   style="width:90px;height:120px;object-fit:cover;border-radius:6px;background:#000"></video>
            <span class="preview-label">🎬 ${matchedName}</span>
          </div>`;
      } else {
        previewHtml = `
          <div class="frame-preview" id="preview-${f.frame_id}">
            <img src="${mediaUrl}" alt="${matchedName}"
                 style="width:90px;height:120px;object-fit:cover;border-radius:6px;background:#222">
            <span class="preview-label">🖼 ${matchedName}</span>
          </div>`;
      }
    } else if (initType === 'portrait') {
      previewHtml = `<div class="frame-preview placeholder">🎨 AI Portrait<br><span class="preview-label">generated at render</span></div>`;
    } else if (initType === 'symbolic') {
      previewHtml = `<div class="frame-preview placeholder">🖼 AI Symbolic<br><span class="preview-label">objects, no people — generated at render</span></div>`;
    } else if (initType === 'text_card') {
      previewHtml = `<div class="frame-preview placeholder">Text Card<br><span class="preview-label">bold statement on a colour field</span></div>`;
    } else {
      previewHtml = `<div class="frame-preview placeholder">📂 Auto<br><span class="preview-label">next folder file, or AI if none</span></div>`;
    }

    card.innerHTML = `
      <div class="frame-card-header">
        <span class="frame-id">${f.frame_id}</span>
        <span class="frame-caption">${f.caption || '(silent frame)'}</span>
        <span class="frame-dur">${f.duration}s</span>
      </div>
      <div class="frame-card-body">
        ${isSilent ? '<div style="font-size:12px;color:var(--text-dim)">Silent frame — visual only</div>' : ''}

        ${previewHtml}

        <div class="frame-iter-row" id="iter-${f.frame_id}" style="display:none">
          <button type="button" class="btn-secondary btn-small redo-still-btn" data-frame="${f.frame_id}">🔄 Redo still</button>
          <button type="button" class="btn-secondary btn-small redo-motion-btn" data-frame="${f.frame_id}">Redo motion</button>
          <button type="button" class="btn-secondary btn-small suggest-frame-btn" data-frame="${f.frame_id}" title="Look at this frame's image and suggest the best camera move + director note">✨ Suggest from image</button>
          <label class="approval-toggle" data-frame="${f.frame_id}" title="Untick to skip paid animation — this frame uses free Ken Burns instead">
            <input type="checkbox" class="approval-cb" data-frame="${f.frame_id}" checked>
            <span class="approval-label">✓ Approved for animation</span>
          </label>
        </div>

        <div class="visual-selector" data-frame="${f.frame_id}">
          <label class="vis-opt ${initType === 'auto' ? 'active' : ''}" data-type="auto">
            <input type="radio" name="vis-${f.frame_id}" value="auto" ${initType === 'auto' ? 'checked' : ''}>
            Auto
          </label>
          <label class="vis-opt ${initType === 'matched' ? 'active' : ''}" data-type="matched">
            <input type="radio" name="vis-${f.frame_id}" value="matched" ${initType === 'matched' ? 'checked' : ''}>
            📁 From Folder
          </label>
          <label class="vis-opt ${initType === 'uploaded' ? 'active' : ''}" data-type="uploaded">
            <input type="radio" name="vis-${f.frame_id}" value="uploaded" ${initType === 'uploaded' ? 'checked' : ''}>
            📷 Upload Photo
          </label>
          <label class="vis-opt ${initType === 'portrait' ? 'active' : ''}" data-type="portrait">
            <input type="radio" name="vis-${f.frame_id}" value="portrait" ${initType === 'portrait' ? 'checked' : ''}>
            🎨 AI Portrait
          </label>
          <label class="vis-opt ${initType === 'symbolic' ? 'active' : ''}" data-type="symbolic">
            <input type="radio" name="vis-${f.frame_id}" value="symbolic" ${initType === 'symbolic' ? 'checked' : ''}>
            🖼 AI Symbolic
          </label>
          <label class="vis-opt ${initType === 'text_card' ? 'active' : ''}" data-type="text_card">
            <input type="radio" name="vis-${f.frame_id}" value="text_card" ${initType === 'text_card' ? 'checked' : ''}>
            Text Card
          </label>
        </div>

        <div class="frame-upload ${initType === 'uploaded' ? 'visible' : ''}" id="upload-${f.frame_id}">
          <label class="upload-label">
            📁 Choose photo
            <input type="file" accept="image/*,video/*" data-frame="${f.frame_id}">
          </label>
          <span class="upload-filename" id="fname-${f.frame_id}"></span>
        </div>

        ${castForSelect ? `
        <div class="director-note-row" style="margin-top:6px">
          <label style="font-size:12px;color:var(--text-dim);min-width:70px">🎭 Speaker</label>
          <select id="speaker-${f.frame_id}" style="font-size:12px;flex:1">
            ${castForSelect.map(m =>
              `<option value="${m.id}" ${m.id === (f.speaker_id || 'narrator') ? 'selected' : ''}>`
              + `${m.label} (${m.gender}/${m.age_bracket})</option>`).join('')}
          </select>
        </div>` : ''}

        <div class="director-note-row">
          <input type="text" placeholder="Director note (optional): e.g. show anger not just sadness"
            id="note-${f.frame_id}" value="${escHtml(f.director_note || '')}">
        </div>
        ${suggestionChips(f.suggestions?.notes, 'note-' + f.frame_id)}

        <div class="director-note-row" style="margin-top:6px">
          <input type="text"
            placeholder="✏️ Image edit (optional): e.g. add thunderstorm, more trees, warmer sunset light"
            id="edit-${f.frame_id}" value="${escHtml(f.edit_prompt || '')}"
            style="border-color: ${f.edit_prompt ? 'var(--orange)' : ''}">
        </div>
        ${suggestionChips(f.suggestions?.edits, 'edit-' + f.frame_id)}

        <div class="director-note-row" style="margin-top:6px">
          <label style="font-size:12px;color:var(--text-dim);min-width:70px">🎥 Camera</label>
          ${cameraSelectHtml(f)}
          ${f.camera_auto ? `<span style="font-size:11px;color:#a78bfa;white-space:nowrap">✨ auto: ${f.camera_reason || 'best for this beat'}</span>` : ''}
        </div>

        ${isSilent ? '' : `
        <div class="caption-style-row" style="margin-top:6px;display:flex;gap:8px;align-items:center;flex-wrap:wrap">
          <span style="font-size:12px;color:var(--text-dim);min-width:70px">💬 Caption</span>
          <select id="cappos-${f.frame_id}" style="font-size:12px" title="Caption position for this frame">
            <option value="">Position: default</option>
            <option value="bottom">Bottom</option>
            <option value="middle">Middle</option>
            <option value="top">Top</option>
          </select>
          <select id="caplines-${f.frame_id}" style="font-size:12px" title="Max caption lines for this frame">
            <option value="">Lines: default</option>
            <option value="1">1 line</option>
            <option value="2">2 lines</option>
            <option value="3">3 lines</option>
          </select>
        </div>`}

        <div class="director-note-row" style="margin-top:6px">
          <select id="model-${f.frame_id}" style="font-size:12px">
            <option value="">🤖 Model: Auto (best per shot)</option>
          </select>
        </div>

        <div class="lipsync-row" style="margin-top:8px;display:flex;align-items:center;gap:10px;flex-wrap:wrap">
          <label style="display:flex;align-items:center;gap:5px;cursor:pointer;font-size:13px;white-space:nowrap">
            <input type="checkbox" id="lipsync-${f.frame_id}" ${f.lipsync ? 'checked' : ''}>
            🎙 Lip Sync
          </label>
          <div id="lipsync-extra-${f.frame_id}" style="display:${f.lipsync ? 'flex' : 'none'};align-items:center;gap:6px;flex:1;flex-wrap:wrap">
            <select id="lipsync-voice-${f.frame_id}" style="font-size:12px;flex:1;min-width:160px">
              <option value="">— use global voice —</option>
            </select>
            <span style="font-size:11px;color:var(--text-dim)">duration driven by audio</span>
          </div>
        </div>

        ${isVideo ? `
        <div class="duration-row" style="margin-top:6px">
          <label class="dur-label" style="color:var(--text-dim)">Video start</label>
          <input type="number" id="vstart-${f.frame_id}" class="dur-input"
            value="${f.video_start_sec || 0}" min="0" step="0.5" style="width:60px">
          <span class="dur-unit">s</span>
          <span class="dur-hint">skip this many seconds into the clip</span>
        </div>` : ''}

        <div class="duration-row">
          <label class="dur-label">Duration</label>
          <input type="number" id="dur-${f.frame_id}" class="dur-input"
            value="${f.duration}" min="2" max="15" step="0.5"
            ${f.lipsync ? 'readonly style="opacity:0.5;cursor:not-allowed"' : ''}>
          <span class="dur-unit">s</span>
          <span class="dur-hint" id="dur-hint-${f.frame_id}">${f.lipsync ? '(driven by audio)' : '(auto: ' + f.duration + 's)'}</span>
        </div>
      </div>
    `;

    container.appendChild(card);

    // Suggestion chips → fill the target input (still fully editable after)
    card.querySelectorAll('.sug-chip').forEach(chip => {
      chip.addEventListener('click', () => {
        const inp = el(chip.dataset.target);
        if (inp) { inp.value = chip.dataset.val; inp.dispatchEvent(new Event('input')); }
      });
    });

    // Per-frame redo: regenerate just this still with the current card settings.
    const redoBtn = card.querySelector('.redo-still-btn');
    if (redoBtn) redoBtn.addEventListener('click', () => redoStill(f.frame_id, redoBtn));
    const redoMotionBtn = card.querySelector('.redo-motion-btn');
    if (redoMotionBtn) redoMotionBtn.addEventListener('click', () => redoMotion(f.frame_id, redoMotionBtn));
    const suggestBtn = card.querySelector('.suggest-frame-btn');
    if (suggestBtn) suggestBtn.addEventListener('click', () => suggestFromImage(f.frame_id, suggestBtn));

    // Approval toggle: untick → frame uses free Ken Burns instead of paid animation.
    const apprCb = card.querySelector('.approval-cb');
    if (apprCb) apprCb.addEventListener('change', () => {
      frameApprovals[f.frame_id] = apprCb.checked;
      card.classList.toggle('frame-rejected', !apprCb.checked);
      const lbl = card.querySelector('.approval-label');
      if (lbl) lbl.textContent = apprCb.checked ? '✓ Approved for animation' : '✗ Free Ken Burns (skipped)';
      scheduleCostEstimate();
    });

    // Speaker dropdown → store override so the render uses the right face + voice
    const spkSel = el(`speaker-${f.frame_id}`);
    if (spkSel) spkSel.addEventListener('change', () => {
      if (!frameOverrides[f.frame_id]) frameOverrides[f.frame_id] = {};
      frameOverrides[f.frame_id].speaker_id = spkSel.value;
    });

    // Wire visual type radios
    card.querySelectorAll('.vis-opt').forEach(opt => {
      opt.addEventListener('click', () => {
        const type = opt.dataset.type;
        // Update active state
        card.querySelectorAll('.vis-opt').forEach(o => o.classList.remove('active'));
        opt.classList.add('active');
        opt.querySelector('input').checked = true;

        // Show/hide upload
        const uploadRow = el(`upload-${f.frame_id}`);
        uploadRow.classList.toggle('visible', type === 'uploaded');

        // Store override
        if (!frameOverrides[f.frame_id]) frameOverrides[f.frame_id] = {};
        frameOverrides[f.frame_id].photo_spec =
            type === 'auto'     ? ''
          : type === 'matched'  ? f.photo_spec   // keep original filename
          : type === 'portrait' ? 'ai_portrait'
          : type === 'symbolic' ? 'ai_symbolic'
          : type === 'text_card' ? ''
          : 'uploaded';
        frameOverrides[f.frame_id].layout = type === 'text_card'
          ? { preset: 'text_card', text: f.caption || '' }
          : {};
      });
    });

    // Live total + cost update when user edits a duration
    const durInp = el(`dur-${f.frame_id}`);
    if (durInp) durInp.addEventListener('input', () => { updateTotalDur(); scheduleCostEstimate(); });

    // Lipsync checkbox wiring
    const lipsyncCb = el(`lipsync-${f.frame_id}`);
    if (lipsyncCb) {
      lipsyncCb.addEventListener('change', () => {
        const on       = lipsyncCb.checked;
        const extra    = el(`lipsync-extra-${f.frame_id}`);
        const durField = el(`dur-${f.frame_id}`);
        const durHint  = el(`dur-hint-${f.frame_id}`);
        if (extra)    extra.style.display = on ? 'flex' : 'none';
        if (durField) { durField.readOnly = on; durField.style.opacity = on ? '0.5' : '1'; }
        if (durHint)  durHint.textContent = on ? '(driven by audio)' : `(auto: ${f.duration}s)`;
        // Populate voice list on first enable
        const vSel = el(`lipsync-voice-${f.frame_id}`);
        if (on && vSel && vSel.options.length <= 1) _populateLipsyncVoices(f.frame_id);
        if (!frameOverrides[f.frame_id]) frameOverrides[f.frame_id] = {};
        frameOverrides[f.frame_id].lipsync = on;
        renderCostEstimate();
      });
      // Populate voice list if lipsync is already enabled from parsed frame
      if (f.lipsync) {
        const vSel = el(`lipsync-voice-${f.frame_id}`);
        if (vSel) _populateLipsyncVoices(f.frame_id, f.voice_override || '');
      }
    }

    // Brand mode hook: let brand.js add the product-beat toggle to this card.
    if (window.HOB_MODE === 'brand' && typeof window.augmentBrandCard === 'function') {
      window.augmentBrandCard(card, f);
    }

    // Wire photo upload
    card.querySelector(`input[type="file"][data-frame]`).addEventListener('change', async (e) => {
      const file = e.target.files[0];
      if (!file) return;
      el(`fname-${f.frame_id}`).textContent = '⏳ Uploading…';

      const fd = new FormData();
      fd.append('photo', file);
      fd.append('session_id', SESSION_ID);
      fd.append('frame_id', f.frame_id);

      try {
        const res = await postForm('/upload-photo', fd);
        if (res.tmp_path) {
          if (!frameOverrides[f.frame_id]) frameOverrides[f.frame_id] = {};
          frameOverrides[f.frame_id].photo_tmp_path = res.tmp_path;
          el(`fname-${f.frame_id}`).textContent = '✓ ' + file.name;
          // Update the preview thumbnail to the uploaded file
          const prev = el(`preview-${f.frame_id}`);
          if (prev) {
            const url = `/media?path=${encodeURIComponent(res.tmp_path)}&t=${Date.now()}`;
            const isVid = /\.(mp4|mov|avi|m4v|webm)$/i.test(file.name);
            prev.innerHTML = isVid
              ? `<video src="${url}#t=0.5" muted playsinline preload="metadata" style="width:90px;height:120px;object-fit:cover;border-radius:6px;background:#000"></video><span class="preview-label">🎬 ${file.name}</span>`
              : `<img src="${url}" style="width:90px;height:120px;object-fit:cover;border-radius:6px;background:#222"><span class="preview-label">🖼 ${file.name}</span>`;
            prev.classList.remove('placeholder');
          }
        } else {
          el(`fname-${f.frame_id}`).textContent = '✗ Upload failed';
        }
      } catch {
        el(`fname-${f.frame_id}`).textContent = '✗ Error';
      }
    });
  });
  renderTimelineStrip();
}

function _frameDuration(f) {
  const inp = el(`dur-${f.frame_id}`);
  return inp ? (parseFloat(inp.value) || f.duration || 0) : (f.duration || 0);
}

function renderTimelineStrip() {
  const strip = el('timeline-strip');
  if (!strip || !parsedFrames.length) return;
  const transition = el('transition')?.value || 'crossfade';
  const overlap = transition === 'none' ? 0 : 0.4;
  const durations = parsedFrames.map(_frameDuration);
  const total = durations.reduce((s, d, i) => s + d - (i ? overlap : 0), 0);
  strip.innerHTML = parsedFrames.map((f, i) => {
    const dur = durations[i] || 0;
    const pct = total > 0 ? Math.max(6, (dur / total) * 100) : 10;
    return `<div class="timeline-seg" style="flex-basis:${pct}%">
      <div class="timeline-id">${escHtml(f.frame_id)}</div>
      <div class="timeline-caption">${escHtml(f.caption || 'silent')}</div>
      <div class="timeline-dur">${dur.toFixed(1)}s</div>
    </div>`;
  }).join('');
}

function renderPostingKitPanel() {
  const panel = el('posting-kit-card');
  if (!panel) return;
  if (window.HOB_MODE === 'brand' || !parsedFrames.length) {
    panel.style.display = 'none';
    return;
  }
  panel.style.display = 'block';
  const seed = el('posting-caption-seed');
  if (seed) seed.value = postingCaption || parsedFrames.map(f => f.caption).filter(Boolean).join('\n');
  const cover = el('posting-cover-frame');
  if (cover) {
    cover.innerHTML = parsedFrames.map((f, i) =>
      `<option value="${f.frame_id}" ${i === 0 ? 'selected' : ''}>${f.frame_id} — ${(f.caption || 'silent').slice(0, 48)}</option>`
    ).join('');
  }
  const out = el('posting-kit-output');
  if (out) out.innerHTML = '<span class="muted">Generate when the story frames are ready. Story-mode only; brand copy remains operator-supplied.</span>';
}

el('posting-kit-btn')?.addEventListener('click', async () => {
  if (!parsedFrames.length) return;
  const btn = el('posting-kit-btn');
  btn.disabled = true;
  btn.textContent = 'Generating...';
  try {
    const res = await post('/posting-kit', {
      mode: window.HOB_MODE === 'brand' ? 'brand' : 'story',
      frames: buildPayload().frames,
      posting_caption: el('posting-caption-seed')?.value || postingCaption,
      cover_frame_id: el('posting-cover-frame')?.value || parsedFrames[0]?.frame_id || '',
    });
    if (res.error) throw new Error(res.error);
    el('posting-kit-output').innerHTML = `
      <div class="posting-result-block"><strong>Caption</strong><textarea readonly>${escHtml(res.caption || '')}</textarea></div>
      <div class="posting-result-block"><strong>Hashtags</strong><div class="posting-tags">${(res.hashtags || []).map(escHtml).join(' ')}</div></div>
      <div class="posting-result-block"><strong>Cover frame</strong> ${escHtml(res.cover_frame_id || '')}</div>
    `;
  } catch (err) {
    el('posting-kit-output').innerHTML = `<span class="text-red">${escHtml(err.message)}</span>`;
  } finally {
    btn.disabled = false;
    btn.textContent = 'Generate posting kit';
  }
});

function escHtml(s) {
  return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// ── Build payload (shared by Preview + Generate) ──────────────────────────

// Approval gate: return the list of frame_ids approved for paid animation, or
// null when nothing has been rejected (null = animate everything, back-compat).
function approvedFrameIds() {
  const rejected = parsedFrames.some(f => frameApprovals[f.frame_id] === false);
  if (!rejected) return null;
  return parsedFrames.filter(f => frameApprovals[f.frame_id] !== false).map(f => f.frame_id);
}

function buildPayload() {
  const musicType = document.querySelector('input[name="music-type"]:checked').value;
  let musicPath = null, voiceId = null;
  if (musicType === 'upload') musicPath = uploadedMusicPath;
  if (musicType === 'generate') musicPath = generatedMusicPath;
  if (musicType === 'voiceover') voiceId = el('voice-select').value;

  const frames = parsedFrames.map(f => {
    const noteEl      = el(`note-${f.frame_id}`);
    const durEl       = el(`dur-${f.frame_id}`);
    const editEl      = el(`edit-${f.frame_id}`);
    const motionEl    = el(`motion-${f.frame_id}`);
    const vstartEl    = el(`vstart-${f.frame_id}`);
    const lipsyncCbEl = el(`lipsync-${f.frame_id}`);
    const lipsyncVEl  = el(`lipsync-voice-${f.frame_id}`);
    const ov          = frameOverrides[f.frame_id] || {};
    return {
      frame_id:        f.frame_id,
      caption:         f.caption,
      duration:        durEl ? parseFloat(durEl.value) || f.duration : f.duration,
      photo_spec:      ov.photo_spec !== undefined ? ov.photo_spec : (f.photo_spec || ''),
      photo_tmp_path:  ov.photo_tmp_path || '',
      visual_path:      f.visual_path || '',
      director_note:   noteEl    ? noteEl.value.trim()              : (f.director_note   || ''),
      edit_prompt:     editEl    ? editEl.value.trim()               : (f.edit_prompt     || ''),
      motion_override: motionEl  ? motionEl.value.trim()             : (f.motion_override || ''),
      lipsync:         lipsyncCbEl ? lipsyncCbEl.checked            : (f.lipsync         || false),
      voice_override:  lipsyncVEl  ? lipsyncVEl.value               : (f.voice_override  || ''),
      image_model_override: ov.model_override || '',
      video_model_override: ov.model_override || '',
      video_start_sec: vstartEl  ? parseFloat(vstartEl.value)||0    : (f.video_start_sec  || 0),
      speaker_id:      ov.speaker_id || f.speaker_id || 'narrator',
      product_beat:    ov.product_beat || false,   // brand mode (ignored in story mode)
      layout:           ov.layout !== undefined ? ov.layout : (f.layout || {}),
      caption_position:  el(`cappos-${f.frame_id}`)?.value || '',     // '' = use global
      caption_max_lines: el(`caplines-${f.frame_id}`)?.value || '',   // '' = use global
    };
  });

  const payload = {
    session_id:          SESSION_ID,
    subject_name:        el('subject-name')?.value.trim() || '',
    subject_description: el('subject-description')?.value.trim() || '',
    assets_dir:          el('assets-dir').value.trim(),
    script:              el('script-input').value,
    frames,
    cast:                parsedCast,
    speaker_voices:      speakerVoices,
    multi_shot:          el('multi-shot')?.checked || false,
    face_ref:            el('face-ref')?.checked || false,
    ip:                  el('ip')?.value || '',
    approved_frame_ids:  approvedFrameIds(),
    mood:                el('mood').value,
    quality:             el('quality').value,
    music_type:          musicType,
    music_path:          musicPath,
    voice_id:            voiceId,
    transition:          el('transition').value,
    image_model:         el('image-model')?.value || 'auto',
    video_model:         el('video-model')?.value || 'auto',
    provider:            (el('video-model')?.value === 'kenburns') ? 'kenburns' : 'kling',
    kling_mode:          el('kling-mode').value,
    orientation:         el('orientation').value,
    caption_style: {
      font:      el('caption-font').value,
      size:      parseInt(el('caption-size').value) || 52,
      color:     el('caption-color').value,
      position:  el('caption-position').value,
      enabled:   el('caption-enabled') ? el('caption-enabled').checked : true,
      max_lines: parseInt(el('caption-max-lines')?.value || '0') || 0,
    },
  };
  // Brand mode: merge mode + brand block (brand.js owns the panel).
  if (window.HOB_MODE === 'brand' && typeof window.buildBrandPayload === 'function') {
    Object.assign(payload, window.buildBrandPayload());
  }
  return payload;
}

// ── Preview Stills (cheap pre-check before paying for animation) ───────────

el('preview-btn').addEventListener('click', async () => {
  if (!parsedFrames.length) { alert('Parse your script first.'); return; }
  const btn = el('preview-btn');
  btn.disabled = true;
  el('preview-status').textContent = '⏳ Generating still images… (scene design + AI images, no animation)';
  el('progress-panel').style.display = 'block';
  el('progress-log').innerHTML = '';
  el('progress-status').textContent = 'Generating stills…';

  try {
    const res = await post('/preview', buildPayload());
    if (res.error) { el('preview-status').textContent = '✗ ' + res.error; btn.disabled = false; return; }
    // Stream logs, then fetch the stills when done
    const es = new EventSource(`/progress/${res.run_id}`);
    es.onmessage = async (event) => {
      const d = JSON.parse(event.data);
      if (d.line) logLine(d.line, d.line.includes('✓') ? 'ok' : 'info');
      if (d.done) {
        es.close();
        btn.disabled = false;
        if (d.status === 'done') {
          const r = await fetch(`/preview-result/${res.run_id}`).then(x => x.json());
          applyPreviewStills(r.stills || []);
          el('preview-status').innerHTML = '<span class="text-green">✓ Stills ready — review below. Add edits & Preview again, or Generate Video (reuses these, only pays for animation).</span>';
          el('progress-status').innerHTML = '<span class="text-green">✓ Stills ready</span>';
        } else {
          el('preview-status').textContent = '✗ Preview failed — see log.';
        }
      }
    };
    es.onerror = () => { es.close(); btn.disabled = false; el('preview-status').textContent = 'Connection lost.'; };
  } catch (err) {
    el('preview-status').textContent = '✗ ' + err.message;
    btn.disabled = false;
  }
});

function applyPreviewStills(stills) {
  previewReady = true;
  stills.forEach(s => {
    const frame = parsedFrames.find(f => f.frame_id === s.frame_id);
    if (frame && s.exists) {
      frame.visual_path = s.path;
      framePreviewPath[s.frame_id] = s.path;   // for the ✨ vision suggest button
    }
    const prev = el(`preview-${s.frame_id}`);
    if (prev && s.exists) {
      const url = `/media?path=${encodeURIComponent(s.path)}&t=${Date.now()}`;
      prev.classList.remove('placeholder');
      prev.innerHTML = s.is_video
        ? `<video src="${url}#t=0.5" muted playsinline preload="metadata" style="width:90px;height:120px;object-fit:cover;border-radius:6px;background:#000"></video><span class="preview-label">🎬 generated still</span>`
        : `<img src="${url}" style="width:90px;height:120px;object-fit:cover;border-radius:6px;background:#222"><span class="preview-label">✅ this image will be animated</span>`;
    }
    // Reveal the per-frame redo + approval controls once a still exists.
    const iter = el(`iter-${s.frame_id}`);
    if (iter && s.exists) iter.style.display = 'flex';
  });
}

// Per-frame redo: regenerate ONE still without re-running the pipeline.
async function redoStill(frameId, btn) {
  const f = parsedFrames.find(x => x.frame_id === frameId);
  if (!f) return;
  if (btn) { btn.disabled = true; btn.textContent = '⏳ Redoing…'; }
  // Build the full payload, then send only this one frame's current settings.
  const payload = buildPayload();
  const frame = payload.frames.find(x => x.frame_id === frameId);
  try {
    const res = await post('/redo-still', { ...payload, frame });
    if (res.error) { alert('Redo failed: ' + res.error); return; }
    if (res.exists) {
      applyPreviewStills([res]);
      const prev = el(`preview-${frameId}`);
      if (prev) prev.querySelector('.preview-label')?.replaceChildren(document.createTextNode('🔄 redone'));
    } else {
      alert('Redo produced no image — check the frame settings.');
    }
  } catch (e) {
    alert('Redo error: ' + e.message);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '🔄 Redo still'; }
  }
}

// Vision-grounded per-frame suggestion (post-Preview, triggered, cached server-side).
// Looks at THIS frame's still → sets the best camera in the dropdown + offers a
// director-note chip to apply or ignore. Image edits stay the operator's call.
async function suggestFromImage(frameId, btn) {
  const f = parsedFrames.find(x => x.frame_id === frameId);
  const imgPath = framePreviewPath[frameId] || f?.visual_path;
  if (!imgPath) { alert("Run 👁 Preview Stills first — there's no image to look at yet."); return; }
  if (btn) { btn.disabled = true; btn.textContent = '⏳ Looking…'; }
  try {
    const res = await post('/suggest-frame', { image_path: imgPath, caption: f?.caption || '' });
    if (res.error) { alert(res.error); return; }
    // Best camera → straight into the dropdown (user can still alter it).
    if (res.camera) {
      const sel = el(`motion-${frameId}`);
      if (sel) {
        if (![...sel.options].some(o => o.value === res.camera)) sel.add(new Option(res.camera, res.camera));
        sel.value = res.camera;
        sel.style.outline = '2px solid #a78bfa';
        setTimeout(() => { sel.style.outline = ''; }, 1500);
      }
    }
    // Director note → a chip the operator can click to apply (then edit) or ignore (discard).
    if (res.note) {
      const noteInput = el(`note-${frameId}`);
      let row = el(`vsug-${frameId}`);
      if (!row && noteInput) {
        row = document.createElement('div');
        row.id = `vsug-${frameId}`;
        row.className = 'sug-row';
        noteInput.parentElement.insertAdjacentElement('afterend', row);
      }
      if (row) {
        row.innerHTML = `<span class="sug-chip" title="Click to use (then edit), or ignore to discard">✨ ${escHtml(res.note)}</span>`;
        row.querySelector('.sug-chip').addEventListener('click', () => {
          if (noteInput) { noteInput.value = res.note; noteInput.dispatchEvent(new Event('input')); }
          row.remove();
        });
      }
    }
    if (!res.camera && !res.note) alert('No suggestion returned for this frame.');
  } catch (e) {
    alert('Suggest error: ' + e.message);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '✨ Suggest from image'; }
  }
}

async function redoMotion(frameId, btn) {
  const f = parsedFrames.find(x => x.frame_id === frameId);
  if (!f) return;
  if (!f.visual_path) {
    alert('Preview or render a still for this frame before redoing motion.');
    return;
  }
  if (btn) { btn.disabled = true; btn.textContent = 'Redoing motion...'; }
  const payload = buildPayload();
  const frame = payload.frames.find(x => x.frame_id === frameId);
  try {
    const res = await post('/redo-motion', { ...payload, frame });
    if (res.error) { alert('Redo motion failed: ' + res.error); return; }
    if (res.url) onClipReady(frameId, res.url);
    if (res.output_ready && currentRunId) showOutput(currentRunId);
  } catch (e) {
    alert('Redo motion error: ' + e.message);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Redo motion'; }
  }
}

// ── Run pipeline ──────────────────────────────────────────────────────────

el('run-btn').addEventListener('click', async () => {
  if (!parsedFrames.length) {
    alert('Parse your script first (click "Parse Frames →").');
    return;
  }

  const payload = buildPayload();

  // Show progress
  el('progress-panel').style.display = 'block';
  el('output-panel').style.display   = 'none';
  el('progress-log').innerHTML        = '';
  el('progress-status').textContent   = 'Running…';
  el('run-btn').disabled              = true;
  el('progress-panel').scrollIntoView({ behavior: 'smooth' });

  try {
    const res = await post('/run', payload);
    if (res.missing && res.missing.length) {
      // Brand hard-block: required items missing — show the checklist, don't render.
      el('run-btn').disabled = false;
      const msg = 'Cannot render — complete these brand requirements:\n• ' + res.missing.join('\n• ');
      if (typeof window.showBrandMissing === 'function') window.showBrandMissing(res.missing);
      else alert(msg);
      logLine('✗ ' + msg, 'err');
      return;
    }
    if (res.error) { logLine('✗ ' + res.error, 'err'); return; }
    currentRunId = res.run_id;
    streamProgress(res.run_id);
  } catch (err) {
    logLine('✗ ' + err.message, 'err');
    el('run-btn').disabled = false;
  }
});

// ── SSE Progress stream ───────────────────────────────────────────────────

function streamProgress(runId) {
  // Mark every frame's preview as "animating…" so the editor sees live progress.
  parsedFrames.forEach(f => {
    const prev = el(`preview-${f.frame_id}`);
    if (prev && !prev.querySelector('video.clip-preview')) {
      const badge = prev.querySelector('.clip-status') || document.createElement('span');
      badge.className = 'clip-status';
      badge.textContent = '⏳ animating…';
      if (!badge.parentNode) prev.appendChild(badge);
    }
  });

  const es = new EventSource(`/progress/${runId}`);

  es.onmessage = (event) => {
    const data = JSON.parse(event.data);

    // Progressive clip reveal: swap the still for the finished clip in its card.
    if (data.type === 'clip_ready' && data.frame_id) {
      onClipReady(data.frame_id, data.url);
      return;
    }

    if (data.line) {
      const cls = data.line.startsWith('✓') || data.line.includes('✓') ? 'ok'
                : data.line.startsWith('✗') || data.line.includes('Error') ? 'err'
                : data.line.startsWith('[Pipeline]') || data.line.startsWith('[Assembler]') ? 'head'
                : 'info';
      logLine(data.line, cls);
    }

    if (data.done) {
      es.close();
      el('run-btn').disabled = false;
      if (data.status === 'done') {
        el('progress-status').innerHTML = '<span class="text-green">✓ Complete</span>';
        showOutput(runId);
      } else {
        el('progress-status').innerHTML = '<span class="text-red">✗ Failed</span>';
      }
    }
  };

  es.onerror = () => {
    es.close();
    logLine('Connection lost. Check if the server is still running.', 'err');
    el('run-btn').disabled = false;
  };
}

// Progressive reveal: replace a frame's still with its finished clip the moment
// it lands, so editors judge direction early instead of waiting for the full cut.
function onClipReady(frameId, url) {
  const prev = el(`preview-${frameId}`);
  if (!prev) return;
  prev.classList.remove('placeholder');
  prev.innerHTML =
    `<video class="clip-preview" src="${url}?t=${Date.now()}" muted playsinline loop autoplay preload="metadata"
            style="width:90px;height:120px;object-fit:cover;border-radius:6px;background:#000"></video>`
    + `<span class="preview-label clip-status ok">🎬 clip ready</span>`;
}

function logLine(text, cls = 'log-line') {
  const log = el('progress-log');
  const div = document.createElement('div');
  div.className = 'log-line ' + cls;
  div.textContent = text;
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
}

// ── Output ────────────────────────────────────────────────────────────────

function showOutput(runId) {
  el('output-panel').style.display = 'block';
  el('output-video').src = `/output/${runId}?t=${Date.now()}`;
  el('download-btn').href = `/download/${runId}`;
  const exportBtn = el('export-btn');
  if (exportBtn) exportBtn.href = `/export/${runId}`;
  el('output-panel').scrollIntoView({ behavior: 'smooth' });
}

// ── AI credits (live vendor balances) ──────────────────────────────────────
const _BAL_ICON = { ok: '✓', error: '✗', no_key: '·', unsupported: '—' };

async function loadBalances() {
  const body = el('balances-body');
  if (!body) return;
  body.innerHTML = '<span class="muted">Checking vendor balances…</span>';
  try {
    const res = await fetch('/balances').then(r => r.json());
    const rows = res.balances || [];
    if (!rows.length) { body.innerHTML = '<span class="muted">No vendors configured.</span>'; return; }
    const html = rows.map(r => {
      const icon = _BAL_ICON[r.status] || '?';
      // A live "0" balance is an empty wallet → flag red.
      const empty = r.status === 'ok' && Number(r.balance) <= 0;
      const cls = r.status === 'ok' ? (empty ? 'bal-empty' : 'bal-ok')
                : r.status === 'error' ? 'bal-err' : 'bal-dim';
      const value = r.status === 'ok'
        ? `${Number(r.balance).toLocaleString()} ${r.unit}${empty ? ' — recharge!' : ''}`
        : r.status.replace('_', ' ');
      return `<div class="bal-row ${cls}">
                <span class="bal-icon">${icon}</span>
                <span class="bal-label">${r.label}</span>
                <span class="bal-value">${value}</span>
                <span class="bal-detail muted">${r.detail || ''}</span>
              </div>`;
    }).join('');
    body.innerHTML = `<div class="bal-summary muted">${res.live_count} of ${res.total} vendors report a live balance</div>${html}`;
  } catch (e) {
    body.innerHTML = `<span class="bal-err">Could not load balances: ${e.message}</span>`;
  }
}

el('balances-refresh')?.addEventListener('click', loadBalances);
loadBalances();   // probe on page load so credits are visible upfront

// ── IP / property watermark dropdown ───────────────────────────────────────
async function loadIps() {
  const sel = el('ip');
  if (!sel) return;
  try {
    const res = await fetch('/ips').then(r => r.json());
    (res.ips || []).forEach(ip => {
      const opt = document.createElement('option');
      opt.value = ip.id;
      // Flag IPs whose PNG isn't added yet so the operator knows it won't overlay.
      opt.textContent = ip.available ? ip.label : `${ip.label} (no image yet)`;
      sel.appendChild(opt);
    });
  } catch (_) { /* leave the "No IP watermark" default */ }
}
loadIps();
