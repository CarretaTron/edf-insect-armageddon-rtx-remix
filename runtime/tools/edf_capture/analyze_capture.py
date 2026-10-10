#!/usr/bin/env python3
"""Inspect diagnostic D3D9 captures without assigning unproved game semantics."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import struct
import sys
from collections import Counter
from pathlib import Path
from typing import Any


MAX_ELEMENTS = 2_000_000
DECL_SIZES = [4, 8, 12, 16, 4, 4, 4, 8, 4, 4, 8, 4, 8, 4, 4, 4, 8, 0]
DECL_NAMES = ["FLOAT1", "FLOAT2", "FLOAT3", "FLOAT4", "D3DCOLOR", "UBYTE4",
              "SHORT2", "SHORT4", "UBYTE4N", "SHORT2N", "SHORT4N", "USHORT2N",
              "USHORT4N", "UDEC3", "DEC3N", "FLOAT16_2", "FLOAT16_4", "UNUSED"]
PRIMITIVES = {"POINTLIST": 1, "LINELIST": 2, "LINESTRIP": 3,
              "TRIANGLELIST": 4, "TRIANGLESTRIP": 5, "TRIANGLEFAN": 6}
REGISTER = re.compile(r"(?<![\w])(?:[cibsvoralp]\d+|vPos|vFace|oPos|oFog|oPts|oDepth|o[CDT]\d+)(?![\w])", re.I)


class CaptureError(ValueError):
    pass


class BlobStore:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.cache: dict[tuple[str, str], bytes] = {}

    def read(self, reference: dict[str, Any]) -> bytes:
        if not isinstance(reference, dict) or not reference.get("path"):
            reason = reference.get("reason", "no path") if isinstance(reference, dict) else "no reference"
            raise CaptureError(f"Blob contents are unavailable ({reason}).")
        if reference.get("available") is False:
            raise CaptureError(f"Blob contents are unavailable ({reference.get('reason', 'recorder limit')}).")
        path_text = reference["path"]
        if not isinstance(path_text, str):
            raise CaptureError("Blob path must be a string.")
        path = (self.root / path_text).resolve()
        try:
            path.relative_to(self.root)
        except ValueError as error:
            raise CaptureError("Blob path escapes the capture directory.") from error
        key = (str(path), str(reference.get("hash", "")))
        if key in self.cache:
            return self.cache[key]
        try:
            data = path.read_bytes()
        except OSError as error:
            raise CaptureError(f"Cannot read blob {path_text}: {error}") from error
        if reference.get("size") is not None and len(data) != reference["size"]:
            raise CaptureError(f"Blob {path_text} is truncated or has an incorrect size.")
        expected = reference.get("hash", "").lower()
        if expected and hashlib.sha1(data).hexdigest() != expected:
            raise CaptureError(f"Blob {path_text} failed its SHA1 check.")
        self.cache[key] = data
        return data


def enum_value(value: Any, names: dict[str, int], prefix: str = "") -> int:
    if isinstance(value, int):
        return value
    text = str(value).upper().removeprefix(prefix)
    if text in names:
        return names[text]
    try:
        return int(text, 0)
    except ValueError as error:
        raise CaptureError(f"Unknown enum value: {value}") from error


def primitive_elements(kind: Any, count: int) -> int:
    kind = enum_value(kind, PRIMITIVES, "D3DPT_")
    if count < 0:
        raise CaptureError("Primitive count is negative.")
    if kind not in range(1, 7):
        raise CaptureError(f"Unsupported primitive type: {kind}")
    if count == 0:
        return 0
    return {1: count, 2: count * 2, 3: count + 1, 4: count * 3,
            5: count + 2, 6: count + 2}[kind]


def shader_model(bytecode: bytes) -> str | None:
    if len(bytecode) < 4 or len(bytecode) % 4:
        return None
    token, = struct.unpack_from("<I", bytecode)
    stage = {0xFFFE: "vs", 0xFFFF: "ps"}.get(token >> 16)
    return f"{stage}_{(token >> 8) & 255}_{token & 255}" if stage else None


def analyze_disassembly(text: str) -> dict[str, Any]:
    """Lexical SM1-3 inspection. This deliberately does not emulate shaders."""
    declarations = []
    instructions = []
    definitions: dict[str, list[str]] = {}
    referenced: set[str] = set()
    implicit_constants: set[int] = set()
    model = None
    relative_constants = False
    matrix_instructions = []
    for original in text.splitlines():
        line = original.split("//", 1)[0].strip().rstrip("\x00")
        if not line:
            continue
        if re.fullmatch(r"[vp]s_\d_\d", line, re.I):
            model = line.lower()
            continue
        parts = line.split(None, 1)
        opcode = parts[0].lower()
        if not re.fullmatch(r"[a-z][a-z0-9_]*", opcode):
            continue
        operands = parts[1] if len(parts) == 2 else ""
        registers = REGISTER.findall(operands)
        if opcode.startswith("dcl"):
            if registers:
                register = registers[-1]
                declarations.append({"register": register, "semantic": opcode[4:] or "unspecified",
                                     "instruction": line})
            continue
        if opcode in {"def", "defi", "defb"}:
            fields = [field.strip() for field in operands.split(",")]
            if fields:
                definitions[fields[0]] = fields[1:]
            continue
        referenced.update(registers)
        relative_constants |= bool(re.search(r"\bc\s*\[", operands, re.I))
        instructions.append({"opcode": opcode, "text": line,
                             "destination": operands.split(",", 1)[0].strip()})
        matrix_opcode = opcode.split("_", 1)[0]
        if matrix_opcode in {"m4x4", "m4x3", "m3x4", "m3x3", "m3x2"}:
            constants = [int(reg[1:]) for reg in registers if re.fullmatch(r"c\d+", reg, re.I)]
            if constants:
                first = constants[-1]
                rows = int(matrix_opcode[-1])
                implicit_constants.update(range(first, first + rows))
                matrix_instructions.append({"instruction": line, "firstRegister": first,
                                            "registerCount": rows, "meaning": "unknown"})
    constant_banks = {}
    for bank in ("c", "i", "b"):
        constant_banks[bank] = sorted({int(reg[1:]) for reg in referenced
                                       if re.fullmatch(bank + r"\d+", reg, re.I)} |
                                      (implicit_constants if bank == "c" else set()))
    inputs = [item for item in declarations if re.match(r"v(?:\d+|Pos|Face)$", item["register"], re.I)]
    outputs = [item for item in declarations if item["register"].lower().startswith("o")]
    if not outputs:
        outputs = [{"register": reg, "semantic": "unspecified"} for reg in sorted(referenced)
                   if reg.lower().startswith("o")]
    position_outputs = {item["register"].lower() for item in outputs
                        if item["semantic"].startswith("position")}
    position_outputs.add("opos")
    position_writes = [item["text"] for item in instructions
                       if item["destination"].split(".", 1)[0].lower() in position_outputs]
    return {"model": model, "inputs": inputs, "outputs": outputs,
            "declarations": declarations, "constantRegisters": constant_banks,
            "samplers": sorted({int(reg[1:]) for reg in referenced
                                if re.fullmatch(r"s\d+", reg, re.I)} |
                               {int(item["register"][1:]) for item in declarations
                                if re.fullmatch(r"s\d+", item["register"], re.I)}),
            "immediateDefinitions": definitions, "relativeConstantAddressing": relative_constants,
            "matrixInstructions": matrix_instructions, "positionOutputWrites": position_writes,
            "instructionCount": len(instructions),
            "limitations": ["Lexical inspection only; branches, relative addressing and data flow require further analysis."]}


def determinant(matrix: list[list[float]]) -> float:
    rows = [row[:] for row in matrix]
    result = 1.0
    for column in range(4):
        pivot = max(range(column, 4), key=lambda row: abs(rows[row][column]))
        if rows[pivot][column] == 0:
            return 0.0
        if pivot != column:
            rows[pivot], rows[column] = rows[column], rows[pivot]
            result = -result
        value = rows[column][column]
        result *= value
        for row in range(column + 1, 4):
            factor = rows[row][column] / value
            for index in range(column + 1, 4):
                rows[row][index] -= factor * rows[column][index]
    return result


def matrix_metrics(matrix: list[list[float]]) -> dict[str, Any]:
    tolerance = 1e-5
    near = lambda left, right: abs(left - right) <= tolerance
    row_affine = all(near(matrix[row][3], 0) for row in range(3)) and near(matrix[3][3], 1)
    column_affine = all(near(matrix[3][column], 0) for column in range(3)) and near(matrix[3][3], 1)
    orthogonal = all(near(sum(matrix[row][index] * matrix[column][index] for index in range(3)),
                          1 if row == column else 0)
                     for row in range(3) for column in range(3))
    det = determinant(matrix)
    return {"determinant": det, "invertible": math.isfinite(det) and det != 0,
            "affineRowVectorShape": row_affine, "affineColumnVectorShape": column_affine,
            "orthonormalUpper3x3": orthogonal,
            "identity": all(near(matrix[row][column], 1 if row == column else 0)
                            for row in range(4) for column in range(4))}


def matrix_candidates(values: list[float], first_register: int = 0,
                      used_registers: list[int] | None = None, limit: int = 64) -> dict[str, Any]:
    candidates = []
    rejected = Counter()
    used = set(used_registers or [])
    total = 0
    for register in range(max(0, len(values) // 4 - 3)):
        block = values[register * 4:register * 4 + 16]
        if not all(math.isfinite(value) for value in block):
            rejected["nonfinite"] += 1
            continue
        if used and not any(first_register + register + row in used for row in range(4)):
            rejected["notReferencedByShader"] += 1
            continue
        matrix = [block[row * 4:row * 4 + 4] for row in range(4)]
        metrics = matrix_metrics(matrix)
        if not metrics["invertible"]:
            rejected["singular"] += 1
            continue
        total += 1
        if len(candidates) >= limit:
            continue
        transposed = [list(row) for row in zip(*matrix)]
        candidates.append({"firstRegister": first_register + register, "registerCount": 4,
                           "meaning": "unknown", "asStored": matrix, "metrics": metrics,
                           "transposed": transposed, "transposedMetrics": matrix_metrics(transposed)})
    return {"candidates": candidates, "candidateCount": total, "omittedByLimit": max(0, total - limit),
            "rejected": dict(rejected),
            "interpretation": "Shape is evidence only. W, V, P and their products are not assigned. A WVP does not uniquely determine W/V/P."}


def constant_floats(reference: Any, store: BlobStore) -> tuple[list[float], int]:
    if reference is None:
        return [], 0
    if isinstance(reference, list):
        flat = [float(item) for row in reference for item in (row if isinstance(row, list) else [row])]
        return flat, 0
    if "float" in reference:
        reference = reference["float"]
    if not reference:
        return [], 0
    data = store.read(reference)
    count = reference.get("count", len(data) // 16)
    if count < 0 or count > MAX_ELEMENTS or len(data) != count * 16:
        raise CaptureError("Float constant bank must contain count little-endian float4 registers.")
    return list(struct.unpack(f"<{count * 4}f", data)), int(reference.get("firstRegister", 0))


def apply_immediate_float_definitions(values: list[float], first_register: int,
                                      definitions: dict[str, list[str]]) -> tuple[list[float], list[int]]:
    result = values[:]
    overridden = []
    for register, components in definitions.items():
        if not re.fullmatch(r"c\d+", register, re.I) or len(components) != 4:
            continue
        index = int(register[1:])
        offset = (index - first_register) * 4
        if offset < 0 or offset + 4 > len(result):
            continue
        try:
            parsed = [float(component) for component in components]
        except ValueError:
            continue
        result[offset:offset + 4] = parsed
        overridden.append(index)
    return result, sorted(overridden)


def draw_vertex_indices(draw: dict[str, Any], store: BlobStore) -> tuple[list[int], dict[str, Any]]:
    args = draw["args"]
    count = primitive_elements(args["primitiveType"], int(args["primitiveCount"]))
    if count > MAX_ELEMENTS:
        raise CaptureError("Draw exceeds the offline element limit; use a narrower capture.")
    indexed = args.get("indexed", "Indexed" in draw.get("api", ""))
    if not indexed:
        start = int(args.get("startVertex", 0))
        if start < 0:
            raise CaptureError("Nonindexed start vertex is negative.")
        return list(range(start, start + count)), {"indexed": False, "elementCount": count}
    indices = draw["state"].get("indices") or {}
    width = {101: 2, 102: 4}.get(enum_value(indices.get("format", 0),
                                           {"INDEX16": 101, "INDEX32": 102}, "D3DFMT_"))
    if not width:
        raise CaptureError("Index format must be D3DFMT_INDEX16 or D3DFMT_INDEX32.")
    reference = indices.get("blob")
    data = store.read(reference)
    offset = int(args.get("startIndex", 0)) * width - int(reference.get("byteOffset", 0))
    if offset < 0 or offset + count * width > len(data):
        raise CaptureError("Index range is outside the captured blob.")
    raw = list(struct.unpack_from(f"<{count}{'H' if width == 2 else 'I'}", data, offset))
    base = int(args.get("baseVertexIndex", 0))
    effective = [value + base for value in raw]
    if any(value < 0 for value in effective):
        raise CaptureError("BaseVertexIndex produces a negative effective vertex index.")
    declared_min = int(args.get("minVertexIndex", 0))
    declared_count = int(args.get("numVertices", 0))
    range_valid = all(declared_min <= value < declared_min + declared_count for value in raw)
    return effective, {"indexed": True, "elementCount": count, "indexWidth": width,
                       "rawMinimum": min(raw) if raw else None, "rawMaximum": max(raw) if raw else None,
                       "effectiveMinimum": min(effective) if effective else None,
                       "effectiveMaximum": max(effective) if effective else None,
                       "baseVertexIndex": base, "declaredIndexRangeValid": range_valid}


def declaration_elements(declaration: Any) -> list[dict[str, Any]]:
    if not declaration:
        return []
    return declaration if isinstance(declaration, list) else declaration.get("elements", [])


def validate_geometry(draw: dict[str, Any], store: BlobStore) -> tuple[dict[str, Any], list[tuple[float, ...]], list[int]]:
    indices, index_report = draw_vertex_indices(draw, store)
    elements = declaration_elements(draw["state"].get("vertexDeclaration"))
    streams = {int(stream["stream"]): stream for stream in draw["state"].get("streams", [])}
    errors = []
    warnings = []
    positions = []
    unique = sorted(set(indices))
    if index_report.get("declaredIndexRangeValid") is False:
        errors.append("Indices exceed the declared MinVertexIndex/NumVertices range.")
    if not elements:
        warnings.append("No declaration elements are available; FVF interpretation was not inferred.")
    instanced = any(int(stream.get("frequency", 1)) & 0xC0000000 for stream in streams.values())
    if instanced:
        warnings.append("Instancing is present; object export is unavailable.")
    for element in elements:
        declaration_type = enum_value(element["type"], dict(zip(DECL_NAMES, range(18))), "D3DDECLTYPE_")
        if declaration_type == 17:
            continue
        if not 0 <= declaration_type < 17:
            errors.append(f"Unsupported declaration type: {declaration_type}")
            continue
        stream_id = int(element["stream"])
        stream = streams.get(stream_id)
        if stream is None:
            errors.append(f"Declaration references absent stream {stream_id}.")
            continue
        stride = int(stream["stride"])
        element_offset = int(element["offset"])
        if stride <= 0 or element_offset < 0 or element_offset + DECL_SIZES[declaration_type] > stride:
            errors.append(f"Stream {stream_id} element does not fit its stride.")
            continue
        try:
            reference = stream.get("blob")
            data = store.read(reference)
            blob_offset = int(reference.get("byteOffset", 0))
            frequency = int(stream.get("frequency", 1))
            if frequency & 0x80000000:
                warnings.append(f"Stream {stream_id} is an instance stream; per-vertex range checks were skipped.")
                continue
            addresses = [int(stream.get("offset", 0)) + vertex * stride + element_offset - blob_offset
                         for vertex in unique]
            if any(address < 0 or address + DECL_SIZES[declaration_type] > len(data) for address in addresses):
                errors.append(f"Stream {stream_id} vertex range is outside the captured blob.")
                continue
            usage = enum_value(element.get("usage", 0), {"POSITION": 0, "POSITIONT": 9,
                                                          "NORMAL": 3, "TEXCOORD": 5,
                                                          "BLENDWEIGHT": 1, "BLENDINDICES": 2,
                                                          "PSIZE": 4, "TANGENT": 6, "BINORMAL": 7,
                                                          "TESSFACTOR": 8, "COLOR": 10, "FOG": 11,
                                                          "DEPTH": 12, "SAMPLE": 13}, "D3DDECLUSAGE_")
            if usage == 0 and int(element.get("usageIndex", 0)) == 0 and declaration_type in (2, 3) and not instanced:
                positions = [struct.unpack_from(f"<{declaration_type + 1}f", data, address) for address in addresses]
                if not all(math.isfinite(value) for position in positions for value in position):
                    errors.append("POSITION0 contains nonfinite values.")
                    positions = []
        except (CaptureError, KeyError, ValueError, TypeError) as error:
            errors.append(str(error))
    bounds = None
    if positions:
        bounds = {"minimum": [min(position[axis] for position in positions) for axis in range(3)],
                  "maximum": [max(position[axis] for position in positions) for axis in range(3)],
                  "space": "input_attribute_space_unknown"}
    return {**index_report, "uniqueVertexCount": len(unique), "bounds": bounds,
            "instanced": instanced, "errors": errors, "warnings": warnings,
            "validCapturedRanges": not errors}, positions, unique


def export_obj(path: Path, draw: dict[str, Any], positions: list[tuple[float, ...]],
               unique: list[int], indices: list[int]) -> None:
    primitive = enum_value(draw["args"]["primitiveType"], PRIMITIVES, "D3DPT_")
    mapped = {vertex: index + 1 for index, vertex in enumerate(unique)}
    triangles = []
    if primitive == 4:
        triangles = [indices[index:index + 3] for index in range(0, len(indices), 3)]
    elif primitive == 5:
        triangles = [([indices[index + 1], indices[index], indices[index + 2]] if index & 1
                      else indices[index:index + 3]) for index in range(len(indices) - 2)]
    elif primitive == 6:
        triangles = [[indices[0], indices[index], indices[index + 1]] for index in range(1, len(indices) - 1)]
    else:
        return
    lines = ["# Diagnostic input POSITION0 only. Shader deformation and camera are not applied.",
             "# Coordinate system and object space are unknown."]
    lines.extend("v " + " ".join(format(value, ".9g") for value in position[:3]) for position in positions)
    lines.extend("f " + " ".join(str(mapped[vertex]) for vertex in triangle) for triangle in triangles
                 if len(set(triangle)) == 3)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def find_disassembly(shader: dict[str, Any], shader_dirs: list[Path], store: BlobStore) -> tuple[str | None, str | None]:
    reference = shader.get("disassembly")
    if reference and reference.get("path") and reference.get("available") is not False:
        return store.read(reference).decode("utf-8", errors="replace"), reference["path"]
    names = [shader.get("name", ""), shader.get("hash", "")]
    stage = shader.get("stage", "")
    if stage in {"vertex", "vs", 1}:
        names.append("VS_" + shader.get("hash", ""))
    if stage in {"pixel", "fragment", "ps", 16}:
        names.append("PS_" + shader.get("hash", ""))
        names.append("FS_" + shader.get("hash", ""))
    for directory in shader_dirs:
        for name in names:
            if not name or Path(name).name != name:
                continue
            path = directory / (name + ".dxso.dis")
            if path.is_file():
                return path.read_text(encoding="utf-8", errors="replace"), str(path)
    return None, None


def analyze_shader(shader: dict[str, Any], shader_dirs: list[Path], store: BlobStore) -> dict[str, Any]:
    result = {"hash": shader.get("hash"), "stage": shader.get("stage"), "category": "unknown",
              "layoutsObserved": [], "importantConstants": [], "evidence": [], "errors": []}
    bytecode = shader.get("bytecode")
    if bytecode:
        try:
            data = store.read(bytecode)
            digest = hashlib.sha1(data).hexdigest()
            result.update({"bytecodeHash": digest, "model": shader_model(data)})
            if shader.get("hash", "").lower() != digest:
                result["errors"].append("Shader identity does not match the original bytecode SHA1.")
            if not result["model"]:
                result["errors"].append("Shader bytecode does not contain a recognized aligned DXSO header.")
        except CaptureError as error:
            result["errors"].append(str(error))
    try:
        text, source = find_disassembly(shader, shader_dirs, store)
        if text:
            result["disassembly"] = analyze_disassembly(text)
            result["evidence"].append({"kind": "disassembly", "source": source})
            if not result.get("model"):
                result["model"] = result["disassembly"]["model"]
        else:
            result["errors"].append("No disassembly found. Configure DXVK_SHADER_DUMP_PATH and pass --shader-dir.")
    except CaptureError as error:
        result["errors"].append(str(error))
    for key in ("inputSignature", "outputSignature", "definedConstants", "samplerMask", "renderTargetMask", "rtMask",
                "maxConstantIndexF", "maxConstantIndexI", "maxConstantIndexB", "relativeConstantCopies"):
        if key in shader:
            result[key] = shader[key]
    return result


def defined_float_constants(shader: dict[str, Any], entry: dict[str, Any]) -> dict[str, list[str]]:
    definitions = dict(entry.get("disassembly", {}).get("immediateDefinitions", {}))
    for constant in shader.get("definedConstants", []):
        if not isinstance(constant, dict) or "register" not in constant:
            continue
        value = constant.get("value")
        if not isinstance(value, list) or len(value) != 4:
            continue
        # The adapter's metadata preserves float32 precision, unlike formatted disassembly.
        definitions[f"c{constant['register']}"] = ["nan" if component is None else str(component) for component in value]
    return definitions


def read_records(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    records = []
    errors = []
    with path.open("r", encoding="utf-8-sig") as source:
        for line_number, line in enumerate(source, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
                if not isinstance(record, dict):
                    raise ValueError("record is not an object")
                records.append(record)
            except (json.JSONDecodeError, ValueError) as error:
                errors.append(f"Line {line_number}: incomplete or invalid record ({error}).")
    return records, errors


def analyze_capture(capture_path: Path, shader_dirs: list[Path] | None = None,
                    output: Path | None = None, export_geometry: bool = False) -> tuple[dict[str, Any], dict[str, Any]]:
    records, errors = read_records(capture_path)
    store = BlobStore(capture_path.parent)
    shader_dirs = shader_dirs or []
    manifest: dict[str, Any] = {}
    draws = []
    types = Counter(record.get("type", "unknown") for record in records)
    warnings = []
    states = {}
    adapter_schema_v1 = any(record.get("type") == "capture_begin" and record.get("schema") == 1
                            for record in records)
    status_records = [record for record in records if record.get("type") == "capture_status"]
    status_terminal = bool(records and records[-1].get("type") == "capture_status")
    capture_ends = [record for record in records if record.get("type") == "capture_end"]
    capture_end = capture_ends[-1] if capture_ends else {}
    shader_records = {record.get("hash"): record for record in records
                      if record.get("type") == "shader" and record.get("hash")}
    draw_results = {(record.get("frame"), record.get("draw")): record for record in records
                    if record.get("type") == "draw_result"}
    draw_decisions: dict[tuple[Any, Any], list[str]] = {}
    for record in records:
        if record.get("type") == "event" and record.get("api") == "RemixDecision":
            arguments = record.get("args", {})
            key = (record.get("frame"), arguments.get("draw"))
            draw_decisions.setdefault(key, []).append(arguments.get("reason", "unknown"))
    ended = False
    truncated = bool(errors)
    for record in records:
        record_type = record.get("type", "")
        if record_type in {"end", "capture_end", "captureEnd", "summary", "capture_status"}:
            ended = True
        if record_type == "capture_status" and record.get("error"):
            warnings.append(f"Recorder failed: {record['error']}")
            truncated = True
        if record.get("truncated") or record_type in {"truncated", "limit"}:
            truncated = True
        if record_type == "state":
            states[record.get("id", record.get("hash"))] = record.get("state", record.get("value", {}))
        if record_type != "draw":
            continue
        draw = dict(record)
        summary = {"frame": draw.get("frame"), "draw": draw.get("draw"), "api": draw.get("api"),
                   "errors": [], "matrixCandidates": {}}
        draw_key = (draw.get("frame"), draw.get("draw"))
        summary["prepareFlags"] = draw_results.get(draw_key, {}).get("prepareFlags")
        summary["remixDecisions"] = draw_decisions.get(draw_key, [])
        summary["recorderInputRangeValid"] = draw.get("inputRangeValid")
        state = draw.get("state", states.get(draw.get("stateId"), {}))
        if isinstance(state, dict) and "path" in state and "streams" not in state:
            try:
                state = json.loads(store.read(state))
            except (CaptureError, ValueError) as error:
                summary["errors"].append(str(error))
                state = {}
        draw["state"] = state
        if not isinstance(state, dict):
            summary["errors"].append("Draw state is not an object.")
            draws.append(summary)
            continue
        for stage_key in ("vertexShader", "pixelShader"):
            shader = state.get(stage_key)
            if not isinstance(shader, dict) or not shader.get("hash"):
                continue
            shader = {**shader_records.get(shader["hash"], {}), **shader}
            state[stage_key] = shader
            key = f"{shader.get('stage', stage_key)}:{shader['hash']}"
            if key not in manifest:
                manifest[key] = analyze_shader(shader, shader_dirs, store)
            entry = manifest[key]
            layout = state.get("vertexDeclaration")
            if layout and layout not in entry["layoutsObserved"]:
                entry["layoutsObserved"].append(layout)
            entry.setdefault("drawsObserved", []).append({"frame": draw.get("frame"), "draw": draw.get("draw")})
            summary[stage_key] = shader["hash"]
        for stage, key in (("vertexShader", "vsConstants"), ("pixelShader", "psConstants")):
            try:
                values, first = constant_floats(state.get(key), store)
                shader = state.get(stage) or {}
                entry = manifest.get(f"{shader.get('stage', stage)}:{shader.get('hash')}", {})
                used = entry.get("disassembly", {}).get("constantRegisters", {}).get("c")
                if entry.get("disassembly", {}).get("relativeConstantAddressing"):
                    used = None
                definitions = defined_float_constants(shader, entry)
                values, overridden = apply_immediate_float_definitions(values, first, definitions)
                summary["matrixCandidates"][key] = matrix_candidates(values, first, used)
                summary["matrixCandidates"][key]["bytecodeDefinedRegistersApplied"] = overridden
            except (CaptureError, ValueError, TypeError) as error:
                summary["errors"].append(f"{key}: {error}")
        try:
            geometry, positions, unique = validate_geometry(draw, store)
            summary["geometry"] = geometry
            if output and export_geometry and positions and geometry["validCapturedRanges"]:
                indices, _ = draw_vertex_indices(draw, store)
                path = output / f"frame_{int(draw['frame'])}_draw_{int(draw['draw'])}_input.obj"
                export_obj(path, draw, positions, unique, indices)
                if path.exists():
                    summary["inputGeometryObj"] = path.name
        except (CaptureError, KeyError, ValueError, TypeError, struct.error) as error:
            summary["errors"].append(f"geometry: {error}")
        summary["textures"] = state.get("textures", [])
        summary["renderTargets"] = state.get("renderTargets", [])
        summary["depthStencil"] = state.get("depthStencil")
        draws.append(summary)
    if not ended:
        warnings.append("No end/summary record was found. The capture may still be running or incomplete.")
    if not status_records:
        warnings.append("No capture_status record was found. capture_end alone does not confirm that asynchronous records/blobs were drained.")
        if adapter_schema_v1:
            truncated = True
    elif not status_terminal:
        warnings.append("capture_status is not the final record; asynchronous writer integrity is unverified.")
        if adapter_schema_v1:
            truncated = True
    end_reason = capture_end.get("reason")
    if end_reason in {"device_destroyed", "observer_stopped"}:
        warnings.append(f"Capture window interrupted ({end_reason}); the last frame may be partial even if capture_status confirms drained writes.")
    elif adapter_schema_v1 and not capture_end:
        warnings.append("No capture_end record was found; frame-window completion is unverified.")
    if truncated:
        warnings.append("Capture is truncated, malformed or lacks verified writer completion; missing records/resources cannot be treated as evidence.")
    warnings.append("Texture contents and material semantics are unavailable unless captured separately.")
    report = {"schemaVersion": 1, "capture": str(capture_path.resolve()), "recordCounts": dict(types),
              "drawCount": len(draws), "shaderCount": len(manifest), "truncated": truncated,
              "writerIntegrityVerified": status_terminal and not truncated,
              "captureWindow": {"reason": end_reason, "complete": end_reason == "window_complete",
                                "completedFrames": capture_end.get("completedFrames"),
                                "selectedDraws": capture_end.get("selectedDraws")},
              "parseErrors": errors, "warnings": warnings, "draws": draws,
              "semantics": "No EDF shader, matrix, camera, material or coordinate-system interpretation is assumed."}
    return report, {"schemaVersion": 1, "shaders": list(manifest.values())}


def render_markdown(report: dict[str, Any], manifest: dict[str, Any]) -> str:
    lines = ["# D3D9 capture inspection", "", f"Draws: {report['drawCount']}. Unique shaders: {report['shaderCount']}.",
             "", report["semantics"], ""]
    lines.extend(f"- {warning}" for warning in report["warnings"] + report["parseErrors"])
    lines.extend(["", "## Shaders", "", "| Stage | SHA1 | Model | Category | Disassembly |",
                  "|---|---|---|---|---|"])
    for shader in manifest["shaders"]:
        lines.append(f"| {shader.get('stage')} | `{shader['hash']}` | {shader.get('model', 'unavailable')} | unknown | {'available' if 'disassembly' in shader else 'unavailable'} |")
    lines.extend(["", "## Draws", "", "| Frame/draw | API | Elements | Captured ranges | Matrix candidates VS/PS |",
                  "|---|---|---:|---|---|"])
    for draw in report["draws"]:
        geometry = draw.get("geometry", {})
        candidates = draw["matrixCandidates"]
        counts = "/".join(str(candidates.get(bank, {}).get("candidateCount", 0)) for bank in ("vsConstants", "psConstants"))
        valid = "valid" if geometry.get("validCapturedRanges") else "invalid/unavailable"
        lines.append(f"| {draw['frame']}/{draw['draw']} | {draw['api']} | {geometry.get('elementCount', '?')} | {valid} | {counts} |")
    lines.extend(["", "Matrix candidates include both storage orientations and remain **unknown**. Shape alone does not identify World/View/Projection.",
                  "", "See report.json for range failures, candidate matrices and pass metadata; shader_manifest.json contains lexical shader evidence.", ""])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path, help="Recorder JSONL file; blobs are relative to its directory.")
    parser.add_argument("--output", type=Path, required=True, help="Directory for report.json, report.md and shader_manifest.json.")
    parser.add_argument("--shader-dir", type=Path, action="append", default=[], help="DXVK_SHADER_DUMP_PATH directory (repeatable).")
    parser.add_argument("--export-input-obj", action="store_true", help="Export raw POSITION0 FLOAT3/FLOAT4 triangle geometry with no shader or transform applied.")
    args = parser.parse_args(argv)
    try:
        args.output.mkdir(parents=True, exist_ok=True)
        report, manifest = analyze_capture(args.capture, args.shader_dir, args.output, args.export_input_obj)
        for name, value in (("report.json", report), ("shader_manifest.json", manifest)):
            (args.output / name).write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
        (args.output / "report.md").write_text(render_markdown(report, manifest), encoding="utf-8")
    except (OSError, CaptureError, ValueError) as error:
        print(f"Capture analysis failed: {error}", file=sys.stderr)
        return 2
    print(f"Inspected {report['drawCount']} draws and {report['shaderCount']} shaders. Reports: {args.output.resolve()}")
    return 1 if report["truncated"] or report["parseErrors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
