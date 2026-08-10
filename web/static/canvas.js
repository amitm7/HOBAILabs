/* Director Canvas board controller (AGENTIC_CANVAS_PLAN).
 * Talks to /api/canvas/* — all cost + gating is server-truth; this only renders.
 * Cost is shown per stage BEFORE spend (the fix for the competitor's wallet drain). */
(function () {
  "use strict";
  let runId = null;
  let activeFrameId = null;
  let brandLogoPath = "";
  let credProbed = "";
  let stillsCache = {};
  let clipsCache = {};

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

  function isDirectUrl(p) {
    const s = String(p || "");
    return /^(https?:|blob:|data:)/i.test(s)
      || /^(\/media\b|\/clip\b|\/download\b|\/output\b|\/static\b)/.test(s);
  }

  function mediaUrl(p) {
    if (!p) return "";
    return isDirectUrl(p) ? p : "/media?path=" + encodeURIComponent(p);
  }

  function isVideoPath(p) {
    return /\.(mov|mp4|m4v|webm|avi)$/i.test(String(p || "").split("?", 1)[0]);
  }

  function mediaThumb(path, alt = "") {
    if (!path) return `<div style="height:100%;background:#cbd5e1"></div>`;
    const src = mediaUrl(path);
    if (isVideoPath(path)) {
      return `<div class="video-tile"><video src="${src}" muted preload="metadata" playsinline></video><span class="play-icon">▶</span></div>`;
    }
    return `<img class="thumb" src="${src}" alt="${escapeHtml(alt)}">`;
  }

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
    if ($("cap-engine")) $("cap-engine").checked = cs.engine === "remotion";
    if (canvas.orientation) $("orientation").value = canvas.orientation;
    if ($("ip") && canvas.ip !== undefined) $("ip").value = canvas.ip;
    // Story-type mode: AI (fiction) hides the real-media tools (folder match / enhance /
    // pick / re-match) since there's no real folder — everything is generated.
    const ai = (canvas.story_type || "real") === "ai";
    document.body.classList.toggle("story-ai", ai);
    if ($("story-type") && canvas.story_type) $("story-type").value = canvas.story_type;
    const w = canvas.world || {};
    if ($("world-style") && document.activeElement !== $("world-style")) $("world-style").value = w.style || "";
    if ($("world-setting") && document.activeElement !== $("world-setting")) $("world-setting").value = w.setting || "";
    // T3 auto-fill: suggestions from the brief fill EMPTY fields only, clearly marked.
    // Nothing generates from them until the operator clicks "Apply world".
    const sug = canvas.suggestions || {};
    if (sug.world_style && $("world-style") && !$("world-style").value.trim()
        && document.activeElement !== $("world-style")) {
      $("world-style").value = sug.world_style;
    }
    if (sug.world_setting && $("world-setting") && !$("world-setting").value.trim()
        && document.activeElement !== $("world-setting")) {
      $("world-setting").value = sug.world_setting;
    }
    if ((sug.world_style || sug.world_setting) && !(w.style || w.setting) && $("world-hint")) {
      $("world-hint").textContent = "✨ suggested from your story — edit freely, then Apply world";
    }
    if (sug.narrator_profile && $("voice-id")) {
      $("voice-id").title = "✨ suggested narrator: " + sug.narrator_profile;
      const ah = $("audio-hint"); if (ah) ah.textContent = "✨ narrator suggestion: " + sug.narrator_profile;
    }
    if (sug.target_seconds && $("length") && !$("length").dataset.userSet) {
      const opt = [...$("length").options].find((o) => o.value === String(sug.target_seconds));
      if (opt) { $("length").value = String(sug.target_seconds); }
    }
    // Reel-level mood (auto-fill slice 1): saved value wins; else ✨-fill the
    // suggestion into the EMPTY field only. Per-shot emotion stays on the cards.
    if ($("mood") && document.activeElement !== $("mood")) {
      if (canvas.mood) { $("mood").value = canvas.mood; }
      else if (sug.mood && !$("mood").value.trim()) {
        $("mood").value = sug.mood;
        $("mood").title = "✨ suggested from your story — edit freely";
      }
    }
    _settingsReady = true;
  }
  $("world-save") && $("world-save").addEventListener("click", async () => {
    if (!runId) { err("Plan a story first."); return; }
    err(""); const btn = $("world-save"); btn.disabled = true;
    try {
      const d = await api(`/api/canvas/${runId}/world`,
        { style: $("world-style").value, setting: $("world-setting").value });
      $("world-hint").textContent = "✓ world applied to every shot";
      setTimeout(() => { $("world-hint").textContent = ""; }, 1800);
      render(d.canvas);
    } catch (e) { err(e.message); }
    finally { btn.disabled = false; }
  });
  async function saveSettings() {
    if (!runId || !_settingsReady) return;
    const caption_style = {
      enabled: $("cap-enabled").checked,
      position: $("cap-position").value,
      font: $("cap-font").value,
      size: parseInt($("cap-size").value, 10) || 24,
      color: $("cap-color").value,
      max_lines: parseInt($("cap-lines").value, 10),
      engine: ($("cap-engine") && $("cap-engine").checked) ? "remotion" : "libass",
    };
    try {
      const d = await api(`/api/canvas/${runId}/settings`,
        { caption_style, orientation: $("orientation").value, ip: $("ip").value,
          mood: $("mood") ? $("mood").value : "" });
      $("settings-hint").textContent = "✓ saved";
      setTimeout(() => { $("settings-hint").textContent = ""; }, 1500);
      if (d.canvas) render(d.canvas);  // orientation may re-lock stages or invalidate stills
    } catch (e) { err(e.message); }
  }
  ["cap-enabled", "cap-position", "cap-font", "cap-size", "cap-color", "cap-lines",
   "cap-engine", "orientation", "ip", "mood"]
    .forEach((id) => { const el = $(id); if (el) el.addEventListener("change", saveSettings); });
  // Populate the IP/watermark dropdown from the server (HOB properties).
  (async function loadIPs() {
    try {
      const d = await fetch("/ips").then((r) => r.json());
      const sel = $("ip"); if (!sel || !d.ips) return;
      d.ips.forEach((ip) => {
        const o = document.createElement("option");
        o.value = ip.id || ip.name || ip; o.textContent = (ip.name || ip.id || ip) + (ip.has_png === false ? " (no logo)" : "");
        sel.appendChild(o);
      });
    } catch (e) { /* no IPs configured — dropdown stays "No watermark" */ }
  })();

  // Last-rendered canvas (re-render source for cache reveals + view toggles).
  let lastCanvas = null;
  let sketchPoll = null;

  // ── Audio options (music / upload song / voiceover) ──────────────────────────
  let canvasMusicPath = "";
  function audioOpts() {
    const mode = $("audio-mode").value;
    const o = { music_type: mode };
    if (mode === "upload") o.music_path = canvasMusicPath;
    if (mode === "voiceover") {
      o.voice_id = $("voice-id").value || "";
      // Optional music bed UNDER the narration (uploaded via the same song control).
      if (canvasMusicPath) o.bg_music_path = canvasMusicPath;
    }
    return o;
  }
  let voicesCache = [];   // shared by the narrator select AND per-character dropdowns (T4)
  async function loadVoices() {
    try {
      const d = await api("/voices");
      voicesCache = d.voices || d || [];
      $("voice-id").innerHTML = voicesCache.map((v) =>
        `<option value="${v.voice_id}">${escapeHtml(v.name || v.voice_id)}</option>`).join("");
    } catch (e) { /* ignore */ }
  }
  // T3: a manually chosen length must never be overwritten by a suggestion.
  $("length") && $("length").addEventListener("change", () => { $("length").dataset.userSet = "1"; });

  // Distinguishes the S28 auto-default (below) from a real operator choice — the
  // T3 rule applies to audio too: a manually chosen mode is never overwritten.
  let audioAutoSetting = false;
  $("audio-mode").addEventListener("change", () => {
    if (!audioAutoSetting) $("audio-mode").dataset.userSet = "1";
    const m = $("audio-mode").value;
    $("voice-id").hidden = m !== "voiceover";
    // Song upload doubles as the optional BED under narration in voiceover mode.
    $("song-label").hidden = !(m === "upload" || m === "voiceover");
    $("song-label").firstChild.textContent = m === "voiceover" ? "⬆ Music bed (optional) " : "⬆ Choose song ";
    if (m !== "upload" && m !== "voiceover") { $("song-name").textContent = ""; canvasMusicPath = ""; }
    if (lastCanvas) renderBoard(lastCanvas.board);   // Audio column mirrors this mode
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

  // S30 P3: per-asset editorial review chip (galleri5-style QA states). Empty = unset.
  const REVIEW_META = {
    needs_review:     { label: "NEEDS REVIEW", cls: "rv-needs" },
    approved:         { label: "APPROVED",     cls: "rv-ok" },
    production_ready: { label: "PROD READY",   cls: "rv-prod" },
    rejected:         { label: "REJECTED",     cls: "rv-no" },
  };
  function reviewChip(review) {
    const m = REVIEW_META[review];
    return m ? `<span class="cv-badge ${m.cls}">${m.label}</span>` : "";
  }

  function renderBoard(board) {
    const empty = board.length === 0;
    $("legend").hidden = empty;
    $("chat").hidden = empty;
    $("assets").hidden = empty;

    if (empty) {
      // F4: a blank board reads as "broken". Say what will appear and how spending works.
      $("board").innerHTML = `<div class="board-empty">
        <div class="be-emoji">🎬</div>
        <p class="be-title">Your storyboard appears here</p>
        <p class="be-sub">Each shot becomes a card, left→right in cut order. You generate and approve one stage at a time from the bar below — nothing is charged until you approve it.</p>
      </div>`;
      $("timeline-strip").innerHTML = "";
      renderLanes([]);
      if ($("board-hint")) $("board-hint").hidden = true;
      $("inspector").hidden = true;
      return;
    }
    if ($("board-hint")) $("board-hint").hidden = false;

    // Final Cut is a whole-reel assembly, not a per-shot stage — it stays off the
    // storyboard and is driven from the top "Render reel" button + the render panel.
    const allStages = lastCanvas ? lastCanvas.stages : [];
    const stages = allStages.filter((s) => s.id !== "finalcut");

    // The board is the FILM, in cut order, left→right. The stage ACTIONS live in the
    // common bar below it (renderStageBar) — a storyboard, not a tracker.
    $("board").innerHTML = board.map((c, i) => shotCard(c, i, board.length)).join("");
    renderStageBar(stages);
    renderTimelineStrip(board);
    renderLanes(board);
    if (activeFrameId) {
      renderInspector(activeFrameId);
    }
  }

  // ── S33.2 stage lanes — one strip per pipeline layer, board-aligned columns ──
  // Sketches (storyboard_art) → Key Frames (stills) → Video (clips) → Audio (who
  // carries each beat). Every cell opens the SAME Shot Inspector; still/clip
  // updates flow through here automatically because showStillOnCard/showClipOnCard
  // re-run renderBoard. Cells reuse .video-poster so hover-play works unchanged.
  function laneCell(inner, fid, extra) {
    const active = (fid === activeFrameId) ? " active" : "";
    return `<div class="lane-cell${extra || ""}${active}" data-frame="${fid}">${inner}</div>`;
  }
  function renderLanes(board) {
    const sk = $("lane-sketch"), fr = $("lane-frames"), vd = $("lane-video"), au = $("lane-audio");
    if (!sk || !fr || !vd || !au) return;
    sk.innerHTML = board.map((c, i) => laneCell(
      `<div class="lc-media">${c.storyboard_art
        ? `<img class="thumb sketch" src="${mediaUrl(c.storyboard_art)}" alt="storyboard sketch">`
        : `<div class="lc-empty">✏️</div>`}</div>
       <div class="lc-foot"><span class="lc-n">${i + 1}</span>
         <span>${c.storyboard_art ? escapeHtml(c.camera || "") : "not sketched"}</span></div>`,
      c.frame_id)).join("");
    fr.innerHTML = board.map((c, i) => {
      const still = stillsCache[c.frame_id] || c.real_path || "";
      const badge = c.asset_kind === "real"
        ? `<span class="kindbadge kb-real">REAL</span>`
        : `<span class="kindbadge kb-${c.asset_kind}">${c.asset_kind === "ai_person" ? "AI FACE" : "AI"}</span>`;
      return laneCell(
        `<div class="lc-media">${still ? mediaThumb(still, "key frame") : `<div class="lc-empty">${i + 1}</div>`}${badge}</div>
         <div class="lc-foot"><span class="lc-n">${i + 1}</span>${reviewChip(c.review)}
           <span>${still ? escapeHtml(c.shot_size || "") : "no frame yet"}</span></div>`,
        c.frame_id);
    }).join("");
    vd.innerHTML = board.map((c, i) => {
      const still = stillsCache[c.frame_id] || c.real_path || "";
      const clip = clipsCache[c.frame_id] || "";
      const media = clip
        ? `<div class="frame video-poster" data-clip="${escapeHtml(clip)}" data-still="${escapeHtml(still)}" data-frame="${c.frame_id}">
             ${mediaThumb(still, "video poster")}<span class="play-icon">▶</span></div>`
        : `<div class="lc-empty">${i + 1}</div>`;
      return laneCell(
        `<div class="lc-media">${media}</div>
         <div class="lc-foot"><span class="lc-n">${i + 1}</span>
           <span>${clip ? (c.duration ? c.duration + "s" : "clip ready") : "no clip yet"}</span></div>`,
        c.frame_id);
    }).join("");
    const FORM_ICON = { dialogue: "🎬", silent: "🌫" };
    const FORM_FALLBACK = { dialogue: "(dialogue beat)", silent: "(silent beat)" };
    au.innerHTML = board.map((c, i) => laneCell(
      `<span class="lc-n">${i + 1}</span>
       <span class="form-tag form-${c.form || "narration"}" title="Storytelling form (S28)">${FORM_ICON[c.form] || "🎙"}</span>
       <span class="lc-line">${escapeHtml(c.caption || FORM_FALLBACK[c.form] || "(no line)")}</span>
       ${c.duration ? `<span class="sb-dur" style="margin-left:auto">${c.duration}s</span>` : ""}`,
      c.frame_id, " lc-audio")).join("");
  }

  // ── Storyboard (S31) ───────────────────────────────────────────────────────
  // Shaped after Interface Builder: shots read left→right in CUT ORDER, joined by
  // segue arrows, because a reel is linear time. (This replaced a stage×shot matrix
  // that read like a project tracker — five cells per shot saying "Awaiting…".)
  // Per-shot progress rides on the card as pips; the stage ACTIONS moved to the
  // common bar below (renderStageBar), which is where the Generate/Approve buttons
  // that used to sit in the column headers now live.

  // Pips are derived from the assets that actually exist for THIS shot — truer than
  // the global stage status, which is identical on every card and so says nothing
  // about the shot you're looking at.
  function shotPips(c, stillPath, clipPath) {
    // [full name (tooltip), short label (under the bar), done?]. The short label is
    // what makes the four bars self-explanatory on the card itself.
    const steps = [
      ["Script", "Script", !!(c.caption || c.shot_size)],
      ["Storyboard", "Board", !!(c.storyboard_art || c.motion)],
      ["Key frame", "Still", !!stillPath],
      ["Video", "Clip", !!clipPath],
    ];
    const tip = steps.map(([n, , d]) => `${n}: ${d ? "done" : "pending"}`).join(" · ");
    return `<span class="sb-pips" title="Shot progress — ${tip}">` +
      steps.map(([, ab, d]) =>
        `<span class="pip${d ? " on" : ""}"><i></i><em>${ab}</em></span>`).join("") + `</span>`;
  }

  function shotCard(c, index, total) {
    const fid = c.frame_id;
    const isReal = c.asset_kind === "real";
    const stillPath = stillsCache[fid] || c.real_path || "";
    const clipPath = clipsCache[fid] || "";
    const active = (fid === activeFrameId) ? " active" : "";
    const formIcon = c.form === "dialogue" ? "🎬" : c.form === "silent" ? "🌫" : "🎙";

    // Best available visual, in order of truth: the rendered clip → the key frame →
    // the reference it will be conditioned on → the storyboard sketch → nothing yet.
    let media;
    if (clipPath) {
      media = `<div class="frame video-poster" data-clip="${escapeHtml(clipPath)}" data-still="${escapeHtml(stillPath)}" data-frame="${fid}">
                 ${mediaThumb(stillPath, "video poster")}<span class="play-icon">▶</span></div>`;
    } else if (stillPath) {
      media = `<div class="frame">${mediaThumb(stillPath, isReal ? "real media" : "keyframe")}</div>`;
    } else if (c.ref_path) {
      media = `<div class="frame"><span class="refchip">${mediaThumb(c.ref_path, "ref")}<b>REF</b></span></div>`;
    } else if (c.storyboard_art) {
      media = `<div class="frame"><img class="thumb sketch" src="${mediaUrl(c.storyboard_art)}" alt=""></div>`;
    } else {
      media = `<div class="sb-empty"><span>${index + 1}</span></div>`;
    }

    const badge = isReal
      ? `<span class="kindbadge kb-real" title="Real media — passes through untouched">REAL</span>`
      : `<span class="kindbadge kb-${c.asset_kind}">${c.asset_kind === "ai_person" ? "AI FACE" : "AI"}</span>`;

    const card = `
      <div class="sb-card${active}${isReal ? " is-real" : ""}" data-frame="${fid}">
        <div class="sb-head">
          <span class="sb-n">${index + 1}</span>
          <span class="form-tag form-${c.form || "narration"}" title="Storytelling form (S28): 🎬 dialogue · 🎙 narration · 🌫 silent. Override in the Inspector.">${formIcon}</span>
          ${c.on_screen ? `<span class="form-tag" title="Who is ON SCREEN in this shot (may differ from who speaks — the narrator can describe someone else)">👤 ${escapeHtml(c.on_screen)}</span>` : ""}
          <span class="sb-head-sp"></span>
          ${reviewChip(c.review)}${badge}
        </div>
        <div class="sb-media">${media}</div>
        <div class="sb-body">
          <div class="sb-cap" title="${escapeHtml(c.caption || "")}">${escapeHtml(c.caption || "(no line)")}</div>
          <div class="sb-meta">
            <span class="sb-motion">${arrowSvg(c.arrow)} ${escapeHtml(c.motion || "slow push")}</span>
            <span class="sb-dur">${c.duration ? c.duration + "s" : ""}</span>
          </div>
          ${shotPips(c, stillPath, clipPath)}
        </div>
      </div>`;

    // The segue: the cut from this shot into the next one.
    const link = index < total - 1
      ? `<div class="sb-link" aria-hidden="true"><span class="sb-line"></span><span class="sb-arrow">▶</span></div>`
      : "";
    return card + link;
  }

  // The storyboard's common bar. Deliberately carries the SAME .gen[data-stage] /
  // .appr[data-approve] markup the old column headers used, and the same click handler
  // is bound to it — so every rule (the AI-face soft gate, per-stage API routing, the
  // spend labels) is unchanged. Only the location moved.
  const STAGE_BAR_LABEL = {
    script: "📝 Script", storyboard: "✏️ Storyboard", keyframes: "🖼 Images",
    audio: "🎵 Audio", video: "🎬 Video",
  };
  function renderStageBar(stages) {
    const bar = $("stage-bar");
    if (!bar) return;
    const tools = stages.map((s) => {
      let btn;
      if (s.id === "audio") {
        btn = `<button disabled title="Music is mixed at Final Cut — choose the track in Audio Setup">in Final Cut</button>`;
      } else if (s.status === "generating") {
        btn = `<button disabled>Generating…</button>`;
      } else if (s.status === "done") {
        btn = `<button class="appr" data-approve="${s.id}">Approve ✓</button>`;
      } else if (s.status === "approved") {
        btn = `<button disabled>Approved ✓</button>`;
      } else if (s.ready) {
        const eta = s.paid ? fmtEta(s.eta_sec) : "";
        btn = `<button class="gen" data-stage="${s.id}" title="${escapeHtml(s.blurb || "")}${eta ? " · ~" + eta : ""}">${
          s.paid ? `Generate · ${usd(s.cost_usd)}` : "Generate"}</button>`;
      } else {
        btn = `<button disabled title="Approve the previous step first">Locked</button>`;
      }
      return `<div class="sb-tool${s.status === "generating" ? " shimmer" : ""}">
        <span class="sb-tool-l">${STAGE_BAR_LABEL[s.id] || s.label}
          <span class="cv-badge st-${s.status}">${s.status}</span></span>
        ${btn}</div>`;
    }).join("");

    // The stage bar is the PIPELINE only. The former "extras" (🎵 Music, ✂️ Beat cut,
    // 🔊 SFX) were removed (S31 red-team): Music/Beat were buttons that did nothing but
    // scroll to the left-rail controls that already own them, and SFX was permanently
    // disabled (its Final-Cut mix stem is ticketed). Music + beat live in Audio Setup /
    // World Context where they belong; SFX returns here when the mix ships.
    // F2: name the bar so its Generate/Approve buttons read as the reel's pipeline
    // controls (they act on the whole board, deliberately, rather than per card).
    bar.innerHTML = `<span class="sb-bar-lead">Pipeline</span>` + tools;
  }

  function renderTimelineStrip(board) {
    const strip = $("timeline-strip");
    if (!strip) return;
    const isApproved = lastCanvas && lastCanvas.stages.find(s => s.id === "video")?.status === "approved";
    strip.innerHTML = board.map((c, i) => {
      const fid = c.frame_id;
      const stillPath = stillsCache[fid] || c.real_path || "";
      const activeClass = (fid === activeFrameId) ? " active" : "";
      // Empty cards used to be a flat gray box (reads as "broken"); show the shot number
      // in a muted placeholder so the strip reads as "shot N, no preview yet".
      const inner = stillPath
        ? mediaThumb(stillPath, "timeline frame")
        : `<div class="tl-empty">${i + 1}</div>`;
      return `
        <div class="cv2-timeline-card${activeClass}${isApproved ? " approved" : ""}" data-frame="${fid}" title="Shot ${i + 1}">
          ${inner}
        </div>
      `;
    }).join("");
  }

  function renderInspector(fid) {
    const c = lastCanvas.board.find((x) => x.frame_id === fid);
    if (!c) {
      $("inspector-content").innerHTML = `<span class="muted">Select a card on the board to view details.</span>`;
      return;
    }

    const stillPath = stillsCache[fid] || c.real_path || "";
    const clipPath = clipsCache[fid] || "";
    
    let visualHtml = "";
    if (clipPath) {
      visualHtml = `
        <div class="inspector-visual">
          <video src="${mediaUrl(clipPath)}" autoplay loop muted playsinline style="width:100%;height:100%;object-fit:cover"></video>
        </div>
      `;
    } else if (stillPath) {
      visualHtml = `
        <div class="inspector-visual">
          ${mediaThumb(stillPath, "selected media")}
        </div>
      `;
    } else {
      visualHtml = `
        <div class="inspector-visual" style="display:flex;align-items:center;justify-content:center;background:#f8fafc;color:#94a3b8;font-size:24px">
          ${arrowSvg(c.arrow)}
        </div>
      `;
    }

    const keyCost = lastCanvas.stages.find(s => s.id === "keyframes")?.cost_usd || 0;
    const vidCost = lastCanvas.stages.find(s => s.id === "video")?.cost_usd || 0;
    const singleShotCost = (keyCost + vidCost) / lastCanvas.board.length;

    let html = `
      <div style="display:flex;flex-direction:column;gap:12px">
        ${visualHtml}
        
        <div>
          <div class="inspector-label">Provenance Status</div>
          <div style="display:flex;align-items:center;gap:6px;margin-top:4px">
            <span class="cv-badge st-${c.asset_kind === "real" ? "approved" : "generating"}">${c.asset_kind.toUpperCase()}</span>
            ${c.ai_likeness ? `<span class="cv-badge st-generating">AI LIKENESS</span>` : ""}
            ${c.restored ? `<span class="cv-badge st-done">RESTORED</span>` : ""}
            ${c.recreated ? `<span class="cv-badge st-done">RECREATED</span>` : ""}
          </div>
        </div>

        <div class="inspector-field">
          <label class="inspector-label">Review — your verdict on this shot's current asset</label>
          <select class="edit" data-frame="${fid}" data-field="review_status">
            <option value="" ${!c.review ? "selected" : ""}>— unset —</option>
            <option value="needs_review" ${c.review === "needs_review" ? "selected" : ""}>🔎 Needs review</option>
            <option value="approved" ${c.review === "approved" ? "selected" : ""}>✅ Approved</option>
            <option value="production_ready" ${c.review === "production_ready" ? "selected" : ""}>🏁 Production-ready</option>
            <option value="rejected" ${c.review === "rejected" ? "selected" : ""}>❌ Rejected (re-roll it)</option>
          </select>
        </div>

        <div class="inspector-field">
          <label class="inspector-label">Caption / Script Line</label>
          <input class="edit" data-frame="${fid}" data-field="caption" value="${escapeHtml(c.caption || "")}" placeholder="narration script line">
        </div>

        <div class="inspector-field">
          <label class="inspector-label">Form (S28) — who carries this beat</label>
          <select class="edit" data-frame="${fid}" data-field="form_override">
            <option value="" ${!c.form_override ? "selected" : ""}>Auto — detected: ${c.form || "narration"}</option>
            <option value="dialogue" ${c.form_override === "dialogue" ? "selected" : ""}>🎬 Dialogue (character speaks in-scene)</option>
            <option value="narration" ${c.form_override === "narration" ? "selected" : ""}>🎙 Narration (storyteller's voice)</option>
            <option value="silent" ${c.form_override === "silent" ? "selected" : ""}>🌫 Silent (atmosphere only)</option>
          </select>
        </div>

        <div class="inspector-field">
          <label class="inspector-label">Camera Move</label>
          <input class="edit" data-frame="${fid}" data-field="motion_override" value="${escapeHtml(c.motion || "")}" placeholder="camera vector / zoom">
        </div>

        <div class="inspector-field">
          <label class="inspector-label">Image Prompt</label>
          <textarea class="edit" data-frame="${fid}" data-field="image_prompt" rows="3" placeholder="visual elements prompt">${escapeHtml(c.image_prompt || "")}</textarea>
        </div>

        <div class="cv2-left-row">
          <div class="inspector-field" style="flex:1">
            <label class="inspector-label">Duration (sec)</label>
            <input class="edit" data-frame="${fid}" data-field="duration" type="number" min="1" max="15" step="0.5" value="${c.duration || ""}">
          </div>
          ${(c.fidelity_options || []).length ? `
          <div class="inspector-field" style="flex:1">
            <label class="inspector-label">Fidelity Rung</label>
            <select class="fid-sel" data-frame="${fid}">
              ${c.fidelity_options.filter(opt => opt !== "recreate").map(opt => `<option value="${opt}" ${opt === c.fidelity ? "selected" : ""}>${escapeHtml(fidelityLabel(opt))}</option>`).join("")}
            </select>
          </div>` : ""}
        </div>

        ${c.fidelity_suggested && c.fidelity_suggested !== c.fidelity ? `
          <div class="fidelity-row">
            <span>Suggested: <b>${c.fidelity_suggested.toUpperCase()}</b></span>
            <span class="muted">${escapeHtml(c.fidelity_reason)}</span>
            <button class="fid-suggest" data-frame="${fid}" data-rung="${c.fidelity_suggested}">Accept Recommendation</button>
          </div>
        ` : ""}

        <div class="inspector-field">
          <label class="inspector-label">Estimated Cost</label>
          <span style="font-weight:600;font-size:12px">$${singleShotCost.toFixed(2)} USD</span>
        </div>

        <div style="border-top:1px solid #f1f5f9;margin-top:6px;padding-top:10px">
          <div class="inspector-label" style="margin-bottom:6px">Shot Actions</div>
          <div class="inspector-actions">
            <button class="btn-secondary restill" data-frame="${fid}" title="Regenerate ONLY the key frame from the (edited) Image Prompt — image cost, review before paying for video">🖼 New still</button>
            <button class="btn-secondary reroll" data-frame="${fid}" title="Regenerate still + clip (image + video cost)">↻ Re-roll</button>
            ${c.can_recreate ? `<button class="btn-secondary recreate" data-frame="${fid}">🎬 Re-create</button>` : ""}
            ${c.can_upscale ? `<button class="btn-secondary upscale" data-frame="${fid}">⬆ Upscale</button>` : ""}
            ${c.can_revert_real ? `<button class="src-btn revert-real btn-secondary" data-frame="${fid}">Revert to real</button>` : ""}
          </div>
        </div>

        <details class="insp-group">
          <summary>Replace asset — swap this shot's media</summary>
          <div class="inspector-actions" style="margin-top:6px">
            <button class="btn-secondary pick-btn" data-frame="${fid}">🖼 Folder pick</button>
            <button class="btn-secondary rematch-btn" data-frame="${fid}">⟳ Re-match</button>
            <label class="btn-primary attach-btn" style="display:flex;align-items:center;justify-content:center">
              📎 Real photo
              <input type="file" accept="image/*" data-frame="${fid}" data-mode="real" hidden>
            </label>
            <button class="btn-primary locked-face" data-frame="${fid}" title="AI-generate this shot with the on-screen character's LOCKED face (from the Character sheet) — same person, staged moment. Labeled as AI likeness.">🎭 Their face (AI)</button>
            <label class="btn-secondary attach-btn" style="display:flex;align-items:center;justify-content:center" title="Upload a DIFFERENT photo as the face reference for this one shot">
              👤 Other face…
              <input type="file" accept="image/*" data-frame="${fid}" data-mode="reference" hidden>
            </label>
            <button class="btn-secondary ai-gen" data-frame="${fid}" title="Fully AI-generated with NO identity — an anonymous figure/scene. Use when nobody's real face should appear.">👻 Generic (no face)</button>
          </div>
        </details>

        <details class="insp-group">
          <summary>Overlays — comic devices (max 2, applied at Final Cut)</summary>
          <div id="ov-list" style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:6px">
            ${(c.overlays || []).map((o, i) => `
              <span style="background:#eef2f7;border:1px solid #e2e8f0;border-radius:999px;padding:3px 8px;font-size:10px">
                ${o.kind} · ${o.style} · ${o.pos}/${o.size}
                <a href="#" class="ov-x" data-frame="${fid}" data-i="${i}" style="color:#dc2626;text-decoration:none;font-weight:700;margin-left:4px">✕</a>
              </span>`).join("") || `<span class="muted">none</span>`}
          </div>
          ${(c.overlays || []).length < 2 ? `
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:4px;margin-bottom:6px">
            <select id="ov-kind" class="ov-field"><option value="memory">📷 Memory inset</option><option value="speaker">🗣 Speaker chip</option><option value="thought">💭 Thought bubble</option><option value="sticker">✨ Thinking sticker</option></select>
            <select id="ov-src" class="ov-field">
              <option value="">(no image — sticker)</option>
              ${((lastCanvas && lastCanvas.characters) || []).filter((ch) => ch.ref_path)
                 .map((ch) => `<option value="@char:${ch.id}">👤 ${escapeHtml(ch.role || ch.name || ch.id)}</option>`).join("")}
            </select>
            <select id="ov-style" class="ov-field"><option value="rounded">Rounded</option><option value="polaroid">Polaroid</option><option value="chip">Circle chip</option><option value="bubble">Thought bubble</option></select>
            <select id="ov-pos" class="ov-field"><option value="tr">Top right</option><option value="tl">Top left</option><option value="tc">Top centre</option><option value="ml">Mid left</option><option value="mr">Mid right</option><option value="bl">Bottom left</option><option value="br">Bottom right</option></select>
            <select id="ov-size" class="ov-field"><option value="s">Small</option><option value="m" selected>Medium</option><option value="l">Large</option></select>
            <select id="ov-when" class="ov-field"><option value="all">Whole shot</option><option value="first-half">First half</option><option value="second-half">Second half</option></select>
          </div>
          <div class="inspector-actions">
            <button class="btn-secondary ov-preview" data-frame="${fid}">👁 Preview on still</button>
            <button class="btn-primary ov-add" data-frame="${fid}">＋ Add overlay</button>
          </div>
          <img id="ov-preview-img" style="width:100%;border-radius:8px;margin-top:6px;border:1px solid #e2e8f0" hidden>
          ` : ""}
        </details>
      </div>
    `;

    $("inspector-content").innerHTML = html;
    loadTakes(fid);   // T5: previous clips for this shot, restorable without re-spend
  }

  // ── T14 Frame Composer: build the overlay spec from the inspector form ────────
  function ovFromForm() {
    return {
      kind: $("ov-kind").value, image: $("ov-src").value,
      style: $("ov-style").value, pos: $("ov-pos").value,
      size: $("ov-size").value, when: $("ov-when").value,
    };
  }
  async function ovSave(fid, overlays) {
    const d = await api(`/api/canvas/${runId}/overlays`, { frame_id: fid, overlays });
    render(d.canvas);
    selectFrame(fid);
    $("match-hint").textContent = "✓ overlays saved — they composite onto the clip at Final Cut";
  }

  // ── T5 take history: list + restore previous clips (a good take is never lost) ──
  async function loadTakes(fid) {
    if (!runId) return;
    try {
      const d = await api(`/api/canvas/${runId}/takes?frame_id=${encodeURIComponent(fid)}`);
      const takes = d.takes || [];
      if (!takes.length || activeFrameId !== fid) return;
      const host = $("inspector-content");
      const div = document.createElement("div");
      div.innerHTML = `
        <div style="border-top:1px solid #f1f5f9;padding-top:10px;margin-top:10px">
          <div class="inspector-label" style="margin-bottom:6px">Previous takes (${takes.length}) — click to restore, free</div>
          <div style="display:flex;gap:6px;flex-wrap:wrap">
            ${takes.map((t) => `
              <div class="take-chip" data-frame="${fid}" data-path="${encodeURIComponent(t.path)}"
                   title="Restore this take (the current clip is saved as a take too)"
                   style="width:84px;cursor:pointer;border:2px solid #e2e8f0;border-radius:8px;overflow:hidden">
                <video src="${mediaUrl(t.path)}" muted preload="metadata" style="width:100%;height:56px;object-fit:cover;display:block"></video>
                <div style="font-size:9px;color:#64748b;text-align:center;padding:2px">↩ restore</div>
              </div>`).join("")}
          </div>
        </div>`;
      host.appendChild(div);
    } catch (e) { /* takes are a bonus — never block the inspector */ }
  }

  function selectFrame(fid) {
    activeFrameId = fid;
    // One selection, every representation: board card, timeline tile, stage lanes.
    document.querySelectorAll(".sb-card, .cv2-timeline-card, .lane-cell").forEach((el) => {
      el.classList.toggle("active", el.getAttribute("data-frame") === fid);
    });
    $("inspector").hidden = false;
    renderInspector(fid);
  }

  function fidelityLabel(r) {
    return r === "passthrough" ? "Real (untouched)"
         : r === "restore" ? "Restore (clean)"
         : r === "recreate" ? "Re-create (cinematic)" : r;
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
        const fr = document.querySelector(`.sb-card[data-frame="${fid}"]`);
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
      $("match-hint").textContent = "✓ shot set to GENERIC AI — no identity. For their face, use 🎭";
    } catch (e) { err(e.message); if (btn) btn.disabled = false; }
  }
  async function useLockedFace(fid, btn) {
    if (!runId) return;
    err(""); if (btn) btn.disabled = true;
    try {
      const d = await api(`/api/canvas/${runId}/use-locked-face`, { frame_id: fid });
      render(d.canvas);
      $("match-hint").textContent = "✓ shot will be AI-generated with their locked face (labeled AI likeness)";
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
    const fr = document.querySelector(`.sb-card[data-frame="${fid}"]`);
    if (fr) fr.classList.add("shimmer");
    const oldLabel = btn ? btn.textContent : "";
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
    finally { if (fr) fr.classList.remove("shimmer"); if (btn) { btn.disabled = false; btn.textContent = oldLabel; } }
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
    // The gallery lives in the inspector (scrollable, never clipped by the grid cells).
    const open = document.querySelector(".picker");
    if (open) { open.remove(); if (open.getAttribute("data-frame") === fid) return; }  // toggle closed
    if (activeFrameId !== fid) selectFrame(fid);    // inspector must show this shot
    $("inspector").hidden = false;
    const card = $("inspector-content");
    if (!card) return;
    err("");
    let assets;
    try { assets = await loadAssets(); } catch (e) { err(e.message); return; }
    if (!assets.length) {
      $("match-hint").textContent = "No folder yet — paste your photos folder above and Match first.";
      return;
    }
    const gal = document.createElement("div");
    gal.className = "picker";
    gal.setAttribute("data-frame", fid);
    const undisplayable = (n) => /\.(heic|heif|bmp)$/i.test(n);
    gal.innerHTML = assets.map((a) => {
      const dp = encodeURIComponent(a.path);
      if (a.is_video)
        return `<video class="pk" data-frame="${fid}" data-path="${dp}" src="${mediaUrl(a.path)}" muted title="${a.name}"></video>`;
      if (undisplayable(a.name))
        return `<div class="pk pk-file" data-frame="${fid}" data-path="${dp}" title="${a.name}">${a.name.split(".").pop().toUpperCase()}</div>`;
      return `<img class="pk" data-frame="${fid}" data-path="${dp}" src="${mediaUrl(a.path)}" alt="${a.name}" title="${a.name}" loading="lazy">`;
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

  // ── Two-page shell (S31) ───────────────────────────────────────────────────
  // Page 1 = the prompt, page 2 = the canvas. Derived from "does a board exist"
  // and called from render(), so there is exactly one source of truth about which
  // page you are on — no separate view state to drift out of sync with the run.
  function setView(hasBoard) {
    const entry = $("entry-view"), board = $("canvas-view");
    if (!entry || !board) return;
    entry.hidden = !!hasBoard;
    board.hidden = !hasBoard;
  }

  function render(canvas) {
    lastCanvas = canvas;
    syncSettings(canvas);
    renderBoard(canvas.board);   // stage headers render as the grid's first row
    // Characters-first: the cast sheet is on screen as soon as the cast exists (AI
    // stories derive it at Plan time). Skip re-render while the operator is typing
    // in it — background polls must not clobber a focused attribute field.
    if ((canvas.characters || []).length && !$("characters").contains(document.activeElement)) {
      renderCharacters(canvas.characters);
    }
    // S30 location anchoring: same auto-render + same don't-clobber-typing rule.
    if ((canvas.locations || []).length && !$("locations").contains(document.activeElement)) {
      renderLocations(canvas.locations);
    }
    const hasBoard = canvas.board && canvas.board.length > 0;
    setView(hasBoard);   // a planned board IS page 2
    // S33: the Script zone is always on screen — same don't-clobber-typing rule as
    // the sheets, and hands-off while the translation review owns the panel.
    if (hasBoard && !langReview && !$("script-panel").contains(document.activeElement)) {
      renderScript(canvas.board);
    }
    // Brand panel rides the scope: an ad is what Commerce means. Publish rides an actual
    // render — there is nothing to post, prove or measure until the reel exists.
    if ($("brand-panel")) $("brand-panel").hidden = !(hasBoard && canvas.scope === "commerce");
    if (canvas.brand && !$("brand-name").value) {
      $("brand-name").value = canvas.brand.name || "";
      $("brand-cta").value = canvas.brand.cta_text || "";
      if (canvas.brand.disclosure !== undefined) $("brand-disclosure").checked = !!canvas.brand.disclosure;
      if (canvas.brand.logo_path) {
        brandLogoPath = canvas.brand.logo_path;
        $("brand-logo-name").textContent = canvas.brand.logo_path.split("/").pop();
      }
    }
    const rid = canvas.render_id || "";
    if ($("publish-panel")) $("publish-panel").hidden = !rid;
    if ($("out-locked")) $("out-locked").hidden = !!rid;   // the Output zone's "not yet" hint
    if (rid) {
      $("export-link").href = "/export/" + rid;
      $("prov-link").href = "/provenance/" + rid;
      // The C2PA credential only exists if signing actually ran for this reel (it makes
      // an outbound call and can be disabled). Probe once rather than show a link that
      // 404s — a dead button is worse than no button.
      $("cred-link").href = "/credential/" + rid;
      if (credProbed !== rid) {
        credProbed = rid;
        $("cred-link").hidden = true;
        fetch("/credential/" + rid, { method: "HEAD" })
          .then((r) => { $("cred-link").hidden = !r.ok; })
          .catch(() => { $("cred-link").hidden = true; });
      }
    }
    const vid = (canvas.stages || []).find((s) => s.id === "video");
    const videoApproved = vid && vid.status === "approved";
    $("render-btn").hidden = !hasBoard;
    // S33: the workspace jumplist, zoomer + settings gear appear once there's a film.
    if ($("settings-gear")) $("settings-gear").hidden = !hasBoard;
    if ($("cv-jump")) $("cv-jump").hidden = !hasBoard;
    if ($("cv-zoom")) $("cv-zoom").hidden = !hasBoard;
    $("render-btn").disabled = !videoApproved;
    // T13: language versions unlock exactly when Final Cut does.
    if ($("lang-controls")) $("lang-controls").hidden = !(hasBoard && videoApproved);
    $("render-btn").textContent = videoApproved ? "🎬 Final Cut" : "🎬 Render reel";
    $("render-btn").title = videoApproved
      ? "Assemble the approved clips into the finished reel"
      : "Generate & approve Key Frames → Video first.";
    updateCostBanner(canvas);
    // A silent finished reel is a defect, not a footnote — show it in the error strip.
    if (canvas.audio_warning) err("🔇 " + canvas.audio_warning);
    // One report panel, two sources: plan-time contract warnings (A2) + render-time
    // degradation events (T1). Both are "things the operator should know before judging".
    const planEvents = (canvas.plan_review || []).map((w) => ({
      step: "story-review",
      severity: w.severity === "warn" ? "warn" : "info",
      msg: `${w.frame_id || ""}: ${w.message || ""}${w.fix ? " → " + w.fix : ""}`,
    }));
    // S28 live form warnings (e.g. dialogue detected but characters share one voice)
    const formEvents = (canvas.form_warnings || []).map((w) => ({
      step: "form", severity: w.severity || "warn",
      msg: `${w.message || ""}${w.fix ? " → " + w.fix : ""}`,
    }));
    renderReport([...(canvas.render_report || []), ...planEvents, ...formEvents]);
    renderFormBadge(canvas.story_form);
    if (canvas.render_id) syncRendered();   // fill cards from disk + reconnect if running
    // S33: zones are content-sized — re-run the layout pass now the content is in,
    // and glide the first board of the session into view.
    if (hasBoard) {
      cvLayout();
      if (!cvFitted) { cvFitted = true; cvGlideTo("zone-board"); cvShowHint(); }
    }
  }

  // S28 detect→declare: the story-level form verdict, always visible once planned.
  function renderFormBadge(sf) {
    const el = $("form-badge");
    if (!el) return;
    if (!sf || (!sf.dialogue && !sf.narration && !sf.silent)) { el.hidden = true; return; }
    const label = { cinematic: "🎬 Cinematic", narrated: "🎙 Narrated", mixed: "🎭 Mixed form", silent: "🌫 Silent" }[sf.form] || sf.form;
    el.hidden = false;
    el.innerHTML = `<b>${label}</b> — ${sf.dialogue} dialogue · ${sf.narration} narrated · ${sf.silent} silent
      <span class="muted">(detected per shot from who speaks — flip any shot's form in its Inspector)</span>`;
    // S28 → audio: a story WITH spoken lines defaults Final-Cut audio to 🎙 VO —
    // a narrated story silently rendering music-only was the #1 "where is my
    // narration?" trap. Only flips the untouched factory default (Suno Music);
    // an operator's own choice (dataset.userSet) is never overwritten (T3 rule).
    const am = $("audio-mode");
    if (am && !am.dataset.userSet && am.value === "generate" && sf.form && sf.form !== "silent") {
      audioAutoSetting = true;
      am.value = "voiceover";
      am.dispatchEvent(new Event("change"));   // reveals the voice picker + bed upload
      audioAutoSetting = false;
    }
  }

  // T1 Degradation Ledger — the render's quality receipt. Alerts scream, warns list,
  // infos collapse. An empty report = clean render = the panel stays hidden.
  function renderReport(events) {
    const el = $("render-report");
    if (!el) return;
    const warns = events.filter((e) => e.severity === "warn");
    const alerts = events.filter((e) => e.severity === "alert");
    const infos = events.filter((e) => e.severity === "info");
    if (!events.length || (!warns.length && !alerts.length && !infos.length)) { el.hidden = true; return; }
    el.hidden = false;
    el.innerHTML = `
      <details ${alerts.length || warns.length ? "open" : ""}>
        <summary>🧾 Render report — ${alerts.length ? `<b style="color:#dc2626">${alerts.length} critical</b> · ` : ""}${warns.length} warning(s) · ${infos.length} info</summary>
        ${alerts.map((e) => `<div class="rr rr-alert">🔴 <b>${escapeHtml(e.step)}</b>: ${escapeHtml(e.msg)}</div>`).join("")}
        ${warns.map((e) => `<div class="rr rr-warn">⚠ <b>${escapeHtml(e.step)}</b>: ${escapeHtml(e.msg)}</div>`).join("")}
        ${infos.map((e) => `<div class="rr rr-info">ℹ ${escapeHtml(e.step)}: ${escapeHtml(e.msg)}</div>`).join("")}
      </details>`;
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
    clipsCache[fid] = src;
    if (lastCanvas) renderBoard(lastCanvas.board);
  }
  function showStillOnCard(fid, src) {
    stillsCache[fid] = src;
    if (lastCanvas) renderBoard(lastCanvas.board);
  }
  function showFinalVideo(url, rid) {
    $("render-panel").hidden = false;
    const v = $("render-video"), dl = $("render-dl");
    if (v.src !== location.origin + url) v.src = url;
    v.hidden = false;
    dl.href = "/download/" + (rid || url.split("/").pop()); dl.hidden = false;
    // Never report a clean 'done ✓' over a silent reel (the Suno-credits incident).
    const warn = lastCanvas && lastCanvas.audio_warning;
    const st = $("render-status");
    st.textContent = warn ? "done — ⚠ NO AUDIO" : "done ✓";
    st.style.color = warn ? "#dc2626" : "";
    st.style.fontWeight = warn ? "700" : "";
  }

  // Rebuild the reveal from disk (survives reloads); reconnect SSE if still running.
  async function syncRendered() {
    if (!runId) return;
    try {
      const d = await api(`/api/canvas/${runId}/rendered`, {});
      if (d.canvas) lastCanvas = d.canvas;
      Object.entries(d.stills || {}).forEach(([fid, p]) => { stillsCache[fid] = p; });
      Object.entries(d.frames || {}).forEach(([fid, p]) => { clipsCache[fid] = p; });
      if (lastCanvas) renderBoard(lastCanvas.board);
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
  // Frame ids (f01…) repeat across runs, so per-run caches MUST reset on a run
  // switch or a resumed canvas would show the previous run's stills/clips.
  function resetRunCaches() {
    stillsCache = {}; clipsCache = {}; assetCache = null;
    activeFrameId = null; canvasMusicPath = "";
    cvFitted = false;               // a different run gets its own first-fit glide
    $("inspector").hidden = true;
    $("timeline-strip").innerHTML = "";
  }
  function setRun(id) {
    if (id !== runId) resetRunCaches();
    runId = id;
    try { localStorage.setItem("hob_canvas_run", id); } catch (e) { /* ignore */ }
    history.replaceState({}, "", "/canvas?run=" + id);
    $("saved").hidden = false;
    if ($("del-btn")) $("del-btn").hidden = false;
    if ($("edit-story-btn")) $("edit-story-btn").hidden = false;
  }
  function clearRun() {
    runId = null;
    resetRunCaches();
    try { localStorage.removeItem("hob_canvas_run"); } catch (e) { /* ignore */ }
    history.replaceState({}, "", "/canvas");
    $("saved").hidden = true;
    if ($("del-btn")) $("del-btn").hidden = true;
    if ($("edit-story-btn")) $("edit-story-btn").hidden = true;
    $("plan-btn").textContent = "Plan ✨";      // fresh story → "Plan", not "Re-plan"
  }
  function applyCanvas(canvas) {
    if (canvas.brief !== undefined) $("brief").value = canvas.brief || "";
    if (canvas.scope) $("scope").value = canvas.scope;
    if (canvas.quality) $("quality").value = canvas.quality;
    if (canvas.target_seconds !== undefined) $("length").value = String(canvas.target_seconds);
    if (canvas.story_type) $("story-type").value = canvas.story_type;
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
  function _ago(ts) {
    if (!ts) return "";
    const s = Math.max(0, Math.floor(Date.now() / 1000 - ts));
    if (s < 3600) return Math.floor(s / 60) + "m ago";
    if (s < 86400) return Math.floor(s / 3600) + "h ago";
    return Math.floor(s / 86400) + "d ago";
  }
  async function loadRecents() {
    try {
      const d = await api("/api/canvas/list");
      const list = d.canvases || [];
      // Distinguishable labels — same brief across sessions is disambiguated by
      // time + shot count + mode (they're separate runs, not real duplicates).
      const meta = (c) => [_ago(c.updated_at), c.shots ? c.shots + " shots" : "",
        c.story_type === "ai" ? "🎭 AI" : "📷 Real"].filter(Boolean).join(" · ");
      $("resume").innerHTML = '<option value="">Resume…</option>' +
        list.map((c) => `<option value="${c.run_id}">${escapeHtml(c.title)}  — ${escapeHtml(meta(c))}</option>`).join("");
      // Same data as cards on the prompt page: a new session opens on an empty box,
      // and picking up yesterday's story shouldn't mean hunting through a dropdown.
      const el = $("entry-recents");
      if (!el) return;
      el.hidden = !list.length;
      el.innerHTML = !list.length ? "" : "<h3>Recent</h3><div class=\"cv-recent-grid\">" +
        list.slice(0, 6).map((c) => `<button type="button" class="cv-recent" data-run="${c.run_id}">` +
          `<b>${escapeHtml(c.title)}</b><span>${escapeHtml(meta(c))}</span></button>`).join("") + "</div>";
    } catch (e) { /* non-fatal */ }
  }
  $("entry-recents") && $("entry-recents").addEventListener("click", (ev) => {
    const card = ev.target.closest("[data-run]");
    if (card) loadCanvas(card.dataset.run);
  });
  // Delete the open canvas (clean up old/duplicate sessions).
  $("del-btn") && $("del-btn").addEventListener("click", async () => {
    if (!runId) return;
    if (!confirm("Delete this canvas? This can't be undone.")) return;
    try {
      await api(`/api/canvas/${runId}/delete`, {});
      try { localStorage.removeItem("hob_canvas_run"); } catch (e) { /* ignore */ }
      location.href = "/canvas";   // clear the board + reload the (now-pruned) picker
    } catch (e) { err(e.message); }
  });

  // ── Events ─────────────────────────────────────────────────────────────────
  $("plan-btn").addEventListener("click", async () => {
    err("");
    const brief = $("brief").value.trim();
    if (!brief) { err("Enter a brief first."); return; }
    const payload = {
      brief, scope: $("scope").value, quality: $("quality").value,
      target_seconds: parseInt($("length").value, 10) || 0,
      story_type: $("story-type").value,
    };
    // Re-plan in place when a run is already open (Edit story) and the story changed —
    // but only after warning that a new shot list drops generated stills for shots that
    // no longer exist. A brand-new story (no run) just plans.
    const replan = !!runId && lastCanvas && brief !== (lastCanvas.brief || "").trim();
    if (replan && !confirm("Re-planning rebuilds the shot list from your edited story. Generated images for shots that change or disappear will be dropped. Continue?")) {
      return;
    }
    $("plan-btn").disabled = true; $("plan-btn").textContent = replan ? "Re-planning…" : "Planning…";
    try {
      const d = replan
        ? await api(`/api/canvas/${runId}/replan`, payload)
        : await api("/api/canvas/plan", payload);
      setRun(d.run_id);
      // Push the operator's PRE-PLAN settings (captions/orientation/watermark) onto the
      // new canvas FIRST — they were silently dropped before (no run existed to save to),
      // so e.g. "position: Middle" chosen before Plan never reached the render.
      _settingsReady = true;
      await saveSettings();                     // renders the returned canvas itself
      if (!lastCanvas) render(d.canvas);        // settings save failed → still show the board
      loadRecents();
    } catch (e) { err(e.message); }
    finally { $("plan-btn").disabled = false; $("plan-btn").textContent = runId ? "Re-plan ✨" : "Plan ✨"; }
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
    $("board").innerHTML = "";
    $("legend").hidden = true; $("chat").hidden = true; $("assets").hidden = true;
    $("render-btn").hidden = true; $("render-panel").hidden = true;
    $("render-video").hidden = true; $("render-dl").hidden = true; $("render-log").textContent = "";
    lastCanvas = null;
    setView(false);          // back to the prompt
    loadRecents();
    err("");
    $("brief").focus();
  });

  // ✎ Edit story — the way BACK to the prompt (S31). Unlike ＋ New (which wipes and
  // starts fresh), this keeps the run open and the brief editable, so you can change the
  // story and re-plan the SAME canvas. The board rebuilds on re-plan.
  $("edit-story-btn") && $("edit-story-btn").addEventListener("click", () => {
    if (lastCanvas) $("brief").value = lastCanvas.brief || $("brief").value;
    $("plan-btn").textContent = "Re-plan ✨";
    setView(false);
    err("");
    $("brief").focus();
  });

  // Resume a saved canvas from the picker.
  $("resume").addEventListener("change", (ev) => {
    if (ev.target.value) loadCanvas(ev.target.value);
  });

  // On open: list recents + voices. Resume ONLY when the URL names a run
  // (?run=) — a bare /canvas means "new story" (prompt-first, S31); silently
  // teleporting into the last session made starting fresh feel impossible.
  // Closed-tab recovery lives one click away in the Resume… picker.
  loadRecents();
  loadVoices();
  (function initResume() {
    const fromUrl = new URLSearchParams(location.search).get("run");
    if (fromUrl) loadCanvas(fromUrl);
  })();

  // Re-roll one shot — regenerate its still + clip (~1–2 min; reuses the pipeline).
  async function rerollShot(fid, btn) {
    if (!runId) return;
    err("");
    const frame = document.querySelector(`.sb-card[data-frame="${fid}"]`);
    if (frame) frame.classList.add("shimmer");
    const oldLabel = btn.textContent;
    btn.disabled = true; btn.textContent = "…";
    try {
      const d = await api(`/api/canvas/${runId}/reroll`, { frame_id: fid });
      if (d.clip_path) {
        const u = mediaUrl(d.clip_path);
        showClipOnCard(fid, u + (u.includes("?") ? "&" : "?") + "t=" + Date.now());
      }
    } catch (e) { err("Re-roll failed: " + e.message); }
    finally {
      if (frame) frame.classList.remove("shimmer");
      btn.disabled = false; btn.textContent = oldLabel;
    }
  }

  // T5: still-only regeneration from the (edited) Image Prompt — review-first, image cost.
  async function restillShot(fid, btn) {
    if (!runId) return;
    err("");
    const cell = document.querySelector(`.sb-card[data-frame="${fid}"]`);
    if (cell) cell.classList.add("shimmer");
    const old = btn.textContent;
    btn.disabled = true; btn.textContent = "…";
    try {
      const d = await api(`/api/canvas/${runId}/restill`, { frame_id: fid });
      if (d.still_path) {
        const u = mediaUrl(d.still_path);
        showStillOnCard(fid, u + (u.includes("?") ? "&" : "?") + "t=" + Date.now());
        $("match-hint").textContent = "🖼 new key frame — happy? ↻ Re-roll animates it";
      }
    } catch (e) { err("New still: " + e.message); }
    finally {
      if (cell) cell.classList.remove("shimmer");
      btn.disabled = false; btn.textContent = old;
    }
  }

  // T5: restore a previous take as the live clip (free; current clip is archived too).
  async function restoreTake(fid, path) {
    if (!runId) return;
    err("");
    try {
      const d = await api(`/api/canvas/${runId}/take-restore`, { frame_id: fid, path });
      if (d.clip_path) {
        const u = mediaUrl(d.clip_path);
        showClipOnCard(fid, u + (u.includes("?") ? "&" : "?") + "t=" + Date.now());
        $("match-hint").textContent = "↩ take restored — the replaced clip is saved as a take too";
        renderInspector(fid);   // refresh the takes strip
      }
    } catch (e) { err("Restore: " + e.message); }
  }

  // Optional ambient re-create — opt-in per shot (non-person only).
  async function recreateShot(fid, btn) {
    if (!runId) return;
    err("");
    const frame = document.querySelector(`.sb-card[data-frame="${fid}"]`);
    if (frame) frame.classList.add("shimmer");
    const oldLabel = btn.textContent;
    btn.disabled = true; btn.textContent = "…";
    try {
      const d = await api(`/api/canvas/${runId}/recreate`, { frame_id: fid });
      render(d.canvas);
    } catch (e) { err("Re-create: " + e.message); }
    finally {
      if (frame) frame.classList.remove("shimmer");
      btn.disabled = false; btn.textContent = oldLabel;
    }
  }

  // Delegated re-roll / re-create / select / picker clicks.
  function handleBoardOrInspectorClick(ev) {
    const cell = ev.target.closest(".sb-card, .lane-cell");
    if (cell && !ev.target.closest("button") && !ev.target.closest("input") && !ev.target.closest("select") && !ev.target.closest(".picker")) {
      const fid = cell.getAttribute("data-frame");
      selectFrame(fid);
      return;
    }
    const rb = ev.target.closest(".reroll");
    if (rb) { ev.preventDefault(); rerollShot(rb.getAttribute("data-frame"), rb); return; }
    const rs = ev.target.closest(".restill");
    if (rs) { ev.preventDefault(); restillShot(rs.getAttribute("data-frame"), rs); return; }
    const tk = ev.target.closest(".take-chip");
    if (tk) {
      ev.preventDefault();
      restoreTake(tk.getAttribute("data-frame"), decodeURIComponent(tk.getAttribute("data-path")));
      return;
    }
    const rc = ev.target.closest(".recreate");
    if (rc) { ev.preventDefault(); recreateShot(rc.getAttribute("data-frame"), rc); return; }
    const mb = ev.target.closest(".more-btn");
    if (mb) {
      ev.preventDefault();
      const more = mb.closest(".source").querySelector(".src-more");
      document.querySelectorAll(".src-more").forEach((m) => { if (m !== more) m.hidden = true; });
      if (more) more.hidden = !more.hidden;
      return;
    }
    const pb = ev.target.closest(".pick-btn");
    if (pb) { ev.preventDefault(); openPicker(pb.getAttribute("data-frame")); return; }
    const rm = ev.target.closest(".rematch-btn");
    if (rm) { ev.preventDefault(); rematchShot(rm.getAttribute("data-frame"), rm); return; }
    const pk = ev.target.closest(".pk");
    if (pk) {
      ev.preventDefault();
      const fid = pk.getAttribute("data-frame");
      assignReal(fid, decodeURIComponent(pk.getAttribute("data-path")));
      const g = document.querySelector(".picker"); if (g) g.remove();
      return;
    }
    const up = ev.target.closest(".upscale");
    if (up) { ev.preventDefault(); upscaleShot(up.getAttribute("data-frame"), up); return; }
    const ag = ev.target.closest(".ai-gen");
    if (ag) { ev.preventDefault(); aiGeneric(ag.getAttribute("data-frame"), ag); return; }
    const lf = ev.target.closest(".locked-face");
    if (lf) { ev.preventDefault(); useLockedFace(lf.getAttribute("data-frame"), lf); return; }
    const rr = ev.target.closest(".revert-real");
    if (rr) { ev.preventDefault(); revertReal(rr.getAttribute("data-frame")); return; }
    const fs = ev.target.closest(".fid-suggest");
    if (fs) { ev.preventDefault(); setFidelity(fs.getAttribute("data-frame"), fs.getAttribute("data-rung")); return; }
    // T14 Frame Composer controls
    const oa = ev.target.closest(".ov-add");
    if (oa) {
      ev.preventDefault();
      const fid2 = oa.getAttribute("data-frame");
      const card = (lastCanvas.board || []).find((x) => x.frame_id === fid2) || {};
      ovSave(fid2, [...(card.overlays || []), ovFromForm()]).catch((e) => err(e.message));
      return;
    }
    const ox = ev.target.closest(".ov-x");
    if (ox) {
      ev.preventDefault();
      const fid2 = ox.getAttribute("data-frame");
      const idx = parseInt(ox.getAttribute("data-i"), 10);
      const card = (lastCanvas.board || []).find((x) => x.frame_id === fid2) || {};
      const ovs = (card.overlays || []).filter((_, i) => i !== idx);
      ovSave(fid2, ovs).catch((e) => err(e.message));
      return;
    }
    const op = ev.target.closest(".ov-preview");
    if (op) {
      ev.preventDefault();
      const fid2 = op.getAttribute("data-frame");
      const card = (lastCanvas.board || []).find((x) => x.frame_id === fid2) || {};
      op.disabled = true;
      api(`/api/canvas/${runId}/overlay-preview`,
          { frame_id: fid2, overlays: [...(card.overlays || []), ovFromForm()] })
        .then((d) => {
          const img = $("ov-preview-img");
          if (img) { img.src = d.preview + "&t=" + Date.now(); img.hidden = false; }
        })
        .catch((e) => err(e.message))
        .finally(() => { op.disabled = false; });
      return;
    }
  }

  $("board").addEventListener("click", handleBoardOrInspectorClick);
  $("inspector").addEventListener("click", handleBoardOrInspectorClick);
  // S33.2: each stage lane routes through the same handler — same selection rules.
  ["lane-sketch", "lane-frames", "lane-video", "lane-audio"].forEach((id) => {
    if ($(id)) $(id).addEventListener("click", handleBoardOrInspectorClick);
  });

  $("timeline-strip").addEventListener("click", (ev) => {
    const card = ev.target.closest(".cv2-timeline-card");
    if (card) {
      const fid = card.getAttribute("data-frame");
      selectFrame(fid);
    }
  });

  // Video hover virtualization
  document.body.addEventListener("mouseover", (ev) => {
    const poster = ev.target.closest(".video-poster");
    if (poster) {
      const clipPath = poster.getAttribute("data-clip");
      const stillPath = poster.getAttribute("data-still") || "";
      if (poster.querySelector("video")) return; // already active
      poster.innerHTML = `<video class="clip" src="${mediaUrl(clipPath)}" autoplay loop muted playsinline style="width:100%;height:100%;object-fit:cover"></video>`;
    }
  });

  document.body.addEventListener("mouseout", (ev) => {
    const poster = ev.target.closest(".video-poster");
    if (poster) {
      const related = ev.relatedTarget;
      if (!related || !poster.contains(related)) {
        const stillPath = poster.getAttribute("data-still") || "";
        poster.innerHTML = `${mediaThumb(stillPath, "video poster")}<span class="play-icon">▶</span>`;
      }
    }
  });

  // Delegated change handler: Fidelity selector, image upload, text input.
  function handleBoardOrInspectorChange(ev) {
    const fidSel = ev.target.closest(".fid-sel");
    if (fidSel) { setFidelity(fidSel.getAttribute("data-frame"), fidSel.value, fidSel); return; }
    const fileInput = ev.target.closest('input[type="file"]');
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
  }

  $("board").addEventListener("change", handleBoardOrInspectorChange);
  $("inspector").addEventListener("change", handleBoardOrInspectorChange);

  // T14: one-click speaker chips across every dialogue shot.
  $("chips-btn") && $("chips-btn").addEventListener("click", async () => {
    if (!runId) { err("Plan a story first."); return; }
    const btn = $("chips-btn");
    const enable = btn.dataset.on !== "1";
    err(""); btn.disabled = true;
    try {
      const d = await api(`/api/canvas/${runId}/speaker-chips`, { enabled: enable });
      btn.dataset.on = enable ? "1" : "";
      btn.textContent = `🗣 Speaker chips: ${enable ? "on" : "off"}`;
      $("match-hint").textContent = enable
        ? `🗣 chips on ${d.chipped} dialogue shot(s) — lock cast faces first if 0`
        : "🗣 speaker chips removed";
      render(d.canvas);
    } catch (e) { err(e.message); }
    finally { btn.disabled = false; }
  });

  // Auto-match a whole folder of the operator's real photos/videos to the shots
  // (the moat: real media, not synthetic portraits of a real person).
  // ── Brand / Ad (S31 pre-flight #4) ────────────────────────────────────────
  // Filling this flips the run to brand mode server-side (web_app._canvas_mode), which
  // arms brand.validate_mandatories on every PAID stage and the sponsored-disclosure
  // burn. Copy is sent verbatim and never generated (BRAND_PLAN §5).
  $("brand-logo") && $("brand-logo").addEventListener("change", async (ev) => {
    const f = ev.target.files && ev.target.files[0];
    if (!f || !runId) return;
    try {
      const fd = new FormData();
      fd.append("session_id", runId);
      fd.append("files", f, f.name);
      const r = await fetch("/upload-folder", { method: "POST", body: fd });
      const j = await r.json();
      if (j.error) throw new Error(j.error);
      brandLogoPath = j.assets_dir + "/" + f.name;
      $("brand-logo-name").textContent = f.name;
    } catch (e) { err("Logo upload failed: " + e.message); }
    finally { ev.target.value = ""; }
  });
  $("brand-save") && $("brand-save").addEventListener("click", async () => {
    if (!runId) { err("Plan a story first."); return; }
    err("");
    try {
      const d = await api(`/api/canvas/${runId}/brand`, {
        brand: {
          name: $("brand-name").value.trim(),
          cta_text: $("brand-cta").value.trim(),
          logo_path: brandLogoPath,
          disclosure: $("brand-disclosure").checked,
        },
      });
      // Show what's still missing BEFORE any spend — that's the whole point of the gate.
      $("brand-hint").innerHTML = d.mode !== "brand"
        ? "story mode — no brand mandatories enforced"
        : (d.missing || []).length
          ? `⚠ still missing: ${(d.missing || []).map(escapeHtml).join(" · ")}`
          : "✓ brand mandatories complete — ad is cleared to render";
      render(d.canvas);
    } catch (e) { err(e.message); }
  });

  // ── Publish: posting kit / export / provenance / performance (S31 #2, #3) ──
  // These routes are run_id-keyed and already worked for a canvas render_id; only the
  // Story door ever surfaced them.
  $("kit-btn") && $("kit-btn").addEventListener("click", async () => {
    if (!runId) return;
    err("");
    try {
      const d = await api(`/api/canvas/${runId}/posting-kit`, {});
      $("kit-out").hidden = false;
      $("kit-out").innerHTML =
        `<textarea class="edit" rows="3" readonly>${escapeHtml(d.caption || "")}</textarea>` +
        `<div class="muted" style="font-size:11px;margin-top:4px">${escapeHtml((d.hashtags || []).join(" "))}</div>`;
    } catch (e) { err(e.message); }
  });
  $("perf-save") && $("perf-save").addEventListener("click", async () => {
    const rid = lastCanvas && lastCanvas.render_id;
    if (!rid) { err("Render the reel first."); return; }
    err("");
    try {
      await api(`/performance/${rid}`, {
        views: parseInt($("perf-views").value, 10) || 0,
        likes: parseInt($("perf-likes").value, 10) || 0,
      });
      $("publish-hint").textContent = "✓ logged — feeds the performance history";
    } catch (e) { err(e.message); }
  });

  // ── Real photos from the browser (S31 pre-flight #1) ───────────────────────
  // THE parity blocker: canvas only accepted a SERVER-side path, and a hosted server
  // cannot see the user's disk — so the moat workflow (real photos → untouched
  // passthrough) was Story-door-only in production. Same /upload-folder route the Story
  // door already used; batched because one big folder in a single request gets rejected.
  const _MEDIA_RE = /\.(jpe?g|png|webp|heic|mp4|mov|m4v|webm)$/i;
  const _BATCH_BYTES = 40 * 1024 * 1024;

  async function uploadMedia(fileList) {
    const media = Array.from(fileList || []).filter((f) => _MEDIA_RE.test(f.name));
    if (!media.length) { err("No images or videos found there."); return; }
    if (!runId) { err("Plan a story first."); return; }
    err("");
    const batches = [];
    let cur = [], bytes = 0;
    for (const f of media) {
      if (cur.length && bytes + f.size > _BATCH_BYTES) { batches.push(cur); cur = []; bytes = 0; }
      cur.push(f); bytes += f.size;
    }
    if (cur.length) batches.push(cur);

    let done = 0, dir = "";
    $("upload-hint").textContent = `uploading 0/${media.length}…`;
    try {
      for (const batch of batches) {
        const fd = new FormData();
        fd.append("session_id", runId);        // land in THIS canvas's assets dir
        batch.forEach((f) => fd.append("files", f, f.name));
        const r = await fetch("/upload-folder", { method: "POST", body: fd });
        if (!r.ok) throw new Error(`server returned ${r.status} (file too large or rejected)`);
        const j = await r.json();
        if (j.error) throw new Error(j.error);
        dir = j.assets_dir;
        done += batch.length;
        $("upload-hint").textContent = `uploading ${done}/${media.length}…`;
      }
      $("assets-folder").value = dir;
      $("upload-hint").textContent = `✓ ${done} uploaded — now match them to your shots`;
    } catch (e) {
      $("upload-hint").textContent = "";
      err("Upload failed: " + e.message);
    }
  }
  $("photos-input") && $("photos-input").addEventListener("change", (ev) => {
    uploadMedia(ev.target.files).finally(() => { ev.target.value = ""; });
  });
  $("photofiles-input") && $("photofiles-input").addEventListener("change", (ev) => {
    uploadMedia(ev.target.files).finally(() => { ev.target.value = ""; });
  });

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
  // 🔎 Review shots — one pass: quality suggestions (⚡ Fidelity chips) + content-fit
  // flags (⚠️). Merges the old "Suggest fidelity" + "Check matches". (Per-shot Restore
  // lives in each card's Fidelity dropdown — no separate bulk Enhance button.)
  let checkPoll = null;
  $("review-btn") && $("review-btn").addEventListener("click", async () => {
    if (!runId) { err("Plan a story first."); return; }
    err(""); const btn = $("review-btn"); btn.disabled = true;
    $("match-hint").textContent = "🔎 reviewing shots…";
    try {
      const d = await api(`/api/canvas/${runId}/fidelity-suggest`, {});   // quality (fast)
      render(d.canvas);
      try { await api(`/api/canvas/${runId}/check-matches`, {}); }         // content-fit (threaded)
      catch (e) { btn.disabled = false; $("match-hint").textContent = "🔎 quality reviewed"; return; }
      if (checkPoll) clearInterval(checkPoll);
      checkPoll = setInterval(async () => {
        try {
          const c = (await api(`/api/canvas/${runId}/state`)).canvas;
          const flagged = (c.board || []).filter((x) => x.match_flag).length;
          const improve = (c.board || []).filter((x) => x.fidelity_suggested && x.fidelity_suggested !== "passthrough").length;
          $("match-hint").textContent = c.checking
            ? `🔎 reviewing ${c.check_done}/${c.check_total}…`
            : `🔎 reviewed — ${flagged} may not fit ⚠️, ${improve} could improve ⚡`;
          if (!c.checking) { clearInterval(checkPoll); checkPoll = null; btn.disabled = false; render(c); }
        } catch (e) { /* keep polling */ }
      }, 2500);
    } catch (e) { err(e.message); btn.disabled = false; }
  });

  // ── T13 Language versions ────────────────────────────────────────────────────
  // Author once → translate → MANDATORY review in the script panel → render the
  // version (same clips/music via cache; new captions + VO). Master captions never change.
  let langReview = null;   // { lang, native } while the review panel is open
  (async function loadLanguages() {
    try {
      const d = await fetch("/languages").then((r) => r.json());
      const sel = $("lang-pick"); if (!sel) return;
      (d.languages || []).filter((l) => l.code !== "en").forEach((l) => {
        const o = document.createElement("option");
        o.value = l.code; o.textContent = `${l.native} (${l.english})`;
        sel.appendChild(o);
      });
    } catch (e) { /* picker stays empty — button will error politely */ }
  })();

  $("lang-translate") && $("lang-translate").addEventListener("click", async () => {
    if (!runId || !lastCanvas) { err("Plan a story first."); return; }
    const lang = $("lang-pick").value;
    if (!lang) { err("Pick a language."); return; }
    const btn = $("lang-translate");
    err(""); btn.disabled = true; btn.textContent = "🌐 Translating…";
    try {
      const d = await api(`/api/canvas/${runId}/translate`, { language: lang });
      lastCanvas = d.canvas;
      openLangReview(lang, d.lines);
    } catch (e) { err(e.message); }
    finally { btn.disabled = false; btn.textContent = "🌐 Translate & review"; }
  });

  function openLangReview(lang, lines) {
    const native = ($("lang-pick").selectedOptions[0] || {}).textContent || lang;
    langReview = { lang, native };
    const board = (lastCanvas && lastCanvas.board) || [];
    $("script-panel").innerHTML =
      `<div class="hdr">🌐 <b>${escapeHtml(native)}</b> captions — machine-translated, <b>review every line</b>
        (names & honorifics especially). Edits save automatically and never touch the original captions.
        <button id="lang-render" class="btn-primary" style="margin-left:10px;padding:6px 14px">🎬 Render ${escapeHtml(native)} version</button>
        <button id="lang-cancel" class="btn-secondary" style="margin-left:6px;padding:6px 10px">Back</button></div>`
      + board.map((c, i) => `
        <div class="ln">
          <span class="n">${i + 1}</span>
          <span class="meta" title="original">${escapeHtml((c.caption || "").slice(0, 34))}</span>
          <textarea class="lang-line" data-lang="${lang}" data-frame="${c.frame_id}" rows="1">${escapeHtml(lines[c.frame_id] || "")}</textarea>
        </div>`).join("");
    $("script-panel").querySelectorAll("textarea").forEach(_autosize);
    cvLayout();               // the review adds rows — re-measure before gliding
    cvGlideTo("zone-script"); // S33: the panel is permanent; the camera goes to it
  }

  function closeLangReview() {
    langReview = null;
    renderScript((lastCanvas && lastCanvas.board) || []);
    cvLayout();
  }

  $("script-panel").addEventListener("click", async (ev) => {
    if (ev.target.closest("#lang-cancel")) { closeLangReview(); return; }
    const rb = ev.target.closest("#lang-render");
    if (rb && langReview) {
      err(""); rb.disabled = true; rb.textContent = "🎬 Rendering…";
      try {
        const d = await api(`/api/canvas/${runId}/render-language`,
          { language: langReview.lang, ...audioOpts() });
        closeLangReview();
        render(d.canvas);     // render_id now tracks the language version
        $("match-hint").textContent = `🌐 rendering the ${langReview ? langReview.native : ""} version — clips & music reused from cache`;
      } catch (e) { err(e.message); rb.disabled = false; rb.textContent = "🎬 Render version"; }
    }
  });

  // 📄 Script zone (S33) — the film line by line, ALWAYS on the canvas (zone 01).
  // Review-first: the captions ARE the script; edits save straight to the shots.
  function _autosize(t) { t.style.height = "auto"; t.style.height = t.scrollHeight + "px"; }
  function renderScript(board) {
    $("script-panel").innerHTML =
      `<div class="hdr">📄 Your story, shot by shot — read it top to bottom and edit any line.
        This is free; nothing generates until you Generate a stage.</div>`
      + (board || []).map((c, i) => `
        <div class="ln">
          <span class="n">${i + 1}</span>
          <span class="meta">${escapeHtml(c.shot_size || "")}</span>
          <textarea class="script-line" data-frame="${c.frame_id}" data-field="caption" rows="1"
            placeholder="(no line — visual-only beat)">${escapeHtml(c.caption || "")}</textarea>
        </div>`).join("");
    $("script-panel").querySelectorAll("textarea").forEach(_autosize);
  }
  $("script-panel").addEventListener("input", (ev) => {
    const t = ev.target.closest(".script-line"); if (t) _autosize(t);
  });
  $("script-panel").addEventListener("change", (ev) => {
    const lt = ev.target.closest(".lang-line");
    if (lt) {   // T13 review edit — saves the TRANSLATION, never the master caption
      api(`/api/canvas/${runId}/translation`, {
        language: lt.getAttribute("data-lang"),
        frame_id: lt.getAttribute("data-frame"),
        text: lt.value,
      }).catch((e) => err(e.message));
      return;
    }
    const t = ev.target.closest(".script-line"); if (t) saveField(t);   // → /frame {caption}
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
  // The sketches now live permanently in the board's Storyboard column, so this
  // button just fills in any missing panels (no separate view mode to toggle).
  $("storyboard-btn") && $("storyboard-btn").addEventListener("click", async () => {
    if (!runId) { err("Plan a story first."); return; }
    err("");
    if (!lastCanvas || (lastCanvas.board || []).every((c) => c.storyboard_art)) {
      $("match-hint").textContent = "✏️ storyboard panels ready — see the Sketches lane";
      return;
    }
    const btn = $("storyboard-btn"); btn.disabled = true;
    try {
      const d = await api(`/api/canvas/${runId}/storyboard-art`, {});
      $("match-hint").textContent = `✏️ sketching 0/${d.total}…`;
      pollSketch();
    } catch (e) { err(e.message); btn.disabled = false; }
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
    // Narrator is voice-only — never needs (or should spend on) a locked face.
    const unlocked = chars.filter((c) => !c.ref_path && c.id !== "narrator").length;
    const bulk = unlocked
      ? `<button class="char-gen-all char-save" style="margin-left:8px" title="Generate a canonical face for every character that doesn't have one yet — each shot then reuses that same face (consistency-first, like a character DNA sheet)">🎨 Generate all faces (${unlocked})</button>`
      : `<span class="hint" style="margin-left:8px">✓ all faces locked</span>`;
    el.innerHTML = `<h4>👥 Character sheet — attributes carry to every shot; a real photo keeps the face exact (consent needed for AI likeness)${bulk}</h4>`
      + chars.map((c) => `
        <div class="cv-char" data-char="${c.id}">
          <div class="char-head">
            <span class="nm">${escapeHtml(c.role || c.name || c.label || c.id)}</span>
            ${c.ref_path ? `<img class="char-photo" src="${mediaUrl(c.ref_path)}" alt="" title="Click to view full size">` : `<span class="muted">no photo</span>`}
            <button class="char-gen" data-char="${c.id}" title="${c.ref_path
              ? "Don't like this face? Generate a FRESH one from the attributes below (uses any edits you just typed — no need to Save first)"
              : "Generate a canonical face for this character from the attributes below — then every shot uses the same face"}">${c.ref_path ? "↻ New face" : "🎨 Generate"}</button>
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
            ${_cattr(c.id, "clothing", c.clothing, "outfit — locked across all shots")}
            ${_cattr(c.id, "species", c.species, "species/anatomy (e.g. vanara, monkey tail)")}
            <select class="char-voice cattr" data-char="${c.id}" data-attr="voice_id" title="This character's lines are read in their OWN voice (radio-drama dialogue); unset = narrator's voice">
              <option value="">🎙 narrator's voice</option>
              ${voicesCache.map((v) => `<option value="${v.voice_id}"${v.voice_id === c.voice_id ? " selected" : ""}>${escapeHtml(v.name || v.voice_id)}</option>`).join("")}
            </select>
            <button class="char-save" data-char="${c.id}">Save</button>
          </div>
        </div>`).join("");
  }
  // Bulk: generate a canonical face for EVERY character still missing one —
  // sequential (each call is spend-gated server-side), with visible progress.
  async function generateAllFaces(btn) {
    const missing = ((lastCanvas && lastCanvas.characters) || []).filter((c) => !c.ref_path && c.id !== "narrator");
    if (!missing.length) return;
    err(""); if (btn) btn.disabled = true;
    let done = 0, lastCanvasResp = null;
    for (const c of missing) {
      $("match-hint").textContent = `🎨 generating faces ${done}/${missing.length} — ${c.role || c.name || c.label || c.id}…`;
      try {
        const d = await api(`/api/canvas/${runId}/character-portrait`, { char_id: c.id });
        lastCanvasResp = d.canvas; done++;
      } catch (e) { err(`Face for ${c.label || c.id}: ${e.message}`); }
    }
    $("match-hint").textContent = `✓ ${done}/${missing.length} character faces locked — every shot reuses them`;
    if (lastCanvasResp) render(lastCanvasResp);
    if (btn) btn.disabled = false;
  }
  $("characters").addEventListener("click", (ev) => {
    const all = ev.target.closest(".char-gen-all");
    if (all) { generateAllFaces(all); }
  });

  // Generate (or REDO) a canonical face for a character. Sends the row's current
  // attribute inputs along (so just-typed edits count without a separate Save), and a
  // fresh cache variant when a face already exists (redo must not return the cached one).
  $("characters").addEventListener("click", async (ev) => {
    const photo = ev.target.closest("img.char-photo");
    if (photo) { window.open(photo.src, "_blank"); return; }   // expand: full size in a new tab
    const gen = ev.target.closest(".char-gen");
    if (!gen) return;
    const cid = gen.getAttribute("data-char");
    const row = gen.closest(".cv-char");
    const attrs = {};
    document.querySelectorAll(`.cattr[data-char="${cid}"]`).forEach((i) => {
      attrs[i.getAttribute("data-attr")] = i.value;
    });
    const isRedo = !!(row && row.querySelector("img.char-photo"));
    const oldLabel = gen.textContent;
    err(""); gen.disabled = true; gen.textContent = "…";
    try {
      const d = await api(`/api/canvas/${runId}/character-portrait`, {
        char_id: cid, attrs,
        variant: isRedo ? (1 + Math.floor(Math.random() * 1e6)) : 0,
      });
      renderCharacters(d.canvas.characters); render(d.canvas);
      $("match-hint").textContent = isRedo
        ? "↻ new face generated — click it to view full size; ↻ again for another"
        : "✓ character face generated — reused across their shots";
    } catch (e) { err("Generate: " + e.message); gen.disabled = false; gen.textContent = oldLabel; }
  });
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
      // Visible response: the sheet may already be open (auto-rendered) — clicking
      // Cast should still DO something. Scroll to it and flash it.
      const panel = $("characters");
      if (!panel.hidden) {
        panel.scrollIntoView({ behavior: "smooth", block: "center" });
        panel.classList.add("flash");
        setTimeout(() => panel.classList.remove("flash"), 1200);
      }
    } catch (e) { err(e.message); }
  });
  $("characters").addEventListener("change", async (ev) => {
    const vsel = ev.target.closest("select.char-voice");
    if (vsel) {   // T4: assign this character their own narration voice
      try {
        const d = await api(`/api/canvas/${runId}/character`,
          { char_id: vsel.getAttribute("data-char"), attrs: { voice_id: vsel.value } });
        renderCharacters(d.canvas.characters);
        $("match-hint").textContent = vsel.value
          ? "✓ voice assigned — this character's lines are read in it"
          : "voice cleared — lines fall back to the narrator";
      } catch (e) { err(e.message); }
      return;
    }
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

  // ── S30 Phase 1: Locations sheet — the "character sheet for places". Mirrors the
  // cast sheet: attrs carry to every shot set there (invariant clause), the generated
  // PLATE (empty set — no people, space for characters) locks the look of the place.
  function _lattr(lid, attr, val, ph, w) {
    return `<input class="lattr cattr" data-loc="${lid}" data-attr="${attr}" placeholder="${ph}"
              value="${escapeHtml(val || "")}"${w ? ` style="width:${w}px"` : ""}>`;
  }
  function renderLocations(locs) {
    const el = $("locations");
    if (!locs || !locs.length) { el.hidden = true; return; }
    el.hidden = false;
    el.innerHTML = `<h4>🏞 Locations — the same place stays the SAME place across shots; a plate locks its look</h4>`
      + locs.map((l) => `
        <div class="cv-char" data-loc="${l.id}">
          <div class="char-head">
            <span class="nm">${escapeHtml(l.label || l.id)}</span>
            ${l.plate_path ? `<img class="loc-plate" src="${mediaUrl(l.plate_path)}" alt="" title="Click to view full size">` : `<span class="muted">no plate</span>`}
            <button class="loc-gen" data-loc="${l.id}" title="${l.plate_path
              ? "Generate a FRESH plate from the fields below (uses any edits you just typed)"
              : "Generate an EMPTY establishing plate of this place — no people, space left for your characters. Shots with NO character are then generated from this plate itself; character shots follow the location's wording (one reference image, and the face wins it — plate+face lands with D5)."}">${l.plate_path ? "↻ New plate" : "🏞 Generate plate"}</button>
            <button class="loc-save char-save" data-loc="${l.id}">Save</button>
          </div>
          <div class="char-attrs">
            ${_lattr(l.id, "label", l.label, "name (cave interior…)")}
            ${_lattr(l.id, "description", l.description, "the place itself — walls, props, light (never the people)", 300)}
            ${_lattr(l.id, "time_of_day", l.time_of_day, "time of day (dawn…)")}
          </div>
        </div>`).join("");
  }
  $("locations").addEventListener("click", async (ev) => {
    const plate = ev.target.closest("img.loc-plate");
    if (plate) { window.open(plate.src, "_blank"); return; }
    const gen = ev.target.closest(".loc-gen");
    const save = ev.target.closest(".loc-save");
    const btn = gen || save;
    if (!btn) return;
    const lid = btn.getAttribute("data-loc");
    const attrs = {};
    document.querySelectorAll(`.lattr[data-loc="${lid}"]`).forEach((i) => {
      attrs[i.getAttribute("data-attr")] = i.value;
    });
    err(""); btn.disabled = true;
    try {
      if (save) {
        const d = await api(`/api/canvas/${runId}/location`, { loc_id: lid, attrs });
        renderLocations(d.canvas.locations); render(d.canvas);
        $("match-hint").textContent = "✓ location saved — applies to its shots";
      } else {
        const row = gen.closest(".cv-char");
        const isRedo = !!(row && row.querySelector("img.loc-plate"));
        const oldLabel = gen.textContent; gen.textContent = "…";
        const d = await api(`/api/canvas/${runId}/location-plate`, {
          loc_id: lid, attrs,
          variant: isRedo ? (1 + Math.floor(Math.random() * 1e6)) : 0,
        });
        renderLocations(d.canvas.locations); render(d.canvas);
        $("match-hint").textContent = isRedo
          ? "↻ new plate generated — click it to view full size; ↻ again for another"
          : "✓ location plate locked — its shots hold this set";
        void oldLabel;
      }
    } catch (e) { err((save ? "Save: " : "Plate: ") + e.message); btn.disabled = false; if (gen) gen.textContent = "🏞 Generate plate"; }
  });
  $("locs-btn").addEventListener("click", async () => {
    if (!runId) { err("Plan a story first."); return; }
    err("");
    try {
      const d = await api(`/api/canvas/${runId}/locations`, {});
      renderLocations((d.canvas && d.canvas.locations) || d.locations || []);
      const panel = $("locations");
      if (!panel.hidden) {   // visible response, same as Cast (S10 lesson)
        panel.scrollIntoView({ behavior: "smooth", block: "center" });
        panel.classList.add("flash");
        setTimeout(() => panel.classList.remove("flash"), 1200);
      } else if (d.canvas && d.canvas.locations_warning) {
        // A FAILED derive used to render as a confident "no locations detected" — the
        // operator concluded their multi-location story had one setting and never
        // retried. Say what actually happened.
        $("match-hint").textContent = "";
        err(d.canvas.locations_warning);
      } else {
        $("match-hint").textContent = "no distinct locations detected for this story";
      }
    } catch (e) { err(e.message); }
  });

  // Character-level: attach a real photo of the person to every people-shot.
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

  async function handleStageClick(ev) {
    const gen = ev.target.closest(".gen[data-stage]");
    const appr = ev.target.closest(".appr[data-approve]");
    if (!runId || (!gen && !appr)) return;
    err("");
    try {
      if (gen) {
        const stage = gen.getAttribute("data-stage");
        // Asset-first gate, now enforced SERVER-side (409 when a character has no
        // locked face — the thing that made the same character drift every shot).
        // The confirm is the operator's explicit escape; OK sends force:true.
        let kfForce = false;
        if (stage === "keyframes") {
          const unlocked = (lastCanvas?.characters || []).filter((c) => !c.ref_path && c.id !== "narrator");
          if (unlocked.length) {
            const names = unlocked.map((c) => c.role || c.name || c.label || c.id).join(", ");
            if (!confirm(`⚠ ${unlocked.length} character(s) have no locked face yet (${names}).\n\nWithout one, their look WILL drift between shots.\n\nOK = generate anyway · Cancel = lock faces first (📎/🎨 in the Character sheet)`)) {
              renderCharacters(lastCanvas.characters);
              $("characters").scrollIntoView({ behavior: "smooth", block: "center" });
              return;
            }
            kfForce = true;
          }
        }
        gen.disabled = true; gen.textContent = "Working…";
        let d;
        if (stage === "keyframes") {
          d = await api(`/api/canvas/${runId}/keyframes`, kfForce ? { force: true } : {});  // cheap stills only
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
  }
  // Bound to BOTH: the buttons moved to the common bar (S31), and the board still
  // hosts inline .gen/.appr affordances. One handler, two hosts, zero duplicated rules.
  $("board").addEventListener("click", handleStageClick);
  $("stage-bar") && $("stage-bar").addEventListener("click", handleStageClick);

  // ══ The infinite canvas (S33) — one pannable, zoomable surface ══════════════
  // The five workspaces (Script → Cast → Locations → Storyboard → Output) are
  // absolutely-positioned zones on #cv-world, moved by ONE transform. Figma
  // grammar: drag pans, wheel pans, ⌘/ctrl+wheel zooms to the cursor, ⇧1 fits,
  // the jumplist glides to a workspace. Pipeline actions, cost and chat stay
  // docked — governance must never be pannable out of view.
  let cvX = 0, cvY = 0, cvK = 1;
  let cvFitted = false;   // the first board of a run auto-fits once, then hands over
  const cvClampK = (v) => Math.min(2.5, Math.max(0.12, v));

  function cvApply() {
    $("cv-world").style.transform = `translate(${cvX}px, ${cvY}px) scale(${cvK})`;
    if ($("zoom-pct")) $("zoom-pct").textContent = Math.round(cvK * 100) + "%";
  }
  function cvGlideOn() {
    const w = $("cv-world");
    w.classList.add("gliding");
    setTimeout(() => w.classList.remove("gliding"), 500);
  }

  // Deterministic layout pass — zones are content-sized (the board grows with the
  // film, the sheets with the cast), so measure and place on every render:
  // Script | Cast over Locations | Storyboard | Output, left→right; the world
  // then hugs the result. The CSS left/top values are only the pre-layout fallback.
  function cvLayout() {
    const script = $("zone-script"), cast = $("zone-cast"), loc = $("zone-loc"),
          out = $("zone-out");
    if (!script || $("canvas-view").hidden) return;   // hidden = zero sizes; skip
    const GAP = 90, TOP = 80, LEFT = 80;
    script.style.left = LEFT + "px"; script.style.top = TOP + "px";
    const castX = LEFT + script.offsetWidth + GAP;
    cast.style.left = castX + "px"; cast.style.top = TOP + "px";
    loc.style.left = castX + "px"; loc.style.top = (TOP + cast.offsetHeight + 56) + "px";
    const boardX = castX + Math.max(cast.offsetWidth, loc.offsetWidth) + GAP;
    // The film column (S33.2): Storyboard, then the stage lanes, stacked so every
    // shot's column lines up — breakdown → sketch → still → clip → sound.
    let y = TOP, filmW = 0;
    ["zone-board", "zone-sketch", "zone-frames", "zone-video", "zone-audio"].forEach((id) => {
      const z = $(id); if (!z) return;
      z.style.left = boardX + "px"; z.style.top = y + "px";
      y += z.offsetHeight + 56;
      filmW = Math.max(filmW, z.offsetWidth);
    });
    const outX = boardX + filmW + GAP;
    out.style.left = outX + "px"; out.style.top = TOP + "px";
    const w = $("cv-world");
    w.style.width = (outX + out.offsetWidth + GAP) + "px";
    w.style.height = (Math.max(TOP + script.offsetHeight,
                               TOP + cast.offsetHeight + 56 + loc.offsetHeight,
                               y - 56,
                               TOP + out.offsetHeight) + GAP) + "px";
  }

  function cvFitRect(x, y, w, h, pad) {
    const c = $("cv-canvas"), cw = c.clientWidth, ch = c.clientHeight;
    if (!cw || !ch || !w || !h) return;
    cvK = cvClampK(Math.min((cw - pad * 2) / w, (ch - pad * 2) / h));
    cvX = (cw - w * cvK) / 2 - x * cvK;
    cvY = (ch - h * cvK) / 2 - y * cvK;
    cvApply();
  }
  function cvFitAll() {
    const w = $("cv-world");
    cvGlideOn(); cvFitRect(0, 0, w.offsetWidth, w.offsetHeight, 28);
    cvMarkJump(null);
  }
  function cvGlideTo(zoneId) {
    const el = $(zoneId);
    if (!el || $("canvas-view").hidden) return;
    cvGlideOn();
    const c = $("cv-canvas"), cw = c.clientWidth, ch = c.clientHeight, pad = 46;
    const x = el.offsetLeft, y = el.offsetTop - 30, w = el.offsetWidth, h = el.offsetHeight + 30;
    const fitW = (cw - pad * 2) / w, fitH = (ch - pad * 2) / h;
    if (Math.min(fitW, fitH) < 0.5) {
      // Fitting the whole zone would shrink it to confetti (a 30-shot film, a long
      // script). Glide to its HEAD at a readable scale instead — wheel pans the rest.
      const k = cvClampK(Math.min(Math.max(fitW, fitH), 1));
      cvK = k;
      if (fitH >= fitW) {   // wide zone: pin its left edge, centre vertically
        cvX = pad - x * k; cvY = (ch - h * k) / 2 - y * k;
      } else {              // tall zone: pin its top edge, centre horizontally
        cvX = (cw - w * k) / 2 - x * k; cvY = pad - y * k;
      }
      cvApply();
    } else {
      cvFitRect(x, y, w, h, pad);
    }
    cvMarkJump(zoneId);
  }
  function cvMarkJump(zoneId) {
    document.querySelectorAll("#cv-jump button").forEach((b) =>
      b.classList.toggle("on", b.dataset.zone === zoneId));
  }
  function cvShowHint() {
    let seen = false;
    try { seen = !!localStorage.getItem("hob_cv_hint"); } catch (e) { /* ignore */ }
    if ($("cv-hint")) $("cv-hint").hidden = seen;
  }

  // Pan: drag anywhere that isn't a control or the inspector overlay. A drag that
  // actually moved must NOT also be a click (it would select whatever card the
  // pointer landed on), so the click that ends a pan is swallowed in capture phase.
  let cvDrag = null, cvMoved = false;
  const CV_NO_PAN = "input, textarea, select, button, summary, a, label, video, .cv2-right, .cv-nav-hint, .cv-dock";
  $("cv-canvas").addEventListener("pointerdown", (e) => {
    if (e.button !== 0 || e.target.closest(CV_NO_PAN)) return;
    cvDrag = { sx: e.clientX, sy: e.clientY, ox: cvX, oy: cvY };
    cvMoved = false;
    $("cv-canvas").classList.add("grabbing");
    $("cv-canvas").setPointerCapture(e.pointerId);
  });
  $("cv-canvas").addEventListener("pointermove", (e) => {
    if (!cvDrag) return;
    const dx = e.clientX - cvDrag.sx, dy = e.clientY - cvDrag.sy;
    if (Math.abs(dx) + Math.abs(dy) > 5) cvMoved = true;
    cvX = cvDrag.ox + dx; cvY = cvDrag.oy + dy; cvApply();
  });
  const cvEndDrag = () => { cvDrag = null; $("cv-canvas").classList.remove("grabbing"); };
  $("cv-canvas").addEventListener("pointerup", cvEndDrag);
  $("cv-canvas").addEventListener("pointercancel", cvEndDrag);
  $("cv-canvas").addEventListener("click", (e) => {
    if (cvMoved) { e.stopPropagation(); e.preventDefault(); cvMoved = false; }
  }, true);

  // Wheel pans · ⌘/ctrl+wheel (and trackpad pinch) zooms to the cursor — the
  // Figma rule. The inspector overlay keeps its own scroll.
  $("cv-canvas").addEventListener("wheel", (e) => {
    if (e.target.closest(".cv2-right")) return;
    e.preventDefault();
    if (e.ctrlKey || e.metaKey) {
      const r = $("cv-canvas").getBoundingClientRect();
      const mx = e.clientX - r.left, my = e.clientY - r.top;
      const k2 = cvClampK(cvK * Math.exp(-e.deltaY * 0.0022));
      cvX = mx - (mx - cvX) * (k2 / cvK); cvY = my - (my - cvY) * (k2 / cvK); cvK = k2;
    } else { cvX -= e.deltaX; cvY -= e.deltaY; }
    cvApply();
  }, { passive: false });

  function cvZoomBy(f) {
    const c = $("cv-canvas"), cx = c.clientWidth / 2, cy = c.clientHeight / 2;
    const k2 = cvClampK(cvK * f);
    cvX = cx - (cx - cvX) * (k2 / cvK); cvY = cy - (cy - cvY) * (k2 / cvK); cvK = k2;
    cvGlideOn(); cvApply();
  }
  $("zoom-in") && $("zoom-in").addEventListener("click", () => cvZoomBy(1.25));
  $("zoom-out") && $("zoom-out").addEventListener("click", () => cvZoomBy(0.8));
  $("zoom-fit") && $("zoom-fit").addEventListener("click", cvFitAll);
  $("cv-jump") && $("cv-jump").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-zone]");
    if (b) cvGlideTo(b.dataset.zone);
  });

  // The inspector overlay closes from its ✕ or Esc; ⇧1 fits the whole canvas.
  function closeInspector() {
    activeFrameId = null;
    $("inspector").hidden = true;
    document.querySelectorAll(".sb-card.active, .cv2-timeline-card.active, .lane-cell.active")
      .forEach((el) => el.classList.remove("active"));
  }
  $("insp-close") && $("insp-close").addEventListener("click", closeInspector);
  $("cv-hint-x") && $("cv-hint-x").addEventListener("click", () => {
    $("cv-hint").hidden = true;
    try { localStorage.setItem("hob_cv_hint", "1"); } catch (e) { /* ignore */ }
  });
  document.addEventListener("keydown", (e) => {
    const el = document.activeElement || {};
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName || "");
    if (e.key === "Escape") {
      if (!$("settings-drawer").hidden) { closeSettings(); return; }
      if (!$("inspector").hidden) closeInspector();
      return;
    }
    if (!typing && e.shiftKey && e.code === "Digit1" && !$("canvas-view").hidden) {
      e.preventDefault(); cvFitAll();
    }
  });

  // Settings drawer (gear) — the output settings live HERE statically (S33);
  // the gear only shows/hides the drawer (no more move-in/move-out).
  function openSettings() { $("settings-drawer").hidden = false; $("settings-backdrop").hidden = false; }
  function closeSettings() { $("settings-drawer").hidden = true; $("settings-backdrop").hidden = true; }
  $("settings-gear") && $("settings-gear").addEventListener("click", openSettings);
  $("settings-close") && $("settings-close").addEventListener("click", closeSettings);
  $("settings-backdrop") && $("settings-backdrop").addEventListener("click", closeSettings);


})();
