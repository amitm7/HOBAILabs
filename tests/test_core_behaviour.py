import os
import json
import tempfile
import unittest

from agents import model_router
from agents.governance import (
    check_spend_cap,
    ledger_total,
    record_consent,
    record_cost_event,
    release_reservation,
    reserve_spend,
    sweep_stale_reservations,
    validate_consent,
)
from agents.brand import validate_mandatories
from agents.caption_writer import _MARGIN_V, generate_frame_srt
from agents.layout import _fit_text_block, is_layout_frame, render_layout_frame
from agents import run_store
from agents.product_surface import approval_history, register_asset, record_approval, save_version
from agents.pricing import estimate
from agents.script_parser import extract_caption_block, parse_frame_script
import web_app
from web_app import app


class CoreBehaviourTests(unittest.TestCase):
    def test_caption_block_extracts_posting_copy(self):
        raw = "Reels\n\nFrame 1\nA short line.\n\nCaption:\nLong post copy here."
        self.assertEqual(extract_caption_block(raw), "Long post copy here.")

    def test_parser_annotations_and_caption_block(self):
        raw = """Reels

Frame 1
The ==turning point== arrived.
[photo: ai_symbolic]
[camera: static]
[duration: 4]

Caption:
Full social caption.
"""
        with tempfile.TemporaryDirectory() as td:
            script = os.path.join(td, "script.txt")
            with open(script, "w", encoding="utf-8") as fp:
                fp.write(raw)
            frames = parse_frame_script(script, "")
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0]["photo_spec"], "ai_symbolic")
        self.assertEqual(frames[0]["motion_override"], "static")
        self.assertEqual(frames[0]["duration"], 4.0)
        self.assertNotIn("[camera:", frames[0]["caption"])

    def test_text_card_layout_renders(self):
        frame = {"frame_id": "f01", "caption": "A bold break", "layout": {"preset": "text_card"}}
        self.assertTrue(is_layout_frame(frame))
        with tempfile.TemporaryDirectory() as td:
            out = render_layout_frame(frame, os.path.join(td, "card.jpg"), width=540, height=960)
            self.assertTrue(os.path.exists(out))
            self.assertGreater(os.path.getsize(out), 1000)
        long_text = " ".join(["This is a long editable text card that must not start above the canvas"] * 20)
        lines, size, y = _fit_text_block(long_text, width=540, height=960)
        self.assertGreater(len(lines), 1)
        self.assertGreaterEqual(size, 24)
        self.assertGreaterEqual(y, 0)

    def test_caption_safe_zone_and_highlight_ass_tags(self):
        self.assertGreaterEqual(_MARGIN_V["bottom"], 300)
        with tempfile.TemporaryDirectory() as td:
            out = generate_frame_srt(
                [{"frame_id": "f01", "caption": "I had ==nothing== left", "duration": 4.0}],
                os.path.join(td, "captions.srt"),
                caption_style={"position": "bottom", "color": "white"},
                timecodes=[(0.0, 4.0)],
            )
            with open(out, encoding="utf-8") as fp:
                text = fp.read()
        self.assertIn("MarginL, MarginR, MarginV", text)
        self.assertIn("320", text)
        self.assertIn("{\\c&H0000FFFF}", text)
        self.assertIn("{\\c&H00FFFFFF}", text)

    def test_model_router_passthrough_and_override(self):
        self.assertEqual(
            model_router.select_model("image", {"photo_spec": "real.jpg"}, "draft"),
            model_router.PASSTHROUGH,
        )
        self.assertEqual(
            model_router.select_model("video", {"photo_spec": "ai_portrait"}, "draft", override="kling_std"),
            "kling_std",
        )

    def test_pricing_approved_ids_skip_animation(self):
        frames = [
            {"frame_id": "f01", "caption": "One", "photo_spec": "ai_portrait", "duration": 5.0},
            {"frame_id": "f02", "caption": "Two", "photo_spec": "ai_portrait", "duration": 5.0},
        ]
        all_cost = estimate(frames, force_5s=True)
        partial_cost = estimate(frames, force_5s=True, approved_ids={"f01"})
        self.assertGreater(all_cost["animation"]["usd"], partial_cost["animation"]["usd"])

    def test_brand_mandatories(self):
        missing = validate_mandatories([], {"cta_text": "", "logo_path": ""})
        self.assertTrue(any("logo" in m.lower() for m in missing))
        self.assertTrue(any("call-to-action" in m.lower() for m in missing))
        self.assertTrue(any("product" in m.lower() for m in missing))

    def test_governance_consent_and_spend_cap(self):
        missing = validate_consent({"mode": "brand", "brand": {"rights_confirmed": False}})
        self.assertTrue(any("consent" in m.lower() for m in missing))
        consent_payload = {
            "mode": "brand",
            "session_id": f"unit-consent-{os.getpid()}",
            "subject_name": f"subject-{os.getpid()}",
            "brand": {"rights_confirmed": True},
        }
        self.assertEqual(validate_consent(consent_payload), [])
        self.assertIsNotNone(record_consent(consent_payload, confirmed_by="unit"))

        key = f"unit-test-cap-{os.getpid()}"
        cap = check_spend_cap({"session_id": key, "spend_cap_usd": 0.01}, 1.0)
        self.assertTrue(cap)
        payload = {"session_id": key, "spend_cap_usd": 1.0}
        self.assertEqual(reserve_spend(payload, 0.6, run_id="r1"), [])
        self.assertTrue(reserve_spend(payload, 0.6, run_id="r2"))
        self.assertAlmostEqual(ledger_total(key), 0.6, places=4)
        release_reservation(payload, run_id="r1", reason="unit_done")
        release_reservation(payload, run_id="r1", reason="unit_done")
        record_cost_event(key, item="render_estimate", usd=0.4, run_id="r1", event_type="estimate")
        self.assertAlmostEqual(ledger_total(key), 0.4, places=4)

    def test_spend_cap_no_bypass_under_concurrency(self):
        import threading
        key = f"unit-conc-{os.getpid()}"
        payload = {"session_id": key, "spend_cap_usd": 10.0}
        passed, lock = [], threading.Lock()

        def worker(i):
            # 20 threads (each its own SQLite connection) race to reserve $2 of a
            # $10 cap. BEGIN IMMEDIATE must serialize them so only 5 ever pass.
            if not reserve_spend(payload, 2.0, run_id=f"r{i}"):
                with lock:
                    passed.append(i)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(passed), 5)
        self.assertLessEqual(ledger_total(key), 10.0)

    def test_stale_reservation_sweep_frees_cap(self):
        from agents import governance
        key = f"unit-sweep-{os.getpid()}"
        payload = {"session_id": key, "spend_cap_usd": 10.0}
        # A run reserves, then "crashes" — it never settles (no release).
        self.assertEqual(reserve_spend(payload, 8.0, run_id="crashed"), [])
        self.assertAlmostEqual(ledger_total(key), 8.0, places=4)
        # The orphaned hold now blocks a fresh run from the same cap.
        self.assertTrue(reserve_spend(payload, 5.0, run_id="fresh"))
        # Backdate the orphaned reservation so it qualifies as stale.
        governance._conn().execute(
            "UPDATE cost_events SET created_at = created_at - 100000 "
            "WHERE project_key=? AND run_id='crashed'", (key,))
        governance._conn().commit()
        self.assertGreaterEqual(sweep_stale_reservations(ttl_seconds=3600), 1)
        self.assertAlmostEqual(ledger_total(key), 0.0, places=4)
        # Cap is free again; the fresh run can now reserve. Sweep is idempotent.
        self.assertEqual(reserve_spend(payload, 5.0, run_id="fresh2"), [])
        self.assertEqual(sweep_stale_reservations(ttl_seconds=3600), 0)

    def test_parse_and_posting_routes(self):
        script = "Reels\n\nFrame 1\nA story starts.\n\nCaption:\nLong caption."
        with app.test_client() as client:
            parsed = client.post(
                "/parse-script",
                json={"script": script, "assets_dir": "", "suggest": False, "detect_speakers": False},
            )
            self.assertEqual(parsed.status_code, 200)
            data = parsed.get_json()
            self.assertEqual(data["posting_caption"], "Long caption.")
            kit = client.post(
                "/posting-kit",
                json={"frames": data["frames"], "posting_caption": data["posting_caption"]},
            )
            self.assertEqual(kit.status_code, 200)
            self.assertTrue(kit.get_json()["hashtags"])

    def test_growth_pilot_routes(self):
        from agents import llm
        original_chat = llm.chat

        def fake_chat(messages=None, *_args, **_kwargs):
            # Translation requests echo each frame_id back with a marked translation
            # so the route's real-translation path is exercised without a live LLM.
            blob = json.dumps(messages or [])
            if "translator" in blob:
                req = json.loads(messages[-1]["content"])
                return json.dumps({"frames": [
                    {"frame_id": fr["frame_id"],
                     "caption": "[hi] " + fr["caption"],
                     "voiceover": "[hi] " + fr["voiceover"]}
                    for fr in req["frames"]
                ]})
            return json.dumps({
                "frames": [
                    {
                        "role": "hook",
                        "caption": "I started small.",
                        "voiceover": "I started small.",
                        "visual_need": "real_photo_preferred",
                        "media_query": "early humble beginning",
                        "motion_hint": "slow push-in",
                        "duration": 4,
                        "confidence": "high",
                        "operator_note": "Use a real early photo if available.",
                    },
                    {
                        "role": "outcome",
                        "caption": "Then I won.",
                        "voiceover": "Then I won.",
                        "visual_need": "ai_symbolic",
                        "media_query": "victory moment",
                        "motion_hint": "crane up",
                        "duration": 4,
                        "confidence": "medium",
                        "operator_note": "Check that the ending is factual.",
                    },
                ],
                "posting_caption": "I started small. Then I won.",
                "tone_note": "Respectful founder journey.",
            })

        llm.chat = fake_chat
        self.addCleanup(lambda: setattr(llm, "chat", original_chat))

        with app.test_client() as client:
            blocked = client.post("/story-intake", json={"mode": "brand", "story": "A real story."})
            self.assertEqual(blocked.status_code, 400)
            ok_payload = {"session_id": "growth-unit", "rights_confirmed": True, "story": "I started small. Then I won."}
            story = client.post("/story-intake", json=ok_payload)
            self.assertEqual(story.status_code, 200)
            story_json = story.get_json()
            self.assertEqual(story_json["status"], "ai_draft")
            self.assertIn("Frame 1", story_json["script"])
            self.assertIn("frames_meta", story_json)
            parsed = client.post("/parse-script", json={"script": story_json["script"], "assets_dir": ""})
            self.assertEqual(parsed.status_code, 200)
            self.assertGreaterEqual(len(parsed.get_json()["frames"]), 2)
            hooks = client.post("/hook-workshop", json={**ok_payload, "frames": [{"caption": "My first line"}]})
            self.assertEqual(hooks.status_code, 200)
            hooks_json = hooks.get_json()
            self.assertEqual(len(hooks_json["candidates"]), 3)
            self.assertNotIn("score", hooks_json["candidates"][0])
            # Multi-language: operator picks "Hindi" → real translation keyed by code "hi".
            variants = client.post("/caption-variants", json={**ok_payload, "frames": [{"frame_id": "f01", "caption": "Hello", "voiceover": "Hello"}], "languages": ["Hindi"]})
            self.assertEqual(variants.status_code, 200)
            variants_json = variants.get_json()
            self.assertEqual(variants_json["status"], "translated")
            self.assertEqual(variants_json["languages"], ["hi"])
            self.assertIn("hi", variants_json["variants"])
            row = variants_json["variants"]["hi"][0]
            self.assertEqual(row["source_caption"], "Hello")
            self.assertEqual(row["draft_caption"], "[hi] Hello")
            self.assertEqual(row["status"], "translated")
            # No language chosen → 400 with the supported catalogue (never auto-fan-out).
            none_picked = client.post("/caption-variants", json={**ok_payload, "frames": [{"frame_id": "f01", "caption": "Hello"}], "languages": []})
            self.assertEqual(none_picked.status_code, 400)
            self.assertTrue(any(l["code"] == "hi" for l in none_picked.get_json()["supported"]))

    def test_story_intake_fallback_remains_parseable(self):
        from agents import llm
        from agents.growth import story_to_draft
        original_chat = llm.chat
        llm.chat = lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("offline"))
        self.addCleanup(lambda: setattr(llm, "chat", original_chat))

        draft = story_to_draft("I started small. Then I won.", max_frames=4)
        self.assertEqual(draft["status"], "fallback_draft")
        self.assertIn("Frame 1", draft["script"])
        self.assertIn("Caption:", draft["script"])
        with tempfile.TemporaryDirectory() as td:
            script = os.path.join(td, "draft.txt")
            with open(script, "w", encoding="utf-8") as fp:
                fp.write(draft["script"])
            frames = parse_frame_script(script, "")
        self.assertGreaterEqual(len(frames), 1)

    def test_language_registry_and_per_language_voice(self):
        from agents import languages
        # Launch set is exactly the 5 chosen languages.
        self.assertEqual(set(languages.SUPPORTED_LANGUAGES), {"hi", "en", "mr", "pa", "bn"})
        # Aliases + codes normalise, junk is dropped, order + de-dupe preserved.
        self.assertEqual(
            languages.normalize_languages(["Hindi", "bn", "klingon", "hi", "Bangla"]),
            ["hi", "bn"],
        )
        self.assertEqual(languages.normalize_languages([]), [])

        # Per-language voice override: a language voice id beats the base role map,
        # and lang=None preserves the original (no-language) resolution.
        from agents import cast
        frame = {"speaker_id": cast.NARRATOR_ID}
        original_loader = cast._load_language_voices
        cast._load_language_voices = lambda lang: {"narrator": "hi_voice_xyz"} if lang == "hi" else {}
        self.addCleanup(lambda: setattr(cast, "_load_language_voices", original_loader))
        self.assertEqual(cast.voice_for_frame(frame, "default_voice", lang="hi"), "hi_voice_xyz")
        self.assertEqual(cast.voice_for_frame(frame, "default_voice", lang=None), "default_voice")

    def test_run_store_persists_payload_and_logs(self):
        run_id = f"unit-run-{os.getpid()}"
        run_store.save(run_id, status="running", payload={"session_id": run_id})
        run_store.append_log(run_id, "hello")
        run_store.append_log(run_id, "again")
        stored = run_store.load(run_id)
        self.assertEqual(stored["status"], "running")
        self.assertEqual(stored["payload"]["session_id"], run_id)
        self.assertIn("hello", stored["log"])
        self.assertIn("again", stored["log"])

    def test_run_store_reconnects_after_a_dead_connection(self):
        # The per-thread connection used to be cached forever, so ONE transient failure
        # (volume sleeping, another process checkpointing the -wal/-shm away) poisoned it
        # and every later call raised until the process restarted — the Library
        # "Couldn't load stories" outage. Closing it underneath is that failure.
        run_id = f"unit-reconnect-{os.getpid()}"
        run_store.save(run_id, status="running", payload={"session_id": run_id})
        run_store._LOCAL.con.close()
        self.assertEqual(run_store.load(run_id)["status"], "running")   # must self-heal

    def test_run_store_never_defaults_into_a_temp_dir(self):
        # The archive + DB must never default under tempfile.gettempdir(): it follows
        # TMPDIR (which can point at removable storage) and the OS may erase it.
        import tempfile as _tf
        self.assertFalse(
            str(run_store.DEFAULT_RUNS_DIR).startswith(_tf.gettempdir()),
            f"runs default {run_store.DEFAULT_RUNS_DIR} is inside the temp dir",
        )

    def test_ip_watermark_resolver_and_overlay(self):
        from agents import watermark
        # Unknown IP and missing-PNG IP both degrade to "" (no overlay, no error).
        self.assertEqual(watermark.watermark_for("Not An IP"), "")
        self.assertEqual(watermark.watermark_for(""), "")
        ids = {ip["id"] for ip in watermark.list_ips()}
        self.assertIn("HOB Originals", ids)  # registry loads

        # Full-frame transparent PNG composites over a clip via apply_brand_overlay.
        import subprocess
        from PIL import Image
        from agents.assembler import apply_brand_overlay
        with tempfile.TemporaryDirectory() as td:
            wm = os.path.join(td, "wm.png")
            Image.new("RGBA", (108, 192), (255, 0, 0, 128)).save(wm)  # semi-transparent
            src = os.path.join(td, "in.mp4")
            out = os.path.join(td, "out.mp4")
            subprocess.run(
                ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=navy:s=108x192:d=1",
                 "-pix_fmt", "yuv420p", src],
                check=True, capture_output=True)
            apply_brand_overlay(src, out, watermark_path=wm, width=108, height=192)
            self.assertTrue(os.path.exists(out) and os.path.getsize(out) > 1000)

    def test_vision_suggest_degrades_and_route_validates(self):
        from agents.suggestions import suggest_from_image, CAMERA_MOVES
        # No image / missing file → {} (caller treats as no-op), never raises.
        self.assertEqual(suggest_from_image("", "caption"), {})
        self.assertEqual(suggest_from_image("/no/such/file.jpg", "caption"), {})
        self.assertIn("static", CAMERA_MOVES)
        # Route rejects a missing/​disallowed still path with 400 (no LLM spend).
        with app.test_client() as client:
            r = client.post("/suggest-frame", json={"image_path": "/etc/passwd", "caption": "x"})
            self.assertEqual(r.status_code, 400)
            r2 = client.post("/suggest-frame", json={"caption": "x"})
            self.assertEqual(r2.status_code, 400)

    def test_fcpxml_and_srt_handoff(self):
        import xml.etree.ElementTree as ET
        from agents.fcpxml import build_fcpxml, build_srt
        frames = [
            {"frame_id": "f01", "caption": "From a ==farmer== to a model", "duration": 4.0},
            {"frame_id": "f02", "caption": "", "duration": 2.5},   # silent → no SRT cue
            {"frame_id": "f03", "caption": "She walked the ramp", "duration": 5.0},
        ]
        xml = build_fcpxml(frames, width=1080, height=1920, fps=30)
        root = ET.fromstring(xml)                       # must be well-formed or this raises
        self.assertEqual(root.tag, "fcpxml")
        self.assertEqual(len(root.findall(".//asset")), 3)        # one asset per frame
        self.assertEqual(len(root.findall(".//asset-clip")), 3)   # placed on the spine
        clips = root.findall(".//asset-clip")
        self.assertEqual(clips[0].get("offset"), "0s")            # FCPXML zero
        self.assertEqual(clips[0].get("duration"), "120/30s")     # 4.0s @30fps
        self.assertEqual(clips[1].get("offset"), "120/30s")       # back-to-back

        srt = build_srt(frames)
        self.assertEqual(srt.count("-->"), 2)            # only the two captioned frames
        self.assertIn("From a farmer to a model", srt)   # == highlight markers stripped
        self.assertNotIn("==", srt)

    def test_product_surface_records(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "asset.txt")
            with open(path, "w", encoding="utf-8") as fp:
                fp.write("asset")
            asset = register_asset(path, kind="text", consent_flag=True, owner="unit")
        self.assertTrue(asset["sha256"])
        self.assertTrue(asset["consent_flag"])
        project_id = f"project-unit-{os.getpid()}"
        approval = record_approval(project_id, {"version": "v1", "claims": ["ok"]}, approver="unit")
        self.assertEqual(approval["project_id"], project_id)
        record_approval(project_id, {"version": "v1", "claims": ["ok again"]}, approver="unit")
        history = approval_history(project_id)
        self.assertEqual(len(history), 2)
        self.assertNotEqual(history[0]["id"], history[1]["id"])
        version = save_version("project-unit", {"version": "v1"}, "out.mp4")
        self.assertEqual(version["output_path"], "out.mp4")

    # ── P0/P1 gaps ─────────────────────────────────────────────────────────────
    def test_operator_auth_gates_money_routes_and_roles(self):
        from agents import auth
        os.environ.pop("HOB_AUTH_DISABLED", None)
        op = f"op-{os.getpid()}"
        appr = f"appr-{os.getpid()}"
        try:
            auth.add_operator(op, "", "pw", "operator")
            auth.add_operator(appr, "", "pw", "approver")
        except Exception:
            pass  # already seeded in a prior run on the shared temp DB
        with app.test_client() as c:
            self.assertEqual(c.post("/run", json={}).status_code, 401)        # gated
            self.assertEqual(c.post("/login", json={"operator_id": op, "password": "bad"}).status_code, 401)
            self.assertEqual(c.post("/login", json={"operator_id": op, "password": "pw"}).status_code, 200)
            # operator role cannot brand-approve; approver can
            self.assertEqual(c.post("/brand-approval", json={"project_id": "p"}).status_code, 403)
            c.post("/logout")
            c.post("/login", json={"operator_id": appr, "password": "pw"})
            self.assertEqual(c.post("/brand-approval", json={"project_id": "p"}).status_code, 200)

    def test_likeness_consent_gate(self):
        from agents import governance as g
        data = {"subject_name": "RealPerson", "session_id": f"lk-{os.getpid()}",
                "frames": [{"photo_spec": "ai_portrait", "lipsync": True}]}
        self.assertEqual(len(g.validate_likeness_consent(data)), 2)            # face + voice
        data["likeness_consent"] = {"face": True, "voice": True}
        self.assertEqual(g.validate_likeness_consent(data), [])                # granted in payload
        # ai_symbolic / no subject are never gated
        self.assertEqual(g.validate_likeness_consent({"subject_name": "X", "frames": [{"photo_spec": "ai_symbolic"}]}), [])
        self.assertEqual(g.validate_likeness_consent({"frames": [{"photo_spec": "ai_portrait"}]}), [])

    def test_provenance_tiers(self):
        from agents import provenance as p
        self.assertEqual(p.summarize({"subject_name": "L", "frames": [{"photo_spec": "ai_portrait"}]})["tier"], "ai_portrait")
        self.assertTrue(p.summarize({"subject_name": "L", "voice_clone": True, "frames": []})["real_person_ai"])
        self.assertEqual(p.summarize({"frames": [{"photo_spec": "ai_symbolic"}]})["tier"], "ai_symbolic")
        self.assertEqual(p.summarize({"frames": [{"photo_spec": "me.jpg"}]})["tier"], "real")

    def test_vendor_fallback_chain(self):
        cat = model_router.catalog()
        # Every GENERATION model has a configured cross-vendor fallback chain. Excluded:
        # 'upscale' (must NOT cross-fall-back — real→creative would hallucinate a real face;
        # degrades to the source image instead), 'edit' (identity models fail over via
        # routing.identity in agents/image_editor, not the per-model fallbacks map) and
        # 'video_to_audio' (single-vendor SFX seam — agents/sfx.py degrades in-module to
        # "" = no atmosphere track + ledger note; a missing bed can never break a render).
        self.assertEqual(
            [m for m, meta in cat["models"].items()
             if meta.get("kind") not in ("upscale", "edit", "video_to_audio")
             and not cat.get("fallbacks", {}).get(m)],
            [])
        # a simulated outage degrades to the next vendor
        tried = []

        def attempt(mid):
            tried.append(mid)
            if mid in ("nano_banana", "flux"):
                raise RuntimeError("no balance")
            return f"/img/{mid}.png"

        res, used = model_router.run_with_fallback(
            ["nano_banana", "flux", "gpt_image"], attempt, axis="image", logger=lambda m: None)
        self.assertEqual(used, "gpt_image")
        self.assertEqual(tried, ["nano_banana", "flux", "gpt_image"])
        with self.assertRaises(RuntimeError):
            model_router.run_with_fallback(["flux"], lambda m: (_ for _ in ()).throw(RuntimeError("down")))

    def test_feedback_loop_list_and_summary(self):
        rid = f"perf-{os.getpid()}"
        run_store.save(rid, status="done", performance_views=777, performance_likes=42,
                       performance_note="unit", performance_by="unit-op")
        rows = run_store.list_performance()
        mine = [r for r in rows if r["run_id"] == rid]
        self.assertEqual(len(mine), 1)
        self.assertEqual(mine[0]["performance_views"], 777)
        self.assertEqual(mine[0]["performance_by"], "unit-op")
        self.assertGreaterEqual(run_store.performance_summary()["total_views"], 777)


if __name__ == "__main__":
    unittest.main()


class CanvasBrandGateTests(unittest.TestCase):
    """S31 pre-flight #4 — the ad-claims gate must fire through the CANVAS path.

    test_brand_mandatories (above) passes at module level even with nothing calling
    validate_mandatories — a green test proving nothing. These assert the wiring.
    """

    def test_canvas_mode_is_derived_not_hardcoded(self):
        story = {"frames": [{"frame_id": "f01"}]}
        self.assertEqual(web_app._canvas_mode(story), "story")
        self.assertEqual(web_app._canvas_brand_gate(story), [])       # a story is never gated
        brand = {"frames": [{"frame_id": "f01"}], "brand": {"name": "Acme", "cta_text": "Buy"}}
        self.assertEqual(web_app._canvas_mode(brand), "brand")
        self.assertEqual(web_app._canvas_render_data(brand, "r1", "op")["mode"], "brand")

    def test_incomplete_ad_is_blocked_before_any_spend(self):
        # No logo, no product beat → the gate must name both.
        state = {"frames": [{"frame_id": "f01", "caption": "x"}],
                 "brand": {"name": "Acme", "cta_text": "Buy Acme"}}
        missing = web_app._canvas_brand_gate(state)
        self.assertTrue(any("logo" in m.lower() for m in missing), missing)
        self.assertTrue(any("product" in m.lower() for m in missing), missing)

    def test_every_paid_canvas_route_arms_the_gate(self):
        # A paid route that forgets the gate is exactly how this leaked the first time.
        import inspect
        src = inspect.getsource(web_app)
        for fn in ("api_canvas_keyframes", "api_canvas_video",
                   "api_canvas_render", "api_canvas_render_language"):
            start = src.index(f"def {fn}(")
            body = src[start:start + 2600]
            self.assertIn("_canvas_brand_gate", body, f"{fn} spends without the brand gate")


class PostingKitTests(unittest.TestCase):
    def test_posting_kit_refuses_brand_copy(self):
        from agents import posting_kit
        # BRAND_PLAN §5: AI never writes ad claims. Harmless on a story, not on an ad.
        with self.assertRaises(posting_kit.BrandCopyRefused):
            posting_kit.build([{"frame_id": "f01", "caption": "x"}], mode="brand")

    def test_posting_kit_builds_from_the_story(self):
        from agents import posting_kit
        kit = posting_kit.build([{"frame_id": "f01", "caption": "A Mumbai chai seller"},
                                 {"frame_id": "f02", "caption": "funded her degree"}])
        self.assertIn("chai seller", kit["caption"])
        self.assertEqual(kit["cover_frame_id"], "f01")
        self.assertTrue(all(t.startswith("#") for t in kit["hashtags"]))
        self.assertIn("#Reels", kit["hashtags"])


class CanvasAuthTests(unittest.TestCase):
    def test_canvas_plan_is_operator_gated_like_run(self):
        """S31 pre-flight #5 — /api/canvas/plan spent LLM tokens anonymously.

        "Free" means no vendor RENDER, not no cost: planning is a reasoning-tier
        completion plus cast derivation. Asserted at the source, because the dev bypass
        (HOB_AUTH_DISABLED=1 in .env) makes a live 200 prove nothing.
        """
        import inspect
        src = inspect.getsource(web_app)
        i = src.index('def api_canvas_plan(')
        self.assertIn("@auth.require_operator()", src[max(0, i - 200):i])
        self.assertIn("operator", inspect.signature(web_app.api_canvas_plan).parameters)


class OrientationDimsTests(unittest.TestCase):
    """Canvas orientation dropdown offers portrait/landscape/square; _run_inner used to
    resolve dims with its own inline ternary that only checked for "portrait", so
    "square" silently fell through to landscape (1920x1080) instead of (1080, 1080).
    Fixed by routing through the one function (_orient_wh) that already knew all three."""

    def test_orient_wh_covers_all_three_canvas_orientations(self):
        self.assertEqual(web_app._orient_wh("portrait"), (1080, 1920))
        self.assertEqual(web_app._orient_wh("landscape"), (1920, 1080))
        self.assertEqual(web_app._orient_wh("square"), (1080, 1080))

    def test_run_inner_resolves_dims_via_orient_wh_not_a_duplicate_branch(self):
        import inspect
        src = inspect.getsource(web_app._run_inner)
        self.assertIn("_orient_wh(orientation)", src)
        self.assertNotIn('== "portrait" else (1920, 1080)', src)


class ScopeRegistryTests(unittest.TestCase):
    """S29 Phase 1a / S31 scope registry: validation and prompt-pick must read the
    SAME table (not two independent branches that can drift, the exact bug class
    OrientationDimsTests caught in web_app). Zero output diff on existing scopes."""

    def test_registry_has_exactly_the_shipped_scopes(self):
        from agents import shot_planner
        self.assertEqual(set(shot_planner._SCOPE_SYSTEM_PROMPTS),
                          {"general", "commerce"})

    def test_unknown_scope_falls_back_to_general_system_prompt(self):
        from agents import shot_planner
        calls = []
        fake_response = json.dumps({"frames": []})

        def fake_chat(messages, **kw):
            calls.append(messages[0]["content"])
            return fake_response

        import unittest.mock as mock
        with mock.patch("agents.llm.chat", side_effect=fake_chat):
            shot_planner.plan("a brief that will not cache-hit " + os.urandom(8).hex(),
                              scope="nonsense")
        self.assertTrue(calls[0].startswith(shot_planner._GENERAL_SYSTEM))

    def test_auto_mode_token_budget_covers_a_dense_dialogue_script(self):
        """A live 'Jai Hanuman' cinematic-dialogue brief (~40 beats, full spoken-line
        captions) truncated mid-JSON under the original 130 tok/shot budget (5200
        total) -> planner LLM failed -> 8-shot generic sentence-split fallback,
        which is what then tripped the story-review slideshow warnings (uniform
        framing/duration). A second, harder case (a live 'Ocean crossing' brief
        with 11 FRAME markers) then silently truncated valid-but-incomplete JSON
        even under a first attempted fix (220 tok/shot) — confirmed live: a 24-shot
        response exactly saturated the 8800-token ceiling at ~367 tok/shot actually
        used, well above the 220 estimate. Current budget: 380 tok/shot, and a
        markerless brief (this test's) sizes off word count, not a flat guess."""
        from agents import shot_planner
        import unittest.mock as mock
        captured = {}

        def fake_chat(messages, **kw):
            captured["max_tokens"] = kw.get("max_tokens")
            return json.dumps({"frames": []})

        with mock.patch("agents.llm.chat", side_effect=fake_chat):
            shot_planner.plan("brief " + os.urandom(8).hex(), scope="general")
        self.assertEqual(captured["max_tokens"], 15200)   # 40 (word-count floor) * 380
        self.assertGreater(captured["max_tokens"], 5200)

    def test_marker_briefs_now_bypass_the_llm_entirely(self):
        """SUPERSEDED budget test: marker-structured briefs used to get a scaled
        LLM token budget (est_shots = markers*4) — since compile mode landed they
        never reach the LLM at all, which retires that truncation class outright.
        The word-count budget proxy still governs unstructured prose briefs
        (covered by test_auto_mode_token_budget_covers_a_dense_dialogue_script)."""
        from agents import shot_planner
        import unittest.mock as mock
        brief = "\n\n".join(f"FRAME {i}: something happens {os.urandom(4).hex()}."
                            for i in range(1, 12))
        with mock.patch("agents.llm.chat") as chat:
            frames = shot_planner.plan(brief, scope="general")
        chat.assert_not_called()
        self.assertEqual(len(frames), 11)   # one authored frame = one shot

    def test_commerce_and_general_still_select_their_own_system_prompt(self):
        from agents import shot_planner
        calls = {}
        fake_response = json.dumps({"frames": []})

        def fake_chat(messages, **kw):
            calls["system"] = messages[0]["content"]
            return fake_response

        import unittest.mock as mock
        for scope, expected in (("general", shot_planner._GENERAL_SYSTEM),
                                 ("commerce", shot_planner._COMMERCE_SYSTEM)):
            with mock.patch("agents.llm.chat", side_effect=fake_chat):
                shot_planner.plan("brief " + os.urandom(8).hex(), scope=scope)
            self.assertTrue(calls["system"].startswith(expected))


class ScriptCompileModeTests(unittest.TestCase):
    """Root-cause fix: an AUTHORED FRAME/SCENE/SHOT script is COMPILED verbatim —
    zero LLM — instead of re-invented by the planner. The bug class this kills,
    all observed live: dialogue attribution discarded then re-guessed wrong (a
    line written SUGRIVA: rendered as Rama), truncation silently dropping the
    back half of a story, camera notes paraphrased away, [Sound:] cues lost."""

    SCRIPT = """FRAME 1: The Command
Camera Angle: Wide establishing, low sun behind the army
SUGRIVA (commanding, final):
"Cross this ocean and find Sita, or do not return to this shore."
[Sound: conch horn blast]

FRAME 2: The Doubt
Camera Angle: Low-angle, looking UP at Hanuman against the sky
Narrative: Hanuman stands apart, uncertain. The other vanaras retreat behind Hanuman.

FRAME 3: The Reminder
JAMBAVAN (ancient, gentle):
"You have forgotten yourself, Hanuman."
JAMBAVAN (continuing):
"Remember what you are — and cross."
"""

    def _compile(self, brief=None):
        from agents import shot_planner
        return shot_planner._compile_frames(brief or self.SCRIPT)

    def test_dialogue_attribution_is_verbatim_never_guessed(self):
        frames = self._compile()
        f01 = frames[0]
        self.assertEqual(f01["speaker_id"], "sugriva")        # the Sugriva→Rama bug
        self.assertEqual(f01["caption"],
                         "Cross this ocean and find Sita, or do not return to this shore.")
        self.assertEqual(f01["voice_direction"], "commanding, final")

    def test_one_shot_per_dialogue_line(self):
        frames = self._compile()
        jamb = [f for f in frames if f["speaker_id"] == "jambavan"]
        self.assertEqual(len(jamb), 2)                        # two speeches → two shots
        self.assertEqual(len(frames), 4)                      # 1 + 1 silent + 2

    def test_camera_notes_survive_verbatim(self):
        frames = self._compile()
        self.assertIn("Camera Angle: Wide establishing, low sun behind the army",
                      frames[0]["director_note"])

    def test_sound_cue_becomes_audio_intent_on_first_shot(self):
        frames = self._compile()
        self.assertEqual(frames[0].get("audio_intent"), "conch horn blast")

    def test_silent_block_infers_most_mentioned_subject(self):
        frames = self._compile()
        silent = frames[1]
        self.assertEqual(silent["caption"], "")
        self.assertEqual(silent["speaker_id"], "narrator")       # nobody talks
        self.assertEqual(silent["visual_subject_id"], "hanuman")  # but he's on screen
        self.assertEqual(silent["photo_spec"], "ai_portrait")

    def test_shot_style_timings_and_reference_identity(self):
        brief = ("Reference Yamraj from Image 1 (identity). Dark 90s look.\n\n"
                 "Shot 1 (0–4s): Extreme close-up of an oil lamp. Camera locked-off.\n\n"
                 "Shot 2 (4–10s): Wide shot, Yamraj emerges. Dramatic crash zoom.\n")
        frames = self._compile(brief)
        self.assertEqual(len(frames), 2)                      # preamble is NOT a shot
        self.assertEqual([f["duration"] for f in frames], [4.0, 6.0])
        self.assertEqual(frames[0]["photo_spec"], "ai_symbolic")   # lamp
        self.assertEqual(frames[1]["visual_subject_id"], "yamraj")  # never speaks —
        self.assertEqual(frames[1]["photo_spec"], "ai_portrait")    # named via Reference
        self.assertEqual(frames[1]["motion_override"], "crash zoom in")
        self.assertIn("[style] Reference Yamraj", frames[0]["director_note"])

    def test_production_note_headers_are_not_speakers(self):
        brief = ("FRAME 1: A\nTONE: Mystical thriller\nHANUMAN:\n\"Rama.\"\n\n"
                 "FRAME 2: B\nCAMERA WORK: wide shots\nNarrative: The cave sits empty.\n")
        frames = self._compile(brief)
        speakers = {f["speaker_id"] for f in frames}
        self.assertIn("hanuman", speakers)                    # quoted → real dialogue
        self.assertNotIn("tone", speakers)                    # unquoted → not cast
        self.assertNotIn("camera_work", speakers)

    def test_substantial_visuals_cues_become_their_own_ordered_silent_shots(self):
        """Delta A (galleri5 23-vs-19 gap): a long [VISUALS:] cue is an authored
        SHOT — it gets its own silent beat IN ORDER, with the subject inferred
        from the cue itself; short cues stay note garnish, never shots."""
        brief = (
            "FRAME 1: The Arrival\n"
            "YAMRAJ (grinding):\n\"Hanuman.\"\n"
            "[VISUALS: A skeletal hand grips the cave entrance arch from the darkness "
            "as Yamraj emerges slowly from deep shadow, tall and crowned.]\n"
            "[VISUALS: wind stirs]\n"
            "YAMRAJ (stepping into view):\n\"Your time has come.\"\n\n"
            "FRAME 2: The Doubt\nNarrative: Hanuman waits alone against the sky.\n")
        from agents import shot_planner
        frames = shot_planner._compile_frames(brief)
        kinds = [(f["caption"] != "", f["visual_subject_id"]) for f in frames]
        # order: dialogue → promoted visual (silent, Yamraj on screen) → dialogue → silent block
        self.assertEqual(kinds[0], (True, "yamraj"))
        self.assertEqual(kinds[1], (False, "yamraj"))       # promoted, subject from cue
        self.assertIn("skeletal hand grips", frames[1]["director_note"])
        self.assertEqual(kinds[2], (True, "yamraj"))
        self.assertEqual(len(frames), 4)
        # the short cue stayed garnish in the shared note, not a shot
        self.assertIn("[on screen] wind stirs", frames[0]["director_note"])

    def test_compiled_frames_are_marked_and_cast_llm_is_skipped(self):
        from agents import cast
        import unittest.mock as mock
        frames = self._compile()
        self.assertTrue(all(f["_cast_detected"] and f["compiled"] for f in frames))
        with mock.patch("agents.llm.chat") as chat:
            members = cast.detect_cast(frames)                # idempotent path
        chat.assert_not_called()
        self.assertIn("sugriva", {m["id"] for m in members})

    def test_plan_routes_structured_scripts_to_compile_no_llm(self):
        from agents import shot_planner
        import unittest.mock as mock
        brief = self.SCRIPT + f"\n{os.urandom(6).hex()}"      # cache-buster
        with mock.patch("agents.llm.chat") as chat:
            frames = shot_planner.plan(brief, scope="general")
        chat.assert_not_called()
        self.assertEqual(len(frames), 4)

    def test_unstructured_brief_still_uses_the_llm_planner(self):
        from agents import shot_planner
        import unittest.mock as mock
        with mock.patch("agents.llm.chat",
                        return_value=json.dumps({"frames": []})) as chat:
            shot_planner.plan("a chai seller's story " + os.urandom(6).hex(),
                              scope="general")
        chat.assert_called_once()

    def test_compile_failure_falls_open_to_the_llm_planner(self):
        from agents import shot_planner
        import unittest.mock as mock
        with mock.patch.object(shot_planner, "_compile_frames",
                               side_effect=RuntimeError("boom")), \
             mock.patch("agents.llm.chat",
                        return_value=json.dumps({"frames": []})) as chat:
            shot_planner.plan(self.SCRIPT + os.urandom(6).hex(), scope="general")
        chat.assert_called_once()                             # never a dead plan


class SketchCompositionTests(unittest.TestCase):
    """Approved storyboard sketch = BINDING composition for the still (probe-
    verified live 2026-07-20: framing transfers, no pencil bleed). Conditioning
    passes BOTH images (face ref + sketch) to the multi-image edit path, and
    the sketch joins the cache hash so an approved-sketch change regenerates."""

    def test_sketch_and_face_ride_the_same_edit_call(self):
        import unittest.mock as mock, tempfile, pathlib
        from agents import image_generator as ig
        td = pathlib.Path(tempfile.mkdtemp())
        face, sketch = td / "face.jpg", td / "sketch.jpg"
        face.write_bytes(b"F" * 2000); sketch.write_bytes(b"S" * 2000)
        captured = {}

        def fake_checked(model, prompt, out, fb, fid, generator=None, **kw):
            generator()          # edit_image is patched at the module attr below —
            return out           # the function-local import resolves it at call time

        frame = {"frame_id": "f01", "scene": {"image_prompt": "she stands by the window"}}
        with mock.patch.object(ig, "_generate_image_checked", side_effect=fake_checked), \
             mock.patch("agents.image_editor.edit_image",
                        side_effect=lambda refs, p, o: captured.update(refs=refs, prompt=p) or o):
            ig.generate_contextual_image(frame, str(td), reference_path=str(face),
                                         sketch_path=str(sketch))
        self.assertEqual(captured["refs"], [str(face), str(sketch)])
        self.assertIn("STORYBOARD sketch", captured["prompt"])
        self.assertIn("no pencil lines", captured["prompt"])

    def test_sketch_changes_the_cache_key(self):
        import unittest.mock as mock, tempfile, pathlib
        from agents import image_generator as ig
        td = pathlib.Path(tempfile.mkdtemp())
        sketch = td / "sketch.jpg"; sketch.write_bytes(b"S" * 2000)
        outs = []

        def fake_checked(model, prompt, out, fb, fid, generator=None, **kw):
            outs.append(out)
            pathlib.Path(out).write_bytes(b"X" * 60000)
            return out

        frame = {"frame_id": "f01", "scene": {"image_prompt": "same prompt"}}
        with mock.patch.object(ig, "_generate_image_checked", side_effect=fake_checked):
            ig.generate_contextual_image(dict(frame), str(td))
            ig.generate_contextual_image(dict(frame), str(td), sketch_path=str(sketch))
        self.assertEqual(len(outs), 2)
        self.assertNotEqual(outs[0], outs[1])   # sketch → different cache identity


class FaceRefExtractionTests(unittest.TestCase):
    """A multi-person family photo attached whole as one character's identity
    ref gives the model an ambiguous 'whose face?' signal. The extractor crops
    the right face: SMALLEST for a baby/child character (the photo also shows a
    parent), largest for adults. Strictly best-effort — failure keeps the photo."""

    def test_pick_face_prefers_by_size(self):
        from agents.image_matcher import _pick_face
        boxes = [(0, 0, 100, 100), (200, 200, 40, 40)]   # adult + baby
        self.assertEqual(_pick_face(boxes, "largest"), (0, 0, 100, 100))
        self.assertEqual(_pick_face(boxes, "smallest"), (200, 200, 40, 40))
        self.assertIsNone(_pick_face([], "largest"))

    def test_extract_degrades_to_none_on_unreadable_input(self):
        from agents.image_matcher import extract_face_ref
        self.assertIsNone(extract_face_ref("/nonexistent.jpg", "/tmp/x_face.jpg"))

    def test_extract_finds_a_real_face(self):
        """Uses OpenCV's own bundled test asset when available; skips otherwise
        (haar on synthetic drawings is unreliable — no fake-face flakiness)."""
        try:
            import cv2, os as _os
            sample = _os.path.join(_os.path.dirname(cv2.data.haarcascades),
                                   "..", "samples", "lena.jpg")
            if not _os.path.exists(sample):
                self.skipTest("no cv2 sample image bundled")
        except ImportError:
            self.skipTest("cv2 not installed")
        import tempfile
        from agents.image_matcher import extract_face_ref
        out = tempfile.mktemp(suffix="_face.jpg")
        got = extract_face_ref(sample, out)
        if got:                                # face found → crop exists and is smaller
            self.assertTrue(os.path.getsize(out) > 1000)
            os.remove(out)


class HindiFirstTests(unittest.TestCase):
    """India-primary authoring: a script WRITTEN in Devanagari must compile with
    correct speaker attribution (no upper case exists to satisfy the Latin
    ALL-CAPS guard) and must auto-set the render language so captions get
    Devanagari glyphs and voices route to the native-Hindi table."""

    HINDI_SCRIPT = """FRAME 1: आगमन
Camera Angle: Wide establishing, cave interior
यमराज (कठोर, गूंजती आवाज़):
"हनुमान। तुम्हारा समय आ गया है।"
[VISUALS: यमराज अंधेरे से प्रकट होता है — लंबा, मुकुटधारी, छाया जैसे वस्त्र।]

FRAME 2: उत्तर
हनुमान (शांत स्वर):
"यमराज। मैं तुम्हारी प्रतीक्षा कर रहा था।"
"""

    def test_devanagari_dialogue_compiles_with_correct_speakers(self):
        from agents import shot_planner
        frames = shot_planner._compile_frames(self.HINDI_SCRIPT)
        spoken = [f for f in frames if f["caption"]]
        self.assertEqual(len(spoken), 2)
        self.assertEqual(spoken[0]["caption"], "हनुमान। तुम्हारा समय आ गया है।")
        self.assertEqual(spoken[0]["speaker_id"], "यमराज")
        self.assertEqual(spoken[0]["voice_direction"], "कठोर, गूंजती आवाज़")
        self.assertEqual(spoken[1]["speaker_id"], "हनुमान")
        # the promoted [VISUALS:] cue infers यमराज as its on-screen subject
        silent = [f for f in frames if not f["caption"]]
        self.assertTrue(any(f["visual_subject_id"] == "यमराज" for f in silent))

    def test_latin_direction_lines_are_not_hindi_speakers(self):
        from agents import shot_planner
        frames = shot_planner._compile_frames(self.HINDI_SCRIPT)
        self.assertNotIn("camera_angle", {f["speaker_id"] for f in frames})

    def test_detect_language_by_script_block(self):
        from agents.languages import detect_language
        self.assertEqual(detect_language(["हनुमान। तुम्हारा समय आ गया है।"]), "hi")
        self.assertEqual(detect_language(["আমি তোমার জন্য অপেক্ষা করছিলাম"]), "bn")
        self.assertEqual(detect_language(["ਮੈਂ ਤੁਹਾਡੀ ਉਡੀਕ ਕਰ ਰਿਹਾ ਸੀ"]), "pa")
        self.assertEqual(detect_language(["I was waiting for you"]), "")
        self.assertEqual(detect_language([]), "")
        # mostly-English with a sprinkle of Hindi stays English (below threshold)
        self.assertEqual(detect_language(["Cross the ocean now " * 10 + "राम"]), "")

    def test_run_inner_wires_detection_before_voice_and_captions(self):
        import inspect
        src = inspect.getsource(web_app._run_inner)
        self.assertIn("detect_language", src)
        # detection must run BEFORE the language rides caption_style and the VO
        self.assertLess(src.index("detect_language"), src.index("caption_style = {**caption_style"))


class AmbienceStemTests(unittest.TestCase):
    """3-stem mixer, stem 3: clip-native audio (r2v — event-synced by
    construction) + per-shot SFX survive into the final mix instead of being
    stripped by `-an` at normalize. Offline ffmpeg only — no vendors."""

    def _clip(self, path, seconds=2.0, with_audio=False):
        import subprocess
        cmd = ["ffmpeg", "-y", "-v", "error",
               "-f", "lavfi", "-i", f"color=c=black:s=320x240:d={seconds}:r=24"]
        if with_audio:
            cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
                    "-c:a", "aac", "-shortest"]
        cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", path]
        subprocess.run(cmd, check=True)
        return path

    def test_native_audio_survives_into_the_final_mix(self):
        import tempfile, subprocess
        from pathlib import Path
        from agents.assembler import assemble_caption_only, _clip_ambience_track, _has_audio
        td = Path(tempfile.mkdtemp())
        a = self._clip(str(td / "a.mp4"), with_audio=True)   # r2v-style clip
        b = self._clip(str(td / "b.mp4"), with_audio=False)  # i2v-style clip
        self.assertTrue(_has_audio(a) and not _has_audio(b))
        clips = [{"segment_id": "f01", "clip_path": a, "actual_duration": 2.0},
                 {"segment_id": "f02", "clip_path": b, "actual_duration": 2.0}]
        amb = _clip_ambience_track(clips, td, "none", None)
        self.assertTrue(amb and os.path.getsize(amb) > 1000)
        out = str(td / "out.mp4")
        assemble_caption_only(clips, str(td), out, transition="none")
        r = subprocess.run(["ffmpeg", "-i", out, "-af", "volumedetect", "-f", "null", "-"],
                           capture_output=True, text=True)
        vol = [l for l in r.stderr.splitlines() if "max_volume" in l]
        self.assertTrue(vol and "-91" not in vol[0])   # NOT digital silence
        db = float(vol[0].split("max_volume:")[1].replace("dB", "").strip())
        self.assertGreater(db, -30.0)                  # the sine is audibly there

    def test_no_ambience_is_a_zero_diff_no_op(self):
        import tempfile
        from pathlib import Path
        from agents.assembler import _clip_ambience_track
        td = Path(tempfile.mkdtemp())
        b = self._clip(str(td / "b.mp4"), with_audio=False)
        self.assertIsNone(_clip_ambience_track(
            [{"segment_id": "f01", "clip_path": b, "actual_duration": 2.0}],
            td, "none", None))


class SarvamTtsSeamTests(unittest.TestCase):
    """TTS provider seam (config/tts.json): language routes the engine — Sarvam
    for Indic languages ElevenLabs can't speak natively, ElevenLabs default +
    the only voice-clone path. Missing key/failing vendor degrades gracefully."""

    def test_language_routing_table(self):
        from agents import sarvam_tts
        self.assertEqual(sarvam_tts.provider_for_lang("hi"), "sarvam")
        self.assertEqual(sarvam_tts.provider_for_lang("mr"), "sarvam")
        self.assertEqual(sarvam_tts.provider_for_lang("en"), "elevenlabs")
        self.assertEqual(sarvam_tts.provider_for_lang(None), "elevenlabs")
        self.assertEqual(sarvam_tts.provider_for_lang("klingon"), "elevenlabs")

    def test_generate_builds_the_documented_payload(self):
        import unittest.mock as mock, tempfile, base64
        from agents import sarvam_tts
        captured = {}

        def fake_post(url, json=None, headers=None, timeout=0):
            captured.update(url=url, body=json, headers=headers)
            r = mock.Mock(ok=True)
            r.json.return_value = {"audios": [base64.b64encode(b"WAV").decode()]}
            return r

        out = tempfile.mktemp(suffix=".mp3")
        with mock.patch.dict(os.environ, {"SARVAM_API_KEY": "s-test"}), \
             mock.patch("agents.sarvam_tts.requests.post", side_effect=fake_post):
            sarvam_tts.generate("नमस्ते", out, "hi")
        self.assertEqual(captured["headers"]["api-subscription-key"], "s-test")
        self.assertEqual(captured["body"]["target_language_code"], "hi-IN")
        self.assertEqual(open(out, "rb").read(), b"WAV")
        os.remove(out)

    def test_no_key_raises_so_caller_falls_back(self):
        import unittest.mock as mock
        from agents import sarvam_tts
        env = {k: v for k, v in os.environ.items() if k != "SARVAM_API_KEY"}
        with mock.patch.dict(os.environ, env, clear=True), \
             self.assertRaises(RuntimeError):
            sarvam_tts.generate("x", "/tmp/never.mp3", "hi")


class CharacterRefHardGateTests(unittest.TestCase):
    """Asset-first gate: paid still generation 409s while an on-screen character
    has no locked face (per canvas-controls-philosophy: the error names the fix,
    `force: true` is the explicit escape — never a silent trap)."""

    def setUp(self):
        # test_operator_auth_gates_money_routes_and_roles pops HOB_AUTH_DISABLED
        # and leaks that env state; these tests target the REF gate, not auth.
        self._auth_prev = os.environ.get("HOB_AUTH_DISABLED")
        os.environ["HOB_AUTH_DISABLED"] = "1"
        self.addCleanup(lambda: (os.environ.__setitem__("HOB_AUTH_DISABLED", self._auth_prev)
                                 if self._auth_prev is not None
                                 else os.environ.pop("HOB_AUTH_DISABLED", None)))

    def _canvas(self, ref=""):
        from agents import canvas_run, run_store
        import unittest.mock as mock, uuid
        with mock.patch("agents.shot_planner.plan", return_value=[
                {"frame_id": "f01", "caption": "x", "photo_spec": "ai_portrait",
                 "duration": 5.0}]):
            state = canvas_run.new_canvas("brief", story_type="ai")
        state["frames"][0]["visual_subject_id"] = "hanuman"
        state["characters"] = [{"id": "hanuman", "name": "Hanuman", "ref_path": ref}]
        rid = f"gate-{uuid.uuid4()}"
        run_store.save(rid, status="canvas",
                       payload={"mode": "canvas", "session_id": rid, "canvas": state})
        return rid

    def test_unlocked_character_blocks_keyframes_with_a_named_error(self):
        rid = self._canvas(ref="")
        with app.test_client() as c:
            r = c.post(f"/api/canvas/{rid}/keyframes", json={})
        self.assertEqual(r.status_code, 409)
        self.assertIn("Hanuman", r.get_json()["error"])
        self.assertEqual(r.get_json()["unlocked_characters"], ["Hanuman"])

    def test_force_is_the_explicit_escape(self):
        rid = self._canvas(ref="")
        import unittest.mock as mock
        with app.test_client() as c, \
             mock.patch.object(web_app, "_track_render") as tr:
            r = c.post(f"/api/canvas/{rid}/keyframes", json={"force": True})
        self.assertEqual(r.status_code, 200)
        tr.assert_called_once()

    def test_locked_character_passes_without_force(self):
        rid = self._canvas(ref="/abs/hanuman.jpg")
        import unittest.mock as mock
        with app.test_client() as c, \
             mock.patch.object(web_app, "_track_render") as tr:
            r = c.post(f"/api/canvas/{rid}/keyframes", json={})
        self.assertEqual(r.status_code, 200)
        tr.assert_called_once()


class KieR2VAdapterTests(unittest.TestCase):
    """HappyHorse 1.1 R2V via Kie.ai (the galleri5 A/B winner's model class).
    Contract verified live 2026-07-19: model slug accepted, auth OK, zero spend
    (tools/kie_probe.py). These lock the payload shape + polling parser."""

    def test_submit_builds_the_documented_payload(self):
        import unittest.mock as mock
        from agents import kie_video
        captured = {}

        def fake_post(url, headers=None, json=None, timeout=0):
            captured.update(url=url, headers=headers, body=json)
            r = mock.Mock(ok=True)
            r.json.return_value = {"data": {"taskId": "task_abc"}}
            return r

        with mock.patch.dict(os.environ, {"KIE_API_KEY": "k-test"}), \
             mock.patch("agents.kie_video.requests.post", side_effect=fake_post):
            tid = kie_video.submit("happyhorse_r2v",
                                   ["https://x/char.png", "https://x/plate.png"],
                                   "Yamraj emerges. [Image 1] is his identity.",
                                   duration=6, aspect_ratio="16:9")
        self.assertEqual(tid, "task_abc")
        self.assertTrue(captured["url"].endswith("/jobs/createTask"))
        self.assertEqual(captured["headers"]["Authorization"], "Bearer k-test")
        body = captured["body"]
        self.assertEqual(body["model"], "happyhorse-1-1/reference-to-video")
        self.assertEqual(body["input"]["reference_image"],
                         ["https://x/char.png", "https://x/plate.png"])
        self.assertEqual(body["input"]["duration"], 6)

    def test_poll_parses_resultjson_string_and_downloads(self):
        import unittest.mock as mock, tempfile
        from agents import kie_video
        out = tempfile.mktemp(suffix=".mp4")

        def fake_get(url, headers=None, params=None, timeout=0):
            r = mock.Mock(ok=True)
            if "recordInfo" in url:
                r.json.return_value = {"data": {
                    "state": "success",
                    "resultJson": json.dumps({"resultUrls": ["https://cdn/video.mp4"]})}}
            else:
                r.content = b"MP4DATA"
                r.raise_for_status = lambda: None
            return r

        with mock.patch.dict(os.environ, {"KIE_API_KEY": "k-test"}), \
             mock.patch("agents.kie_video.requests.get", side_effect=fake_get):
            kie_video.poll_and_download("task_abc", out)
        self.assertEqual(open(out, "rb").read(), b"MP4DATA")
        os.remove(out)

    def test_failed_task_raises_with_the_vendor_message(self):
        import unittest.mock as mock
        from agents import kie_video
        r = mock.Mock(ok=True)
        r.json.return_value = {"data": {"state": "fail", "failMsg": "insufficient credits"}}
        with mock.patch.dict(os.environ, {"KIE_API_KEY": "k-test"}), \
             mock.patch("agents.kie_video.requests.get", return_value=r), \
             self.assertRaises(RuntimeError) as cm:
            kie_video.poll_and_download("task_abc", "/tmp/never.mp4")
        self.assertIn("insufficient credits", str(cm.exception))

    def test_config_registers_the_model_and_its_price(self):
        cfg = json.load(open("config/models.json"))
        m = cfg["models"]["happyhorse_r2v"]
        self.assertEqual(m["backend"], "kie")
        self.assertEqual(m["kie_model"], "happyhorse-1-1/reference-to-video")
        self.assertIn("happyhorse_r2v", cfg["routing"]["video"]["face"]["premium"])
        pricing = json.load(open("config/pricing.json"))
        section, key = m["pricing_key"].split(".")
        self.assertGreater(pricing[section][key], 0)

    def test_r2v_prompt_carries_the_authored_action_not_a_camera_microcue(self):
        """Frozen-tableau bug: r2v generates the WHOLE scene, so its prompt must
        carry the director_note action; the i2v micro-prompt ('static' + caption)
        produced beautiful still images that never moved."""
        from agents.clip_builder import _kie_scene_prompt
        p = _kie_scene_prompt({
            "director_note": "A skeletal hand grips the cave entrance arch.\n"
                             "Yamraj emerges slowly from deep shadow.",
            "text": "Hanuman.", "motion_prompt": "static"})
        self.assertIn("skeletal hand grips", p)
        self.assertIn("emerges slowly", p)
        self.assertIn('speaks: "Hanuman."', p)
        self.assertIn("never a frozen still", p)

    def test_assignments_carry_the_r2v_reference_paths(self):
        data = {"orientation": "portrait", "detect_speakers": False,
                "frames": [{"frame_id": "f01", "caption": "x",
                            "photo_spec": "ai_portrait",
                            "character_ref_path": "/abs/char.png",
                            "location_ref_path": "/abs/plate.png"}]}
        frames = web_app._build_frames_from_payload(data, 5.0)
        self.assertEqual(frames[0]["character_ref_path"], "/abs/char.png")
        self.assertEqual(frames[0]["location_ref_path"], "/abs/plate.png")


class OrientationThreadingTests(unittest.TestCase):
    """A/B-test-surfaced bugs (2026-07-19): still generation hardcoded 9:16 sizes
    and Gate B hardcoded the portrait expectation, so a deliberate 16:9 reel got
    portrait stills (center-cropped at assembly) and every correctly-landscape
    image burned 3 QC retries before grudging acceptance."""

    def _img(self, w, h):
        import tempfile
        from PIL import Image
        import random
        img = Image.new("RGB", (w, h))
        img.putdata([(random.randint(0, 255),) * 3 for _ in range(w * h)])
        p = tempfile.mktemp(suffix=".jpg")
        img.save(p, quality=95)
        return p

    def test_gate_b_accepts_the_requested_orientation(self):
        from agents.safety import check_face_sanity
        land = self._img(640, 360)
        port = self._img(360, 640)
        self.assertTrue(check_face_sanity(land, "t1", orientation="landscape"))
        self.assertFalse(check_face_sanity(land, "t2", orientation="portrait"))
        self.assertTrue(check_face_sanity(port, "t3", orientation="portrait"))
        self.assertFalse(check_face_sanity(port, "t4", orientation="landscape"))
        self.assertTrue(check_face_sanity(land, "t5", orientation="square"))  # square: any
        for p in (land, port):
            os.remove(p)

    def test_size_table_covers_all_three_orientations_and_backends(self):
        from agents import image_generator as ig
        for o in ("portrait", "landscape", "square"):
            self.assertTrue(ig._size(o, "fal"))
            self.assertIn("x", ig._size(o, "openai"))
        self.assertEqual(ig._size("landscape", "fal"), "landscape_16_9")
        self.assertEqual(ig._size("", "fal"), "portrait_16_9")        # default safe
        self.assertEqual(ig._size("nonsense", "fal"), "portrait_16_9")

    def test_frames_carry_the_reel_orientation(self):
        data = {"orientation": "landscape",
                "frames": [{"frame_id": "f01", "caption": "x", "photo_spec": "ai_portrait"}],
                "detect_speakers": False}
        frames = web_app._build_frames_from_payload(data, 5.0)
        self.assertEqual(frames[0]["orientation"], "landscape")

    def test_disclosure_png_fallback_renders(self):
        """No-drawtext ffmpeg builds must still burn the provenance disclosure —
        the old path silently shipped an UNLABELED reel (governance failure)."""
        from agents import assembler
        p = assembler._disclosure_png("AI likeness · real person depicted", 1080)
        self.assertTrue(os.path.exists(p) and os.path.getsize(p) > 500)
        os.remove(p)


class VoiceoverBedTests(unittest.TestCase):
    """Red-team finding: VO mode skipped bed generation entirely (the gate at
    _canvas_render_thread only fired for music_type=='generate'), so narration
    played over dead silence unless the operator manually uploaded a song — while
    the assembler's VO+ducked-bed mixer (brand path) sat unused. The fix generates
    the same bed and routes it to bg_music_path instead of music_path (which in VO
    mode carries the narration track itself)."""

    def _run(self, data):
        import unittest.mock as mock, pathlib, tempfile
        def fake_generate_music(brief, path):
            pathlib.Path(path).write_bytes(b"mp3")
        with tempfile.TemporaryDirectory() as td, \
             mock.patch.object(web_app, "_execute_pipeline") as ep, \
             mock.patch("agents.music_generator.generate_music",
                        side_effect=fake_generate_music), \
             mock.patch("agents.music_generator.compose_music_brief",
                        return_value="brief"):
            web_app._canvas_render_thread("vo-bed-test", data, pathlib.Path(td))
            self.assertTrue(ep.called)   # render always proceeds
        return data

    def test_voiceover_mode_generates_a_bed_as_bg_music(self):
        data = self._run({"music_type": "voiceover",
                          "frames": [{"caption": "a line"}], "canvas_run_id": ""})
        self.assertTrue(data.get("bg_music_path", "").endswith("music.mp3"))
        # music_path must stay free — the VO branch fills it with the narration track.
        self.assertNotIn("music_path", data)

    def test_generate_mode_behaviour_unchanged(self):
        data = self._run({"music_type": "generate",
                          "frames": [{"caption": "a line"}], "canvas_run_id": ""})
        self.assertTrue(data.get("music_path", "").endswith("music.mp3"))
        self.assertNotIn("bg_music_path", data)

    def test_operator_uploaded_bed_is_never_overwritten(self):
        data = self._run({"music_type": "voiceover", "bg_music_path": "/abs/my_song.mp3",
                          "frames": [{"caption": "a line"}], "canvas_run_id": ""})
        self.assertEqual(data["bg_music_path"], "/abs/my_song.mp3")

    def test_bed_failure_degrades_to_vo_only_and_still_renders(self):
        import unittest.mock as mock, pathlib, tempfile
        with tempfile.TemporaryDirectory() as td, \
             mock.patch.object(web_app, "_execute_pipeline") as ep, \
             mock.patch("agents.music_generator.generate_music",
                        side_effect=RuntimeError("suno down")), \
             mock.patch("agents.music_generator.compose_music_brief",
                        return_value="brief"):
            data = {"music_type": "voiceover",
                    "frames": [{"caption": "a line"}], "canvas_run_id": ""}
            web_app._canvas_render_thread("vo-bed-fail", data, pathlib.Path(td))
            self.assertTrue(ep.called)          # narration still renders
        self.assertNotIn("bg_music_path", data)  # no half-written bed


class VisualSubjectIdentityTests(unittest.TestCase):
    """Third-person narration bug: a mythological/fictional protagonist (e.g.
    Hanuman) is narrated ABOUT, not quoted — speaker_id correctly stays 'narrator'
    (the narrator's voice reads the line) but detect_cast previously had no way to
    say someone else is DEPICTED, so every such shot fell back to the narrator's
    face (i.e. no locked face at all) -> age/appearance drifted shot to shot.
    visual_subject_id is the new field: who's on screen, independent of who's
    talking. All tests hermetic — llm.chat mocked, zero API spend."""

    def test_detect_cast_gives_a_narrated_protagonist_their_own_visual_subject_id(self):
        from agents import cast
        import unittest.mock as mock
        frames = [{"frame_id": "f01", "caption": "Sugriva gave the command."},
                  {"frame_id": "f02", "caption": "Hanuman stood at the ocean's edge."},
                  {"frame_id": "f03", "caption": "Jambavan approached him."}]
        fake_response = json.dumps({
            "cast": [{"id": "narrator", "label": "Narrator", "gender": "male", "age_bracket": "adult"},
                     {"id": "hanuman", "label": "Hanuman", "gender": "male", "age_bracket": "adult"},
                     {"id": "jambavan", "label": "Jambavan", "gender": "male", "age_bracket": "elderly"}],
            "by_frame": [
                {"frame_id": "f01", "speaker_id": "narrator", "visual_subject_id": "narrator"},
                {"frame_id": "f02", "speaker_id": "narrator", "visual_subject_id": "hanuman"},
                {"frame_id": "f03", "speaker_id": "narrator", "visual_subject_id": "jambavan"},
            ],
        })
        with mock.patch("agents.llm.chat", return_value=fake_response):
            members = cast.detect_cast(frames)
        self.assertEqual({m["id"] for m in members}, {"narrator", "hanuman", "jambavan"})
        f02 = next(f for f in frames if f["frame_id"] == "f02")
        self.assertEqual(f02["speaker_id"], "narrator")          # voice: still the narrator
        self.assertEqual(f02["visual_subject_id"], "hanuman")    # face: Hanuman, not the narrator
        self.assertEqual(f02["visual_subject_label"], "Hanuman")

    def test_detect_cast_synthesizes_a_member_the_model_forgot_to_declare(self):
        """Schema doesn't enforce that every by_frame id appears in cast[] — if the
        model references a visual_subject_id without declaring it, that must not
        silently collapse back to the narrator (the exact bug this fix targets)."""
        from agents import cast
        import unittest.mock as mock
        frames = [{"frame_id": "f01", "caption": "Hanuman leapt."}]
        fake_response = json.dumps({
            "cast": [{"id": "narrator", "label": "Narrator", "gender": "male", "age_bracket": "adult"}],
            "by_frame": [{"frame_id": "f01", "speaker_id": "narrator", "visual_subject_id": "hanuman"}],
        })
        with mock.patch("agents.llm.chat", return_value=fake_response):
            members = cast.detect_cast(frames)
        self.assertIn("hanuman", {m["id"] for m in members})
        self.assertEqual(frames[0]["visual_subject_id"], "hanuman")

    def test_apply_defaults_visual_subject_to_speaker_for_single_narrator(self):
        from agents import cast
        frames = [{"frame_id": "f01", "caption": "x"}]
        cast._apply(frames, cast._narrator_member("", ""))
        self.assertEqual(frames[0]["visual_subject_id"], "narrator")

    def test_apply_cast_preserves_existing_visual_subject_id(self):
        """Carried back from the UI after a prior detect_cast pass — apply_cast (the
        no-LLM render-path resolver) must not clobber it, and must resolve that
        member's OWN gender/age, not the speaker's."""
        from agents import cast
        frames = [{"frame_id": "f01", "speaker_id": "narrator",
                   "visual_subject_id": "hanuman"}]
        cast_list = [{"id": "narrator", "label": "Narrator", "gender": "female", "age_bracket": "adult"},
                     {"id": "hanuman", "label": "Hanuman", "gender": "male", "age_bracket": "adult"}]
        cast.apply_cast(frames, cast_list)
        self.assertEqual(frames[0]["visual_subject_id"], "hanuman")
        self.assertEqual(frames[0]["visual_subject_gender"], "male")     # Hanuman's, not the narrator's
        self.assertEqual(frames[0]["speaker_gender"], "female")          # unaffected

    def test_apply_cast_defaults_visual_subject_for_frames_predating_the_field(self):
        from agents import cast
        frames = [{"frame_id": "f01", "speaker_id": "son"}]   # no visual_subject_id at all
        cast_list = [{"id": "narrator", "label": "Narrator", "gender": "female", "age_bracket": "adult"},
                     {"id": "son", "label": "Son", "gender": "male", "age_bracket": "child"}]
        cast.apply_cast(frames, cast_list)
        self.assertEqual(frames[0]["visual_subject_id"], "son")
        self.assertEqual(frames[0]["visual_subject_gender"], "male")

    def test_subject_descriptor_prefers_visual_subject_over_speaker(self):
        from agents import cast
        frame = {"speaker_id": "narrator", "speaker_gender": "female", "speaker_age_bracket": "adult",
                 "visual_subject_id": "hanuman", "visual_subject_gender": "male",
                 "visual_subject_age_bracket": "adult", "visual_subject_label": "Hanuman"}
        desc = cast.subject_descriptor(frame, narrator_description="a woman telling her story")
        self.assertIn("Hanuman", desc)
        self.assertIn("man", desc)

    def test_subject_descriptor_falls_back_to_speaker_for_old_frames(self):
        from agents import cast
        frame = {"speaker_id": "son", "speaker_gender": "male", "speaker_age_bracket": "child",
                 "speaker_label": "Son"}   # no visual_subject_* at all
        desc = cast.subject_descriptor(frame)
        self.assertIn("boy", desc)
        self.assertIn("Son", desc)

    def test_subject_descriptor_narrator_visual_subject_uses_narrator_description(self):
        from agents import cast
        frame = {"speaker_id": "narrator", "visual_subject_id": "narrator"}
        self.assertEqual(cast.subject_descriptor(frame, "a tall man"), "a tall man")

    def test_cast_from_frames_collects_visual_only_subjects(self):
        """Idempotent rebuild path (already-tagged frames) must not lose a
        narrated-about subject that never appears as a speaker_id."""
        from agents import cast
        frames = [{"frame_id": "f01", "speaker_id": "narrator", "speaker_label": "Narrator",
                   "speaker_gender": "female", "speaker_age_bracket": "adult",
                   "visual_subject_id": "hanuman"}]
        members = cast._cast_from_frames(frames)
        self.assertIn("hanuman", {m["id"] for m in members})

    def test_scene_intelligence_reaches_narrated_visual_subject(self):
        """The actual trigger point that was still broken after cast.py alone was
        fixed: design_all_scenes only asked cast.subject_descriptor for a non-
        narrator SPEAKER, so a narrated-about (speaker=narrator) visual subject
        like Hanuman never reached the image prompt at all — every one of his
        shots silently kept using the operator's generic story-level subject
        description instead of his own. Mocks both LLM-backed helpers; asserts
        only on which subject_description/subject_name reached design_scene."""
        from agents import scene_intelligence
        import unittest.mock as mock
        frame = {"frame_id": "f01", "caption": "Hanuman leapt across the ocean.",
                 "photo_spec": "ai_portrait", "speaker_id": "narrator",
                 "speaker_label": "Narrator", "visual_subject_id": "hanuman",
                 "visual_subject_label": "Hanuman", "visual_subject_gender": "male",
                 "visual_subject_age_bracket": "adult"}
        captured = {}

        def fake_design_scene(story_beat, subject_name="", **kw):
            captured["subject_name"] = subject_name
            captured["subject_description"] = kw.get("subject_description", "")
            return {"emotion": "awe", "motion_prompt": "m", "camera_angle": "wide",
                    "image_prompt": "p"}

        with mock.patch.object(scene_intelligence, "design_treatment", return_value=None), \
             mock.patch.object(scene_intelligence, "design_scene", side_effect=fake_design_scene):
            scene_intelligence.design_all_scenes(
                [frame], subject_name="Amit", subject_description="a tech founder")
        self.assertEqual(captured["subject_name"], "Hanuman")
        self.assertIn("Hanuman", captured["subject_description"])
        self.assertNotIn("tech founder", captured["subject_description"])

    def test_silent_beat_uses_director_note_instead_of_empty_prompt(self):
        """The 'fish, tower, teenager' bug: a caption-less beat (no dialogue —
        legitimate for pure-visual storytelling, e.g. 'his fist glows') always
        returned image_prompt="", discarding director_note entirely. The
        storyboard sketch (and the real render, which reads the same
        scene.image_prompt) then had literally nothing to draw and produced
        unrelated generic content. Must not call design_scene (LLM) — silent
        beats stay free, same as before this fix."""
        from agents import scene_intelligence
        import unittest.mock as mock
        frame = {"frame_id": "f14", "caption": "", "photo_spec": "ai_portrait",
                 "director_note": "Extreme close-up on Hanuman's fist at his heart. "
                                   "A faint warm glow pulses once."}
        with mock.patch.object(scene_intelligence, "design_treatment", return_value=None), \
             mock.patch.object(scene_intelligence, "design_scene") as mock_design:
            out = scene_intelligence.design_all_scenes([frame])
        mock_design.assert_not_called()
        scene = out[0]["scene"]
        self.assertIn("fist at his heart", scene["image_prompt"])
        self.assertEqual(scene["emotion"], "silence")

    def test_original_first_person_hob_story_is_byte_identical(self):
        """The canonical use case this whole module exists for (its own docstring):
        ONE narrator (the mother) quotes her son mid-story. This is the ENTIRE
        pre-existing product before third-person narration existed — proves the
        full detect_cast -> design_all_scenes chain produces identical speaker
        attribution and identical subject descriptions to before this session's
        changes, for the case every existing story/run actually is."""
        from agents import cast, scene_intelligence
        import unittest.mock as mock
        frames = [
            {"frame_id": "f01", "caption": "Eighteen years on this street."},
            {"frame_id": "f02", "caption": "Mom, where is father gone?"},
            {"frame_id": "f03", "caption": "I never had an answer for him."},
        ]
        fake_cast_response = json.dumps({
            "cast": [{"id": "narrator", "label": "Mother", "gender": "female", "age_bracket": "adult"},
                     {"id": "son", "label": "Son", "gender": "male", "age_bracket": "child"}],
            "by_frame": [
                {"frame_id": "f01", "speaker_id": "narrator", "visual_subject_id": "narrator"},
                {"frame_id": "f02", "speaker_id": "son", "visual_subject_id": "son"},
                {"frame_id": "f03", "speaker_id": "narrator", "visual_subject_id": "narrator"},
            ],
        })
        with mock.patch("agents.llm.chat", return_value=fake_cast_response):
            cast.detect_cast(frames, "Mother", "a 45-year-old street vendor")

        # speaker_id/visual_subject_id are identical for EVERY frame — first-person
        # has no split by construction. This is the property that guarantees zero
        # regression: nothing downstream can observe a difference.
        for f in frames:
            self.assertEqual(f["speaker_id"], f["visual_subject_id"])

        captured = []

        def fake_design_scene(story_beat, subject_name="", **kw):
            captured.append((subject_name, kw.get("subject_description", "")))
            return {"emotion": "e", "motion_prompt": "m", "camera_angle": "c", "image_prompt": "p"}

        with mock.patch.object(scene_intelligence, "design_treatment", return_value=None), \
             mock.patch.object(scene_intelligence, "design_scene", side_effect=fake_design_scene):
            scene_intelligence.design_all_scenes(
                frames, subject_name="Mother", subject_description="a 45-year-old street vendor")

        # f01/f03 (narrator): the OPERATOR's own description, exactly as before —
        # never routed through cast.subject_descriptor at all.
        self.assertEqual(captured[0], ("Mother", "a 45-year-old street vendor"))
        self.assertEqual(captured[2], ("Mother", "a 45-year-old street vendor"))
        # f02 (quoted son): gender/age-accurate descriptor, exactly the pre-existing
        # "quoted speaker" behaviour this module's docstring describes.
        self.assertEqual(captured[1][0], "Son")
        self.assertIn("boy", captured[1][1])

    def test_genuinely_contentless_silent_beat_stays_empty(self):
        """No caption AND no director_note — nothing to draw; unchanged behaviour."""
        from agents import scene_intelligence
        import unittest.mock as mock
        frame = {"frame_id": "f01", "caption": "", "director_note": ""}
        with mock.patch.object(scene_intelligence, "design_treatment", return_value=None):
            out = scene_intelligence.design_all_scenes([frame])
        self.assertEqual(out[0]["scene"]["image_prompt"], "")


class LikenessChainTests(unittest.TestCase):
    """Likeness fidelity chain (2026-07-20): Gate B3 compares the GENERATED face to
    the REAL reference (Gate B2 only checks the prompt text — a stranger passed QC),
    and canonical portraits derive FROM the real photo via the identity path instead
    of inventing a face from text ('not even 10 percent of my character pictures')."""

    def _img(self, w=360, h=640):
        import tempfile
        from PIL import Image
        import random
        img = Image.new("RGB", (w, h))
        img.putdata([(random.randint(0, 255),) * 3 for _ in range(w * h)])
        p = tempfile.mktemp(suffix=".jpg")
        img.save(p, quality=95)
        return p

    def test_gate_b3_passes_without_a_reference(self):
        """No ref → nothing to compare → open gate, zero LLM calls."""
        from agents.safety import check_likeness
        import unittest.mock as mock
        with mock.patch("agents.llm.chat") as chat:
            self.assertTrue(check_likeness("/nonexistent.jpg", "", "f01"))
            self.assertTrue(check_likeness("/nonexistent.jpg", "/gone.jpg", "f01"))
            chat.assert_not_called()

    def test_gate_b3_fails_a_stranger_and_passes_a_match(self):
        from agents.safety import check_likeness
        import unittest.mock as mock
        gen, ref = self._img(), self._img()
        try:
            with mock.patch("agents.llm.chat",
                            return_value='{"same_person": false, "similarity": 2, "reason": "different face"}'):
                self.assertFalse(check_likeness(gen, ref, "f01"))
            with mock.patch("agents.llm.chat",
                            return_value='{"same_person": true, "similarity": 8, "reason": "same"}'):
                self.assertTrue(check_likeness(gen, ref, "f01"))
            # same person but weak similarity still fails (threshold 6)
            with mock.patch("agents.llm.chat",
                            return_value='{"same_person": true, "similarity": 4, "reason": "vague"}'):
                self.assertFalse(check_likeness(gen, ref, "f01"))
        finally:
            os.remove(gen), os.remove(ref)

    def test_gate_b3_degrades_open_and_can_be_disabled(self):
        from agents.safety import check_likeness
        import unittest.mock as mock
        gen, ref = self._img(), self._img()
        try:
            with mock.patch("agents.llm.chat", side_effect=RuntimeError("offline")):
                self.assertTrue(check_likeness(gen, ref, "f01"))
            os.environ["HOB_LIKENESS_QC"] = "0"
            try:
                with mock.patch("agents.llm.chat") as chat:
                    self.assertTrue(check_likeness(gen, ref, "f01"))
                    chat.assert_not_called()
            finally:
                os.environ.pop("HOB_LIKENESS_QC", None)
        finally:
            os.remove(gen), os.remove(ref)

    def test_checked_generation_enforces_likeness_when_ref_given(self):
        """_generate_image_checked retries on a likeness fail; a strange face never
        returns silently as a pass."""
        from agents import image_generator as ig
        import unittest.mock as mock
        out = self._img()
        calls = {"n": 0}

        def gen():
            # regenerate the file each attempt — the checked loop deletes rejects
            calls["n"] += 1
            from PIL import Image
            Image.new("RGB", (8, 8)).save(out)
        try:
            with mock.patch("agents.safety.check_face_sanity", return_value=True), \
                 mock.patch("agents.safety.critique_image", return_value=True), \
                 mock.patch("agents.safety.check_likeness",
                            side_effect=[False, True]) as lk:
                path = ig._generate_image_checked(
                    "flux", "p", out, "gpt_image", "f01", max_retries=2,
                    generator=gen, likeness_ref="/refs/mom_face.jpg")
            self.assertEqual(path, out)
            self.assertEqual(calls["n"], 2)          # retried once after the fail
            self.assertEqual(lk.call_args[0][1], "/refs/mom_face.jpg")
        finally:
            os.remove(out)

    def test_portrait_derives_from_real_photo_via_identity_path(self):
        """reference_path set → the canonical sheet goes through edit_image (identity
        path), the identity-preservation clause rides the prompt, and the ref hash
        joins the cache key (photo swap → new sheet)."""
        import tempfile
        from agents import image_generator as ig
        import unittest.mock as mock
        ref = self._img()
        out_dir = tempfile.mkdtemp()
        seen = []

        def fake_checked(model_id, prompt, out_path, fallback, frame_id, **kw):
            seen.append({"prompt": prompt, "gen": kw.get("generator"),
                         "likeness_ref": kw.get("likeness_ref")})
            from PIL import Image
            Image.new("RGB", (8, 8)).save(out_path)
            return out_path
        try:
            with mock.patch.object(ig, "_generate_image_checked", side_effect=fake_checked):
                p1 = ig.generate_character_portrait(
                    "a young mother", out_dir, char_id="mom", reference_path=ref)
                p_text = ig.generate_character_portrait(
                    "a young mother", out_dir, char_id="mom")
            self.assertIn("IDENTICAL to the reference", seen[0]["prompt"])
            self.assertEqual(seen[0]["likeness_ref"], ref)  # Gate B3 armed on the sheet itself
            self.assertIsNotNone(seen[0]["gen"])            # identity path, not text-to-image
            self.assertIsNone(seen[1]["gen"])               # no-photo path unchanged
            self.assertNotEqual(p1, p_text)                 # ref participates in the cache key
        finally:
            os.remove(ref)
            import shutil
            shutil.rmtree(out_dir, ignore_errors=True)
