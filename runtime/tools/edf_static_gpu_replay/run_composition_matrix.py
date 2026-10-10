#!/usr/bin/env python3
"""Prepare or serially run bounded native EDF WSI composition fixtures.

All inputs and outputs must stay inside this workspace. The real EDF install is
never accessed. No GPU process starts unless --run is explicit. These fixtures
use measured geometry/shaders/cameras and synthetic textures, not EDF gameplay.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys

import verify_composition
import verify_probe


WORKSPACE = Path(__file__).resolve().parents[3]
REPOSITORY = WORKSPACE / "runtime"
TOOLS = Path(__file__).resolve().parent
VALIDATION_LAYER = "VK_LAYER_KHRONOS_validation"
SYNC_FEATURE = "VK_VALIDATION_FEATURE_ENABLE_SYNCHRONIZATION_VALIDATION_EXT"
CASES = {
    "positive_camera0": {"sourceCamera": 2, "expectedFrame": 1, "expectedComposition": True},
    "positive_camera1": {"sourceCamera": 0, "expectedFrame": 1, "expectedComposition": True},
    "positive_camera2": {"sourceCamera": 1, "expectedFrame": 1, "expectedComposition": True},
    "disabled": {"sourceCamera": 2, "expectedFrame": 1, "expectedComposition": False, "enabled": False},
    "invalid_roi": {"sourceCamera": 2, "expectedFrame": 1, "expectedComposition": False, "panelX": 1919},
    "absent_initial": {"sourceCamera": 2, "expectedFrame": 2, "expectedComposition": True,
                       "arguments": ["--skip-target-frame", "1"], "missingFrame": 1},
    "mutated_initial": {"sourceCamera": 2, "expectedFrame": 2, "expectedComposition": True,
                        "arguments": ["--mutate-target-frame", "1"], "missingFrame": 1},
    "duplicate_initial": {"sourceCamera": 2, "expectedFrame": 2, "expectedComposition": True,
                          "arguments": ["--duplicate-target-frame", "1"]},
    "absent_after": {"sourceCamera": 2, "expectedFrame": 1, "expectedComposition": True,
                     "arguments": ["--skip-target-frame", "2"], "missingFrame": 2},
    "reset_after": {"sourceCamera": 2, "expectedFrame": 1, "expectedComposition": True,
                    "arguments": ["--reset-before-frame", "2"], "resetFrame": 2},
    "nonidentity_gamma": {"sourceCamera": 2, "expectedFrame": 1, "expectedComposition": False,
                          "arguments": ["--nonidentity-gamma"]},
    "partial_present": {"sourceCamera": 2, "expectedFrame": 1, "expectedComposition": False,
                        "arguments": ["--partial-present"]},
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def hash_file(path: Path, algorithm: str = "sha256") -> str:
    digest = hashlib.new(algorithm)
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def contained(path: Path) -> Path:
    result = path.resolve()
    require(result.is_relative_to(WORKSPACE.resolve()), f"Only workspace paths are allowed: {result}.")
    return result


def pe_machine(path: Path) -> int:
    with path.open("rb") as stream:
        require(stream.read(2) == b"MZ", f"Not a PE binary: {path}.")
        stream.seek(0x3c)
        offset = struct.unpack("<I", stream.read(4))[0]
        stream.seek(offset)
        require(stream.read(4) == b"PE\0\0", "Invalid PE signature.")
        return struct.unpack("<H", stream.read(2))[0]


def configure_sync_validation(output: Path, environment: dict, sdk: Path | None = None) -> dict:
    """Configure only the native child, recording the installed layer contract.

    Runtime validation must also be enabled: forcing VK_INSTANCE_LAYERS alone
    does not install its debug callback. The SDK's documented legacy enable is
    retained alongside the explicit modern setting, so sync validation does not
    depend on a user's vkconfig defaults. No system/SDK files are modified.
    """
    sdk = (sdk or Path(environment.get("VULKAN_SDK", "C:/VulkanSDK/1.4.363.0"))).resolve()
    manifest_path = sdk / "Bin/VkLayer_khronos_validation.json"
    layer_path = sdk / "Bin/VkLayer_khronos_validation.dll"
    require(manifest_path.is_file() and layer_path.is_file(), "Installed x64 Vulkan validation layer is required.")
    metadata = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    layer = metadata.get("layer", {})
    require(layer.get("name") == VALIDATION_LAYER and pe_machine(layer_path) == 0x8664,
            "The validation manifest must describe the x64 Khronos layer.")
    settings_tree = json.dumps(layer)
    require('"key": "validate_sync"' in settings_tree and SYNC_FEATURE in settings_tree
            and '"env": "VK_LAYER_ENABLES"' in settings_tree,
            "SDK manifest does not advertise the requested synchronization validation feature.")
    settings_path = output / "vk_layer_settings.txt"
    require(not settings_path.exists(), "Validation settings must be newly created for this case.")
    settings_path.write_text(
        "# Controlled native fixture; no global vkconfig changes.\n"
        "khronos_validation.validate_sync = true\n"
        "khronos_validation.syncval_full_validation = true\n"
        "khronos_validation.syncval_record_time_validation = true\n"
        "khronos_validation.gpuav_enable = false\n"
        "khronos_validation.validate_best_practices = false\n"
        "khronos_validation.report_flags = error,warn,info\n"
        "khronos_validation.debug_action = VK_DBG_LAYER_ACTION_LOG_MSG,VK_DBG_LAYER_ACTION_DEBUG_OUTPUT\n"
        "khronos_validation.enable_message_limit = false\n", encoding="utf-8")
    child_options = {
        "VK_LAYER_PATH": str(manifest_path.parent),
        "VK_INSTANCE_LAYERS": VALIDATION_LAYER,
        "VK_LAYER_ENABLES": SYNC_FEATURE,
        "VK_LAYER_SETTINGS_PATH": str(settings_path),
        "DXVK_ENABLE_VALIDATION_LAYERS": "1",
        "DXVK_ENABLE_VALIDATION_LAYER_EXTENDED_VALIDATION": "0",
        "DXVK_LOG_CALLSTACKS_ON_VALIDATION_LAYER_ERRORS": "0",
    }
    environment.update(child_options)
    return {"requested": True, "syncRequested": True, "childEnvironment": child_options,
            "sdkManifest": str(manifest_path), "sdkManifestSha256": hash_file(manifest_path),
            "sdkLayer": str(layer_path), "sdkLayerSha256": hash_file(layer_path),
            "apiVersion": layer.get("api_version"), "settingsFile": str(settings_path),
            "settingsSha256": hash_file(settings_path), "globalSettingsModified": False,
            "gpuAssistedValidationRequested": False, "bestPracticesRequested": False}


def validation_activation(runtime_log: Path, console_logs: list[Path]) -> dict:
    """Require actual layer activation, separate from requested environment.

    The Runtime's enabled-layer section proves vkCreateInstance used the layer.
    Sync activation needs the layer's own status message, not the settings file
    or an 'available layers' enumeration. A missing banner remains unproven.
    """
    text = runtime_log.read_text(encoding="utf-8-sig", errors="replace")
    enabled = False
    in_enabled = False
    for line in text.splitlines():
        if "Enabled Layer Names:" in line:
            in_enabled = True
            continue
        if in_enabled:
            payload = re.sub(r"^.*?\b(?:info|warn|err):\s*", "", line).strip()
            if payload.startswith("VK_LAYER_"):
                enabled |= payload == VALIDATION_LAYER
            else:
                in_enabled = False
    streams = [runtime_log, *console_logs]
    banners = []
    for path in streams:
        contents = path.read_text(encoding="utf-8-sig", errors="replace")
        for match in re.finditer(r"(?:Khronos Validation Layer Active|Validation Layer.*?Active)[\s\S]{0,2400}", contents, re.I):
            block = re.split(r"Disabled Features:", match.group(0), maxsplit=1, flags=re.I)[0]
            if re.search(r"(?:SYNCHRONIZATION_VALIDATION|Synchronization Validation|validate_sync\s*[:=]\s*true|SYNCVAL)", block, re.I):
                banners.append({"file": str(path), "message": block[:1600]})
    return {"layerActivatedInRuntime": enabled, "syncActivationObserved": bool(banners),
            "syncActivationMessages": banners, "submissionVisibilityProof": False}


def prepare_case(bundle: Path, architecture: str, output: Path, name: str,
                 profile: Path, renderer: Path | None = None) -> dict:
    bundle, output, profile = contained(bundle), contained(output), contained(profile)
    require(name in CASES, "Unknown composition case.")
    require(not output.exists(), "Case output already exists; choose a fresh directory.")
    require(not output.is_relative_to(bundle) and not bundle.is_relative_to(output), "Output overlaps Runtime bundle.")
    require((bundle / "d3d9.dll").is_file(), "Runtime bundle does not contain d3d9.dll.")
    expected_machine = 0x8664 if architecture == "x64" else 0x14c
    require(pe_machine(bundle / "d3d9.dll") == expected_machine, "Bundle D3D9 entry DLL has the wrong architecture.")
    renderer_relative = ".trex/d3d9.dll" if architecture == "x86" else "d3d9.dll"
    require((bundle / renderer_relative).is_file() and pe_machine(bundle / renderer_relative) == 0x8664,
            "The Runtime renderer must be the matching x64 DLL.")
    if architecture == "x86":
        require((bundle / ".trex/NvRemixBridge.exe").is_file(), "The x86 test requires its bundled Bridge server.")
    built = REPOSITORY / "_build/edf_static_gpu_replay" / architecture
    require((built / "edf_static_gpu_replay.exe").is_file() and (built / "replay_fixture.bin").is_file(),
            "Compile the current fixture for this architecture first.")
    require(pe_machine(built / "edf_static_gpu_replay.exe") == expected_machine, "Replay EXE architecture mismatch.")
    for excluded in ("user.conf", "replay_report.json", "replay_stdout.jsonl"):
        require(not (bundle / excluded).exists(), f"Use an unexecuted package bundle, not an old replay: {excluded}.")
    require(not (bundle / "rtx-remix/logs").exists(), "The input bundle contains old Runtime logs.")
    shutil.copytree(bundle, output)
    for filename in ("edf_static_gpu_replay.exe", "replay_fixture.bin"):
        shutil.copy2(built / filename, output / filename)
    if renderer is not None:
        renderer = contained(renderer)
        require(renderer.is_file() and pe_machine(renderer) == 0x8664, "Renderer override must be a workspace x64 DLL.")
        shutil.copy2(renderer, output / renderer_relative)
    settings = CASES[name]
    profile_text = profile.read_text(encoding="utf-8-sig")
    config = profile_text + "\n# Controlled replay overrides; never copy this autorun profile into EDF.\n"
    options = {
        "rtx.edfStatic.executable": '"edf_static_gpu_replay.exe"',
        "rtx.edfStatic.executableSha1": '"' + hash_file(output / "edf_static_gpu_replay.exe", "sha1") + '"',
        "rtx.edfStatic.probe.enabled": "True", "rtx.edfStatic.probe.frameCount": "1",
        "rtx.edfStatic.probe.autorun": "True", "rtx.edfStatic.probe.armKey": "0", "rtx.edfStatic.probe.cancelKey": "0",
        "rtx.edfStatic.probe.autorunStartFrame": "1",
        "rtx.edfStatic.probe.outputDirectory": '"' + (output / "probe-output").as_posix() + '"',
        "rtx.edfStatic.composition.enabled": "True" if settings.get("enabled", True) else "False",
        "rtx.edfStatic.composition.panelX": str(settings.get("panelX", 1040)),
        "rtx.edfStatic.composition.panelY": "800", "rtx.edfStatic.composition.panelWidth": "820",
        "rtx.edfStatic.composition.panelHeight": "200", "rtx.compatCapture.enabled": "False",
    }
    config += "\n".join(f"{key} = {value}" for key, value in options.items()) + "\n"
    (output / "rtx.conf").write_text(config, encoding="utf-8")
    arguments = ["--frames", "4", "--no-control", "--presentation-pattern", "--source-camera", str(settings["sourceCamera"])]
    arguments += settings.get("arguments", [])
    manifest = {"schema": 1, "case": name, "architecture": architecture, "status": "prepared_not_executed",
                "notGpuEvidence": True, "notEdfGameplay": True, "directory": str(output),
                "profile": str(profile), "profileSha256": hash_file(profile), "arguments": arguments,
                "executableSha1": hash_file(output / "edf_static_gpu_replay.exe", "sha1"),
                "runtimeBinary": str(output / renderer_relative), "runtimeSha256Before": hash_file(output / renderer_relative),
                "expected": settings, "activeGameInstallTouched": False}
    (output / "composition_case.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def execute_case(manifest: dict, validation_layer: bool = False, validation_sdk: Path | None = None) -> dict:
    output = contained(Path(manifest["directory"]))
    require(manifest["status"] == "prepared_not_executed", "Case has already executed.")
    environment = os.environ.copy()
    environment["TEMP"] = environment["TMP"] = str(REPOSITORY / "_build/test-temp")
    command = [str(output / "edf_static_gpu_replay.exe"), *manifest["arguments"]]
    result = dict(manifest, status="executed_failed", passed=False, errors=[], notGpuEvidence=False,
                  evidenceKind="controlled_native_fixture_with_synthetic_textures", presentationDisplayVerified=False,
                  realtimePathTracingProven=False, validationLayerRequested=validation_layer)
    try:
        if validation_layer:
            result["validation"] = configure_sync_validation(output, environment, validation_sdk)
        require(hash_file(Path(manifest["runtimeBinary"])) == manifest["runtimeSha256Before"], "Runtime changed before execution.")
        completed = subprocess.run(command, cwd=output, env=environment, capture_output=True, timeout=180,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        (output / "replay_stdout.jsonl").write_bytes(completed.stdout)
        (output / "replay_stderr.log").write_bytes(completed.stderr)
        result["exitCode"] = completed.returncode
        require(completed.returncode == 0, f"Native replay failed with exit code {completed.returncode}.")
        for stream_name in ("replay_stdout.jsonl", "replay_stderr.log"):
            stream_failures = verify_composition.audit_runtime_log(output / stream_name)
            require(not stream_failures, f"GPU validation failure in {stream_name}: {stream_failures}.")
        report = json.loads((output / "replay_report.json").read_text(encoding="utf-8"))
        require(report.get("result") == "passed" and report.get("frames") == 4, "Replay HRESULT report failed.")
        logs = sorted(path for path in output.rglob("remix-dxvk.log") if b"[EDF-Static-Probe]" in path.read_bytes())
        require(len(logs) == 1, "Expected one exact Runtime renderer log.")
        runtime_log = logs[0]
        failures = verify_composition.audit_runtime_log(runtime_log)
        require(not failures, f"Runtime GPU/validation failure: {failures}.")
        if validation_layer:
            result["validation"].update(validation_activation(runtime_log, [output / "replay_stdout.jsonl", output / "replay_stderr.log"]))
            require(result["validation"]["layerActivatedInRuntime"], "Runtime did not report the Khronos layer in Enabled Layer Names.")
            require(result["validation"]["syncActivationObserved"], "The validation layer did not report synchronization validation active; settings alone are not approval.")
        sessions = sorted(path for path in (output / "probe-output").glob("session_*") if path.is_dir())
        require(len(sessions) == 1, "Expected one exact probe session.")
        session = sessions[0]
        probe = verify_probe.verify(session, runtime_log, expected_samples=1,
                                    expected_missing_frame=manifest["expected"].get("missingFrame"))
        probe_path = output / "probe_verification.json"
        probe_path.write_text(json.dumps(probe, indent=2) + "\n", encoding="utf-8")
        result["probeVerification"] = str(probe_path)
        require(probe["passed"], f"Independent probe verification failed: {probe['errors']}.")
        require(len(probe["samples"]) == 1 and probe["samples"][0]["frameId"] == manifest["expected"]["expectedFrame"],
                "Probe did not run in the expected current/recovery frame.")
        composition_files = sorted(session.glob("*_composition.json"))
        events = verify_composition.parse_log(runtime_log)
        if manifest["expected"]["expectedComposition"]:
            require(len(composition_files) == 1, "Expected exactly one bounded WSI composition.")
            composition = verify_composition.verify(session, runtime_log, probe_verification=probe_path,
                                                   runtime_binary=Path(manifest["runtimeBinary"]))
            composition_path = output / "composition_verification.json"
            composition_path.write_text(json.dumps(composition, indent=2) + "\n", encoding="utf-8")
            result["compositionVerification"] = str(composition_path)
            require(composition["passed"], f"WSI byte/CPU preview verification failed: {composition['errors']}.")
            verify_composition.export_images(composition, output / "verified-images", session)
            require(not any(e.get("stage") == "composition_recorded" and e.get("frame") != manifest["expected"]["expectedFrame"]
                            for e in events), "Preview was reused in another frame.")
        else:
            require(not composition_files and not any(e.get("stage") == "composition_recorded" for e in events),
                    "A disabled/rejected preview wrote or recorded a composition.")
            if manifest["case"] == "invalid_roi":
                require(any(e.get("stage") == "preview_rejected" for e in events), "ROI rejection was not observed.")
            elif manifest["case"] in ("nonidentity_gamma", "partial_present"):
                rejected = [e for e in events if e.get("stage") == "composition_rejected"]
                key = "gamma_identity" if manifest["case"] == "nonidentity_gamma" else "presentation_compatible"
                direct_guard = any(e.get(key) == "0" for e in rejected)
                recreation_guard = manifest["case"] == "partial_present" and any(
                    e.get("stage") == "preview_discarded" and e.get("reason") in
                    ("swapchain_recreate", "swapchain_images_recreated") for e in events)
                require(direct_guard or recreation_guard, "Expected presentation/gamma/recreation guard was not observed.")
        stdout_events = [json.loads(line) for line in completed.stdout.decode("utf-8-sig").splitlines() if line.startswith("{")]
        callback_reports = [e for e in stdout_events if e.get("event") == "callbacks_completed"]
        require(len(callback_reports) == 1 and callback_reports[0].get("complete") is True,
                "The native fixture did not confirm capture callback publication before exit.")
        callback_report = callback_reports[0]
        require(callback_report.get("probeRecorded") == callback_report.get("probeCompleted") == 1,
                "The fixture did not wait for the single probe callback.")
        expected_panels = int(manifest["expected"]["expectedComposition"])
        require(callback_report.get("compositionRecorded") == callback_report.get("compositionCompleted") == expected_panels,
                "The fixture did not wait for the expected composition callbacks.")
        result["callbackCompletion"] = callback_report
        if "resetFrame" in manifest["expected"]:
            require(any(e.get("event") == "reset_completed" and e.get("replayFrame") == manifest["expected"]["resetFrame"]
                        and e.get("recreatedDefaultPoolResources") is True for e in stdout_events), "Reset resource recovery was not observed.")
        result["runtimeLog"] = str(runtime_log)
        result["runtimeLogSha256"] = hash_file(runtime_log)
        result["runtimeSha256After"] = hash_file(Path(manifest["runtimeBinary"]))
        require(result["runtimeSha256After"] == manifest["runtimeSha256Before"], "Runtime binary changed during execution.")
        result["status"], result["passed"] = "executed_and_verified", True
    except (ValueError, OSError, KeyError, TypeError, subprocess.TimeoutExpired) as error:
        result["errors"].append(str(error))
    (output / "composition_case_result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--architecture", choices=("x64", "x86"), required=True)
    parser.add_argument("--output", type=Path, required=True, help="Fresh workspace matrix directory.")
    parser.add_argument("--profile", type=Path, default=WORKSPACE / "profiles/edf-pt-probe/rtx.conf")
    parser.add_argument("--renderer", type=Path, help="Optional freshly built x64 renderer override.")
    parser.add_argument("--case", choices=tuple(CASES), action="append", required=True)
    parser.add_argument("--run", action="store_true", help="Explicitly start bounded GPU applications serially.")
    parser.add_argument("--validation-layer", action="store_true")
    parser.add_argument("--validation-sdk", type=Path, help="Read-only installed Vulkan SDK with an x64 Khronos validation layer.")
    args = parser.parse_args()
    output = contained(args.output)
    require(not output.exists(), "Matrix output already exists; choose a fresh directory.")
    require(len(set(args.case)) == len(args.case), "A case may be selected only once.")
    output.mkdir(parents=True)
    lock = WORKSPACE / "runtime/_build/edf_composition_gpu.lock"
    lock_file = None
    try:
        if args.run:
            lock_file = lock.open("x", encoding="utf-8")
            lock_file.write(f"pid={os.getpid()}\noutput={output}\n")
            lock_file.flush()
        results = []
        for name in args.case:
            manifest = prepare_case(args.bundle, args.architecture, output / name, name, args.profile, args.renderer)
            result = execute_case(manifest, args.validation_layer, args.validation_sdk) if args.run else manifest
            results.append(result)
            print(json.dumps({"case": name, "status": result["status"], "passed": result.get("passed"), "errors": result.get("errors", [])}), flush=True)
            if args.run and not result["passed"]:
                break
        summary = {"schema": 1, "architecture": args.architecture, "executed": args.run,
                   "passed": bool(args.run and len(results) == len(args.case) and all(r.get("passed") for r in results)),
                   "notGpuEvidence": not args.run, "notEdfGameplay": True, "cases": results,
                   "activeGameInstallTouched": False, "presentationDisplayVerified": False,
                   "realtimePathTracingProven": False}
        (output / "matrix_verification.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        return 0 if not args.run or summary["passed"] else 1
    finally:
        if lock_file is not None:
            lock_file.close()
            lock.unlink()


if __name__ == "__main__":
    raise SystemExit(main())
