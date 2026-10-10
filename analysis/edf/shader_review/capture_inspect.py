"""Read-only inspection of EDF capture inputs; writes derived review artifacts only."""
from __future__ import annotations
import collections
import hashlib
import json
import pathlib
import struct

ROOT = pathlib.Path(r"D:\EDF IA RTX REMIX\captures\edf")
OUT = pathlib.Path(__file__).parent
OUT.mkdir(parents=True, exist_ok=True)
(OUT / "shaders").mkdir(exist_ok=True)
shaders = {}
captures = []
draws = []
layouts = {}

def blob(directory, ref):
    if not ref.get("available"):
        return None
    data = (directory / ref["path"]).read_bytes()
    assert hashlib.sha1(data).hexdigest() == ref["hash"]
    assert len(data) == ref["size"]
    return data

for directory in sorted(ROOT.iterdir()):
    manifest = directory / "records.jsonl"
    if not manifest.exists():
        continue
    records = [json.loads(line) for line in manifest.read_text(encoding="utf-8").splitlines()]
    states = {r["id"]: r["state"] for r in records if r["type"] == "state"}
    status = records[-1]
    captures.append({"directory": directory.name, "begin": records[0], "status": status,
                     "draws": sum(r["type"] == "draw" for r in records)})
    for record in records:
        if record["type"] == "shader":
            key = (record["stage"], record["hash"])
            data = blob(directory, record["disassembly"])
            bytecode = blob(directory, record["bytecode"])
            if bytecode is not None:
                assert hashlib.sha1(bytecode).hexdigest() == record["hash"]
            if data is not None:
                path = OUT / "shaders" / ("_".join(key) + ".asm")
                path.write_text(data.decode("utf-8").rstrip("\0"), encoding="utf-8")
            shaders.setdefault(key, {"record": record, "directory": str(directory), "disassembly": str(path) if data else None})
        if record["type"] != "draw":
            continue
        state = states[record["stateId"]]
        declaration = state["vertexDeclaration"]
        layouts[declaration["hash"]] = declaration["elements"]
        rs = state["renderStates"]
        draws.append({"directory": str(directory), "truncatedCapture": bool(status.get("truncated")),
                      "draw": record, "state": state,
                      "vertexShader": (state.get("vertexShader") or {}).get("hash"),
                      "pixelShader": (state.get("pixelShader") or {}).get("hash"),
                      "layout": declaration["hash"],
                      "rs": {str(i): rs[i] for i in [7, 14, 15, 19, 20, 22, 23, 27, 52, 168, 171, 206, 207, 208]},
                      "renderTarget": state["renderTargets"][0]})

pair_counts = collections.Counter((d["vertexShader"], d["pixelShader"]) for d in draws)
complete_pair_counts = collections.Counter((d["vertexShader"], d["pixelShader"]) for d in draws if not d["truncatedCapture"])
summary = {"captures": captures, "drawCount": len(draws), "shaderCount": len(shaders),
           "pairs": [{"vs": vs, "ps": ps, "allPrefixDraws": count, "completeCaptureDraws": complete_pair_counts[(vs, ps)]}
                     for (vs, ps), count in pair_counts.most_common()],
           "layouts": layouts, "shaders": list(shaders.values())}
(OUT / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

def sample_details(d):
    s = d["state"]
    directory = pathlib.Path(d["directory"])
    const = blob(directory, s["vsConstants"]["float"])
    regs = [list(struct.unpack_from("<4f", const, i * 16)) for i in range(16)] if const else None
    geometry = []
    for stream in s["streams"]:
        ref = stream["blob"]
        data = blob(directory, ref)
        geometry.append({**stream, "firstBytesHex": data[:64].hex() if data else None})
    return {"directory": d["directory"], "truncatedCapture": d["truncatedCapture"], "draw": d["draw"],
            "vertexShader": d["vertexShader"], "pixelShader": d["pixelShader"],
            "rs": d["rs"], "renderTargets": s["renderTargets"], "depthStencil": s["depthStencil"],
            "declaration": s["vertexDeclaration"], "streams": geometry, "indices": s["indices"],
            "vsRegisters0through15": regs, "transforms": s["transforms"],
            "textures": [{"slot": i, **t} for i, t in enumerate(s["textures"]) if t]}

samples = {}
for key, count in pair_counts.most_common():
    selected = next((d for d in draws if (d["vertexShader"], d["pixelShader"]) == key and not d["truncatedCapture"]),
                    next(d for d in draws if (d["vertexShader"], d["pixelShader"]) == key))
    samples["_".join(key)] = sample_details(selected)
(OUT / "draw_samples.json").write_text(json.dumps(samples, indent=2), encoding="utf-8")
print(json.dumps({"captures": len(captures), "draws": len(draws), "shaders": len(shaders), "output": str(OUT)}))
