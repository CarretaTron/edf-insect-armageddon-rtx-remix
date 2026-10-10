#!/usr/bin/env python3
"""Verify the selected EDF mesh through a controlled Runtime/Bridge GPU replay."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "edf_capture"))
from analyze_capture import BlobStore, CaptureError, read_records, validate_geometry


TRACE_ID = 0x4544465354415449
SOURCE_FRAMES = (755, 1182, 1292)
POSITION_SHA1 = "f3ddbe4c53df3aea29e2cad8d305844c95c7dc4c"
INDEX_SHA1 = "2ecc0d2d1046f418d56ea51bbcca29579a04a937"
STREAM_SHA1 = {
    0: "392f544f133f951dea21d8177cf549c553608827",
    1: "7c6fcffa660096291a371651cd73a85bfa5aa1ce",
    2: POSITION_SHA1,
    3: "d6b757b9a822e87a7983c8dca148d55ff813ed77",
    4: "f4d8a45daf8dcb614756f4bbd45a13d18e390b49",
}
VS_SHA1 = "f098f457d9ae603bea5f84ec985d473c29cfb0c9"
PS_SHA1 = "44b92a29eb3e5f2ee6d53f02e14fb11a01511da8"
CONSTANT_SHA1 = {
    755: ("8efec5f4f679852fa8bc047f30d22ae1656e392a", "f7d6a527de811abbd97b780dbd610ce00db918d7"),
    1182: ("4197781f14e595d6b7def6b2c290691b7cade10c", "778ddec2cebdb665892542d40ff0e147edd18638"),
    1292: ("b9a8904201959c76e13e822372fd8bf1c902f774", "f7eb6821329405149ac4e0a24923b22d944f0ea5"),
}
HASH_FIELDS = ("position_hash", "index_hash", "uv_hash", "geometry_hash", "material_hash")
KEY_VALUE = re.compile(r"\b([A-Za-z_]\w*)=([^\s]+)")
PREFIX = "[RTX-Compatibility-Trace]"
KNOWN_STAGES = {
    "recognized", "raster_geometry", "camera_accepted", "camera_rejected",
    "scene_geometry", "scene_rejected", "instance_accepted", "instance_preserved",
    "instance_rejected", "blas_recorded", "blas_reused", "blas_rejected",
    "tlas_recorded", "tlas_rejected", "injection_rejected", "scene_only_frame",
}


def integer(value: Any) -> int:
    return int(value, 0) if isinstance(value, str) else int(value)


def runtime_provenance(directory: Path, fixture_only: bool,
                       errors: list[str]) -> dict[str, Any]:
    """Identify the renderer DLL, never the x86 Bridge proxy, for this replay."""
    bridge = any((directory / relative).is_file() for relative in
                 (".trex/NvRemixBridge.exe", "NvRemixBridge.exe"))
    runtime = directory / (".trex/d3d9.dll" if bridge else "d3d9.dll")
    provenance = {"available": False,
                  "transport": "x86_bridge_x64_runtime" if bridge else "direct_x64",
                  "path": str(runtime.resolve()),
                  "measurement": "replay_directory_binary_after_execution",
                  "reason": None}
    result = {"runtimeBinary": str(runtime.resolve()), "runtimeSha256": None,
              "runtimeSize": None, "runtimeProvenance": provenance}
    try:
        digest = hashlib.sha256()
        size = 0
        with runtime.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
                size += len(block)
        if not size:
            raise ValueError("Runtime DLL is empty.")
        result.update(runtimeSha256=digest.hexdigest(), runtimeSize=size)
        provenance["available"] = True
    except (OSError, ValueError) as error:
        provenance["reason"] = str(error)
        if not fixture_only:
            errors.append(f"Runtime DLL provenance unavailable: {runtime}: {error}")
    return result


def references(value: Any):
    if isinstance(value, dict):
        if {"path", "hash", "size"} <= value.keys():
            yield value
        for child in value.values():
            yield from references(child)
    elif isinstance(value, list):
        for child in value:
            yield from references(child)


def audit_capture(path: Path, errors: list[str]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    records, parse_errors = read_records(path)
    errors.extend(f"{path}: {message}" for message in parse_errors)
    kinds = Counter(record.get("type") for record in records)
    begins = [record for record in records if record.get("type") == "capture_begin"]
    ends = [record for record in records if record.get("type") == "capture_end"]
    statuses = [record for record in records if record.get("type") == "capture_status"]
    if kinds["capture_begin"] != 1 or kinds["capture_end"] != 1 or kinds["capture_status"] != 1:
        errors.append(f"{path}: requires exactly one begin/end/status.")
    status = statuses[-1] if statuses else {}
    end = ends[-1] if ends else {}
    if not records or records[-1].get("type") != "capture_status":
        errors.append(f"{path}: missing terminal status.")
    if not records or records[0].get("type") != "capture_begin":
        errors.append(f"{path}: missing initial capture_begin.")
    if status.get("truncated") is not False or status.get("error") is not None:
        errors.append(f"{path}: truncated capture or recorder error: {status}.")
    if end.get("reason") != "window_complete":
        errors.append(f"{path}: did not reach window_complete.")
    if begins and begins[0].get("schema") != 1:
        errors.append(f"{path}: unsupported recorder schema.")
    if status.get("written_records") != len(records) - 1:
        errors.append(f"{path}: written record count mismatch.")
    bounds = {key: integer(status.get(key, -1)) for key in
              ("peak_queue_bytes", "max_queue_bytes", "accepted_bytes", "max_bytes")}
    if any(value < 0 for value in bounds.values()) or not bounds["max_queue_bytes"] or not bounds["max_bytes"]:
        errors.append(f"{path}: missing or invalid queue/budget counters.")
    if bounds["peak_queue_bytes"] > bounds["max_queue_bytes"]:
        errors.append(f"{path}: peak queue exceeds its configured bound.")
    if bounds["accepted_bytes"] > bounds["max_bytes"]:
        errors.append(f"{path}: accepted bytes exceed the capture budget.")

    store = BlobStore(path.parent)
    used = set()
    for record in records:
        for reference in references(record):
            try:
                store.read(reference)
                used.add((path.parent / reference["path"]).resolve())
            except (CaptureError, OSError, ValueError) as error:
                errors.append(f"{path}: {error}")
    written = set((path.parent / "blobs").glob("*.bin"))
    written = {blob.resolve() for blob in written}
    for blob in written:
        if hashlib.sha1(blob.read_bytes()).hexdigest() != blob.stem.lower():
            errors.append(f"{path}: written blob hash mismatch: {blob.name}.")
    if written - used:
        errors.append(f"{path}: orphan written blobs: {len(written - used)}.")
    if status.get("written_blobs") != len(written):
        errors.append(f"{path}: written blob count mismatch.")
    shaders = {record.get("hash"): record for record in records if record.get("type") == "shader"}
    for shader_hash in (VS_SHA1, PS_SHA1):
        shader = shaders.get(shader_hash)
        if not shader or shader.get("bytecode", {}).get("hash") != shader_hash:
            errors.append(f"{path}: missing verified original shader bytecode {shader_hash}.")

    states = {record["id"]: record["state"] for record in records if record.get("type") == "state"}
    results = {(record["frame"], record["draw"]): record for record in records
               if record.get("type") == "draw_result"}
    if len(results) != kinds["draw_result"]:
        errors.append(f"{path}: duplicate draw_result identity.")
    decisions = defaultdict(list)
    for record in records:
        if record.get("type") == "event" and record.get("api") == "RemixDecision":
            decisions[(record["frame"], record["args"]["draw"])].append(record["args"].get("reason"))
    draws = []
    for record in records:
        if record.get("type") != "draw":
            continue
        key = (record["frame"], record["draw"])
        state = states.get(record.get("stateId"))
        if state is None or key not in results:
            errors.append(f"{path}: missing state/result for draw {key}.")
            continue
        draw = {**record, "state": state}
        try:
            geometry, _, _ = validate_geometry(draw, store)
            if geometry["errors"] or geometry["instanced"] or geometry["uniqueVertexCount"] != 252:
                errors.append(f"{path}: invalid captured geometry for {key}: {geometry}.")
            streams = {stream["stream"]: stream for stream in state["streams"]}
            content = {stream: streams[stream]["blob"]["hash"] for stream in STREAM_SHA1}
            draws.append({"frame": key[0], "draw": key[1], "api": record["api"],
                          "args": record["args"], "inputRangeValid": record.get("inputRangeValid"),
                          "flags": results[key]["prepareFlags"], "positionSha1": content[2],
                          "streamSha1": content, "indexSha1": state["indices"]["blob"]["hash"],
                          "vertexShader": state["vertexShader"]["hash"],
                          "pixelShader": state["pixelShader"]["hash"],
                          "vsFloatConstantsSha1": state["vsConstants"]["float"]["hash"],
                          "psFloatConstantsSha1": state["psConstants"]["float"]["hash"],
                          "decisions": decisions[key], "geometry": geometry,
                          "capture": str(path.parent)})
        except (CaptureError, KeyError, ValueError, TypeError) as error:
            errors.append(f"{path}: cannot validate draw {key}: {error}")
    if end.get("selectedDraws") != len(draws):
        errors.append(f"{path}: selectedDraws disagrees with validated records.")
    if len({(draw["frame"], draw["draw"]) for draw in draws}) != len(draws):
        errors.append(f"{path}: duplicate draw identity.")
    frames = sorted({draw["frame"] for draw in draws})
    if end.get("completedFrames") != len(frames):
        errors.append(f"{path}: completedFrames disagrees with captured draw frames.")
    return {"records": str(path), "frames": frames, "status": status, "end": end,
            "writtenBlobs": len(written), "referencedBlobs": len(used)}, draws


def read_traces(paths: list[Path], errors: list[str]) -> list[dict[str, Any]]:
    traces = []
    seen = set()
    for path in paths:
        for line_number, line in enumerate(path.read_text(encoding="utf-8-sig", errors="replace").splitlines(), 1):
            if PREFIX not in line:
                continue
            fields = dict(KEY_VALUE.findall(line.split(PREFIX, 1)[1]))
            try:
                frame = integer(fields["frame"])
                trace = integer(fields["trace"])
                stage = fields["stage"]
            except (KeyError, ValueError, TypeError) as error:
                errors.append(f"{path}:{line_number}: malformed trace: {error}")
                continue
            identity = tuple(sorted(fields.items()))
            if identity in seen:
                continue
            seen.add(identity)
            traces.append({"frame": frame, "trace": trace, "stage": stage,
                           "fields": fields, "file": str(path), "line": line_number})
    return traces


def verify(directory: Path, logs: list[Path], captures: list[Path], stdout: Path,
           frame_offset: int, capture_frame_offset: int = 0) -> dict[str, Any]:
    errors: list[str] = []
    report_path = directory / "replay_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8-sig"))
    if report.get("result") != "passed" or integer(report.get("frames", 0)) < 3 or integer(report.get("hresultChecks", 0)) <= 0:
        errors.append("Replay did not pass all HRESULT checks for at least three frames.")
    if report.get("syntheticTextures") is not True:
        errors.append("Replay report must identify its synthetic texture limitation.")
    mapping = {}
    stdout_events = []
    if stdout.is_file():
        stdout_events, stdout_errors = read_records(stdout)
        errors.extend(f"Replay stdout: {message}" for message in stdout_errors)
        for event in stdout_events:
            if event.get("event") == "frame":
                if event.get("draws") != 2:
                    errors.append(f"Replay frame {event.get('replayFrame')} must submit control and positive draws.")
                replay_frame = integer(event["replayFrame"]) + frame_offset
                if replay_frame in mapping:
                    errors.append(f"Duplicate replay stdout frame {replay_frame}.")
                mapping[replay_frame] = integer(event["sourceFrame"])
        if not stdout_events or stdout_events[-1].get("event") != "success":
            errors.append("Replay stdout lacks terminal success.")
        if any(event.get("event") == "error" for event in stdout_events):
            errors.append("Replay stdout reports an error.")
    elif report.get("sourceFrames") == list(SOURCE_FRAMES) and report.get("controlDrawsPerFrame") == 1 and report.get("positiveDrawsPerFrame") == 1:
        mapping = {frame_offset + i: source for i, source in enumerate(SOURCE_FRAMES)}
    else:
        errors.append("Missing explicit source-frame mapping in stdout or replay report.")
    expected_frames = [frame_offset + i for i in range(3)]
    for frame, source in zip(expected_frames, SOURCE_FRAMES):
        if mapping.get(frame) != source:
            errors.append(f"Runtime frame {frame} does not explicitly map to source frame {source}.")

    capture_reports = []
    observed = defaultdict(list)
    for path in captures:
        audit, draws = audit_capture(path, errors)
        capture_reports.append(audit)
        for draw in draws:
            observed[draw["frame"]].append(draw)
    if not captures:
        errors.append("No observer records.jsonl found.")
    controls = []
    for frame in expected_frames:
        observer_frame = frame - frame_offset + capture_frame_offset
        draws = sorted(observed[observer_frame], key=lambda draw: draw["draw"])
        if len(draws) != 2:
            errors.append(f"Observer frame {observer_frame} (Runtime {frame}) has {len(draws)} draws; expected exactly two.")
            continue
        negative, positive = draws
        if (negative["draw"], positive["draw"]) != (0, 1):
            errors.append(f"Observer frame {frame} has unexpected draw IDs.")
        for draw in draws:
            if draw["api"] != "DrawIndexedPrimitive" or draw["args"].get("primitiveCount") != 144 or draw["args"].get("elementCount") != 432 or draw["inputRangeValid"] is not True:
                errors.append(f"Observer frame {frame} draw {draw['draw']} has wrong API/range/count.")
            if draw["vertexShader"] != VS_SHA1 or draw["pixelShader"] != PS_SHA1 or draw["indexSha1"] != INDEX_SHA1:
                errors.append(f"Observer frame {frame} draw {draw['draw']} has unexpected shaders/indices.")
            if mapping.get(frame) in CONSTANT_SHA1 and (draw["vsFloatConstantsSha1"], draw["psFloatConstantsSha1"]) != CONSTANT_SHA1[mapping[frame]]:
                errors.append(f"Observer frame {frame} does not carry the float constant banks from source frame {mapping[frame]}.")
        if negative["flags"] != 6 or negative["positionSha1"] == POSITION_SHA1:
            errors.append(f"Observer frame {frame}: negative control recognized or not preserved.")
        if positive["flags"] != 7 or positive["streamSha1"] != STREAM_SHA1:
            errors.append(f"Observer frame {frame}: positive draw rejected or stream contents differ.")
        controls.append({"frame": frame, "observerFrame": observer_frame,
                         "sourceFrame": mapping.get(frame), "negative": negative, "positive": positive})

    traces = read_traces(logs, errors)
    indexed = defaultdict(list)
    for trace in traces:
        if trace["trace"] != TRACE_ID:
            errors.append(f"Unexpected compatibility trace ID: {hex(trace['trace'])}.")
        if trace["stage"] not in KNOWN_STAGES:
            errors.append(f"Unknown compatibility trace stage: {trace['stage']}.")
        if trace["stage"].endswith("_rejected"):
            errors.append(f"Runtime rejected a prototype stage: {trace}.")
        indexed[(trace["frame"], trace["stage"])].append(trace)
    if not traces:
        errors.append("No Runtime compatibility trace logs found.")
    scene_geometry = [trace for trace in traces if trace["stage"] == "scene_geometry"]
    if not scene_geometry:
        errors.append("No RaytraceGeometry was accepted by SceneManager.")
    for trace in scene_geometry:
        values = trace["fields"]
        if any(integer(values.get(key, -1)) != value for key, value in
               (("vertices", 252), ("indices", 432), ("position_format", 106), ("normal_format", 106), ("uv_format", 103))):
            errors.append(f"SceneManager counts/float formats are invalid: {trace}.")
    blas = [trace for trace in traces if trace["stage"] == "blas_recorded"]
    if not blas:
        errors.append("No actual selected BLAS build command was recorded.")

    instance_ids = set()
    stable_hashes = defaultdict(set)
    frame_evidence = []
    for frame in expected_frames:
        evidence = {"frame": frame, "sourceFrame": mapping.get(frame)}
        for stage in ("recognized", "raster_geometry", "camera_accepted", "tlas_recorded", "scene_only_frame"):
            entries = indexed[(frame, stage)]
            if len(entries) != 1:
                errors.append(f"Runtime frame {frame}: expected one {stage}, found {len(entries)}.")
            evidence[stage] = entries
        for trace in evidence["recognized"]:
            values = trace["fields"]
            if integer(values.get("draw", -1)) != 1:
                errors.append(f"Runtime frame {frame}: negative control was recognized.")
            if values.get("vs") != VS_SHA1 or values.get("ps") != PS_SHA1 or values.get("preserve_raster") != "true":
                errors.append(f"Runtime frame {frame}: recognized shader/raster preservation evidence is invalid.")
        for trace in evidence["raster_geometry"]:
            values = trace["fields"]
            if values.get("position") != "FLOAT3" or values.get("normal") != "FLOAT3" or values.get("uv") != "FLOAT2" or integer(values.get("vertices", -1)) != 252 or integer(values.get("indices", -1)) != 432:
                errors.append(f"Runtime frame {frame}: invalid RasterGeometry.")
            if integer(values.get("stride", -1)) != 32 or values.get("indices_rebased") != "0..251":
                errors.append(f"Runtime frame {frame}: invalid decoded vertex stride/index rebasing.")
            pixel_error = float(values.get("reprojection_error_px", "nan"))
            if not math.isfinite(pixel_error) or pixel_error < 0 or pixel_error > 0.25:
                errors.append(f"Runtime frame {frame}: invalid decoder reprojection error.")
        for trace in evidence["camera_accepted"]:
            if integer(trace["fields"].get("valid", 0)) != 1 or integer(trace["fields"].get("type", -1)) != 0:
                errors.append(f"Runtime frame {frame}: main camera invalid.")
        for trace in evidence["scene_only_frame"]:
            values = trace["fields"]
            if integer(values.get("raster_preserved", 0)) != 1 or integer(values.get("path_tracing_dispatched", 1)) != 0:
                errors.append(f"Runtime frame {frame}: raster-only mode was not retained.")
        for trace in evidence["tlas_recorded"]:
            values = trace["fields"]
            if values.get("type") != "TLAS_Opaque" or integer(values.get("included", 0)) != 1 or not integer(values.get("blas_address", 0)) or integer(values.get("instance_count", 0)) < 1 or integer(values.get("surface_index", -1)) < 0:
                errors.append(f"Runtime frame {frame}: selected BLAS absent from opaque TLAS.")

        instances = indexed[(frame, "instance_accepted")] + indexed[(frame, "instance_preserved")]
        if len(instances) != 1:
            errors.append(f"Runtime frame {frame}: expected one accepted/preserved instance.")
        evidence["instance"] = instances
        for trace in instances:
            values = trace["fields"]
            instance_ids.add(integer(values["instance_id"]))
            if not integer(values.get("mask", 0)) or integer(values.get("hidden", 0)):
                errors.append(f"Runtime frame {frame}: instance has no ray visibility.")
        frame_instance_ids = {integer(trace["fields"]["instance_id"]) for trace in instances}
        for trace in evidence["tlas_recorded"] + evidence["scene_only_frame"]:
            if integer(trace["fields"].get("instance_id", -1)) not in frame_instance_ids:
                errors.append(f"Runtime frame {frame}: {trace['stage']} refers to a different instance.")
        hash_entries = indexed[(frame, "scene_geometry")] + indexed[(frame, "instance_preserved")]
        if not hash_entries:
            errors.append(f"Runtime frame {frame}: no geometry/hash evidence for accepted/preserved instance.")
        for trace in hash_entries:
            if integer(trace["fields"].get("vertices", -1)) != 252 or integer(trace["fields"].get("indices", -1)) != 432:
                errors.append(f"Runtime frame {frame}: accepted/preserved geometry counts are invalid.")
            for name in HASH_FIELDS:
                value = integer(trace["fields"].get(name, 0))
                if not value:
                    errors.append(f"Runtime frame {frame}: missing/nonzero {name} evidence.")
                stable_hashes[name].add(value)
        evidence["blas"] = indexed[(frame, "blas_recorded")] + indexed[(frame, "blas_reused")]
        frame_evidence.append(evidence)
    if len(instance_ids) != 1:
        errors.append(f"RTX instance ID is not stable across all three frames: {sorted(instance_ids)}.")
    for name in HASH_FIELDS:
        if len(stable_hashes[name]) != 1:
            errors.append(f"RTX {name} changes across source frames: {sorted(stable_hashes[name])}.")
    built_addresses = {integer(trace["fields"].get("blas_address", 0)) for trace in blas}
    for trace in blas:
        values = trace["fields"]
        if integer(values.get("instance_id", -1)) not in instance_ids or integer(values.get("geometry_count", 0)) < 1 or integer(values.get("mode", -1)) not in (0, 1) or not integer(values.get("blas_address", 0)):
            errors.append(f"Selected BLAS command does not match the accepted instance: {trace}.")
    for trace in traces:
        if trace["stage"] == "tlas_recorded" and integer(trace["fields"].get("blas_address", 0)) not in built_addresses:
            errors.append(f"TLAS references a selected BLAS with no recorded build evidence: {trace}.")
    fixture_only = (directory / "UNIT_TEST_FIXTURE_ONLY.txt").is_file()
    binary_provenance = runtime_provenance(directory, fixture_only, errors)
    limitations = ["Synthetic textures; original EDF texture pixels and lighting are not reproduced.",
                   "AS evidence identifies recorded Vulkan commands; visual agreement with the real game still requires its in-game test."]
    if fixture_only:
        limitations.insert(0, "Synthetic verifier fixture only; these logs are not evidence of any GPU execution.")
    return {"schema": 1, "result": "passed" if not errors else "failed", "errors": errors,
            **binary_provenance,
            "fixtureOnly": fixture_only, "notGpuEvidence": fixture_only,
            "evidenceKind": "synthetic_verifier_fixture" if fixture_only else "controlled_runtime_replay",
            "replayDirectory": str(directory), "traceId": hex(TRACE_ID), "replayReport": report,
            "runtimeFrameOffset": frame_offset, "captureFrameOffset": capture_frame_offset,
            "sourceFrameMapping": mapping,
            "captures": capture_reports, "negativeAndPositiveControls": controls,
            "frameEvidence": frame_evidence, "stageCounts": dict(Counter(trace["stage"] for trace in traces)),
            "instanceIds": sorted(instance_ids),
            "stableHashes": {name: [hex(value) for value in sorted(values)] for name, values in stable_hashes.items()},
            "runtimeLogs": [str(path) for path in logs],
            "limitations": limitations}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("replay_directory", type=Path)
    parser.add_argument("--runtime-log", type=Path, action="append")
    parser.add_argument("--capture", type=Path, action="append", help="records.jsonl or capture directory")
    parser.add_argument("--stdout", type=Path)
    parser.add_argument("--runtime-frame-offset", type=int, default=0,
                        help="Explicit offset relative to replay frame numbers; never inferred.")
    parser.add_argument("--capture-frame-offset", type=int, default=0,
                        help="Explicit observer offset relative to replay frame numbers; never inferred.")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    directory = args.replay_directory.resolve()
    output = args.output or directory / "verification.json"
    logs = sorted(path.resolve() for path in (args.runtime_log or directory.rglob("*.log")))
    captures = args.capture or sorted(directory.rglob("records.jsonl"))
    captures = [((path / "records.jsonl") if path.is_dir() else path).resolve() for path in captures]
    try:
        result = verify(directory, logs, captures, args.stdout or directory / "replay_stdout.jsonl",
                        args.runtime_frame_offset, args.capture_frame_offset)
    except (OSError, ValueError, TypeError, KeyError) as error:
        result = {"schema": 1, "result": "failed", "errors": [str(error)], "replayDirectory": str(directory)}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"result": result["result"], "errors": result["errors"], "verification": str(output)}))
    return 0 if result["result"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
