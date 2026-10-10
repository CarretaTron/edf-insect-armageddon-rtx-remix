# Offline D3D9 capture inspection

This tool inspects the optional Remix diagnostic recorder's schema v1 JSONL and binary blobs. It uses Python 3.10 or newer and only the standard library. It does not modify the game, Runtime, shaders or input capture.

## Run

```powershell
python tools/edf_capture/analyze_capture.py path/to/capture.jsonl --output path/to/inspection --shader-dir path/to/shader-dumps
python -m unittest discover -s tools/edf_capture/tests -v
```

A reproducible synthetic fixture exercises the actual adapter's separate shader/state/draw records and blob offsets:

```powershell
python tools/edf_capture/make_fixture.py tools/edf_capture/generated/schema_v1
python tools/edf_capture/analyze_capture.py tools/edf_capture/generated/schema_v1/records.jsonl --output tools/edf_capture/generated/inspection --export-input-obj
```

The fixture is a triangle with 16-bit indices, negative base vertex, nonzero stream/index byte offsets, SM3 float definitions and inline disassembly. It contains no EDF data and proves only the recorder/analyzer schema contract. Its OBJ should contain three vertices and face `1 2 3`; shader-defined identity registers remain categorized as `unknown`.

`--shader-dir` can be repeated. Configure the existing Runtime `DXVK_SHADER_DUMP_PATH` before the game starts to obtain `.dxso.dis` disassembly. The analyzer matches `shader.name`, raw SHA1 and `VS_<SHA1>`/`FS_<SHA1>`/`PS_<SHA1>` file names. Original bytecode blobs are checked against their reproducible SHA1. Shader stage remains a separate field.

Outputs:

- `report.md`: draw/shader counts, completeness warnings and inspection summary.
- `report.json`: index ranges, declaration/stream bounds checks, raw POSITION0 bounds, both matrix storage orientations and per-draw errors.
- `shader_manifest.json`: shader hash, category `unknown`, observed declarations, lexical inputs/outputs, referenced constant registers, immediate definitions, samplers and position-output writers.
- Optional `--export-input-obj`: raw POSITION0 FLOAT3/FLOAT4 triangle meshes. No shader deformation, world transform, camera, UV, normals or material is applied. The first three components are exported as input attributes; object space and coordinate convention are unknown.

The CLI returns `0` after successful inspection, `1` for explicitly truncated/malformed JSONL, a recorder write error or a missing/nonterminal `capture_status` in adapter schema v1 and `2` for a fatal input/output error. A completed JSONL file can still contain unavailable or invalid resource data; inspect per-draw errors before using the report as evidence.

`capture_end` describes why the window stopped and how many frames/draws completed. It does not confirm that asynchronous records and blobs reached disk; only a final `capture_status` without errors or truncation verifies writer integrity. `device_destroyed` and `observer_stopped` explicitly produce a partial-window warning even when all writes drained. `writerIntegrityVerified` and `captureWindow.complete` report these two independent facts. A schema v1 capture with `capture_end` alone is incomplete and returns `1`.

## Capture contract

Each `draw` contains `frame`, `draw`, `api`, `args` and `stateId`, referring to a preceding `{type: state, id, state}` record. Inline `state` or a JSON blob reference is also accepted. Shader metadata can be inline or supplemented by `{type: shader, hash, ...}` records. No state is inferred from setters. This preserves draws whose initial state was configured before the capture window or through a stateblock.

Binary blobs use `{hash, path, size}`. Paths are relative to the JSONL directory, and paths escaping that directory are rejected. `hash` is lowercase hexadecimal SHA1 of the stored bytes. Constant banks use `vsConstants.float` and `psConstants.float` references with `count` float4 registers and `firstRegister`. Float data is little-endian IEEE 754. Integer and boolean banks are preserved by the recorder but are not interpreted by this analyzer.

`vertexDeclaration.elements` holds D3D9 `stream`, `offset`, `type`, `method`, `usage`, `usageIndex` values. Streams hold `stream`, `offset`, `stride`, `frequency`, `resource` and `blob`; indices hold `format`, `resource` and `blob`. Geometry blobs add `byteOffset`, the absolute start of the copied range in the original buffer. The analyzer applies original `startIndex`, `startVertex` and `baseVertexIndex`; it accepts valid negative base indices when the resulting vertex indices remain nonnegative.

The input contract does not imply a vertex semantic is correct for the game's shader. `POSITION0` is inspected as declared, and float values in constants remain unlabeled.

## What the report can establish

For captured resources, it verifies SHA1 and size, index width, primitive-derived element count, declared indexed range, effective base indices, declaration element sizes and captured stream ranges. It reports finite POSITION0 attribute bounds. Instancing is reported but not exported; instance-frequency streams are not treated as vertex streams.

Shader disassembly inspection extracts declarations, explicit register references, the implicit constant span of `m4x4`/related instructions and direct position-output writers. Bytecode-defined constants are kept distinct from externally set constants; parseable float `def` values override the externally set register bank during candidate-matrix inspection. The adapter's `definedConstants: [{register, value}]` metadata has priority and works when disassembly is unavailable. Applied overrides are listed in the report. Relative constant addressing is flagged; precise dynamic register use is not inferred.

Matrix inspection scans sliding groups of four float4 registers, filters nonfinite/singular groups and records determinant, affine shape, orthonormal 3×3 shape and identity. Shader-referenced register ranges narrow the search when available; dynamic addressing disables this narrowing. A maximum of 64 candidates per bank/draw is emitted with an explicit omitted count. Every candidate retains `meaning: unknown`.

## Evidence still required

- A nonsingular 4×4 block is not proof of World, View, Projection or their products. Both stored/transposed versions are candidates. A WVP cannot uniquely recover W/V/P.
- Lexical disassembly analysis is not shader execution, a control-flow graph or data-flow proof. Swizzles, branches, indirect addressing, immediate definitions and skinning need further inspection or emulation before adopting a game decoder.
- A valid vertex/index range is not proof of reconstructed geometry. The VS may decode, deform, skin or reinterpret attributes. Compare shader-transformed positions with the original raster in more than one camera position.
- Texture metadata cannot establish content, UV convention, albedo or lighting semantics. This first recorder increment does not read back texture contents.
- Integer/bool constants, packed vertex-format decoding, instance reconstruction, UV/normal export and CPU-side camera discovery are not implemented here.
- Captured ranges may be correct while declared draw ranges are invalid. Both outcomes are reported. Zero stride is reported as unsupported for inspection; it is not evidence that the game itself issued an invalid draw.

The synthetic tests contain no claims about EDF's actual shaders, matrix registers, resource layouts or materials.

## Validate the observed EDF static candidate

`validate_edf_draw.py` is a separate, deliberately restricted evidence tool. It accepts only the
observed VS `f098f457d9ae603bea5f84ec985d473c29cfb0c9`, PS
`44b92a29eb3e5f2ee6d53f02e14fb11a01511da8` and their five-stream declaration.
It verifies original bytecode/blob hashes, decodes signed SHORT4 positions, signed SHORT2 UVs
and raw UBYTE4 vectors, and executes every straight-line VS arithmetic instruction with
swizzles and destination masks. Unsupported opcodes, instancing and invalid ranges fail.

```powershell
python tools/edf_capture/validate_edf_draw.py 'D:/EDF IA RTX REMIX/captures/edf' --output 'D:/EDF IA RTX REMIX/analysis/edf/selected_draw'
```

The default reference is capture `1791384430306-28796-6`, draw 284. `--reference` and `--draw`
select another observed draw of that shader pair. It matches the same used geometry in
other captures by active-stream contents and indices, then writes `validation.json`, shader
disassembly and `selected_object.obj` with decoded local positions, UVs and normals.
`--additional-capture-root` can be repeated to compare later capture directories with the
original measured reference without moving or rewriting any capture files.

The camera fit uses the independent shader-local camera constant and linear basis. It checks
a proper-rotation right-handed perspective factorization and adopts a **fixed translation of
zero for the selected object**. This is a documented spatial origin, not recovery of the
engine's absolute world origin. Algebraic/software projection errors do not measure actual
GPU shader outputs or raster overlap. Python double arithmetic is not a bit-exact GPU FMA
emulator. Texture pixels and entity names remain unknown.

The current real-capture findings and plots are under `D:/EDF IA RTX REMIX/analysis/edf`.
`edf-static-observe.conf` filters the shader pair for subsequent short capture windows;
it selects that shader category, not a persistent entity ID. Read the terminal capture status
before using a window as complete evidence.

## Verify the controlled D3D9 probe

`verify_probe.py` is separate from the generic analyzer. It validates only the explicit inputs in `tools/edf_d3d9_probe/probe.cpp` and its README:

```powershell
python tools/edf_capture/verify_probe.py path/to/capture/records.jsonl --probe-shaders path/to/the-same-run/probe_shaders --frames 3 --output path/to/probe-verification.json
```

Use all eight draws, shader/buffer dumps enabled, capture start frame 0 and three captured frames. `--frames 1` or `2` supports a shorter initial run; `--start-frame` accepts a window starting later. Shader identities are calculated from that run's original compiler dumps, because exact bytecode can depend on compiler version. Optional `--probe-log` checks the probe's JSONL stdout success/Present records.

The verifier checks 8 draws per frame, known declarations, stream offsets, BaseVertexIndex and StartIndex, VB/IB bytes, UP resource-zero payloads, per-frame DISCARD/NOOVERWRITE versions/events, F/I/B constants, stateblock recording/application, shader hashes/original bytecode, FVF and MultiplyTransform, viewport/pass metadata and sampler/render states. Floats use an absolute tolerance of `1e-6`; indices, offsets, identities and integer values are exact. Successful verification is observer validation, not camera reconstruction or an EDF compatibility result. Texture pixels remain unavailable.

Verifier tests use synthetic serialization of the known probe inputs. They include valid captures and purposeful corruption of offsets, indices, UP identities, shader identity, constants, pre-Apply state, dynamic tails, transforms, footer integrity and record order. The synthetic shader token streams used in those tests are never executed.
