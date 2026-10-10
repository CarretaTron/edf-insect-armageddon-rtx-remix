"""Malformed composition evidence and independent pixel tests, never GPU evidence."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from verify_composition import (BACKGROUND, BORDER, GEOMETRY_HASH, MESH, PS, TRACE, VS,
                                cpu_panel, export_images, fit_rectangle, verify)
from verify_probe import fnv1a64


TEST_TEMP = Path(__file__).resolve().parents[2] / "_build/test-temp"
WIDTH, HEIGHT = 128, 96
ROI = [80, 72, 40, 20]


class CompositionVerifierTests(unittest.TestCase):
    def setUp(self):
        TEST_TEMP.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="composition-verifier-", dir=TEST_TEMP)
        self.directory = Path(self.temporary.name)
        self.path = self.directory / "edf_pt_request_1_frame_5_composition.json"
        self.log = self.directory / "runtime.log"
        self.log.write_text("[EDF-Static-Composition] stage=composition_recorded frame=5 request_id=1\n"
                            "[EDF-Static-Composition] stage=presentation_queued frame=5 present_serial=12 loop_index=0 present_signal_after_overlay=1 frame_latency_after_overlay=1\n"
                            "[EDF-Static-Composition] stage=composition_gpu_completed frame=5 request_id=1\n", encoding="utf-8")
        self.whitelist = {"orderedInstances": 1, "instanceId": 7, "vertices": 252, "indices": 432,
                          "sourceMeshKey": MESH, "decoderVertexShader": VS, "decoderPixelShader": PS,
                          "geometryHash": GEOMETRY_HASH, "materialHash": "0x1234"}
        self.data = {"schema": 1, "traceId": TRACE, "gpuCompleted": True, "compositionPerformed": True,
                     "rtxFrame": 5, "generatedFrame": 5, "cameraFrame": 5, "presentedFrame": 5,
                     "requestId": 1, "presentSerial": 12, "loopIndex": 0, "wsiImageIdentity": 7000,
                     "width": WIDTH, "height": HEIGHT,
                     "format": 37, "roi": ROI.copy(), "images": [], "preservedTargets": [],
                     "colour": {"performSRGBConversion": True, "captureScreenImage": False,
                                "debugViewIndex": 0, "finalEncoding": "display_gamma22_approx"},
                     "compositionQueueSubmitCount": 2, "baseWaitAcquire": 1, "baseSignalPresent": 0,
                     "overlaySignalPresent": 1, "frameLatencySignalAfterOverlay": True,
                     "gpuHitCount": 40, "gpuDepthNonfiniteCount": 0, "gpuFinalNonfiniteCount": 0, "missLinearZ": 500001,
                     "whitelist": copy.deepcopy(self.whitelist)}
        self.depth = np.full((HEIGHT, WIDTH), 500001, dtype="<f4")
        self.depth[40:44, 30:40] = -1000
        self.final = np.zeros((HEIGHT, WIDTH, 4), dtype="<f2")
        self.final[:, :, 3] = 1
        self.final[40:44, 30:40, :3] = [.25, .5, .75]
        self.crop = (22, 32, 26, 20)
        self.data["crop"] = list(self.crop)
        self.data["fit"] = list(fit_rectangle(26, 20, 40, 20))
        self.panel = cpu_panel(self.final, self.depth, 500001, self.crop, 40, 20)
        self.before = np.empty((HEIGHT, WIDTH, 4), dtype=np.uint8)
        xx, yy = np.meshgrid(np.arange(WIDTH), np.arange(HEIGHT))
        self.before[:, :, 0], self.before[:, :, 1], self.before[:, :, 2] = xx, yy, xx ^ yy
        self.before[:, :, 3] = 255
        self.after = self.before.copy()
        self.after[72:92, 80:120] = self.panel
        for name, image in (("wsi_before", self.before), ("wsi_after", self.after),
                            ("expected_panel", self.panel), ("scratch_before", self.before[72:92, 80:120])):
            self.data["images"].append(self.write_image(name, image))
        for slot in range(4):
            before_name, after_name = f"original_{slot}_before.bin", f"original_{slot}_after.bin"
            raw = bytes([slot, 42, 84, 255]) * (WIDTH * HEIGHT)
            (self.directory / before_name).write_bytes(raw)
            (self.directory / after_name).write_bytes(raw)
            self.data["preservedTargets"].append({"slot": slot, "sessionId": 1000 + slot, "format": 44,
                                                   "width": WIDTH, "height": HEIGHT, "byteCount": len(raw),
                                                   "unchanged": True, "comparison": "full_memcmp",
                                                   "beforeFile": before_name, "afterFile": after_name,
                                                   "beforeFnv1a64": hex(fnv1a64(raw)), "afterFnv1a64": hex(fnv1a64(raw))})
        self.probe = {"frameId": 5, "requestId": 1, "cameraLastUpdateFrame": 5, "currentFrameDrawCount": 1,
                      "traceId": TRACE, "compositionPerformed": False, "rasterPreservedByDesign": True,
                      "originalTargetsUnchanged": True,
                      "gpuCompleted": True, "whitelist": copy.deepcopy(self.whitelist), "missLinearZ": 500001,
                      "images": [self.write_image("final_offscreen", self.final, 97, 8),
                                 self.write_image("linear_z", self.depth, 100, 4)]}
        self.probe_path = self.directory / "edf_pt_request_1_frame_5.json"
        self.flush()

    def tearDown(self):
        self.temporary.cleanup()

    def write_image(self, name, image, fmt=37, bpp=4):
        raw = image.tobytes()
        filename = name + ".bin"
        (self.directory / filename).write_bytes(raw)
        return {"name": name, "file": filename, "format": fmt, "width": image.shape[1], "height": image.shape[0],
                "bytesPerPixel": bpp, "byteCount": len(raw), "checksumAlgorithm": "fnv1a64",
                "checksum": hex(fnv1a64(raw)), "sha256": hashlib.sha256(raw).hexdigest()}

    def replace_image(self, name, value):
        for index, image in enumerate(self.data["images"]):
            if image["name"] == name:
                self.data["images"][index] = self.write_image(name, value)
                return
        raise AssertionError(name)

    def flush(self):
        self.path.write_text(json.dumps(self.data), encoding="utf-8")
        self.probe_path.write_text(json.dumps(self.probe), encoding="utf-8")

    def report(self):
        self.flush()
        return verify(self.directory, self.log, fixture_only=True)

    def assert_rejected(self, text):
        result = self.report()
        self.assertFalse(result["passed"])
        self.assertIn(text, " ".join(result["errors"]))

    def test_complete_fixture_labels_itself_as_not_gpu_evidence(self):
        result = self.report()
        self.assertTrue(result["passed"], result["errors"])
        self.assertTrue(result["notGpuEvidence"])
        self.assertEqual(result["evidenceKind"], "synthetic_fixture")
        self.assertEqual(result["samples"][0]["outsideRoiChangedPixels"], 0)
        self.assertFalse(result["realtimePathTracingProven"])

    def test_outside_roi_single_alpha_byte_corruption(self):
        image = self.after.copy()
        image[1, 2, 3] ^= 1
        self.replace_image("wsi_after", image)
        self.assert_rejected("outside ROI")

    def test_panel_copy_corruption(self):
        image = self.after.copy()
        image[74, 82, 0] ^= 1
        self.replace_image("wsi_after", image)
        self.assert_rejected("Presented ROI")

    def test_scratch_must_preserve_actual_pre_overlay_pixels(self):
        scratch = self.before[72:92, 80:120].copy()
        scratch[0, 0, 1] ^= 1
        self.replace_image("scratch_before", scratch)
        self.assert_rejected("Scratch")

    def test_wrong_gpu_shader_output_detected_even_if_copy_matches(self):
        panel = self.panel.copy()
        panel[4:8, 12:18, 0] = 197
        after = self.after.copy()
        after[72:92, 80:120] = panel
        self.replace_image("expected_panel", panel)
        self.replace_image("wsi_after", after)
        self.assert_rejected("independent nearest")

    def test_stale_camera_or_source_or_present_frame(self):
        for name in ("cameraFrame", "generatedFrame", "presentedFrame"):
            with self.subTest(name=name):
                self.data[name] = 4
                self.assert_rejected("stale")
                self.data[name] = 5

    def test_original_mrt_corruption_even_when_claimed_unchanged(self):
        target = self.data["preservedTargets"][2]
        raw = bytearray((self.directory / target["afterFile"]).read_bytes())
        raw[123] ^= 1
        (self.directory / target["afterFile"]).write_bytes(raw)
        target["afterFnv1a64"] = hex(fnv1a64(raw))
        self.assert_rejected("original game target")

    def test_mesh_or_shader_or_count_change_rejected(self):
        for name, value in (("sourceMeshKey", "0" * 40), ("decoderVertexShader", "1" * 40),
                            ("decoderPixelShader", "2" * 40), ("vertices", 104),
                            ("orderedInstances", 2), ("geometryHash", "0x1234")):
            with self.subTest(name=name):
                old = self.data["whitelist"][name]
                self.data["whitelist"][name] = value
                self.assert_rejected("whitelist")
                self.data["whitelist"][name] = old

    def test_invalid_or_overlarge_or_zero_roi(self):
        for roi in ([127, 0, 40, 20], [0, 0, 120, 90], [0, 0, 0, 8], [0, 0, 3, 3]):
            with self.subTest(roi=roi):
                self.data["roi"] = roi
                self.assertFalse(self.report()["passed"])

    def test_duplicate_present_iteration_rejected(self):
        self.data["loopIndex"] = 1
        self.assert_rejected("single initial")

    def test_no_gpu_fence_not_approved(self):
        self.data["gpuCompleted"] = False
        self.assert_rejected("GPU completion")

    def test_colour_transfer_or_debug_override_rejected(self):
        for key, bad in (("performSRGBConversion", False), ("captureScreenImage", True),
                         ("debugViewIndex", 1), ("finalEncoding", "linear")):
            with self.subTest(key=key):
                old = self.data["colour"][key]
                self.data["colour"][key] = bad
                self.assert_rejected("semantics")
                self.data["colour"][key] = old

    def test_readback_checksum_corruption(self):
        self.data["images"][0]["checksum"] = "0x1234"
        self.assert_rejected("FNV1a64")

    def test_source_readback_path_escape_rejected(self):
        self.probe["images"][0]["file"] = "../escape.bin"
        self.assert_rejected("escapes")

    def test_byte_count_or_format_mismatch(self):
        self.data["images"][0]["format"] = 44
        self.assert_rejected("RGBA8")
        self.data["images"][0]["format"] = 37
        self.data["images"][0]["byteCount"] -= 1
        self.assert_rejected("byte count")

    def test_missing_target_or_image(self):
        self.data["preservedTargets"].pop()
        self.assert_rejected("four original")

    def test_depth_crop_must_come_from_current_hits(self):
        self.data["crop"][0] += 1
        self.assert_rejected("depth hits")

    def test_missing_gpu_completed_log(self):
        lines = self.log.read_text(encoding="utf-8").splitlines()
        self.log.write_text("\n".join(line for line in lines if "composition_gpu_completed" not in line) + "\n", encoding="utf-8")
        self.assert_rejected("composition_gpu_completed")

    def test_runtime_failure_record(self):
        with self.log.open("a", encoding="utf-8") as stream:
            stream.write("[EDF-Static-Composition] stage=composition_failed frame=6\n")
        self.assert_rejected("Runtime reports")

    def test_panel_border_and_background_are_exact(self):
        self.assertTrue(np.all(self.panel[:2] == BORDER))
        self.assertTrue(np.all(self.panel[:, :2] == BORDER))
        self.assertTrue(np.any(np.all(self.panel == BACKGROUND, axis=2)))

    def test_alpha_is_not_geometry_coverage(self):
        changed_alpha = self.final.copy()
        changed_alpha[:, :, 3] = 0
        panel = cpu_panel(changed_alpha, self.depth, 500001, self.crop, 40, 20)
        np.testing.assert_array_equal(panel, self.panel)

    def test_nonfinite_output_or_depth_rejected(self):
        final = self.final.copy()
        final[0, 0, 0] = np.nan
        with self.assertRaisesRegex(ValueError, "Nonfinite"):
            cpu_panel(final, self.depth, 500001, self.crop, 40, 20)

    def test_integer_fit_has_no_overshoot(self):
        for crop in ((1919, 1079), (1, 1), (1900, 1), (1, 1000), (583, 71)):
            x, y, w, h = fit_rectangle(*crop, 820, 200)
            self.assertGreaterEqual(x, 2)
            self.assertGreaterEqual(y, 2)
            self.assertLessEqual(x + w, 818)
            self.assertLessEqual(y + h, 198)

    def test_recorded_submission_contract_cannot_signal_present_early(self):
        self.data["baseSignalPresent"] = 1
        self.assert_rejected("submission contract")

    def test_reported_hit_statistics_and_sentinel_match_raw_source(self):
        self.data["gpuHitCount"] -= 1
        self.assert_rejected("hit/nonfinite")

    def test_nonfinite_final_counter_rejected_before_panel_approval(self):
        self.data["gpuFinalNonfiniteCount"] = 1
        self.assert_rejected("hit/nonfinite")

    def test_source_whitelist_independently_rechecked(self):
        self.data.pop("whitelist")
        self.probe["whitelist"]["indices"] = 431
        self.assert_rejected("whitelist")

    def test_non_gpu_fixture_cannot_be_approved_as_native(self):
        self.flush()
        result = verify(self.directory, self.log, fixture_only=False)
        self.assertFalse(result["passed"])
        self.assertIn("Unvalidated native WSI resolution", " ".join(result["errors"]))

    def test_png_export_preserves_raw_pixels_and_source_files(self):
        from PIL import Image
        before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.directory.iterdir() if p.is_file()}
        result = self.report()
        output = self.directory.parent / (self.directory.name + "_images")
        try:
            manifest = export_images(result, output, self.directory)
            self.assertEqual(len(manifest["files"]), 5)
            image = np.asarray(Image.open(output / "request_1_frame_5_wsi_after.png"))
            np.testing.assert_array_equal(image, self.after)
            after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.directory.iterdir() if p.is_file()}
            self.assertEqual(before, after)
        finally:
            if output.exists():
                for p in output.iterdir():
                    p.unlink()
                output.rmdir()

    def test_png_export_rejects_original_session_as_destination(self):
        with self.assertRaisesRegex(ValueError, "overlaps"):
            export_images(self.report(), self.directory / "images", self.directory)

    def test_png_export_rejects_failed_verification(self):
        with self.assertRaisesRegex(ValueError, "passing"):
            export_images({"passed": False}, self.directory.parent / "unused", self.directory)

    def test_actual_submission_log_matches_present_serial(self):
        self.log.write_text(self.log.read_text(encoding="utf-8").replace("present_serial=12", "present_serial=11"), encoding="utf-8")
        self.assert_rejected("Actual presentation submission")

    def test_gpu_validation_failure_rejected_despite_completion_callback(self):
        original = self.log.read_text(encoding="utf-8")
        for failure in ("SYNC-HAZARD-WRITE_AFTER_WRITE", "VUID-vkCmdDispatch-None-08600",
                        "VK_ERROR_DEVICE_LOST", "DxvkSubmissionQueue: Command submission failed",
                        "vkWaitForFences returned error", "SPIR-V validation failed"):
            with self.subTest(failure=failure):
                self.log.write_text(original + failure + "\n", encoding="utf-8")
                self.assert_rejected("GPU submission/device/synchronization/validation failure")

    def test_recording_or_generation_failure_rejected(self):
        original = self.log.read_text(encoding="utf-8")
        for stage in ("composition_recording_failed", "preview_generation_failed"):
            with self.subTest(stage=stage):
                self.log.write_text(original + f"[EDF-Static-Composition] stage={stage} frame=6\n", encoding="utf-8")
                self.assert_rejected("Runtime reports")

    def test_sdk_plain_hazards_reject_without_sync_identifier_or_vuid(self):
        original = self.log.read_text(encoding="utf-8")
        for failure in (
                "[09:48:28.024] err:   [VK_DEBUG_REPORT] Code 1544472022: vkCmdBuildAccelerationStructuresKHR(): WRITE_AFTER_WRITE hazard detected. Writes to scratch buffer VkBuffer [BVH Scratch].",
                "info: vkCmdCopyImage(): READ_AFTER_WRITE hazard detected.",
                "warning: vkCmdPipelineBarrier(): WRITE_AFTER_READ hazard detected.",
                "info: WRITE_RACING_WRITE hazard detected."):
            with self.subTest(failure=failure):
                self.log.write_text(original + failure + "\n", encoding="utf-8")
                self.assert_rejected("GPU submission/device/synchronization/validation failure")

    def test_runtime_debug_report_errors_reject_without_message_identifier(self):
        original = self.log.read_text(encoding="utf-8")
        for failure in (
                "[09:48:27.598] err:   [VK_DEBUG_REPORT] Code 861854874: vkCmdBeginRenderPass(): dependencyCount is incompatible between VkRenderPass and VkFramebuffer.",
                "error: [VK_DEBUG_REPORT] Code 1397510317: descriptor image format does not support depth comparison."):
            with self.subTest(failure=failure):
                self.log.write_text(original + failure + "\n", encoding="utf-8")
                self.assert_rejected("GPU submission/device/synchronization/validation failure")

    def test_debug_report_information_is_not_an_error(self):
        with self.log.open("a", encoding="utf-8") as stream:
            stream.write('info: [VK_DEBUG_REPORT] Code 0: Inserted device layer "VK_LAYER_KHRONOS_validation"\n')
        result = self.report()
        self.assertTrue(result["passed"], result["errors"])

    def test_ordinary_injection_rejections_and_cache_warnings_are_not_gpu_failure(self):
        with self.log.open("a", encoding="utf-8") as stream:
            stream.write("[RTX-Compatibility-Trace] stage=injection_rejected reason=already_injected_this_frame\n"
                         "warn: DXVK cache not found; rebuilding\n")
        result = self.report()
        self.assertTrue(result["passed"], result["errors"])


if __name__ == "__main__":
    unittest.main()
