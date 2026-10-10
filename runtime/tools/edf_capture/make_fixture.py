#!/usr/bin/env python3
"""Generate a synthetic recorder schema v1 fixture, with no EDF data."""

import argparse
import hashlib
import json
import struct
from pathlib import Path


def make_fixture(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "blobs").mkdir(exist_ok=True)

    def blob(data, byte_offset=0, **metadata):
        digest = hashlib.sha1(data).hexdigest()
        relative = f"blobs/{digest}.bin"
        (directory / relative).write_bytes(data)
        return {"hash": digest, "path": relative, "size": len(data),
                "byteOffset": byte_offset, "available": True, **metadata}

    identity = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
    rows = [identity[index:index + 4] for index in range(0, 16, 4)]
    # SM3 DCL/DEF/M4X4 tokens use the encodings documented by src/dxso/dxso_decoder.cpp.
    words = [0xFFFE0300, 0x0200001F, 0x80000000, 0x900F0000,
             0x0200001F, 0x80000000, 0xE00F0000]
    for index, row in enumerate(rows):
        words.extend([0x05000051, 0xA00F0010 + index])
        words.extend(struct.unpack("<4I", struct.pack("<4f", *row)))
    words.extend([0x03000014, 0xE00F0000, 0x90E40000, 0xA0E40010, 0x0000FFFF])
    bytecode = blob(struct.pack(f"<{len(words)}I", *words))
    shader_hash = bytecode["hash"]
    disassembly = "vs_3_0\ndcl_position v0\ndcl_position o0\n"
    for index, row in enumerate(rows):
        disassembly += f"def c{16 + index}, " + ", ".join(map(str, row)) + "\n"
    disassembly += "m4x4 o0, v0, c16\n"
    shader = {"type": "shader", "hash": shader_hash, "stage": "vs", "name": "VS_" + shader_hash,
              "major": 3, "minor": 0, "bytecode": bytecode,
              "disassembly": blob(disassembly.encode("utf-8") + b"\0"),
              "inputSignature": [{"register": 0, "usage": 0, "usageIndex": 0}],
              "outputSignature": [{"register": 0, "usage": 0, "usageIndex": 0}],
              "definedConstants": [{"register": 16 + index, "value": row} for index, row in enumerate(rows)],
              "maxConstantIndexF": 20, "maxConstantIndexI": 0,
              "maxConstantIndexB": 0, "relativeConstantCopies": False, "samplerMask": 0, "renderTargetMask": 0}

    def constants(count):
        return {"float": blob(bytes(count * 16), count=count, firstRegister=0, packed=False),
                "int": blob(bytes(16 * 16), count=16, firstRegister=0, packed=False),
                "bool": blob(bytes(4), count=16, firstRegister=0, packed=True)}

    declaration = [{"stream": 0, "offset": 0, "type": 2, "method": 0, "usage": 0, "usageIndex": 0}]
    declaration_hash = hashlib.sha1(json.dumps(declaration, separators=(",", ":")).encode()).hexdigest()
    vertices = blob(struct.pack("<9f", 0, 0, 0, 1, 0, 0, 0, 1, 0), 32)
    indices = blob(struct.pack("<3H", 100, 101, 102), 16)
    unavailable_content = {"available": False, "reason": "gpu_texture_readback_not_implemented"}
    target = {"resource": 4, "descriptorHash": "0000000000000001", "width": 1280, "height": 720,
              "depth": 1, "format": 21, "usage": 1, "pool": 0, "mipLevels": 1,
              "arraySize": 1, "backBuffer": True, "content": unavailable_content,
              "surfaceWidth": 1280, "surfaceHeight": 720, "mip": 0, "face": 0}
    state = {"vertexShader": {"hash": shader_hash, "stage": "vs"}, "pixelShader": None,
             "vertexDeclaration": {"hash": declaration_hash, "elements": declaration}, "fvf": 0,
             "streams": [{"stream": 0, "offset": 32, "stride": 12, "frequency": 1,
                          "resource": 1, "version": 1, "blob": vertices}],
             "indices": {"resource": 2, "version": 1, "format": 101, "count": 3, "blob": indices},
             "textures": [None] * 21, "renderTargets": [target, None, None, None], "depthStencil": None,
             "renderStates": [0] * 256, "samplerStates": [[0] * 14 for _ in range(21)],
             "textureStageStates": [[0] * 32 for _ in range(8)],
             "vsConstants": constants(256), "psConstants": constants(224),
             "transforms": {"storage": "column_major_float32", "all": blob(struct.pack("<16f", *identity) * 266),
                            "world": identity, "view": identity, "projection": identity},
             "viewport": {"x": 0, "y": 0, "width": 1280, "height": 720, "minZ": [0], "maxZ": [1]},
             "scissor": [0, 0, 1280, 720]}
    state_json = json.dumps(state, separators=(",", ":"))
    state_id = hashlib.sha1(state_json.encode("utf-8")).hexdigest()
    draw = {"type": "draw", "frame": 4, "draw": 7, "api": "DrawIndexedPrimitive", "stateId": state_id,
            "args": {"primitiveType": 4, "primitiveCount": 1, "baseVertexIndex": -100,
                     "startVertex": 0, "minVertexIndex": 100, "numVertices": 3,
                     "startIndex": 8, "indexed": True, "elementCount": 3}, "inputRangeValid": True}
    records = [{"type": "capture_begin", "schema": 1, "frame": 4, "revision": "synthetic-fixture",
                "executable": "SYNTHETIC-NOT-EDF.exe", "hashAlgorithm": "sha1", "textureContentCaptured": False},
               shader, {"type": "state", "id": state_id, "state": state}, draw,
               {"type": "draw_result", "frame": 4, "draw": 7, "prepareFlags": 0},
               {"type": "event", "frame": 4, "beforeDraw": 8, "api": "RemixDecision",
                "recordedInStateBlock": False, "args": {"draw": 7, "reason": "synthetic_fixture_only"}},
               {"type": "capture_end", "reason": "window_complete", "completedFrames": 1, "selectedDraws": 1},
               ]
    serialized = [json.dumps(record, separators=(",", ":")) + "\n" for record in records]
    blob_paths = list((directory / "blobs").glob("*.bin"))
    status = {"type": "capture_status", "truncated": False, "reason": "", "error": None,
              "accepted_bytes": sum(len(item.encode("utf-8")) for item in serialized) + sum(item.stat().st_size for item in blob_paths),
              "max_bytes": 268435456, "written_records": len(records), "written_blobs": len(blob_paths)}
    serialized.append(json.dumps(status, separators=(",", ":")) + "\n")
    path = directory / "records.jsonl"
    path.write_text("".join(serialized), encoding="utf-8")
    return path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(make_fixture(args.output).resolve())
