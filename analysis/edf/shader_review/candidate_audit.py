"""Validate a narrowly selected captured draw; never modifies captures or source."""
from __future__ import annotations
import hashlib
import json
import math
import pathlib
import struct

ROOT = pathlib.Path(r"D:\EDF IA RTX REMIX")
CAPTURE = ROOT / "captures/edf/1791384430306-28796-6"
OUTPUT = pathlib.Path(__file__).with_name("candidate_audit.json")
records = [json.loads(line) for line in (CAPTURE / "records.jsonl").read_text(encoding="utf-8").splitlines()]
draw = next(r for r in records if r["type"] == "draw" and r["frame"] == 7572 and r["draw"] == 284)
state = next(r["state"] for r in records if r["type"] == "state" and r["id"] == draw["stateId"])

def blob(ref):
    assert ref["available"], ref
    data = (CAPTURE / ref["path"]).read_bytes()
    assert len(data) == ref["size"]
    assert hashlib.sha1(data).hexdigest() == ref["hash"]
    return data

vs = [struct.unpack_from("<4f", blob(state["vsConstants"]["float"]), i * 16) for i in range(256)]
ps = [struct.unpack_from("<4f", blob(state["psConstants"]["float"]), i * 16) for i in range(224)]
streams = {s["stream"]: s for s in state["streams"]}
pos_bytes = blob(streams[2]["blob"])
normal_bytes = blob(streams[1]["blob"])
uv_bytes = blob(streams[4]["blob"])
positions = [tuple(v * vs[9][0] for v in raw[:3]) for raw in struct.iter_unpack("<4h", pos_bytes)]
normals = [tuple(v * vs[9][2] - vs[9][3] for v in raw[:3]) for raw in struct.iter_unpack("<4B", normal_bytes)]
uvs = [tuple(v * vs[9][1] for v in raw) for raw in struct.iter_unpack("<2h", uv_bytes)]
indices = [raw[0] for raw in struct.iter_unpack("<H", blob(state["indices"]["blob"]))]
assert len(positions) == len(normals) == len(uvs) == 84
assert len(indices) == 168 and set(indices) == set(range(84))
assert all(math.isfinite(v) for values in positions + normals + uvs for v in values)
assert vs[9][0] == 1 / 1024 and vs[9][1] == 1 / 2048 and vs[9][3] == 1
assert ps[1] == (1, 1, 1, 1)
assert all(streams[i]["frequency"] == 1 for i in range(5))
out = {
    "sourceCapture": str(CAPTURE), "frame": 7572, "drawIndex": 284,
    "vertexShaderHash": state["vertexShader"]["hash"],
    "pixelShaderHash": state["pixelShader"]["hash"],
    "vertexDeclaration": state["vertexDeclaration"],
    "draw": draw, "indices": state["indices"],
    "activeLayoutStreams": [streams[i] for i in range(5)],
    "unusedButBoundStreams": [s for i, s in streams.items() if i > 4],
    "vsRegisters0through9": vs[:10], "psRegisters0through3": ps[:4],
    "positionBounds": [[min(p[i] for p in positions), max(p[i] for p in positions)] for i in range(3)],
    "uvBounds": [[min(p[i] for p in uvs), max(p[i] for p in uvs)] for i in range(2)],
    "normalLengthBounds": [min(math.sqrt(sum(v*v for v in n)) for n in normals), max(math.sqrt(sum(v*v for v in n)) for n in normals)],
    "firstPositions": positions[:4], "firstUvs": uvs[:4], "firstNormals": normals[:4],
    "renderStates": {str(i): state["renderStates"][i] for i in [7, 14, 15, 22, 23, 27, 52, 168]},
    "renderTargets": state["renderTargets"], "depthStencil": state["depthStencil"],
    "textures": state["textures"][:3], "samplerStates": state["samplerStates"][:3],
    "transforms": state["transforms"],
    "classification": "unskinned_opaque_candidate; static entity status requires cross-frame evidence",
    "textureContentCaptured": False,
}
OUTPUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
print(json.dumps({"output": str(OUTPUT), "vertices": len(positions), "triangles": len(indices)//3, "verified": True}))
