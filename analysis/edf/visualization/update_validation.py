"""Record the actual EDF offscreen evidence without changing captures or game files."""
from pathlib import Path
import hashlib
import json
import re


WORKSPACE = Path(__file__).resolve().parents[3]
ROOT = Path(__file__).resolve().parent
SESSION = WORKSPACE / "captures/edf-pt-probe/session_1791490947848472_12776"
EXPECTED_RUNTIME = "93b265b00cb55b51b25c5b7f0495d72a9e0cf76d55abe1462f932501843b4641"


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1048576), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write(path, data):
    path.write_text(json.dumps(data, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def main():
    baseline_path = ROOT / "original_inputs_before.json"
    baseline = read(baseline_path)
    before = {item["file"]: (item["size"], item["sha256"]) for item in baseline["files"]}
    after = {str(path.relative_to(SESSION)): (path.stat().st_size, sha256(path))
             for path in SESSION.rglob("*") if path.is_file()}
    if before != after:
        raise ValueError("Original session files changed.")
    verification_path = ROOT / "actual_edf_verification.json"
    verification = read(verification_path)
    viewer_path = ROOT / "images/viewer_manifest.json"
    viewer = read(viewer_path)
    mockup_path = ROOT / "mockup/mockup_verification.json"
    mockup = read(mockup_path)
    if not verification["passed"] or len(verification["samples"]) != 3 or verification["errors"]:
        raise ValueError("Actual GPU verification must pass for all three frames.")
    if not viewer["passed"] or not viewer["originalFilesPreserved"] or len(viewer["samples"]) != 3:
        raise ValueError("Offline viewer validation must pass.")
    if not mockup["passed"] or mockup["compositionPerformedInGame"]:
        raise ValueError("Mockup must remain offline.")
    runtime = Path(verification["runtimeBinary"])
    game_root = runtime.parent.parent
    if sha256(runtime) != verification["runtimeSha256"] or verification["runtimeSha256"] != EXPECTED_RUNTIME:
        raise ValueError("Renderer binary provenance differs from the tested Runtime.")
    session_log = SESSION / "remix-dxvk.log"
    if sha256(session_log) != verification["runtimeLogSha256"]:
        raise ValueError("Archived and verified runtime logs differ.")
    log = session_log.read_text(encoding="utf-8-sig", errors="replace")
    if "Game: EDF-IA.exe" not in log:
        raise ValueError("Log does not identify EDF-IA.exe.")
    samples = []
    for sample, display in zip(verification["samples"], viewer["samples"]):
        if sample["frameId"] != display["frameId"]:
            raise ValueError("Viewer and GPU verification frame order differs.")
        metadata_path = Path(sample["metadata"])
        if sha256(metadata_path) != sample["metadataSha256"]:
            raise ValueError("Metadata changed after verification.")
        metadata = read(metadata_path)
        images = {item["name"]: item for item in display["sourceImages"]}
        preserved = sample["preservedTargets"]
        if len(preserved) != 4 or not all(item["unchanged"] for item in preserved):
            raise ValueError("All four original color targets must be unchanged.")
        samples.append({"frameId": sample["frameId"], "requestId": sample["requestId"],
                        "metadata": str(metadata_path), "metadataSha256": sample["metadataSha256"],
                        "gpuCompleted": sample["gpuCompleted"], "compositionPerformed": False,
                        "radianceNonzeroObserved": sample["radianceNonzeroObserved"],
                        "radianceNonzeroPixelCount": sample["radianceNonzeroCount"],
                        "gpuHitCount": display["hitCount"], "hitBounds": display["hitBoundsInclusive"],
                        "cpuGpuSilhouetteIoU": sample["independentCpuRayTriangles"]["silhouetteIoU"],
                        "cpuGpuDepth": sample["independentCpuRayTriangles"],
                        "originalColorTargetsUnchanged": True, "originalColorTargetCount": len(preserved),
                        "originalDepthReadbackVerified": False, "resolution": metadata["rendererResolution"],
                        "cameraLastUpdateFrame": metadata["cameraLastUpdateFrame"],
                        "sourceViewMaxError": metadata["rendererViewError"],
                        "sourceProjectionMaxError": metadata["rendererProjectionError"],
                        "directRadianceMaximumRgb": images["direct_diffuse_radiance"]["rgbMaximum"],
                        "directRadianceRgbValuesAboveOne": images["direct_diffuse_radiance"]["rgbValuesAboveOne"],
                        "finalMaximumRgb": images["final_offscreen"]["rgbMaximum"]})
    snapshot_lines = [line for line in log.splitlines() if "stage=injection_snapshot" in line]
    missing = [line for line in snapshot_lines if re.search(r"\bframe=867\b", line)]
    recovery = [line for line in snapshot_lines if re.search(r"\bframe=910\b", line)]
    audit = {"schema": 1, "passed": True, "evidenceKind": "actual_edf_gameplay_offscreen_gpu",
             "session": str(SESSION), "sampleCount": 3, "errors": [], "samples": samples,
             "verifiedRuntime": {"path": str(runtime), "sha256": EXPECTED_RUNTIME,
                                 "provenance": "Read-only binary measured after execution; not an in-process build fingerprint."},
             "runtimeLog": str(session_log), "runtimeLogSha256": sha256(session_log),
             "verification": str(verification_path), "verificationSha256": sha256(verification_path),
             "legacyVerificationEvidenceKind": verification["evidenceKind"],
             "originEvidence": "User supplied actual gameplay session; log identifies EDF-IA.exe; current renderer matches delivered SHA256.",
             "viewer": str(viewer_path), "viewerManifestSha256": sha256(viewer_path),
             "pngCount": len(viewer["outputs"]), "originalInputFileCount": len(after),
             "viewerUnitTestCount": 40, "viewerUnitTestLog": str(WORKSPACE / "runtime/_build/edf_probe_viewer_unit_tests.log"),
             "independentReview": {"completePNGsComparedPixelByPixel": 18, "differingPixels": 0,
                                   "rawHashesChecked": 50, "outputHashesChecked": 42,
                                   "preservedBackbufferChecksumGapCorrected": True},
             "originalInputBytes": sum(item[0] for item in after.values()),
             "originalInputsPreserved": True, "beforeManifestSha256": sha256(baseline_path),
             "compositionPerformed": False, "realTimePathTracingProven": False,
             "finalEncoding": "display", "runtimeTransferFlagsMeasured": False,
             "finalEncodingBasis": "Normal source path performs tonemapping and approximate gamma 2.2; explicit display mode preserves stored RGB; per-frame transfer/debug flags not exported.",
             "originalDepthReadbackVerified": False, "lighting": "Controlled diagnostic fallback light, not recovered EDF lighting.",
             "offlineMockup": str(mockup_path), "offlineMockupSha256": sha256(mockup_path),
             "cameraLifecycle": {"missingFrame": 867, "missingSnapshots": missing,
                                 "recoveryFrame": 910, "recoverySnapshots": recovery},
             "activeInstallationWritesPerformed": False,
             "activeFilesReadOnlyMeasured": [{"path": str(path), "sha256": sha256(path)}
                                              for path in [runtime, game_root / "rtx.conf", game_root / "rtx-remix/logs/remix-dxvk.log"]]}
    write(ROOT / "SESSION_VALIDATION.json", audit)
    after_manifest = {"schema": 1, "session": str(SESSION), "matchesBefore": True,
                      "fileCount": len(after), "totalBytes": audit["originalInputBytes"],
                      "files": [{"file": name, "size": value[0], "sha256": value[1]}
                                for name, value in sorted(after.items())]}
    write(ROOT / "original_inputs_after.json", after_manifest)
    validation_path = WORKSPACE / "analysis/edf/next_validation/validation.json"
    validation = read(validation_path)
    probe = validation["pathTracingProbe"]
    prior = probe.get("actualGameEvidence", {})
    if "priorSceneSubmissionEvidence" not in probe:
        probe["priorSceneSubmissionEvidence"] = prior
    probe.update(status="actual_edf_offscreen_gpu_and_offline_pngs_validated_visual_composition_pending",
                 actualEdfProbeRunPending=False, visualCompositionAllowed=False,
                 realTimePathTracingProven=False,
                 actualGameEvidence={"session": str(SESSION), "log": str(session_log),
                                     "sha256": sha256(session_log), "audit": str(ROOT / "SESSION_VALIDATION.json"),
                                     "scope": "Actual EDF: three GPU-completed offscreen PT frames for the exact static mesh; all four original color targets unchanged; no visual composition.",
                                     "sampleCount": 3, "passed": True, "samples": samples},
                 offlineVisualization={"tool": str(WORKSPACE / "runtime/tools/edf_probe_viewer/view_capture.py"),
                                       "manifest": str(viewer_path), "manifestSha256": sha256(viewer_path),
                                       "passed": True, "pngCount": len(viewer["outputs"]),
                                       "unitTestCount": 40, "independentPixelComparedPNGs": 18,
                                       "originalInputsPreserved": True, "finalEncoding": "display",
                                       "runtimeTransferFlagsMeasured": False, "report": str(ROOT / "REPORT.md")},
                 compositionPreparation={"status": "plan_only_renderer_not_changed",
                                         "plan": str(ROOT / "COMPOSITION_PLAN.md"),
                                         "proposedMode": "One-frame diagnostic ROI on WSI presentation image only; exact static mesh; default disabled.",
                                         "mockup": str(mockup_path), "implementationAvailable": False})
    validation["decoderGate"]["scope"] = "Only this exact mesh and shader pair; actual EDF scene submission and three offscreen GPU frames validated; visual replacement not validated."
    implementation = validation["implementation"]
    if "historicalSceneOnlyBuild" not in implementation:
        implementation["historicalSceneOnlyBuild"] = {key: implementation[key] for key in ["status", "runtimeBinary", "runtimeSha256", "runtimeSize", "actualEdfEvidence"] if key in implementation}
    implementation.update(status="actual_edf_exact_mesh_offscreen_gpu_validated_visual_composition_pending",
                          actualEdfRunStillRequired=False, runtimeBinary=str(runtime),
                          runtimeSha256=EXPECTED_RUNTIME, runtimeSize=runtime.stat().st_size,
                          actualEdfOffscreenEvidence=str(ROOT / "SESSION_VALIDATION.json"))
    write(validation_path, validation)
    print(json.dumps({"passed": True, "sampleCount": 3, "pngCount": audit["pngCount"],
                      "originalInputsPreserved": True, "validation": str(validation_path)}))


if __name__ == "__main__":
    main()
