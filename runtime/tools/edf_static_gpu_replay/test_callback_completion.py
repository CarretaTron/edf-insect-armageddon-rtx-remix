"""Native CPU-only callback log audits; never load D3D9 or submit GPU work."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import unittest


REPOSITORY = Path(__file__).resolve().parents[2]
TEST_TEMP = REPOSITORY / "_build/test-temp"
PROBE_RECORD = "info: [EDF-Static-Probe] stage=readback_recorded frame=2 request_id=1\n"
PROBE_DONE = "info: [EDF-Static-Probe] stage=gpu_completed frame=2 request_id=1\n"
PANEL_RECORD = "info: [EDF-Static-Composition] stage=composition_recorded frame=2 request_id=1\n"
PANEL_DONE = "info: [EDF-Static-Composition] stage=composition_gpu_completed frame=2 request_id=1 passed=1\n"


class CallbackLogTests(unittest.TestCase):
    def setUp(self):
        TEST_TEMP.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="callback-cpu-unit-", dir=TEST_TEMP)
        self.directory = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def audit(self, contents: str, expected_exit: int):
        log = self.directory / "runtime.log"
        log.write_text(contents, encoding="utf-8")
        reports = []
        for architecture in ("x64", "x86"):
            executable = REPOSITORY / "_build/edf_static_gpu_replay" / architecture / "edf_static_gpu_replay.exe"
            self.assertTrue(executable.is_file(), "Compile both current replay architectures first.")
            result = subprocess.run([str(executable), "--audit-callback-log", str(log)], cwd=self.directory,
                                    capture_output=True, timeout=10,
                                    creationflags=subprocess.CREATE_NO_WINDOW)
            self.assertEqual(result.returncode, expected_exit, result.stderr.decode("utf-8", errors="replace"))
            if result.stdout:
                report = json.loads(result.stdout.decode("utf-8"))
                self.assertEqual(report["event"], "callback_log_audit")
                reports.append(report)
        return reports

    def test_disabled_capture_zero_records_can_finish(self):
        for report in self.audit("info: Original raster only\n", 0):
            self.assertTrue(report["complete"])
            self.assertEqual(report["probeRecorded"], 0)

    def test_gpu_completion_waits_for_both_callback_publications(self):
        for report in self.audit(PROBE_RECORD + PANEL_RECORD + PROBE_DONE + PANEL_DONE, 0):
            self.assertTrue(report["complete"])
            self.assertEqual(report["compositionCompleted"], 1)

    def test_gpu_completed_probe_does_not_complete_pending_panel(self):
        for report in self.audit(PROBE_RECORD + PANEL_RECORD + PROBE_DONE, 2):
            self.assertFalse(report["complete"])
            self.assertEqual(report["compositionCompleted"], 0)

    def test_unterminated_last_line_is_still_in_progress(self):
        for report in self.audit(PROBE_RECORD + PANEL_RECORD + PROBE_DONE + PANEL_DONE.rstrip("\n"), 2):
            self.assertFalse(report["complete"])

    def test_completion_for_wrong_frame_or_request_cannot_match(self):
        self.audit(PROBE_RECORD + PROBE_DONE.replace("frame=2", "frame=3"), 1)
        self.audit(PROBE_RECORD + PROBE_DONE.replace("request_id=1", "request_id=2"), 1)

    def test_duplicated_or_unrecorded_callbacks_reject(self):
        self.audit(PROBE_RECORD + PROBE_DONE + PROBE_DONE, 1)
        self.audit(PANEL_DONE, 1)

    def test_missing_identity_rejects(self):
        self.audit(PROBE_RECORD.replace(" request_id=1", ""), 1)

    def test_runtime_readback_failures_are_never_waited_as_success(self):
        self.audit(PROBE_RECORD + "err: [EDF-Static-Probe] stage=readback_failed frame=2 request_id=1\n", 1)
        self.audit(PANEL_RECORD + "err: [EDF-Static-Composition] stage=composition_readback_failed frame=2\n", 1)

    def test_gpu_validation_failures_reject_despite_completed_callbacks(self):
        for failure in ("SYNC-HAZARD-WRITE_AFTER_WRITE", "VUID-vkCmdCopyImage-01995", "VK_ERROR_DEVICE_LOST", "command submission failed"):
            self.audit(PROBE_RECORD + PROBE_DONE + "err: " + failure + "\n", 1)

    def test_ordinary_guard_rejections_do_not_fail_callback_completion(self):
        for report in self.audit(PROBE_RECORD + PROBE_DONE +
                                 "info: [EDF-Static] stage=injection_rejected reason=already_injected_this_frame\n", 0):
            self.assertTrue(report["complete"])

    def test_sdk_hazard_without_sync_identifier_rejects_completed_callback(self):
        for failure in (
                "[09:48:28.024] err:   [VK_DEBUG_REPORT] Code 1544472022: vkCmdBuildAccelerationStructuresKHR(): WRITE_AFTER_WRITE hazard detected.",
                "info: READ_AFTER_WRITE hazard detected.",
                "warning: WRITE_AFTER_READ hazard detected.",
                "info: WRITE_RACING_WRITE hazard detected."):
            self.audit(PROBE_RECORD + PROBE_DONE + failure + "\n", 1)

    def test_debug_report_error_rejects_without_hazard_or_vuid(self):
        self.audit(PROBE_RECORD + PROBE_DONE +
                   "[09:48:27.598] err:   [VK_DEBUG_REPORT] Code 861854874: vkCmdBeginRenderPass(): dependencyCount is incompatible.\n", 1)

    def test_debug_report_layer_information_is_not_an_error(self):
        self.audit(PROBE_RECORD + PROBE_DONE +
                   'info: [VK_DEBUG_REPORT] Code 0: Inserted device layer "VK_LAYER_KHRONOS_validation"\n', 0)


if __name__ == "__main__":
    unittest.main()
