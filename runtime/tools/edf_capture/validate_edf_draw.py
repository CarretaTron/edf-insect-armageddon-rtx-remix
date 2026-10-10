#!/usr/bin/env python3
"""Evidence checks for one observed EDF SM3 shader pair, never a Runtime decoder.

Uses original captured attributes and executes the straight-line VS instructions.
Camera factorization is a measured fit, in an explicitly chosen object anchor.
No engine-global translation, GPU output or texture pixels are inferred.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import struct
from pathlib import Path

from analyze_capture import BlobStore, CaptureError, draw_vertex_indices, read_records, validate_geometry


VS_HASH = "f098f457d9ae603bea5f84ec985d473c29cfb0c9"
PS_HASH = "44b92a29eb3e5f2ee6d53f02e14fb11a01511da8"
LAYOUT = [(0, 0, 5, 7), (1, 0, 5, 3), (2, 0, 7, 0),
          (3, 0, 5, 6), (4, 0, 6, 5)]
FORMATS = {5: "<4B", 6: "<2h", 7: "<4h"}


def dot(left, right):
    return sum(a * b for a, b in zip(left, right))


def norm(value):
    return math.sqrt(dot(value, value))


def mul(left, right):
    return [[sum(left[i][k] * right[k][j] for k in range(len(right)))
             for j in range(len(right[0]))] for i in range(len(left))]


def inverse(matrix):
    n = len(matrix)
    rows = [list(row) + [float(i == j) for j in range(n)] for i, row in enumerate(matrix)]
    for i in range(n):
        pivot = max(range(i, n), key=lambda j: abs(rows[j][i]))
        if abs(rows[pivot][i]) < 1e-12:
            raise CaptureError("Singular measured basis.")
        rows[i], rows[pivot] = rows[pivot], rows[i]
        scale = rows[i][i]
        rows[i] = [v / scale for v in rows[i]]
        for j in range(n):
            if j != i:
                scale = rows[j][i]
                rows[j] = [a - scale * b for a, b in zip(rows[j], rows[i])]
    return [row[n:] for row in rows]


def determinant3(m):
    return (m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
            - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
            + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]))


def execute_straight_line(text, inputs, constants):
    """Evaluate only mov/mul/mad/add, including swizzles and destination masks.

    Arithmetic uses Python double precision. This checks algebra/data flow;
    it deliberately does not claim bit-exact GPU FMA or raster agreement.
    Unknown opcodes and reads of uninitialized components are errors.
    """
    registers = {key: list(value) for key, value in inputs.items()}
    registers.update({f"c{i}": list(value) for i, value in enumerate(constants)})

    count = 0
    for original in text.splitlines():
        line = original.split("//", 1)[0].strip().strip("\x00")
        if not line or line == "vs_3_0" or line.startswith("dcl_"):
            continue
        opcode, operands = line.split(None, 1)
        if opcode not in {"mov", "mul", "mad", "add"}:
            raise CaptureError(f"Unsupported opcode {opcode!r}.")
        fields = [v.strip() for v in operands.split(",")]
        destination = re.fullmatch(r"([ro]\d+)(?:\.([xyzw]{1,4}))?", fields[0])
        if not destination:
            raise CaptureError(f"Unsupported destination {fields[0]!r}.")
        key, mask = destination.groups()
        mask = mask or "xyzw"
        # Source components outside the destination mask need not be initialized.
        sources = []
        for operand in fields[1:]:
            match = re.fullmatch(r"(-?)([vroc]\d+)(?:\.([xyzw]{1,4}))?", operand)
            if not match:
                raise CaptureError(f"Unsupported shader operand {operand!r}.")
            negative, src, swizzle = match.groups()
            if src not in registers:
                raise CaptureError(f"Uninitialized register {src!r}.")
            swizzle = swizzle or "xyzw"
            swizzle += swizzle[-1] * (4 - len(swizzle))
            vector = []
            for component in mask:
                v = registers[src]["xyzw".index(swizzle["xyzw".index(component)])]
                if v is None:
                    raise CaptureError(f"Uninitialized source {operand!r}.")
                vector.append(-v if negative else v)
            sources.append(vector)
        expected = {"mov": 1, "mul": 2, "add": 2, "mad": 3}[opcode]
        if len(sources) != expected:
            raise CaptureError("Incorrect operand count.")
        result = registers.setdefault(key, [None] * 4)
        for j, component in enumerate(mask):
            a = sources[0][j]
            value = (a if opcode == "mov" else a * sources[1][j] if opcode == "mul"
                     else a + sources[1][j] if opcode == "add"
                     else a * sources[1][j] + sources[2][j])
            result["xyzw".index(component)] = value
        count += 1
    return registers, count


def decode_inputs(state, store, vertex):
    elements = state["vertexDeclaration"]["elements"]
    layout = [(e["stream"], e["offset"], e["type"], e["usage"]) for e in elements]
    if layout != LAYOUT or any(e["usageIndex"] or e["method"] for e in elements):
        raise CaptureError("Draw does not match the measured declaration.")
    streams = {s["stream"]: s for s in state["streams"]}
    result = {}
    for register, element in enumerate(elements):
        stream = streams[element["stream"]]
        if stream["frequency"] != 1:
            raise CaptureError("Instanced streams are outside this experiment.")
        reference = stream["blob"]
        data = store.read(reference)
        offset = stream["offset"] + vertex * stream["stride"] + element["offset"] - reference["byteOffset"]
        fmt = FORMATS[element["type"]]
        if offset < 0 or offset + struct.calcsize(fmt) > len(data):
            raise CaptureError("Attribute exceeds the captured range.")
        value = list(struct.unpack_from(fmt, data, offset))
        if element["type"] == 6:
            value += [0, 1]
        result[f"v{register}"] = value
    return result


def fit_camera(constants):
    """Factor measured local-to-clip using the shader's separate linear basis.

    Adopt translation zero for this one object, preserving its measured basis.
    The proper-rotation RH view convention is chosen explicitly, not claimed
    as a unique factorization or as the original engine's absolute origin.
    """
    local_clip = [list(v) for v in constants[:4]]
    basis = [list(v[:3]) for v in constants[4:7]]
    camera_local = list(constants[7][:3])
    camera_anchor = mul([camera_local], basis)[0]
    reduced = mul(inverse(basis), local_clip[:3])
    columns = list(zip(*reduced))
    fx, fy = norm(columns[0]), norm(columns[1])
    w_length = norm(columns[3])
    if not all(math.isfinite(v) for row in constants[:10] for v in row) or min(fx, fy, w_length) < 1e-12:
        raise CaptureError("Nonfinite or degenerate measured camera coefficients.")
    a = dot(columns[2], columns[3]) / dot(columns[3], columns[3])
    camera_clip = [dot(camera_local, [local_clip[i][j] for i in range(3)]) + local_clip[3][j]
                   for j in range(4)]
    b = camera_clip[2]
    if a <= 1 or b >= 0:
        raise CaptureError("Depth coefficients do not fit the tested finite RH projection.")
    rotation = [[reduced[i][0] / fx, reduced[i][1] / fy, -reduced[i][3]] for i in range(3)]
    projection = [[fx, 0, 0, 0], [0, fy, 0, 0], [0, 0, -a, -1], [0, 0, b, 0]]
    view = [row + [0] for row in rotation]
    view += [[-dot(camera_anchor, [rotation[i][j] for i in range(3)]) for j in range(3)] + [1]]
    world = [row + [0] for row in basis] + [[0, 0, 0, 1]]
    reconstructed = mul(mul(world, view), projection)
    orthogonality = max(abs(dot([rotation[k][i] for k in range(3)],
                                [rotation[k][j] for k in range(3)]) - float(i == j))
                        for i in range(3) for j in range(3))
    metrics = {"worldLinear": basis, "cameraLocal": camera_local, "cameraAnchor": camera_anchor,
               "viewRowMajor": view, "projectionRowMajor": projection, "worldAnchorRowMajor": world,
               "rotationDeterminant": determinant3(rotation), "rotationOrthogonalityError": orthogonality,
               "clipWColumnLength": w_length, "depthColumnParallelError":
               max(abs(columns[2][i] - a * columns[3][i]) for i in range(3)),
               "cameraMappedToClip": camera_clip, "depthA": a, "depthB": b,
               "nearEstimate": -b / a, "farEstimateIllConditioned": -b / (a - 1),
               "horizontalFovDegrees": math.degrees(2 * math.atan(1 / fx)),
               "verticalFovDegrees": math.degrees(2 * math.atan(1 / fy)), "aspect": fy / fx,
               "combinedMatrixMaxError": max(abs(reconstructed[i][j] - local_clip[i][j])
                                              for i in range(4) for j in range(4)),
               "meaning": "validated_camera_fit_in_selected_object_anchor_not_engine_absolute_world"}
    if orthogonality > 2e-5 or abs(w_length - 1) > 2e-5 or abs(determinant3(rotation) - 1) > 2e-5:
        raise CaptureError("Measured matrix does not support the tested proper-rotation RH camera fit.")
    if max(abs(camera_clip[i]) for i in (0, 1, 3)) > 0.002 or metrics["depthColumnParallelError"] > 2e-5:
        raise CaptureError("Camera-local and perspective invariants failed.")
    return metrics, reconstructed


def geometry_key(state):
    active = {e["stream"] for e in state["vertexDeclaration"]["elements"]}
    payload = {"decl": state["vertexDeclaration"]["elements"],
               "streams": [(s["stream"], s["stride"], s["blob"].get("hash"))
                           for s in state["streams"] if s["stream"] in active],
               "indices": state["indices"]["blob"].get("hash")}
    return hashlib.sha1(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def load_capture(path):
    records, errors = read_records(path / "records.jsonl")
    if errors:
        raise CaptureError("Malformed JSONL: " + "; ".join(errors))
    states = {r["id"]: r["state"] for r in records if r["type"] == "state"}
    shaders = {r["hash"]: r for r in records if r["type"] == "shader"}
    draws = [dict(r, state=states[r["stateId"]]) for r in records if r["type"] == "draw"]
    status = records[-1] if records and records[-1]["type"] == "capture_status" else {}
    complete = bool(status and not status.get("truncated") and not status.get("error")
                    and any(r["type"] == "capture_end" and r["reason"] == "window_complete" for r in records))
    return draws, shaders, {"capture": path.name, "complete": complete, "status": status,
                             "recordedDraws": len(draws), "frames": sorted({d["frame"] for d in draws})}


def verify_draw(path, draw, shaders):
    store = BlobStore(path)
    state = draw["state"]
    if state["vertexShader"].get("hash") != VS_HASH or state["pixelShader"].get("hash") != PS_HASH:
        raise CaptureError("Shader pair is outside this experiment.")
    disassemblies = {}
    for digest in (VS_HASH, PS_HASH):
        shader = shaders[digest]
        if hashlib.sha1(store.read(shader["bytecode"])).hexdigest() != digest:
            raise CaptureError("Original shader bytecode identity mismatch.")
        disassemblies[digest] = store.read(shader["disassembly"]).decode().strip("\x00")
    geometry, _, unique = validate_geometry(draw, store)
    if geometry["errors"] or not draw.get("inputRangeValid"):
        raise CaptureError("Invalid geometry: " + "; ".join(geometry["errors"]))
    if draw["args"]["primitiveType"] != 4:
        raise CaptureError("Only triangle lists are tested.")
    rs = state["renderStates"]
    if rs[27] or rs[15] or rs[7] != 1 or rs[14] != 1:
        raise CaptureError("Draw does not match the observed opaque depth-writing state.")
    cb = store.read(state["vsConstants"]["float"])
    constants = list(struct.iter_unpack("<4f", cb))
    if state["vsConstants"]["float"]["firstRegister"] != 0:
        raise CaptureError("Unexpected float register base.")
    fit, fitted_matrix = fit_camera(constants)
    maximum_clip = maximum_uv = maximum_pixel = maximum_normal = 0.0
    vertices = []
    for vertex in unique:
        inputs = decode_inputs(state, store, vertex)
        output, slots = execute_straight_line(disassemblies[VS_HASH], inputs, constants)
        p = [v * constants[9][0] for v in inputs["v2"][:3]]
        uv = [v * constants[9][1] for v in inputs["v4"][:2]]
        q = [sum(p[i] * constants[i][j] for i in range(3)) + constants[3][j] for j in range(4)]
        normal_local = [v * constants[9][2] - constants[9][3] for v in inputs["v1"][:3]]
        normal_world = mul([normal_local], fit["worldLinear"])[0]
        maximum_clip = max(maximum_clip, *(abs(a - b) for a, b in zip(q, output["o0"])))
        maximum_uv = max(maximum_uv, *(abs(a - b) for a, b in zip(uv, output["o1"][1:3])))
        maximum_normal = max(maximum_normal, *(abs(a - b) for a, b in zip(normal_world, output["o4"][:3])))
        fitted_q = mul([p + [1]], fitted_matrix)[0]
        if q[3] <= 0 or fitted_q[3] <= 0:
            raise CaptureError("Selected object has vertices behind the measured camera.")
        viewport = state["viewport"]
        pixel_error = max(abs(q[i] / q[3] - fitted_q[i] / fitted_q[3]) * viewport["width" if i == 0 else "height"] / 2
                          for i in range(2))
        maximum_pixel = max(maximum_pixel, pixel_error)
        vertices.append({"index": vertex, "positionLocal": p, "uv": uv, "normalLocal": normal_local,
                         "clipFromExecutedShader": output["o0"], "screen":
                         [viewport["x"] + (q[0] / q[3] + 1) * viewport["width"] / 2,
                          viewport["y"] + (1 - q[1] / q[3]) * viewport["height"] / 2]})
    indices, _ = draw_vertex_indices(draw, store)
    positions = [v["positionLocal"] for v in vertices]
    result = {"capture": path.name, "frame": draw["frame"], "draw": draw["draw"], "args": draw["args"],
              "geometryKey": geometry_key(state), "geometry": geometry, "cameraFit": fit,
              "registersC0ThroughC9": constants[:10], "vertexShader": VS_HASH, "pixelShader": PS_HASH,
              "positionEquationMaxError": maximum_clip, "uvEquationMaxError": maximum_uv,
              "normalEquationMaxError": maximum_normal, "fittedProjectionMaxPixelError": maximum_pixel,
              "executedVsInstructionsPerVertex": slots, "vertices": vertices, "indices": indices,
              "decodedBounds": {"minimum": [min(p[i] for p in positions) for i in range(3)],
                                "maximum": [max(p[i] for p in positions) for i in range(3)]},
              "textures": state["textures"][:3], "renderTargets": state["renderTargets"],
              "depthStencil": state["depthStencil"], "strides":
              {str(s["stream"]): s["stride"] for s in state["streams"]},
              "gpuRasterAgreement": "not_measured", "texturePixels": "not_captured",
              "category": "static_opaque_candidate_observed_not_entity_identification"}
    return result, disassemblies


def export_object(output, result):
    vertices = result["vertices"]
    mapping = {v["index"]: i + 1 for i, v in enumerate(vertices)}
    lines = ["# EDF measured shader-local positions and UV, no engine-world translation or texture image."]
    for tag, key in (("v", "positionLocal"), ("vt", "uv"), ("vn", "normalLocal")):
        lines.extend(tag + " " + " ".join(format(x, ".10g") for x in v[key]) for v in vertices)
    indices = result["indices"]
    lines.extend("f " + " ".join(f"{mapping[v]}/{mapping[v]}/{mapping[v]}" for v in indices[i:i+3])
                 for i in range(0, len(indices), 3))
    (output / "selected_object.obj").write_text("\n".join(lines) + "\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture_root", type=Path)
    parser.add_argument("--additional-capture-root", type=Path, action="append", default=[],
                        help="Include later captures while retaining the original measured reference.")
    parser.add_argument("--reference", default="1791384430306-28796-6")
    parser.add_argument("--draw", type=int, default=284)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    paths = sorted(p for root in [args.capture_root] + args.additional_capture_root
                   for p in root.iterdir() if (p / "records.jsonl").is_file())
    if len({p.name for p in paths}) != len(paths):
        parser.error("Capture names must be unique across the input roots.")
    reference_path = next((p for p in paths if p.name == args.reference), None)
    if reference_path is None:
        parser.error("Reference capture was not found in the input roots.")
    loaded = {p.name: load_capture(p) for p in paths}
    draws, shaders, _ = loaded[args.reference]
    reference = next(d for d in draws if d["draw"] == args.draw)
    selected, disassemblies = verify_draw(reference_path, reference, shaders)
    key = selected["geometryKey"]
    matches, failures = [], []
    for p in paths:
        draws, shaders, inventory = loaded[p.name]
        for draw in draws:
            s = draw["state"]
            if s["vertexShader"].get("hash") != VS_HASH or s["pixelShader"].get("hash") != PS_HASH:
                continue
            if geometry_key(s) != key:
                continue
            try:
                result, _ = verify_draw(p, draw, shaders)
                result["completeCapture"] = inventory["complete"]
                matches.append(result)
            except (CaptureError, KeyError, ValueError) as error:
                failures.append({"capture": p.name, "draw": draw["draw"], "error": str(error)})
    args.output.mkdir(parents=True, exist_ok=True)
    for digest, text in disassemblies.items():
        (args.output / (digest + ".dxso.dis")).write_text(text)
    export_object(args.output, selected)
    report = {"scope": "one_measured_shader_pair_and_one_mesh", "reference": selected,
              "inventory": [value[2] for value in loaded.values()], "matchedDraws": matches, "failures": failures,
              "limitations": ["Software execution proves the VS algebra, not measured GPU outputs or raster overlap.",
                              "Absolute engine-world translation is absent; selected-object translation is explicitly zero.",
                              "Cross-frame byte equality is positive evidence over these samples, not proof of permanence.",
                              "Far-plane inversion amplifies float32 coefficient and camera-local rounding errors.",
                              "Texture descriptors are not content hashes; alpha in sampler0 is material data."]}
    (args.output / "validation.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"output": str(args.output), "matches": len(matches), "failures": failures,
                      "positionMaxError": max(r["positionEquationMaxError"] for r in matches),
                      "uvMaxError": max(r["uvEquationMaxError"] for r in matches),
                      "fittedProjectionMaxPixelError": max(r["fittedProjectionMaxPixelError"] for r in matches)}))
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
