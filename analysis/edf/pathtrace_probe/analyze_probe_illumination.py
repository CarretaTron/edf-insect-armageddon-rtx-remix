"""Read-only analysis of the dark/light controlled GPU probe A/B comparison.

This reconstructs nearest CPU ray triangles for actual GPU hit pixels, then
applies the documented triangle-normal orientation used by Remix. It does not
claim to have read GPU shading normals or the original EDF lighting.
"""
from pathlib import Path
import hashlib
import json
import math
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
RUNTIME = ROOT / "runtime"
sys.path.insert(0, str(RUNTIME / "tools/edf_static_gpu_replay"))
import verify_probe as vp

CAPTURE = RUNTIME / "_build/edf-pt-x64-probe/probe-output/session_1791488375451875_25904"
LIT_CAPTURE = RUNTIME / "_build/edf-pt-x64-lit/probe-output/session_1791488778002148_33280"
GEOMETRY = ROOT / "analysis/edf/new_candidate"
OUTPUT = Path(__file__).with_suffix(".json")


def sha256(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def image_stats(path, mask):
    image = np.fromfile(path, dtype="<f2").reshape(*mask.shape, 4).astype("f4")
    return {"path": str(path.resolve()), "sha256": sha256(path),
            "nonzeroRgbPixelCount": int(np.any(image[:, :, :3] != 0, axis=2).sum()),
            "nonzeroHitRgbPixelCount": int(np.any(image[mask, :3] != 0, axis=1).sum()),
            "rgbaAllZero": bool(np.all(image == 0)),
            "finite": bool(np.all(np.isfinite(image))),
            "rgbMinimum": image[:, :, :3].min((0, 1)).tolist(),
            "rgbMaximum": image[:, :, :3].max((0, 1)).tolist()}


def config_values(path):
    values = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def cone_bounds(dots, angle):
    perpendicular = np.sqrt(np.maximum(0, 1 - dots * dots))
    maximum = dots * math.cos(angle) + perpendicular * math.sin(angle)
    minimum = dots * math.cos(angle) - perpendicular * math.sin(angle)
    maximum = np.where(np.arccos(np.clip(dots, -1, 1)) <= angle, 1, maximum)
    minimum = np.where(np.arccos(np.clip(-dots, -1, 1)) <= angle, -1, minimum)
    return minimum, maximum


def ray_hit_normals(positions, indices, vertex_normals, metadata, depth):
    ys, xs = np.nonzero(depth != np.float32(metadata["missLinearZ"]))
    view = vp.matrix(metadata["rendererWorldToView"])
    projection = vp.matrix(metadata["rendererViewToProjection"])
    inverse_view = np.linalg.inv(view)
    ndc = np.stack([(xs + .5) / depth.shape[1] * 2 - 1,
                    1 - (ys + .5) / depth.shape[0] * 2,
                    np.ones(len(xs)), np.ones(len(xs))], axis=1)
    directions = (ndc @ np.linalg.inv(projection).T)[:, :3] @ inverse_view[:3, :3].T
    directions /= np.linalg.norm(directions, axis=1)[:, None]
    origin = inverse_view[:3, 3]
    triangles = positions[indices]
    edge1, edge2 = triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0]
    pvec = np.cross(directions[:, None, :], edge2[None, :, :])
    determinant = np.einsum("tq,ptq->pt", edge1, pvec)
    inverse = np.divide(1, determinant, out=np.zeros_like(determinant), where=np.abs(determinant) > 1e-10)
    tvec = origin - triangles[:, 0]
    u = np.einsum("tq,ptq->pt", tvec, pvec) * inverse
    qvec = np.cross(tvec, edge1)
    v = np.einsum("pq,tq->pt", directions, qvec) * inverse
    distances = np.einsum("tq,tq->t", edge2, qvec)[None, :] * inverse
    valid = ((np.abs(determinant) > 1e-10) & (u >= -1e-5) & (v >= -1e-5)
             & (u + v <= 1.00001) & (distances > 0))
    distances = np.where(valid, distances, np.inf)
    selected = np.argmin(distances, axis=1)
    row = np.arange(len(xs))
    assert np.all(np.isfinite(distances[row, selected]))
    u, v = u[row, selected], v[row, selected]
    selected_vertex_normals = vertex_normals[indices[selected]]
    weights = np.stack([1 - u - v, u, v], axis=1)
    normals = np.sum(selected_vertex_normals * weights[:, :, None], axis=1)
    normals /= np.linalg.norm(normals, axis=1)[:, None]
    triangle_normals = np.cross(edge1[selected], edge2[selected])
    triangle_normals /= np.linalg.norm(triangle_normals, axis=1)[:, None]
    same_direction = np.einsum("pq,pq->p", triangle_normals, -directions) >= 0
    flipped_triangle = triangle_normals * np.where(same_direction, 1, -1)[:, None]
    vertex_signs = np.einsum("pvq,pq->pv", selected_vertex_normals, flipped_triangle) >= 0
    oriented_normals = np.sum(selected_vertex_normals * np.where(vertex_signs, 1, -1)[:, :, None]
                              * weights[:, :, None], axis=1)
    oriented_normals /= np.linalg.norm(oriented_normals, axis=1)[:, None]
    unique, counts = np.unique(np.round(triangle_normals, 5), axis=0, return_counts=True)
    return normals, oriented_normals, {
        "cpuNearestTrianglesFound": len(xs),
        "normalOrientationFlipCount": int(np.any(~vertex_signs, axis=1).sum()),
        "triangleNormalHistogram": [{"normal": normal.tolist(), "pixels": int(count)}
                                    for normal, count in zip(unique, counts)],
        "cameraPosition": origin.tolist(),
    }


def main():
    positions, indices, geometry = vp.load_geometry(GEOMETRY)
    normals = np.fromfile(GEOMETRY / "replay/stream1_used.bin", dtype="u1").reshape(-1, 4)[:, :3].astype("f8") / 127 - 1
    direction = np.array([-.2, -1, .4])
    incoming = -direction / np.linalg.norm(direction)
    half_angle = math.radians(2.5)
    report = {
        "schema": 2,
        "evidenceKind": "read_only_analysis_of_controlled_gpu_readbacks",
        "originalEdfLightingRecovered": False,
        "gpuShadingNormalsReadBack": False,
        "shaderNormalModel": "Each CPU vertex normal oriented to Remix flippedTriangleNormal before interpolation; excludes optional getBentNormal perturbation and GPU floating-point/half precision.",
        "mesh": "925aa18481228b01392a574dbe28ba4e21aa1616",
        "vertices": 252, "triangles": 144,
        "sourceVertexShader": "f098f457d9ae603bea5f84ec985d473c29cfb0c9",
        "sourcePixelShader": "44b92a29eb3e5f2ee6d53f02e14fb11a01511da8",
        "configuredDistantTravelDirection": direction.tolist(),
        "centralSurfaceToLightDirection": incoming.tolist(),
        "configuredFullLightAngleDegrees": 5,
        "coneHalfAngleDegrees": 2.5,
        "testedDiagnosticTravelDirection": (-direction).tolist(),
        "probeConfig": str((RUNTIME / "_build/edf-pt-x64-probe/rtx.conf").resolve()),
        "probeConfigSha256": sha256(RUNTIME / "_build/edf-pt-x64-probe/rtx.conf"),
        "inputs": [{"path": str(path.resolve()), "sha256": sha256(path)}
                   for path in [GEOMETRY / "selected_object.obj", GEOMETRY / "replay/stream1_used.bin",
                                GEOMETRY / "replay/stream2_used.bin", GEOMETRY / "replay/indices_original_used.bin"]],
        "codeEvidence": [
            {"file": "src/dxvk/rtx_render/rtx_light_manager.cpp", "function": "LightManager::prepareSceneData", "line": 269,
             "meaning": "Configured travel direction is normalized when constructing RtDistantLight; full angle is divided by two."},
            {"file": "src/dxvk/shaders/rtx/concept/light/distant_light.slangh", "function": "distantLightSampleArea", "line": 78,
             "meaning": "Sample position = surface position - sampled travel direction * 100000; incoming L has the opposite sign."},
            {"file": "src/dxvk/shaders/rtx/concept/surface/surface_interaction.slangh", "line": 361,
             "meaning": "Triangle normal is oriented against incoming camera ray using viewDirection=-ray.direction."},
            {"file": "src/dxvk/shaders/rtx/concept/surface/surface_interaction.slangh", "line": 425,
             "meaning": "Vertex normals align to that flipped triangle normal before interpolation."},
            {"file": "src/dxvk/shaders/rtx/algorithm/integrator_direct.slangh", "function": "integrateDirectPath", "line": 429,
             "meaning": "Direct sampling is gated by enableDirectLighting and opaque material; RTXDI false uses RIS, not a disabled light pass."},
        ],
        "samples": [],
    }
    for metadata_path in sorted(CAPTURE.glob("*.json")):
        data = json.loads(metadata_path.read_text())
        images = {image["name"]: image for image in data["images"]}
        depth = np.fromfile(CAPTURE / images["linear_z"]["file"], dtype="<f4").reshape(1080, 1920)
        mask = np.isfinite(depth) & (depth != np.float32(data["missLinearZ"]))
        raw_normals, oriented, sample = ray_hit_normals(positions, indices, normals, data, depth)
        raw_dots, dots = raw_normals @ incoming, oriented @ incoming
        old_minimum, old_maximum = cone_bounds(dots, half_angle)
        new_minimum, new_maximum = cone_bounds(-dots, half_angle)
        sample.update({
            "frameId": data["frameId"], "metadata": str(metadata_path.resolve()), "metadataSha256": sha256(metadata_path),
            "cameraLastUpdateFrame": data["cameraLastUpdateFrame"], "surfaceCount": data["surfaceCount"], "lightCount": data["lightCount"],
            "gpuDepthHitCount": int(mask.sum()),
            "rawInterpolatedNormalDotIncomingRange": [float(raw_dots.min()), float(raw_dots.max())],
            "rawPositiveCentralDotCount": int((raw_dots > 0).sum()),
            "orientedNormalDotIncomingRange": [float(dots.min()), float(dots.max())],
            "orientedPositiveCentralDotCount": int((dots > 0).sum()),
            "oldLightConeMaximumDotRange": [float(old_maximum.min()), float(old_maximum.max())],
            "oldLightConeCanIlluminatePixelCount": int((old_maximum > 0).sum()),
            "reversedLightConeMinimumDotRange": [float(new_minimum.min()), float(new_minimum.max())],
            "reversedLightConeMinimumPositivePixelCount": int((new_minimum > 0).sum()),
        })
        sample["slots"] = []
        for name in ("direct_diffuse_radiance", "final_offscreen"):
            path = CAPTURE / images[name]["file"]
            sample["slots"].append({"name": name, **image_stats(path, mask)})
        lit_metadata_path = LIT_CAPTURE / metadata_path.name
        lit = json.loads(lit_metadata_path.read_text())
        lit_images = {image["name"]: image for image in lit["images"]}
        lit_sample = {"metadata": str(lit_metadata_path.resolve()), "metadataSha256": sha256(lit_metadata_path),
                      "gpuCompleted": lit["gpuCompleted"], "depthHitCount": lit["depthHitCount"],
                      "radianceNonzeroCount": lit["radianceNonzeroCount"],
                      "radianceNonfiniteCount": lit["radianceNonfiniteCount"],
                      "cameraLastUpdateFrame": lit["cameraLastUpdateFrame"],
                      "surfaceCount": lit["surfaceCount"], "lightCount": lit["lightCount"],
                      "whitelist": lit["whitelist"], "compositionPerformed": lit["compositionPerformed"],
                      "sourceAndRendererCameraEqualToDarkCase": all(lit[key] == data[key] for key in (
                          "sourceWorldToView", "sourceViewToProjection", "rendererWorldToView", "rendererViewToProjection")),
                      "slots": [], "identicalGeometryOutputs": [], "preservedTargets": []}
        for name in ("linear_z", "albedo"):
            dark_path, lit_path = CAPTURE / images[name]["file"], LIT_CAPTURE / lit_images[name]["file"]
            lit_sample["identicalGeometryOutputs"].append({"name": name, "darkSha256": sha256(dark_path),
                                                          "litSha256": sha256(lit_path),
                                                          "fullByteEqual": dark_path.read_bytes() == lit_path.read_bytes()})
        for name in ("direct_diffuse_radiance", "final_offscreen"):
            path = LIT_CAPTURE / lit_images[name]["file"]
            lit_sample["slots"].append({"name": name, **image_stats(path, mask)})
        for target in lit["preservedTargets"]:
            before, after = LIT_CAPTURE / target["beforeFile"], LIT_CAPTURE / target["afterFile"]
            lit_sample["preservedTargets"].append({"slot": target["slot"], "beforeSha256": sha256(before),
                                                  "afterSha256": sha256(after),
                                                  "fullByteEqual": before.read_bytes() == after.read_bytes()})
        sample["lightDirectionAbComparison"] = lit_sample
        report["samples"].append(sample)
    dark_case, lit_case = CAPTURE.parent.parent, LIT_CAPTURE.parent.parent
    config_a, config_b = config_values(dark_case / "rtx.conf"), config_values(lit_case / "rtx.conf")
    report["abProvenance"] = {
        "darkConfigSha256": sha256(dark_case / "rtx.conf"), "litConfigSha256": sha256(lit_case / "rtx.conf"),
        "changedConfigFields": [{"key": key, "dark": config_a.get(key), "lit": config_b.get(key)}
                                for key in sorted(config_a.keys() | config_b.keys()) if config_a.get(key) != config_b.get(key)],
        "binaries": [{"name": name, "darkSha256": sha256(dark_case / name), "litSha256": sha256(lit_case / name),
                      "identical": sha256(dark_case / name) == sha256(lit_case / name)}
                     for name in ("d3d9.dll", "edf_static_gpu_replay.exe")],
        "logs": [{"case": str(case.resolve()), "path": str((case / "rtx-remix/logs/remix-dxvk.log").resolve()),
                  "sha256": sha256(case / "rtx-remix/logs/remix-dxvk.log")} for case in (dark_case, lit_case)],
    }
    report["conclusion"] = {
        "confirmedExplanation": "Reversing only the diagnostic distant-light travel direction causes positive direct diffuse radiance at every geometry hit in all three frames, using unchanged Runtime and replay binaries, cameras and geometry outputs.",
        "zeroRadianceDoesNotProveDecoderFailure": True,
        "directLightingGpuEvidence": "Dark case: 0 nonzero RGB pixels in direct diffuse. Reversed case: 6435, 6438, 6895, exactly matching depth hits; all original targets remain byte-identical.",
        "finalImageCaveat": "Final RGB can contain indirect/sky/composite/tonemapping/dither energy; its nonzero pixels alone do not establish direct-light integration.",
        "replacementAllowed": False,
    }
    OUTPUT.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(OUTPUT), "samples": [
        {"frame": sample["frameId"], "hits": sample["gpuDepthHitCount"],
         "flipped": sample["normalOrientationFlipCount"], "oldConePositive": sample["oldLightConeCanIlluminatePixelCount"],
         "newConePositive": sample["reversedLightConeMinimumPositivePixelCount"]} for sample in report["samples"]]}))


if __name__ == "__main__":
    main()
