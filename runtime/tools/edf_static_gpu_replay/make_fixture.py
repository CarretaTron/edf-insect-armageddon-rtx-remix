#!/usr/bin/env python3
"""Export a GPU replay fixture from the validated manifest, with no game texture pixels."""
import argparse
import json
import struct
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    if len(manifest) != 3:
        raise RuntimeError("Exactly three validated frames are required.")
    data = bytearray(b"EDFGPU01")
    def integers(*values):
        data.extend(struct.pack("<" + "I" * len(values), *values))
    def bytes_file(path):
        value = (args.manifest.parent / path).read_bytes()
        integers(len(value))
        data.extend(value)
    shared = manifest[0]
    for stream in shared["streams"]:
        bytes_file(stream["replayFile"])
    bytes_file(shared["indices"]["replayFile"])
    bytes_file(shared["shaderReferences"]["vsBytecode"]["file"])
    bytes_file(shared["shaderReferences"]["psBytecode"]["file"])
    integers(len(manifest))
    for frame in manifest:
        integers(frame["frame"])
        for stage in ("vs", "ps"):
            for bank in ("Float", "Int", "Bool"):
                bytes_file(frame["shaderReferences"][stage + "Constants" + bank]["file"])
        integers(len(frame["renderStates"]))
        integers(*frame["renderStates"])
        integers(len(frame["samplerStates"]))
        for sampler in frame["samplerStates"]:
            integers(len(sampler))
            integers(*sampler)
        viewport = frame["viewport"]
        integers(*(viewport[key] for key in ("x", "y", "width", "height")))
        data.extend(struct.pack("<2f", viewport["minZ"], viewport["maxZ"]))
        data.extend(struct.pack("<4i", *frame["scissor"]))
        for transform in ("world", "view", "projection"):
            data.extend(struct.pack("<16f", *frame["transforms"][transform]))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(data)


if __name__ == "__main__":
    main()
