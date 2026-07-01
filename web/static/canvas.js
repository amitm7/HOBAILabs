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

  // ── Captions & format settings (persisted on the canvas via /settings) ──────
  let _settingsReady = false;
  function syncSettings(canvas) {
    const cs = canvas.caption_style || {};
    if ("enabled" in cs) $("cap-enabled").checked = cs.enabled !== false;
    if (cs.position) $("cap-position").value = cs.position;
    if (cs.font) $("cap-font").value = cs.font;
    if (cs.size) $("cap-size").value = cs.size;
    if (cs.color) $("cap-color").value = cs.color;
    if (cs.max_lines !== undefined) $("cap-lines").value = String(cs.max_lines);
    if (canvas.orientation) $("orientation").value = canvas.orientation;
    // Story-type mode: AI (fiction) hides the real-media tools (folder match / enhance /
    // pick / re-match) since there's no real folder — everything is generated.
    const ai = (canvas.story_type || "real") === "ai";
    document.body.classList.toggle("story-ai", ai);
    if ($("story-type") && canvas.story_type) $("story-type").value = canvas.story_type;
    _settingsReady = true;
  }
  async function saveSettings() {
    if (!runId || !_settingsReady) return;
    const caption_style = {
      enabled: $("cap-enabled").checked,
      position: $("cap-position").value,
      font: $("cap-font").value,
      size: parseInt($("cap-size").value, 10) || 24,
      color: $("cap-color").value,
      max_lines: parseInt($("cap-lines").value, 10),
    };
    try {
      const d = await api(`/api/canvas/${runId}/settings`,
        { caption_style, orientation: $("orientation").value });
      $("settings-hint").textContent = "✓ saved";
      setTimeout(() => { $("settings-hint").textContent = ""; }, 1500);
      if (d.canvas) { lastCanvas = d.canvas; renderRail(d.canvas.stages); }  // orientation may re-lock stages
    } catch (e) { err(e.message); }
  }
  ["cap-enabled", "cap-position", "cap-font", "cap-size", "cap-color", "cap-lines", "orientation"]
    .forEach((id) => { const el = $(id); if (el) el.addEventListener("change", saveSettings); });

  // Storyboard (pencil-sketch) view toggle + last-rendered canvas (for re-render on toggle).
  let storyboardView = false;
  let lastCanvas = null;
  let sketchPoll = null;

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
      if (storyboardView && c.storyboard_art) {
        // Storyboard view: the pencil panel replaces the photo/placeholder for EVERY shot
        // (it's the planning board), with the motion arrow overlaid as the camera-move cue.
        frameInner = `<img class="thumb sketch" src="${mediaUrl(c.storyboard_art)}" alt="storyboard panel">`
                   + `<span class="sketch-arrow">${arrowSvg(c.arrow)}</span>`;
        img = true;
      } else if (c.real_path) {
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
          ${c.can_recreate ? `<button class="recreate" data-frame="${fid}" title="Re-create this scene cinematically, inspired from your real footage — no person faked (ambient only)">🎬</button>` : ""}
          ${c.can_upscale ? `<button class="upscale" data-frame="${fid}" title="Upscale this shot (final-render quality lift) — real shots use a faithful super-res that keeps the face exact; AI shots get a creative detail pass">⬆</button>` : ""}
          ${c.recreated ? `<span class="fromreal">AI · from real</span>`
            : c.ai_likeness ? `<span class="fromreal">AI · likeness</span>`
            : c.forced_ai ? `<span class="fromreal">AI</span>` : ""}
          ${c.upscaled ? `<span class="upbadge">⬆ upscaled</span>` : ""}
          ${frameInner}
        </div>
        <div class="meta">
          <input class="edit cap" data-frame="${fid}" data-field="caption"
            value="${escapeHtml(c.caption || "")}" placeholder="caption / line">
          <input class="edit" data-frame="${fid}" data-field="motion_override"
            value="${escapeHtml(c.motion || "")}" placeholder="camera move">
          <div class="edit-row">
            <input class="edit" data-frame="${fid}" data-field="emotion"
              value="${escapeHtml(c.emotion || "")}" placeholder="emotion" title="Emotional tone of this shot">
            <input class="edit" data-frame="${fid}" data-field="camera_angle"
              value="${escapeHtml(c.camera || "")}" placeholder="camera angle" title="Shot angle (e.g. low angle, close-up)">
          </div>
          ${promptBox}
          <div class="sub">${c.duration ? c.duration + "s" : ""}</div>
          ${fidelityRow(c)}
          <div class="source">
            <span class="lbl">Replace</span>
            <button class="src-btn pick-btn" data-frame="${fid}" title="Pick the right photo from your folder (fix a wrong auto-match)">🖼 Pick</button>
            <button class="src-btn rematch-btn" data-frame="${fid}" title="Auto re-match this one shot against your folder (role-aware)">⟳ Re-match</button>
            <label class="src-btn" title="Upload a different real photo, untouched (🟢 the moat)">📎 Real
              <input type="file" accept="image/*" data-frame="${fid}" data-mode="real" hidden></label>
            <label class="src-btn" title="AI image conditioned on a face you upload — labeled AI · likeness">🎭 AI face
              <input type="file" accept="image/*" data-frame="${fid}" data-mode="reference" hidden></label>
            <button class="src-btn ai-gen" data-frame="${fid}" title="Replace with a fully AI-generated image (no real footage, no identity)">🤖 AI</button>
            ${c.can_revert_real ? `<button class="src-btn revert-real" data-frame="${fid}" title="Discard the AI version and go back to your real photo">↩ Real</button>` : ""}
          </div>
        </div>
      </div>`;
    }).join("");
  }

  // Reality–Fidelity ladder selector (rung 1d): per-shot choice across the rungs we
  // already shipped, plus a one-tap ⚡ auto-suggest. Only REAL shots have a ladder;
  // person shots are capped at Restore (never re-create a real person here).
  function fidelityLabel(r) {
    return r === "passthrough" ? "Real (untouched)"
         : r === "restore" ? "Restore (clean)"
         : r === "recreate" ? "Re-create (cinematic)" : r;
  }
  function fidelityRow(c) {
    if (!c.fidelity_options || !c.fidelity_options.length) return "";
    const fid = c.frame_id;
    const opts = c.fidelity_options.map((r) =>
      `<option value="${r}"${r === c.fidelity ? " selected" : ""}>${fidelityLabel(r)}</option>`).join("");
    let suggest = "";
    if (c.fidelity_suggested && c.fidelity_suggested !== c.fidelity) {
      suggest = `<button class="fid-suggest" data-frame="${fid}" data-rung="${c.fidelity_suggested}"
        title="${escapeHtml(c.fidelity_reason || "")}">⚡ ${fidelityLabel(c.fidelity_suggested)}</button>`;
    } else if (c.fidelity_suggested) {
      suggest = `<span class="fid-ok" title="${escapeHtml(c.fidelity_reason || "")}">⚡ best already</span>`;
    }
    return `<div class="fidelity">
      <span class="lbl">Fidelity</span>
      <select class="fid-sel" data-frame="${fid}">${opts}</select>
      ${suggest}
    </div>`;
  }

  // Dispatch a Fidelity change to the right (already-verified) route: Passthrough reverts
  // to the untouched original; Restore + Re-create reuse the shipped /restore + /recreate.
  async function setFidelity(fid, rung, sel) {
    if (!runId) return;
    err("");
    try {
      if (rung === "passthrough") {
        const d = await api(`/api/canvas/${runId}/fidelity`, { frame_id: fid, rung });
        render(d.canvas);
      } else if (rung === "restore") {
        if (sel) sel.disabled = true;
        await api(`/api/canvas/${runId}/restore`, { frame_id: fid });
        $("match-hint").textContent = "enhancing this shot…";
        pollRestore();
      } else if (rung === "recreate") {
        if (sel) sel.disabled = true;
        const fr = document.querySelector(`.cv-card[data-frame="${fid}"] .frame`);
        if (fr) fr.classList.add("shimmer");
        const d = await api(`/api/canvas/${runId}/recreate`, { frame_id: fid });
        render(d.canvas);
      }
    } catch (e) {
      err((rung === "recreate" ? "Re-create: " : "") + e.message);
      try { const s = await api(`/api/canvas/${runId}/state`); render(s.canvas); } catch (_) {}
    }
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
      // Visible confirmation — an attach used to change only a small REF chip + badge,
      // which read as "nothing happened". Say exactly what changed and where.
      const n = d.affected || 1;
      const what = mode === "real" ? "real, untouched 🟢"
                 : mode === "reference" ? "an AI likeness 🔴 (labeled)"
                 : "a scene reference 🟡";
      $("match-hint").textContent = `✓ your photo applied to ${n} shot${n === 1 ? "" : "s"} as ${what}`;
    } catch (e) { err(e.message); }
  }

  // Per-shot source swap (the escape hatch for a matched real photo you dislike):
  // 🤖 fully AI-generated, ↩ back to your real photo. Likeness-from-a-face goes through
  // uploadAndAttach('reference'). Rotate fixes a landscape phone photo for the 9:16 reel.
  async function aiGeneric(fid, btn) {
    if (!runId) return;
    err(""); if (btn) btn.disabled = true;
    try {
      const d = await api(`/api/canvas/${runId}/ai-source`, { frame_id: fid });
      render(d.canvas);
      $("match-hint").textContent = "✓ shot replaced with a fully AI-generated image";
    } catch (e) { err(e.message); if (btn) btn.disabled = false; }
  }
  async function revertReal(fid) {
    if (!runId) return;
    err("");
    try {
      const d = await api(`/api/canvas/${runId}/fidelity`, { frame_id: fid, rung: "passthrough" });
      render(d.canvas);
      $("match-hint").textContent = "↩ back to your real photo 🟢";
    } catch (e) { err(e.message); }
  }
  // Generative upscale — final-render quality lift. Routed server-side (real→faithful,
  // AI→creative) so a real face is never altered. Per-shot, spend-gated, ~10-40s.
  async function upscaleShot(fid, btn) {
    if (!runId) return;
    err("");
    const fr = btn && btn.closest(".frame");
    if (fr) fr.classList.add("shimmer");
    if (btn) { btn.disabled = true; btn.textContent = "…"; }
    try {
      const d = await api(`/api/canvas/${runId}/upscale`, { frame_id: fid });
      render(d.canvas);
      $("match-hint").textContent = d.skipped
        ? (d.message || "Already high-res — no upscale needed.")
        : d.creative
          ? "✓ upscaled (creative detail pass)"
          : "✓ upscaled (faithful — identity preserved 🟢)";
    } catch (e) { err("Upscale: " + e.message); }
    finally { if (fr) fr.classList.remove("shimmer"); if (btn) { btn.disabled = false; btn.textContent = "⬆"; } }
  }

  // Per-shot re-match (C6): auto-pick the best-fitting photo for just this beat (role-aware).
  async function rematchShot(fid, btn) {
    if (!runId) return;
    err(""); if (btn) { btn.disabled = true; btn.textContent = "…"; }
    try {
      const d = await api(`/api/canvas/${runId}/rematch`, { frame_id: fid });
      render(d.canvas);
      $("match-hint").textContent = "✓ re-matched this shot";
    } catch (e) { err(e.message); }
    finally { if (btn) { btn.disabled = false; btn.textContent = "⟳ Re-match"; } }
  }

  // Per-shot photo picker — auto-match is never perfect on abstract beats, so let the
  // operator swap a shot to the RIGHT photo from their own folder in two clicks. The
  // folder list is fetched once and reused across all cards.
  let assetCache = null;
  async function loadAssets() {
    if (assetCache) return assetCache;
    const d = await api(`/api/canvas/${runId}/assets`);
    assetCache = d.assets || [];
    return assetCache;
  }
  async function openPicker(fid) {
    if (!runId) return;
    const card = document.querySelector(`.cv-card[data-frame="${fid}"]`);
    if (!card) return;
    const open = card.querySelector(".picker");
    if (open) { open.remove(); return; }            // toggle closed
    document.querySelectorAll(".picker").forEach((g) => g.remove());  // one at a time
    err("");
    let assets;
    try { assets = await loadAssets(); } catch (e) { err(e.message); return; }
    if (!assets.length) {
      $("match-hint").textContent = "No folder yet — paste your photos folder above and Match first.";
      return;
    }
    const gal = document.createElement("div");
    gal.className = "picker";
    // HEIC/HEIF/BMP don't render in <img> — show a labeled tile (still selectable).
    const undisplayable = (n) => /\.(heic|heif|bmp)$/i.test(n);
    gal.innerHTML = assets.map((a) => {
      const dp = encodeURIComponent(a.path);
      if (a.is_video)
        return `<video class="pk" data-path="${dp}" src="${mediaUrl(a.path)}" muted title="${a.name}"></video>`;
      if (undisplayable(a.name))
        return `<div class="pk pk-file" data-path="${dp}" title="${a.name}">${a.name.split(".").pop().toUpperCase()}</div>`;
      return `<img class="pk" data-path="${dp}" src="${mediaUrl(a.path)}" alt="${a.name}" title="${a.name}" loading="lazy">`;
    }).join("");
    card.appendChild(gal);
  }
  async function assignReal(fid, path) {
    err("");
    try {
      const d = await api(`/api/canvas/${runId}/asset`, { path, mode: "real", frame_id: fid });
      render(d.canvas);
      $("match-hint").textContent = "✓ shot set to your chosen real photo 🟢";
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
    lastCanvas = canvas;
    syncSettings(canvas);
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
    updateCostBanner(canvas);
    if (canvas.render_id) syncRendered();   // fill cards from disk + reconnect if running
  }

  // Upfront whole-reel cost + spend-cap (parity with galleri5's credit warning,
  // backed by our hard per-stage gate). Estimate is instant from public_state.
  async function updateCostBanner(canvas) {
    const el = $("cost-banner");
    if (!canvas.board || !canvas.board.length) { el.hidden = true; return; }
    el.hidden = false; el.classList.remove("over");
    const est = canvas.total_cost_usd || 0;
    el.innerHTML = `💰 Full reel ≈ $${est.toFixed(2)} <span class="sub">at ${canvas.quality}</span>`;
    if (!runId) return;
    try {
      const b = await api(`/api/canvas/${runId}/budget`);
      el.innerHTML = `💰 Full reel ≈ $${(b.estimate_usd ?? est).toFixed(2)} `
        + `<span class="sub">at ${b.quality} · spend cap $${(b.spend_cap_usd || 0).toFixed(0)}`
        + (b.over_cap ? ` · ⚠ exceeds your cap` : ``) + `</span>`;
      el.classList.toggle("over", !!b.over_cap);
    } catch (e) { /* keep the basic estimate */ }
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
        story_type: $("story-type").value,
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

  // Optional ambient re-create — opt-in per shot (non-person only).
  async function recreateShot(fid, btn) {
    if (!runId) return;
    err("");
    const frame = btn.closest(".frame");
    if (frame) frame.classList.add("shimmer");
    btn.disabled = true; btn.textContent = "…";
    try {
      const d = await api(`/api/canvas/${runId}/recreate`, { frame_id: fid });
      render(d.canvas);
    } catch (e) { err("Re-create: " + e.message); }
    finally {
      if (frame) frame.classList.remove("shimmer");
      btn.disabled = false; btn.textContent = "🎬";
    }
  }

  // Delegated re-roll / re-create clicks.
  $("board").addEventListener("click", (ev) => {
    const rb = ev.target.closest(".reroll");
    if (rb) { ev.preventDefault(); rerollShot(rb.getAttribute("data-frame"), rb); return; }
    const rc = ev.target.closest(".recreate");
    if (rc) { ev.preventDefault(); recreateShot(rc.getAttribute("data-frame"), rc); return; }
    const pb = ev.target.closest(".pick-btn");
    if (pb) { ev.preventDefault(); openPicker(pb.getAttribute("data-frame")); return; }
    const rm = ev.target.closest(".rematch-btn");
    if (rm) { ev.preventDefault(); rematchShot(rm.getAttribute("data-frame"), rm); return; }
    const pk = ev.target.closest(".pk");
    if (pk) {
      ev.preventDefault();
      const card = pk.closest(".cv-card");
      assignReal(card.getAttribute("data-frame"), decodeURIComponent(pk.getAttribute("data-path")));
      const g = card.querySelector(".picker"); if (g) g.remove();
      return;
    }
    const up = ev.target.closest(".upscale");
    if (up) { ev.preventDefault(); upscaleShot(up.getAttribute("data-frame"), up); return; }
    const ag = ev.target.closest(".ai-gen");
    if (ag) { ev.preventDefault(); aiGeneric(ag.getAttribute("data-frame"), ag); return; }
    const rr = ev.target.closest(".revert-real");
    if (rr) { ev.preventDefault(); revertReal(rr.getAttribute("data-frame")); return; }
    const fs = ev.target.closest(".fid-suggest");
    if (fs) { ev.preventDefault(); setFidelity(fs.getAttribute("data-frame"), fs.getAttribute("data-rung")); }
  });

  // Delegated change handler: Fidelity selector, per-card image upload (Replace row), text.
  $("board").addEventListener("change", (ev) => {
    const fidSel = ev.target.closest(".fid-sel");
    if (fidSel) { setFidelity(fidSel.getAttribute("data-frame"), fidSel.value, fidSel); return; }
    const fileInput = ev.target.closest('input[type="file"][data-mode]');
    if (fileInput && fileInput.files[0]) {
      uploadAndAttach(fileInput.files[0], {
        frame_id: fileInput.getAttribute("data-frame"),
        mode: fileInput.getAttribute("data-mode") || "reference",
      });
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
      assetCache = null;   // new folder → refresh the picker gallery
      $("match-hint").textContent = `✓ ${d.real_shots} shots now use your real media`;
      render(d.canvas);
    } catch (e) { err(e.message); $("match-hint").textContent = ""; }
    finally { btn.disabled = false; }
  });

  // Enhance (Restore, ladder rung 1): non-generative cleanup of the real footage.
  // Keeps identity 100% real — just upscale/denoise/stabilize/grade. Threaded → poll.
  let restorePoll = null;
  // Poll the threaded restore job to completion (used by the bulk Enhance button AND the
  // per-shot Fidelity → Restore selector). Re-renders the board so a 'best already' hint
  // and the restored thumbnail appear as soon as the ffmpeg pass finishes.
  function pollRestore(onDone) {
    if (restorePoll) clearInterval(restorePoll);
    restorePoll = setInterval(async () => {
      try {
        const s = await api(`/api/canvas/${runId}/state`);
        const c = s.canvas;
        $("match-hint").textContent = c.restoring
          ? `enhancing ${c.restore_done}/${c.restore_total}…`
          : `✓ enhanced ${c.restore_total} real shot${c.restore_total === 1 ? "" : "s"}`;
        if (!c.restoring) {
          clearInterval(restorePoll); restorePoll = null; render(c); if (onDone) onDone();
        }
      } catch (e) { /* keep polling */ }
    }, 2500);
  }
  $("restore-btn").addEventListener("click", async () => {
    if (!runId) { err("Plan a story first."); return; }
    err(""); const btn = $("restore-btn");
    btn.disabled = true;
    try {
      const d = await api(`/api/canvas/${runId}/restore`, {});
      render(d.canvas);
      $("match-hint").textContent = `enhancing 0/${d.total}…`;
      pollRestore(() => { btn.disabled = false; });
    } catch (e) { err(e.message); btn.disabled = false; }
  });

  // ⚡ Auto-suggest a Fidelity rung for every real shot (quality assessment, no spend).
  $("fidelity-btn") && $("fidelity-btn").addEventListener("click", async () => {
    if (!runId) { err("Plan a story first."); return; }
    err(""); const btn = $("fidelity-btn");
    btn.disabled = true; $("match-hint").textContent = "assessing shot quality…";
    try {
      const d = await api(`/api/canvas/${runId}/fidelity-suggest`, {});
      const n = (d.suggestions || []).length;
      const flagged = (d.suggestions || []).filter((s) => s.suggested && s.suggested !== "passthrough").length;
      $("match-hint").textContent = `⚡ assessed ${n} real shot${n === 1 ? "" : "s"} — ${flagged} could improve`;
      render(d.canvas);
    } catch (e) { err(e.message); $("match-hint").textContent = ""; }
    finally { btn.disabled = false; }
  });

  // ✏️ Storyboard view — toggle the board between photo/placeholder and pencil-sketch
  // panels. First enable renders the panels (cheap draft model, one per shot); after that
  // it's just a view toggle (panels are cached).
  function pollSketch() {
    if (sketchPoll) clearInterval(sketchPoll);
    sketchPoll = setInterval(async () => {
      try {
        const s = await api(`/api/canvas/${runId}/state`);
        const c = s.canvas;
        $("match-hint").textContent = c.sketching
          ? `✏️ sketching ${c.sketch_done}/${c.sketch_total}…`
          : "✏️ storyboard ready";
        if (!c.sketching) {
          clearInterval(sketchPoll); sketchPoll = null;
          $("storyboard-btn").disabled = false; render(c);
        }
      } catch (e) { /* keep polling */ }
    }, 2500);
  }
  $("storyboard-btn") && $("storyboard-btn").addEventListener("click", async () => {
    if (!runId) { err("Plan a story first."); return; }
    err("");
    storyboardView = !storyboardView;
    $("storyboard-btn").classList.toggle("on", storyboardView);
    $("storyboard-btn").textContent = storyboardView ? "✏️ Exit storyboard" : "✏️ Storyboard";
    if (lastCanvas) render(lastCanvas);               // toggle the view immediately
    // Generate any missing panels on first enable.
    if (storyboardView && lastCanvas && (lastCanvas.board || []).some((c) => !c.storyboard_art)) {
      const btn = $("storyboard-btn"); btn.disabled = true;
      try {
        const d = await api(`/api/canvas/${runId}/storyboard-art`, {});
        $("match-hint").textContent = `✏️ sketching 0/${d.total}…`;
        pollSketch();
      } catch (e) { err(e.message); btn.disabled = false; }
    }
  });

  // Characters stage → story-level Character Sheet. Each person: a real photo (+consent
  // for AI likeness) AND appearance attributes (role/name/gender/age/skin/hair/clothing)
  // that carry to every shot they're in, so they stay the same person across the reel.
  function _cattr(cid, attr, val, ph) {
    return `<input class="cattr" data-char="${cid}" data-attr="${attr}" placeholder="${ph}" value="${escapeHtml(val || "")}">`;
  }
  function renderCharacters(chars) {
    const el = $("characters");
    if (!chars || !chars.length) { el.hidden = true; return; }
    el.hidden = false;
    el.innerHTML = `<h4>👥 Character sheet — attributes carry to every shot; a real photo keeps the face exact (consent needed for AI likeness)</h4>`
      + chars.map((c) => `
        <div class="cv-char" data-char="${c.id}">
          <div class="char-head">
            <span class="nm">${escapeHtml(c.role || c.name || c.label || c.id)}</span>
            ${c.ref_path ? `<img src="${mediaUrl(c.ref_path)}" alt="">` : `<span class="muted">no photo</span>`}
            <label class="attach-btn">📎 Real photo<input type="file" accept="image/*" data-char="${c.id}" hidden></label>
            <label><input type="checkbox" class="consent" data-char="${c.id}" ${c.consent ? "checked" : ""}> consent</label>
          </div>
          <div class="char-attrs">
            ${_cattr(c.id, "role", c.role, "role (father/friend…)")}
            ${_cattr(c.id, "name", c.name, "name")}
            ${_cattr(c.id, "gender", c.gender, "gender")}
            ${_cattr(c.id, "age", c.age, "age (child/adult/elderly)")}
            ${_cattr(c.id, "skin_tone", c.skin_tone, "skin tone")}
            ${_cattr(c.id, "hair", c.hair, "hair")}
            ${_cattr(c.id, "clothing", c.clothing, "clothing style")}
            <button class="char-save" data-char="${c.id}">Save</button>
          </div>
        </div>`).join("");
  }
  // Save a character's attributes (delegated click on its Save button).
  $("characters").addEventListener("click", async (ev) => {
    const save = ev.target.closest(".char-save");
    if (!save) return;
    const cid = save.getAttribute("data-char");
    const attrs = {};
    document.querySelectorAll(`.cattr[data-char="${cid}"]`).forEach((i) => {
      attrs[i.getAttribute("data-attr")] = i.value;
    });
    save.disabled = true; err("");
    try {
      const d = await api(`/api/canvas/${runId}/character`, { char_id: cid, attrs });
      renderCharacters(d.canvas.characters); render(d.canvas);
      $("match-hint").textContent = "✓ character saved — applies to their shots";
    } catch (e) { err(e.message); save.disabled = false; }
  });
  $("chars-btn").addEventListener("click", async () => {
    if (!runId) { err("Plan a story first."); return; }
    err("");
    try {
      const d = await api(`/api/canvas/${runId}/characters`, {});
      renderCharacters((d.canvas && d.canvas.characters) || d.characters || []);
    } catch (e) { err(e.message); }
  });
  $("characters").addEventListener("change", async (ev) => {
    const file = ev.target.closest('input[type="file"][data-char]');
    const consent = ev.target.closest("input.consent");
    if (file && file.files[0]) {
      const cid = file.getAttribute("data-char");
      try {
        const fd = new FormData();
        fd.append("photo", file.files[0]); fd.append("session_id", runId); fd.append("frame_id", "char_" + cid);
        const up = await fetch("/upload-photo", { method: "POST", body: fd }).then((r) => r.json());
        if (up.tmp_path) {
          const d = await api(`/api/canvas/${runId}/character`, { char_id: cid, ref_path: up.tmp_path });
          renderCharacters(d.canvas.characters); render(d.canvas);
        } else { err(up.error || "upload failed"); }
      } catch (e) { err(e.message); }
      return;
    }
    if (consent) {
      const cid = consent.getAttribute("data-char");
      try {
        const d = await api(`/api/canvas/${runId}/character`, { char_id: cid, consent: consent.checked });
        renderCharacters(d.canvas.characters); render(d.canvas);
      } catch (e) { err(e.message); }
    }
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
