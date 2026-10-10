#!/usr/bin/env python3
"""Verify one exact EDF mesh composed into WSI, independently of the game targets.

Byte comparisons and a CPU preview reference verify GPU readbacks. Recorded
semaphore fields describe the submission contract and do not independently prove
GPU queue ordering. Native execution and source/validation-layer review remain
necessary. Synthetic unit fixtures are explicitly labelled as non-GPU evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
from typing import Any

import numpy as np

from verify_probe import FileAudit, GEOMETRY_HASH, MESH, PS, TRACE, VS, require


KEY_VALUE = re.compile(r"\b([A-Za-z_]\w*)=([^\s]+)")
IMAGE_NAMES = {"wsi_before", "wsi_after", "expected_panel", "scratch_before"}
FAILED_STAGES = {"composition_failed", "composition_readback_failed", "composition_recording_failed",
                 "preview_generation_failed", "readback_failed", "probe_failed"}
FATAL_LOG_PATTERN = re.compile(
    r"SYNC-HAZARD-[A-Z_]+|VUID-[A-Za-z0-9_-]+|VK_ERROR_DEVICE_LOST|"
    r"\b(?:READ|WRITE|PRESENT)_(?:AFTER|RACING)_[A-Z_]+\s+hazard\s+detected\b|"
    r"\b(?:err|error)\s*:.*?\[VK_DEBUG_REPORT\]|"
    r"command submission failed|failed to (?:submit|wait for (?:a )?fence)|"
    r"(?:vkQueueSubmit|vkQueuePresent|vkWaitForFences|vkGetFenceStatus).*?(?:failed|error)|"
    r"SPIR-V validation failed|shader compilation failed|unhandled exception", re.IGNORECASE)
BACKGROUND = np.array([16, 16, 20, 255], dtype=np.uint8)
BORDER = np.array([0, 220, 255, 255], dtype=np.uint8)


def integer(value: Any, name: str, minimum: int = 0) -> int:
    require(isinstance(value, int) and not isinstance(value, bool) and value >= minimum,
            f"Invalid integer {name}.")
    return value


def rectangle(value: Any, width: int, height: int, name: str) -> tuple[int, int, int, int]:
    require(isinstance(value, list) and len(value) == 4, f"Invalid {name} rectangle.")
    x, y, w, h = (integer(v, name) for v in value)
    require(w > 0 and h > 0 and x + w <= width and y + h <= height,
            f"Out of range {name} rectangle.")
    return x, y, w, h


def fit_rectangle(crop_width: int, crop_height: int, width: int, height: int) -> tuple[int, int, int, int]:
    """Fit with four-pixel padding using deterministic integer aspect arithmetic."""
    inner_width, inner_height = width - 8, height - 8
    require(inner_width > 0 and inner_height > 0, "Panel is smaller than its border.")
    if inner_width * crop_height <= inner_height * crop_width:
        fw = inner_width
        fh = max(1, crop_height * inner_width // crop_width)
    else:
        fh = inner_height
        fw = max(1, crop_width * inner_height // crop_height)
    return ((width - fw) // 2, (height - fh) // 2, fw, fh)


def cpu_panel(final: np.ndarray, depth: np.ndarray, miss: float,
              crop: tuple[int, int, int, int], width: int, height: int,
              fit: tuple[int, int, int, int] | None = None) -> np.ndarray:
    """Reconstruct the nearest-sampled display preview from the frame's raw images."""
    require(final.shape == (*depth.shape, 4), "Final/depth shape mismatch.")
    require(np.all(np.isfinite(final)) and np.all(np.isfinite(depth)), "Nonfinite preview source.")
    require(np.all((final[:, :, :3] >= 0) & (final[:, :, :3] <= 1)),
            "Display output exceeds the expected saturated range.")
    x, y, cw, ch = crop
    fx, fy, fw, fh = fit or fit_rectangle(cw, ch, width, height)
    require(fw > 0 and fh > 0 and fx >= 2 and fy >= 2
            and fx + fw <= width - 2 and fy + fh <= height - 2,
            "Invalid preview fit rectangle.")
    panel = np.empty((height, width, 4), dtype=np.uint8)
    panel[:] = BACKGROUND
    panel[:2] = panel[-2:] = BORDER
    panel[:, :2] = panel[:, -2:] = BORDER
    # floor((local - fitStart + .5) * cropSize / fitSize).
    sx = x + np.minimum(cw - 1, ((2 * np.arange(fw, dtype=np.int64) + 1) * cw // (2 * fw)))
    sy = y + np.minimum(ch - 1, ((2 * np.arange(fh, dtype=np.int64) + 1) * ch // (2 * fh)))
    colors = final[sy[:, None], sx[None, :]].astype(np.float64)
    z = depth[sy[:, None], sx[None, :]]
    hit = np.isfinite(z) & (z != np.float32(miss))
    quantized = np.rint(np.clip(colors, 0, 1) * 255).astype(np.uint8)
    quantized[:, :, 3] = 255
    target = panel[fy:fy + fh, fx:fx + fw]
    target[hit] = quantized[hit]
    return panel


def parse_log(path: Path) -> list[dict]:
    events = []
    for index, line in enumerate(path.read_text(encoding="utf-8-sig", errors="replace").splitlines(), 1):
        if "[EDF-Static-Composition]" in line or "[EDF-Static-Probe]" in line:
            event = dict(KEY_VALUE.findall(line))
            event.update(line=index, raw=line)
            if "frame" in event:
                event["frame"] = int(event["frame"])
            events.append(event)
    return events


def audit_runtime_log(path: Path) -> list[dict]:
    """Reject GPU/validation failures even when a callback emitted completion."""
    failures = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8-sig", errors="replace").splitlines(), 1):
        match = FATAL_LOG_PATTERN.search(line)
        if match:
            failures.append({"line": line_number, "matched": match.group(0), "text": line[:1000]})
    return failures


def read_image(directory: Path, data: dict, auditor: FileAudit) -> tuple[np.ndarray, dict]:
    width = integer(data.get("width"), "image width", 1)
    height = integer(data.get("height"), "image height", 1)
    require(data.get("format") == 37 and data.get("bytesPerPixel") == 4,
            "WSI/panel evidence must be tightly packed RGBA8 UNORM.")
    require(data.get("byteCount") == width * height * 4, "Image byte count differs from its extent.")
    require(data.get("checksumAlgorithm") == "fnv1a64", "Unsupported image checksum algorithm.")
    raw, report = auditor.read(directory, data.get("file"), data["byteCount"], data.get("checksum"))
    if "sha256" in data:
        require(data["sha256"] == report["sha256"], "Image SHA256 mismatch.")
    return np.frombuffer(raw, dtype=np.uint8).reshape(height, width, 4), report


def verify_targets(directory: Path, targets: Any, auditor: FileAudit,
                   width: int, height: int) -> list[dict]:
    require(isinstance(targets, list) and len(targets) == 4
            and {t.get("slot") for t in targets} == {0, 1, 2, 3},
            "Composition preservation does not cover all four original game targets.")
    require(len({t.get("sessionId") for t in targets}) == 4,
            "Original target identities must be unique.")
    result = []
    for target in targets:
        require(target.get("format") in (37, 44) and target.get("width") == width
                and target.get("height") == height and target.get("byteCount") == width * height * 4,
                "Original target format or extent mismatch.")
        require(target.get("unchanged") is True and target.get("comparison") == "full_memcmp",
                "Original target preservation is not confirmed by full comparison.")
        before, before_report = auditor.read(directory, target.get("beforeFile"), target["byteCount"], target.get("beforeFnv1a64"))
        after, after_report = auditor.read(directory, target.get("afterFile"), target["byteCount"], target.get("afterFnv1a64"))
        require(before == after, "Composition changed an original game target.")
        result.append({"slot": target["slot"], "sessionId": target["sessionId"],
                       "unchanged": True, "before": before_report, "after": after_report})
    return result


def verify_sample(path: Path, auditor: FileAudit, events: list[dict],
                  fixture_only: bool = False) -> dict:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    require(data.get("schema") == 1 and data.get("traceId", TRACE) == TRACE,
            "Unknown composition schema/trace.")
    require(data.get("gpuCompleted") is True and data.get("compositionPerformed") is True,
            "Composition GPU completion is not confirmed.")
    frame = integer(data.get("rtxFrame"), "RTX frame")
    request = integer(data.get("requestId"), "request", 1)
    for name in ("generatedFrame", "cameraFrame", "presentedFrame"):
        require(integer(data.get(name), name) == frame, "Composition uses a stale or mismatched frame.")
    require(data.get("loopIndex") == 0, "Preview must use the single initial presentation iteration.")
    integer(data.get("presentSerial"), "present serial")
    integer(data.get("wsiImageIdentity"), "WSI image identity", 1)
    width, height = integer(data.get("width"), "width", 1), integer(data.get("height"), "height", 1)
    require(fixture_only or (width, height) == (1920, 1080), "Unvalidated native WSI resolution.")
    require(data.get("format") == 37, "Unexpected WSI format.")
    x, y, w, h = rectangle(data.get("roi"), width, height, "ROI")
    require(16 <= w <= 820 and 16 <= h <= 200 and w * h <= width * height // 4,
            "ROI exceeds the bounded preview area.")
    colour = data.get("colour", {})
    require(colour.get("performSRGBConversion") is True and colour.get("captureScreenImage") is False
            and colour.get("debugViewIndex") == 0 and colour.get("finalEncoding") == "display_gamma22_approx",
            "Preview transfer/debug output semantics are not the validated display path.")
    probe_filename = data.get("probeMetadata", f"edf_pt_request_{request}_frame_{frame}.json")
    require(isinstance(probe_filename, str) and probe_filename, "Missing source probe metadata filename.")
    probe_path = (path.parent / probe_filename).resolve()
    require(probe_path.is_relative_to(path.parent.resolve()), "Probe metadata path escapes composition session.")
    probe = json.loads(probe_path.read_text(encoding="utf-8-sig"))
    require(probe.get("frameId") == frame and probe.get("requestId") == request
            and probe.get("cameraLastUpdateFrame") == frame and probe.get("currentFrameDrawCount") == 1
            and probe.get("gpuCompleted") is True, "Offscreen source is not the current validated frame.")
    require(probe.get("traceId") == TRACE and probe.get("compositionPerformed") is False
            and probe.get("rasterPreservedByDesign") is True and probe.get("originalTargetsUnchanged") is True,
            "Offscreen source preservation/trace audit is not approved.")
    whitelist = probe.get("whitelist", {})
    if "whitelist" in data:
        require(data["whitelist"] == whitelist, "Offscreen/composition whitelist differs.")
    require(whitelist.get("sourceMeshKey") == MESH and whitelist.get("decoderVertexShader") == VS
            and whitelist.get("decoderPixelShader") == PS and whitelist.get("geometryHash") == GEOMETRY_HASH
            and whitelist.get("vertices") == 252 and whitelist.get("indices") == 432
            and whitelist.get("orderedInstances") == 1,
            "Composition mesh/shader/count whitelist failed.")
    images = data.get("images", [])
    require(len(images) == 4 and {entry.get("name") for entry in images} == IMAGE_NAMES,
            "Missing or duplicate WSI/panel readback.")
    decoded, reports = {}, []
    for entry in images:
        image, report = read_image(path.parent, entry, auditor)
        expected_shape = (height, width, 4) if entry["name"].startswith("wsi_") else (h, w, 4)
        require(image.shape == expected_shape, "Readback has wrong WSI/panel dimensions.")
        decoded[entry["name"]] = image
        reports.append(dict(report, name=entry["name"]))
    before, after = decoded["wsi_before"], decoded["wsi_after"]
    changed = np.any(before != after, axis=2)
    outside = changed.copy()
    outside[y:y + h, x:x + w] = False
    require(not outside.any(), f"WSI changed {int(outside.sum())} pixels outside ROI.")
    require(changed[y:y + h, x:x + w].any(), "Preview did not change any ROI pixel.")
    require(np.array_equal(decoded["scratch_before"], before[y:y + h, x:x + w]),
            "Scratch does not preserve the original WSI ROI.")
    require(np.array_equal(decoded["expected_panel"], after[y:y + h, x:x + w]),
            "Presented ROI differs from the GPU panel texture.")
    for key, value in (("outsideChangedPixels", int(outside.sum())),
                       ("insideChangedPixels", int(changed[y:y + h, x:x + w].sum())),
                       ("panelMismatchPixels", 0)):
        if key in data:
            require(data[key] == value, "Reported WSI difference statistics do not match raw pixels.")
    require(data.get("originalTargetsUnchanged", True) is True and data.get("passed", True) is True,
            "Runtime composition audit reported failure.")
    for key, value in (("compositionQueueSubmitCount", 2), ("baseWaitAcquire", 1),
                       ("baseSignalPresent", 0), ("overlaySignalPresent", 1),
                       ("frameLatencySignalAfterOverlay", True)):
        require(data.get(key) == value, f"Recorded submission contract mismatch: {key}.")
    preservation = verify_targets(path.parent, data.get("preservedTargets"), auditor, width, height)
    source_images = {entry["name"]: entry for entry in probe.get("images", [])}
    source = {}
    for name, fmt, bpp, dtype in (("final_offscreen", 97, 8, "<f2"), ("linear_z", 100, 4, "<f4")):
        entry = source_images.get(name, {})
        require(entry.get("format") == fmt and entry.get("bytesPerPixel") == bpp
                and entry.get("width") == width and entry.get("height") == height
                and entry.get("byteCount") == width * height * bpp, "Invalid current-frame RTX source.")
        raw, _ = auditor.read(path.parent, entry.get("file"), entry["byteCount"], entry.get("checksum"))
        source[name] = np.frombuffer(raw, dtype=dtype).reshape((height, width, 4) if bpp == 8 else (height, width))
    crop = rectangle(data.get("crop"), width, height, "crop")
    mask = np.isfinite(source["linear_z"]) & (source["linear_z"] != np.float32(probe["missLinearZ"]))
    rows, cols = np.nonzero(mask)
    require(len(rows) > 0, "Current-frame preview has no geometry hits.")
    require(data.get("gpuHitCount") == int(mask.sum()) and data.get("gpuDepthNonfiniteCount") == 0
            and data.get("gpuFinalNonfiniteCount") == 0,
            "GPU bounds hit/nonfinite count differs from current source pixels.")
    require(float(data.get("missLinearZ")) == float(probe["missLinearZ"]), "Composition miss sentinel differs from source.")
    min_x, min_y = max(0, int(cols.min()) - 8), max(0, int(rows.min()) - 8)
    max_x, max_y = min(width - 1, int(cols.max()) + 8), min(height - 1, int(rows.max()) + 8)
    require(crop == (min_x, min_y, max_x - min_x + 1, max_y - min_y + 1),
            "GPU crop does not match current-frame depth hits and eight-pixel margin.")
    fit = fit_rectangle(crop[2], crop[3], w, h)
    if "fit" in data:
        require(tuple(data["fit"]) == fit, "Reported aspect fit differs from the independent CPU reference.")
    reference = cpu_panel(source["final_offscreen"], source["linear_z"], probe["missLinearZ"], crop, w, h, fit)
    difference = np.abs(reference.astype(np.int16) - decoded["expected_panel"].astype(np.int16))
    # UNORM conversion rounding can differ by one at half-integer boundaries.
    require(np.max(difference) <= 1 and np.array_equal(reference[:, :, 3], decoded["expected_panel"][:, :, 3]),
            "GPU preview differs from the independent nearest/depth/quantization reference.")
    sample_events = [e for e in events if e.get("frame") == frame and int(e.get("request_id", request)) == request]
    for stage in ("composition_recorded", "presentation_queued", "composition_gpu_completed"):
        require(len([e for e in sample_events if e.get("stage") == stage]) == 1,
                f"Expected one frame-local {stage} event.")
    queued = next(e for e in sample_events if e.get("stage") == "presentation_queued")
    require(int(queued.get("present_serial", -1)) == data["presentSerial"] and queued.get("loop_index") == "0"
            and queued.get("present_signal_after_overlay") == "1" and queued.get("frame_latency_after_overlay") == "1",
            "Actual presentation submission log does not match the composition source/latency contract.")
    return {"passed": True, "metadata": str(path.resolve()),
            "metadataSha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "frameId": frame, "requestId": request, "roi": [x, y, w, h], "crop": list(crop), "fit": list(fit),
            "gpuCompleted": True, "compositionPerformed": True,
            "presentationQueued": True, "presentationDisplayVerified": False,
            "outsideRoiChangedPixels": 0, "insideRoiChangedPixels": int(changed[y:y + h, x:x + w].sum()),
            "cpuPanelMaximumByteDifference": int(difference.max()), "images": reports,
            "preservedTargets": preservation, "originalTargetsUnchanged": True}


def verify(directory: Path, runtime_log: Path, expected_samples: int = 1,
           fixture_only: bool = False, metadata_files: list[Path] | None = None,
           probe_verification: Path | None = None, runtime_binary: Path | None = None) -> dict:
    result = {"schema": 1, "passed": False, "evidenceKind": "synthetic_fixture" if fixture_only else "controlled_wsi_runtime_execution",
              "fixtureOnly": fixture_only, "notGpuEvidence": fixture_only, "errors": [], "samples": [],
              "realtimePathTracingProven": False, "spatialObjectReplacementProven": False}
    try:
        require(expected_samples >= 1, "Expected sample count must be positive.")
        events = parse_log(runtime_log)
        result["runtimeLogFailures"] = audit_runtime_log(runtime_log)
        require(not result["runtimeLogFailures"], "Runtime log contains a GPU submission/device/synchronization/validation failure.")
        result["runtimeLog"] = str(runtime_log.resolve())
        result["runtimeLogSha256"] = hashlib.sha256(runtime_log.read_bytes()).hexdigest()
        require(not any(e.get("stage") in FAILED_STAGES for e in events),
                "Runtime reports a composition/readback failure.")
        files = metadata_files if metadata_files is not None else sorted(directory.rglob("*_composition.json"))
        require(len(files) == expected_samples, "Unexpected composition sample count.")
        require(len({p.parent.resolve() for p in files}) == 1, "Select one exact composition session.")
        auditor = FileAudit()
        for file in files:
            try:
                result["samples"].append(verify_sample(file, auditor, events, fixture_only))
            except (ValueError, KeyError, OSError, TypeError, OverflowError) as error:
                result["errors"].append(f"{file.name}: {error}")
        require(len({sample["frameId"] for sample in result["samples"]}) == len(result["samples"]), "Duplicate successful composition frame.")
        if not fixture_only:
            require(probe_verification is not None, "Native composition verification requires the independent probe verification JSON.")
            probe_result = json.loads(probe_verification.read_text(encoding="utf-8-sig"))
            require(probe_result.get("passed") is True and not probe_result.get("fixtureOnly", True)
                    and not probe_result.get("notGpuEvidence", True), "Source probe verification is unapproved or synthetic.")
            source_samples = {sample["metadataSha256"]: sample for sample in probe_result.get("samples", [])}
            for sample in result["samples"]:
                metadata = json.loads(Path(sample["metadata"]).read_text(encoding="utf-8-sig"))
                source_path = Path(sample["metadata"]).parent / metadata["probeMetadata"]
                source_digest = hashlib.sha256(source_path.read_bytes()).hexdigest()
                require(source_digest in source_samples and source_samples[source_digest].get("passed") is True
                        and source_samples[source_digest].get("frameId") == sample["frameId"]
                        and source_samples[source_digest].get("requestId") == sample["requestId"],
                        "Composition source is absent from the verified independent probe samples.")
            result["probeVerification"] = str(probe_verification.resolve())
            result["probeVerificationSha256"] = hashlib.sha256(probe_verification.read_bytes()).hexdigest()
            measured_runtime = runtime_binary or Path(probe_result.get("runtimeBinary", ""))
            require(measured_runtime.is_file(), "Runtime renderer provenance is unavailable.")
            runtime_hash = hashlib.sha256(measured_runtime.read_bytes()).hexdigest()
            require(runtime_hash == probe_result.get("runtimeSha256"), "Runtime renderer changed since independent probe verification.")
            result["runtimeBinary"] = str(measured_runtime.resolve())
            result["runtimeSha256"] = runtime_hash
        result["passed"] = not result["errors"] and len(result["samples"]) == expected_samples
    except (ValueError, KeyError, OSError, TypeError, OverflowError) as error:
        result["errors"].append(str(error))
    result["limits"] = ["A successful result verifies GPU-completed current-frame WSI pixels and original target readbacks.",
                        "Readbacks do not independently prove semaphore ordering; inspect native submission source and GPU validation output.",
                        "The panel is a diagnostic preview, not an occlusion-correct object replacement or a continuous performance test.",
                        "Synthetic fixtures exercise the verifier and are never reported as GPU or EDF gameplay evidence."]
    return result


def export_images(result: dict, directory: Path, session_directory: Path) -> dict:
    """Write lossless views of already verified raw RGBA bytes outside captures."""
    require(result.get("passed") is True, "Images are exported only from a passing verification.")
    target, source = directory.resolve(), session_directory.resolve()
    require(not target.is_relative_to(source) and not source.is_relative_to(target),
            "Image output overlaps original capture directory.")
    require(not target.exists(), "Image output directory already exists.")
    from PIL import Image
    target.mkdir(parents=True)
    files = []
    for sample in result["samples"]:
        metadata = json.loads(Path(sample["metadata"]).read_text(encoding="utf-8-sig"))
        stem = f"request_{sample['requestId']}_frame_{sample['frameId']}"
        before, after = None, None
        for descriptor, report in zip(metadata["images"], sample["images"]):
            raw = Path(report["path"]).read_bytes()
            require(hashlib.sha256(raw).hexdigest() == report["sha256"], "Verified raw image changed before export.")
            image = np.frombuffer(raw, dtype=np.uint8).reshape(descriptor["height"], descriptor["width"], 4)
            filename = target / f"{stem}_{descriptor['name']}.png"
            Image.fromarray(image).save(filename)
            files.append({"file": str(filename), "sha256": hashlib.sha256(filename.read_bytes()).hexdigest(),
                          "source": report["path"], "sourceSha256": report["sha256"]})
            if descriptor["name"] == "wsi_before":
                before = image
            elif descriptor["name"] == "wsi_after":
                after = image
        changes = np.any(before != after, axis=2)
        difference = np.zeros_like(before)
        difference[:, :, 3] = 255
        difference[changes, :3] = [255, 64, 0]
        filename = target / f"{stem}_changed_pixels.png"
        Image.fromarray(difference).save(filename)
        files.append({"file": str(filename), "sha256": hashlib.sha256(filename.read_bytes()).hexdigest(),
                      "meaning": "Orange pixels have at least one different RGBA byte; black pixels are identical."})
    manifest = {"schema": 1, "files": files, "sourceInputsModified": False,
                "presentationDisplayVerified": False, "interpretation": "Raw GPU WSI readbacks, not desktop screenshots or scanout evidence."}
    (target / "image_exports.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--runtime-log", type=Path, required=True)
    parser.add_argument("--expected-samples", type=int, default=1)
    parser.add_argument("--metadata", type=Path, action="append")
    parser.add_argument("--probe-verification", type=Path, required=True)
    parser.add_argument("--runtime", type=Path)
    parser.add_argument("--image-output", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    require(output.suffix.lower() == ".json", "Verification output must be a JSON file.")
    require(not output.exists(), "Verification output already exists; choose a new file.")
    result = verify(args.directory, args.runtime_log, args.expected_samples, metadata_files=args.metadata,
                    probe_verification=args.probe_verification, runtime_binary=args.runtime)
    if args.image_output is not None and result["passed"]:
        result["imageExports"] = export_images(result, args.image_output, args.directory)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"passed": result["passed"], "sampleCount": len(result["samples"]),
                      "errors": result["errors"], "output": str(output)}))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
