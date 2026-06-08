/* HOBAILabs Web UI */
'use strict';

// ── Session ID (persists for photo uploads across the form fill) ──────────
const SESSION_ID = crypto.randomUUID();

// State
let parsedFrames = [];          // [{frame_id, caption, duration, photo_spec, director_note}]
let frameOverrides = {};        // frame_id → {photo_spec, photo_tmp_path, director_note}
let generatedMusicPath = null;
let uploadedMusicPath = null;
let currentRunId = null;
let _pricing = null;            // loaded once from /pricing

async function loadPricing() {
  if (_pricing) return _pricing;
  try {
    _pricing = await fetch('/pricing').then(r => r.json());
  } catch (_) {
    _pricing = {
      kling: { standard_5s_usd: 0.08, pro_5s_usd: 0.14 },
      image_gen: { flux_portrait_usd: 0.05, openai_gpt_image_usd: 0.04, openai_edit_usd: 0.04 },
      music: { suno_song_usd: 0.05 },
    };
  }
  return _pricing;
}

// ── Model catalog + client-side router (mirrors agents/model_router.py) ─────
let _models = null;             // loaded once from /models  {models, routing, defaults}
const _VIDEO_RE = /\.(mp4|mov|avi|m4v|webm)$/i;

async function loadModels() {
  if (_models) return _models;
  try { _models = await fetch('/models').then(r => r.json()); }
  catch (_) { _models = { models: {}, routing: {}, defaults: {} }; }
  return _models;
}

function modelField(id, field, dflt) {
  const m = (_models && _models.models && _models.models[id]) || {};
  return (field in m) ? m[field] : dflt;
}

function costTierFromQuality(q) {
  return (['dev', 'draft', 'preview'].includes((q || 'dev').toLowerCase())) ? 'draft' : 'premium';
}

function _isRealMedia(shot) {
  const spec = (shot.photo_spec || '').trim();
  if (spec.startsWith('ai_')) return false;
  if (spec) return true;
  return !!(shot.visual_path);
}
function _isVideoSrc(shot) {
  return _VIDEO_RE.test(shot.visual_path || '') || _VIDEO_RE.test(shot.photo_spec || '');
}
function _imageShotType(shot) {
  return (shot.photo_spec || '').trim() === 'ai_symbolic' ? 'object' : 'face';
}
function _videoShotType(shot) {
  if (shot.lipsync) return 'dialogue';
  if (_isRealMedia(shot)) return 'real';
  if ((shot.photo_spec || '').trim() === 'ai_symbolic') return 'landscape';
  if (shot.is_hero || shot.frame_index === 0) return 'hero';
  return 'face';
}

// Returns a model id, or 'passthrough' (image step, real media), '' for none.
function pickModel(kind, shot, tier, override) {
  const o = (override || '').trim().toLowerCase();
  if (o && o !== 'auto' && modelField(o, 'kind') === kind) return o;
  if (kind === 'image' && (_isRealMedia(shot) || _isVideoSrc(shot))) return 'passthrough';
  const st = kind === 'image' ? _imageShotType(shot) : _videoShotType(shot);
  const t = (tier === 'draft' || tier === 'premium') ? tier : 'draft';
  const prefs = (((_models.routing || {})[kind] || {})[st] || {})[t] || [];
  for (const mid of prefs) if (_models.models && _models.models[mid]) return mid;
  return (_models.defaults || {})[kind] || '';
}

function modelCostJs(id, dur) {
  const key = modelField(id, 'pricing_key', '');
  if (!key || !_pricing) return 0;
  let cur = _pricing;
  for (const part of key.split('.')) { cur = cur && cur[part]; }
  const base = (typeof cur === 'number') ? cur : 0;
  if (modelField(id, 'kind') === 'video') return base * Math.max(1, Math.ceil(dur / 5));
  return base;
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
  const prompt = el('music-prompt').value.trim() ||
    'Emotional Bollywood instrumental, struggle to triumph, sitar and tabla';

  el('generate-music-btn').disabled = true;
  el('music-gen-status').innerHTML = '<span class="spinner"></span> Generating with Suno V5.5… (2–3 min)';

  try {
    const res = await fetch('/generate-music', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ prompt, session_id: SESSION_ID }),
    }).then(r => r.json());

    if (res.music_path) {
      generatedMusicPath = res.music_path;
      el('music-gen-status').innerHTML = '<span class="text-green">✓ Music ready</span>';
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
    const res = await post('/parse-script', { script, assets_dir: assetsDir, smart_match: smartMatch });
    if (res.error) { alert('Parse error: ' + res.error); return; }
    if (assetsDir) {
      const matched = (res.frames || []).filter(f => f.visual_path).length;
      el('assets-status').innerHTML = `<span class="text-green">✓ ${matched} photos matched</span>`;
    }
    parsedFrames = res.frames || [];
    frameOverrides = {};
    renderFrameCards(parsedFrames);
    el('frames-card').style.display = 'block';
    el('frames-count').textContent = `${parsedFrames.length} frames`;
    // Auto-apply target duration if already set
    const presetSec = parseInt(el('target-sec').value);
    if (presetSec > 0) redistributeDurations(presetSec);
    else updateTotalDur();
    el('frames-card').scrollIntoView({ behavior: 'smooth', block: 'start' });
    // Show cost estimate
    await loadPricing();
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

// ── Cost estimate ─────────────────────────────────────────────────────────

function renderCostEstimate() {
  if (!parsedFrames.length || !_pricing) return;
  const p = _pricing;
  const quality    = el('quality').value;
  const force5s    = quality === 'dev';
  const tier       = costTierFromQuality(quality);
  const musicType  = document.querySelector('input[name="music-type"]:checked')?.value || 'none';
  const _VIDEO = _VIDEO_RE;

  const gImg = (el('image-model')?.value || 'auto');
  const gVid = (el('video-model')?.value || 'auto');
  const skipScene = false; // UI always runs scene AI

  let sceneCount = 0, sceneTotal = 0;
  let animCount = 0,  animTotal  = 0;
  let imgCount = 0,   imgTotal   = 0;
  let editCount = 0,  editTotal  = 0;
  let lsCount = 0,    lsTotal    = 0,  lsAudioChars = 0;

  const syncRate    = (p.synclabs && p.synclabs.per_second_usd) || 0.012;
  const hedraRate   = (p.hedra && p.hedra.per_generation_usd) || 0.10;
  const sceneRate   = (p.scene_intelligence && p.scene_intelligence.gpt_per_frame_usd) || 0.01;
  const charsPerUsd = (p.voice && p.voice.elevenlabs_chars_per_dollar) || 25000;
  const usedModels  = new Set();

  parsedFrames.forEach((f, idx) => {
    const ov      = frameOverrides[f.frame_id] || {};
    const spec    = ov.photo_spec !== undefined ? ov.photo_spec : (f.photo_spec || '');
    const vpath   = f.visual_path || '';
    const dur     = parseFloat(el(`dur-${f.frame_id}`)?.value || f.duration);
    const isVid   = _VIDEO.test(vpath) || (spec && _VIDEO.test(spec));
    const editP   = el(`edit-${f.frame_id}`)?.value?.trim() || f.edit_prompt || '';
    const caption = (f.caption || '').trim();
    const lipsyncOn = el(`lipsync-${f.frame_id}`)?.checked ?? (f.lipsync || false);
    const perFrame  = ov.model_override || '';
    const shot      = { photo_spec: spec, visual_path: vpath, lipsync: lipsyncOn, frame_index: idx };

    // Scene intelligence — one GPT call per captioned frame
    if (caption && !skipScene) { sceneCount++; sceneTotal += sceneRate; }

    // Image gen — router picks model per shot (cost-tier aware)
    if (spec === 'ai_portrait' || spec === 'ai_symbolic') {
      const im = pickModel('image', shot, tier, (perFrame || gImg));
      if (im !== 'passthrough') { imgCount++; imgTotal += modelCostJs(im, 0); }
    }

    if (editP) { editCount++; editTotal += p.image_gen.openai_edit_usd; }

    // Lipsync frames: vendor cost + ElevenLabs audio, skip animation
    if (lipsyncOn) {
      lsCount++;
      lsTotal += isVid ? (syncRate * Math.max(1, dur)) : hedraRate;
      lsAudioChars += caption.length;
      return;
    }

    // Animation — router picks the video model; videos & Ken Burns are free
    if (!isVid && (spec || vpath)) {
      const useKenburns = (gVid === 'kenburns' && !perFrame);
      if (!useKenburns) {
        const vm = pickModel('video', shot, tier, (perFrame || gVid));
        if (vm) {
          const clipDur = force5s ? 5 : dur;
          animCount++; animTotal += modelCostJs(vm, clipDur); usedModels.add(vm);
        }
      }
    }
  });

  const lsAudioTotal = lsAudioChars / charsPerUsd;
  const musicTotal   = musicType === 'generate' ? p.music.suno_song_usd : 0;
  const total = sceneTotal + animTotal + imgTotal + editTotal + lsTotal + lsAudioTotal + musicTotal;

  const animName = usedModels.size ? [...usedModels].sort().join(', ') : 'Ken Burns';
  const pad = (s) => s + '&nbsp;'.repeat(Math.max(1, 26 - s.length));
  const lines = [];
  if (sceneCount) lines.push(`${pad(`${sceneCount} × Scene AI (GPT-4.1)`)}<b>$${sceneTotal.toFixed(2)}</b>`);
  if (imgCount)   lines.push(`${pad(`${imgCount} × Image gen (fal.ai/GPT)`)}<b>$${imgTotal.toFixed(2)}</b>`);
  if (editCount)  lines.push(`${pad(`${editCount} × Image edits`)}<b>$${editTotal.toFixed(2)}</b>`);
  if (animCount)  lines.push(`${pad(`${animCount} × ${animName} clips`)}<b>$${animTotal.toFixed(2)}</b>`);
  if (lsCount)  { lines.push(`${pad(`${lsCount} × Lip sync (Hedra/Sync)`)}<b>$${lsTotal.toFixed(2)}</b>`);
                  lines.push(`${pad('   + lip-sync voice (11Labs)')}<b>$${lsAudioTotal.toFixed(2)}</b>`); }
  if (musicTotal) lines.push(`${pad('1 × Suno music')}<b>$${musicTotal.toFixed(2)}</b>`);
  lines.push('─'.repeat(34));
  lines.push(`${pad('Total')}<b>~$${total.toFixed(2)} USD</b>`);

  el('cost-breakdown').innerHTML = lines.join('<br>');
  el('cost-card').style.display = 'block';
  if (p._updated) el('cost-updated').textContent = `prices as of ${p._updated}`;
}

// Recompute cost when quality/model/kling-mode/music changes
['quality', 'image-model', 'video-model', 'kling-mode'].forEach(id => el(id)?.addEventListener('change', renderCostEstimate));
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

// ── Render frame cards ────────────────────────────────────────────────────

function renderFrameCards(frames) {
  const container = el('frames-container');
  container.innerHTML = '';

  frames.forEach(f => {
    const isSilent = !f.caption;
    const card = document.createElement('div');
    card.className = 'frame-card';
    card.id = `card-${f.frame_id}`;

    // Determine initial visual type from photo_spec
    const initSpec    = f.photo_spec || (f.visual_path ? f.visual_path.split('/').pop() : '');
    const isVideo     = /\.(mp4|mov|avi|m4v|webm)$/i.test(f.visual_path || initSpec);
    const isMatched   = !!(f.visual_path) && !initSpec.startsWith('ai_');
    const initType    = initSpec === 'ai_portrait' ? 'portrait'
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
        </div>

        <div class="frame-upload ${initType === 'uploaded' ? 'visible' : ''}" id="upload-${f.frame_id}">
          <label class="upload-label">
            📁 Choose photo
            <input type="file" accept="image/*,video/*" data-frame="${f.frame_id}">
          </label>
          <span class="upload-filename" id="fname-${f.frame_id}"></span>
        </div>

        <div class="director-note-row">
          <input type="text" placeholder="Director note (optional): e.g. show anger not just sadness"
            id="note-${f.frame_id}" value="${escHtml(f.director_note || '')}">
        </div>

        <div class="director-note-row" style="margin-top:6px">
          <input type="text"
            placeholder="✏️ Image edit (optional): e.g. add thunderstorm, more trees, warmer sunset light"
            id="edit-${f.frame_id}" value="${escHtml(f.edit_prompt || '')}"
            style="border-color: ${f.edit_prompt ? 'var(--orange)' : ''}">
        </div>

        <div class="director-note-row" style="margin-top:6px">
          <input type="text"
            placeholder="🎥 Camera motion (optional): e.g. 360 orbit, dolly in, crash zoom, crane up, bullet time"
            id="motion-${f.frame_id}" value="${escHtml(f.motion_override || '')}"
            style="border-color: ${f.motion_override ? '#a78bfa' : ''}">
          ${f.camera_auto ? `<span style="font-size:11px;color:#a78bfa;white-space:nowrap">✨ auto: ${f.camera_reason || 'chosen for you'} — edit to change</span>` : ''}
        </div>

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
          : 'uploaded';
      });
    });

    // Live total update when user edits a duration
    const durInp = el(`dur-${f.frame_id}`);
    if (durInp) durInp.addEventListener('input', updateTotalDur);

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
}

function escHtml(s) {
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// ── Build payload (shared by Preview + Generate) ──────────────────────────

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
      director_note:   noteEl    ? noteEl.value.trim()              : (f.director_note   || ''),
      edit_prompt:     editEl    ? editEl.value.trim()               : (f.edit_prompt     || ''),
      motion_override: motionEl  ? motionEl.value.trim()             : (f.motion_override || ''),
      lipsync:         lipsyncCbEl ? lipsyncCbEl.checked            : (f.lipsync         || false),
      voice_override:  lipsyncVEl  ? lipsyncVEl.value               : (f.voice_override  || ''),
      image_model_override: ov.model_override || '',
      video_model_override: ov.model_override || '',
      video_start_sec: vstartEl  ? parseFloat(vstartEl.value)||0    : (f.video_start_sec  || 0),
    };
  });

  return {
    session_id:          SESSION_ID,
    subject_name:        el('subject-name').value.trim(),
    subject_description: el('subject-description').value.trim(),
    assets_dir:          el('assets-dir').value.trim(),
    script:              el('script-input').value,
    frames,
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
      font:     el('caption-font').value,
      size:     parseInt(el('caption-size').value) || 52,
      color:    el('caption-color').value,
      position: el('caption-position').value,
    },
  };
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
  stills.forEach(s => {
    const prev = el(`preview-${s.frame_id}`);
    if (!prev || !s.exists) return;
    const url = `/media?path=${encodeURIComponent(s.path)}&t=${Date.now()}`;
    prev.classList.remove('placeholder');
    prev.innerHTML = s.is_video
      ? `<video src="${url}#t=0.5" muted playsinline preload="metadata" style="width:90px;height:120px;object-fit:cover;border-radius:6px;background:#000"></video><span class="preview-label">🎬 generated still</span>`
      : `<img src="${url}" style="width:90px;height:120px;object-fit:cover;border-radius:6px;background:#222"><span class="preview-label">✅ this image will be animated</span>`;
  });
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
  const es = new EventSource(`/progress/${runId}`);

  es.onmessage = (event) => {
    const data = JSON.parse(event.data);

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
  el('output-panel').scrollIntoView({ behavior: 'smooth' });
}
