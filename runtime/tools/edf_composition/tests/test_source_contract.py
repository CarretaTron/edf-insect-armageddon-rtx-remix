"""Static EDF WSI contracts; these checks do not execute or validate the GPU.

Run directly with --report to persist the audit, or use unittest discovery.
The checks intentionally fail when the bounded two-list submission contract
changes, including a future reactivation of automatic memcpy submissions.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import unittest
from datetime import datetime, timezone


RUNTIME = Path(__file__).resolve().parents[3]
REPORT = RUNTIME.parent / "analysis/edf/composition/source_audit.json"
FILES = {
    "swapchain": "src/d3d9/d3d9_swapchain.cpp",
    "composition": "src/dxvk/rtx_render/rtx_edf_static_composition.cpp",
    "composition_header": "src/dxvk/rtx_render/rtx_edf_static_composition.h",
    "context": "src/dxvk/dxvk_context.cpp",
    "presenter": "src/vulkan/vulkan_presenter.cpp",
    "presenter_header": "src/vulkan/vulkan_presenter.h",
    "present_shader": "src/dxvk/shaders/rtx/pass/edf_static_composition/edf_static_composition_present.comp.slang",
}


def without_comments(source: str) -> str:
    # Keep quoted literals intact; braces or comment syntax inside them must
    # not interfere with function/block boundaries.
    token = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|//[^\n]*|/\*.*?\*/', re.S)
    return token.sub(lambda match: " " if match[0].startswith(("//", "/*")) else match[0], source)


def block(source: str, anchor: str) -> str:
    match = re.search(anchor, source, re.S)
    if match is None:
        raise AssertionError(f"Missing source anchor: {anchor}")
    start = source.index("{", match.end())
    # Mask literals solely for brace counting, preserving all offsets.
    masked = re.sub(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'',
                    lambda match: " " * len(match[0]), source)
    depth = 0
    for index in range(start, len(source)):
        if masked[index] == "{":
            depth += 1
        elif masked[index] == "}":
            depth -= 1
            if not depth:
                return source[start + 1:index]
    raise AssertionError(f"Unbalanced source block: {anchor}")


class SourceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.raw = {name: (RUNTIME / path).read_bytes() for name, path in FILES.items()}
        cls.source = {name: without_comments(data.decode("utf-8")) for name, data in cls.raw.items()}
        cls.submit = block(cls.source["swapchain"], r"void D3D9SwapChainEx::SubmitPresent\(")
        cls.overlay = block(cls.submit, r"if\s*\(cDeferFrameLatency\)")
        cls.compose = block(cls.source["composition"], r"bool EdfStaticComposition::composeWSI\(")
        cls.shader = block(cls.source["present_shader"], r"void main\(")

    def ordered(self, source: str, *needles: str) -> None:
        offset = 0
        for needle in needles:
            index = source.find(needle, offset)
            self.assertNotEqual(index, -1, f"Missing or out-of-order: {needle}")
            offset = index + len(needle)

    def test_01_acquire_wait_belongs_to_base_list(self) -> None:
        """Base waits for the acquired WSI image and does not signal Present."""
        self.ordered(self.overlay,
                     "submitCommandList(cCommandList, cSync.acquire, VK_NULL_HANDLE)",
                     "submitCommandList(overlayCommands, VK_NULL_HANDLE, cSync.present)")
        self.assertNotIn("submitCommandList(cCommandList, cSync.acquire, cSync.present)", self.overlay)

    def test_02_present_signal_belongs_to_final_list(self) -> None:
        """Present is queued only after the final overlay submission."""
        self.ordered(self.submit,
                     "submitCommandList(overlayCommands, VK_NULL_HANDLE, cSync.present)",
                     "stage=presentation_queued", "m_device->presentImage(")

    def test_03_frame_latency_belongs_to_final_list(self) -> None:
        """Latency completion must cover the overlay and its readbacks."""
        self.ordered(self.overlay,
                     "overlayContext->signal(cFrameLatencySignal, cPresentSerial)",
                     "auto overlayCommands = overlayContext->endRecording()",
                     "submitCommandList(cCommandList", "submitCommandList(overlayCommands")
        guarded_signal = block(self.source["swapchain"],
                               r"if\s*\(i \+ 1 >= SyncInterval && !deferFrameLatency\)")
        self.assertIn("m_context->signal(m_frameLatencySignal, m_frameId)", guarded_signal)

    def test_04_cross_list_dependency_precedes_wsi_access(self) -> None:
        """Prior raster/RTX writes are visible before overlay reads and writes."""
        barrier = re.search(
            r"emitMemoryBarrier\(0,\s*VK_PIPELINE_STAGE_ALL_COMMANDS_BIT,\s*"
            r"VK_ACCESS_MEMORY_WRITE_BIT,\s*VK_PIPELINE_STAGE_ALL_COMMANDS_BIT,\s*"
            r"VK_ACCESS_MEMORY_READ_BIT\s*\|\s*VK_ACCESS_MEMORY_WRITE_BIT\)", self.compose)
        self.assertIsNotNone(barrier)
        self.assertLess(barrier.start(), self.compose.index("copyImageToBuffer("))

    def test_05_storage_write_uses_general_layout(self) -> None:
        """Storage descriptor creation is followed by GENERAL before dispatch."""
        self.ordered(self.compose,
                     "createView(pContext->getDevice().ptr(), image, VK_IMAGE_USAGE_STORAGE_BIT)",
                     "changeImageLayout(image, VK_IMAGE_LAYOUT_GENERAL)",
                     "bindResourceView(EDF_COMPOSITION_OUTPUT, presentationView", "pContext->dispatch(")

    def test_06_wsi_returns_to_present_after_readbacks(self) -> None:
        """Image returns to PRESENT before registering completion."""
        self.ordered(self.compose, "pContext->dispatch(",
                     "copyImageToBuffer(job->images[1].buffer", "copyBuffer(job->bounds",
                     "changeImageLayout(image, VK_IMAGE_LAYOUT_PRESENT_SRC_KHR)",
                     "VK_PIPELINE_STAGE_HOST_BIT, VK_ACCESS_HOST_READ_BIT", "pContext->signal(fence, 1)")

    def test_07_default_off_and_exact_executable_profile(self) -> None:
        """Only an explicit option and a matching executable enable WSI hooks."""
        self.assertRegex(self.source["composition_header"],
                         r'RTX_OPTION\("rtx\.edfStatic\.composition",\s*bool,\s*enabled,\s*false,')
        self.assertIn("m_edfCompositionProfileAllowed = EdfStaticComposition::enabled() && EdfStaticProfile::matchExecutable()",
                      self.source["swapchain"])

    def test_08_shader_writes_only_bounded_roi(self) -> None:
        """Out-of-panel threads return and the only store uses the ROI offset."""
        guard = block(self.shader, r"if\s*\(threadId\.x >= cb\.panelWidth \|\| threadId\.y >= cb\.panelHeight")
        self.assertRegex(guard, r"^\s*return;\s*$")
        self.assertEqual(len(re.findall(r"Presentation\s*\[", self.shader)), 1)
        self.ordered(self.shader, "return;",
                     "Presentation[threadId + uint2(cb.sourceWidth, cb.sourceHeight)] = Panel.Load(int3(threadId, 0))")

    def test_09_invalid_gpu_mask_does_not_write_wsi(self) -> None:
        """Empty geometry or nonfinite depth/final colour rejects the store."""
        self.ordered(self.shader, "Bounds[4] == 0 || Bounds[5] != 0 || Bounds[6] != 0", "return;", "Presentation[")

    def test_10_usage_reflects_supported_presenter_capabilities(self) -> None:
        """Diagnostic SRC usage is optional and accurately mirrored by WSI."""
        self.assertRegex(self.source["presenter_header"], r"VkImageUsageFlags\s+optionalImageUsage\s*=\s*0;")
        self.assertIn("swapInfo.imageUsage |= desc.optionalImageUsage & caps.supportedUsageFlags", self.source["presenter"])
        self.assertIn("m_info.imageUsage = swapInfo.imageUsage", self.source["presenter"])
        self.assertIn("imageInfo.usage = info.imageUsage", self.source["swapchain"])
        self.assertIn("(image->info().usage & kRequiredUsage) != kRequiredUsage", self.compose)

    def assert_memcpy_auto_submit_disabled(self, source: str) -> None:
        body = block(source, r"void DxvkContext::recordGPUMemCopy\(uint32_t bytes\)")
        self.assertRegex(body, r"^\s*return;", "Automatic memcpy submission would bypass the base acquire/list order")

    def test_11_memcpy_cannot_auto_submit_overlay(self) -> None:
        """Readback helpers cannot submit a partial overlay before its base."""
        context = self.source["context"]
        self.assert_memcpy_auto_submit_disabled(context)
        body = block(context, r"void DxvkContext::recordGPUMemCopy\(uint32_t bytes\)")
        self.assertIn("flushCommandList()", body)
        # Verify the contract actually detects future reactivation, rather
        # than passing because a dormant flush call is not inspected.
        reactivated = context.replace(body, body.replace("return;", "", 1), 1)
        with self.assertRaises(self.failureException):
            self.assert_memcpy_auto_submit_disabled(reactivated)

    def test_12_recreate_drains_cached_profile_gpu_work(self) -> None:
        """Destroying WSI waits for prior experiment work even after disable."""
        recreate = block(self.source["swapchain"], r"void D3D9SwapChainEx::RecreateSwapChain\(")
        guard = block(recreate, r"if\s*\(m_edfCompositionProfileAllowed && !m_context->isDLFGEnabled\(\)\)")
        self.assertNotIn("EdfStaticComposition::enabled()", guard)
        self.ordered(guard, "m_parent->EmitCs(", 'invalidateEdfStaticComposition("swapchain_recreate")',
                     "m_parent->SynchronizeCsThread()", "m_device->waitForIdle()")
        self.ordered(recreate, "m_device->waitForIdle()", "GetPresenter()->recreateSwapChain(")

    def test_13_exception_layout_restore_is_noexcept_and_host_only(self) -> None:
        """Discarded recording restores bookkeeping without new GPU commands."""
        destructor = block(self.source["composition"], r"~RestoreImageLayout\(\) noexcept")
        self.assertRegex(destructor, r"^\s*image->setLayout\(layout\);\s*$")
        self.ordered(self.compose, "const RestoreImageLayout restoreLayout",
                     "changeImageLayout(image, VK_IMAGE_LAYOUT_GENERAL)")
        self.ordered(self.overlay, 'composition->fail("composition_recording_exception")',
                     "overlayContext->endRecording()", "overlayContext = new DxvkContext(m_device)")

    def test_14_producer_failure_preserves_raster_fallback(self) -> None:
        """Frame/camera/geometry guards and producer exception fallback remain."""
        source = self.source["composition"]
        producer = block(source, r"void EdfStaticComposition::preparePreview\(")
        self.assertRegex(source, r"void EdfStaticComposition::preparePreview\([^{}]+\) try\s*\{")
        for guard in ("source.frameId != frameId", "source.cameraFrame != frameId",
                      "source.geometryHash != 0x32aeb7bb7e772c13ull", "EdfStaticProbe::frameCount() != 1"):
            self.assertIn(guard, producer)
        self.ordered(source[source.index("void EdfStaticComposition::preparePreview"):],
                     "catch (const std::exception& error)", 'fail("preview_generation_exception")',
                     "stage=preview_generation_failed", "raster_fallback=1 reason=")

    def assert_context_retained_until_completion(self, source: str) -> None:
        self.assertRegex(source, r"overlayLifetimeFence->setCallback\(1,\s*\[overlayContext\]\s*\{\s*\}\)")
        self.ordered(source, "Rc<sync::CallbackFence> overlayLifetimeFence",
                     "overlayLifetimeFence->setCallback(", "overlayContext->signal(overlayLifetimeFence, 1)",
                     "auto overlayCommands = overlayContext->endRecording()", "submitCommandList(overlayCommands")

    def test_15_overlay_context_descriptor_pool_survives_until_gpu_completion(self) -> None:
        """The temporary context owns its active descriptor pool until fence notification."""
        self.assert_context_retained_until_completion(self.overlay)
        end_recording = block(self.source["context"], r"Rc<DxvkCommandList> DxvkContext::endRecording\(")
        self.assertIn("return std::exchange(m_cmd, nullptr)", end_recording)
        # Empty capture destroys the context/pool immediately after recording,
        # reproducing VUID-vkQueueSubmit-pCommandBuffers-00070 from the real test.
        without_context_capture = self.overlay.replace("[overlayContext]", "[]", 1)
        with self.assertRaises(self.failureException):
            self.assert_context_retained_until_completion(without_context_capture)


class AuditResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.contracts = []

    def addSuccess(self, test):
        super().addSuccess(test)
        self.contracts.append({"id": test._testMethodName, "passed": True, "purpose": test.shortDescription()})

    def addFailure(self, test, error):
        super().addFailure(test, error)
        self.contracts.append({"id": test._testMethodName, "passed": False, "error": self._exc_info_to_string(error, test)})

    def addError(self, test, error):
        super().addError(test, error)
        self.contracts.append({"id": str(test), "passed": False, "error": self._exc_info_to_string(error, test)})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, help="Write only analysis/edf/composition/source_audit.json")
    args = parser.parse_args()
    if args.report is not None and args.report.resolve() != REPORT.resolve():
        parser.error(f"Report output must be {REPORT}")
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(SourceContractTests)
    result = unittest.TextTestRunner(verbosity=2, resultclass=AuditResult).run(suite)
    snapshots = getattr(SourceContractTests, "raw", {})
    unchanged = all((RUNTIME / FILES[name]).read_bytes() == data for name, data in snapshots.items())
    if args.report is not None:
        report = {
            "schema": 1,
            "generatedUtc": datetime.now(timezone.utc).isoformat(),
            "scope": "static_source_contracts_only",
            "passed": result.wasSuccessful() and result.testsRun == 15 and unchanged,
            "testCount": result.testsRun,
            "passedCount": sum(item["passed"] for item in result.contracts),
            "sourcesUnchangedDuringAudit": unchanged,
            "gpuExecuted": False,
            "gameInstallationModified": False,
            "presentationDisplayVerified": False,
            "depthStencilBinaryVerified": False,
            "commandPowerShell": "& 'D:\\EDF IA RTX REMIX\\.tools\\build-python\\Scripts\\python.exe' -B 'D:\\EDF IA RTX REMIX\\runtime\\tools\\edf_composition\\tests\\test_source_contract.py' --report 'D:\\EDF IA RTX REMIX\\analysis\\edf\\composition\\source_audit.json'",
            "contracts": sorted(result.contracts, key=lambda item: item["id"]),
            "sources": [{"path": FILES[name], "sha256": hashlib.sha256(data).hexdigest()} for name, data in sorted(snapshots.items())],
            "limitations": [
                "Text-level contracts support review; they do not prove Vulkan execution or display correctness.",
                "Actual synchronization/WSI validation and visual approval still require controlled GPU and EDF tests.",
                "Depth/stencil preservation is structural (no experiment access), not a binary depth/stencil checksum.",
                "getCompositionSource(...) argument evaluation occurs before the preparePreview function-try-block; an allocation exception there remains a standard failure path.",
            ],
        }
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return 0 if result.wasSuccessful() and unchanged else 1


if __name__ == "__main__":
    sys.exit(main())
