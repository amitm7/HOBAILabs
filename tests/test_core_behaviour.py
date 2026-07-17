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
