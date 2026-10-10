# Project handoff — read this first

## Exact scope and identity

- Executable: EDF-IA.exe, x86, SHA1 `d2e90641c560420d279a629ddd86e13cb767f464`.
- Mesh SHA1 identity: `925aa18481228b01392a574dbe28ba4e21aa1616`.
- VS: `f098f457d9ae603bea5f84ec985d473c29cfb0c9`.
- PS: `44b92a29eb3e5f2ee6d53f02e14fb11a01511da8`.
- Geometry: 252 decoded vertices, 432 indices, 144 triangles; hash `0x32aeb7bb7e772c13`.
- Final custom renderer SHA256: `b051fe14b0965c7a45d3a2370d8dc68fc7a18e6ae8ba43ef4a5c4e0d7d745f9e`, 189184512 bytes.

## Proven and pending

Actual EDF evidence demonstrates recognition, RasterGeometry, camera acceptance, SceneManager, InstanceManager, recorded BLAS/TLAS, and three completed offscreen PT readbacks. CPU/GPU geometry, nonzero diagnostic radiance and original color targets were checked. Offline PNGs were generated and inspected.

The diagnostic current-frame panel is implemented, compiled and default OFF. Twelve x64 and six Bridge x86 controlled GPU cases passed, including three cameras, disable, invalid ROI, stale/missing camera, geometry mutation, duplicate draw, Reset, incompatible gamma and partial Present. Full WSI readbacks showed zero differences outside the ROI, and all four original color targets stayed identical. These replay textures/presentation patterns are synthetic; do not call them gameplay screenshots.

Khronos testing caught a new temporary-context descriptor-pool lifetime bug. The final build fixes it with a callback fence holding the overlay context until GPU completion. Post-fix positive/Reset tests no longer show the queue-submit invalidation. Global validation remains failed because baseline AS scratch, shadow-terminator/final-output layout hazards, renderpass compatibility and sampler errors persist. Do not suppress these errors or claim global validation passes.

Depth/stencil preservation is structural, not a full binary checksum. Physical display in EDF, final scene occlusion, original lighting/material interpretation, continuous performance, additional meshes, characters, skinning and effects remain pending.

## Next experiment

Run only the final exact-mesh manual panel experiment in `profiles/edf-composition-preview/TESTAR.md`: closed game/Bridge; independent backup; OFF install; explicit Enable; 1920x1080; F9 one frame; verify offscreen + composition sidecars and complete log; close and Restore. F8 cancels pending requests, not submitted GPU work. Never use the older installer to restore while the new backup journal is active.

Before broader compatibility, obtain actual EDF panel evidence and investigate remaining baseline validation errors separately. Replacement aligned with the scene requires original depth/visibility, coverage and downstream MRT/HUD pass analysis, as described in `analysis/edf/visualization/COMPOSITION_PLAN.md`.

## Engineering rules

Use evidence rather than assumed shader constants. Preserve exact shader/geometry/executable checks and current-frame camera validity. Preserve the raster. Avoid global PT, unrelated draws or generic subsystem rewrites. Compile and test between increments. Preserve historical evidence and explicit failures. Never automatically modify the active EDF installation.

Primary reports: `analysis/edf/next_validation/validation.json`, `analysis/edf/composition/validation.json`, `analysis/edf/composition/REPORT.md`, `analysis/edf/composition/sync_diagnosis.json`, `analysis/edf/visualization/REPORT.md`, and `runtime/documentation/edf-static-composition.md`.
