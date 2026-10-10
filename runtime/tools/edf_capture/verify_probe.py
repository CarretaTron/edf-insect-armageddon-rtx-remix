#!/usr/bin/env python3
"""Verify captures from tools/edf_d3d9_probe. No game semantics are inferred."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import struct
import sys
from pathlib import Path
from typing import Any

from analyze_capture import BlobStore, CaptureError, read_records, shader_model


BASE_TRIANGLE = [(-0.7, -0.5, 0.5, 0, 0, 1, 0, 1),
                 (0, 0.6, 0.5, 0, 0, 1, 0.5, 0),
                 (0.7, -0.5, 0.5, 0, 0, 1, 1, 1)]
TAIL_TRIANGLE = [(-0.2, -0.2, 0.4, 0, 0, 1, 0, 1),
                 (0, 0.2, 0.4, 0, 0, 1, 0.5, 0),
                 (0.2, -0.2, 0.4, 0, 0, 1, 1, 1)]
IDENTITY = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
DECLARATION = [{"stream": 0, "offset": 0, "type": 2, "method": 0, "usage": 0, "usageIndex": 0},
               {"stream": 0, "offset": 12, "type": 2, "method": 0, "usage": 3, "usageIndex": 0},
               {"stream": 0, "offset": 24, "type": 1, "method": 0, "usage": 5, "usageIndex": 0}]
APIS = ["DrawPrimitive", "DrawIndexedPrimitive", "DrawPrimitiveUP", "DrawIndexedPrimitiveUP",
        "DrawPrimitive", "DrawIndexedPrimitive", "DrawPrimitive", "DrawPrimitive"]
VERTEX_OFFSETS = [64, 64, 0, 32, 64, 64, 160, 64]
STREAM_OFFSETS = [0, 32, 0, 0, 0, 32, 0, 0]


class Checks:
    def __init__(self):
        self.errors: list[str] = []
        self.count = 0

    def require(self, condition: bool, label: str, detail: Any = "") -> None:
        self.count += 1
        if not condition:
            self.errors.append(f"{label}: {detail}" if detail != "" else label)

    def equal(self, actual: Any, expected: Any, label: str) -> None:
        self.require(actual == expected, label, f"expected {expected!r}, got {actual!r}")

    def floats(self, actual: Any, expected: Any, label: str) -> None:
        if not isinstance(actual, (list, tuple)) or len(actual) != len(expected):
            self.require(False, label, f"expected {len(expected)} components, got {actual!r}")
            return
        matches = all(isinstance(left, (int, float)) and math.isfinite(left)
                      and abs(left - right) <= 1e-6 for left, right in zip(actual, expected))
        self.require(matches, label, f"expected {expected!r}, got {actual!r}")


def expected_wvp(frame: int) -> list[float]:
    return [0.8, 0, 0, 0.05 + frame * 0.02, 0, 0.8, 0, -0.05,
            0, 0, 1, 0, 0, 0, 0, 1]


def expected_vertices(frame: int, draw: int) -> list[float]:
    triangle = BASE_TRIANGLE
    if draw == 6:
        triangle = [(vertex[0] + (frame + 1) * 0.01, *vertex[1:]) for vertex in TAIL_TRIANGLE]
    return [value for vertex in triangle for value in vertex]


def shader_expectations(directory: Path, checks: Checks) -> dict[str, tuple[str, bytes]]:
    expected = {}
    for stage in ("vs", "ps"):
        paths = list(directory.glob(stage + "_*.dxso"))
        checks.equal(len(paths), 1, f"probe shader dumps: {stage} file count")
        if len(paths) != 1:
            continue
        data = paths[0].read_bytes()
        digest = hashlib.sha1(data).hexdigest()
        checks.equal(paths[0].stem, stage + "_" + digest, f"probe {stage} file identity")
        checks.equal(shader_model(data), stage + "_3_0", f"probe {stage} shader model")
        expected[stage] = (digest, data)
    return expected


def bank_values(state: dict[str, Any], key: str, kind: str, store: BlobStore) -> list[Any]:
    reference = state[key][kind]
    data = store.read(reference)
    count = reference["count"]
    if kind == "bool":
        if not reference.get("packed") or len(data) != ((count + 31) // 32) * 4:
            raise CaptureError("Expected packed boolean DWORDs.")
        words = struct.unpack(f"<{len(data) // 4}I", data)
        return [bool(words[index // 32] & (1 << (index % 32))) for index in range(count)]
    if len(data) != count * 16:
        raise CaptureError(f"Incorrect {kind} constant-bank size.")
    return list(struct.unpack(f"<{count * 4}{'f' if kind == 'float' else 'i'}", data))


def check_draw(draw: dict[str, Any], state: dict[str, Any], store: BlobStore,
               expected_shaders: dict[str, tuple[str, bytes]], checks: Checks) -> dict[str, Any]:
    frame, number = draw["frame"], draw["draw"]
    prefix = f"frame {frame} draw {number}"
    checks.equal(draw.get("api"), APIS[number], prefix + " API")
    args = draw["args"]
    checks.equal(args.get("primitiveType"), 4, prefix + " primitive type")
    checks.equal(args.get("primitiveCount"), 1, prefix + " primitive count")
    checks.equal(args.get("elementCount"), 3, prefix + " element count")
    checks.equal(draw.get("inputRangeValid"), True, prefix + " recorder input range")
    indexed = number in (1, 3, 5)
    checks.equal(args.get("indexed"), indexed, prefix + " indexed")
    if indexed:
        expected_args = {1: (1, 0, 3, 3), 3: (0, 1, 3, 0), 5: (1, 0, 3, 6)}[number]
        actual_args = tuple(args.get(key) for key in ("baseVertexIndex", "minVertexIndex", "numVertices", "startIndex"))
        checks.equal(actual_args, expected_args, prefix + " indexed arguments")
    else:
        checks.equal(args.get("startVertex"), 5 if number == 6 else (0 if number == 2 else 2), prefix + " start vertex")
    for field, stage in (("vertexShader", "vs"), ("pixelShader", "ps")):
        if number == 7:
            checks.equal(state.get(field), None, prefix + " fixed-function " + field)
        else:
            shader = state.get(field) or {}
            checks.equal(shader.get("hash"), expected_shaders.get(stage, (None, b""))[0], prefix + " " + field + " hash")
            checks.equal(shader.get("stage"), stage, prefix + " " + field + " stage")
    checks.equal(state.get("fvf"), 0x112 if number == 7 else 0, prefix + " FVF")
    checks.equal(state.get("vertexDeclaration", {}).get("elements"), DECLARATION, prefix + " declaration")
    streams = state.get("streams", [])
    checks.equal(len(streams), 1, prefix + " stream count")
    stream = next((entry for entry in streams if entry.get("stream") == 0), {})
    checks.equal(stream.get("stride"), 32, prefix + " stride")
    checks.equal(stream.get("frequency"), 1, prefix + " frequency")
    checks.equal(stream.get("offset"), STREAM_OFFSETS[number], prefix + " stream offset")
    up = number in (2, 3)
    checks.equal(stream.get("source"), "up" if up else "buffer", prefix + " stream source")
    checks.require(stream.get("resource") == 0 if up else isinstance(stream.get("resource"), int) and stream["resource"] > 0,
                   prefix + " stream resource identity", stream.get("resource"))
    expected_version = 0 if up else 2 * frame + (2 if number >= 5 else 1)
    checks.equal(stream.get("version"), expected_version, prefix + " VB version")
    reference = stream.get("blob", {})
    checks.equal(reference.get("byteOffset"), VERTEX_OFFSETS[number], prefix + " vertex blob byte offset")
    vertex_data = store.read(reference)
    checks.equal(len(vertex_data), 96, prefix + " vertex blob length")
    if len(vertex_data) == 96:
        checks.floats(struct.unpack("<24f", vertex_data), expected_vertices(frame, number), prefix + " vertex/normal/UV contents")
    indices = state.get("indices")
    if indexed:
        indices = indices or {}
        checks.equal(indices.get("format"), 101, prefix + " index format")
        checks.equal(indices.get("count"), 3, prefix + " index count")
        checks.require(indices.get("resource") == 0 if up else isinstance(indices.get("resource"), int) and indices["resource"] > 0,
                       prefix + " index resource identity", indices.get("resource"))
        checks.equal(indices.get("version"), 0 if up else 2 * frame + (2 if number == 5 else 1), prefix + " IB version")
        index_reference = indices.get("blob", {})
        checks.equal(index_reference.get("byteOffset"), {1: 6, 3: 0, 5: 12}[number], prefix + " index blob byte offset")
        checks.equal(store.read(index_reference), struct.pack("<3H", *{1: (0, 1, 2), 3: (1, 2, 3), 5: (2, 1, 0)}[number]), prefix + " index contents")
    else:
        checks.equal(indices, None, prefix + " nonindexed diagnostic index range")
    checks.floats(bank_values(state, "vsConstants", "float", store)[:16], expected_wvp(frame), prefix + " VS c0-c3")
    ps_values = bank_values(state, "psConstants", "float", store)
    checks.floats(ps_values[:4], [1, 0.25, 0.25, 1] if number >= 5 else [1, 1, 1, 1], prefix + " PS tint / stateblock application")
    checks.floats(ps_values[4:8], [0, 0, 1, 0], prefix + " PS light direction")
    for bank in ("vsConstants", "psConstants"):
        checks.equal(bank_values(state, bank, "int", store)[:4], [1, 2, 3, 4], prefix + " " + bank + " i0")
        checks.equal(bank_values(state, bank, "bool", store)[0], True, prefix + " " + bank + " b0")
    transform = state.get("transforms", {})
    world = IDENTITY[:]
    if frame > 0 or number == 7:
        world[12] = 0.1
    checks.floats(transform.get("world"), world, prefix + " World / MultiplyTransform")
    checks.floats(transform.get("view"), IDENTITY, prefix + " View")
    checks.floats(transform.get("projection"), IDENTITY, prefix + " Projection")
    checks.equal(transform.get("storage"), "column_major_float32", prefix + " transform storage")
    viewport = state.get("viewport", {})
    checks.equal([viewport.get(key) for key in ("x", "y", "width", "height")], [0, 0, 640, 480], prefix + " viewport")
    checks.floats([viewport.get("minZ"), viewport.get("maxZ")], [0, 1], prefix + " viewport depth range")
    checks.equal(state.get("scissor"), [0, 0, 640, 480], prefix + " scissor")
    for label, surface in (("RT0", state.get("renderTargets", [None])[0]), ("depth", state.get("depthStencil"))):
        surface = surface or {}
        checks.equal([surface.get(key) for key in ("surfaceWidth", "surfaceHeight", "mipLevel", "face")], [640, 480, 0, 0], prefix + " " + label + " surface")
        if label == "depth":
            checks.equal(surface.get("format"), 75, prefix + " depth format")
    texture = (state.get("textures") or [None])[0] or {}
    checks.equal([texture.get(key) for key in ("width", "height", "format")], [2, 2, 21], prefix + " texture descriptor")
    checks.equal(texture.get("content", {}).get("available"), False, prefix + " texture readback limitation")
    render_states = state["renderStates"]
    for key, value in {7: 1, 14: 0, 15: 0, 22: 1, 23: 4, 27: 0, 28: 0, 137: 0, 174: 0}.items():
        checks.equal(render_states[key], value, prefix + f" render state {key}")
    sampler = state["samplerStates"][0]
    for key, value in {1: 3, 2: 3, 5: 1, 6: 1, 7: 0}.items():
        checks.equal(sampler[key], value, prefix + f" sampler state {key}")
    return {"frame": frame, "draw": number, "vertexResource": stream.get("resource"),
            "indexResource": (indices or {}).get("resource"), "stateId": draw.get("stateId")}


def check_events(records: list[dict[str, Any]], frame: int, vb: Any, ib: Any,
                 store: BlobStore, checks: Checks) -> None:
    events = [record for record in records if record.get("type") == "event" and record.get("frame") == frame]
    for api, before in (("BeginStateBlock", 4), ("EndStateBlock", 4),
                        ("StateBlockApplyBegin", 5), ("StateBlockApplyEnd", 5), ("Present", 8)):
        matches = [event for event in events if event.get("api") == api and event.get("beforeDraw") == before]
        checks.equal(len(matches), 1, f"frame {frame} {api} event count")
        if matches:
            checks.equal(matches[0].get("beforeDraw"), before, f"frame {frame} {api} draw boundary")
            if api == "Present":
                checks.equal(matches[0].get("args", {}).get("totalDraws"), 8, f"frame {frame} Present draw count")
    locks = [event.get("args", {}) for event in events if event.get("api") == "BufferLock"]
    for expected in ((vb, 0, 256, 0x2000), (ib, 0, 18, 0x2000), (vb, 160, 96, 0x1000), (ib, 12, 6, 0x1000)):
        actual = [tuple(lock.get(key) for key in ("resource", "offset", "size", "flags")) for lock in locks]
        checks.equal(actual.count(expected), 1, f"frame {frame} dynamic buffer lock {expected}")
    recorded_tints = [event for event in events if event.get("api") == "SetPixelShaderConstantF"
                      and event.get("recordedInStateBlock") and event.get("args", {}).get("startRegister") == 0]
    checks.equal(len(recorded_tints), 1, f"frame {frame} recorded tint event")
    if recorded_tints:
        data = store.read(recorded_tints[0]["args"]["data"])
        checks.equal(len(data), 16, f"frame {frame} recorded tint size")
        if len(data) == 16:
            checks.floats(struct.unpack("<4f", data), [1, 0.25, 0.25, 1], f"frame {frame} recorded tint bytes")
    checks.equal(len([event for event in events if event.get("api") == "MultiplyTransform"
                      and event.get("args", {}).get("state") == 256]), 1, f"frame {frame} MultiplyTransform event")


def verify_probe(capture: Path, probe_shaders: Path, frames: int = 3,
                 start_frame: int = 0, probe_log: Path | None = None) -> dict[str, Any]:
    checks = Checks()
    records, parse_errors = read_records(capture)
    checks.equal(parse_errors, [], "capture JSONL parsing")
    checks.require(bool(records) and records[-1].get("type") == "capture_status", "terminal capture_status")
    status = records[-1] if records and records[-1].get("type") == "capture_status" else {}
    checks.equal(status.get("truncated"), False, "capture truncation")
    checks.equal(status.get("error"), None, "capture writer error")
    ends = [record for record in records if record.get("type") == "capture_end"]
    checks.equal(len(ends), 1, "capture_end count")
    end = ends[-1] if ends else {}
    checks.equal(end.get("reason"), "window_complete", "capture window reason")
    checks.equal(end.get("completedFrames"), frames, "completed frame count")
    checks.equal(end.get("selectedDraws"), frames * 8, "selected draw count")
    expected_shaders = shader_expectations(probe_shaders, checks)
    store = BlobStore(capture.parent)
    shader_records = [record for record in records if record.get("type") == "shader"]
    checks.equal(len(shader_records), 2, "unique shader-record count")
    for stage, (digest, expected_data) in expected_shaders.items():
        matches = [record for record in shader_records if record.get("stage") == stage]
        checks.equal(len(matches), 1, stage + " shader-record count")
        if matches:
            checks.equal(matches[0].get("hash"), digest, stage + " original shader identity")
            try:
                checks.equal(store.read(matches[0]["bytecode"]), expected_data, stage + " original bytecode")
            except (CaptureError, KeyError) as error:
                checks.require(False, stage + " bytecode blob", str(error))
    states = {}
    draws = []
    results = {}
    expected_draw_keys = {(frame, number) for frame in range(start_frame, start_frame + frames) for number in range(8)}
    observed = set()
    for record in records:
        if record.get("type") == "state":
            states[record["id"]] = record["state"]
        if record.get("type") != "draw":
            continue
        key = (record.get("frame"), record.get("draw"))
        checks.require(key not in observed, "duplicate draw", key)
        observed.add(key)
        checks.require(key in expected_draw_keys, "unexpected frame/draw", key)
        draws.append(record)
        if key not in expected_draw_keys:
            continue
        state = states.get(record.get("stateId"))
        checks.require(state is not None, f"frame {key[0]} draw {key[1]} preceding state record")
        if state is not None:
            try:
                results[key] = check_draw(record, state, store, expected_shaders, checks)
            except (CaptureError, KeyError, TypeError, ValueError, IndexError, struct.error) as error:
                checks.require(False, f"frame {key[0]} draw {key[1]} readable state/blob", str(error))
    checks.equal(observed, expected_draw_keys, "complete draw set")
    for frame in range(start_frame, start_frame + frames):
        first, indexed = results.get((frame, 0), {}), results.get((frame, 1), {})
        recorded = results.get((frame, 4), {})
        if first and recorded:
            checks.equal(first["stateId"], recorded["stateId"], f"frame {frame} recording preserves effective snapshot")
        vb, ib = first.get("vertexResource"), indexed.get("indexResource")
        for number in (1, 4, 5, 6, 7):
            if (frame, number) in results:
                checks.equal(results[frame, number]["vertexResource"], vb, f"frame {frame} draw {number} stable VB resource")
        if (frame, 5) in results:
            checks.equal(results[frame, 5]["indexResource"], ib, f"frame {frame} stable IB resource")
        try:
            check_events(records, frame, vb, ib, store, checks)
        except (CaptureError, KeyError, ValueError, struct.error) as error:
            checks.require(False, f"frame {frame} readable event data", str(error))
    if probe_log:
        log, log_errors = read_records(probe_log)
        checks.equal(log_errors, [], "probe stdout JSONL parsing")
        successes = [item for item in log if item.get("probe_event") == "success"]
        checks.equal(len(successes), 1, "probe success record")
        checks.equal(len([item for item in log if item.get("probe_event") == "present"]), frames, "probe Present count")
        if successes:
            checks.equal(successes[0].get("frames"), frames, "probe success frame count")
            checks.equal(successes[0].get("draws"), frames * 8, "probe success draw count")
    return {"passed": not checks.errors, "checks": checks.count, "errors": checks.errors,
            "capture": str(capture.resolve()), "expectedFrames": frames, "observedDraws": len(draws),
            "expectedShaderHashes": {stage: value[0] for stage, value in expected_shaders.items()},
            "limitations": ["Controlled probe inputs only; this does not establish EDF compatibility, camera reconstruction or path tracing.",
                            "Texture descriptor is checked; texture pixels are not captured by this recorder."]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    parser.add_argument("--probe-shaders", type=Path, required=True, help="The same run's probe_shaders directory.")
    parser.add_argument("--frames", type=int, choices=range(1, 4), default=3)
    parser.add_argument("--start-frame", type=int, default=0)
    parser.add_argument("--probe-log", type=Path)
    parser.add_argument("--output", type=Path, help="Optional JSON verification report.")
    args = parser.parse_args(argv)
    try:
        result = verify_probe(args.capture, args.probe_shaders, args.frames, args.start_frame, args.probe_log)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    except (OSError, CaptureError, ValueError) as error:
        print(f"Probe verification failed: {error}", file=sys.stderr)
        return 2
    print(f"{'PASS' if result['passed'] else 'FAIL'}: {result['checks']} checks, {result['observedDraws']} draws.")
    for error in result["errors"]:
        print("- " + error)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
