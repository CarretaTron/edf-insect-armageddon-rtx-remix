/*
* Copyright (c) 2026, NVIDIA CORPORATION. All rights reserved.
*
* Permission is hereby granted, free of charge, to any person obtaining a
* copy of this software and associated documentation files (the "Software"),
* to deal in the Software without restriction, including without limitation
* the rights to use, copy, modify, merge, publish, distribute, sublicense,
* and/or sell copies of the Software, and to permit persons to whom the
* Software is furnished to do so, subject to the following conditions:
*
* The above copyright notice and this permission notice shall be included in
* all copies or substantial portions of the Software.
*
* THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
* IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
* FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL
* THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
* LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
* FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
* DEALINGS IN THE SOFTWARE.
*/
#pragma once

#include <array>
#include <atomic>
#include <cstdint>
#include <memory>
#include <string>
#include <vector>

#include "rtx_option.h"
#include "rtx_resources.h"

namespace dxvk {
  class RtxContext;

  class EdfStaticProbe {
  public:
    static constexpr uint64_t kTraceId = 0x4544465354415449ull;
    RTX_OPTION("rtx.edfStatic.probe", bool, enabled, false, "Enable the bounded offscreen path tracing experiment for the single validated EDF mesh. The game image is never replaced.");
    RTX_OPTION("rtx.edfStatic.probe", uint32_t, frameCount, 1, "Number of compatible frames requested by one arm operation. Clamped to 1 through 3.");
    RTX_OPTION("rtx.edfStatic.probe", bool, autorun, false, "Arm once automatically for controlled GPU replays. Keep disabled for gameplay.");
    RTX_OPTION("rtx.edfStatic.probe", uint32_t, armKey, 120, "Virtual key that arms one bounded offscreen experiment. Default 120: F9. Zero disables the hotkey.");
    RTX_OPTION("rtx.edfStatic.probe", uint32_t, cancelKey, 119, "Virtual key that cancels pending offscreen frames. Default 119: F8. Completed GPU readbacks remain available.");
    RTX_OPTION("rtx.edfStatic.probe", std::string, outputDirectory, "./captures/edf-pt-probe", "Directory for GPU-completed camera, depth, albedo and radiance diagnostic files.");

    void noteDraw(uint32_t frameId, const Matrix4& worldToView, const Matrix4& viewToProjection,
      const std::array<Rc<DxvkImage>, 3>& sourceTargets = {});
    void recordInjection(RtxContext* pContext, uint32_t frameId, const char* pSource,
      uint32_t lastInjected, const Rc<DxvkImage>& target);
    bool beginFrame(RtxContext* pContext, const Rc<DxvkImage>& injectionTarget);
    void queueReadback(RtxContext* pContext, const Resources::RaytracingOutput& output);
    void cancel(const char* pReason);

    struct CompositionOriginal {
      Rc<DxvkImage> image;
      Rc<DxvkBuffer> before;
      uint32_t slot = 0;
    };

    struct CompositionSource {
      uint32_t frameId = kInvalidFrameIndex;
      uint32_t cameraFrame = kInvalidFrameIndex;
      uint64_t requestId = 0;
      uint64_t instanceId = 0;
      uint64_t geometryHash = 0;
      std::string directory;
      std::vector<CompositionOriginal> originals;
    };

    CompositionSource getCompositionSource(uint32_t frameId) const;

  private:
    void pollControls(uint32_t frameId);
    uint32_t currentDrawCount(uint32_t frameId) const;

    struct InjectionState {
      uint32_t count = 0;
      uint32_t lastLoggedFrame = kInvalidFrameIndex;
      uint64_t signature = ~uint64_t(0);
    };

    struct PreservedTarget {
      Rc<DxvkImage> image;
      Rc<DxvkBuffer> before;
      uint32_t slot = 0;
    };

    std::array<InjectionState, 2> m_injectionStates = {};
    uint64_t m_injectionSerial = 0;
    uint64_t m_requestSerial = 0;
    uint64_t m_probeInstanceId = 0;
    uint64_t m_probeGeometryHash = 0;
    uint64_t m_probeMaterialHash = 0;
    uint32_t m_latestDrawFrame = kInvalidFrameIndex;
    uint32_t m_drawCount = 0;
    uint32_t m_lastControlFrame = kInvalidFrameIndex;
    uint32_t m_armFrame = kInvalidFrameIndex;
    uint32_t m_lastProbeFrame = kInvalidFrameIndex;
    uint32_t m_readbackFrame = kInvalidFrameIndex;
    uint32_t m_remainingFrames = 0;
    bool m_autorunUsed = false;
    bool m_armKeyDown = false;
    bool m_cancelKeyDown = false;
    std::string m_outputSessionDirectory;
    std::shared_ptr<std::atomic<uint32_t>> m_readbacksInFlight = std::make_shared<std::atomic<uint32_t>>(0);
    Matrix4 m_sourceView;
    Matrix4 m_sourceProjection;
    std::array<Rc<DxvkImage>, 3> m_sourceTargets;
    std::vector<PreservedTarget> m_preservedTargets;
    Rc<DxvkImage> m_injectionTarget;
  };
}
