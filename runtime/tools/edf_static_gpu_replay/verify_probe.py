#!/usr/bin/env python3
"""Independently verify bounded EDF offscreen GPU readbacks, never visual composition.

Requires NumPy. Ray conventions come from concept/camera/camera.slangh and
concept/ray/ray.slangh: pixel centers, top-left UV, NDC (2u-1, 1-2v),
normalize((P^-1*[ndc.x,ndc.y,1,1]).xyz), then the view-to-world rotation.
CPU triangles come from the measured SHORT4 stream and rebased UINT16 indices;
the OBJ must match them exactly. Silhouette edge precision has one pixel allowance.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import sys
from typing import Any

import numpy as np


TRACE = "0x4544465354415449"
MESH = "925aa18481228b01392a574dbe28ba4e21aa1616"
VS = "f098f457d9ae603bea5f84ec985d473c29cfb0c9"
PS = "44b92a29eb3e5f2ee6d53f02e14fb11a01511da8"
GEOMETRY_HASH = "0x32aeb7bb7e772c13"
POSITION_SHA1 = "f3ddbe4c53df3aea29e2cad8d305844c95c7dc4c"
INDEX_SHA1 = "2ecc0d2d1046f418d56ea51bbcca29579a04a937"
DEFAULT_GEOMETRY = Path(__file__).resolve().parents[3] / "analysis/edf/new_candidate"
IMAGE_FORMATS = {"linear_z": (100, 4), "albedo": (64, 4),
                 "direct_diffuse_radiance": (97, 8), "final_offscreen": (97, 8)}
KEY_VALUE = re.compile(r"\b([A-Za-z_]\w*)=([^\s]+)")
METADATA_NAME = re.compile(r"^edf_pt_request_[0-9]+_frame_[0-9]+\.json$")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def fnv1a64(content: bytes) -> int:
    value = 14695981039346656037
    for byte in content:
        value = ((value ^ byte) * 1099511628211) & 0xffffffffffffffff
    return value


def number(value: Any) -> float:
    require(isinstance(value, (int, float)) and not isinstance(value, bool), "Expected numeric value.")
    value = float(value)
    require(math.isfinite(value), "Nonfinite numeric value.")
    return value


def matrix(value: Any) -> np.ndarray:
    require(isinstance(value, list) and len(value) == 16, "Matrix must have 16 column-major elements.")
    return np.asarray([number(x) for x in value], dtype=np.float64).reshape((4, 4), order="F")


def bounds(mask: np.ndarray) -> list[int] | None:
    rows, cols = np.nonzero(mask)
    return [int(cols.min()), int(rows.min()), int(cols.max()), int(rows.max())] if len(rows) else None


def dilate(mask: np.ndarray) -> np.ndarray:
    padded = np.pad(mask, 1, constant_values=False)
    return np.logical_or.reduce([padded[y:y+mask.shape[0], x:x+mask.shape[1]] for y in range(3) for x in range(3)])


def erode(mask: np.ndarray) -> np.ndarray:
    padded = np.pad(mask, 1, constant_values=False)
    return np.logical_and.reduce([padded[y:y+mask.shape[0], x:x+mask.shape[1]] for y in range(3) for x in range(3)])


def load_geometry(directory: Path) -> tuple[np.ndarray, np.ndarray, dict]:
    position_file = directory / "replay/stream2_used.bin"
    index_file = directory / "replay/indices_original_used.bin"
    position_raw, index_raw = position_file.read_bytes(), index_file.read_bytes()
    require(hashlib.sha1(position_raw).hexdigest() == POSITION_SHA1, "Measured position stream SHA1 mismatch.")
    require(hashlib.sha1(index_raw).hexdigest() == INDEX_SHA1, "Measured index stream SHA1 mismatch.")
    require(len(position_raw) == 252*8 and len(index_raw) == 432*2, "Unexpected measured geometry size.")
    vertices = np.frombuffer(position_raw, dtype="<i2").reshape(252, 4)[:, :3].astype(np.float64) * .25
    indices = np.frombuffer(index_raw, dtype="<u2").astype(np.int64) - 630
    require(indices.min() == 0 and indices.max() == 251, "Measured indices fail rebased range.")
    faces = indices.reshape(144, 3)
    obj_file = directory / "selected_object.obj"
    obj_vertices, obj_faces = [], []
    for line in obj_file.read_text(encoding="utf-8").splitlines():
        tokens = line.split()
        if tokens and tokens[0] == "v":
            obj_vertices.append([float(x) for x in tokens[1:4]])
        elif tokens and tokens[0] == "f":
            require(len(tokens) == 4, "OBJ must retain triangle topology.")
            obj_faces.append([int(x.split("/")[0])-1 for x in tokens[1:]])
    require(np.array_equal(vertices, np.asarray(obj_vertices)), "OBJ vertices differ from measured SHORT4 positions.")
    require(np.array_equal(faces, np.asarray(obj_faces)), "OBJ topology differs from measured indices.")
    manifest_file = directory / "replay_manifest.json"
    manifest = json.loads(manifest_file.read_text(encoding="utf-8-sig"))
    require(len(manifest) == 3 and all(x["originalArgs"]["primitiveCount"] == 144 for x in manifest), "Unexpected source replay manifest.")
    return vertices, faces, {"vertexCount": 252, "triangleCount": 144, "positionSha1": POSITION_SHA1,
                             "indicesSha1": INDEX_SHA1, "objSha256": hashlib.sha256(obj_file.read_bytes()).hexdigest(),
                             "manifestSha256": hashlib.sha256(manifest_file.read_bytes()).hexdigest(), "manifest": manifest}


def cpu_depth(vertices: np.ndarray, faces: np.ndarray, view: np.ndarray,
              projection: np.ndarray, width: int, height: int) -> np.ndarray:
    """Nearest two-sided opaque triangle intersections in original anchored world.

    This target contains closed components. Two-sided nearest intersections avoid
    guessing Vulkan winding flags; a reversed GPU cull direction still fails the
    independent nearest-depth comparison. Primary rays originate at the camera,
    not at the near plane, as in rayCreatePrimary; tMax is floatMax.
    """
    require(vertices.ndim == 2 and vertices.shape[1] == 3, "Invalid CPU vertices.")
    inverse_view, inverse_projection = np.linalg.inv(view), np.linalg.inv(projection)
    camera = inverse_view[:3, 3]
    homogeneous = np.column_stack((vertices, np.ones(len(vertices))))
    clip = homogeneous @ (projection @ view).T
    require(np.all(clip[:, 3] > 0), "Reference needs near-plane clipping; this candidate must be wholly in front.")
    screen = (clip[:, :2] / clip[:, 3, None] * [0.5, -0.5] + 0.5) * [width, height]
    nearest = np.full((height, width), np.inf, dtype=np.float64)
    depth = np.full((height, width), np.nan, dtype=np.float64)
    for face in faces:
        triangle = vertices[face]
        rectangle = screen[face]
        xmin, ymin = np.maximum(np.floor(rectangle.min(axis=0)).astype(int)-1, [0, 0])
        xmax, ymax = np.minimum(np.ceil(rectangle.max(axis=0)).astype(int)+1, [width-1, height-1])
        if xmax < xmin or ymax < ymin:
            continue
        grid_y, grid_x = np.mgrid[ymin:ymax+1, xmin:xmax+1]
        ndc = np.column_stack(((grid_x.ravel()+.5)/width*2-1,
                               1-(grid_y.ravel()+.5)/height*2,
                               np.ones(grid_x.size), np.ones(grid_x.size)))
        view_directions = (ndc @ inverse_projection.T)[:, :3]
        view_directions /= np.linalg.norm(view_directions, axis=1)[:, None]
        directions = view_directions @ inverse_view[:3, :3].T
        directions /= np.linalg.norm(directions, axis=1)[:, None]
        edge1, edge2 = triangle[1]-triangle[0], triangle[2]-triangle[0]
        pvec = np.cross(directions, edge2)
        determinant = pvec @ edge1
        valid = np.abs(determinant) > 1e-10
        invdet = np.divide(1., determinant, out=np.zeros_like(determinant), where=valid)
        origin_relative = camera-triangle[0]
        u = (pvec @ origin_relative)*invdet
        qvec = np.cross(origin_relative, edge1)
        v = (directions @ qvec)*invdet
        t = float(edge2 @ qvec)*invdet
        valid &= (u >= -1e-10) & (v >= -1e-10) & (u+v <= 1+1e-10) & (t > 0)
        xs, ys = grid_x.ravel(), grid_y.ravel()
        update = valid & (t < nearest[ys, xs])
        if np.any(update):
            hit = camera + directions[update]*t[update, None]
            linear_z = (np.column_stack((hit, np.ones(len(hit)))) @ view.T)[:, 2]
            nearest[ys[update], xs[update]] = t[update]
            depth[ys[update], xs[update]] = linear_z
    return depth


def compare_depth(actual: np.ndarray, reference: np.ndarray, miss: float) -> dict:
    require(actual.shape == reference.shape, "Depth/reference dimensions mismatch.")
    require(np.all(np.isfinite(actual)), "GPU depth contains nonfinite pixels.")
    gpu_mask, cpu_mask = actual != np.float32(miss), np.isfinite(reference)
    require(np.any(cpu_mask) and np.any(gpu_mask), "Both GPU and independent CPU must hit geometry.")
    extra = gpu_mask & ~dilate(cpu_mask)
    missing = erode(cpu_mask) & ~gpu_mask
    require(not np.any(extra), f"GPU has {int(extra.sum())} hits beyond a one-pixel CPU silhouette allowance.")
    require(not np.any(missing), f"GPU misses {int(missing.sum())} interior CPU hits.")
    common = erode(cpu_mask) & gpu_mask
    require(np.any(common), "No stable interior samples for independent CPU depth validation.")
    difference = np.abs(actual[common].astype(np.float64)-reference[common])
    tolerance = .0005 + np.abs(reference[common])*2e-5
    require(np.all(difference <= tolerance), f"Nearest-depth disagreement exceeds float tolerance; maximum error {difference.max()}.")
    intersection, union = np.count_nonzero(gpu_mask & cpu_mask), np.count_nonzero(gpu_mask | cpu_mask)
    iou = intersection/union
    require(iou >= .98, f"GPU/CPU silhouette IoU {iou} is below .98.")
    return {"passed": True, "cpuHitCount": int(cpu_mask.sum()), "gpuHitCount": int(gpu_mask.sum()),
            "cpuBounds": bounds(cpu_mask), "gpuBounds": bounds(gpu_mask), "silhouetteIoU": iou,
            "allowedSilhouetteEdgePixels": 1, "interiorDepthSamples": int(common.sum()),
            "maximumInteriorDepthError": float(difference.max()),
            "depthTolerance": "0.0005 + abs(cpuLinearZ) * 2e-5", "nearestTrianglesTwoSided": True}


def parse_log(path: Path) -> list[dict]:
    events = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8-sig", errors="replace").splitlines(), 1):
        if "[EDF-Static-Probe]" in line or "[RTX-Compatibility-Trace]" in line:
            event = dict(KEY_VALUE.findall(line))
            event.update(line=line_number, raw=line)
            if "frame" in event:
                event["frame"] = int(event["frame"])
            events.append(event)
    return events


class FileAudit:
    def __init__(self) -> None:
        self.fnv_cache: dict[str, int] = {}

    def read(self, directory: Path, filename: Any, byte_count: int, checksum: Any) -> tuple[bytes, dict]:
        require(isinstance(filename, str) and filename, "Missing readback filename.")
        path = (directory/filename).resolve()
        require(path.is_relative_to(directory.resolve()), "Readback path escapes metadata directory.")
        raw = path.read_bytes()
        require(len(raw) == byte_count, f"Wrong byte count: {path.name}.")
        sha256 = hashlib.sha256(raw).hexdigest()
        fnv = self.fnv_cache.get(sha256)
        if fnv is None:
            fnv = self.fnv_cache[sha256] = fnv1a64(raw)
        require(isinstance(checksum, str) and int(checksum, 16) == fnv, f"FNV1a64 mismatch: {path.name}.")
        return raw, {"path": str(path), "byteCount": len(raw), "sha256": sha256, "fnv1a64": hex(fnv)}


def check_matrices(data: dict) -> tuple[np.ndarray, np.ndarray, dict]:
    require(data.get("matrixStorage") == "column_major", "Unsupported matrix storage.")
    names = ("sourceWorldToView", "sourceViewToProjection", "rendererWorldToView",
             "rendererViewToProjection", "rendererViewToWorld", "rendererProjectionToView",
             "rendererWorldToProjectionJittered", "rendererProjectionToWorldJittered", "rendererViewToProjectionJittered")
    matrices = {name: matrix(data[name]) for name in names}
    view, projection = matrices["rendererWorldToView"], matrices["rendererViewToProjection"]
    view_error = float(np.max(np.abs(view-matrices["sourceWorldToView"])))
    projection_error = float(np.max(np.abs(projection-matrices["sourceViewToProjection"])))
    require(view_error <= 1e-4 and projection_error <= 1e-4, "Renderer matrices differ from the decoded source camera.")
    require(abs(number(data["rendererViewError"])-view_error) <= 1e-5, "Reported renderer view error is inconsistent.")
    require(abs(number(data["rendererProjectionError"])-projection_error) <= 1e-5, "Reported projection error is inconsistent.")
    require(data.get("jitterPixels") == [0, 0], "Controlled probe must disable camera jitter.")
    algebra = {"viewInverse": view @ matrices["rendererViewToWorld"],
               "projectionInverse": projection @ matrices["rendererProjectionToView"],
               "worldProjectionInverse": matrices["rendererWorldToProjectionJittered"] @ matrices["rendererProjectionToWorldJittered"]}
    errors = {name: float(np.max(np.abs(value-np.eye(4)))) for name, value in algebra.items()}
    errors["composition"] = float(np.max(np.abs(projection @ view-matrices["rendererWorldToProjectionJittered"])))
    errors["jitterProjection"] = float(np.max(np.abs(projection-matrices["rendererViewToProjectionJittered"])))
    require(all(error <= 1e-3 for name, error in errors.items() if name != "jitterProjection"), f"Camera inverse/composition algebra failed: {errors}.")
    require(errors["jitterProjection"] <= 1e-6, "Jittered projection differs in the controlled probe.")
    require(int(data["rendererCameraFlags"]) & 1, "Expected validated right-handed camera flag.")
    require(number(data["rendererNearPlane"]) > 0, "Invalid renderer near plane.")
    require(np.max(np.abs(view[3]-[0, 0, 0, 1])) <= 1e-6, "Renderer view is not affine.")
    return view, projection, {"sourceViewMaxError": view_error, "sourceProjectionMaxError": projection_error, "algebraMaxErrors": errors}


def verify_sample(metadata: Path, events: list[dict], vertices: np.ndarray, faces: np.ndarray,
                  auditor: FileAudit, width: int, height: int) -> dict:
    data = json.loads(metadata.read_text(encoding="utf-8-sig"))
    require(data.get("schema") == 1 and data.get("traceId") == TRACE, "Unknown probe schema or trace identity.")
    require(data.get("gpuCompleted") is True, "GPU completion is not confirmed.")
    require(data.get("compositionPerformed") is False, "Probe composed into the game image.")
    require(data.get("rasterPreservedByDesign") is True and data.get("originalTargetsUnchanged") is True, "Original target preservation is not confirmed.")
    frame, request = int(data["frameId"]), int(data["requestId"])
    require(data["cameraLastUpdateFrame"] == frame and data["currentFrameDrawCount"] == 1, "Camera and selected draw are not current in the probe frame.")
    require(data["surfaceCount"] == 1, "Probe has surfaces outside the single mesh.")
    require(data["rendererResolution"] == [width, height] and data["injectionTargetExtent"] == [width, height], "Expected original renderer dimensions.")
    whitelist = data["whitelist"]
    require(whitelist["orderedInstances"] == 1 and whitelist["vertices"] == 252 and whitelist["indices"] == 432, "Mesh/instance whitelist count failed.")
    require(whitelist["sourceMeshKey"] == MESH and whitelist["decoderVertexShader"] == VS and whitelist["decoderPixelShader"] == PS, "Wrong mesh or original shader hash.")
    require(whitelist["geometryHash"].lower() == GEOMETRY_HASH, "Modified/replaced geometry hash differs from measured candidate.")
    sample_events = [e for e in events if e.get("frame") == frame]
    for stage in ["whitelist_accepted", "offscreen_begin", "readback_recorded", "gpu_completed"]:
        matches = [e for e in sample_events if e.get("stage") == stage]
        require(len(matches) == 1, f"Expected one frame-local {stage} record for frame {frame}.")
        if "request_id" in matches[0]:
            require(int(matches[0]["request_id"]) == request, f"Mismatched request_id for {stage}.")
        if stage == "whitelist_accepted":
            logged = matches[0]
            require(logged.get("vs") == VS and logged.get("ps") == PS and logged.get("mesh") == MESH
                    and logged.get("vertices") == "252" and logged.get("indices") == "432"
                    and logged.get("ordered_instances") == "1" and logged.get("geometry_hash", "").lower() == GEOMETRY_HASH
                    and int(logged["instance_id"]) == whitelist["instanceId"], "Frame-local shader/geometry whitelist log mismatch.")
    snapshots = [e for e in sample_events if e.get("stage") == "injection_snapshot" and e.get("camera_valid") == "1"
                 and e.get("current_frame_draw_count") == "1" and e.get("duplicate") == "0"]
    require(snapshots and all(int(e["camera_last_update_frame"]) == frame for e in snapshots), "No current-camera nonduplicate injection snapshot.")
    view, projection, matrix_report = check_matrices(data)
    names = [x["name"] for x in data["images"]]
    require(len(names) == 4 and set(names) == set(IMAGE_FORMATS), "Missing/duplicate diagnostic output image.")
    images, raw_images = [], {}
    for image in data["images"]:
        expected_format, bytes_per_pixel = IMAGE_FORMATS[image["name"]]
        require(image["format"] == expected_format and image["bytesPerPixel"] == bytes_per_pixel, "Unsupported image format/stride.")
        require(image["width"] == width and image["height"] == height and image["byteCount"] == width*height*bytes_per_pixel, "Image dimensions/byte size failed.")
        require(image["checksumAlgorithm"] == "fnv1a64", "Unsupported readback checksum algorithm.")
        raw, report = auditor.read(metadata.parent, image["file"], image["byteCount"], image["checksum"])
        report.update(name=image["name"], format=image["format"])
        if "sha256" in image:
            require(image["sha256"] == report["sha256"], "Reported raw SHA256 mismatch.")
        images.append(report)
        raw_images[image["name"]] = raw
    depth = np.frombuffer(raw_images["linear_z"], dtype="<f4").reshape(height, width)
    miss = number(data["missLinearZ"])
    mask = np.isfinite(depth) & (depth != np.float32(miss))
    require(int(np.count_nonzero(~np.isfinite(depth))) == data["depthNonfiniteCount"] == 0, "Nonfinite GPU depth or incorrect count.")
    require(int(mask.sum()) == data["depthHitCount"] and mask.any(), "GPU depth hit count inconsistent or zero.")
    require(data["hitBounds"] == bounds(mask), "Reported depth hit bounds differ from raw pixels.")
    require(np.all(depth[mask] < 0), "Validated right-handed hit depth must be negative; miss sentinel is distinct.")
    require(abs(number(data["minimumHitLinearZ"])-float(depth[mask].min())) <= .001
            and abs(number(data["maximumHitLinearZ"])-float(depth[mask].max())) <= .001, "Reported hit depth range differs from raw image.")
    radiance = np.frombuffer(raw_images["direct_diffuse_radiance"], dtype="<f2").reshape(height, width, 4)[:, :, :3].astype(np.float32)
    nonfinite = int(np.any(~np.isfinite(radiance), axis=2).sum())
    nonzero = int(np.any(np.isfinite(radiance) & (radiance > 0), axis=2).sum())
    require(nonfinite == data["radianceNonfiniteCount"] == 0 and nonzero == data["radianceNonzeroCount"], "Radiance raw statistics differ or contain nonfinite values.")
    final = np.frombuffer(raw_images["final_offscreen"], dtype="<f2")
    require(np.all(np.isfinite(final)), "Final offscreen output contains nonfinite values.")
    targets = data["preservedTargets"]
    slots = {x["slot"] for x in targets}
    expected_slots = {0, 2, 3} if data["sourceTargetSessionId"] == data["injectionTargetSessionId"] else {0, 1, 2, 3}
    require(len(targets) == len(expected_slots) and slots == expected_slots, "Preservation evidence does not cover injection target and three original MRTs.")
    require(len({x["sessionId"] for x in targets}) == len(targets), "Preserved targets must deduplicate identical images.")
    preservation = []
    for target in targets:
        require(target["sessionId"] > 0 and target["format"] in (37, 43, 44, 50), "Invalid original target identity/format.")
        require(target["width"] == width and target["height"] == height and target["byteCount"] == width*height*4, "Original target size mismatch.")
        require(target["unchanged"] is True and target["comparison"] == "full_memcmp", "Original target did not pass full memcmp.")
        before, before_report = auditor.read(metadata.parent, target["beforeFile"], target["byteCount"], target["beforeFnv1a64"])
        after, after_report = auditor.read(metadata.parent, target["afterFile"], target["byteCount"], target["afterFnv1a64"])
        require(before == after and before_report["sha256"] == after_report["sha256"], "Original target bytes changed during offscreen rendering.")
        if target["slot"] == 0:
            require(target["sessionId"] == data["injectionTargetSessionId"], "Injection target preservation identity mismatch.")
        if target["slot"] == 1:
            require(target["sessionId"] == data["sourceTargetSessionId"], "MRT0 preservation identity mismatch.")
        preservation.append({"slot": target["slot"], "sessionId": target["sessionId"], "unchanged": True,
                             "before": before_report, "after": after_report})
    reference = cpu_depth(vertices, faces, view, projection, width, height)
    independent = compare_depth(depth, reference, miss)
    completed = next(e for e in sample_events if e.get("stage") == "gpu_completed")
    require(completed.get("composition_performed") == "0" and completed.get("original_targets_unchanged") == "1", "Completion log does not confirm offscreen preservation.")
    require(int(completed["depth_hits"]) == data["depthHitCount"], "Completion log/raw depth counts mismatch.")
    return {"passed": True, "metadata": str(metadata.resolve()), "metadataSha256": hashlib.sha256(metadata.read_bytes()).hexdigest(),
            "frameId": frame, "requestId": request, "matrices": matrix_report, "images": images,
            "preservedTargets": preservation, "independentCpuRayTriangles": independent,
            "radianceNonzeroCount": nonzero, "radianceNonzeroObserved": nonzero > 0,
            "radianceNonzeroRequired": False, "lightCount": data["lightCount"], "compositionPerformed": False, "gpuCompleted": True}


def verify(directory: Path, runtime_log: Path, geometry_directory: Path = DEFAULT_GEOMETRY,
           expected_missing_frame: int | None = None, expected_samples: int = 1,
           fixture_only: bool = False, width: int = 1920, height: int = 1080,
           metadata_files: list[Path] | None = None) -> dict:
    errors, samples = [], []
    result = {"schema": 1, "passed": False, "errors": errors,
              "evidenceKind": "synthetic_fixture" if fixture_only else "controlled_offscreen_runtime_replay",
              "fixtureOnly": fixture_only, "notGpuEvidence": fixture_only, "compositionVerified": False,
              "visualReplacementAllowed": False, "originalGameMaterialsVisuallyVerified": False,
              "runtimeLog": str(runtime_log.resolve()), "samples": samples}
    try:
        vertices, faces, geometry = load_geometry(geometry_directory)
        geometry.pop("manifest")
        result["referenceGeometry"] = geometry
        events = parse_log(runtime_log)
        result["runtimeLogSha256"] = hashlib.sha256(runtime_log.read_bytes()).hexdigest()
        binary_root = runtime_log.parents[2] if len(runtime_log.parents) >= 3 else directory
        runtime = binary_root/".trex/d3d9.dll" if (binary_root/".trex/d3d9.dll").is_file() else binary_root/"d3d9.dll"
        if runtime.is_file():
            digest = hashlib.sha256()
            with runtime.open("rb") as stream:
                for block in iter(lambda: stream.read(1024*1024), b""):
                    digest.update(block)
            result["runtimeBinary"] = str(runtime.resolve())
            result["runtimeSha256"] = digest.hexdigest()
            result["runtimeProvenance"] = "Renderer binary measured after execution; x86 proxy excluded."
        elif not fixture_only:
            errors.append(f"Runtime renderer provenance unavailable: {runtime}.")
        for event in events:
            if event.get("stage") in ["readback_failed", "probe_failed"]:
                errors.append(f"Runtime readback failure at log line {event['line']}.")
        files = metadata_files if metadata_files is not None else sorted(
            path for path in directory.rglob("edf_pt_request_*_frame_*.json") if METADATA_NAME.fullmatch(path.name))
        require(len(files) >= expected_samples >= 1, "Insufficient GPU readback metadata samples.")
        require(len({p.parent.resolve() for p in files}) == 1, "Multiple probe sessions found; select one exact session directory.")
        auditor = FileAudit()
        for file in files:
            try:
                samples.append(verify_sample(file, events, vertices, faces, auditor, width, height))
            except (ValueError, KeyError, OSError, TypeError, np.linalg.LinAlgError, OverflowError) as error:
                errors.append(f"{file.name}: {error}")
        require(len({sample["frameId"] for sample in samples}) == len(samples), "Duplicate successful sample frame IDs.")
        if expected_missing_frame is not None:
            missing = [e for e in events if e.get("stage") == "injection_snapshot" and e.get("frame") == expected_missing_frame]
            require(missing and all(e.get("current_frame_draw_count") == "0" and e.get("camera_valid") == "0"
                    and int(e["camera_last_update_frame"]) == expected_missing_frame-1 for e in missing), "Missing-mesh frame did not expose absent current camera/draw.")
            require(not any(s["frameId"] == expected_missing_frame for s in samples), "GPU probe ran with a stale/missing-frame camera.")
            recovery = [e for e in events if e.get("stage") == "injection_snapshot" and e.get("frame") == expected_missing_frame+1
                        and e.get("camera_valid") == "1" and e.get("current_frame_draw_count") == "1"
                        and int(e["camera_last_update_frame"]) == expected_missing_frame+1]
            require(recovery, "Camera failed to recover in the next selected-mesh frame.")
            result["cameraLifecycle"] = {"passed": True, "missingFrame": expected_missing_frame,
                                         "missingSnapshots": missing, "recoverySnapshots": recovery}
        result["passed"] = not errors and len(samples) >= expected_samples
    except (ValueError, KeyError, OSError, TypeError, np.linalg.LinAlgError, OverflowError) as error:
        errors.append(str(error))
    result["limits"] = ["Verifies offscreen GPU-completed buffers and immediate before/after targets; it does not authorize visual composition.",
                        "CPU ray comparison uses the validated anchored static mesh, original index topology and actual renderer camera.",
                        "The CPU reference uses nearest two-sided closed surfaces and permits only one-pixel silhouette precision differences.",
                        "Nonzero radiance is reported, not required, because the game lighting has not been reconstructed."]
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, help="One probe session directory, or replay directory containing only one session.")
    parser.add_argument("--runtime-log", type=Path, required=True)
    parser.add_argument("--geometry-directory", type=Path, default=DEFAULT_GEOMETRY)
    parser.add_argument("--metadata", type=Path, action="append")
    parser.add_argument("--expected-missing-frame", type=int)
    parser.add_argument("--expected-samples", type=int, default=1)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = verify(args.directory, args.runtime_log, args.geometry_directory, args.expected_missing_frame,
                    args.expected_samples, metadata_files=args.metadata)
    output = args.output or args.directory/"probe_verification.json"
    output.write_text(json.dumps(report, indent=2, allow_nan=False)+"\n", encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "sampleCount": len(report["samples"]), "errors": report["errors"], "output": str(output)}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
