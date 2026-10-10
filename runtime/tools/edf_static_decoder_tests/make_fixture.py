#!/usr/bin/env python3
"""Build a small replay fixture from the three positively observed target draws."""
import argparse
import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "edf_capture"))
from analyze_capture import BlobStore
from validate_edf_draw import load_capture, verify_draw, VS_HASH, PS_HASH


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture_root", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    targets = [("1791398932979-38296-0", 952), ("1791398948329-38296-1", 952),
               ("1791398952733-38296-2", 898)]
    data = bytearray(b"EDFDEC01" + struct.pack("<I", len(targets)))
    metadata = []
    def integers(*values):
        data.extend(struct.pack("<" + "I" * len(values), *values))
    def byte_span(value):
        integers(len(value))
        data.extend(value)
    for name, draw_number in targets:
        path = args.capture_root / name
        draws, shaders, inventory = load_capture(path)
        if not inventory["complete"]:
            raise RuntimeError(f"Incomplete capture {name}")
        draw = next(d for d in draws if d["draw"] == draw_number)
        validated, _ = verify_draw(path, draw, shaders)
        state = draw["state"]
        store = BlobStore(path)
        integers(len(state["vertexDeclaration"]["elements"]))
        for element in state["vertexDeclaration"]["elements"]:
            integers(*(element[key] for key in ("stream", "offset", "type", "method", "usage", "usageIndex")))
        streams = {stream["stream"]: stream for stream in state["streams"]}
        for i in range(5):
            integers(streams[i]["stride"], streams[i]["frequency"])
            byte_span(store.read(streams[i]["blob"]))
        byte_span(store.read(shaders[VS_HASH]["bytecode"]))
        byte_span(store.read(shaders[PS_HASH]["bytecode"]))
        integers(2)
        byte_span(store.read(state["indices"]["blob"]))
        texture = state["textures"][0]
        integers(len(validated["vertices"]), draw["args"]["primitiveCount"], draw["args"]["primitiveType"],
                 int(draw["args"]["indexed"]), 1, texture["format"], texture["width"], texture["height"],
                 int(state["samplerStates"][0][11]), state["viewport"]["width"], state["viewport"]["height"])
        data.extend(store.read(state["vsConstants"]["float"])[:160])
        data.extend(store.read(state["psConstants"]["float"])[16:32])
        for vertex in validated["vertices"]:
            data.extend(struct.pack("<8d", *(vertex["positionLocal"] + vertex["normalLocal"] + vertex["uv"])))
        integers(len(validated["indices"]))
        data.extend(struct.pack("<" + "H" * len(validated["indices"]),
                                *(i - 630 for i in validated["indices"])))
        metadata.append({"capture": name, "frame": draw["frame"], "draw": draw_number,
                         "vertexCount": len(validated["vertices"]), "indexCount": len(validated["indices"]),
                         "positionEquationMaxError": validated["positionEquationMaxError"],
                         "uvEquationMaxError": validated["uvEquationMaxError"],
                         "normalEquationMaxError": validated["normalEquationMaxError"]})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(data)
    args.output.with_suffix(".json").write_text(json.dumps(metadata, indent=2) + "\n")


if __name__ == "__main__":
    main()
