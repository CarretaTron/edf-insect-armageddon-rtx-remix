"""Runner preparation/contract fixtures. No test launches a GPU process."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

import run_composition_matrix as matrix


TEST_TEMP = Path(__file__).resolve().parents[2] / "_build/test-temp"


def fake_pe(path: Path, machine: int) -> None:
    content = bytearray(256)
    content[:2] = b"MZ"
    struct.pack_into("<I", content, 0x3c, 128)
    content[128:132] = b"PE\0\0"
    struct.pack_into("<H", content, 132, machine)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


class MatrixPreparationTests(unittest.TestCase):
    def setUp(self):
        TEST_TEMP.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="composition-matrix-unit-", dir=TEST_TEMP)
        self.directory = Path(self.temporary.name)
        self.repo = self.directory / "runtime"
        self.bundle = self.directory / "bundle"
        self.profile = self.directory / "profile.conf"
        self.profile.write_text('rtx.edfStatic.executable = "EDF-IA.exe"\nrtx.edfStatic.probe.autorun = False\n', encoding="utf-8")
        self.profile_hash = hashlib.sha256(self.profile.read_bytes()).hexdigest()
        fake_pe(self.bundle / "d3d9.dll", 0x8664)
        for arch, machine in (("x64", 0x8664), ("x86", 0x14c)):
            built = self.repo / "_build/edf_static_gpu_replay" / arch
            fake_pe(built / "edf_static_gpu_replay.exe", machine)
            (built / "replay_fixture.bin").write_bytes(b"UNIT_FIXTURE_NOT_GPU_INPUT")
        self.patcher = patch.object(matrix, "REPOSITORY", self.repo)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self.temporary.cleanup()

    def prepare(self, name="positive_camera0", architecture="x64", output=None):
        return matrix.prepare_case(self.bundle, architecture, output or self.directory / name, name, self.profile)

    def test_preparation_is_not_gpu_evidence_and_cannot_launch_process(self):
        with patch.object(matrix.subprocess, "run", side_effect=AssertionError("GPU must not launch")):
            result = self.prepare()
        self.assertTrue(result["notGpuEvidence"])
        self.assertTrue(result["notEdfGameplay"])
        self.assertEqual(result["status"], "prepared_not_executed")
        self.assertFalse(result["activeGameInstallTouched"])

    def test_three_positive_camera_offsets_match_frame_one(self):
        for camera in range(3):
            case = matrix.CASES[f"positive_camera{camera}"]
            self.assertEqual((case["sourceCamera"] + case["expectedFrame"]) % 3, camera)
            self.assertEqual(case["expectedFrame"], 1)

    def test_profile_preserved_and_replay_fingerprint_is_measured(self):
        result = self.prepare()
        output = Path(result["directory"])
        config = (output / "rtx.conf").read_text(encoding="utf-8")
        self.assertIn('rtx.edfStatic.executable = "edf_static_gpu_replay.exe"', config)
        self.assertIn(f'rtx.edfStatic.executableSha1 = "{result["executableSha1"]}"', config)
        self.assertIn("rtx.edfStatic.probe.autorunStartFrame = 1", config)
        self.assertIn("rtx.edfStatic.probe.frameCount = 1", config)
        self.assertIn("rtx.edfStatic.probe.autorun = True", config)
        self.assertIn("rtx.compatCapture.enabled = False", config)
        self.assertEqual(hashlib.sha256(self.profile.read_bytes()).hexdigest(), self.profile_hash)

    def test_disabled_case_has_no_composition_authorization(self):
        result = self.prepare("disabled")
        config = (Path(result["directory"]) / "rtx.conf").read_text(encoding="utf-8")
        self.assertIn("rtx.edfStatic.composition.enabled = False", config)
        self.assertFalse(result["expected"]["expectedComposition"])

    def test_invalid_roi_is_explicitly_out_of_bounds(self):
        result = self.prepare("invalid_roi")
        config = (Path(result["directory"]) / "rtx.conf").read_text(encoding="utf-8")
        self.assertIn("rtx.edfStatic.composition.panelX = 1919", config)
        self.assertFalse(result["expected"]["expectedComposition"])

    def test_negative_frames_run_after_swapchain_warmup(self):
        for name, flag in (("absent_initial", "--skip-target-frame"), ("mutated_initial", "--mutate-target-frame"),
                           ("duplicate_initial", "--duplicate-target-frame")):
            case = matrix.CASES[name]
            self.assertEqual(case["arguments"], [flag, "1"])
            self.assertEqual(case["expectedFrame"], 2)

    def test_reset_and_late_absence_follow_composed_frame(self):
        self.assertEqual(matrix.CASES["reset_after"]["arguments"], ["--reset-before-frame", "2"])
        self.assertEqual(matrix.CASES["absent_after"]["arguments"], ["--skip-target-frame", "2"])

    def test_existing_output_not_overwritten(self):
        self.prepare()
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.prepare()

    def test_output_inside_original_bundle_rejected(self):
        with self.assertRaisesRegex(ValueError, "overlaps"):
            self.prepare(output=self.bundle / "case")

    def test_output_outside_workspace_rejected(self):
        with self.assertRaisesRegex(ValueError, "workspace"):
            self.prepare(output=Path("C:/Windows/edf-matrix-not-written"))

    def test_old_logs_are_not_copied_to_fresh_case(self):
        (self.bundle / "rtx-remix/logs").mkdir(parents=True)
        with self.assertRaisesRegex(ValueError, "old Runtime logs"):
            self.prepare()

    def test_old_user_config_rejected(self):
        (self.bundle / "user.conf").write_text("old config", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "unexecuted package"):
            self.prepare()

    def test_wrong_entry_architecture_rejected(self):
        fake_pe(self.bundle / "d3d9.dll", 0x14c)
        with self.assertRaisesRegex(ValueError, "wrong architecture"):
            self.prepare()

    def test_x86_requires_matching_x64_bridge_renderer(self):
        fake_pe(self.bundle / "d3d9.dll", 0x14c)
        with self.assertRaisesRegex(ValueError, "matching x64 DLL"):
            self.prepare(architecture="x86")
        fake_pe(self.bundle / ".trex/d3d9.dll", 0x8664)
        with self.assertRaisesRegex(ValueError, "Bridge server"):
            self.prepare(architecture="x86")
        fake_pe(self.bundle / ".trex/NvRemixBridge.exe", 0x8664)
        result = self.prepare(architecture="x86")
        self.assertTrue(result["runtimeBinary"].endswith(".trex\\d3d9.dll") or result["runtimeBinary"].endswith(".trex/d3d9.dll"))

    def test_replay_four_frames_and_pattern_are_bounded(self):
        result = self.prepare()
        self.assertEqual(result["arguments"][:2], ["--frames", "4"])
        self.assertIn("--presentation-pattern", result["arguments"])
        self.assertIn("--no-control", result["arguments"])

    def test_sync_validation_is_configured_only_for_child_and_measured(self):
        sdk = self.directory / "sdk"
        fake_pe(sdk / "Bin/VkLayer_khronos_validation.dll", 0x8664)
        metadata = {"layer": {"name": matrix.VALIDATION_LAYER, "api_version": "TEST_NOT_NATIVE",
                             "settings": [{"key": "validate_sync"}, {"env": "VK_LAYER_ENABLES", "flags": [matrix.SYNC_FEATURE]}]}}
        (sdk / "Bin/VkLayer_khronos_validation.json").write_text(json.dumps(metadata), encoding="utf-8")
        case = self.directory / "sync_case"
        case.mkdir()
        environment = {"ORIGINAL_CHILD_VALUE": "preserved"}
        result = matrix.configure_sync_validation(case, environment, sdk)
        self.assertEqual(environment["DXVK_ENABLE_VALIDATION_LAYERS"], "1")
        self.assertEqual(environment["DXVK_ENABLE_VALIDATION_LAYER_EXTENDED_VALIDATION"], "0")
        self.assertEqual(environment["VK_LAYER_ENABLES"], matrix.SYNC_FEATURE)
        self.assertEqual(environment["ORIGINAL_CHILD_VALUE"], "preserved")
        self.assertFalse(result["globalSettingsModified"])
        settings = Path(result["settingsFile"]).read_text(encoding="utf-8")
        self.assertIn("validate_sync = true", settings)
        self.assertIn("gpuav_enable = false", settings)
        self.assertEqual(result["settingsSha256"], matrix.hash_file(Path(result["settingsFile"])))

    def test_settings_and_available_layer_list_do_not_prove_activation(self):
        log = self.directory / "inactive.log"
        log.write_text("info: Available layers:\ninfo: VK_LAYER_KHRONOS_validation\n"
                       "info: Enabled Layer Names:\ninfo: Required extensions:\n"
                       "validate_sync=true\n", encoding="utf-8")
        result = matrix.validation_activation(log, [])
        self.assertFalse(result["layerActivatedInRuntime"])
        self.assertFalse(result["syncActivationObserved"])

    def test_runtime_layer_and_sdk_enabled_feature_banner_are_required(self):
        log = self.directory / "active.log"
        log.write_text("info: Enabled Layer Names:\ninfo:   VK_LAYER_KHRONOS_validation\n"
                       "info: Required extensions:\n", encoding="utf-8")
        console = self.directory / "sdk-console.log"
        console.write_text("Khronos Validation Layer Active:\n  Enabled Features:\n"
                           "    VK_VALIDATION_FEATURE_ENABLE_SYNCHRONIZATION_VALIDATION_EXT\n"
                           "  Disabled Features:\n", encoding="utf-8")
        result = matrix.validation_activation(log, [console])
        self.assertTrue(result["layerActivatedInRuntime"])
        self.assertTrue(result["syncActivationObserved"])
        self.assertFalse(result["submissionVisibilityProof"])

    def test_sync_feature_listed_as_disabled_does_not_count(self):
        log = self.directory / "disabled-sync.log"
        log.write_text("info: Enabled Layer Names:\ninfo: VK_LAYER_KHRONOS_validation\n"
                       "info: Required extensions:\nKhronos Validation Layer Active:\n"
                       "Enabled Features:\nDisabled Features:\nSYNCVAL\n", encoding="utf-8")
        result = matrix.validation_activation(log, [])
        self.assertTrue(result["layerActivatedInRuntime"])
        self.assertFalse(result["syncActivationObserved"])


if __name__ == "__main__":
    unittest.main()
