#!/usr/bin/env python3
"""Synthetic format, transfer, integrity and write-isolation tests (not GPU proof)."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
from PIL import Image

import view_capture as viewer


class ViewerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.capture = self.root / "capture"
        self.capture.mkdir()
        self.output = self.root / "png"
        self.width, self.height = 4, 3
        self.depth = np.full((3, 4), 500001, dtype="<f4")
        self.depth[1, 1:3] = [-4, -6]
        packed = np.full((3, 4), (1023 << 20) | (512 << 10) | (3 << 30), dtype="<u4")
        half = np.full((3, 4, 4), [.25, .5, 1., 0.], dtype="<f2")
        self.raw = {"linear_z": self.depth.tobytes(), "albedo": packed.tobytes(),
                    "direct_diffuse_radiance": half.tobytes(), "final_offscreen": half.tobytes()}
        self.metadata = {"schema": 1, "gpuCompleted": True, "compositionPerformed": False,
                         "frameId": 10, "requestId": 1, "rendererResolution": [4, 3],
                         "injectionTargetExtent": [4, 3], "cameraLastUpdateFrame": 10,
                         "currentFrameDrawCount": 1, "rendererCameraFlags": 1,
                         "missLinearZ": 500001, "depthHitCount": 2, "hitBounds": [1, 1, 2, 1],
                         "images": []}
        self.metadata_file = self.capture / "edf_pt_request_1_frame_10.json"
        for name, raw in self.raw.items():
            file = f"buffer_{name}.bin"
            (self.capture / file).write_bytes(raw)
            fmt, bpp = viewer.FORMATS[name]
            self.metadata["images"].append({"name": name, "file": file, "format": fmt,
                                            "width": 4, "height": 3, "bytesPerPixel": bpp,
                                            "byteCount": len(raw), "checksumAlgorithm": "fnv1a64",
                                            "checksum": viewer.fnv1a64(raw)})
        self.write_metadata()

    def tearDown(self):
        self.temp.cleanup()

    def write_metadata(self):
        self.metadata_file.write_text(json.dumps(self.metadata), encoding="utf-8")

    def render(self, **kwargs):
        return viewer.render(self.capture, self.output, expected_samples=1,
                             final_encoding="srgb", **kwargs)

    def test_half_little_endian(self):
        raw = bytes.fromhex("003c00c000380000")
        np.testing.assert_array_equal(viewer.decode(raw, "final_offscreen", 1, 1), [[[1, -2, .5, 0]]])

    def test_packed_channel_order_and_alpha(self):
        value = np.array([(3 << 30) | (513 << 20) | (127 << 10) | 1000], dtype="<u4")
        np.testing.assert_allclose(viewer.decode(value.tobytes(), "albedo", 1, 1),
                                   [[[1000/1023, 127/1023, 513/1023, 1]]])

    def test_float_depth_endian(self):
        decoded = viewer.decode(bytes.fromhex("000080bf"), "linear_z", 1, 1)
        self.assertEqual(float(decoded[0, 0]), -1.)

    def test_exact_srgb_piecewise(self):
        values = np.array([0, .001, .0031308, .01, .5, 1])
        expected = np.array([0, .01292, .040449936, .099852823, .735356983, 1])
        np.testing.assert_allclose(viewer.linear_to_srgb(values), expected, atol=1e-8)

    def test_hdr_reinhard_then_srgb(self):
        np.testing.assert_array_equal(viewer.display_rgb(np.array([0, 1, 3]), "hdr", 0), [0, 188, 225])

    def test_exposure_fixed_ev(self):
        self.assertEqual(int(viewer.display_rgb(np.array([.5]), "hdr", 1)[0]), 188)

    def test_final_srgb_bypasses_double_gamma_and_tonemap(self):
        np.testing.assert_array_equal(viewer.display_rgb(np.array([.25, .5, 1]), "srgb", 0), [64, 128, 255])

    def test_display_passthrough_ignores_viewer_hdr_exposure(self):
        np.testing.assert_array_equal(viewer.display_rgb(np.array([.25, .5, 1]), "display", 5), [64, 128, 255])

    def test_linear_final_uses_transfer_without_tonemap(self):
        self.assertEqual(int(viewer.display_rgb(np.array([.5]), "linear", 0)[0]), 188)

    def test_depth_mask_ignores_zero_alpha(self):
        mask, distance, bounds = viewer.depth_data(self.depth, 500001, 1)
        self.assertEqual(mask.sum(), 2)
        np.testing.assert_array_equal(distance[mask], [4, 6])
        self.assertEqual(bounds, [1, 1, 2, 1])

    def test_left_handed_depth_sign(self):
        mask, distance, _ = viewer.depth_data(np.array([[2, 500001]], dtype="<f4"), 500001, 0)
        self.assertEqual(float(distance[mask][0]), 2)

    def test_depth_wrong_sign_rejected(self):
        with self.assertRaisesRegex(ValueError, "handedness"):
            viewer.depth_data(self.depth, 500001, 0)

    def test_nonfinite_depth_rejected(self):
        with self.assertRaisesRegex(ValueError, "Nonfinite"):
            viewer.depth_data(np.array([[np.nan]], dtype="<f4"), 500001, 1)

    def test_nonfinite_rgb_rejected(self):
        with self.assertRaisesRegex(ValueError, "Nonfinite"):
            viewer.display_rgb(np.array([np.inf]), "hdr", 0)

    def test_clamps_are_explicit(self):
        np.testing.assert_array_equal(viewer.display_rgb(np.array([-1, 2]), "srgb", 0), [0, 255])

    def test_fnv_known_value(self):
        self.assertEqual(viewer.fnv1a64(b"hello"), "0xa430d84680aabd0b")

    def test_success_pngs_and_original_hashes(self):
        before = viewer.snapshot(self.capture)
        result = self.render()
        self.assertTrue(result["passed"])
        self.assertTrue(result["originalFilesPreserved"])
        self.assertEqual(result["depthRange"], [4, 6])
        self.assertEqual(viewer.snapshot(self.capture), before)
        pixel = np.asarray(Image.open(self.output / "request_1_frame_10_final_offscreen.png"))[1, 1]
        np.testing.assert_array_equal(pixel, [64, 128, 255])
        self.assertEqual(len(result["outputs"]), 15)

    def test_global_depth_scale_multiple_frames(self):
        data = deepcopy(self.metadata)
        data.update(requestId=2, frameId=11, cameraLastUpdateFrame=11)
        data["images"] = deepcopy(data["images"])
        raw = self.depth.copy()
        raw[1, 1:3] = [-2, -8]
        (self.capture / "depth2.bin").write_bytes(raw.tobytes())
        image = next(x for x in data["images"] if x["name"] == "linear_z")
        image.update(file="depth2.bin", checksum=viewer.fnv1a64(raw.tobytes()))
        (self.capture / "edf_pt_request_2_frame_11.json").write_text(json.dumps(data))
        result = viewer.render(self.capture, self.output, expected_samples=2, final_encoding="srgb")
        self.assertEqual(result["depthRange"], [2, 8])
        self.assertEqual(len(result["samples"]), 2)

    def test_unsupported_format(self):
        self.metadata["images"][0]["format"] = 97
        self.write_metadata()
        with self.assertRaisesRegex(ValueError, "format"):
            self.render()

    def test_truncated_buffer(self):
        (self.capture / "buffer_albedo.bin").write_bytes(b"bad")
        with self.assertRaisesRegex(ValueError, "Truncated"):
            self.render()

    def test_extra_buffer_bytes(self):
        path = self.capture / "buffer_albedo.bin"
        path.write_bytes(path.read_bytes() + b"bad")
        with self.assertRaisesRegex(ValueError, "Truncated/extra"):
            self.render()

    def test_checksum_mismatch(self):
        self.metadata["images"][0]["checksum"] = "0x0000000000000000"
        self.write_metadata()
        with self.assertRaisesRegex(ValueError, "checksum"):
            self.render()

    def test_path_escape(self):
        self.metadata["images"][0]["file"] = "../external.bin"
        (self.root / "external.bin").write_bytes(self.raw["linear_z"])
        self.write_metadata()
        with self.assertRaisesRegex(ValueError, "escaping"):
            self.render()

    def test_output_inside_capture_rejected(self):
        with self.assertRaisesRegex(ValueError, "disjoint"):
            viewer.render(self.capture, self.capture / "png")

    def test_output_ancestor_rejected(self):
        with self.assertRaisesRegex(ValueError, "disjoint"):
            viewer.render(self.capture, self.root)

    def test_output_overwrite_rejected(self):
        self.output.mkdir()
        (self.output / "keep.txt").write_text("keep")
        with self.assertRaisesRegex(ValueError, "never overwritten"):
            self.render()

    def test_duplicate_image_rejected(self):
        self.metadata["images"][0] = deepcopy(self.metadata["images"][1])
        self.write_metadata()
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.render()

    def test_gpu_uncompleted_rejected(self):
        self.metadata["gpuCompleted"] = False
        self.write_metadata()
        with self.assertRaisesRegex(ValueError, "uncompleted"):
            self.render()

    def test_camera_frame_mismatch(self):
        self.metadata["cameraLastUpdateFrame"] = 9
        self.write_metadata()
        with self.assertRaisesRegex(ValueError, "Camera"):
            self.render()

    def test_unproved_auto_encoding_rejected(self):
        with self.assertRaisesRegex(ValueError, "requires --encoding-proof"):
            viewer.render(self.capture, self.output, expected_samples=1)

    def test_metadata_bound_encoding_proof(self):
        proof = self.root / "proof.json"
        proof.write_text(json.dumps({"schema": 1, "finalEncoding": "srgb", "explanation": "synthetic test encoding",
                                     "sources": ["synthetic fixture"], "metadataSha256": [viewer.sha256(self.metadata_file)]}))
        result = viewer.render(self.capture, self.output, expected_samples=1, encoding_proof=proof)
        self.assertEqual(result["finalEncoding"], "srgb")

    def test_wrong_metadata_encoding_proof_rejected(self):
        proof = self.root / "proof.json"
        proof.write_text(json.dumps({"schema": 1, "finalEncoding": "srgb", "explanation": "fixture",
                                     "sources": ["fixture"], "metadataSha256": ["wrong"]}))
        with self.assertRaisesRegex(ValueError, "metadata hashes"):
            viewer.render(self.capture, self.output, expected_samples=1, encoding_proof=proof)

    def make_cache(self):
        path = self.root / "verification.json"
        records = [{"path": str(self.capture / x["file"]), "sha256": viewer.sha256(self.capture / x["file"]),
                    "fnv1a64": x["checksum"], "byteCount": x["byteCount"], "format": x["format"], "name": x["name"]}
                   for x in self.metadata["images"]]
        targets = []
        for target in self.metadata.get("preservedTargets", []):
            target_records = {"slot": target["slot"]}
            for timing in ("before", "after"):
                raw_path = self.capture / target[f"{timing}File"]
                target_records[timing] = {"path": str(raw_path), "sha256": viewer.sha256(raw_path),
                                          "fnv1a64": target[f"{timing}Fnv1a64"], "byteCount": raw_path.stat().st_size}
            targets.append(target_records)
        result = {"schema": 1, "passed": True, "errors": [], "samples": [{"passed": True, "gpuCompleted": True,
                  "metadata": str(self.metadata_file), "metadataSha256": viewer.sha256(self.metadata_file),
                  "images": records, "preservedTargets": targets}]}
        path.write_text(json.dumps(result))
        return path, result

    def test_validated_sha_cache(self):
        path, _ = self.make_cache()
        result = self.render(verification=path)
        self.assertIn("metadata-bound", result["samples"][0]["sourceImages"][0]["checksumValidation"])

    def test_cache_raw_sha_mismatch_rejected(self):
        path, _ = self.make_cache()
        target = self.capture / "buffer_albedo.bin"
        target.write_bytes(bytes(len(target.read_bytes())))
        with self.assertRaisesRegex(ValueError, "verification checksum"):
            self.render(verification=path)

    def test_cache_metadata_sha_mismatch_rejected(self):
        path, _ = self.make_cache()
        self.metadata["requestId"] = 2
        self.write_metadata()
        with self.assertRaisesRegex(ValueError, "metadata SHA256"):
            self.render(verification=path)

    def test_nonfinite_raw_half_rejected(self):
        item = next(x for x in self.metadata["images"] if x["name"] == "final_offscreen")
        raw = np.full((3, 4, 4), np.nan, dtype="<f2").tobytes()
        (self.capture / item["file"]).write_bytes(raw)
        item["checksum"] = viewer.fnv1a64(raw)
        self.write_metadata()
        with self.assertRaisesRegex(ValueError, "Nonfinite values"):
            self.render()

    def add_backbuffer(self):
        raw = np.full((3, 4, 4), [20, 40, 60, 255], dtype=np.uint8).tobytes()
        target = {"slot": 0, "format": 44, "width": 4, "height": 3, "byteCount": len(raw),
                  "unchanged": True, "beforeFile": "backbuffer_before.bin", "afterFile": "backbuffer_after.bin",
                  "beforeFnv1a64": viewer.fnv1a64(raw), "afterFnv1a64": viewer.fnv1a64(raw)}
        for timing in ("before", "after"):
            (self.capture / target[f"{timing}File"]).write_bytes(raw)
        self.metadata["preservedTargets"] = [target]
        self.write_metadata()
        return target

    def test_both_backbuffers_identically_corrupted_rejected_without_cache(self):
        target = self.add_backbuffer()
        for timing in ("before", "after"):
            (self.capture / target[f"{timing}File"]).write_bytes(bytes(target["byteCount"]))
        with self.assertRaisesRegex(ValueError, "FNV checksum mismatch"):
            self.render()

    def test_both_backbuffers_identically_corrupted_rejected_with_cache(self):
        target = self.add_backbuffer()
        cache, _ = self.make_cache()
        for timing in ("before", "after"):
            (self.capture / target[f"{timing}File"]).write_bytes(bytes(target["byteCount"]))
        with self.assertRaisesRegex(ValueError, "bound verification checksum"):
            self.render(verification=cache)

    def test_valid_backbuffer_cache_and_bgra_conversion(self):
        self.add_backbuffer()
        cache, _ = self.make_cache()
        result = self.render(verification=cache)
        evidence = result["samples"][0]["originalBackbuffer"]
        self.assertTrue(evidence["fullMemcmpEqual"])
        self.assertEqual(evidence["before"]["sha256"], evidence["after"]["sha256"])
        self.assertIn("metadata-bound", evidence["before"]["checksumValidation"])
        png = np.asarray(Image.open(self.output / "request_1_frame_10_original_backbuffer.png"))
        np.testing.assert_array_equal(png[1, 1], [60, 40, 20])

    def test_backbuffer_after_checksum_validated_separately(self):
        target = self.add_backbuffer()
        target["afterFnv1a64"] = "0x0000000000000000"
        self.write_metadata()
        with self.assertRaisesRegex(ValueError, "FNV checksum mismatch.*backbuffer_after"):
            self.render()


if __name__ == "__main__":
    unittest.main(verbosity=2)
