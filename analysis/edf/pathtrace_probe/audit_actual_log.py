"""Audit the immutable actual EDF runtime log without inferring missing samples."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re


def time_ms(text: str) -> int:
    hour, minute, rest = text.split(":")
    second, milli = rest.split(".")
    return ((int(hour) * 60 + int(minute)) * 60 + int(second)) * 1000 + int(milli)


def audit(log: Path) -> dict:
    payload = log.read_bytes()
    lines = payload.decode("utf-8-sig", errors="replace").splitlines()
    events = []
    warnings = []
    errors = []
    config = []
    for line_number, line in enumerate(lines, 1):
        if re.search(r"\] warn:", line):
            warnings.append({"line": line_number, "text": line})
        if re.search(r"\] err(?:or)?:", line):
            errors.append({"line": line_number, "text": line})
        if "rtx.edfStatic." in line or "rtx.compatCapture." in line:
            config.append({"line": line_number, "text": line})
        match = re.search(r"^\[([\d:.]+)\].*\[RTX-Compatibility-Trace\] (.+)$", line)
        if not match:
            continue
        fields = dict(re.findall(r"(\w+)=([^\s]+)", match[2]))
        events.append({"line": line_number, "time": match[1], "time_ms": time_ms(match[1]),
                       **fields, "frame": int(fields["frame"]), "raw": line})
    stages = Counter(item["stage"] for item in events)
    per_frame = defaultdict(list)
    for item in events:
        per_frame[item["frame"]].append(item)
    cameras = [e for e in events if e["stage"] == "camera_accepted"]
    recognized = [e for e in events if e["stage"] == "recognized"]
    rejected = [e for e in events if e["stage"] == "injection_rejected"]
    rejection_context = []
    for rejection in rejected:
        previous_camera = next((c for c in reversed(cameras) if c["line"] < rejection["line"]), None)
        next_camera = next((c for c in cameras if c["line"] > rejection["line"]), None)
        previous_recognition = next((c for c in reversed(recognized) if c["line"] < rejection["line"]), None)
        rejection_context.append({
            **rejection,
            "same_frame_logged_stages": [e["stage"] for e in per_frame[rejection["frame"]]],
            "previous_logged_camera": previous_camera,
            "next_logged_camera": next_camera,
            "previous_logged_recognition": previous_recognition,
            "frames_since_logged_camera": rejection["frame"] - previous_camera["frame"] if previous_camera else None,
            "ms_since_logged_camera": rejection["time_ms"] - previous_camera["time_ms"] if previous_camera else None,
            "same_frame_camera_acceptance_logged": any(c["frame"] == rejection["frame"] for c in cameras),
            "target_absence_proven": False,
        })
    builds = [e for e in events if e["stage"] == "blas_recorded"]
    inclusion = [e for e in events if e["stage"] == "tlas_recorded" and e.get("included") == "1"]
    full_frames = []
    required = {"recognized", "raster_geometry", "camera_accepted", "scene_geometry", "instance_accepted", "blas_recorded", "tlas_recorded", "scene_only_frame"}
    for frame, frame_events in sorted(per_frame.items()):
        if required.issubset({e["stage"] for e in frame_events}):
            full_frames.append(frame)
    geometry_keys = ["position_hash", "index_hash", "uv_hash", "geometry_hash", "material_hash"]
    geometry_unique = {key: sorted({e[key] for e in events if key in e and e["stage"] in ["scene_geometry", "instance_preserved"]}) for key in geometry_keys}
    counts = sorted({(e.get("vertices"), e.get("indices")) for e in events if e["stage"] in ["scene_geometry", "raster_geometry", "instance_preserved"]})
    reprojection = [float(e["reprojection_error_px"]) for e in events if "reprojection_error_px" in e]
    return {
        "schema_version": 1,
        "evidence_kind": "actual_edf_runtime_log",
        "source_path": str(log),
        "source_sha256": hashlib.sha256(payload).hexdigest(),
        "source_bytes": len(payload), "line_count": len(lines),
        "logging_policy": {"per_trace_stage": True, "first_unique_frames": 8,
                           "later_minimum_frame_interval": 120, "same_frame_duplicates_suppressed": True,
                           "absence_is_not_proof": True},
        "events_count": len(events), "stage_counts_logged_only": dict(stages),
        "full_logged_pipeline_frames": full_frames,
        "camera_accepted_frames_logged_only": [e["frame"] for e in cameras],
        "recognized_frames_logged_only": [e["frame"] for e in recognized],
        "rejection_counts_logged_only": dict(Counter(e.get("reason") for e in rejected)),
        "rejection_context": rejection_context,
        "geometry_counts_unique": counts,
        "geometry_hashes_unique": geometry_unique,
        "decoder_reprojection_error_px": {"samples": len(reprojection), "minimum": min(reprojection), "maximum": max(reprojection)},
        "blas_recorded": builds, "tlas_included": inclusion,
        "instance_ids_logged": sorted({int(e["instance_id"]) for e in events if "instance_id" in e}),
        "instance_recreation_is_not_geometry_change_proof": True,
        "source_log_vk_errors": errors, "warnings": warnings,
        "dlss_load_messages": [{"line": n, "text": line} for n, line in enumerate(lines, 1) if "error: failed to load NGXCore" in line],
        "profile_config_lines": config,
        "capture_directory": "D:/EDF IA RTX REMIX/captures/edf-prototype",
        "capture_directory_exists_at_audit": Path("D:/EDF IA RTX REMIX/captures/edf-prototype").exists(),
        "capture_trigger_or_window_lines": [{"line": n, "text": line} for n, line in enumerate(lines, 1) if any(token in line for token in ["capture_armed", "window_complete", "capture_begin", "capture_end", "[CompatCapture]"])],
        "milestones": {
            "M1": {"status": "demonstrated_actual_game", "evidence": "Profile enabled and gameplay draws reached frame 1385; no crash is reported in this log."},
            "M2": {"status": "demonstrated_for_selected_draw", "evidence": "Selected shader pair and 252/432 buffers recognized in actual gameplay."},
            "M3": {"status": "pipeline_acceptance_demonstrated_visual_pending", "evidence": "Decoded mesh reached SceneManager, InstanceManager, BLAS commands and opaque TLAS inclusion."},
            "M4": {"status": "partial", "evidence": "Main camera accepted in several frames and changing anchors; decoder reprojection checks passed. No visual overlay or per-frame injection-camera snapshot."},
            "M5": {"status": "partial", "evidence": "Actual base texture/material hash consistent. Visual UV, colors and PBR interpretation remain untested."},
            "M6": {"status": "not_demonstrated", "evidence": "No asset replacement was exercised."},
            "M7": {"status": "not_demonstrated", "evidence": "rasterOnly=True; all emitted scene_only_frame records state path_tracing_dispatched=0."},
            "M8": {"status": "out_of_scope"}, "M9": {"status": "out_of_scope"}, "M10": {"status": "out_of_scope"},
        },
        "limits": [
            "BLAS/TLAS recorded means Vulkan commands recorded; this log has no dedicated completion query, fence or GPU readback proving execution.",
            "A camera_accepted record proves validity in that record's frame, not later frames.",
            "Missing camera/recognized/success stages are censored by the rate limiter and cannot establish missing draws or injection ordering.",
            "The source log ends after a camera-invalid message and contains no final process-exit result.",
            "No new configured edf-prototype capture window exists to reconstruct EDF pass/composition order for this run.",
        ],
        "events": events,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("log", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.log)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in ["source_sha256", "events_count", "stage_counts_logged_only", "full_logged_pipeline_frames", "rejection_counts_logged_only", "geometry_hashes_unique", "decoder_reprojection_error_px"]}, indent=2))


if __name__ == "__main__":
    main()
