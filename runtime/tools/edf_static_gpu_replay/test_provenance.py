"""Synthetic tests for replay provenance guards; these never execute a GPU replay."""

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from verify_runtime import runtime_provenance, verify


TOOLS = Path(__file__).resolve().parent
TEST_TEMP = TOOLS.parents[1] / "_build" / "test-temp"


class RuntimeProvenanceTests(unittest.TestCase):
    def setUp(self):
        TEST_TEMP.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="provenance-unit-", dir=TEST_TEMP)
        self.directory = Path(self.temporary.name)

    def tearDown(self):
        if not self.directory.resolve().is_relative_to(TEST_TEMP.resolve()):
            raise RuntimeError("Refusing cleanup outside the workspace test-temp directory.")
        self.temporary.cleanup()

    def test_direct_runtime_content_is_hashed(self):
        content = b"Synthetic renderer bytes: this is not a usable DLL."
        (self.directory / "d3d9.dll").write_bytes(content)
        errors = []
        result = runtime_provenance(self.directory, False, errors)
        self.assertEqual(errors, [])
        self.assertTrue(result["runtimeProvenance"]["available"])
        self.assertEqual(result["runtimeProvenance"]["transport"], "direct_x64")
        self.assertEqual(result["runtimeSha256"], hashlib.sha256(content).hexdigest())
        self.assertEqual(result["runtimeSize"], len(content))

    def test_bridge_runtime_does_not_hash_the_proxy(self):
        (self.directory / "d3d9.dll").write_bytes(b"Synthetic x86 proxy")
        server = self.directory / ".trex"
        server.mkdir()
        (server / "NvRemixBridge.exe").write_bytes(b"Synthetic bridge marker")
        content = b"Synthetic x64 renderer"
        (server / "d3d9.dll").write_bytes(content)
        errors = []
        result = runtime_provenance(self.directory, False, errors)
        self.assertEqual(errors, [])
        self.assertEqual(result["runtimeSha256"], hashlib.sha256(content).hexdigest())
        self.assertEqual(result["runtimeProvenance"]["transport"], "x86_bridge_x64_runtime")
        self.assertEqual(Path(result["runtimeBinary"]), server / "d3d9.dll")

    def test_missing_bridge_runtime_has_no_proxy_fallback(self):
        (self.directory / "d3d9.dll").write_bytes(b"Synthetic x86 proxy")
        server = self.directory / ".trex"
        server.mkdir()
        (server / "NvRemixBridge.exe").write_bytes(b"Synthetic bridge marker")
        errors = []
        result = runtime_provenance(self.directory, False, errors)
        self.assertFalse(result["runtimeProvenance"]["available"])
        self.assertIsNone(result["runtimeSha256"])
        self.assertIsNone(result["runtimeSize"])
        self.assertTrue(errors)

    def test_missing_fixture_runtime_is_explicitly_unavailable(self):
        errors = []
        result = runtime_provenance(self.directory, True, errors)
        self.assertEqual(errors, [])
        self.assertFalse(result["runtimeProvenance"]["available"])
        self.assertIsNone(result["runtimeSha256"])
        self.assertIsNone(result["runtimeSize"])
        self.assertTrue(result["runtimeProvenance"]["reason"])

    def test_empty_runtime_is_rejected(self):
        (self.directory / "d3d9.dll").touch()
        errors = []
        result = runtime_provenance(self.directory, False, errors)
        self.assertFalse(result["runtimeProvenance"]["available"])
        self.assertTrue(errors)

    def test_existing_complete_synthetic_fixture_remains_fixture_only(self):
        directory = TOOLS.parents[1] / "_build" / "verify_runtime_unit"
        if not directory.is_dir():
            self.skipTest("The previously generated verifier fixture is unavailable.")
        result = verify(directory, [directory / "runtime.log"],
                        [directory / "capture" / "records.jsonl"],
                        directory / "replay_stdout.jsonl", 0)
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["result"], "passed")
        self.assertTrue(result["fixtureOnly"])
        self.assertTrue(result["notGpuEvidence"])
        self.assertFalse(result["runtimeProvenance"]["available"])
        self.assertIsNone(result["runtimeSha256"])


class FinalizerProvenanceTests(unittest.TestCase):
    def setUp(self):
        TEST_TEMP.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="finalize-unit-", dir=TEST_TEMP)
        self.directory = Path(self.temporary.name)
        self.analysis = self.directory / "analysis"
        candidate = self.analysis / "new_candidate"
        next_dir = self.analysis / "next_validation"
        candidate.mkdir(parents=True)
        next_dir.mkdir()
        (candidate / "validation.json").write_text('{"syntheticTestOnly": true}', encoding="utf-8")
        (next_dir / "validation.json").write_text('{"oldSyntheticTarget": true}', encoding="utf-8")
        self.runtime = self.directory / "d3d9.dll"
        content = b"Synthetic finalizer test bytes, not a usable DLL"
        self.runtime.write_bytes(content)
        self.gpu = {"result": "passed", "errors": [], "fixtureOnly": False,
                    "notGpuEvidence": False, "evidenceKind": "controlled_runtime_replay",
                    "runtimeSha256": hashlib.sha256(content).hexdigest(),
                    "runtimeSize": len(content), "runtimeProvenance": {"available": True},
                    "replayDirectory": str(self.directory / "positive")}
        self.raster = {"result": "passed", "errors": [], "fixtureOnly": False,
                       "notGpuEvidence": False, "evidenceKind": "controlled_replay",
                       "profileDirectory": self.gpu["replayDirectory"]}

    def tearDown(self):
        if not self.directory.resolve().is_relative_to(TEST_TEMP.resolve()):
            raise RuntimeError("Refusing cleanup outside the workspace test-temp directory.")
        self.temporary.cleanup()

    def finalize(self, include_runtime=True):
        gpu_path, raster_path = self.directory / "gpu.json", self.directory / "raster.json"
        gpu_path.write_text(json.dumps(self.gpu), encoding="utf-8")
        raster_path.write_text(json.dumps(self.raster), encoding="utf-8")
        args = [sys.executable, str(TOOLS / "finalize_validation.py"),
                "--analysis", str(self.analysis), "--gpu-verification", str(gpu_path),
                "--raster-verification", str(raster_path)]
        if include_runtime:
            args += ["--runtime", str(self.runtime)]
        return subprocess.run(args, capture_output=True, text=True, check=False)

    def test_matching_runtime_and_positive_directory_accepted(self):
        result = self.finalize()
        self.assertEqual(result.returncode, 0, result.stderr)
        evidence = json.loads((self.analysis / "next_validation" / "validation.json").read_text())
        self.assertEqual(evidence["implementation"]["runtimeSha256"], self.gpu["runtimeSha256"])
        self.assertTrue(evidence["implementation"]["actualEdfRunStillRequired"])

    def test_stale_runtime_hash_rejected(self):
        self.gpu["runtimeSha256"] = "0" * 64
        result = self.finalize()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not match --runtime", result.stderr)

    def test_runtime_size_mismatch_rejected(self):
        self.gpu["runtimeSize"] += 1
        result = self.finalize()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not match --runtime", result.stderr)

    def test_unavailable_runtime_provenance_rejected(self):
        self.gpu["runtimeProvenance"]["available"] = False
        result = self.finalize()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not match --runtime", result.stderr)

    def test_unrelated_positive_raster_directory_rejected(self):
        self.raster["profileDirectory"] = str(self.directory / "other-positive")
        result = self.finalize()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no matching positive GPU replay", result.stderr)

    def test_gpu_evidence_without_runtime_rejected(self):
        result = self.finalize(include_runtime=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("requires --runtime", result.stderr)

    def test_fixture_gpu_evidence_remains_rejected(self):
        self.gpu["fixtureOnly"] = True
        self.gpu["notGpuEvidence"] = True
        self.gpu["evidenceKind"] = "synthetic_verifier_fixture"
        result = self.finalize()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Not a successful controlled GPU replay", result.stderr)

    def test_raster_evidence_with_errors_rejected(self):
        self.raster["errors"] = ["Synthetic negative test error"]
        result = self.finalize()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Not a successful controlled raster comparison", result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
