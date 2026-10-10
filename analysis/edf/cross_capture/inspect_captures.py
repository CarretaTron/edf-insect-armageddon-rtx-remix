"""Read-only cross-capture evidence analysis. No EDF decoder is assumed."""

import collections
import hashlib
import json
import math
import struct
from pathlib import Path


ROOT = Path("D:/EDF IA RTX REMIX/captures/edf")
OUTPUT = Path(__file__).parent


def fingerprint(value):
    return hashlib.sha1(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def blob_refs(value):
    if isinstance(value, dict):
        if "path" in value and "hash" in value and "size" in value:
            yield value
        else:
            for child in value.values():
                yield from blob_refs(child)
    elif isinstance(value, list):
        for child in value:
            yield from blob_refs(child)


class Capture:
    def __init__(self, directory):
        self.directory = directory
        self.records = [json.loads(line) for line in (directory / "records.jsonl").read_text().splitlines() if line.strip()]
        self.states = {record["id"]: record["state"] for record in self.records if record["type"] == "state"}
        self.shaders = {record["hash"]: record for record in self.records if record["type"] == "shader"}
        self.draws = [record for record in self.records if record["type"] == "draw"]
        self.cache = {}
        self.blob_errors = []
        self.checked = set()
        self.unavailable = collections.Counter()
        self.shader_pairs = collections.Counter()
        for record in self.records:
            for reference in blob_refs(record):
                key = (reference["path"], reference["hash"], reference["size"])
                if key not in self.checked:
                    self.checked.add(key)
                    try:
                        self.read(reference)
                    except (OSError, ValueError) as error:
                        self.blob_errors.append(str(error))
        self.status = self.records[-1] if self.records[-1]["type"] == "capture_status" else {}
        self.ends = [record for record in self.records if record["type"] == "capture_end"]
        self.complete = bool(self.status and not self.status.get("truncated") and not self.status.get("error")
                             and self.ends and self.ends[-1].get("reason") == "window_complete" and not self.blob_errors)

    def read(self, reference):
        if not reference or not reference.get("available", True) or not reference.get("path"):
            raise ValueError("unavailable blob")
        key = reference["path"]
        if key not in self.cache:
            path = (self.directory / key).resolve()
            path.relative_to(self.directory.resolve())
            data = path.read_bytes()
            if len(data) != reference["size"] or hashlib.sha1(data).hexdigest() != reference["hash"]:
                raise ValueError(f"{self.directory.name}/{key}: hash/size mismatch")
            self.cache[key] = data
        return self.cache[key]

    def constants(self, state, bank="vsConstants"):
        reference = state[bank]["float"]
        data = self.read(reference)
        return [tuple(values) for values in struct.iter_unpack("<4f", data)]


def geometry_identity(state, args):
    streams = [(entry["stream"], entry["stride"], entry["blob"].get("hash"), entry["blob"].get("size"))
               for entry in state["streams"]]
    indices = state.get("indices") or {}
    return {"declaration": state["vertexDeclaration"]["hash"], "streams": streams,
            "indexFormat": indices.get("format"), "indices": indices.get("blob", {}).get("hash"),
            "indexSize": indices.get("blob", {}).get("size"), "primitive": args["primitiveType"],
            "primitiveCount": args["primitiveCount"]}


def main():
    captures = [Capture(directory) for directory in sorted(ROOT.iterdir()) if (directory / "records.jsonl").exists()]
    groups = {}
    draw_entries = []
    shader_stats = {}
    all_hashes = {}
    summaries = []
    for capture in captures:
        for shader_hash, shader in capture.shaders.items():
            all_hashes.setdefault(shader_hash, {"stage": shader["stage"], "major": shader["major"], "minor": shader["minor"],
                                                "samplerMask": shader.get("samplerMask"), "renderTargetMask": shader.get("renderTargetMask"),
                                                "captures": []})["captures"].append(capture.directory.name)
        for draw in capture.draws:
            state = capture.states.get(draw["stateId"])
            if not state:
                capture.blob_errors.append(f"draw {draw['draw']} missing state {draw['stateId']}")
                continue
            vs = (state.get("vertexShader") or {}).get("hash")
            ps = (state.get("pixelShader") or {}).get("hash")
            capture.shader_pairs[(vs, ps)] += 1
            identity = geometry_identity(state, draw["args"])
            geom_hash = fingerprint(identity)
            render = state["renderStates"]
            textures = [(slot, texture["resource"], texture.get("descriptorHash"))
                        for slot, texture in enumerate(state["textures"]) if texture]
            target = state["renderTargets"][0] or {}
            has_skin_semantics = any(element["usage"] in (1, 2) for element in state["vertexDeclaration"]["elements"])
            constant_hash = state["vsConstants"]["float"].get("hash")
            entry = {"capture": capture.directory.name, "completeCapture": capture.complete, "frame": draw["frame"], "draw": draw["draw"],
                     "geometryHash": geom_hash, "vertexShader": vs, "pixelShader": ps, "args": draw["args"],
                     "alphaBlend": render[27], "alphaTest": render[15], "zEnable": render[7], "zWrite": render[14], "zFunc": render[23],
                     "cull": render[22], "colorWrite": render[168], "textures": textures,
                     "target": {key: target.get(key) for key in ("resource", "descriptorHash", "format", "surfaceWidth", "surfaceHeight", "backBuffer")},
                     "depth": state.get("depthStencil"), "skinningDeclarationSemantics": has_skin_semantics,
                     "vsConstantsHash": constant_hash,
                     "psConstantsHash": state["psConstants"]["float"].get("hash"),
                     "transforms": {key: state["transforms"].get(key) for key in ("world", "view", "projection")}}
            draw_entries.append(entry)
            group = groups.setdefault((geom_hash, vs, ps), {"geometryHash": geom_hash, "vertexShader": vs, "pixelShader": ps,
                                                            "identity": identity, "occurrences": []})
            group["occurrences"].append(entry)
            if vs and capture.complete:
                stats = shader_stats.setdefault(vs, {"stage": "vs", "samples": []})
                try:
                    stats["samples"].append((entry, capture.constants(state)))
                except (OSError, ValueError) as error:
                    capture.blob_errors.append(str(error))
        summaries.append({"capture": capture.directory.name, "frame": capture.records[0]["frame"], "drawCount": len(capture.draws),
                          "complete": capture.complete, "end": capture.ends[-1] if capture.ends else None, "status": capture.status,
                          "checkedBlobCount": len(capture.checked), "blobErrors": capture.blob_errors,
                          "shaderCount": len(capture.shaders),
                          "shaderPairs": [{"vs": pair[0], "ps": pair[1], "draws": count} for pair, count in capture.shader_pairs.most_common()]})
    ranked = []
    for group in groups.values():
        occurrences = group["occurrences"]
        completed = [entry for entry in occurrences if entry["completeCapture"]]
        complete_captures = sorted({entry["capture"] for entry in completed})
        opaque = [entry for entry in completed if entry["alphaBlend"] == 0 and entry["alphaTest"] == 0 and entry["zEnable"] and entry["zWrite"]]
        group.update({"completeCaptures": complete_captures, "occurrenceCount": len(occurrences),
                      "opaqueDepthWritingOccurrences": len(opaque), "uniqueVSConstantBanks": len({entry["vsConstantsHash"] for entry in completed}),
                      "declaresSkinning": any(entry["skinningDeclarationSemantics"] for entry in completed),
                      "candidate": len(complete_captures) >= 2 and len(opaque) == len(completed) and not any(entry["skinningDeclarationSemantics"] for entry in completed)
                                   and group["identity"]["primitive"] == 4 and group["identity"]["primitiveCount"] >= 10})
        if len(complete_captures) >= 2:
            ranked.append(group)
    ranked.sort(key=lambda group: (group["candidate"], len(group["completeCaptures"]), -group["identity"]["primitiveCount"], group["occurrenceCount"]), reverse=True)
    constants_summary = {}
    for shader, stats in shader_stats.items():
        samples = stats["samples"]
        count = min(len(bank) for _, bank in samples)
        registers = []
        for register in range(count):
            values = [bank[register] for _, bank in samples]
            per_capture = {}
            for entry, bank in samples:
                per_capture.setdefault(entry["capture"], set()).add(bank[register])
            if any(any(value != 0 for value in row) for row in values):
                registers.append({"register": register, "uniqueValues": len(set(values)), "example": values[0],
                                  "uniqueValuesPerCapture": {capture: len(rows) for capture, rows in per_capture.items()},
                                  "uniformWithinEachCapture": all(len(rows) == 1 for rows in per_capture.values()),
                                  "changesAcrossCaptures": len(set(values)) > 1 and len(per_capture) > 1})
        constants_summary[shader] = {"sampleCount": len(samples), "captures": sorted({entry["capture"] for entry, _ in samples}), "registers": registers}
    report = {"captures": summaries, "shaderInventory": all_hashes, "totalDraws": len(draw_entries),
              "crossCaptureGeometryGroups": ranked, "vsConstantPatternsCompleteCaptures": constants_summary,
              "limitations": ["Geometry hashes identify captured input bytes, not static world-space objects.",
                              "Opaque status is D3D9 alpha/depth state only; shader clipping/deformation and lighting require bytecode evidence.",
                              "Texture contents are unavailable. Descriptor hashes are not texture content hashes.",
                              "Truncated captures establish positive observations only; absent draws/passes cannot be inferred.",
                              "Uniform per-frame varying constants may be camera/time/pass data; this analysis does not assign W/V/P."]}
    (OUTPUT / "cross_capture.json").write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps({"captures": [{key: summary[key] for key in ("capture", "frame", "drawCount", "complete", "checkedBlobCount", "blobErrors")} for summary in summaries],
                      "crossGroups": len(ranked), "candidates": sum(group["candidate"] for group in ranked),
                      "topCandidates": [{key: group[key] for key in ("geometryHash", "vertexShader", "pixelShader", "identity", "completeCaptures", "occurrenceCount", "uniqueVSConstantBanks", "candidate")}
                                        | {"draws": [(entry["capture"], entry["draw"], entry["target"], entry["textures"]) for entry in group["occurrences"] if entry["completeCapture"]]}
                                        for group in ranked[:12]]}, indent=2))


if __name__ == "__main__":
    main()
