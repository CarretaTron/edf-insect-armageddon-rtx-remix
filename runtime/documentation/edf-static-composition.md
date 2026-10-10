# EDF static composition: frame-local WSI diagnostic panel

This experiment remains restricted to the validated EDF mesh (252 vertices, 432 indices), its shader/layout hashes and a current valid camera. `rtx.edfStatic.composition.enabled` defaults to false. The delivered profile allows a manually armed, single-frame preview; it does not replace the object's original scene pixels or enable continuous path tracing.

Implementation and evidence: [REPORT.md](<D:/EDF IA RTX REMIX/analysis/edf/composition/REPORT.md>), [validation.json](<D:/EDF IA RTX REMIX/analysis/edf/composition/validation.json>). Manual installation, activation, verification and restoration are documented separately in [TESTAR.md](<D:/EDF IA RTX REMIX/profiles/edf-composition-preview/TESTAR.md>). Consult the final validation result before installation; a successful build or historical pixel check alone is insufficient.

## Producer and presentation consumer

`EdfStaticComposition::preparePreview` executes on the CS thread in the existing guarded offscreen block of `RtxContext::injectRTX`, only for `present_fallback`. GPU reduction derives the finite depth hit bounds and rejects nonfinite final RGB. A second compute pass creates a private RGBA8 panel with an integer crop/nearest mapping, diagnostic background and cyan border. Alpha is not treated as coverage. The final source already passed tone mapping and display gamma/dither, so no second color transform is applied.

A pending panel owns its resources and validated source references, frame/request and original backbuffer identity. The presentation consumer runs on the same CS thread and must match the current `QueuePresentCount`. It rejects stale panels, duplicates, F8 cancellation, unsupported formats/usages, nonidentity gamma, partial Present mapping and source aliasing. Resize/Reset invalidates pending work. Unknown meshes do not acquire a decoder rule.

## Two command lists and completion

The ordinary presentation list copies the game backbuffer and draws runtime HUD/ImGui into the acquired WSI image. The opt-in EDF path records a separate overlay list before submitting either list, so an experimental recording failure can discard that list and retain a signal-only raster fallback.

1. Submit the base list with the `acquire` semaphore and no `present` signal.
2. Submit the overlay/fallback list on the same graphics queue. An `ALL_COMMANDS` memory-write to memory-read/write barrier precedes overlay accesses. Only this final list signals `present` and the swapchain frame-latency value.
3. Enqueue `presentImage` after the final submission.

The internal `FrameId` parameter of `SubmitPresent` is a SyncInterval loop index. It must not be confused with the RTX `QueuePresentCount` or the swapchain serial. Those identities are recorded separately. The first experiment accepts only a compatible single presentation.

WSI transfer-source usage is optional and restricted to the exact opt-in profile, checked against surface capabilities and mirrored in the DXVK image wrapper. Storage writes require `GENERAL`; recording explicitly restores `PRESENT_SRC_KHR` after the post-overlay readback. The shader can store only within the bounded ROI and returns without writing when the GPU mask is empty or invalid. Original game color/depth resources are never overlay destinations.

This revision already disables the memcpy auto-flush heuristic in `DxvkContext::recordGPUMemCopy`. The experiment preserves that generic behavior and has a source-contract test for it. Re-enabling the heuristic would require explicit experimental-context protection and new GPU validation: otherwise a `copyImageToBuffer` helper could submit part of the overlay ahead of the base list and break the acquire/present ordering.

## Descriptor and image lifetime

Retaining images and buffers alone is insufficient. The overlay `DxvkContext` owns the descriptor pool used by its dispatch. A callback fence retains an `Rc` to that context until GPU completion. This prevents the temporary context's destruction from freeing descriptors still referenced by the submitted list. Synchronization validation found this bug in the initial candidate. The corrected Runtime passed 18 independent native GPU cases; the post-fix positive and Reset synchronization runs no longer reported `VUID-vkQueueSubmit-pCommandBuffers-00070` for that lifetime defect. Generic validation errors in the disabled baseline remain a separate unresolved limitation.

Readback jobs retain their source references and host buffers until the completion callback. Swapchain recreation drains the relevant CS/GPU work before destroying acquired images, even if the option was disabled after a submission. Failed unsubmitted recording restores host layout bookkeeping with a `noexcept` scope guard; no new GPU commands are allocated during unwind.

## Audit and boundaries

The offscreen probe JSON still describes `compositionPerformed=false`. The separate composition sidecar correlates that exact frame/request and records full WSI pre/post readbacks, the original ROI scratch, the expected panel and the four original targets before the probe/after the overlay. Independent verification checks crop math, finite mask, fidelity, zero differences outside the ROI and full original-color equality. Its full-log audit rejects submission/device/synchronization/validation failures even when a sample callback reported completion.

Fence completion proves GPU work and readback completion. It does not prove physical scanout, user-visible presentation or an EDF gameplay run; `presentationDisplayVerified` stays false until a separate real observation. Native fixtures use real mesh/shader/camera data with synthetic textures and raster presentation patterns. They must remain labelled as controlled execution rather than game screenshots.

Depth/stencil preservation is structural: this experiment does not write it. No full binary depth/stencil comparison is collected. Correct in-scene replacement would additionally require original depth, visibility, MRT consumer semantics and later HUD/effect ordering. The panel can cover existing HUD inside its reserved rectangle.

Vulkan validation also reports generic renderpass/shadow-sampler/layout issues in the disabled baseline. The post-fix positive and Reset cases introduced no additional observed error code or normalized hazard signature against that baseline, but all three validation runs still failed the strict global audit. Four hazard occurrences involve BLAS scratch, shadow-terminator images and final-output transitions. They must be tracked separately from overlay-specific errors. No global Vulkan-validation approval is implied by removal of an overlay descriptor-lifetime error or successful pixel comparisons. `globalVulkanValidationPassed=false` remains explicit.

F8 cancels pending work; it cannot undo a submitted frame. Observed verification failure prevents later attempts until process restart. Ordinary device-lost and critical allocation failures require the game's normal recovery. Manual deployment uses an independent `.edf-composition-backup` journal and restores the immediately preceding Runtime/profile without invoking the older deployment's restore operation.
