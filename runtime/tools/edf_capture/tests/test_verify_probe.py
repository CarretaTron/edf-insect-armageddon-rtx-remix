import copy
import hashlib
import importlib.util
import struct
import sys
import tempfile
import unittest
from pathlib import Path


DIRECTORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DIRECTORY))
SPEC = importlib.util.spec_from_file_location("edf_probe_verifier", DIRECTORY / "verify_probe.py")
verifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verifier)
from probe_fixture import make_probe_capture, write_records


class VerifyProbeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path, self.shaders, self.records = make_probe_capture(Path(self.directory.name))

    def tearDown(self):
        self.directory.cleanup()

    def verify(self):
        write_records(self.path, self.records)
        return verifier.verify_probe(self.path, self.shaders)

    def state_for(self, frame, number):
        draw = next(record for record in self.records if record.get("type") == "draw"
                    and (record["frame"], record["draw"]) == (frame, number))
        return next(record["state"] for record in self.records if record.get("type") == "state" and record["id"] == draw["stateId"])

    def assert_failure_contains(self, phrase):
        result = self.verify()
        self.assertFalse(result["passed"])
        self.assertTrue(any(phrase in error for error in result["errors"]), result["errors"])

    def replace_blob(self, reference, data):
        digest = hashlib.sha1(data).hexdigest()
        relative = "blobs/" + digest + ".bin"
        (self.path.parent / relative).write_bytes(data)
        reference.update({"hash": digest, "path": relative, "size": len(data)})

    def test_all_known_inputs_validate_across_three_frames(self):
        result = self.verify()
        self.assertTrue(result["passed"], result["errors"])
        self.assertEqual(result["observedDraws"], 24)
        self.assertGreater(result["checks"], 1000)

    def test_wrong_buffer_offset_catches_sentinel_geometry_risk(self):
        self.state_for(0, 1)["streams"][0]["blob"]["byteOffset"] = 32
        self.assert_failure_contains("vertex blob byte offset")

    def test_wrong_index_base_is_rejected(self):
        next(record for record in self.records if record.get("type") == "draw" and record["frame"] == 0 and record["draw"] == 1)["args"]["baseVertexIndex"] = 0
        self.assert_failure_contains("indexed arguments")

    def test_wrong_up_payload_identity_is_rejected(self):
        self.state_for(0, 2)["streams"][0]["resource"] = 101
        self.assert_failure_contains("stream resource identity")

    def test_shader_address_or_wrong_hash_cannot_replace_content_identity(self):
        self.state_for(0, 0)["vertexShader"]["hash"] = "0000000000000000000000000000000000000000"
        self.assert_failure_contains("vertexShader hash")

    def test_transposed_vertex_constants_are_rejected(self):
        state = self.state_for(0, 0)
        reference = state["vsConstants"]["float"]
        data = bytearray((self.path.parent / reference["path"]).read_bytes())
        values = struct.unpack_from("<16f", data)
        transposed = [values[column * 4 + row] for row in range(4) for column in range(4)]
        struct.pack_into("<16f", data, 0, *transposed)
        self.replace_blob(reference, data)
        self.assert_failure_contains("VS c0-c3")

    def test_false_boolean_bank_is_rejected_even_with_valid_blob_hash(self):
        reference = self.state_for(0, 0)["vsConstants"]["bool"]
        self.replace_blob(reference, struct.pack("<I", 0))
        self.assert_failure_contains("vsConstants b0")

    def test_normal_and_uv_contents_are_checked(self):
        reference = self.state_for(0, 6)["streams"][0]["blob"]
        data = bytearray((self.path.parent / reference["path"]).read_bytes())
        struct.pack_into("<f", data, 5 * 4, -1)
        struct.pack_into("<f", data, 6 * 4, 9)
        self.replace_blob(reference, data)
        self.assert_failure_contains("vertex/normal/UV contents")

    def test_recorded_red_tint_must_not_be_effective_before_apply(self):
        self.state_for(0, 4)["psConstants"] = copy.deepcopy(self.state_for(0, 5)["psConstants"])
        self.assert_failure_contains("PS tint / stateblock application")

    def test_nooverwrite_tail_must_include_current_frame_shift(self):
        self.state_for(2, 6)["streams"][0]["blob"] = copy.deepcopy(self.state_for(0, 6)["streams"][0]["blob"])
        self.assert_failure_contains("vertex/normal/UV contents")

    def test_index_update_has_to_reverse_order(self):
        self.state_for(0, 5)["indices"]["blob"] = copy.deepcopy(self.state_for(0, 1)["indices"]["blob"])
        self.state_for(0, 5)["indices"]["blob"]["byteOffset"] = 12
        self.assert_failure_contains("index contents")

    def test_multiply_world_translation_is_not_optional(self):
        self.state_for(0, 7)["transforms"]["world"][12] = 0
        self.assert_failure_contains("World / MultiplyTransform")

    def test_incomplete_capture_must_fail(self):
        self.records.pop()
        self.assert_failure_contains("terminal capture_status")

    def test_missing_draw_is_not_hidden_by_correct_footer_counts(self):
        self.records = [record for record in self.records if not (record.get("type") == "draw" and record["frame"] == 2 and record["draw"] == 6)]
        self.assert_failure_contains("complete draw set")

    def test_state_must_precede_draw(self):
        draw = next(record for record in self.records if record.get("type") == "draw")
        self.records.remove(draw)
        self.records.insert(0, draw)
        self.assert_failure_contains("preceding state record")

    def test_readback_pixels_are_not_claimed(self):
        result = self.verify()
        self.assertTrue(any("pixels are not captured" in item for item in result["limitations"]))


if __name__ == "__main__":
    unittest.main()
