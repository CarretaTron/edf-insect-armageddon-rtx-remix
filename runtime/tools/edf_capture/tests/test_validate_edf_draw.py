"""Mathematical and packed-format checks, independent of EDF capture contents."""

import math
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from validate_edf_draw import (CaptureError, LAYOUT, decode_inputs, execute_straight_line,
                               fit_camera, mul)


class MemoryStore:
    def read(self, reference):
        return reference["bytes"]


class PackedAndShaderTests(unittest.TestCase):
    def state(self):
        data = [struct.pack("<4B", 0, 127, 254, 255), struct.pack("<4B", 1, 2, 3, 4),
                struct.pack("<4h", -32768, 0, 32767, -1), struct.pack("<4B", 5, 6, 7, 8),
                struct.pack("<2h", -2048, 4096)]
        return {"vertexDeclaration": {"elements": [
            dict(stream=s, offset=o, type=t, usage=u, usageIndex=0, method=0) for s, o, t, u in LAYOUT]},
            "streams": [dict(stream=i, stride=len(b), offset=101, frequency=1,
                             blob={"byteOffset": 101, "bytes": b}) for i, b in enumerate(data)]}

    def test_raw_signed_formats_and_short2_expansion(self):
        value = decode_inputs(self.state(), MemoryStore(), 0)
        self.assertEqual(value["v2"], [-32768, 0, 32767, -1])
        self.assertEqual(value["v4"], [-2048, 4096, 0, 1])
        self.assertEqual(value["v0"], [0, 127, 254, 255])

    def test_reject_out_of_range_and_instancing(self):
        with self.assertRaises(CaptureError):
            decode_inputs(self.state(), MemoryStore(), 1)
        state = self.state()
        state["streams"][2]["frequency"] = 0x80000001
        with self.assertRaises(CaptureError):
            decode_inputs(state, MemoryStore(), 0)

    def test_uv_swizzle_preserves_fog_component(self):
        result, count = execute_straight_line(
            "vs_3_0\nmov o1.x, c0.x\nmul o1.yz, c0.y, v4.xxyw\n",
            {"v4": [-2048, 4096, 0, 1]}, [[0.75, 1 / 2048, 0, 0]])
        self.assertEqual(result["o1"], [0.75, -1, 2, None])
        self.assertEqual(count, 2)

    def test_uninitialized_and_unknown_instructions_rejected(self):
        with self.assertRaises(CaptureError):
            execute_straight_line("mul r0.x, r1.x, c0.x", {}, [[1] * 4])
        with self.assertRaises(CaptureError):
            execute_straight_line("dp4 o0, v0, c0", {"v0": [1] * 4}, [[1] * 4])


class CameraTests(unittest.TestCase):
    def fixture(self):
        theta = 0.37
        basis = [[math.cos(theta), -math.sin(theta), 0],
                 [math.sin(theta), math.cos(theta), 0], [0, 0, 1]]
        camera_local = [3, -2, 8]
        world = [row + [0] for row in basis] + [[0, 0, 0, 1]]
        camera_anchor = mul([camera_local], basis)[0]
        view = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0],
                [-v for v in camera_anchor] + [1]]
        projection = [[1.5, 0, 0, 0], [0, 2, 0, 0], [0, 0, -1.001, -1], [0, 0, -0.25025, 0]]
        matrix = mul(mul(world, view), projection)
        constants = matrix + [row + [0] for row in basis] + [camera_local + [0]] + [[0] * 4] * 2
        return constants, matrix, camera_anchor

    def test_factor_recovers_known_camera_and_projection(self):
        constants, matrix, camera = self.fixture()
        fit, rebuilt = fit_camera(constants)
        self.assertLess(fit["combinedMatrixMaxError"], 1e-12)
        self.assertAlmostEqual(fit["nearEstimate"], 0.25)
        for a, b in zip(camera, fit["cameraAnchor"]):
            self.assertAlmostEqual(a, b)
        for row_a, row_b in zip(matrix, rebuilt):
            for a, b in zip(row_a, row_b):
                self.assertAlmostEqual(a, b)

    def test_wrong_transpose_rejected(self):
        constants, _, _ = self.fixture()
        constants[:4] = list(map(list, zip(*constants[:4])))
        with self.assertRaises(CaptureError):
            fit_camera(constants)

    def test_camera_position_is_an_independent_constraint(self):
        constants, _, _ = self.fixture()
        constants[7] = [0, 0, 0, 0]
        with self.assertRaises(CaptureError):
            fit_camera(constants)


if __name__ == "__main__":
    unittest.main()
