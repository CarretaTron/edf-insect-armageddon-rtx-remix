"""Synthetic malformed-evidence and independent ray tests; never GPU evidence."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from verify_probe import (DEFAULT_GEOMETRY, GEOMETRY_HASH, IMAGE_FORMATS, MESH, PS, TRACE, VS,
                          FileAudit, check_matrices, compare_depth, cpu_depth, fnv1a64,
                          load_geometry, matrix, verify)


TOOLS = Path(__file__).resolve().parent
TEST_TEMP = TOOLS.parents[1]/"_build/test-temp"
WIDTH, HEIGHT = 160, 90


def flatten(value):
    return np.asarray(value).reshape(-1, order="F").tolist()


class ProbeVerifierTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.vertices, cls.faces, cls.geometry = load_geometry(DEFAULT_GEOMETRY)
        cls.view = np.asarray(cls.geometry["manifest"][0]["cameraFit"]["viewRowMajor"]).T
        cls.projection = np.asarray(cls.geometry["manifest"][0]["cameraFit"]["projectionRowMajor"]).T
        cls.reference = cpu_depth(cls.vertices, cls.faces, cls.view, cls.projection, WIDTH, HEIGHT)
        TEST_TEMP.mkdir(parents=True, exist_ok=True)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="probe-verifier-unit-", dir=TEST_TEMP)
        self.directory = Path(self.temporary.name)
        self.log = self.directory/"runtime.log"
        self.metadata = self.directory/"edf_pt_request_1_frame_0.json"
        miss = float(np.float32(1e30))
        depth = np.where(np.isfinite(self.reference), self.reference, miss).astype("<f4")
        mask = depth != np.float32(miss)
        rows, cols = np.nonzero(mask)
        view, projection = self.view, self.projection
        self.data = {
            "schema": 1, "traceId": TRACE, "gpuCompleted": True,
            "rasterPreservedByDesign": True, "compositionPerformed": False,
            "frameId": 0, "requestId": 1, "currentFrameDrawCount": 1, "cameraLastUpdateFrame": 0,
            "surfaceCount": 1, "lightCount": 0, "pathMaxBounces": 2, "matrixStorage": "column_major",
            "whitelist": {"orderedInstances": 1, "instanceId": 1, "vertices": 252, "indices": 432,
                          "sourceMeshKey": MESH, "decoderVertexShader": VS, "decoderPixelShader": PS,
                          "geometryHash": GEOMETRY_HASH, "materialHash": "0x1234"},
            "sourceTargetSessionId": 1001, "injectionTargetSessionId": 1000,
            "injectionTargetContentHash": "0x1234", "injectionTargetFormat": 44,
            "injectionTargetExtent": [WIDTH, HEIGHT], "sourceWorldToView": flatten(view),
            "sourceViewToProjection": flatten(projection), "rendererWorldToView": flatten(view),
            "rendererViewToProjection": flatten(projection), "rendererViewToWorld": flatten(np.linalg.inv(view)),
            "rendererProjectionToView": flatten(np.linalg.inv(projection)),
            "rendererWorldToProjectionJittered": flatten(projection@view),
            "rendererProjectionToWorldJittered": flatten(np.linalg.inv(projection@view)),
            "rendererViewToProjectionJittered": flatten(projection),
            "rendererViewError": 0, "rendererProjectionError": 0, "jitterPixels": [0, 0],
            "rendererResolution": [WIDTH, HEIGHT], "rendererCameraFlags": 1, "rendererNearPlane": .25,
            "missLinearZ": miss, "images": [], "preservedTargets": [], "originalTargetsUnchanged": True,
            "depthHitCount": int(mask.sum()), "depthNonfiniteCount": 0,
            "radianceNonzeroCount": 0, "radianceNonfiniteCount": 0,
            "hitBounds": [int(cols.min()), int(rows.min()), int(cols.max()), int(rows.max())],
            "minimumHitLinearZ": float(depth[mask].min()), "maximumHitLinearZ": float(depth[mask].max()),
        }
        for name, (fmt, bpp) in IMAGE_FORMATS.items():
            raw = depth.tobytes() if name == "linear_z" else bytes(WIDTH*HEIGHT*bpp)
            filename = name+".bin"
            (self.directory/filename).write_bytes(raw)
            self.data["images"].append({"name": name, "file": filename, "format": fmt, "width": WIDTH,
                                       "height": HEIGHT, "bytesPerPixel": bpp, "byteCount": len(raw),
                                       "checksumAlgorithm": "fnv1a64", "checksum": hex(fnv1a64(raw))})
        for slot in range(4):
            raw = bytes([slot, 33, 55, 255])*(WIDTH*HEIGHT)
            before, after = f"target_{slot}_before.bin", f"target_{slot}_after.bin"
            (self.directory/before).write_bytes(raw)
            (self.directory/after).write_bytes(raw)
            self.data["preservedTargets"].append({"slot": slot, "sessionId": 1000+slot, "format": 44,
                                                  "width": WIDTH, "height": HEIGHT, "byteCount": len(raw),
                                                  "unchanged": True, "comparison": "full_memcmp",
                                                  "beforeFile": before, "afterFile": after,
                                                  "beforeFnv1a64": hex(fnv1a64(raw)), "afterFnv1a64": hex(fnv1a64(raw))})
        self.log.write_text("\n".join([
            f"[EDF-Static-Probe] stage=injection_snapshot frame=0 current_frame_draw_count=1 camera_valid=1 camera_last_update_frame=0 duplicate=0",
            f"[EDF-Static-Probe] stage=whitelist_accepted frame=0 instance_id=1 ordered_instances=1 vertices=252 indices=432 geometry_hash={GEOMETRY_HASH} mesh={MESH} vs={VS} ps={PS}",
            "[EDF-Static-Probe] stage=offscreen_begin frame=0 request_id=1",
            "[EDF-Static-Probe] stage=readback_recorded frame=0 request_id=1",
            f"[EDF-Static-Probe] stage=gpu_completed frame=0 request_id=1 depth_hits={int(mask.sum())} original_targets_unchanged=1 composition_performed=0",
        ])+"\n", encoding="utf-8")
        self.write_data()

    def tearDown(self):
        if not self.directory.resolve().is_relative_to(TEST_TEMP.resolve()):
            raise RuntimeError("Refusing cleanup outside workspace test-temp.")
        self.temporary.cleanup()

    def write_data(self):
        self.metadata.write_text(json.dumps(self.data)+"\n", encoding="utf-8")

    def result(self, **kwargs):
        self.write_data()
        return verify(self.directory, self.log, fixture_only=True, width=WIDTH, height=HEIGHT, **kwargs)

    def fails(self):
        result = self.result()
        self.assertFalse(result["passed"], result)
        self.assertTrue(result["errors"])

    def test_positive_is_explicitly_synthetic(self):
        report = self.result()
        self.assertTrue(report["passed"], report["errors"])
        self.assertTrue(report["fixtureOnly"] and report["notGpuEvidence"])
        self.assertFalse(report["visualReplacementAllowed"])
        self.assertFalse(report["samples"][0]["radianceNonzeroObserved"])

    def test_composition_sidecar_is_not_a_probe_sample(self):
        sidecar = self.directory / "edf_pt_request_1_frame_0_composition.json"
        sidecar.write_text('{"schema":1,"compositionPerformed":true}', encoding="utf-8")
        report = self.result()
        self.assertTrue(report["passed"], report["errors"])
        self.assertEqual(len(report["samples"]), 1)

    def test_camera_missing_frame_and_recovery(self):
        with self.log.open("a", encoding="utf-8") as output:
            output.write("[EDF-Static-Probe] stage=injection_snapshot frame=3 current_frame_draw_count=0 camera_valid=0 camera_last_update_frame=2 duplicate=0\n")
            output.write("[EDF-Static-Probe] stage=injection_snapshot frame=4 current_frame_draw_count=1 camera_valid=1 camera_last_update_frame=4 duplicate=0\n")
        self.assertTrue(self.result(expected_missing_frame=3)["passed"])

    def test_stale_camera_on_missing_frame_fails(self):
        with self.log.open("a", encoding="utf-8") as output:
            output.write("[EDF-Static-Probe] stage=injection_snapshot frame=3 current_frame_draw_count=0 camera_valid=1 camera_last_update_frame=2\n")
        self.assertFalse(self.result(expected_missing_frame=3)["passed"])

    def test_gpu_uncompleted_fails(self):
        self.data["gpuCompleted"] = False
        self.fails()

    def test_composition_fails(self):
        self.data["compositionPerformed"] = True
        self.fails()

    def test_stale_probe_camera_fails(self):
        self.data["cameraLastUpdateFrame"] = 5
        self.fails()

    def test_multiple_selected_draws_fails(self):
        self.data["currentFrameDrawCount"] = 2
        self.fails()

    def test_additional_surface_fails(self):
        self.data["surfaceCount"] = 2
        self.fails()

    def test_wrong_shader_fails(self):
        self.data["whitelist"]["decoderVertexShader"] = "0"*40
        self.fails()

    def test_replaced_mesh_hash_fails(self):
        self.data["whitelist"]["geometryHash"] = "0x1"
        self.fails()

    def test_wrong_log_whitelist_fails(self):
        self.log.write_text(self.log.read_text().replace("vertices=252", "vertices=104"))
        self.fails()

    def test_completion_without_matching_request_fails(self):
        self.log.write_text(self.log.read_text().replace("stage=gpu_completed frame=0 request_id=1", "stage=gpu_completed frame=0 request_id=2"))
        self.fails()

    def test_nonfinite_matrix_fails(self):
        self.data["rendererWorldToView"][0] = None
        self.fails()

    def test_renderer_camera_differs_fails(self):
        self.data["rendererWorldToView"][12] += 1
        self.fails()

    def test_transposed_matrix_fails(self):
        self.data["rendererViewToWorld"] = flatten(np.linalg.inv(self.view).T)
        self.fails()

    def test_wrong_composed_matrix_fails(self):
        self.data["rendererWorldToProjectionJittered"][12] += 1
        self.fails()

    def test_jitter_fails(self):
        self.data["jitterPixels"] = [.5, 0]
        self.fails()

    def test_wrong_raw_size_fails(self):
        (self.directory/"linear_z.bin").write_bytes(b"short")
        self.fails()

    def test_raw_checksum_fails(self):
        self.data["images"][0]["checksum"] = "0x1234"
        self.fails()

    def test_readback_path_escape_fails(self):
        self.data["images"][0]["file"] = "../escape.bin"
        self.fails()

    def test_missing_original_target_fails(self):
        self.data["preservedTargets"].pop()
        self.fails()

    def test_claimed_equal_original_target_mutation_fails(self):
        target = self.data["preservedTargets"][0]
        raw = bytearray((self.directory/target["afterFile"]).read_bytes())
        raw[0] ^= 1
        (self.directory/target["afterFile"]).write_bytes(raw)
        target["afterFnv1a64"] = hex(fnv1a64(raw))
        self.fails()

    def test_duplicate_target_identity_fails(self):
        self.data["preservedTargets"][1]["sessionId"] = 1000
        self.fails()

    def test_missing_completed_log_fails(self):
        self.log.write_text("\n".join(x for x in self.log.read_text().splitlines() if "gpu_completed" not in x))
        self.fails()

    def test_independent_reference_analytic_triangle(self):
        vertices = np.array([[-1., -1., -5.], [1., -1., -5.], [0., 1., -5.]])
        projection = np.array([[1., 0, 0, 0], [0, 1., 0, 0], [0, 0, -1.01, -.1], [0, 0, -1., 0]])
        depth = cpu_depth(vertices, np.array([[0, 1, 2]]), np.eye(4), projection, 100, 100)
        self.assertAlmostEqual(float(depth[50, 50]), -5., places=9)
        self.assertTrue(np.isnan(depth[10, 10]))
        self.assertTrue(np.isnan(depth[40, 30]))
        self.assertTrue(np.isfinite(depth[58, 50]))

    def test_wrong_vertical_orientation_fails(self):
        depth = np.where(np.isfinite(self.reference), self.reference, 1e30).astype(np.float32)
        with self.assertRaises(ValueError):
            compare_depth(depth[::-1], self.reference, 1e30)

    def test_wrong_nearest_depth_fails(self):
        depth = np.where(np.isfinite(self.reference), self.reference-10, 1e30).astype(np.float32)
        with self.assertRaises(ValueError):
            compare_depth(depth, self.reference, 1e30)

    def test_nonfinite_depth_fails(self):
        depth = np.where(np.isfinite(self.reference), self.reference, 1e30).astype(np.float32)
        depth[0, 0] = np.nan
        with self.assertRaises(ValueError):
            compare_depth(depth, self.reference, 1e30)

    def test_known_fnv_vectors(self):
        self.assertEqual(fnv1a64(b""), 0xcbf29ce484222325)
        self.assertEqual(fnv1a64(b"a"), 0xaf63dc4c8601ec8c)
        self.assertEqual(fnv1a64(b"hello"), 0xa430d84680aabd0b)

    def test_column_major_matrix_known_translation(self):
        value = np.eye(4)
        value[:3, 3] = [11, 22, 33]
        self.assertTrue(np.array_equal(matrix(flatten(value))@np.array([1, 2, 3, 1]), [12, 24, 36, 1]))


if __name__ == "__main__":
    unittest.main()
