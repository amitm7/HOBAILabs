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
    const res = await post('/parse-script', { script, assets_dir: assetsDir });
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
    const initSpec    = f.photo_spec || 'auto';
    const isMatched   = f.visual_path && initSpec && !initSpec.startsWith('ai_');  // from assets folder
    const initType    = initSpec === 'ai_portrait' ? 'portrait'
                      : initSpec === 'ai_symbolic'  ? 'symbolic'
                      : isMatched                   ? 'matched'
                      : 'auto';
    const matchedName = isMatched ? initSpec : '';

    card.innerHTML = `
      <div class="frame-card-header">
        <span class="frame-id">${f.frame_id}</span>
        <span class="frame-caption">${f.caption || '(silent frame)'}</span>
        <span class="frame-dur">${f.duration}s</span>
      </div>
      <div class="frame-card-body">
        ${isSilent ? '<div style="font-size:12px;color:var(--text-dim)">Silent frame — visual only</div>' : ''}

        ${matchedName ? `<div class="matched-file-badge">📁 ${matchedName}</div>` : ''}

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

        <div class="duration-row">
          <label class="dur-label">Duration</label>
          <input type="number" id="dur-${f.frame_id}" class="dur-input"
            value="${f.duration}" min="2" max="15" step="0.5">
          <span class="dur-unit">s</span>
          <span class="dur-hint">(auto: ${f.duration}s)</span>
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

// ── Run pipeline ──────────────────────────────────────────────────────────

el('run-btn').addEventListener('click', async () => {
  if (!parsedFrames.length) {
    alert('Parse your script first (click "Parse Frames →").');
    return;
  }

  // Gather music
  const musicType = document.querySelector('input[name="music-type"]:checked').value;
  let musicPath = null;
  let voiceId = null;
  if (musicType === 'upload') musicPath = uploadedMusicPath;
  if (musicType === 'generate') musicPath = generatedMusicPath;
  if (musicType === 'voiceover') voiceId = el('voice-select').value;

  // Gather frames
  const frames = parsedFrames.map(f => {
    const override = frameOverrides[f.frame_id] || {};
    const noteEl   = el(`note-${f.frame_id}`);
    const durEl    = el(`dur-${f.frame_id}`);
    return {
      frame_id:       f.frame_id,
      caption:        f.caption,
      duration:       durEl ? parseFloat(durEl.value) || f.duration : f.duration,
      photo_spec:     override.photo_spec !== undefined ? override.photo_spec : (f.photo_spec || ''),
      photo_tmp_path: override.photo_tmp_path || '',
      director_note:  noteEl ? noteEl.value.trim() : (f.director_note || ''),
    };
  });

  const payload = {
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
    kling_mode:          el('kling-mode').value,
    orientation:         el('orientation').value,
    caption_style: {
      font:     el('caption-font').value,
      size:     parseInt(el('caption-size').value) || 52,
      color:    el('caption-color').value,
      position: el('caption-position').value,
    },
  };

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
