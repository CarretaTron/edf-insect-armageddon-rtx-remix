"""Evidence-first selection of one mesh from the three complete EDF recaptures.

This is an offline analysis artifact, not a generic Runtime classifier.
Run from any working directory with the repository's Python environment.
"""
import collections
import hashlib
import itertools
import json
import math
import statistics
import struct
import sys
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[3]
OUTPUT = Path(__file__).parent
sys.path.insert(0, str(WORKSPACE / "runtime/tools/edf_capture"))
from validate_edf_draw import (BlobStore, PS_HASH, VS_HASH, decode_inputs, dot,
                              export_object, fit_camera, geometry_key,
                              load_capture, mul, norm, verify_draw)
from analyze_capture import draw_vertex_indices, validate_geometry

SELECTED_KEY = "925aa18481228b01392a574dbe28ba4e21aa1616"


def constants(store, state, stage):
    return list(struct.iter_unpack("<4f", store.read(state[stage + "Constants"]["float"])))


def mesh_stats(draw, store, cb):
    state = draw["state"]
    geometry, _, unique = validate_geometry(draw, store)
    positions, uvs, normals, screens = [], [], [], []
    for vertex in unique:
        inp = decode_inputs(state, store, vertex)
        p = [v * cb[9][0] for v in inp["v2"][:3]]
        q = [sum(p[i] * cb[i][j] for i in range(3)) + cb[3][j] for j in range(4)]
        if q[3] <= 0:
            screens.append(None)
        else:
            vp = state["viewport"]
            screens.append([vp["x"] + (q[0] / q[3] + 1) * vp["width"] / 2,
                            vp["y"] + (1 - q[1] / q[3]) * vp["height"] / 2])
        positions.append(p)
        uvs.append([v * cb[9][1] for v in inp["v4"][:2]])
        normals.append([v * cb[9][2] - cb[9][3] for v in inp["v1"][:3]])
    valid_screens = [v for v in screens if v]
    vp = state["viewport"]
    screen_bounds = [[min(v[i] for v in valid_screens), max(v[i] for v in valid_screens)]
                     for i in range(2)] if valid_screens else None
    fully_inside = (len(valid_screens) == len(unique) and all(
        vp["x"] <= v[0] <= vp["x"] + vp["width"] and
        vp["y"] <= v[1] <= vp["y"] + vp["height"] for v in valid_screens))
    indices, _ = draw_vertex_indices(draw, store)
    return {"usedVertices": len(unique), "rawIndexRange": [min(indices), max(indices)],
            "contiguousUsedRange": len(unique) == max(indices) - min(indices) + 1,
            "triangleCount": draw["args"]["primitiveCount"], "geometryErrors": geometry["errors"],
            "screenBounds": screen_bounds, "allVerticesInsideViewport": fully_inside,
            "normalLengthRange": [min(map(norm, normals)), max(map(norm, normals))],
            "uvBounds": [[min(v[i] for v in uvs), max(v[i] for v in uvs)] for i in range(2)]}


def topology_stats(result):
    vertices = {v["index"]: v for v in result["vertices"]}
    areas, uv_areas, normal_cosines = [], [], []
    adjacency = collections.defaultdict(set)
    indices = result["indices"]
    for i in range(0, len(indices), 3):
        a, b, c = [vertices[v] for v in indices[i:i+3]]
        points = [tuple(v["positionLocal"]) for v in (a, b, c)]
        for point in points:
            adjacency[point].update(points)
        ab = [y - x for x, y in zip(a["positionLocal"], b["positionLocal"])]
        ac = [y - x for x, y in zip(a["positionLocal"], c["positionLocal"])]
        cross = [ab[1]*ac[2]-ab[2]*ac[1], ab[2]*ac[0]-ab[0]*ac[2], ab[0]*ac[1]-ab[1]*ac[0]]
        areas.append(norm(cross) / 2)
        normal = [sum(v["normalLocal"][j] for v in (a, b, c)) / 3 for j in range(3)]
        normal_cosines.append(dot(cross, normal) / norm(cross) / norm(normal) if norm(cross) else 0)
        au, bu, cu = [v["uv"] for v in (a, b, c)]
        uv_areas.append(abs((bu[0]-au[0])*(cu[1]-au[1])-(bu[1]-au[1])*(cu[0]-au[0])) / 2)
    seen, components = set(), []
    for point in adjacency:
        if point in seen:
            continue
        pending, connected = [point], []
        while pending:
            point = pending.pop()
            if point in seen:
                continue
            seen.add(point)
            connected.append(point)
            pending.extend(adjacency[point] - seen)
        components.append({"uniquePositionCount": len(connected),
                           "minimum": [min(p[i] for p in connected) for i in range(3)],
                           "maximum": [max(p[i] for p in connected) for i in range(3)]})
    return {"triangleAreaRange": [min(areas), max(areas)],
            "degeneratePositionTriangles": sum(a <= 1e-9 for a in areas),
            "uvTriangleAreaRange": [min(uv_areas), max(uv_areas)],
            "degenerateUvTriangles": sum(a <= 1e-12 for a in uv_areas),
            "windingNormalCosineRange": [min(normal_cosines), max(normal_cosines)],
            "opposingVertexNormals": sum(c <= 0 for c in normal_cosines),
            "connectedComponentsAfterExactPositionWeld": components,
            "componentInterpretation": "six baked static parts in one draw, not six instanced streams"}


def compare(left, right, consensus):
    a, b = left["cameraFit"], right["cameraFit"]
    delta = [y - x for x, y in zip(a["cameraAnchor"], b["cameraAnchor"])]
    f, g = [[row[2] for row in fit["viewRowMajor"][:3]] for fit in (a, b)]
    cross = [f[1]*g[2]-f[2]*g[1], f[2]*g[0]-f[0]*g[2], f[0]*g[1]-f[1]*g[0]]
    median = consensus["cameraDeltaMedian"]
    return {"captures": [left["capture"], right["capture"]],
            "draws": [left["draw"], right["draw"]],
            "geometryKeyEqual": left["geometryKey"] == right["geometryKey"],
            "decodedVerticesEqual": all(a[k] == b[k] for a, b in zip(left["vertices"], right["vertices"])
                                        for k in ("positionLocal", "normalLocal", "uv")),
            "indicesEqual": left["indices"] == right["indices"],
            "objectBasisEqual": a["worldLinear"] == b["worldLinear"],
            "cameraDeltaChosenAnchor": delta, "cameraDeltaLength": norm(delta),
            "cameraForwardAngleDegrees": math.degrees(math.atan2(norm(cross), dot(f, g))),
            "maxProjectedVertexDisplacementPixels": max(abs(x-y)
                for va, vb in zip(left["vertices"], right["vertices"])
                for x, y in zip(va["screen"], vb["screen"])),
            "independentTransformPairs": consensus["distinctTransformPairs"],
            "sceneMedianCameraDelta": median,
            "maxDeltaResidualToSceneMedian": max(abs(x-y) for x, y in zip(delta, median)),
            "sceneMaxAbsoluteResidual": consensus["maxAbsoluteResidual"]}


def write_replay(path, draw, shaders, result):
    state, store = draw["state"], BlobStore(path)
    root = OUTPUT / "replay"
    root.mkdir(exist_ok=True)
    frame_root = root / str(draw["frame"])
    frame_root.mkdir(exist_ok=True)
    references = {}
    for stage, digest in (("vs", VS_HASH), ("ps", PS_HASH)):
        name = stage + "_original.dxso"
        (root / name).write_bytes(store.read(shaders[digest]["bytecode"]))
        references[stage + "Bytecode"] = {"file": "replay/" + name, "sha1": digest}
        for bank in ("float", "int", "bool"):
            ref = state[stage + "Constants"][bank]
            name = stage + "_constants_" + bank + ".bin"
            (frame_root / name).write_bytes(store.read(ref))
            references[stage + "Constants" + bank.title()] = dict(ref, file=f"replay/{draw['frame']}/{name}")
    streams = []
    for s in state["streams"][:5]:
        name = f"stream{s['stream']}_used.bin"
        (root / name).write_bytes(store.read(s["blob"]))
        streams.append(dict(s, replayFile="replay/" + name))
    (root / "indices_original_used.bin").write_bytes(store.read(state["indices"]["blob"]))
    return {"capture": path.name, "frame": draw["frame"], "draw": draw["draw"],
            "originalArgs": draw["args"], "stateId": draw["stateId"],
            "vertexDeclaration": state["vertexDeclaration"], "streams": streams,
            "indices": dict(state["indices"], replayFile="replay/indices_original_used.bin"),
            "shaderReferences": references, "textures": state["textures"][:3],
            "samplerStates": state["samplerStates"][:3], "renderStates": state["renderStates"],
            "renderTargets": state["renderTargets"], "depthStencil": state["depthStencil"],
            "viewport": state["viewport"], "scissor": state["scissor"],
            "transforms": state["transforms"], "cameraFit": result["cameraFit"],
            "firstUsedVertex": 630, "lastUsedVertex": 881,
            "replayInstructions": "These are used ranges, not whole source buffers. Pad each stream with 630 vertices and append the used range, then bind at offset 0. Use original 432 indices 630..881 from index offset 0, DrawIndexedPrimitive(TRIANGLELIST,0,0,882,0,144). Original buffer IDs and source offsets are evidence only. For an exact original-args replay, pad the index buffer with 70707 indices and use startIndex 70707. Texture pixels were not captured; synthetic replay textures cannot prove original material appearance."}


def main():
    paths = sorted(p for p in (WORKSPACE / "captures/edf-static").iterdir()
                   if (p / "records.jsonl").is_file())
    if len(paths) != 3:
        raise RuntimeError("Expected exactly the three controlled recaptures.")
    loaded = [load_capture(p) for p in paths]
    if not all(inventory["complete"] for _, _, inventory in loaded):
        raise RuntimeError("Incomplete capture blocks selection.")
    integrity = json.loads((OUTPUT / "integrity.json").read_text())
    if not integrity["allThreeCompleteAndIntegral"]:
        raise RuntimeError("Full window integrity audit blocks selection.")
    for path, audit in zip(paths, integrity["windows"]):
        digest = hashlib.sha256((path / "records.jsonl").read_bytes()).hexdigest()
        if path.name != audit["capture"] or digest != audit["recordsSha256"]:
            raise RuntimeError("Integrity audit is stale; run audit_windows.py again.")
    groups, stores = [], [BlobStore(p) for p in paths]
    for draws, _, _ in loaded:
        group = collections.defaultdict(list)
        for draw in draws:
            if draw["state"]["vertexShader"].get("hash") == VS_HASH and draw["state"]["pixelShader"].get("hash") == PS_HASH:
                group[geometry_key(draw["state"])].append(draw)
        groups.append(group)
    common = set.intersection(*(set(g) for g in groups))
    ranking = []
    for key in common:
        occurrences = [g[key] for g in groups]
        draws = [v[0] for v in occurrences]
        cb = [constants(s, d["state"], "vs") for s, d in zip(stores, draws)]
        pc = [constants(s, d["state"], "ps") for s, d in zip(stores, draws)]
        basis_equal = all(v[4:7] == cb[0][4:7] for v in cb)
        scales_equal = all(v[9] == cb[0][9] for v in cb)
        white = all(tuple(v[1][:3]) == (1, 1, 1) for v in pc)
        unique = all(len(v) == 1 for v in occurrences)
        row = {"geometryKey": key, "draws": [[d["draw"] for d in v] for v in occurrences],
               "occurrenceCounts": list(map(len, occurrences)),
               "basisEqual": basis_equal, "c9Equal": scales_equal,
               "psC1": [v[1] for v in pc], "unmodifiedTextureRgbIsDiffuse": white,
               "primitiveCount": draws[0]["args"]["primitiveCount"],
               "uniqueOccurrenceEachFrame": unique,
               "allActiveStreamFrequenciesOne": all(all(s["frequency"] == 1 for s in d["state"]["streams"][:5]) for d in draws),
               "opaqueDepthWriting": all(not d["state"]["renderStates"][27] and not d["state"]["renderStates"][15]
                   and d["state"]["renderStates"][7] == 1 and d["state"]["renderStates"][14] == 1 for d in draws),
               "shaderFlow": "26 straight-line VS instructions; no skinning, relative addressing, loops or vertex texture fetch"}
        # Project all simple alternatives, rather than automatically adopting a previously noticed vertex count.
        if unique and basis_equal and scales_equal and row["primitiveCount"] <= 200:
            row["measurements"] = [mesh_stats(d, s, c) for d, s, c in zip(draws, stores, cb)]
            row["allVerticesInsideViewportThreeFrames"] = all(r["allVerticesInsideViewport"] for r in row["measurements"])
        else:
            indices, _ = draw_vertex_indices(draws[0], stores[0])
            row["usedVertices"] = len(set(indices))
            row["allVerticesInsideViewportThreeFrames"] = None
        row["selected"] = key == SELECTED_KEY
        ranking.append(row)
    ranking.sort(key=lambda r: (not r["uniqueOccurrenceEachFrame"], not r["basisEqual"],
                               not r["unmodifiedTextureRgbIsDiffuse"],
                               r["allVerticesInsideViewportThreeFrames"] is not True,
                               r["primitiveCount"], r["geometryKey"]))
    chosen = [g[SELECTED_KEY] for g in groups]
    if not all(len(v) == 1 for v in chosen):
        raise RuntimeError("Selected mesh has ambiguous or missing occurrence.")
    results, replays = [], []
    for path, rows, (_, shaders, inventory) in zip(paths, chosen, loaded):
        result, disassemblies = verify_draw(path, rows[0], shaders)
        if result["fittedProjectionMaxPixelError"] > 0.001:
            raise RuntimeError("Projection error blocks the prototype.")
        result["completeCapture"] = inventory["complete"]
        result["topology"] = topology_stats(result)
        if result["topology"]["degeneratePositionTriangles"] or result["topology"]["degenerateUvTriangles"]:
            raise RuntimeError("Degenerate geometry or UV blocks this choice.")
        result["inputState"] = rows[0]["state"]
        results.append(result)
        replays.append(write_replay(path, rows[0], shaders, result))
        for digest, text in disassemblies.items():
            (OUTPUT / (digest + ".dxso.dis")).write_text(text)
    comparisons = json.loads((WORKSPACE / "analysis/edf/next_validation/comparison.json").read_text())
    pair_rows = []
    for i, j in itertools.combinations(range(3), 2):
        consensus = next(p for p in comparisons["pairs"] if p["captures"] == [paths[i].name, paths[j].name])
        pair_rows.append(compare(results[i], results[j], consensus))
    first_state = chosen[0][0]["state"]
    profile = {"profile": "edf_static_252_144_experimental", "enabledByDefault": False,
               "executable": "EDF-IA.exe",
               "executableSha256": "87ba53c92c1377ba50b98cae00860afe23e6cdcad7f95850166c1b83bf83082d",
               "vertexShaderOriginalSha1": VS_HASH, "pixelShaderOriginalSha1": PS_HASH,
               "vertexDeclaration": first_state["vertexDeclaration"],
               "vertexCountUsedRange": 252, "triangleCount": 144, "rawIndexMinimum": 630,
               "rawIndexMaximum": 881, "indexFormat": "UINT16", "indexCount": 432,
               "indexUsedBytesSha1": first_state["indices"]["blob"]["hash"],
               "activeStreams": [{"stream": s["stream"], "stride": s["stride"], "frequency": 1,
                                   "usedBytes": s["blob"]["size"], "sha1": s["blob"]["hash"]}
                                  for s in first_state["streams"][:5]],
               "requiredVsC4ThroughC6": results[0]["registersC0ThroughC9"][4:7],
               "requiredVsC9": results[0]["registersC0ThroughC9"][9],
               "requiredPsC1Rgb": [1, 1, 1], "requiredPsC0X": 0.5,
               "opaqueRenderStates": {str(k): first_state["renderStates"][k] for k in (7,14,15,22,27,168,190,191,192)},
               "texture0Descriptor": {k: v for k, v in first_state["textures"][0].items() if k not in ("resource", "content")},
               "texture0SamplerStates": first_state["samplerStates"][0],
               "renderTargetPass": {"count": 3, "formats": [21, 21, 21], "backBuffer": False,
                                     "sameExtentAsViewport": True, "depthFormat": 75},
               "spatialConvention": "FLOAT3 positions=v2.xyz*0.25; object anchor W=identity. Factor perspective RH from c0..3, check camera c7 and separate basis c4..6=identity. No claim about original engine-global translation.",
               "materialConvention": "s0 RGB opaque. PS_c1 RGB white makes material blend sample.rgb*((1-alpha)*white+alpha)=sample.rgb. Preserve sampler/clamp and sRGB=0. Ignore s1/s2 cubemap original lighting in RTX.",
               "preserveOriginalRasterDraw": True,
               "excludeCameraConstantsFromDecodedGeometryIdentity": True,
               "sourceResourceIdsAndDrawNumbersAreNeverRecognitionRules": True}
    export_object(OUTPUT, results[0])
    # Binary data accepted by the existing actual-Remix projection helper probe.
    payload = bytearray(struct.pack("<I", len(results)))
    for r in results:
        payload += struct.pack("<II", r["frame"], len(r["vertices"]))
        for matrix in (r["registersC0ThroughC9"][:4], r["cameraFit"]["worldAnchorRowMajor"],
                       r["cameraFit"]["viewRowMajor"], r["cameraFit"]["projectionRowMajor"]):
            payload += struct.pack("<16f", *(v for row in matrix for v in row))
        for vertex in r["vertices"]:
            payload += struct.pack("<3f", *vertex["positionLocal"])
    (OUTPUT / "runtime_projection_input.bin").write_bytes(payload)
    report = {"scope": "one_new_candidate_validated_in_three_controlled_captures",
              "supersedesOldReferenceGate": "The old 84/56 mesh was absent in all three complete windows. The user explicitly authorized choosing a new candidate; this validation applies only to the selected 252/144 mesh.",
              "decoderGate": {"allowed": True, "mesh": SELECTED_KEY,
                              "scope": "only this exact mesh and shader pair; runtime acceptance still requires GPU test"},
              "inventory": [inventory for _, _, inventory in loaded], "commonGeometryKeys": len(common),
              "uniqueOccurrenceCommonKeys": sum(all(len(g[k]) == 1 for g in groups) for k in common),
              "chosenReason": "Smallest recurrent white-PS_c1 candidate wholly inside the viewport in all three frames. 230/120 white alternative is partially outside viewport. Smaller 20/10 meshes have colored PS_c1 and require alpha-aware material conversion; 104/56 has that complexity too.",
              "reference": results[0], "matchedDraws": results, "failures": [], "comparisons": pair_rows,
              "windowIntegrityAudit": integrity,
              "limitations": ["Shader execution is algebraic CPU validation, not GPU/raster screenshot overlap.",
                              "Geometry/basis equality and independent cross-mesh camera consensus support observed static geometry; future destruction/LOD may remove or replace it.",
                              "The original engine's absolute object translation is not uniquely recoverable; W identity is the chosen fixed anchor.",
                              "Texture pixel contents were not captured; descriptor hashes are not texture content hashes.",
                              "Far plane inferred from depth coefficients is ill-conditioned and should be validated rather than hardcoded."]}
    probe_path = OUTPUT / "runtime_projection_verification.json"
    if probe_path.exists():
        report["actualRemixProjectionHelperVerification"] = json.loads(probe_path.read_text())
    for name, value in (("validation.json", report), ("candidate_ranking.json", ranking),
                        ("profile_manifest.json", profile), ("replay_manifest.json", replays)):
        (OUTPUT / name).write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"chosen": SELECTED_KEY, "draws": [r["draw"] for r in results],
                      "commonKeys": len(common), "uniqueOccurrenceKeys": report["uniqueOccurrenceCommonKeys"],
                      "gate": report["decoderGate"], "cameraComparisons": pair_rows}, indent=2))


if __name__ == "__main__":
    main()
