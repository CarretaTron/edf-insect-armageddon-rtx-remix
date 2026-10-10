# Controlled D3D9 capture probe

This small console application creates a Win32 window and renders known input data using SM3 shaders and fixed function D3D9. It tests diagnostic capture without interpreting any EDF shader or modifying the game. It exits after three frames by default, returning `0` on success and `1` on a failed API call or invariant.

## Build without launching

```powershell
./tools/edf_d3d9_probe/build.ps1 -Architecture x64
./tools/edf_d3d9_probe/build.ps1 -Architecture x86
```

Build Tools are discovered through `vswhere`. Pass `-BuildToolsRoot 'D:\EDFBuildTools'` if necessary. Each build receives a new test directory under `_build/edf_d3d9_probe`. The build script does not launch the application unless `-Run` is explicitly present.

To prepare an app-local Runtime test, copy a complete matching bundle into the isolated test directory:

```powershell
./tools/edf_d3d9_probe/build.ps1 -Architecture x64 -RuntimeDirectory 'D:\path\to\x64-runtime-bundle'
./tools/edf_d3d9_probe/build.ps1 -Architecture x86 -RuntimeDirectory 'D:\path\to\x86-bridge-runtime-bundle'
```

For x86, the source bundle must contain the x86 Bridge client `d3d9.dll` plus its `.trex` server/runtime tree. For x64, use the x64 runtime DLL and dependencies. The script verifies the architecture of the top-level DLL before copying. It does not copy anything into the game directory.

## Run after preparation

Run from the prepared executable's directory so Bridge files, logs, and shader dumps remain inside the test directory:

```powershell
./tools/edf_d3d9_probe/run.ps1 -ProbeDirectory 'D:\path\to\isolated-test-directory' -Frames 3
```

Alternatively append `-Run -Frames 3` to the build command. Set the recorder's capture environment variables before launching. Prefer a one-frame window first, with ray tracing disabled. The application explicitly loads its neighboring `d3d9.dll` if present; otherwise it loads the Windows system D3D9 for a baseline. Startup output reports the actual module path and architecture. `d3dcompiler_47.dll` is loaded from the system directory.

## Known data and expected draws

The application compiles explicit-register HLSL with the documented `D3DCompile` API using `vs_3_0` and `ps_3_0`. It writes original bytecode and `D3DDisassemble` output into `probe_shaders`, reports exact-byte SHA1 hashes, and checks that `GetFunction` returns the same bytes.

The vertex declaration is stream 0, stride 32: position `FLOAT3` at byte 0, normal `FLOAT3` at byte 12, and UV `FLOAT2` at byte 24. All normals are `(0,0,1)`. The base triangle is in vertex slots 2, 3, 4. Slots 0 and 1 contain sentinel positions `(9,9,0.5)` and `(8,8,0.5)` to expose mistaken offsets. The 16-bit index buffer starts with three sentinel indices, followed by `(0,1,2)` and an initially zero tail. NOOVERWRITE changes that tail to `(2,1,0)` before draw 5.

VS registers `c0..c3` contain four rows. Clip position is computed as four dot products of `(position,1)` with those rows. On zero-based frame `f`, these are:

```text
c0 = (0.8, 0,   0, 0.05 + 0.02*f)
c1 = (0,   0.8, 0, -0.05)
c2 = (0,   0,   1, 0)
c3 = (0,   0,   0, 1)
```

PS `s0` samples a 2x2 A8R8G8B8 texture: red/green on the first row, blue/white on the second. PS `c0` is the tint; `c1=(0,0,1,0)` is a known light direction. VS/PS integer `i0=(1,2,3,4)` and boolean `b0=true` are also set for state capture, although these shaders do not use them.

Each frame contains eight triangle-list draws:

| Draw | API | Expected state/data |
|---|---|---|
| 0 | DrawPrimitive | StartVertex 2, stream offset 0, white tint. |
| 1 | DrawIndexedPrimitive | Stream byte offset 32, BaseVertexIndex 1, StartIndex 3, indices `(0,1,2)`, therefore VB slots 2–4. |
| 2 | DrawPrimitiveUP | Three triangle vertices, stride 32. Stream 0 becomes null after return. |
| 3 | DrawIndexedPrimitiveUP | MinVertexIndex 1, NumVertices 3, indices `(1,2,3)`, four-vertex backing payload with a leading sentinel. Stream 0 and IB become null after return. |
| 4 | DrawPrimitive | Stateblock has been recorded but not applied. Stream offset remains 0 and PS tint remains white. |
| 5 | DrawIndexedPrimitive | Stateblock applied: stream offset 32, base 1, StartIndex 6, reversed indices, tint `(1,0.25,0.25,1)`. |
| 6 | DrawPrimitive | StartVertex 5, offset 0. Uses the NOOVERWRITE vertex tail, shifted by `0.01*(f+1)` in X. |
| 7 | DrawPrimitive | VS/PS null, FVF XYZ/NORMAL/TEX1, identity View/Projection and World translated by +0.1 X through MultiplyTransform. |

Each frame begins with dynamic VB/IB DISCARD writes. NOOVERWRITE updates use untouched tail intervals before draws 5 and 6. The recorded stateblock sets shaders, declaration, buffers, texture, render state, and constants. Getter checks confirm recording does not alter effective constants and `Apply` does. Clear, depth/RT binding, viewport, scissor, sampler states, and stream frequency are also exercised.

Before the first draw, an unused texture transform tests noncommuting matrices: existing X scale 2 followed by `MultiplyTransform` with X translation 0.1 must produce X translation 0.2. `GetTransform` is checked and the texture transform restored to identity. The multiplication order follows Microsoft's [MultiplyTransform documentation](https://learn.microsoft.com/en-us/windows/win32/api/d3d9/nf-d3d9-idirect3ddevice9-multiplytransform). This check exposes a wrong client-cache order without changing the eight expected draws.

A second preflight records SetTransform(scale) and calls MultiplyTransform(translation) between BeginStateBlock/EndStateBlock. Native Windows D3D9 changes the live matrix immediately through MultiplyTransform (scale 1, translation 0.1) and later Apply restores only the recorded scale (scale 2, translation 0). Both getters are checked before restoring identity. Preflight stateblock events occur before draw 0; the capture verifier separately checks the main stateblock before draws 4/5.

Logs are JSON lines on stdout. A successful three-frame test reports 24 draws and three Presents. The probe verifies API behavior and known bytes; capture correctness must additionally be checked against the table above.

The UP state reset expectations follow Microsoft's [DrawPrimitiveUP documentation](https://learn.microsoft.com/en-us/windows/win32/api/d3d9/nf-d3d9-idirect3ddevice9-drawprimitiveup) and [DrawIndexedPrimitiveUP documentation](https://learn.microsoft.com/en-us/windows/win32/api/d3d9/nf-d3d9-idirect3ddevice9-drawindexedprimitiveup). Shader compilation and disassembly use the documented [D3DCompile](https://learn.microsoft.com/en-us/windows/win32/api/d3dcompiler/nf-d3dcompiler-d3dcompile) and [D3DDisassemble](https://learn.microsoft.com/en-us/windows/win32/api/d3dcompiler/nf-d3dcompiler-d3ddisassemble) APIs.
