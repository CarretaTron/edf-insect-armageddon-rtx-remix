import hashlib
import importlib.util
import json
import struct
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "analyze_capture.py"
SPEC = importlib.util.spec_from_file_location("edf_capture_analyzer", MODULE_PATH)
analyzer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(analyzer)
FIXTURE_SPEC = importlib.util.spec_from_file_location("edf_capture_fixture", MODULE_PATH.parent / "make_fixture.py")
fixture_module = importlib.util.module_from_spec(FIXTURE_SPEC)
FIXTURE_SPEC.loader.exec_module(fixture_module)


class Fixture:
    def __init__(self, directory):
        self.root = Path(directory)

    def blob(self, name, data, **metadata):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return {"path": name, "hash": hashlib.sha1(data).hexdigest(), "size": len(data), **metadata}

    def triangle(self, width=2, base=-100):
        vertices = self.blob("vertices.bin", struct.pack("<9f", 0, 0, 0, 1, 0, 0, 0, 1, 0), byteOffset=32)
        indices = self.blob("indices.bin", struct.pack("<3" + ("H" if width == 2 else "I"), 100, 101, 102), byteOffset=8 * width)
        return {"type": "draw", "frame": 4, "draw": 7, "api": "DrawIndexedPrimitive",
                "args": {"primitiveType": 4, "primitiveCount": 1, "indexed": True,
                         "baseVertexIndex": base, "startIndex": 8, "minVertexIndex": 100, "numVertices": 3},
                "state": {"vertexDeclaration": {"hash": "fixture", "elements": [
                    {"stream": 0, "offset": 0, "type": 2, "method": 0, "usage": 0, "usageIndex": 0}]},
                    "streams": [{"stream": 0, "offset": 32, "stride": 12, "frequency": 1,
                                 "resource": 1, "blob": vertices}],
                    "indices": {"format": 101 if width == 2 else 102, "resource": 2, "blob": indices},
                    "textures": [{"slot": 0, "contentAvailable": False}],
                    "renderTargets": [{"slot": 0, "width": 1280, "height": 720}]}}


class MatrixTests(unittest.TestCase):
    def test_bytecode_definitions_override_application_constants(self):
        values, overridden = analyzer.apply_immediate_float_definitions([0.0] * 16, 5,
            {"c5": ["1", "0", "0", "0"], "c6": ["0", "1", "0", "0"],
             "c7": ["0", "0", "1", "0"], "c8": ["0", "0", "0", "1"],
             "i0": ["1", "2", "3", "4"]})
        self.assertEqual(overridden, [5, 6, 7, 8])
        self.assertTrue(analyzer.matrix_candidates(values, 5)["candidates"][0]["metrics"]["identity"])

    def test_transposed_affine_storage_is_reported_in_both_orientations(self):
        matrix = [[0, -1, 0, 0], [1, 0, 0, 0], [0, 0, 1, 0], [12, 34, 56, 1]]
        values = [item for row in matrix for item in row]
        result = analyzer.matrix_candidates(values)
        candidate = result["candidates"][0]
        self.assertTrue(candidate["metrics"]["affineRowVectorShape"])
        self.assertFalse(candidate["metrics"]["affineColumnVectorShape"])
        self.assertTrue(candidate["transposedMetrics"]["affineColumnVectorShape"])
        self.assertTrue(candidate["metrics"]["orthonormalUpper3x3"])
        self.assertEqual(candidate["meaning"], "unknown")

    def test_projection_or_wvp_shape_does_not_assign_camera(self):
        values = [2, 0, 0, 0, 0, 3, 0, 0, 0, 0, 1.01, 1, 0, 0, -0.101, 0]
        result = analyzer.matrix_candidates(values, 12, [12, 13, 14, 15])
        self.assertEqual(result["candidateCount"], 1)
        self.assertEqual(result["candidates"][0]["meaning"], "unknown")
        self.assertEqual(result["candidates"][0]["firstRegister"], 12)
        self.assertIn("does not uniquely", result["interpretation"])

    def test_nan_and_singular_matrices_are_not_candidates(self):
        self.assertEqual(analyzer.matrix_candidates([0.0] * 16)["candidateCount"], 0)
        self.assertEqual(analyzer.matrix_candidates([float("nan")] * 16)["candidateCount"], 0)

    def test_shader_usage_filter_is_evidence_only(self):
        values = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
        self.assertEqual(analyzer.matrix_candidates(values, 0, [100])["candidateCount"], 0)
        self.assertEqual(analyzer.matrix_candidates(values, 0, [3])["candidateCount"], 1)


class ShaderTests(unittest.TestCase):
    def test_sm3_declarations_constants_samplers_and_matrix_implicit_range(self):
        text = """// Synthetic bytecode disassembly. It is not an EDF shader.
vs_3_0
dcl_position v0
dcl_normal v1
dcl_texcoord0 v2
dcl_position o0
dcl_texcoord o1
def c8, 1, 0, 0, 0
m4x4 o0, v0, c12
mov o1.xy, v2
"""
        result = analyzer.analyze_disassembly(text)
        self.assertEqual(result["model"], "vs_3_0")
        self.assertEqual([item["register"] for item in result["inputs"]], ["v0", "v1", "v2"])
        self.assertEqual(result["constantRegisters"]["c"], [12, 13, 14, 15])
        self.assertEqual(result["immediateDefinitions"]["c8"], ["1", "0", "0", "0"])
        self.assertEqual(result["positionOutputWrites"], ["m4x4 o0, v0, c12"])
        self.assertEqual(result["matrixInstructions"][0]["meaning"], "unknown")

    def test_pixel_shader_relative_constants_and_sampler(self):
        result = analyzer.analyze_disassembly("ps_3_0\ndcl_2d s2\ndcl vPos.xy\ntexld r0, v0, s2\nadd r1, r0, c[a0.x + 4]\nmov oC0, r1\nmov oDepth, r1.x\n")
        self.assertEqual(result["samplers"], [2])
        self.assertTrue(result["relativeConstantAddressing"])
        self.assertEqual(result["inputs"][0]["register"], "vPos")
        self.assertEqual({item["register"] for item in result["outputs"]}, {"oC0", "oDepth"})

    def test_original_bytecode_header(self):
        self.assertEqual(analyzer.shader_model(struct.pack("<2I", 0xFFFE0300, 0xFFFF)), "vs_3_0")
        self.assertEqual(analyzer.shader_model(struct.pack("<2I", 0xFFFF0300, 0xFFFF)), "ps_3_0")
        self.assertIsNone(analyzer.shader_model(b"not a shader"))


class GeometryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.fixture = Fixture(self.directory.name)
        self.store = analyzer.BlobStore(self.fixture.root)

    def tearDown(self):
        self.directory.cleanup()

    def test_negative_base_index_is_valid_for_16_and_32_bits(self):
        for width in (2, 4):
            with self.subTest(width=width):
                draw = self.fixture.triangle(width)
                geometry, positions, unique = analyzer.validate_geometry(draw, self.store)
                self.assertTrue(geometry["validCapturedRanges"], geometry["errors"])
                self.assertEqual(unique, [0, 1, 2])
                self.assertEqual(positions[1][:3], (1, 0, 0))
                self.assertEqual(geometry["bounds"]["space"], "input_attribute_space_unknown")

    def test_negative_effective_index_is_rejected(self):
        with self.assertRaisesRegex(analyzer.CaptureError, "negative effective"):
            analyzer.validate_geometry(self.fixture.triangle(base=-101), self.store)

    def test_index_blob_offset_is_used_and_outside_range_rejected(self):
        draw = self.fixture.triangle()
        draw["args"]["startIndex"] = 7
        with self.assertRaisesRegex(analyzer.CaptureError, "outside"):
            analyzer.validate_geometry(draw, self.store)

    def test_invalid_declared_index_range_does_not_silently_pass(self):
        draw = self.fixture.triangle()
        draw["args"]["numVertices"] = 2
        result, _, _ = analyzer.validate_geometry(draw, self.store)
        self.assertFalse(result["validCapturedRanges"])
        self.assertFalse(result["declaredIndexRangeValid"])

    def test_layout_stride_and_missing_vertex_bytes_are_reported(self):
        draw = self.fixture.triangle()
        draw["state"]["streams"][0]["stride"] = 8
        result, _, _ = analyzer.validate_geometry(draw, self.store)
        self.assertFalse(result["validCapturedRanges"])
        self.assertIn("stride", result["errors"][0])
        draw["state"]["streams"][0]["stride"] = 12
        draw["state"]["streams"][0]["blob"]["byteOffset"] += 12
        result, _, _ = analyzer.validate_geometry(draw, self.store)
        self.assertFalse(result["validCapturedRanges"])
        self.assertIn("outside", result["errors"][0])

    def test_nonindexed_up_data_is_analyzed_without_pointer_identity(self):
        draw = self.fixture.triangle()
        draw["api"] = "DrawPrimitiveUP"
        draw["args"].update({"indexed": False, "startVertex": 0})
        stream = draw["state"]["streams"][0]
        stream.update({"resource": 0, "source": "up", "offset": 0})
        stream["blob"]["byteOffset"] = 0
        result, _, _ = analyzer.validate_geometry(draw, self.store)
        self.assertTrue(result["validCapturedRanges"])
        self.assertFalse(result["indexed"])

    def test_indexed_up_resource_zero_uses_blob_range_not_resource_identity(self):
        draw = self.fixture.triangle(base=0)
        draw["api"] = "DrawIndexedPrimitiveUP"
        draw["args"]["startIndex"] = 0
        stream = draw["state"]["streams"][0]
        stream.update({"resource": 0, "source": "up", "offset": 0})
        stream["blob"]["byteOffset"] = 100 * stream["stride"]
        indices = draw["state"]["indices"]
        indices.update({"resource": 0, "source": "up"})
        indices["blob"]["byteOffset"] = 0
        result, positions, unique = analyzer.validate_geometry(draw, self.store)
        self.assertTrue(result["validCapturedRanges"], result["errors"])
        self.assertEqual(unique, [100, 101, 102])
        self.assertEqual(positions[1][:3], (1, 0, 0))

    def test_instance_stream_is_not_read_as_a_vertex_stream(self):
        draw = self.fixture.triangle()
        draw["state"]["streams"][0]["frequency"] = 0x80000001
        result, positions, _ = analyzer.validate_geometry(draw, self.store)
        self.assertTrue(result["instanced"])
        self.assertEqual(positions, [])
        self.assertIn("skipped", " ".join(result["warnings"]))


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.fixture = Fixture(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def write_capture(self, records, tail=""):
        path = self.fixture.root / "capture.jsonl"
        path.write_text("".join(json.dumps(record) + "\n" for record in records) + tail, encoding="utf-8")
        return path

    def test_capture_missing_tail_is_reported_but_completed_draw_survives(self):
        path = self.write_capture([self.fixture.triangle()], '{"type":"draw"')
        report, manifest = analyzer.analyze_capture(path)
        self.assertTrue(report["truncated"])
        self.assertEqual(report["drawCount"], 1)
        self.assertEqual(manifest["shaders"], [])
        self.assertTrue(report["parseErrors"])

    def test_deduplicated_state_shader_records_and_terminal_status(self):
        draw = self.fixture.triangle()
        state = draw.pop("state")
        bytecode = self.fixture.blob("shader.dxso", struct.pack("<2I", 0xFFFE0300, 0xFFFF))
        state["vertexShader"] = {"hash": bytecode["hash"], "stage": "vertex"}
        draw["stateId"] = "state_fixture"
        path = self.write_capture([
            {"type": "shader", "hash": bytecode["hash"], "stage": "vertex", "bytecode": bytecode},
            {"type": "state", "id": "state_fixture", "state": state}, draw,
            {"type": "capture_status", "truncated": False, "error": None}])
        report, manifest = analyzer.analyze_capture(path)
        self.assertFalse(report["truncated"])
        self.assertTrue(report["draws"][0]["geometry"]["validCapturedRanges"])
        self.assertEqual(manifest["shaders"][0]["model"], "vs_3_0")
        self.assertFalse(any("No end" in warning for warning in report["warnings"]))

    def test_recorder_write_error_makes_capture_incomplete(self):
        path = self.write_capture([{ "type": "capture_status", "truncated": False, "error": "disk full"}])
        report, _ = analyzer.analyze_capture(path)
        self.assertTrue(report["truncated"])
        self.assertTrue(any("disk full" in warning for warning in report["warnings"]))

    def test_adapter_capture_end_without_writer_status_fails_integrity(self):
        path = self.write_capture([{"type": "capture_begin", "schema": 1},
                                  {"type": "capture_end", "reason": "window_complete", "completedFrames": 1}])
        report, _ = analyzer.analyze_capture(path)
        self.assertTrue(report["truncated"])
        self.assertFalse(report["writerIntegrityVerified"])
        self.assertTrue(any("does not confirm" in warning for warning in report["warnings"]))
        self.assertEqual(analyzer.main([str(path), "--output", str(self.fixture.root / "inspection")]), 1)

    def test_interrupted_window_is_partial_despite_drained_writer(self):
        for reason in ("device_destroyed", "observer_stopped"):
            with self.subTest(reason=reason):
                path = self.write_capture([{"type": "capture_begin", "schema": 1},
                                          {"type": "capture_end", "reason": reason, "completedFrames": 0, "selectedDraws": 1},
                                          {"type": "capture_status", "truncated": False, "error": None}])
                report, _ = analyzer.analyze_capture(path)
                self.assertFalse(report["truncated"])
                self.assertTrue(report["writerIntegrityVerified"])
                self.assertFalse(report["captureWindow"]["complete"])
                self.assertEqual(report["captureWindow"]["completedFrames"], 0)
                self.assertTrue(any("last frame may be partial" in warning for warning in report["warnings"]))

    def test_writer_status_must_be_terminal_for_adapter_integrity(self):
        path = self.write_capture([{"type": "capture_begin", "schema": 1},
                                  {"type": "capture_status", "truncated": False, "error": None},
                                  {"type": "draw_result", "frame": 1, "draw": 0}])
        report, _ = analyzer.analyze_capture(path)
        self.assertTrue(report["truncated"])
        self.assertFalse(report["writerIntegrityVerified"])

    def test_adapter_schema_fixture_exports_obj_and_applies_metadata_definitions(self):
        path = fixture_module.make_fixture(self.fixture.root)
        output = self.fixture.root / "inspection"
        result = analyzer.main([str(path), "--output", str(output), "--export-input-obj"])
        self.assertEqual(result, 0)
        report = json.loads((output / "report.json").read_text(encoding="utf-8"))
        draw = report["draws"][0]
        self.assertTrue(draw["geometry"]["validCapturedRanges"])
        self.assertEqual(draw["geometry"]["effectiveMinimum"], 0)
        self.assertEqual(draw["prepareFlags"], 0)
        self.assertTrue(report["writerIntegrityVerified"])
        self.assertTrue(report["captureWindow"]["complete"])
        self.assertEqual(draw["remixDecisions"], ["synthetic_fixture_only"])
        self.assertEqual(draw["matrixCandidates"]["vsConstants"]["bytecodeDefinedRegistersApplied"], [16, 17, 18, 19])
        candidate = next(item for item in draw["matrixCandidates"]["vsConstants"]["candidates"] if item["firstRegister"] == 16)
        self.assertTrue(candidate["metrics"]["identity"])
        self.assertEqual(candidate["meaning"], "unknown")
        self.assertIn("f 1 2 3", (output / "frame_4_draw_7_input.obj").read_text(encoding="utf-8"))

    def test_shader_metadata_definitions_work_when_disassembly_is_unavailable(self):
        path = fixture_module.make_fixture(self.fixture.root)
        records, _ = analyzer.read_records(path)
        shader = next(item for item in records if item["type"] == "shader")
        shader["disassembly"] = {"available": False, "reason": "disabled_or_disassembly_failed"}
        path = self.write_capture(records)
        report, manifest = analyzer.analyze_capture(path)
        self.assertNotIn("disassembly", manifest["shaders"][0])
        candidates = report["draws"][0]["matrixCandidates"]["vsConstants"]
        self.assertEqual(candidates["bytecodeDefinedRegistersApplied"], [16, 17, 18, 19])
        self.assertTrue(any(item["firstRegister"] == 16 and item["metrics"]["identity"] for item in candidates["candidates"]))

    def test_truncated_or_tampered_blob_cannot_validate_geometry(self):
        draw = self.fixture.triangle()
        (self.fixture.root / "vertices.bin").write_bytes(b"short")
        path = self.write_capture([draw, {"type": "capture_end"}])
        report, _ = analyzer.analyze_capture(path)
        geometry = report["draws"][0]["geometry"]
        self.assertFalse(geometry["validCapturedRanges"])
        self.assertIn("truncated", geometry["errors"][0])

    def test_blob_paths_cannot_escape_capture_directory(self):
        store = analyzer.BlobStore(self.fixture.root)
        with self.assertRaisesRegex(analyzer.CaptureError, "escapes"):
            store.read({"path": "../outside.bin"})

    def test_shader_manifest_and_cli_emit_unknown_semantics(self):
        draw = self.fixture.triangle()
        bytecode = self.fixture.blob("shader.dxso", struct.pack("<2I", 0xFFFE0300, 0xFFFF))
        digest = bytecode["hash"]
        shader = {"hash": digest, "stage": "vertex", "name": "VS_" + digest, "bytecode": bytecode}
        draw["state"]["vertexShader"] = shader
        (self.fixture.root / (shader["name"] + ".dxso.dis")).write_text("vs_3_0\ndcl_position v0\ndcl_position o0\nm4x4 o0, v0, c0\n", encoding="utf-8")
        values = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
        draw["state"]["vsConstants"] = {"float": self.fixture.blob("vs.bin", struct.pack("<16f", *values), count=4, firstRegister=0)}
        path = self.write_capture([draw, draw, {"type": "capture_end"}])
        output = self.fixture.root / "report"
        result = analyzer.main([str(path), "--output", str(output), "--shader-dir", str(self.fixture.root), "--export-input-obj"])
        self.assertEqual(result, 0)
        manifest = json.loads((output / "shader_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(len(manifest["shaders"]), 1)
        self.assertEqual(manifest["shaders"][0]["category"], "unknown")
        self.assertEqual(manifest["shaders"][0]["bytecodeHash"], digest)
        report = json.loads((output / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["draws"][0]["matrixCandidates"]["vsConstants"]["candidates"][0]["meaning"], "unknown")
        obj = (output / "frame_4_draw_7_input.obj").read_text(encoding="utf-8")
        self.assertIn("f 1 2 3", obj)
        self.assertIn("space are unknown", obj)


if __name__ == "__main__":
    unittest.main()
