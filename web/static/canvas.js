/* Director Canvas board controller (AGENTIC_CANVAS_PLAN).
 * Talks to /api/canvas/* — all cost + gating is server-truth; this only renders.
 * Cost is shown per stage BEFORE spend (the fix for the competitor's wallet drain). */
(function () {
  "use strict";
  let runId = null;

  const $ = (id) => document.getElementById(id);
  const err = (msg) => { const e = $("err"); e.hidden = !msg; e.textContent = msg || ""; };
  const usd = (n) => "$" + (Number(n) || 0).toFixed(2);

  async function api(path, body) {
    const opt = { method: body ? "POST" : "GET", headers: { "Content-Type": "application/json" } };
    if (body) opt.body = JSON.stringify(body);
    const r = await fetch(path, opt);
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.error || ("HTTP " + r.status));
    return data;
  }

  // Arrow geometry per motion token — structured, not a decorative scribble.
  const ARROWS = {
    in:    "M60,150 L60,95",  out:  "M60,95 L60,150",
    left:  "M150,95 L70,95",  right:"M30,95 L110,95",
    up:    "M90,150 L90,80",  down: "M90,80 L90,150",
    orbit: "M40,95 a50,28 0 1 1 100,0",
  };

  function arrowSvg(token) {
    const d = ARROWS[token] || ARROWS.in;
    return `<svg viewBox="0 0 180 200" width="64" height="72" style="opacity:.85">
      <path class="arrow" d="${d}"/></svg>`;
  }

  function fmtEta(sec) {
    if (!sec || sec < 1) return "";
    return sec < 90 ? `~${Math.round(sec)}s` : `~${Math.round(sec / 60)}m`;
  }

  function renderRail(stages) {
    $("rail").innerHTML = stages.map((s) => {
      const free = !s.paid;
      const eta = s.paid ? fmtEta(s.eta_sec) : "";
      const costLine = free
        ? `<div class="cost free">Free</div>`
        : `<div class="cost">${usd(s.cost_usd)}${eta ? ` · <span class="eta">${eta}</span>` : ""}</div>`;
      let btn = "";
      if (s.id === "audio") {
        // Audio is chosen in the audio bar and produced inside Final Cut — not a gate.
        btn = `<button disabled>in Final Cut</button>`;
      } else if (s.status === "generating") {
        btn = `<button disabled>Generating…</button>`;
      } else if (s.status === "done") {
        btn = `<button class="appr" data-approve="${s.id}">Approve ✓</button>`;
      } else if (s.status === "approved") {
        btn = `<button disabled>Approved ✓</button>`;
      } else if (s.ready) {
        const label = free ? "Generate" : `Generate · ${usd(s.cost_usd)}`;
        btn = `<button class="gen" data-stage="${s.id}">${label}</button>`;
      } else {
        btn = `<button disabled>Locked</button>`;
      }
      return `<div class="cv-stage${s.status === "generating" ? " shimmer" : ""}">
        <div style="display:flex;justify-content:space-between;align-items:center">
          <h3>${s.label}</h3>
          <span class="cv-badge st-${s.status}">${s.status}</span>
        </div>
        <div class="blurb">${s.blurb}</div>
        ${costLine}${btn}
      </div>`;
    }).join("");
  }

  function mediaUrl(p) { return "/media?path=" + encodeURIComponent(p); }

  // ── Audio options (music / upload song / voiceover) ──────────────────────────
  let canvasMusicPath = "";
  function audioOpts() {
    const mode = $("audio-mode").value;
    const o = { music_type: mode };
    if (mode === "upload") o.music_path = canvasMusicPath;
    if (mode === "voiceover") o.voice_id = $("voice-id").value || "";
    return o;
  }
  async function loadVoices() {
    try {
      const d = await api("/voices");
      const voices = d.voices || d || [];
      $("voice-id").innerHTML = voices.map((v) =>
        `<option value="${v.voice_id}">${escapeHtml(v.name || v.voice_id)}</option>`).join("");
    } catch (e) { /* ignore */ }
  }
  $("audio-mode").addEventListener("change", () => {
    const m = $("audio-mode").value;
    $("voice-id").hidden = m !== "voiceover";
    $("song-label").hidden = m !== "upload";
    if (m !== "upload") $("song-name").textContent = "";
  });
  $("song-file").addEventListener("change", async (ev) => {
    const f = ev.target.files[0]; if (!f) return;
    $("song-name").textContent = "uploading…";
    try {
      const fd = new FormData();
      fd.append("photo", f); fd.append("session_id", runId || "canvas-song"); fd.append("frame_id", "song");
      const up = await fetch("/upload-photo", { method: "POST", body: fd }).then((r) => r.json());
      if (up.tmp_path) { canvasMusicPath = up.tmp_path; $("song-name").textContent = f.name; }
      else { err(up.error || "song upload failed"); $("song-name").textContent = ""; }
    } catch (e) { err(e.message); $("song-name").textContent = ""; }
  });

  function renderBoard(board) {
    const empty = board.length === 0;
    $("legend").hidden = empty;
    $("chat").hidden = empty;
    $("assets").hidden = empty;
    $("board").innerHTML = board.map((c) => {
      const fid = c.frame_id;
      // A REAL photo IS the final visual → fill the frame. A REFERENCE is only the
      // conditioning source (each shot will render a DIFFERENT keyframe from it) →
      // show it as a small corner chip over the shot's placeholder, never full-frame.
      let frameInner, img = false;
      if (c.real_path) {
        const isVid = /\.(mov|mp4|m4v|webm|avi)$/i.test(c.real_path);
        frameInner = isVid
          ? `<video class="clip" src="${mediaUrl(c.real_path)}" muted autoplay loop playsinline></video>`
          : `<img class="thumb" src="${mediaUrl(c.real_path)}" alt="">`;
        img = true;
      } else {
        const chip = c.ref_path
          ? `<span class="refchip"><img src="${mediaUrl(c.ref_path)}" alt="ref"><b>REF</b></span>`
          : "";
        frameInner = arrowSvg(c.arrow) + chip;   // distinct per-shot placeholder + ref source
      }
      // The prompt box only shows once the Storyboard stage has filled image_prompt.
      const promptBox = c.image_prompt
        ? `<div class="lbl">Image prompt (editable)</div>
           <textarea class="edit" data-frame="${fid}" data-field="image_prompt"
             rows="3">${escapeHtml(c.image_prompt)}</textarea>`
        : "";
      return `<div class="cv-card" data-frame="${fid}">
        <div class="frame${img ? " has-img" : ""}">
          <span class="grammar">${escapeHtml(c.shot_size || "shot")}${c.camera ? " · " + escapeHtml(c.camera) : ""}</span>
          <span class="kindbadge kb-${c.asset_kind}">${
            c.asset_kind === "real" ? "REAL" : c.asset_kind === "ai_person" ? "AI FACE" : "AI"
          }</span>
          <button class="reroll" data-frame="${fid}" title="Re-roll this shot (new still + clip)">↻</button>
          ${frameInner}
        </div>
        <div class="meta">
          <input class="edit cap" data-frame="${fid}" data-field="caption"
            value="${escapeHtml(c.caption || "")}" placeholder="caption / line">
          <input class="edit" data-frame="${fid}" data-field="motion_override"
            value="${escapeHtml(c.motion || "")}" placeholder="camera move">
          ${promptBox}
          <div class="sub">${escapeHtml(c.emotion || "")}${c.duration ? " · " + c.duration + "s" : ""}</div>
          <div class="attach">
            <select class="amode" data-frame="${fid}">
              <option value="reference">Reference (likeness)</option>
              <option value="real">Real (untouched)</option>
              <option value="scene">Scene ref</option>
            </select>
            <label class="attach-btn">📎 Image
              <input type="file" accept="image/*" data-frame="${fid}" hidden></label>
          </div>
        </div>
      </div>`;
    }).join("");
  }

  // Upload an image then attach it to shot(s) with the chosen mode. Reuses the
  // existing /upload-photo route, then /api/canvas/<id>/asset sets the frame keys.
  async function uploadAndAttach(file, { frame_id = null, mode = "reference", all_talent = false }) {
    if (!runId || !file) return;
    err("");
    try {
      const fd = new FormData();
      fd.append("photo", file);
      fd.append("session_id", runId);
      fd.append("frame_id", frame_id || "character");
      const up = await fetch("/upload-photo", { method: "POST", body: fd }).then((r) => r.json());
      if (up.error) { err(up.error); return; }
      const d = await api(`/api/canvas/${runId}/asset`,
        { path: up.tmp_path, mode, frame_id, all_talent });
      render(d.canvas);
    } catch (e) { err(e.message); }
  }

  // Save an edited shot field on change (blur/Enter). Server cascade-invalidates
  // downstream stages so an edit can't ship a stale render.
  async function saveField(el) {
    if (!runId) return;
    const frame_id = el.getAttribute("data-frame");
    const field = el.getAttribute("data-field");
    try {
      const d = await api(`/api/canvas/${runId}/frame`,
        { frame_id, fields: { [field]: el.value } });
      render(d.canvas);
    } catch (e) { err(e.message); }
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"]/g, (ch) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[ch]));
  }

  function render(canvas) {
    renderRail(canvas.stages);
    renderBoard(canvas.board);
    const hasBoard = canvas.board && canvas.board.length > 0;
    const vid = (canvas.stages || []).find((s) => s.id === "video");
    const videoApproved = vid && vid.status === "approved";
    $("render-btn").hidden = !hasBoard;
    $("render-btn").disabled = !videoApproved;
    $("render-btn").textContent = videoApproved ? "🎬 Final Cut" : "🎬 Render reel";
    $("render-btn").title = videoApproved
      ? "Assemble the approved clips into the finished reel"
      : "Generate & approve Key Frames → Video first.";
    if (canvas.render_id) syncRendered();   // fill cards from disk + reconnect if running
  }

  // ── Per-shot render reveal ───────────────────────────────────────────────────
  function showClipOnCard(fid, src) {
    const frame = document.querySelector(`.cv-card[data-frame="${fid}"] .frame`);
    if (!frame) return;
    frame.classList.add("has-img");
    // Replace prior media/placeholder but KEEP the overlays (grammar, badge, re-roll).
    frame.querySelectorAll("video.clip, img.thumb, svg, .refchip").forEach((el) => el.remove());
    const v = document.createElement("video");
    v.className = "clip"; v.src = src;
    v.muted = true; v.autoplay = true; v.loop = true; v.playsInline = true;
    frame.insertBefore(v, frame.firstChild);   // base layer; absolute overlays sit on top
  }
  function showStillOnCard(fid, src) {
    const frame = document.querySelector(`.cv-card[data-frame="${fid}"] .frame`);
    if (!frame || frame.querySelector("video.clip")) return;   // a clip wins over the still
    frame.classList.add("has-img");
    frame.querySelectorAll("svg, .refchip").forEach((el) => el.remove());
    let img = frame.querySelector("img.still");
    if (!img) {
      img = document.createElement("img");
      img.className = "thumb still";
      frame.insertBefore(img, frame.firstChild);
    }
    if (img.src !== src) img.src = src;
  }
  function showFinalVideo(url, rid) {
    $("render-panel").hidden = false;
    const v = $("render-video"), dl = $("render-dl");
    if (v.src !== location.origin + url) v.src = url;
    v.hidden = false;
    dl.href = "/download/" + (rid || url.split("/").pop()); dl.hidden = false;
    $("render-status").textContent = "done ✓";
  }

  // Rebuild the reveal from disk (survives reloads); reconnect SSE if still running.
  async function syncRendered() {
    if (!runId) return;
    try {
      const d = await api(`/api/canvas/${runId}/rendered`, {});   // POST: also syncs stage chips
      if (d.canvas) renderRail(d.canvas.stages);                  // unstick 'generating'
      Object.entries(d.stills || {}).forEach(([fid, p]) => showStillOnCard(fid, mediaUrl(p)));
      Object.entries(d.frames || {}).forEach(([fid, p]) => showClipOnCard(fid, mediaUrl(p)));
      if (d.output_url) showFinalVideo(d.output_url, d.render_id);
      if (d.render_status === "running" && d.render_id && !renderStream) openRenderStream(d.render_id);
    } catch (e) { /* ignore */ }
  }

  // ── Live render stream (logs + per-shot clips; poll fills stills) ────────────
  let renderStream = null, renderPoll = null;
  function openRenderStream(rid) {
    $("render-panel").hidden = false;
    const log = $("render-log");
    $("render-status").textContent = "running…";
    renderStream = new EventSource(`/progress/${rid}`);
    if (renderPoll) clearInterval(renderPoll);
    renderPoll = setInterval(syncRendered, 4000);   // surface stills/clips as they land
    renderStream.onmessage = (ev) => {
      let d; try { d = JSON.parse(ev.data); } catch (e) { return; }
      if (d.line) { log.textContent += d.line + "\n"; log.scrollTop = log.scrollHeight; }
      if (d.type === "clip_ready" && d.frame_id && d.url) showClipOnCard(d.frame_id, d.url);
      if (d.done) {
        clearInterval(renderPoll); renderPoll = null;
        renderStream.close(); renderStream = null;
        $("render-status").textContent = d.status === "done" ? "done ✓" : "error";
        syncRendered();   // final refresh: stills/clips/output + stage statuses
      }
    };
    renderStream.onerror = () => { /* SSE auto-retries; ignore transient */ };
  }

  // ── Save / resume ─────────────────────────────────────────────────────────
  // Every action already autosaves server-side (run_store). These just let the
  // user get back in: the run id rides in the URL (?run=) + localStorage, and a
  // "Resume…" picker lists saved canvases.
  function setRun(id) {
    runId = id;
    try { localStorage.setItem("hob_canvas_run", id); } catch (e) { /* ignore */ }
    history.replaceState({}, "", "/canvas?run=" + id);
    $("saved").hidden = false;
  }
  function clearRun() {
    runId = null;
    try { localStorage.removeItem("hob_canvas_run"); } catch (e) { /* ignore */ }
    history.replaceState({}, "", "/canvas");
    $("saved").hidden = true;
  }
  function applyCanvas(canvas) {
    if (canvas.brief !== undefined) $("brief").value = canvas.brief || "";
    if (canvas.scope) $("scope").value = canvas.scope;
    if (canvas.quality) $("quality").value = canvas.quality;
    if (canvas.target_seconds !== undefined) $("length").value = String(canvas.target_seconds);
    render(canvas);
  }
  async function loadCanvas(id) {
    if (!id) return;
    try {
      const d = await api(`/api/canvas/${id}/state`);
      setRun(id);
      applyCanvas(d.canvas);
    } catch (e) { err("Couldn't resume that canvas — it may have been cleared."); clearRun(); }
  }
  async function loadRecents() {
    try {
      const d = await api("/api/canvas/list");
      $("resume").innerHTML = '<option value="">Resume…</option>' +
        (d.canvases || []).map((c) =>
          `<option value="${c.run_id}">${escapeHtml(c.title)}</option>`).join("");
    } catch (e) { /* non-fatal */ }
  }

  // ── Events ─────────────────────────────────────────────────────────────────
  $("plan-btn").addEventListener("click", async () => {
    err("");
    const brief = $("brief").value.trim();
    if (!brief) { err("Enter a brief first."); return; }
    $("plan-btn").disabled = true; $("plan-btn").textContent = "Planning…";
    try {
      const d = await api("/api/canvas/plan", {
        brief, scope: $("scope").value, quality: $("quality").value,
        target_seconds: parseInt($("length").value, 10) || 0,
      });
      setRun(d.run_id);
      render(d.canvas);
      loadRecents();
    } catch (e) { err(e.message); }
    finally { $("plan-btn").disabled = false; $("plan-btn").textContent = "Plan ✨"; }
  });

  // Render the whole reel (prod by default) via the proven pipeline.
  $("render-btn").addEventListener("click", async () => {
    if (!runId) return;
    err("");
    const btn = $("render-btn");
    btn.disabled = true; btn.textContent = "🎬 Rendering…";
    try {
      const d = await api(`/api/canvas/${runId}/render`, { quality: $("quality").value, ...audioOpts() });
      render(d.canvas);   // canvas.render_id is now set → render() opens the stream
    } catch (e) { err(e.message); btn.disabled = false; btn.textContent = "🎬 Render reel"; }
  });

  // New canvas — clear the board and the saved pointer.
  $("new-btn").addEventListener("click", () => {
    clearRun();
    if (renderStream) { renderStream.close(); renderStream = null; }
    $("brief").value = "";
    $("board").innerHTML = ""; $("rail").innerHTML = "";
    $("legend").hidden = true; $("chat").hidden = true; $("assets").hidden = true;
    $("render-btn").hidden = true; $("render-panel").hidden = true;
    $("render-video").hidden = true; $("render-dl").hidden = true; $("render-log").textContent = "";
    err("");
  });

  // Resume a saved canvas from the picker.
  $("resume").addEventListener("change", (ev) => {
    if (ev.target.value) loadCanvas(ev.target.value);
  });

  // On open: list recents + voices, resume the last canvas (URL ?run= or localStorage).
  loadRecents();
  loadVoices();
  (function initResume() {
    const fromUrl = new URLSearchParams(location.search).get("run");
    let last = null;
    try { last = localStorage.getItem("hob_canvas_run"); } catch (e) { /* ignore */ }
    const id = fromUrl || last;
    if (id) loadCanvas(id);
  })();

  // Re-roll one shot — regenerate its still + clip (~1–2 min; reuses the pipeline).
  async function rerollShot(fid, btn) {
    if (!runId) return;
    err("");
    const frame = btn.closest(".frame");
    if (frame) frame.classList.add("shimmer");
    btn.disabled = true; btn.textContent = "…";
    try {
      const d = await api(`/api/canvas/${runId}/reroll`, { frame_id: fid });
      if (d.clip_path) showClipOnCard(fid, mediaUrl(d.clip_path) + "&t=" + Date.now());
    } catch (e) { err("Re-roll failed: " + e.message); }
    finally {
      if (frame) frame.classList.remove("shimmer");
      btn.disabled = false; btn.textContent = "↻";
    }
  }

  // Delegated re-roll click.
  $("board").addEventListener("click", (ev) => {
    const rb = ev.target.closest(".reroll");
    if (rb) { ev.preventDefault(); rerollShot(rb.getAttribute("data-frame"), rb); }
  });

  // Delegated change handler: per-card image upload, or an edited text field.
  $("board").addEventListener("change", (ev) => {
    const fileInput = ev.target.closest('input[type="file"]');
    if (fileInput && fileInput.files[0]) {
      const fid = fileInput.getAttribute("data-frame");
      const sel = document.querySelector(`select.amode[data-frame="${fid}"]`);
      uploadAndAttach(fileInput.files[0], { frame_id: fid, mode: sel ? sel.value : "reference" });
      fileInput.value = "";
      return;
    }
    const el = ev.target.closest(".edit");
    if (el) saveField(el);
  });

  // Auto-match a whole folder of the operator's real photos/videos to the shots
  // (the moat: real media, not synthetic portraits of a real person).
  $("match-btn").addEventListener("click", async () => {
    if (!runId) { err("Plan a story first."); return; }
    const folder = $("assets-folder").value.trim();
    if (!folder) { err("Enter the path to your photos folder."); return; }
    err(""); const btn = $("match-btn");
    btn.disabled = true; $("match-hint").textContent = "matching… (reading your images)";
    try {
      const d = await api(`/api/canvas/${runId}/match-photos`, { assets_dir: folder });
      $("match-hint").textContent = `✓ ${d.real_shots} shots now use your real media`;
      render(d.canvas);
    } catch (e) { err(e.message); $("match-hint").textContent = ""; }
    finally { btn.disabled = false; }
  });

  // Character-level: attach a real photo of the person to every people-shot.
  $("char-photo").addEventListener("change", (ev) => {
    const file = ev.target.files[0];
    if (!file) return;
    const mode = $("char-mode").value;
    uploadAndAttach(file, { all_talent: true, mode });
    ev.target.value = "";
  });
  $("board").addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" && ev.target.matches("input.edit")) ev.target.blur();
  });

  // Chat command box — refine + re-plan (the Studio-Chat equivalent).
  async function sendChat() {
    const inp = $("chat-input");
    const message = inp.value.trim();
    if (!runId || !message) return;
    inp.disabled = true; $("chat-send").disabled = true; err("");
    try {
      const d = await api(`/api/canvas/${runId}/chat`, { message });
      inp.value = "";
      render(d.canvas);
    } catch (e) { err(e.message); }
    finally { inp.disabled = false; $("chat-send").disabled = false; inp.focus(); }
  }
  $("chat-send").addEventListener("click", sendChat);
  $("chat-input").addEventListener("keydown", (ev) => { if (ev.key === "Enter") sendChat(); });

  $("rail").addEventListener("click", async (ev) => {
    const gen = ev.target.closest("[data-stage]");
    const appr = ev.target.closest("[data-approve]");
    if (!runId || (!gen && !appr)) return;
    err("");
    try {
      if (gen) {
        const stage = gen.getAttribute("data-stage");
        gen.disabled = true; gen.textContent = "Working…";
        let d;
        if (stage === "keyframes") {
          d = await api(`/api/canvas/${runId}/keyframes`, {});           // cheap stills only
        } else if (stage === "video") {
          d = await api(`/api/canvas/${runId}/video`, {});               // clips only (gated by Key Frames)
        } else if (stage === "finalcut") {
          d = await api(`/api/canvas/${runId}/render`, { quality: $("quality").value, ...audioOpts() }); // assemble (reuses clips)
        } else {
          d = await api(`/api/canvas/${runId}/advance`, { stage });      // free stages
        }
        render(d.canvas);
      } else {
        const stage = appr.getAttribute("data-approve");
        const d = await api(`/api/canvas/${runId}/approve`, { stage });
        render(d.canvas);
      }
    } catch (e) { err(e.message); }
  });
})();
