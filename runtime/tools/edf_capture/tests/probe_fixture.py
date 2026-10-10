"""Synthetic serialization of probe inputs; no D3D9 execution or EDF data."""

import hashlib
import json
import struct
from pathlib import Path


def make_probe_capture(directory: Path, frames=3):
    directory.mkdir(parents=True, exist_ok=True)
    shaders = directory / "probe_shaders"
    shaders.mkdir(exist_ok=True)
    (directory / "blobs").mkdir(exist_ok=True)
    records = [{"type": "capture_begin", "schema": 1, "frame": 0}]

    def blob(data, byte_offset=0, **metadata):
        digest = hashlib.sha1(data).hexdigest()
        relative = f"blobs/{digest}.bin"
        (directory / relative).write_bytes(data)
        return {"hash": digest, "path": relative, "size": len(data), "available": True,
                "byteOffset": byte_offset, **metadata}

    hashes = {}
    for stage, version in (("vs", 0xFFFE0300), ("ps", 0xFFFF0300)):
        # Only identity transport is tested here. These empty shaders are never executed.
        code = struct.pack("<2I", version, 0xFFFF)
        reference = blob(code)
        hashes[stage] = reference["hash"]
        (shaders / f"{stage}_{reference['hash']}.dxso").write_bytes(code)
        records.append({"type": "shader", "hash": reference["hash"], "stage": stage, "bytecode": reference})

    triangle = [(-0.7, -0.5, 0.5, 0, 0, 1, 0, 1), (0, 0.6, 0.5, 0, 0, 1, 0.5, 0),
                (0.7, -0.5, 0.5, 0, 0, 1, 1, 1)]
    tail = [(-0.2, -0.2, 0.4, 0, 0, 1, 0, 1), (0, 0.2, 0.4, 0, 0, 1, 0.5, 0),
            (0.2, -0.2, 0.4, 0, 0, 1, 1, 1)]
    identity = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
    declaration = [{"stream": 0, "offset": 0, "type": 2, "method": 0, "usage": 0, "usageIndex": 0},
                   {"stream": 0, "offset": 12, "type": 2, "method": 0, "usage": 3, "usageIndex": 0},
                   {"stream": 0, "offset": 24, "type": 1, "method": 0, "usage": 5, "usageIndex": 0}]
    states = set()

    def bank(values, count):
        floats = values + [0] * (count * 4 - len(values))
        return {"float": blob(struct.pack(f"<{len(floats)}f", *floats), count=count, firstRegister=0),
                "int": blob(struct.pack("<64i", 1, 2, 3, 4, *([0] * 60)), count=16, firstRegister=0),
                "bool": blob(struct.pack("<I", 1), count=16, firstRegister=0, packed=True)}

    for frame in range(frames):
        wvp = [0.8, 0, 0, 0.05 + frame * 0.02, 0, 0.8, 0, -0.05, 0, 0, 1, 0, 0, 0, 0, 1]
        for number in range(8):
            up = number in (2, 3)
            indexed = number in (1, 3, 5)
            vertices = triangle if number != 6 else [(vertex[0] + 0.01 * (frame + 1), *vertex[1:]) for vertex in tail]
            vertex_blob = blob(struct.pack("<24f", *(value for vertex in vertices for value in vertex)),
                               [64, 64, 0, 32, 64, 64, 160, 64][number])
            stream = {"stream": 0, "offset": 32 if number in (1, 5) else 0, "stride": 32, "frequency": 1,
                      "source": "up" if up else "buffer", "resource": 0 if up else 101,
                      "version": 0 if up else frame * 2 + (2 if number >= 5 else 1), "blob": vertex_blob}
            indices = None
            if indexed:
                indices = {"resource": 0 if up else 202, "format": 101, "count": 3,
                           "version": 0 if up else frame * 2 + (2 if number == 5 else 1),
                           "blob": blob(struct.pack("<3H", *{1: (0, 1, 2), 3: (1, 2, 3), 5: (2, 1, 0)}[number]),
                                        {1: 6, 3: 0, 5: 12}[number])}
            world = identity[:]
            if frame > 0 or number == 7:
                world[12] = 0.1
            render_states = [0] * 256
            for key, value in {7: 1, 14: 0, 15: 0, 22: 1, 23: 4, 27: 0, 28: 0, 137: 0, 174: 0}.items():
                render_states[key] = value
            sampler = [0] * 14
            for key, value in {1: 3, 2: 3, 5: 1, 6: 1, 7: 0}.items():
                sampler[key] = value
            state = {"vertexShader": None if number == 7 else {"hash": hashes["vs"], "stage": "vs"},
                     "pixelShader": None if number == 7 else {"hash": hashes["ps"], "stage": "ps"},
                     "fvf": 0x112 if number == 7 else 0, "vertexDeclaration": {"elements": declaration},
                     "streams": [stream], "indices": indices,
                     "vsConstants": bank(wvp, 256), "psConstants": bank(([1, 0.25, 0.25, 1] if number >= 5 else [1, 1, 1, 1]) + [0, 0, 1, 0], 224),
                     "transforms": {"world": world, "view": identity, "projection": identity, "storage": "column_major_float32"},
                     "viewport": {"x": 0, "y": 0, "width": 640, "height": 480, "minZ": 0, "maxZ": 1},
                     "scissor": [0, 0, 640, 480], "renderStates": render_states, "samplerStates": [sampler],
                     "renderTargets": [{"surfaceWidth": 640, "surfaceHeight": 480, "mipLevel": 0, "face": 0}],
                     "depthStencil": {"surfaceWidth": 640, "surfaceHeight": 480, "mipLevel": 0, "face": 0, "format": 75},
                     "textures": [{"width": 2, "height": 2, "format": 21, "content": {"available": False}}]}
            state_id = hashlib.sha1(json.dumps(state, separators=(",", ":")).encode()).hexdigest()
            if state_id not in states:
                records.append({"type": "state", "id": state_id, "state": state})
                states.add(state_id)
            api = ["DrawPrimitive", "DrawIndexedPrimitive", "DrawPrimitiveUP", "DrawIndexedPrimitiveUP",
                   "DrawPrimitive", "DrawIndexedPrimitive", "DrawPrimitive", "DrawPrimitive"][number]
            args = {"primitiveType": 4, "primitiveCount": 1, "elementCount": 3, "indexed": indexed}
            if indexed:
                args.update(dict(zip(("baseVertexIndex", "minVertexIndex", "numVertices", "startIndex"),
                                     {1: (1, 0, 3, 3), 3: (0, 1, 3, 0), 5: (1, 0, 3, 6)}[number])))
            else:
                args["startVertex"] = 5 if number == 6 else (0 if number == 2 else 2)
            records.append({"type": "draw", "frame": frame, "draw": number, "api": api, "args": args,
                            "stateId": state_id, "inputRangeValid": True})
        for api, before in (("BeginStateBlock", 4), ("EndStateBlock", 4), ("StateBlockApplyBegin", 5),
                            ("StateBlockApplyEnd", 5), ("Present", 8), ("MultiplyTransform", 7)):
            records.append({"type": "event", "frame": frame, "beforeDraw": before, "api": api,
                            "args": {"totalDraws": 8} if api == "Present" else {"state": 256} if api == "MultiplyTransform" else {}})
        for resource, offset, size, flags in ((101, 0, 256, 0x2000), (202, 0, 18, 0x2000),
                                              (101, 160, 96, 0x1000), (202, 12, 6, 0x1000)):
            records.append({"type": "event", "frame": frame, "api": "BufferLock",
                            "args": {"resource": resource, "offset": offset, "size": size, "flags": flags}})
        records.append({"type": "event", "frame": frame, "api": "SetPixelShaderConstantF", "beforeDraw": 4,
                        "recordedInStateBlock": True, "args": {"startRegister": 0, "count": 1,
                        "data": blob(struct.pack("<4f", 1, 0.25, 0.25, 1))}})
    records.extend([{"type": "capture_end", "reason": "window_complete", "completedFrames": frames, "selectedDraws": frames * 8},
                    {"type": "capture_status", "truncated": False, "error": None}])
    path = directory / "records.jsonl"
    write_records(path, records)
    return path, shaders, records


def write_records(path, records):
    path.write_text("".join(json.dumps(record, separators=(",", ":")) + "\n" for record in records), encoding="utf-8")
